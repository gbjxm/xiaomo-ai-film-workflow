[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourcePath,

    [Parameter(Mandatory = $true)]
    [string]$SnapshotRoot,

    [Parameter(Mandatory = $true)]
    [ValidateSet('base', 'user', 'codex', 'merged', 'adopted', 'production')]
    [string]$Role,

    [Parameter(Mandatory = $true)]
    [string]$VersionId,

    [string[]]$ParentVersionId = @(),

    [Parameter(Mandatory = $true)]
    [string]$WriteAuthorizationEvidence,

    [string]$AdoptionEvidence,
    [string]$BWriteEvidence,
    [string]$ProductionReleaseEvidence,
    [string]$MergeEvidencePath,
    [string]$ProductionDerivationEvidencePath,
    [string]$SxxScope,
    [string[]]$ScopeAnchor = @(),
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:StageDirectory = $null
$script:RoleDirectory = $null

function ConvertTo-JsonLine([object]$Value) {
    return ($Value | ConvertTo-Json -Depth 16 -Compress)
}

function Write-Result([object]$Value) {
    Write-Output (ConvertTo-JsonLine -Value $Value)
}

function Get-FullPath([string]$Path) {
    return [IO.Path]::GetFullPath($Path)
}

function Normalize-OneLine([string]$Value, [string]$Name, [bool]$Required) {
    if ($null -eq $Value) { $Value = '' }
    $result = ($Value -replace '[\r\n]+', ' ').Trim()
    if ($Required -and [string]::IsNullOrWhiteSpace($result)) {
        throw "$Name 不能为空。"
    }
    if ([string]::IsNullOrWhiteSpace($result)) { return $null }
    return $result
}

function Assert-SafeId([string]$Value, [string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
        throw "$Name 必须为 1—64 位 ASCII 字母、数字、点、下划线或连字符，且首字符为字母或数字。"
    }
}

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Normalize-Sha256([object]$Value, [string]$Name) {
    $text = Normalize-OneLine -Value ([string]$Value) -Name $Name -Required $true
    if ($text -notmatch '^[A-Fa-f0-9]{64}$') { throw "$Name 必须是 64 位 SHA-256。" }
    return $text.ToUpperInvariant()
}

function Get-RequiredProperty([object]$Object, [string]$Name, [string]$Context) {
    if ($null -eq $Object) { throw "$Context 不能为空。" }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { throw "$Context 缺少字段：$Name" }
    return $property.Value
}

function Get-RequiredString([object]$Object, [string]$Name, [string]$Context) {
    $value = Get-RequiredProperty -Object $Object -Name $Name -Context $Context
    return Normalize-OneLine -Value ([string]$value) -Name "$Context.$Name" -Required $true
}

function Get-RequiredSha256([object]$Object, [string]$Name, [string]$Context) {
    $value = Get-RequiredProperty -Object $Object -Name $Name -Context $Context
    return Normalize-Sha256 -Value $value -Name "$Context.$Name"
}

function Assert-EmptyArrayProperty([object]$Object, [string]$Name, [string]$Context) {
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value -or -not ($property.Value -is [Array])) {
        throw "$Context.$Name 必须是数组。"
    }
    if (@($property.Value).Count -ne 0) { throw "$Context.$Name 必须为空。" }
}

function Get-ProjectLocalJsonEvidence([string]$Path, [string]$Root, [string]$ExpectedSchema, [string]$Name) {
    $pathOneLine = Normalize-OneLine -Value $Path -Name $Name -Required $true
    $fullPath = Get-FullPath $pathOneLine
    $rootFull = (Get-FullPath $Root).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $rootPrefix = $rootFull + [IO.Path]::DirectorySeparatorChar
    if (-not $fullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Name 必须位于项目内版本账根目录：$rootFull"
    }
    if ([IO.Path]::GetExtension($fullPath) -ine '.json') { throw "$Name 必须是 .json 文件。" }
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) { throw "$Name 不存在：$fullPath" }

    $infoBefore = Get-Item -LiteralPath $fullPath
    $hashBefore = Get-FileSha256 -Path $fullPath
    try {
        $data = Get-Content -LiteralPath $fullPath -Raw | ConvertFrom-Json
    } catch {
        throw "$Name 不是有效 JSON：$($_.Exception.Message)"
    }
    $hashAfter = Get-FileSha256 -Path $fullPath
    $infoAfter = Get-Item -LiteralPath $fullPath
    if ($hashBefore -ne $hashAfter -or
        $infoBefore.Length -ne $infoAfter.Length -or
        $infoBefore.LastWriteTimeUtc -ne $infoAfter.LastWriteTimeUtc) {
        throw "$Name 在读取期间发生变化。"
    }
    $schema = Get-RequiredString -Object $data -Name 'schema' -Context $Name
    if ($schema -ne $ExpectedSchema) { throw "$Name schema 必须为 $ExpectedSchema。" }

    return [pscustomobject]@{
        path = $fullPath
        sha256 = $hashBefore
        schema = $schema
        data = $data
    }
}

function Get-EvidencePointer([object]$Evidence) {
    if ($null -eq $Evidence) { return $null }
    return [ordered]@{
        path = [string]$Evidence.path
        sha256 = [string]$Evidence.sha256
        schema = [string]$Evidence.schema
    }
}

function Get-VerifiedParentRecords([string]$Root, [string[]]$Ids) {
    $roles = @('base', 'user', 'codex', 'merged', 'adopted', 'production')
    $records = New-Object System.Collections.Generic.List[object]

    foreach ($id in @($Ids)) {
        Assert-SafeId -Value $id -Name 'ParentVersionId'
        $matches = New-Object System.Collections.Generic.List[string]
        foreach ($candidateRole in $roles) {
            $candidate = Join-Path (Join-Path (Join-Path $Root $candidateRole) $id) 'metadata.json'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $matches.Add($candidate) }
        }
        if ($matches.Count -eq 0) { throw "找不到父版本元数据：$id" }
        if ($matches.Count -gt 1) { throw "父版本 ID 在版本账中不唯一：$id" }

        $metadataPath = $matches[0]
        $metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
        if ($metadata.schema -ne 'screenplay-word-lineage-v1' -or $metadata.version_id -ne $id) {
            throw "父版本元数据不符合 screenplay-word-lineage-v1：$id"
        }

        $parentDirectory = Split-Path -Parent $metadataPath
        $parentDocx = Join-Path $parentDirectory 'screenplay.docx'
        if (-not (Test-Path -LiteralPath $parentDocx -PathType Leaf)) {
            throw "父版本缺少 screenplay.docx：$id"
        }
        $actualHash = Get-FileSha256 -Path $parentDocx
        if ($actualHash -ne ([string]$metadata.sha256).ToUpperInvariant()) {
            throw "父版本文件 hash 已漂移：$id"
        }

        $records.Add([pscustomobject]@{
            version_id = [string]$metadata.version_id
            role = [string]$metadata.role
            sha256 = $actualHash
            path = $parentDocx
            parent_version_ids = @($metadata.parent_version_ids)
            parent_roles = @($metadata.parent_roles)
        })
    }

    return @($records.ToArray())
}

function Get-VerifiedMergeEvidence([string]$Path, [string]$Root, [object[]]$Parents, [string]$SourceHash) {
    $userParent = @($Parents | Where-Object { $_.role -eq 'user' })[0]
    $codexParent = @($Parents | Where-Object { $_.role -eq 'codex' })[0]
    if ($null -eq $userParent -or $null -eq $codexParent) { throw 'merged 缺少 user 或 codex 父版本。' }
    if (@($userParent.parent_version_ids).Count -ne 1 -or @($codexParent.parent_version_ids).Count -ne 1) {
        throw 'user 与 codex 父版本必须各自唯一指向一个 base。'
    }
    $userBaseId = [string]@($userParent.parent_version_ids)[0]
    $codexBaseId = [string]@($codexParent.parent_version_ids)[0]
    if ([string]::IsNullOrWhiteSpace($userBaseId) -or $userBaseId -ne $codexBaseId) {
        throw 'user 与 codex 不共享同一个精确 base 版本。'
    }
    $baseRecord = @(Get-VerifiedParentRecords -Root $Root -Ids @($userBaseId))[0]
    if ($null -eq $baseRecord -or $baseRecord.role -ne 'base') { throw '共同父版本不是有效 base。' }

    $evidence = Get-ProjectLocalJsonEvidence -Path $Path -Root $Root -ExpectedSchema 'screenplay-word-merge-evidence/v1' -Name 'MergeEvidencePath'
    $data = $evidence.data
    $baseBlock = Get-RequiredProperty -Object $data -Name 'common_base' -Context 'MergeEvidencePath'
    $userBlock = Get-RequiredProperty -Object $data -Name 'user' -Context 'MergeEvidencePath'
    $codexBlock = Get-RequiredProperty -Object $data -Name 'codex' -Context 'MergeEvidencePath'

    if ((Get-RequiredString -Object $baseBlock -Name 'version_id' -Context 'MergeEvidencePath.common_base') -ne $baseRecord.version_id -or
        (Get-RequiredSha256 -Object $baseBlock -Name 'sha256' -Context 'MergeEvidencePath.common_base') -ne $baseRecord.sha256) {
        throw 'MergeEvidencePath 的共同 base 版本/hash 与版本账不一致。'
    }
    if ((Get-RequiredString -Object $userBlock -Name 'version_id' -Context 'MergeEvidencePath.user') -ne $userParent.version_id -or
        (Get-RequiredSha256 -Object $userBlock -Name 'sha256' -Context 'MergeEvidencePath.user') -ne $userParent.sha256) {
        throw 'MergeEvidencePath 的 user 版本/hash 与版本账不一致。'
    }
    if ((Get-RequiredString -Object $codexBlock -Name 'version_id' -Context 'MergeEvidencePath.codex') -ne $codexParent.version_id -or
        (Get-RequiredSha256 -Object $codexBlock -Name 'sha256' -Context 'MergeEvidencePath.codex') -ne $codexParent.sha256) {
        throw 'MergeEvidencePath 的 codex 版本/hash 与版本账不一致。'
    }
    if ((Get-RequiredSha256 -Object $data -Name 'merged_sha256' -Context 'MergeEvidencePath') -ne $SourceHash) {
        throw 'MergeEvidencePath.merged_sha256 与源 DOCX 不一致。'
    }
    Assert-EmptyArrayProperty -Object $data -Name 'unresolved_conflicts' -Context 'MergeEvidencePath'
    [void](Get-RequiredString -Object $data -Name 'method' -Context 'MergeEvidencePath')
    [void](Get-RequiredString -Object $data -Name 'evidence' -Context 'MergeEvidencePath')
    return $evidence
}

function Get-VerifiedProductionDerivationEvidence([string]$Path, [string]$Root, [object]$AdoptedParent, [string]$SourceHash) {
    $evidence = Get-ProjectLocalJsonEvidence -Path $Path -Root $Root -ExpectedSchema 'screenplay-word-production-derivation/v1' -Name 'ProductionDerivationEvidencePath'
    $data = $evidence.data
    $adoptedBlock = Get-RequiredProperty -Object $data -Name 'adopted' -Context 'ProductionDerivationEvidencePath'
    if ((Get-RequiredString -Object $adoptedBlock -Name 'version_id' -Context 'ProductionDerivationEvidencePath.adopted') -ne $AdoptedParent.version_id -or
        (Get-RequiredSha256 -Object $adoptedBlock -Name 'sha256' -Context 'ProductionDerivationEvidencePath.adopted') -ne $AdoptedParent.sha256) {
        throw 'ProductionDerivationEvidencePath 的 adopted 版本/hash 与版本账不一致。'
    }
    if ((Get-RequiredSha256 -Object $data -Name 'production_sha256' -Context 'ProductionDerivationEvidencePath') -ne $SourceHash) {
        throw 'ProductionDerivationEvidencePath.production_sha256 与源 DOCX 不一致。'
    }
    foreach ($field in @('standardized_text_equal', 'scene_anchor_order_equal')) {
        $value = Get-RequiredProperty -Object $data -Name $field -Context 'ProductionDerivationEvidencePath'
        if (-not ($value -is [bool]) -or $value -ne $true) {
            throw "ProductionDerivationEvidencePath.$field 必须为 true。"
        }
    }
    Assert-EmptyArrayProperty -Object $data -Name 'unresolved_conflicts' -Context 'ProductionDerivationEvidencePath'
    if ((Get-RequiredString -Object $data -Name 'visual_qa' -Context 'ProductionDerivationEvidencePath') -ne 'passed') {
        throw "ProductionDerivationEvidencePath.visual_qa 必须为 'passed'。"
    }
    [void](Get-RequiredString -Object $data -Name 'method' -Context 'ProductionDerivationEvidencePath')
    [void](Get-RequiredString -Object $data -Name 'evidence' -Context 'ProductionDerivationEvidencePath')
    return $evidence
}

function Assert-Transition([string]$TargetRole, [object[]]$Parents) {
    $validParents = @($Parents | Where-Object { $null -ne $_ })
    $parentRoles = @($validParents | ForEach-Object { $_.role })
    switch ($TargetRole) {
        'base' {
            if ($parentRoles.Count -gt 1) { throw 'base 只允许零个或一个父版本。' }
        }
        'user' {
            if ($parentRoles.Count -ne 1 -or $parentRoles[0] -ne 'base') {
                throw 'user 必须且只能指向一个 base 父版本。'
            }
        }
        'codex' {
            if ($parentRoles.Count -ne 1 -or $parentRoles[0] -ne 'base') {
                throw 'codex 必须且只能指向一个 base 父版本。'
            }
        }
        'merged' {
            $sorted = @($parentRoles | Sort-Object)
            if ($sorted.Count -ne 2 -or $sorted[0] -ne 'codex' -or $sorted[1] -ne 'user') {
                throw 'merged 必须且只能同时指向一个 user 和一个 codex 父版本。'
            }
        }
        'adopted' {
            if ($parentRoles.Count -ne 1 -or $parentRoles[0] -notin @('base', 'user', 'codex', 'merged')) {
                throw 'adopted 必须且只能指向一个 base、user、codex 或 merged 父版本。'
            }
        }
        'production' {
            if ($parentRoles.Count -ne 1 -or $parentRoles[0] -ne 'adopted') {
                throw 'production 必须且只能指向一个 adopted 父版本。'
            }
        }
    }
}

function Remove-StagingDirectorySafely {
    if ([string]::IsNullOrWhiteSpace($script:StageDirectory) -or
        [string]::IsNullOrWhiteSpace($script:RoleDirectory) -or
        -not (Test-Path -LiteralPath $script:StageDirectory -PathType Container)) {
        return
    }

    $stageFull = Get-FullPath $script:StageDirectory
    $roleFull = (Get-FullPath $script:RoleDirectory).TrimEnd('\')
    $rolePrefix = $roleFull + '\'
    if (-not $stageFull.StartsWith($rolePrefix, [StringComparison]::OrdinalIgnoreCase) -or
        -not ([IO.Path]::GetFileName($stageFull)).StartsWith('.staging-', [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理未通过边界检查的暂存目录：$stageFull"
    }
    Remove-Item -LiteralPath $stageFull -Recurse -Force
}

try {
    Assert-SafeId -Value $VersionId -Name 'VersionId'

    $writeEvidence = Normalize-OneLine -Value $WriteAuthorizationEvidence -Name 'WriteAuthorizationEvidence' -Required $true
    $adoptionEvidenceOneLine = Normalize-OneLine -Value $AdoptionEvidence -Name 'AdoptionEvidence' -Required ($Role -in @('adopted', 'production'))
    $bWriteEvidenceOneLine = Normalize-OneLine -Value $BWriteEvidence -Name 'BWriteEvidence' -Required ($Role -eq 'production')
    $releaseEvidenceOneLine = Normalize-OneLine -Value $ProductionReleaseEvidence -Name 'ProductionReleaseEvidence' -Required ($Role -eq 'production')
    $sxxScopeOneLine = Normalize-OneLine -Value $SxxScope -Name 'SxxScope' -Required ($Role -eq 'production')

    $anchors = New-Object System.Collections.Generic.List[string]
    foreach ($anchor in @($ScopeAnchor)) {
        $normalized = Normalize-OneLine -Value $anchor -Name 'ScopeAnchor' -Required $true
        if (-not $anchors.Contains($normalized)) { $anchors.Add($normalized) }
    }

    $parentIds = New-Object System.Collections.Generic.List[string]
    foreach ($parentId in @($ParentVersionId)) {
        Assert-SafeId -Value $parentId -Name 'ParentVersionId'
        if ($parentIds.Contains($parentId)) { throw "ParentVersionId 重复：$parentId" }
        $parentIds.Add($parentId)
    }

    $source = Get-FullPath $SourcePath
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "源文件不存在：$source" }
    if ([IO.Path]::GetExtension($source) -ine '.docx') { throw '源文件必须是 .docx。' }

    $root = Get-FullPath $SnapshotRoot
    if (Test-Path -LiteralPath $root -PathType Leaf) { throw "SnapshotRoot 不能是文件：$root" }

    $parentRecords = @(Get-VerifiedParentRecords -Root $root -Ids @($parentIds.ToArray()) | Where-Object { $null -ne $_ })
    Assert-Transition -TargetRole $Role -Parents $parentRecords

    $statusByRole = @{
        base = 'saved'
        user = 'saved'
        codex = 'under_review'
        merged = 'merge_ready'
        adopted = 'adopted'
        production = 'released'
    }

    $roleDirectory = Join-Path $root $Role
    foreach ($candidateRole in @('base', 'user', 'codex', 'merged', 'adopted', 'production')) {
        $candidateVersion = Join-Path (Join-Path $root $candidateRole) $VersionId
        if (Test-Path -LiteralPath $candidateVersion) {
            throw "版本 ID 已存在，拒绝在另一角色下复用或覆盖：$candidateVersion"
        }
    }

    $finalDirectory = Join-Path $roleDirectory $VersionId
    $finalDocx = Join-Path $finalDirectory 'screenplay.docx'
    $finalMetadata = Join-Path $finalDirectory 'metadata.json'
    if (Test-Path -LiteralPath $finalDirectory) { throw "版本已存在，拒绝覆盖：$finalDirectory" }

    $sourceInfoBefore = Get-Item -LiteralPath $source
    $sourceHashBefore = Get-FileSha256 -Path $source
    $sourceHashConfirm = Get-FileSha256 -Path $source
    $sourceInfoConfirm = Get-Item -LiteralPath $source
    if ($sourceHashBefore -ne $sourceHashConfirm -or
        $sourceInfoBefore.Length -ne $sourceInfoConfirm.Length -or
        $sourceInfoBefore.LastWriteTimeUtc -ne $sourceInfoConfirm.LastWriteTimeUtc) {
        throw '源文件在读取期间发生变化；请先保存，再重新建立快照。'
    }

    if ($Role -in @('base', 'adopted') -and $parentRecords.Count -eq 1 -and
        $sourceHashBefore -ne $parentRecords[0].sha256) {
        throw "$Role 只登记既有父版本身份，源 hash 必须与父版本相同。"
    }

    if ($Role -ne 'merged' -and -not [string]::IsNullOrWhiteSpace($MergeEvidencePath)) {
        throw 'MergeEvidencePath 只允许用于 merged。'
    }
    if ($Role -ne 'production' -and -not [string]::IsNullOrWhiteSpace($ProductionDerivationEvidencePath)) {
        throw 'ProductionDerivationEvidencePath 只允许用于 production。'
    }

    $mergeEvidence = $null
    $productionDerivationEvidence = $null
    $productionBinding = $null
    if ($Role -eq 'merged') {
        $mergeEvidence = Get-VerifiedMergeEvidence -Path $MergeEvidencePath -Root $root -Parents $parentRecords -SourceHash $sourceHashBefore
    }
    if ($Role -eq 'production') {
        $adoptedParent = @($parentRecords)[0]
        if ($sourceHashBefore -eq $adoptedParent.sha256) {
            $productionBinding = 'byte_identical'
            if (-not [string]::IsNullOrWhiteSpace($ProductionDerivationEvidencePath)) {
                $productionDerivationEvidence = Get-VerifiedProductionDerivationEvidence -Path $ProductionDerivationEvidencePath -Root $root -AdoptedParent $adoptedParent -SourceHash $sourceHashBefore
            }
        } else {
            $productionBinding = 'verified_derivation'
            $productionDerivationEvidence = Get-VerifiedProductionDerivationEvidence -Path $ProductionDerivationEvidencePath -Root $root -AdoptedParent $adoptedParent -SourceHash $sourceHashBefore
        }
    }

    $planned = [ordered]@{
        success = $true
        dry_run = [bool]$DryRun
        schema = 'screenplay-word-lineage-v1'
        role = $Role
        status = $statusByRole[$Role]
        version_id = $VersionId
        source_path = $source
        source_sha256 = $sourceHashBefore
        source_saved_bytes_only = $true
        destination_path = $finalDocx
        metadata_path = $finalMetadata
        parent_version_ids = @($parentIds.ToArray())
        scope_anchors = @($anchors.ToArray())
        sxx_scope = $sxxScopeOneLine
        merge_evidence = Get-EvidencePointer -Evidence $mergeEvidence
        production_binding = $productionBinding
        production_derivation_evidence = Get-EvidencePointer -Evidence $productionDerivationEvidence
    }

    if ($DryRun) {
        Write-Result -Value $planned
        exit 0
    }

    New-Item -ItemType Directory -Path $roleDirectory -Force | Out-Null
    $script:RoleDirectory = Get-FullPath $roleDirectory
    $script:StageDirectory = Join-Path $script:RoleDirectory ('.staging-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $script:StageDirectory | Out-Null

    $stagedDocx = Join-Path $script:StageDirectory 'screenplay.docx'
    $stagedMetadata = Join-Path $script:StageDirectory 'metadata.json'
    Copy-Item -LiteralPath $source -Destination $stagedDocx

    $snapshotHash = Get-FileSha256 -Path $stagedDocx
    $sourceHashAfter = Get-FileSha256 -Path $source
    $sourceInfoAfter = Get-Item -LiteralPath $source
    if ($snapshotHash -ne $sourceHashBefore -or
        $sourceHashAfter -ne $sourceHashBefore -or
        $sourceInfoAfter.Length -ne $sourceInfoBefore.Length -or
        $sourceInfoAfter.LastWriteTimeUtc -ne $sourceInfoBefore.LastWriteTimeUtc) {
        throw '源文件在复制期间发生变化；已放弃暂存快照，请保存后重试。'
    }
    foreach ($evidenceFile in @($mergeEvidence, $productionDerivationEvidence) | Where-Object { $null -ne $_ }) {
        if ((Get-FileSha256 -Path $evidenceFile.path) -ne $evidenceFile.sha256) {
            throw "证据文件在快照期间发生变化：$($evidenceFile.path)"
        }
    }

    $metadata = [ordered]@{
        schema = 'screenplay-word-lineage-v1'
        version_id = $VersionId
        role = $Role
        status = $statusByRole[$Role]
        path = $finalDocx
        sha256 = $snapshotHash
        parent_version_ids = @($parentIds.ToArray())
        parent_roles = @($parentRecords | ForEach-Object { $_.role })
        source_path = $source
        source_sha256 = $sourceHashBefore
        source_last_write_utc = $sourceInfoBefore.LastWriteTimeUtc.ToString('o')
        source_saved_bytes_only = $true
        captured_at = (Get-Date).ToString('o')
        scope_anchors = @($anchors.ToArray())
        sxx_scope = $sxxScopeOneLine
        evidence = [ordered]@{
            artifact_write_authorization = $writeEvidence
            adoption = $adoptionEvidenceOneLine
            b_write = $bWriteEvidenceOneLine
            production_release = $releaseEvidenceOneLine
        }
        lineage_evidence = [ordered]@{
            merge = Get-EvidencePointer -Evidence $mergeEvidence
            production_binding = $productionBinding
            production_derivation = Get-EvidencePointer -Evidence $productionDerivationEvidence
        }
        limits = @(
            '只快照磁盘上已保存的 DOCX；不包含 Word 未保存内容。',
            '快照成功不代表创作采用、B_STAGE_READY、媒体生成或其他外部动作获准。'
        )
    }

    $utf8 = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($stagedMetadata, ($metadata | ConvertTo-Json -Depth 16), $utf8)
    (Get-Item -LiteralPath $stagedDocx).IsReadOnly = $true
    (Get-Item -LiteralPath $stagedMetadata).IsReadOnly = $true

    [IO.Directory]::Move($script:StageDirectory, $finalDirectory)
    $script:StageDirectory = $null

    $result = [ordered]@{
        success = $true
        dry_run = $false
        schema = 'screenplay-word-lineage-v1'
        role = $Role
        status = $statusByRole[$Role]
        version_id = $VersionId
        path = $finalDocx
        sha256 = $snapshotHash
        metadata_path = $finalMetadata
        metadata_sha256 = Get-FileSha256 -Path $finalMetadata
        parent_version_ids = @($parentIds.ToArray())
        source_saved_bytes_only = $true
        merge_evidence = Get-EvidencePointer -Evidence $mergeEvidence
        production_binding = $productionBinding
        production_derivation_evidence = Get-EvidencePointer -Evidence $productionDerivationEvidence
    }
    Write-Result -Value $result
    exit 0
} catch {
    try { Remove-StagingDirectorySafely } catch {
        [Console]::Error.WriteLine((ConvertTo-JsonLine -Value ([ordered]@{
            success = $false
            error = $_.Exception.Message
            cleanup_failed = $true
        })))
        exit 1
    }

    [Console]::Error.WriteLine((ConvertTo-JsonLine -Value ([ordered]@{
        success = $false
        error = $_.Exception.Message
    })))
    exit 1
}
