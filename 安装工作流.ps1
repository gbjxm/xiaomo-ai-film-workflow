[CmdletBinding()]
param(
    [string]$InstallRoot,
    [string]$KnowledgeTreeConfig,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'

function Get-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Read-Utf8Text {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
}

function Write-Utf8BomText {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    $encoding = New-Object System.Text.UTF8Encoding($true)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Get-RelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$ItemPath
    )
    $base = (Get-FullPath $BasePath).TrimEnd([char[]]'\/')
    $item = Get-FullPath $ItemPath
    $prefix = $base + [System.IO.Path]::DirectorySeparatorChar
    if (-not $item.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "路径不在预期目录中：$item"
    }
    return $item.Substring($prefix.Length).Replace('\', '/')
}

function Get-TreeManifest {
    param([Parameter(Mandatory = $true)][string]$Root)
    return @(
        Get-SafeTreeItems -Root $Root | Where-Object { -not $_.PSIsContainer } |
            Sort-Object FullName |
            ForEach-Object {
                [pscustomobject]@{
                    path = Get-RelativePath -BasePath $Root -ItemPath $_.FullName
                    length = $_.Length
                    sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
                }
            }
    )
}

function Convert-ManifestToLines {
    param([Parameter(Mandatory = $true)][array]$Manifest)
    return @($Manifest | ForEach-Object { '{0}|{1}|{2}' -f $_.path, $_.length, $_.sha256 })
}

function Assert-TreeEqual {
    param(
        [Parameter(Mandatory = $true)][string]$ExpectedRoot,
        [Parameter(Mandatory = $true)][string]$ActualRoot,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $ActualRoot -PathType Container)) {
        throw "$Label 不存在：$ActualRoot"
    }
    $expected = @(Convert-ManifestToLines -Manifest @(Get-TreeManifest $ExpectedRoot))
    $actual = @(Convert-ManifestToLines -Manifest @(Get-TreeManifest $ActualRoot))
    $difference = @(Compare-Object -ReferenceObject $expected -DifferenceObject $actual -SyncWindow 0)
    if ($difference.Count -ne 0) {
        $details = ($difference | Select-Object -First 8 | ForEach-Object { '{0} {1}' -f $_.SideIndicator, $_.InputObject }) -join '; '
        throw "$Label 内容校验失败：$details"
    }
}

function Assert-SkillSource {
    param(
        [Parameter(Mandatory = $true)][string]$SkillRoot,
        [Parameter(Mandatory = $true)][string]$ExpectedName
    )
    $skillFile = Join-Path $SkillRoot 'SKILL.md'
    if (-not (Test-Path -LiteralPath $skillFile -PathType Leaf)) {
        throw "Skill 缺少 SKILL.md：$ExpectedName"
    }
    $content = Read-Utf8Text $skillFile
    $match = [regex]::Match($content, '(?ms)\A---\s*\r?\n(?<frontmatter>.*?)\r?\n---')
    if (-not $match.Success) {
        throw "Skill frontmatter 无效：$ExpectedName"
    }
    $nameMatch = [regex]::Match($match.Groups['frontmatter'].Value, '(?m)^name:\s*(?<name>[^\r\n]+)\s*$')
    if (-not $nameMatch.Success -or $nameMatch.Groups['name'].Value.Trim() -ne $ExpectedName) {
        throw "Skill 名称与清单不一致：$ExpectedName"
    }
    if (@(Get-TreeManifest $SkillRoot).Count -eq 0) {
        throw "Skill 目录为空：$ExpectedName"
    }
}

function Assert-DirectChild {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Parent
    )
    $pathFull = Get-FullPath $Path
    $parentFull = (Get-FullPath $Parent).TrimEnd([char[]]'\/')
    $actualParent = (Get-FullPath (Split-Path -Parent $pathFull)).TrimEnd([char[]]'\/')
    if (-not $actualParent.Equals($parentFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝操作预期目录之外的路径：$pathFull"
    }
}



function Assert-NoReparsePath {

    param([Parameter(Mandatory = $true)][string]$Path)

    $current = Get-FullPath $Path

    while (-not [string]::IsNullOrWhiteSpace($current)) {

        $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue

        if ($null -ne $item -and ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {

            throw "拒绝符号链接或 Junction 路径：$current"

        }

        $parent = Split-Path -Parent $current

        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) { break }

        $current = $parent

    }

}



function Get-SafeTreeItems {

    param([Parameter(Mandatory = $true)][string]$Root)

    Assert-NoReparsePath -Path $Root

    $items = New-Object System.Collections.Generic.List[object]

    $pending = New-Object System.Collections.Generic.Queue[string]

    $pending.Enqueue((Get-FullPath $Root))

    while ($pending.Count -gt 0) {

        $directory = $pending.Dequeue()

        Assert-NoReparsePath -Path $directory

        foreach ($item in @(Get-ChildItem -LiteralPath $directory -Force)) {

            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {

                throw "拒绝 Skill 或事务目录中的符号链接或 Junction：$($item.FullName)"

            }

            $items.Add($item)

            if ($item.PSIsContainer) { $pending.Enqueue($item.FullName) }

        }

    }

    return @($items.ToArray())

}



function Get-PathFingerprint {

    param([Parameter(Mandatory = $true)][string]$Path)

    Assert-NoReparsePath -Path $Path

    if (-not (Test-Path -LiteralPath $Path)) { return 'absent' }

    if (Test-Path -LiteralPath $Path -PathType Leaf) {

        $file = Get-Item -LiteralPath $Path -Force

        return ('file|{0}|{1}' -f $file.Length, (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash)

    }

    $lines = @(

        Get-SafeTreeItems -Root $Path | Sort-Object FullName | ForEach-Object {

            $relative = Get-RelativePath -BasePath $Path -ItemPath $_.FullName

            if ($_.PSIsContainer) { 'directory|' + $relative }

            else { 'file|{0}|{1}|{2}' -f $relative, $_.Length, (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }

        }

    )

    $sha = [System.Security.Cryptography.SHA256]::Create()

    try {

        $bytes = [System.Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))

        return 'tree|' + ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '')

    } finally { $sha.Dispose() }

}



function Assert-PathFingerprint {

    param([string]$Path, [string]$Expected, [string]$Label)

    if ((Get-PathFingerprint -Path $Path) -ne $Expected) {

        throw "写前指纹已变化，保留外部修改并停止：$Label；$Path"

    }

}



function Move-GuardedItem {

    param([string]$Path, [string]$Destination, [string]$SourceParent, [string]$DestinationParent)

    Assert-DirectChild -Path $Path -Parent $SourceParent

    Assert-DirectChild -Path $Destination -Parent $DestinationParent

    Assert-NoReparsePath -Path $Path

    Assert-NoReparsePath -Path $Destination

    if (Test-Path -LiteralPath $Path -PathType Container) { $null = @(Get-SafeTreeItems -Root $Path) }

    if (Test-Path -LiteralPath $Destination) { throw "移动目标已存在，拒绝覆盖：$Destination" }

    Move-Item -LiteralPath $Path -Destination $Destination

}



function Remove-GuardedItem {

    param([string]$Path, [string]$Parent)

    Assert-DirectChild -Path $Path -Parent $Parent

    Assert-NoReparsePath -Path $Path

    if (Test-Path -LiteralPath $Path -PathType Container) {

        $null = @(Get-SafeTreeItems -Root $Path)

        Remove-Item -LiteralPath $Path -Recurse -Force

    } elseif (Test-Path -LiteralPath $Path) {

        Remove-Item -LiteralPath $Path -Force

    }

}



function Restore-GuardedBackup {

    param([string]$Path, [string]$Destination, [string]$SourceParent, [string]$DestinationParent)

    Assert-DirectChild -Path $Path -Parent $SourceParent

    Assert-DirectChild -Path $Destination -Parent $DestinationParent

    Assert-NoReparsePath -Path $Path

    Assert-NoReparsePath -Path $Destination

    if (Test-Path -LiteralPath $Destination) { throw "恢复目标已存在，保留备份：$Destination" }

    if (Test-Path -LiteralPath $Path -PathType Container) { $null = @(Get-SafeTreeItems -Root $Path) }

    Copy-Item -LiteralPath $Path -Destination $Destination -Recurse

    if ((Get-PathFingerprint -Path $Path) -ne (Get-PathFingerprint -Path $Destination)) {

        throw "备份恢复校验失败：$Destination"

    }

}



function Ensure-GuardedDirectory {

    param([string]$Path)

    Assert-NoReparsePath -Path $Path

    if (-not (Test-Path -LiteralPath $Path)) { New-Item -ItemType Directory -Path $Path | Out-Null }

    Assert-NoReparsePath -Path $Path

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { throw "路径不是目录：$Path" }

}


function Assert-TemplateWorkflowVersion {
    param(
        [Parameter(Mandatory = $true)][string]$TemplatePath,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion
    )

    Assert-NoReparsePath -Path $TemplatePath
    $entryPath = Join-Path $TemplatePath '开始这里.md'
    if (-not (Test-Path -LiteralPath $entryPath -PathType Leaf)) { throw '模板缺少开始这里.md。' }
    Assert-NoReparsePath -Path $entryPath
    $entry = Read-Utf8Text $entryPath
    $releases = [regex]::Matches($entry, '(?im)^[ \t]*(?:>[ \t]*)?workflowRelease[ \t]*:[ \t]*(?<value>[^\r\n]*)\r?$')
    $studios = [regex]::Matches($entry, '(?im)^[ \t]*(?:>[ \t]*)?workflowStudio[ \t]*:[ \t]*(?<value>[^\r\n]*)\r?$')
    if ($releases.Count -gt 1 -or ($releases.Count -eq 1 -and $releases[0].Groups['value'].Value.Trim() -cne '1.0')) {
        throw '不支持或不唯一的 workflowRelease 标识；仅支持 1.0。'
    }
    if ($studios.Count -gt 1 -or ($studios.Count -eq 1 -and $studios[0].Groups['value'].Value.Trim() -cne 'studio-v0.9')) {
        throw '不支持或不唯一的 workflowStudio 标识。'
    }
    $isStudio = $studios.Count -eq 1
    if ($releases.Count -eq 1 -and -not $isStudio) { throw 'workflowRelease: 1.0 必须配套 workflowStudio: studio-v0.9。' }
    $actualVersion = if ($releases.Count -eq 1) { '1.0' } elseif ($isStudio) { '0.9' } else { '0.8' }
    if ($actualVersion -ne $ExpectedVersion) { throw '模板 workflowRelease/workflowStudio 与 manifest bundleVersion 不匹配。' }
    $capabilityPath = Join-Path $TemplatePath '工作室能力.json'
    if (-not $isStudio) {
        if (Test-Path -LiteralPath $capabilityPath) { throw '旧模板含工作室能力文件却缺少 workflowStudio 标识。' }
        return
    }
    if (-not (Test-Path -LiteralPath $capabilityPath -PathType Leaf)) { throw '工作室模板缺少工作室能力.json。' }
    Assert-NoReparsePath -Path $capabilityPath
    $capability = (Read-Utf8Text $capabilityPath) | ConvertFrom-Json
    if ($capability.schema -ne 'xiaomo.studio-capabilities/v1' -or $capability.workflowStudio -ne 'studio-v0.9') {
        throw '工作室能力 schema 或 workflowStudio 不匹配。'
    }
    foreach ($name in @('candidate_design', 'workset_runtime', 'adoption_transactions', 'media_execution')) {
        if ($capability.$name -isnot [bool]) { throw "工作室能力 $name 必须为布尔值。" }
    }
    if (-not $capability.candidate_design -or $capability.media_execution -or $capability.workset_runtime -ne $capability.adoption_transactions) {
        throw '工作室候选、运行时、采用或媒体能力不配套。'
    }
    if ([string]$capability.stage -eq 'active' -and -not $capability.workset_runtime) {
        throw '活动工作室必须同时启用 workset_runtime 与 adoption_transactions。'
    }
    if (($capability.workset_runtime -or $capability.PSObject.Properties.Name -contains 'runtime_schema') -and $capability.runtime_schema -ne 'xiaomo.studio-runtime/v1') {
        throw '运行时能力需要 runtime_schema=xiaomo.studio-runtime/v1。'
    }
}

function Get-ConfigPath {
    if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
        return Get-FullPath (Join-Path $env:CODEX_HOME 'xiaomo-ai-film-workflow.json')
    }
    if ([string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        throw '无法定位用户目录：USERPROFILE 为空。'
    }
    return Get-FullPath (Join-Path (Join-Path $env:USERPROFILE '.agents') 'xiaomo-ai-film-workflow.json')
}

function Resolve-KnowledgeTreeConfig {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $null
    }
    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        throw "知识树配置必须是绝对路径：$Path"
    }
    $full = Get-FullPath $Path
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
        throw "知识树配置不存在：$full"
    }
    $payload = (Read-Utf8Text $full) | ConvertFrom-Json
    if ([int]$payload.version -notin @(1, 2) -or [string]::IsNullOrWhiteSpace([string]$payload.knowledge_library)) {
        throw "知识树配置缺少受支持的 version 或 knowledge_library：$full"
    }
    return $full
}

$workflowRoot = Get-FullPath $PSScriptRoot
$sourceSkillsRoot = Get-FullPath (Join-Path $workflowRoot 'skills')
$manifestPath = Join-Path $sourceSkillsRoot 'manifest.json'
$projectsRoot = Get-FullPath (Join-Path $workflowRoot '项目')
$templateRoot = $null
Assert-NoReparsePath -Path $workflowRoot

if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
        $InstallRoot = Join-Path $env:CODEX_HOME 'skills'
    } elseif (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        $InstallRoot = Join-Path (Join-Path $env:USERPROFILE '.agents') 'skills'
    } else {
        throw '无法定位 Skill 安装目录：CODEX_HOME 和 USERPROFILE 均为空。'
    }
}
$installRootFull = Get-FullPath $InstallRoot
$installParent = Get-FullPath (Split-Path -Parent $installRootFull)
$configPath = Get-ConfigPath
$configParent = Get-FullPath (Split-Path -Parent $configPath)
$initialConfigFingerprint = Get-PathFingerprint -Path $configPath
$existingConfig = $null
if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    try { $existingConfig = (Read-Utf8Text $configPath) | ConvertFrom-Json } catch { $existingConfig = $null }
}
if ([string]::IsNullOrWhiteSpace($KnowledgeTreeConfig) -and $null -ne $existingConfig -and
    $null -ne $existingConfig.knowledgeBridge -and $existingConfig.knowledgeBridge.enabled -eq $true) {
    $KnowledgeTreeConfig = [string]$existingConfig.knowledgeBridge.configPath
}
$knowledgeConfigFull = Resolve-KnowledgeTreeConfig -Path $KnowledgeTreeConfig

foreach ($required in @($manifestPath, (Join-Path $workflowRoot '00_开始这里.md'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "工作流文件缺失：$required"
    }
}
$manifest = (Read-Utf8Text $manifestPath) | ConvertFrom-Json

if ([int]$manifest.schemaVersion -ne 1 -or [string]$manifest.bundleVersion -notin @('0.8', '0.9', '1.0')) {

    throw '不支持的工作流 manifest schemaVersion 或 bundleVersion；仅支持 0.8、0.9 与 1.0。'

}

$templateRoot = Get-FullPath (Join-Path $workflowRoot ('工作流\项目模板\小陌AI影视项目模板_v' + [string]$manifest.bundleVersion))

foreach ($required in @($projectsRoot, $templateRoot)) {
    if (-not (Test-Path -LiteralPath $required -PathType Container)) {
        throw "工作流目录缺失：$required"
    }
}

Assert-TemplateWorkflowVersion -TemplatePath $templateRoot -ExpectedVersion ([string]$manifest.bundleVersion)

$skillNames = @($manifest.skills)
$retiredSkillNames = @('prompt-generator')
if ($skillNames.Count -lt 1 -or @($skillNames | Select-Object -Unique).Count -ne $skillNames.Count) {
    throw 'skills/manifest.json 必须列出至少一个不重复的活动 Skill。'
}
if (@($skillNames | Where-Object { $retiredSkillNames -contains $_ }).Count -ne 0) {
    throw '已停用 Skill 不得继续出现在 skills/manifest.json。'
}
foreach ($skillName in $skillNames) {
    if ([string]::IsNullOrWhiteSpace([string]$skillName) -or [string]$skillName -notmatch '^[a-z0-9-]+$') {
        throw "Skill 清单名称无效：$skillName"
    }
    Assert-SkillSource -SkillRoot (Join-Path $sourceSkillsRoot $skillName) -ExpectedName $skillName
}

$expectedConfig = [ordered]@{
    schemaVersion = 2
    workflowVersion = [string]$manifest.bundleVersion
    workflowRoot = $workflowRoot
    projectsRoot = $projectsRoot
    templateRoot = $templateRoot
    skillsRoot = $installRootFull
    knowledgeBridge = [ordered]@{
        enabled = ($null -ne $knowledgeConfigFull)
        configPath = $knowledgeConfigFull
        skillName = 'apply-film-knowledge'
    }
}

if ($VerifyOnly) {
    foreach ($skillName in $skillNames) {
        Assert-TreeEqual -ExpectedRoot (Join-Path $sourceSkillsRoot $skillName) -ActualRoot (Join-Path $installRootFull $skillName) -Label "已安装 Skill $skillName"
    }
    foreach ($skillName in $retiredSkillNames) {
        if (Test-Path -LiteralPath (Join-Path $installRootFull $skillName)) {
            throw "已停用 Skill 仍残留在安装目录：$skillName"
        }
    }
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw "本机配置不存在：$configPath"
    }
    $actualConfig = (Read-Utf8Text $configPath) | ConvertFrom-Json
    foreach ($key in @('schemaVersion', 'workflowVersion', 'workflowRoot', 'projectsRoot', 'templateRoot', 'skillsRoot')) {
        if ([string]$actualConfig.$key -ne [string]$expectedConfig[$key]) {
            throw "本机配置不匹配：$key"
        }
    }
    foreach ($key in @('enabled', 'configPath', 'skillName')) {
        if ([string]$actualConfig.knowledgeBridge.$key -ne [string]$expectedConfig.knowledgeBridge[$key]) {
            throw "本机知识桥配置不匹配：$key"
        }
    }
    if ($expectedConfig.knowledgeBridge.enabled) {
        foreach ($required in @(
            (Join-Path $installRootFull 'apply-film-knowledge\scripts\retrieve_knowledge.py'),
            (Join-Path $installRootFull 'operate-personal-knowledge-tree\scripts\knowledge_tree_config.py')
        )) {
            if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
                throw "知识桥依赖不存在：$required"
            }
        }
    }
    [ordered]@{
        success = $true
        mode = 'verify'
        workflowRoot = $workflowRoot
        installRoot = $installRootFull
        configPath = $configPath
        knowledgeBridge = $expectedConfig.knowledgeBridge
        verifiedSkills = $skillNames
    } | ConvertTo-Json -Depth 5
    exit 0
}

foreach ($directory in @($installParent, $installRootFull, $configParent)) {

    Ensure-GuardedDirectory -Path $directory

}

$runId = (Get-Date -Format 'yyyyMMdd_HHmmss_fff') + '_' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$stageParent = Get-FullPath (Join-Path $installParent ('.xiaomo-ai-film-workflow-staging-' + $runId))
$backupParent = Get-FullPath (Join-Path $installParent 'xiaomo-ai-film-workflow-backups')

$backupRunRoot = Get-FullPath (Join-Path $backupParent $runId)

Assert-DirectChild -Path $backupParent -Parent $installParent

Assert-DirectChild -Path $backupRunRoot -Parent $backupParent

Assert-NoReparsePath -Path $backupRunRoot
Assert-DirectChild -Path $stageParent -Parent $installParent
$actions = New-Object System.Collections.Generic.List[object]
$processed = New-Object System.Collections.Generic.List[object]
$configBackup = $null
$configTemp = $null

$configInstalled = $false

$configWrittenFingerprint = $null

$rollbackIssues = New-Object System.Collections.Generic.List[string]



try {
    Ensure-GuardedDirectory -Path $stageParent
    foreach ($skillName in $skillNames) {
        $source = Get-FullPath (Join-Path $sourceSkillsRoot $skillName)
        $target = Get-FullPath (Join-Path $installRootFull $skillName)
        Assert-DirectChild -Path $target -Parent $installRootFull
        $expectedTarget = Get-PathFingerprint -Path $target
        $same = $false
        if (Test-Path -LiteralPath $target -PathType Container) {
            try {
                Assert-TreeEqual -ExpectedRoot $source -ActualRoot $target -Label $skillName
                $same = $true
            } catch {
                $same = $false
            }
        } elseif (Test-Path -LiteralPath $target) {
            throw "同名安装目标不是目录：$target"
        }

        if ($same) {
            $actions.Add([pscustomobject]@{ name = $skillName; status = 'unchanged'; source = $source; target = $target; stage = $null; backup = $null; expectedTarget = $expectedTarget })
            continue
        }

        $stage = Get-FullPath (Join-Path $stageParent $skillName)
        Assert-DirectChild -Path $stage -Parent $stageParent
        Copy-Item -LiteralPath $source -Destination $stage -Recurse
        Assert-TreeEqual -ExpectedRoot $source -ActualRoot $stage -Label "暂存 Skill $skillName"
        $backup = Get-FullPath (Join-Path $backupRunRoot $skillName)
        Assert-DirectChild -Path $backup -Parent $backupRunRoot
        $actions.Add([pscustomobject]@{ name = $skillName; status = 'pending'; source = $source; target = $target; stage = $stage; backup = $backup; expectedTarget = $expectedTarget; placedFingerprint = (Get-PathFingerprint -Path $stage); hadTarget = ($expectedTarget -ne 'absent'); movedBackup = $false; placed = $false })
    }

    foreach ($skillName in $retiredSkillNames) {
        $target = Get-FullPath (Join-Path $installRootFull $skillName)
        Assert-DirectChild -Path $target -Parent $installRootFull
        if (Test-Path -LiteralPath $target -PathType Container) {
            $backup = Get-FullPath (Join-Path $backupRunRoot $skillName)
            Assert-DirectChild -Path $backup -Parent $backupRunRoot
            $actions.Add([pscustomobject]@{ name = $skillName; status = 'retire-pending'; source = $null; target = $target; stage = $null; backup = $backup; expectedTarget = (Get-PathFingerprint -Path $target); placedFingerprint = 'absent'; hadTarget = $true; movedBackup = $false; placed = $false })
        } elseif (Test-Path -LiteralPath $target) {
            throw "已停用 Skill 的安装目标不是目录：$target"
        } else {
            $actions.Add([pscustomobject]@{ name = $skillName; status = 'absent'; source = $null; target = $target; stage = $null; backup = $null })
        }
    }

    # Check every mutation before committing the first Skill, then repeat next to each rename.

    Assert-PathFingerprint -Path $configPath -Expected $initialConfigFingerprint -Label '本机配置'

    foreach ($action in @($actions | Where-Object { $_.status -in @('pending', 'retire-pending') })) {

        Assert-PathFingerprint -Path $action.target -Expected $action.expectedTarget -Label $action.name

    }

    foreach ($action in @($actions | Where-Object { $_.status -in @('pending', 'retire-pending') })) {

        Assert-PathFingerprint -Path $action.target -Expected $action.expectedTarget -Label $action.name

        $processed.Add($action)

        if ($action.hadTarget) {

            Ensure-GuardedDirectory -Path $backupRunRoot

            Move-GuardedItem -Path $action.target -Destination $action.backup -SourceParent $installRootFull -DestinationParent $backupRunRoot

            $action.movedBackup = $true

        }

        if ($action.status -eq 'retire-pending') {

            $action.status = 'removed'

            continue

        }

        Move-GuardedItem -Path $action.stage -Destination $action.target -SourceParent $stageParent -DestinationParent $installRootFull

        $action.placed = $true

        Assert-TreeEqual -ExpectedRoot $action.source -ActualRoot $action.target -Label "安装后 Skill $($action.name)"

        $action.status = if ($action.hadTarget) { 'updated' } else { 'installed' }

    }



    $configJson = ($expectedConfig | ConvertTo-Json -Depth 5) + [Environment]::NewLine
    $configTemp = Get-FullPath (Join-Path $configParent ('.xiaomo-ai-film-workflow.config-' + $runId + '.tmp'))
    Assert-DirectChild -Path $configTemp -Parent $configParent
    Write-Utf8BomText -Path $configTemp -Content $configJson
    $null = (Read-Utf8Text $configTemp) | ConvertFrom-Json
    $configWrittenFingerprint = Get-PathFingerprint -Path $configTemp

    Assert-PathFingerprint -Path $configPath -Expected $initialConfigFingerprint -Label '本机配置'

    if ($initialConfigFingerprint -ne 'absent') {

        Ensure-GuardedDirectory -Path $backupRunRoot

        $configBackup = Get-FullPath (Join-Path $backupRunRoot 'xiaomo-ai-film-workflow.json')

        Move-GuardedItem -Path $configPath -Destination $configBackup -SourceParent $configParent -DestinationParent $backupRunRoot

    }

    Move-GuardedItem -Path $configTemp -Destination $configPath -SourceParent $configParent -DestinationParent $configParent

    $configTemp = $null

    $configInstalled = $true



    foreach ($skillName in $skillNames) {
        Assert-TreeEqual -ExpectedRoot (Join-Path $sourceSkillsRoot $skillName) -ActualRoot (Join-Path $installRootFull $skillName) -Label "最终 Skill $skillName"
    }
    foreach ($skillName in $retiredSkillNames) {
        if (Test-Path -LiteralPath (Join-Path $installRootFull $skillName)) {
            throw "已停用 Skill 未能移除：$skillName"
        }
    }

    [ordered]@{
        success = $true
        mode = 'install'
        workflowVersion = [string]$manifest.bundleVersion
        templateRoot = $templateRoot
        workflowRoot = $workflowRoot
        installRoot = $installRootFull
        configPath = $configPath
        knowledgeBridge = $expectedConfig.knowledgeBridge
        backupRoot = if (Test-Path -LiteralPath $backupRunRoot) { $backupRunRoot } else { $null }
        actions = @($actions | ForEach-Object { [ordered]@{ name = $_.name; status = $_.status; backup = if ($_.status -in @('updated', 'removed')) { $_.backup } else { $null } } })
    } | ConvertTo-Json -Depth 6
} catch {

    $installFailure = $_

    if ($configTemp -and (Test-Path -LiteralPath $configTemp)) {

        try { Remove-GuardedItem -Path $configTemp -Parent $configParent } catch { $rollbackIssues.Add($_.Exception.Message) }

    }

    try {

        if ($configInstalled) {

            Assert-PathFingerprint -Path $configPath -Expected $configWrittenFingerprint -Label '本次已写配置（回滚）'

            Remove-GuardedItem -Path $configPath -Parent $configParent

        }

        if ($configBackup -and (Test-Path -LiteralPath $configBackup)) {

            Restore-GuardedBackup -Path $configBackup -Destination $configPath -SourceParent $backupRunRoot -DestinationParent $configParent

        }

    } catch { $rollbackIssues.Add($_.Exception.Message) }

    for ($processedIndex = $processed.Count - 1; $processedIndex -ge 0; $processedIndex--) {

        $action = $processed[$processedIndex]

        try {

            if ($action.placed) {

                Assert-PathFingerprint -Path $action.target -Expected $action.placedFingerprint -Label ($action.name + '（回滚）')

                Remove-GuardedItem -Path $action.target -Parent $installRootFull

            }

            if ($action.movedBackup -and (Test-Path -LiteralPath $action.backup)) {

                Restore-GuardedBackup -Path $action.backup -Destination $action.target -SourceParent $backupRunRoot -DestinationParent $installRootFull

            }

        } catch { $rollbackIssues.Add($_.Exception.Message) }

    }

    if ($rollbackIssues.Count -gt 0) {

        Write-Warning ('部分回滚被外部修改或路径检查阻止，保留原件及事务备份：' + $backupRunRoot + '；' + ($rollbackIssues -join '；'))

    }

    throw $installFailure

} finally {

    if (Test-Path -LiteralPath $stageParent) {

        try { Remove-GuardedItem -Path $stageParent -Parent $installParent }

        catch { Write-Warning ('暂存目录保留供检查：' + $stageParent + '；' + $_.Exception.Message) }

    }

}

