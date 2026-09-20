[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [Parameter(Mandatory = $true)]
    [string]$PacketPath,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedPacketSha256
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'project_document_paths.ps1')
$script:PacketDirectory='剧本/阶段完成包'
$script:ValidationErrors = New-Object 'System.Collections.Generic.List[string]'
$script:ProjectRootFull = $null

function Add-ValidationError {
    param([string]$Message)
    [void]$script:ValidationErrors.Add($Message)
}

function Test-HasProperty {
    param($Object, [string]$Name)
    return ($null -ne $Object -and $null -ne $Object.PSObject -and $null -ne $Object.PSObject.Properties[$Name])
}

function Get-Field {
    param($Object, [string]$Name, [string]$Path, [switch]$Required)
    if (-not (Test-HasProperty -Object $Object -Name $Name)) {
        if ($Required) { Add-ValidationError "$Path.$Name is required." }
        return $null
    }
    $value = $Object.PSObject.Properties[$Name].Value
    if (Test-IsArray -Value $value) { return ,$value }
    return $value
}

function Test-IsObject {
    param($Value)
    return ($null -ne $Value -and (($Value -is [System.Management.Automation.PSCustomObject]) -or ($Value -is [System.Collections.IDictionary])))
}

function Test-IsArray {
    param($Value)
    return ($Value -is [System.Array] -or $Value -is [System.Collections.IList])
}

function Assert-Object {
    param($Value, [string]$Path)
    if (-not (Test-IsObject -Value $Value)) {
        Add-ValidationError "$Path must be an object."
        return $false
    }
    return $true
}

function Assert-Array {
    param($Value, [string]$Path, [switch]$NonEmpty)
    if (-not (Test-IsArray -Value $Value)) {
        Add-ValidationError "$Path must be an array."
        return $false
    }
    if ($NonEmpty -and @($Value).Count -eq 0) {
        Add-ValidationError "$Path must not be empty."
        return $false
    }
    return $true
}

function Assert-NonEmptyString {
    param($Value, [string]$Path)
    if (-not ($Value -is [string]) -or [string]::IsNullOrWhiteSpace($Value)) {
        Add-ValidationError "$Path must be a non-empty string."
        return $false
    }
    return $true
}

function Assert-ExactString {
    param($Value, [string]$Expected, [string]$Path)
    if (-not ($Value -is [string]) -or $Value -cne $Expected) {
        Add-ValidationError "$Path must equal '$Expected'."
        return $false
    }
    return $true
}

function Assert-Sha256 {
    param($Value, [string]$Path)
    if (-not ($Value -is [string]) -or $Value -notmatch '^[0-9A-Fa-f]{64}$') {
        Add-ValidationError "$Path must be a 64-character SHA-256 value."
        return $false
    }
    return $true
}

function Assert-PositiveInteger {
    param($Value, [string]$Path, [switch]$AllowZero)
    $integerTypes = @(
        [byte], [sbyte], [int16], [uint16], [int32], [uint32], [int64], [uint64]
    )
    $isInteger = $false
    foreach ($type in $integerTypes) {
        if ($Value -is $type) { $isInteger = $true; break }
    }
    if (-not $isInteger -or ([decimal]$Value -lt 0) -or (-not $AllowZero -and [decimal]$Value -eq 0)) {
        $rule = if ($AllowZero) { 'a non-negative integer' } else { 'a positive integer' }
        Add-ValidationError "$Path must be $rule."
        return $false
    }
    return $true
}

function Get-PropertyNames {
    param($Object)
    if ($null -eq $Object) { return @() }
    return @($Object.PSObject.Properties | ForEach-Object { $_.Name })
}

function Assert-ExactPropertyNames {
    param($Object, [string[]]$Expected, [string]$Path)
    if (-not (Assert-Object -Value $Object -Path $Path)) { return $false }
    $actual = @(Get-PropertyNames -Object $Object)
    $missing = @($Expected | Where-Object { $actual -cnotcontains $_ })
    $extra = @($actual | Where-Object { $Expected -cnotcontains $_ })
    foreach ($name in $missing) { Add-ValidationError "$Path.$name is required." }
    foreach ($name in $extra) { Add-ValidationError "$Path contains unsupported key '$name'." }
    return ($missing.Count -eq 0 -and $extra.Count -eq 0)
}

function Find-ForbiddenPacketHashField {
    param($Value, [string]$Path)
    if ($null -eq $Value -or $Value -is [string] -or $Value -is [System.ValueType]) { return }
    if (Test-IsArray -Value $Value) {
        $index = 0
        foreach ($item in @($Value)) {
            Find-ForbiddenPacketHashField -Value $item -Path "$Path[$index]"
            $index++
        }
        return
    }
    if (-not (Test-IsObject -Value $Value)) { return }
    foreach ($property in $Value.PSObject.Properties) {
        if ($property.Name -ceq 'packet_sha256') {
            Add-ValidationError "$Path.packet_sha256 is forbidden because a packet cannot contain its own hash."
        }
        Find-ForbiddenPacketHashField -Value $property.Value -Path "$Path.$($property.Name)"
    }
}

function Normalize-ContractPath {
    param([string]$Path)
    if ($null -eq $Path) { return $null }
    return ($Path -replace '\\', '/').TrimStart('./')
}

function Test-PathInsideProject {
    param([string]$FullPath)
    $rootWithSeparator = $script:ProjectRootFull + [System.IO.Path]::DirectorySeparatorChar
    return ($FullPath.StartsWith($rootWithSeparator, [System.StringComparison]::OrdinalIgnoreCase))
}

function Assert-NoReparseSegments {
    param([string]$FullPath, [string]$Label)
    try {
        $rootItem = Get-Item -LiteralPath $script:ProjectRootFull -Force
        if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            Add-ValidationError "$Label is unsafe because ProjectRoot is a reparse point."
            return $false
        }
        $relative = $FullPath.Substring($script:ProjectRootFull.Length).TrimStart([char[]]'\/')
        $current = $script:ProjectRootFull
        foreach ($segment in ($relative -split '[\\/]')) {
            if ([string]::IsNullOrWhiteSpace($segment)) { continue }
            $current = Join-Path $current $segment
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                Add-ValidationError "$Label contains a reparse point: $current"
                return $false
            }
        }
    } catch {
        Add-ValidationError "$Label reparse-point check failed: $($_.Exception.Message)"
        return $false
    }
    return $true
}

function Resolve-ProjectFile {
    param($PathValue, [string]$Label, [switch]$NoReparse)
    if (-not (Assert-NonEmptyString -Value $PathValue -Path $Label)) { return $null }
    try {
        $candidate = if ([System.IO.Path]::IsPathRooted($PathValue)) {
            [System.IO.Path]::GetFullPath($PathValue)
        } else {
            [System.IO.Path]::GetFullPath((Join-Path $script:ProjectRootFull $PathValue))
        }
    } catch {
        Add-ValidationError "$Label is not a valid path: $($_.Exception.Message)"
        return $null
    }
    if (-not (Test-PathInsideProject -FullPath $candidate)) {
        Add-ValidationError "$Label must stay inside ProjectRoot."
        return $null
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        Add-ValidationError "$Label does not exist as a file: $candidate"
        return $null
    }
    if ($NoReparse) { [void](Assert-NoReparseSegments -FullPath $candidate -Label $Label) }
    return $candidate
}

function Resolve-StoryFile {
    param($PathValue)
    if (-not (Assert-NonEmptyString -Value $PathValue -Path '$.authoritative_story.path')) { return $null }
    try {
        if ([System.IO.Path]::IsPathRooted($PathValue)) {
            $candidate = [System.IO.Path]::GetFullPath($PathValue)
        } else {
            $candidate = [System.IO.Path]::GetFullPath((Join-Path $script:ProjectRootFull $PathValue))
            if (-not (Test-PathInsideProject -FullPath $candidate)) {
                Add-ValidationError '$.authoritative_story.path must stay inside ProjectRoot when it is relative.'
                return $null
            }
        }
    } catch {
        Add-ValidationError "$.authoritative_story.path is not a valid path: $($_.Exception.Message)"
        return $null
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        Add-ValidationError "$.authoritative_story.path does not exist as a file: $candidate"
        return $null
    }
    return $candidate
}

function Assert-CurrentHash {
    param([string]$FullPath, $Expected, [string]$Path)
    if ([string]::IsNullOrWhiteSpace($FullPath) -or -not (Assert-Sha256 -Value $Expected -Path $Path)) { return }
    try {
        $actual = (Get-FileHash -LiteralPath $FullPath -Algorithm SHA256).Hash
        if ($actual -ine [string]$Expected) {
            Add-ValidationError "$Path does not match the current file hash."
        }
    } catch {
        Add-ValidationError "$Path could not be verified: $($_.Exception.Message)"
    }
}

function Get-Scope {
    param($Value, [string]$Path, [switch]$AllowEmpty)
    if (-not (Assert-Array -Value $Value -Path $Path -NonEmpty:(-not $AllowEmpty))) { return ,@() }
    $result = @()
    foreach ($item in @($Value)) {
        if (-not (Assert-NonEmptyString -Value $item -Path "$Path[]")) { continue }
        if ($result -ccontains $item) {
            Add-ValidationError "$Path contains duplicate value '$item'."
        } else {
            $result += $item
        }
    }
    return ,@($result)
}

function Test-SetEquals {
    param([string[]]$Left, [string[]]$Right)
    if ($Left.Count -ne $Right.Count) { return $false }
    foreach ($item in $Left) { if ($Right -cnotcontains $item) { return $false } }
    return $true
}

function Assert-ExternalActionMap {
    param($Map, [string]$Path)
    $keys = @('tts', 'document_editing', 'image_video_audio_generation', 'editing_export_cover_upload_publish')
    if (-not (Assert-ExactPropertyNames -Object $Map -Expected $keys -Path $Path)) { return @{} }
    $normalized = @{}
    foreach ($key in $keys) {
        $entry = Get-Field -Object $Map -Name $key -Path $Path -Required
        if (-not (Assert-ExactPropertyNames -Object $entry -Expected @('status', 'evidence', 'scope') -Path "$Path.$key")) { continue }
        $status = Get-Field -Object $entry -Name 'status' -Path "$Path.$key" -Required
        $evidence = Get-Field -Object $entry -Name 'evidence' -Path "$Path.$key" -Required
        $scopeValue = Get-Field -Object $entry -Name 'scope' -Path "$Path.$key" -Required
        $scope = Get-Scope -Value $scopeValue -Path "$Path.$key.scope" -AllowEmpty
        if ($status -cnotin @('not_authorized', 'authorized')) {
            Add-ValidationError "$Path.$key.status must be 'not_authorized' or 'authorized'."
        } elseif ($status -ceq 'not_authorized') {
            if ($null -ne $evidence) { Add-ValidationError "$Path.$key.evidence must be null when status is not_authorized." }
            if ($scope.Count -ne 0) { Add-ValidationError "$Path.$key.scope must be empty when status is not_authorized." }
        } else {
            [void](Assert-NonEmptyString -Value $evidence -Path "$Path.$key.evidence")
            if ($scope.Count -eq 0) { Add-ValidationError "$Path.$key.scope must not be empty when status is authorized." }
        }
        $normalized[$key] = @{ status = $status; scope = @($scope) }
    }
    return $normalized
}

try {
    if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
        Add-ValidationError 'ProjectRoot does not exist as a directory.'
    } else {
        $script:ProjectRootFull = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd([char[]]'\/')
        $script:PacketDirectory=Get-StudioDocumentRelativePath -ProjectRoot $script:ProjectRootFull -RelativePath '剧本/阶段完成包'
    }

    if (-not (Assert-Sha256 -Value $ExpectedPacketSha256 -Path 'ExpectedPacketSha256')) {
        $ExpectedPacketSha256 = ''
    }

    $packetFull = $null
    if ($null -ne $script:ProjectRootFull) {
        $packetFull = Resolve-ProjectFile -PathValue $PacketPath -Label 'PacketPath' -NoReparse
    }

    $packet = $null
    $packetRaw = $null
    $packetRelative = $null
    $filenameVersion = $null
    if ($null -ne $packetFull) {
        $packetRelative = ($packetFull.Substring($script:ProjectRootFull.Length).TrimStart([char[]]'\/') -replace '\\', '/')
        if ($packetRelative -cnotmatch ('^'+[regex]::Escape($script:PacketDirectory)+'/B阶段完成包_(v\d{2,})\.json$')) {
            Add-ValidationError "PacketPath must be named $($script:PacketDirectory)/B阶段完成包_vNN.json."
        } else {
            $filenameVersion = $Matches[1]
        }
        $actualPacketHash = (Get-FileHash -LiteralPath $packetFull -Algorithm SHA256).Hash
        if (-not [string]::IsNullOrWhiteSpace($ExpectedPacketSha256) -and $actualPacketHash -ine $ExpectedPacketSha256) {
            Add-ValidationError 'ExpectedPacketSha256 does not match the current packet file.'
        }
        try {
            $packetRaw = Get-Content -LiteralPath $packetFull -Raw -Encoding UTF8
            $packet = $packetRaw | ConvertFrom-Json
        } catch {
            Add-ValidationError "Packet JSON could not be parsed: $($_.Exception.Message)"
        }
    }

    if ($null -ne $packet) {
        Find-ForbiddenPacketHashField -Value $packet -Path '$'
        $requiredTop = @(
            'contract', 'contract_version', 'packet_version', 'supersedes_packet', 'handoff_id',
            'handoff_revision', 'result', 'checked_at', 'source_b_task_id', 'a_task_envelope',
            'b_record', 'authoritative_story', 'professional_self_check', 'user_decisions',
            'released_sxx', 'rights_and_safety', 'external_actions', 'blocking_items',
            'non_blocking_items', 'unique_next_step'
        )
        [void](Assert-ExactPropertyNames -Object $packet -Expected $requiredTop -Path '$')

        [void](Assert-ExactString -Value (Get-Field $packet 'contract' '$') -Expected 'B_STAGE_READY' -Path '$.contract')
        [void](Assert-ExactString -Value (Get-Field $packet 'contract_version' '$') -Expected 'v1' -Path '$.contract_version')
        [void](Assert-ExactString -Value (Get-Field $packet 'result' '$') -Expected 'PASS' -Path '$.result')
        $packetVersion = Get-Field $packet 'packet_version' '$'
        if (-not ($packetVersion -is [string]) -or $packetVersion -cnotmatch '^v\d{2,}$') {
            Add-ValidationError '$.packet_version must match vNN.'
        } elseif ($null -ne $filenameVersion -and $packetVersion -cne $filenameVersion) {
            Add-ValidationError '$.packet_version must exactly match the filename version.'
        }
        $handoffId = Get-Field $packet 'handoff_id' '$'
        $handoffRevision = Get-Field $packet 'handoff_revision' '$'
        [void](Assert-NonEmptyString -Value $handoffId -Path '$.handoff_id')
        [void](Assert-PositiveInteger -Value $handoffRevision -Path '$.handoff_revision')
        [void](Assert-NonEmptyString -Value (Get-Field $packet 'source_b_task_id' '$') -Path '$.source_b_task_id')
        $supersedes = Get-Field $packet 'supersedes_packet' '$'
        if ($null -ne $supersedes) {
            if (Assert-ExactPropertyNames $supersedes @('path', 'packet_version', 'sha256', 'handoff_revision') '$.supersedes_packet') {
                $superPath = Normalize-ContractPath (Get-Field $supersedes 'path' '$.supersedes_packet')
                if (-not ($superPath -is [string]) -or $superPath -cnotmatch ('^'+[regex]::Escape($script:PacketDirectory)+'/B阶段完成包_v\d{2,}\.json$')) { Add-ValidationError '$.supersedes_packet.path is invalid.' }
                $superVersion = Get-Field $supersedes 'packet_version' '$.supersedes_packet'
                if (-not ($superVersion -is [string]) -or $superVersion -cnotmatch '^v\d{2,}$') { Add-ValidationError '$.supersedes_packet.packet_version must match vNN.' }
                $superSha = Get-Field $supersedes 'sha256' '$.supersedes_packet'
                $superRevision = Get-Field $supersedes 'handoff_revision' '$.supersedes_packet'
                [void](Assert-Sha256 $superSha '$.supersedes_packet.sha256')
                [void](Assert-PositiveInteger $superRevision '$.supersedes_packet.handoff_revision')
                if ($superPath -is [string] -and $superVersion -is [string]) {
                    $superNameVersion = $null
                    if ($superPath -match 'B阶段完成包_(v\d{2,})\.json$') { $superNameVersion = $Matches[1] }
                    if ($null -ne $superNameVersion -and $superNameVersion -cne $superVersion) { Add-ValidationError '$.supersedes_packet.path and packet_version do not match.' }
                }
                $superFull = Resolve-ProjectFile $superPath '$.supersedes_packet.path' -NoReparse
                Assert-CurrentHash $superFull $superSha '$.supersedes_packet.sha256'
                if ($null -ne $superFull) {
                    try { $superPacket = Get-Content -LiteralPath $superFull -Raw -Encoding UTF8 | ConvertFrom-Json } catch { Add-ValidationError "$.supersedes_packet.path is not valid JSON: $($_.Exception.Message)"; $superPacket = $null }
                    if ($null -ne $superPacket) {
                        if ((Get-Field $superPacket 'contract' '$.supersedes_packet.file') -cne 'B_STAGE_READY' -or (Get-Field $superPacket 'contract_version' '$.supersedes_packet.file') -cne 'v1') { Add-ValidationError '$.supersedes_packet file must be B_STAGE_READY v1.' }
                        if ((Get-Field $superPacket 'packet_version' '$.supersedes_packet.file') -cne $superVersion) { Add-ValidationError '$.supersedes_packet.packet_version does not match the prior packet.' }
                        if ((Get-Field $superPacket 'handoff_id' '$.supersedes_packet.file') -cne $handoffId) { Add-ValidationError '$.supersedes_packet must use the same handoff_id.' }
                        $oldRevision = Get-Field $superPacket 'handoff_revision' '$.supersedes_packet.file'
                        if ($oldRevision -ne $superRevision -or $handoffRevision -ne ($oldRevision + 1)) { Add-ValidationError '$.supersedes_packet handoff_revision chain must increase by exactly one.' }
                        if ((Get-Field $superPacket 'source_b_task_id' '$.supersedes_packet.file') -cne (Get-Field $packet 'source_b_task_id' '$')) { Add-ValidationError '$.supersedes_packet must come from the same source_b_task_id.' }
                    }
                }
            }
        } elseif ($handoffRevision -ne 1) {
            Add-ValidationError '$.supersedes_packet may be null only when handoff_revision is 1.'
        }
        $checkedAt = Get-Field $packet 'checked_at' '$'
        $parsedDate = [DateTimeOffset]::MinValue
        $dateValid = $false
        if ($null -ne $packetRaw) {
            $rawDateMatches = [regex]::Matches($packetRaw, '"checked_at"\s*:\s*"(?<value>[^"]*)"')
            if ($rawDateMatches.Count -eq 1) {
                $rawCheckedAt = $rawDateMatches[0].Groups['value'].Value
                $dateValid = ($rawCheckedAt -match '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$' -and [DateTimeOffset]::TryParse($rawCheckedAt, [ref]$parsedDate))
            }
        }
        if (-not $dateValid) {
            Add-ValidationError '$.checked_at must be an ISO-8601 timestamp with a timezone.'
        }

        $envelope = Get-Field $packet 'a_task_envelope' '$'
        $boundary = @{}
        $envelopeFields = @('a_task_id', 'a_task_version', 'a_file_path', 'a_file_sha256', 'target_role', 'stage_goal', 'input_scope', 'out_of_scope', 'writeback_scope', 'external_action_boundary', 'envelope_match')
        if (Assert-ExactPropertyNames -Object $envelope -Expected $envelopeFields -Path '$.a_task_envelope') {
            [void](Assert-NonEmptyString (Get-Field $envelope 'a_task_id' '$.a_task_envelope') '$.a_task_envelope.a_task_id')
            [void](Assert-NonEmptyString (Get-Field $envelope 'a_task_version' '$.a_task_envelope') '$.a_task_envelope.a_task_version')
            [void](Assert-ExactString (Normalize-ContractPath (Get-Field $envelope 'a_file_path' '$.a_task_envelope')) 'A_总控.md' '$.a_task_envelope.a_file_path')
            [void](Assert-ExactString (Get-Field $envelope 'target_role' '$.a_task_envelope') 'B' '$.a_task_envelope.target_role')
            [void](Assert-NonEmptyString (Get-Field $envelope 'stage_goal' '$.a_task_envelope') '$.a_task_envelope.stage_goal')
            [void](Get-Scope (Get-Field $envelope 'input_scope' '$.a_task_envelope') '$.a_task_envelope.input_scope')
            [void](Get-Scope (Get-Field $envelope 'out_of_scope' '$.a_task_envelope') '$.a_task_envelope.out_of_scope' -AllowEmpty)
            $writeback = @((Get-Scope (Get-Field $envelope 'writeback_scope' '$.a_task_envelope') '$.a_task_envelope.writeback_scope') | ForEach-Object { Normalize-ContractPath $_ })
            if ($null -ne $packetRelative -and -not (Test-SetEquals -Left @($writeback) -Right @('B_故事剧本.md', $packetRelative))) {
                Add-ValidationError '$.a_task_envelope.writeback_scope must contain exactly B_故事剧本.md and the current packet path.'
            }
            [void](Assert-ExactString (Get-Field $envelope 'envelope_match' '$.a_task_envelope') 'PASS' '$.a_task_envelope.envelope_match')
            $boundary = Assert-ExternalActionMap (Get-Field $envelope 'external_action_boundary' '$.a_task_envelope') '$.a_task_envelope.external_action_boundary'
            $aFull = Resolve-ProjectFile (Get-Field $envelope 'a_file_path' '$.a_task_envelope') '$.a_task_envelope.a_file_path' -NoReparse
            Assert-CurrentHash $aFull (Get-Field $envelope 'a_file_sha256' '$.a_task_envelope') '$.a_task_envelope.a_file_sha256'
        }

        $bRecord = Get-Field $packet 'b_record' '$'
        if (Assert-ExactPropertyNames $bRecord @('path', 'version_or_anchor', 'sha256') '$.b_record') {
            [void](Assert-ExactString (Normalize-ContractPath (Get-Field $bRecord 'path' '$.b_record')) 'B_故事剧本.md' '$.b_record.path')
            [void](Assert-NonEmptyString (Get-Field $bRecord 'version_or_anchor' '$.b_record') '$.b_record.version_or_anchor')
            $bFull = Resolve-ProjectFile (Get-Field $bRecord 'path' '$.b_record') '$.b_record.path' -NoReparse
            Assert-CurrentHash $bFull (Get-Field $bRecord 'sha256' '$.b_record') '$.b_record.sha256'
        }

        $story = Get-Field $packet 'authoritative_story' '$'
        $storyScope = @()
        if (Assert-ExactPropertyNames $story @('carrier', 'path', 'version_id', 'sha256', 'exact_scope', 'word_lineage_status', 'word_lineage_evidence', 'word_conflicts') '$.authoritative_story') {
            $carrier = Get-Field $story 'carrier' '$.authoritative_story'
            if ($carrier -cnotin @('word', 'b_markdown', 'other_registered')) { Add-ValidationError '$.authoritative_story.carrier is invalid.' }
            [void](Assert-NonEmptyString (Get-Field $story 'version_id' '$.authoritative_story') '$.authoritative_story.version_id')
            $storyScope = Get-Scope (Get-Field $story 'exact_scope' '$.authoritative_story') '$.authoritative_story.exact_scope'
            [void](Assert-ExactString (Get-Field $story 'word_conflicts' '$.authoritative_story') 'none' '$.authoritative_story.word_conflicts')
            $lineage = Get-Field $story 'word_lineage_status' '$.authoritative_story'
            $lineageEvidence = Get-Field $story 'word_lineage_evidence' '$.authoritative_story'
            if ($carrier -ceq 'word') {
                if ($lineage -cne 'released') { Add-ValidationError '$.authoritative_story.word_lineage_status must be released for Word.' }
                if (Assert-ExactPropertyNames $lineageEvidence @('path', 'version_or_anchor', 'sha256') '$.authoritative_story.word_lineage_evidence') {
                    [void](Assert-NonEmptyString (Get-Field $lineageEvidence 'version_or_anchor' '$.authoritative_story.word_lineage_evidence') '$.authoritative_story.word_lineage_evidence.version_or_anchor')
                    $lineageFull = Resolve-ProjectFile (Get-Field $lineageEvidence 'path' '$.authoritative_story.word_lineage_evidence') '$.authoritative_story.word_lineage_evidence.path' -NoReparse
                    Assert-CurrentHash $lineageFull (Get-Field $lineageEvidence 'sha256' '$.authoritative_story.word_lineage_evidence') '$.authoritative_story.word_lineage_evidence.sha256'
                }
            } else {
                if ($lineage -cne 'not_applicable') { Add-ValidationError '$.authoritative_story.word_lineage_status must be not_applicable for a non-Word carrier.' }
                if ($null -ne $lineageEvidence) { Add-ValidationError '$.authoritative_story.word_lineage_evidence must be null for a non-Word carrier.' }
            }
            $storyFull = Resolve-StoryFile (Get-Field $story 'path' '$.authoritative_story')
            Assert-CurrentHash $storyFull (Get-Field $story 'sha256' '$.authoritative_story') '$.authoritative_story.sha256'
        }

        $selfCheck = Get-Field $packet 'professional_self_check' '$'
        $selfCheckFields = @('overall', 'evidence_path', 'evidence_version_or_anchor', 'evidence_sha256', 'whole_story_categories', 'expected_scene_count', 'checked_scene_count', 'scene_coverage_anchor')
        if (Assert-ExactPropertyNames $selfCheck $selfCheckFields '$.professional_self_check') {
            [void](Assert-ExactString (Get-Field $selfCheck 'overall' '$.professional_self_check') 'PASS' '$.professional_self_check.overall')
            [void](Assert-NonEmptyString (Get-Field $selfCheck 'evidence_version_or_anchor' '$.professional_self_check') '$.professional_self_check.evidence_version_or_anchor')
            [void](Assert-NonEmptyString (Get-Field $selfCheck 'scene_coverage_anchor' '$.professional_self_check') '$.professional_self_check.scene_coverage_anchor')
            $evidenceFull = Resolve-ProjectFile (Get-Field $selfCheck 'evidence_path' '$.professional_self_check') '$.professional_self_check.evidence_path' -NoReparse
            Assert-CurrentHash $evidenceFull (Get-Field $selfCheck 'evidence_sha256' '$.professional_self_check') '$.professional_self_check.evidence_sha256'
            $categories = Get-Field $selfCheck 'whole_story_categories' '$.professional_self_check'
            $requiredCategories = @('premise_and_rules', 'protagonist_arc_and_relationships', 'causality_reveals_and_payoffs', 'continuity', 'dialogue', 'visible_action_and_production_boundary', 'duration_rhythm_format_and_scope', 'rights_safety_privacy_and_synthetic_risk')
            if (Assert-Array $categories '$.professional_self_check.whole_story_categories') {
                if (@($categories).Count -ne 8) { Add-ValidationError '$.professional_self_check.whole_story_categories must contain exactly 8 entries.' }
                $seen = @()
                foreach ($category in @($categories)) {
                    if (-not (Assert-ExactPropertyNames $category @('id', 'result', 'evidence_anchor', 'na_reason') '$.professional_self_check.whole_story_categories[]')) { continue }
                    $id = Get-Field $category 'id' '$.professional_self_check.whole_story_categories[]'
                    if ($requiredCategories -cnotcontains $id) { Add-ValidationError "Unknown self-check category '$id'." }
                    elseif ($seen -ccontains $id) { Add-ValidationError "Duplicate self-check category '$id'." }
                    else { $seen += $id }
                    [void](Assert-NonEmptyString (Get-Field $category 'evidence_anchor' '$.professional_self_check.whole_story_categories[]') "$.professional_self_check.whole_story_categories[$id].evidence_anchor")
                    $categoryResult = Get-Field $category 'result' '$.professional_self_check.whole_story_categories[]'
                    $naReason = Get-Field $category 'na_reason' '$.professional_self_check.whole_story_categories[]'
                    if ($categoryResult -cnotin @('PASS', 'N/A')) { Add-ValidationError "Self-check category '$id' must be PASS or N/A." }
                    elseif ($categoryResult -ceq 'N/A') { [void](Assert-NonEmptyString $naReason "$.professional_self_check.whole_story_categories[$id].na_reason") }
                    elseif ($null -ne $naReason) { Add-ValidationError "Self-check category '$id' must have null na_reason when result is PASS." }
                }
                foreach ($id in $requiredCategories) { if ($seen -cnotcontains $id) { Add-ValidationError "Missing self-check category '$id'." } }
            }
            $expectedScenes = Get-Field $selfCheck 'expected_scene_count' '$.professional_self_check'
            $checkedScenes = Get-Field $selfCheck 'checked_scene_count' '$.professional_self_check'
            [void](Assert-PositiveInteger $expectedScenes '$.professional_self_check.expected_scene_count')
            [void](Assert-PositiveInteger $checkedScenes '$.professional_self_check.checked_scene_count')
            if ($null -ne $expectedScenes -and $null -ne $checkedScenes -and $expectedScenes -ne $checkedScenes) { Add-ValidationError '$.professional_self_check scene counts must be equal.' }
        }

        $decisions = Get-Field $packet 'user_decisions' '$'
        $creativeScope = @(); $recordedScope = @(); $productionScope = @()
        if (Assert-ExactPropertyNames $decisions @('creative_adoption', 'b_record_write', 'production_release') '$.user_decisions') {
            $creative = Get-Field $decisions 'creative_adoption' '$.user_decisions'
            if (Assert-ExactPropertyNames $creative @('status', 'exact_scope', 'artifact_version', 'artifact_sha256', 'evidence') '$.user_decisions.creative_adoption') {
                [void](Assert-ExactString (Get-Field $creative 'status' '$.user_decisions.creative_adoption') 'granted' '$.user_decisions.creative_adoption.status')
                $creativeScope = Get-Scope (Get-Field $creative 'exact_scope' '$.user_decisions.creative_adoption') '$.user_decisions.creative_adoption.exact_scope'
                [void](Assert-NonEmptyString (Get-Field $creative 'evidence' '$.user_decisions.creative_adoption') '$.user_decisions.creative_adoption.evidence')
                if ($null -ne $story) {
                    if ((Get-Field $creative 'artifact_version' '$.user_decisions.creative_adoption') -cne (Get-Field $story 'version_id' '$.authoritative_story')) { Add-ValidationError '$.user_decisions.creative_adoption.artifact_version must match authoritative_story.version_id.' }
                    if ((Get-Field $creative 'artifact_sha256' '$.user_decisions.creative_adoption') -ine (Get-Field $story 'sha256' '$.authoritative_story')) { Add-ValidationError '$.user_decisions.creative_adoption.artifact_sha256 must match authoritative_story.sha256.' }
                }
            }
            $recordWrite = Get-Field $decisions 'b_record_write' '$.user_decisions'
            if (Assert-ExactPropertyNames $recordWrite @('status', 'recorded_scope', 'evidence', 'b_write_completed', 'packet_write_completed') '$.user_decisions.b_record_write') {
                [void](Assert-ExactString (Get-Field $recordWrite 'status' '$.user_decisions.b_record_write') 'granted' '$.user_decisions.b_record_write.status')
                $recordedScope = Get-Scope (Get-Field $recordWrite 'recorded_scope' '$.user_decisions.b_record_write') '$.user_decisions.b_record_write.recorded_scope'
                [void](Assert-NonEmptyString (Get-Field $recordWrite 'evidence' '$.user_decisions.b_record_write') '$.user_decisions.b_record_write.evidence')
                if ((Get-Field $recordWrite 'b_write_completed' '$.user_decisions.b_record_write') -cne $true) { Add-ValidationError '$.user_decisions.b_record_write.b_write_completed must be true.' }
                if ((Get-Field $recordWrite 'packet_write_completed' '$.user_decisions.b_record_write') -cne $true) { Add-ValidationError '$.user_decisions.b_record_write.packet_write_completed must be true.' }
            }
            $release = Get-Field $decisions 'production_release' '$.user_decisions'
            if (Assert-ExactPropertyNames $release @('status', 'exact_scope', 'evidence') '$.user_decisions.production_release') {
                [void](Assert-ExactString (Get-Field $release 'status' '$.user_decisions.production_release') 'granted' '$.user_decisions.production_release.status')
                $productionScope = Get-Scope (Get-Field $release 'exact_scope' '$.user_decisions.production_release') '$.user_decisions.production_release.exact_scope'
                [void](Assert-NonEmptyString (Get-Field $release 'evidence' '$.user_decisions.production_release') '$.user_decisions.production_release.evidence')
            }
            foreach ($scopeRule in @(
                @{ name = 'creative_adoption.exact_scope'; values = $creativeScope },
                @{ name = 'b_record_write.recorded_scope'; values = $recordedScope },
                @{ name = 'production_release.exact_scope'; values = $productionScope },
                @{ name = 'authoritative_story.exact_scope'; values = $storyScope }
            )) {
                foreach ($id in @($scopeRule.values)) {
                    if ($id -cnotmatch '^S\d{2,}$') { Add-ValidationError "$.user_decisions.$($scopeRule.name) ID '$id' must match Sxx." }
                }
            }
            foreach ($id in $productionScope) {
                if ($creativeScope -cnotcontains $id) { Add-ValidationError "Production scope '$id' is outside creative_adoption.exact_scope." }
                if ($recordedScope -cnotcontains $id) { Add-ValidationError "Production scope '$id' is not covered by b_record_write.recorded_scope." }
            }
            if ($storyScope.Count -gt 0 -and -not (Test-SetEquals $storyScope $productionScope)) { Add-ValidationError '$.authoritative_story.exact_scope must exactly match production_release.exact_scope.' }
        }

        $released = Get-Field $packet 'released_sxx' '$'
        $releasedIds = @()
        if (Assert-Array $released '$.released_sxx' -NonEmpty) {
            foreach ($item in @($released)) {
                if (-not (Assert-ExactPropertyNames $item @('id', 'version', 'b_anchor', 'story_scope', 'production_boundary') '$.released_sxx[]')) { continue }
                $id = Get-Field $item 'id' '$.released_sxx[]'
                if (-not ($id -is [string]) -or $id -cnotmatch '^S\d{2,}$') { Add-ValidationError '$.released_sxx[].id must match Sxx.' }
                elseif ($releasedIds -ccontains $id) { Add-ValidationError "Duplicate released_sxx ID '$id'." }
                else { $releasedIds += $id }
                $version = Get-Field $item 'version' '$.released_sxx[]'
                if (-not ($version -is [string]) -or $version -cnotmatch '^v\d{2,}$') { Add-ValidationError "released_sxx '$id' version must match vNN." }
                [void](Assert-NonEmptyString (Get-Field $item 'b_anchor' '$.released_sxx[]') "$.released_sxx[$id].b_anchor")
                [void](Get-Scope (Get-Field $item 'story_scope' '$.released_sxx[]') "$.released_sxx[$id].story_scope")
                [void](Assert-NonEmptyString (Get-Field $item 'production_boundary' '$.released_sxx[]') "$.released_sxx[$id].production_boundary")
            }
        }
        if (-not (Test-SetEquals $releasedIds $productionScope)) { Add-ValidationError '$.released_sxx IDs must exactly equal production_release.exact_scope.' }

        $safety = Get-Field $packet 'rights_and_safety' '$'
        if (Assert-ExactPropertyNames $safety @('stage_status', 'current_blockers', 'downstream_conditions', 'evidence') '$.rights_and_safety') {
            [void](Assert-ExactString (Get-Field $safety 'stage_status' '$.rights_and_safety') 'CLEAR' '$.rights_and_safety.stage_status')
            $safetyBlockers = Get-Field $safety 'current_blockers' '$.rights_and_safety'
            if (Assert-Array $safetyBlockers '$.rights_and_safety.current_blockers') { if (@($safetyBlockers).Count -ne 0) { Add-ValidationError '$.rights_and_safety.current_blockers must be empty.' } }
            [void](Assert-NonEmptyString (Get-Field $safety 'downstream_conditions' '$.rights_and_safety') '$.rights_and_safety.downstream_conditions')
            [void](Assert-NonEmptyString (Get-Field $safety 'evidence' '$.rights_and_safety') '$.rights_and_safety.evidence')
        }
        $blocking = Get-Field $packet 'blocking_items' '$'
        if (Assert-Array $blocking '$.blocking_items') { if (@($blocking).Count -ne 0) { Add-ValidationError '$.blocking_items must be empty.' } }
        $nonBlocking = Get-Field $packet 'non_blocking_items' '$'
        if (Assert-Array $nonBlocking '$.non_blocking_items') {
            foreach ($item in @($nonBlocking)) {
                if (Assert-ExactPropertyNames $item @('description', 'owner', 'milestone') '$.non_blocking_items[]') {
                    [void](Assert-NonEmptyString (Get-Field $item 'description' '$.non_blocking_items[]') '$.non_blocking_items[].description')
                    [void](Assert-NonEmptyString (Get-Field $item 'owner' '$.non_blocking_items[]') '$.non_blocking_items[].owner')
                    [void](Assert-NonEmptyString (Get-Field $item 'milestone' '$.non_blocking_items[]') '$.non_blocking_items[].milestone')
                }
            }
        }

        $actions = Assert-ExternalActionMap (Get-Field $packet 'external_actions' '$') '$.external_actions'
        foreach ($key in @('tts', 'document_editing', 'image_video_audio_generation', 'editing_export_cover_upload_publish')) {
            if (-not $actions.ContainsKey($key) -or -not $boundary.ContainsKey($key)) { continue }
            if ($actions[$key].status -ceq 'authorized') {
                if ($boundary[$key].status -cne 'authorized') {
                    Add-ValidationError "$.external_actions.$key claims authorization beyond a not_authorized task envelope."
                } else {
                    foreach ($scopeItem in @($actions[$key].scope)) {
                        if (@($boundary[$key].scope) -cnotcontains $scopeItem) { Add-ValidationError "$.external_actions.$key scope '$scopeItem' exceeds the task envelope." }
                    }
                }
            }
        }
        [void](Assert-ExactString (Get-Field $packet 'unique_next_step' '$') 'A 按 B_STAGE_READY v1 做完整性验收；通过后直接派 C' '$.unique_next_step')
    }
} catch {
    Add-ValidationError "Validator failed safely: $($_.Exception.Message) | $($_.ScriptStackTrace)"
}

$result = [ordered]@{
    valid = ($script:ValidationErrors.Count -eq 0)
    errors = @($script:ValidationErrors)
}
[Console]::Out.WriteLine(($result | ConvertTo-Json -Compress -Depth 8))
if (-not $result.valid) { exit 1 }
exit 0
