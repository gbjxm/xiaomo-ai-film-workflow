[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [AllowEmptyString()]
    [string]$ProjectName,

    [string]$WorkflowRoot,

    [string]$ProjectsRoot,

    [string]$TemplateRoot,

    [switch]$NoOpen
)

$ErrorActionPreference = 'Stop'

try {
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [Console]::OutputEncoding = $utf8NoBom
} catch {
    # Output encoding is a convenience only; project creation must not depend on it.
}

$script:StagingPath = $null
$script:ProjectsRootFull = $null
$script:ConfigPath = $null

function Get-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path)
}

function Get-WorkflowConfigCandidates {
    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
        $candidates.Add((Join-Path $env:CODEX_HOME 'xiaomo-ai-film-workflow.json'))
    }
    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        $candidates.Add((Join-Path (Join-Path $env:USERPROFILE '.agents') 'xiaomo-ai-film-workflow.json'))
    }
    return @($candidates.ToArray() | Select-Object -Unique)
}

function Find-SourceWorkflowRoot {
    $current = Get-FullPath -Path $PSScriptRoot
    for ($depth = 0; $depth -lt 10; $depth++) {
        $marker = Join-Path $current '00_开始这里.md'
        $manifest = Join-Path $current 'skills\manifest.json'
        $sourceSkill = Join-Path $current 'skills\start-ai-film-project\SKILL.md'
        if ((Test-Path -LiteralPath $marker -PathType Leaf) -and
            (Test-Path -LiteralPath $manifest -PathType Leaf) -and
            (Test-Path -LiteralPath $sourceSkill -PathType Leaf)) {
            return $current
        }

        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) {
            break
        }
        $current = Get-FullPath -Path $parent
    }
    return $null
}

function Get-WorkflowBundleVersion {
    param([Parameter(Mandatory = $true)][string]$Root)

    $manifestPath = Join-Path $Root 'skills\manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "工作流缺少 skills/manifest.json：$Root。请使用完整工作流文件夹。"
    }
    try {
        $manifest = (Read-Utf8Text -Path $manifestPath) | ConvertFrom-Json
    } catch {
        throw "工作流 manifest 无法读取：$manifestPath"
    }
    $version = [string]$manifest.bundleVersion
    if ([int]$manifest.schemaVersion -ne 1 -or $version -notin @('0.8', '0.9', '1.0')) {
        throw "不支持的工作流 bundleVersion：$version"
    }
    return $version
}

function Resolve-WorkflowContext {
    param(
        [string]$RequestedWorkflowRoot,
        [string]$RequestedProjectsRoot,
        [string]$RequestedTemplateRoot
    )

    $resolvedWorkflowRoot = $null
    $resolvedProjectsRoot = $null
    $resolvedTemplateRoot = $null
    $config = $null
    $configPath = $null
    $expectedWorkflowVersion = $null
    $templateSource = $null

    if (-not [string]::IsNullOrWhiteSpace($RequestedWorkflowRoot)) {
        $resolvedWorkflowRoot = Get-FullPath -Path $RequestedWorkflowRoot
    } else {
        foreach ($candidate in @(Get-WorkflowConfigCandidates)) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                try {
                    $config = (Read-Utf8Text -Path $candidate) | ConvertFrom-Json
                } catch {
                    throw "工作流配置无法读取：$candidate。请重新运行安装工作流.ps1。"
                }
                $configPath = Get-FullPath -Path $candidate
                if ([string]::IsNullOrWhiteSpace([string]$config.workflowRoot)) {
                    throw "工作流配置缺少 workflowRoot：$candidate。请重新运行安装工作流.ps1。"
                }
                $resolvedWorkflowRoot = Get-FullPath -Path ([string]$config.workflowRoot)
                break
            }
        }
        if ([string]::IsNullOrWhiteSpace($resolvedWorkflowRoot)) {
            $resolvedWorkflowRoot = Find-SourceWorkflowRoot
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($resolvedWorkflowRoot) -and -not (Test-Path -LiteralPath $resolvedWorkflowRoot -PathType Container)) {
        throw "工作流根目录不存在：$resolvedWorkflowRoot。请重新运行安装工作流.ps1。"
    }

    if (-not [string]::IsNullOrWhiteSpace($RequestedProjectsRoot)) {
        $resolvedProjectsRoot = Get-FullPath -Path $RequestedProjectsRoot
    } elseif (-not [string]::IsNullOrWhiteSpace($RequestedWorkflowRoot)) {
        $resolvedProjectsRoot = Get-FullPath -Path (Join-Path $resolvedWorkflowRoot '项目')
    } elseif ($null -ne $config -and -not [string]::IsNullOrWhiteSpace([string]$config.projectsRoot)) {
        $resolvedProjectsRoot = Get-FullPath -Path ([string]$config.projectsRoot)
    } elseif (-not [string]::IsNullOrWhiteSpace($resolvedWorkflowRoot)) {
        $resolvedProjectsRoot = Get-FullPath -Path (Join-Path $resolvedWorkflowRoot '项目')
    }

    if (-not [string]::IsNullOrWhiteSpace($RequestedTemplateRoot)) {
        $resolvedTemplateRoot = Get-FullPath -Path $RequestedTemplateRoot
        $templateSource = 'explicit'
    } elseif (-not [string]::IsNullOrWhiteSpace($resolvedWorkflowRoot)) {
        $expectedWorkflowVersion = Get-WorkflowBundleVersion -Root $resolvedWorkflowRoot
        if ($null -ne $config -and -not [string]::IsNullOrWhiteSpace([string]$config.workflowVersion) -and
            [string]$config.workflowVersion -ne $expectedWorkflowVersion) {
            throw "安装配置 workflowVersion 与工作流 manifest 不一致。请重新运行安装工作流.ps1。"
        }
        if ($null -ne $config -and -not [string]::IsNullOrWhiteSpace([string]$config.templateRoot)) {
            $resolvedTemplateRoot = Get-FullPath -Path ([string]$config.templateRoot)
            $templateSource = 'config'
        } else {
            $resolvedTemplateRoot = Get-FullPath -Path (Join-Path $resolvedWorkflowRoot "工作流\项目模板\小陌AI影视项目模板_v$expectedWorkflowVersion")
            $templateSource = 'manifest'
        }
    }

    if ([string]::IsNullOrWhiteSpace($resolvedProjectsRoot) -or [string]::IsNullOrWhiteSpace($resolvedTemplateRoot)) {
        throw '无法定位工作流、项目目录或模板。请先在工作流根目录运行“安装工作流.ps1”，或显式传入 -WorkflowRoot/-ProjectsRoot/-TemplateRoot。'
    }
    return [pscustomobject]@{
        workflowRoot = $resolvedWorkflowRoot
        projectsRoot = $resolvedProjectsRoot
        templateRoot = $resolvedTemplateRoot
        configPath = $configPath
        expectedWorkflowVersion = $expectedWorkflowVersion
        templateSource = $templateSource
    }
}

function Get-RelativeItemPath {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$ItemPath
    )

    $baseFull = (Get-FullPath -Path $BasePath).TrimEnd([char[]]'\/')
    $prefix = $baseFull + [System.IO.Path]::DirectorySeparatorChar
    $itemFull = Get-FullPath -Path $ItemPath

    if (-not $itemFull.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "路径不在预期根目录中：$itemFull"
    }

    return $itemFull.Substring($prefix.Length).Replace('\', '/')
}

function Get-FileManifest {
    param([Parameter(Mandatory = $true)][string]$Root)

    $items = @(Get-ChildItem -LiteralPath $Root -Recurse -Force -File | Sort-Object FullName)
    return @($items | ForEach-Object {
        [pscustomobject]@{
            path   = Get-RelativeItemPath -BasePath $Root -ItemPath $_.FullName
            length = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
    })
}

function Get-DirectoryManifest {
    param([Parameter(Mandatory = $true)][string]$Root)

    $items = @(Get-ChildItem -LiteralPath $Root -Recurse -Force -Directory | Sort-Object FullName)
    return @($items | ForEach-Object {
        Get-RelativeItemPath -BasePath $Root -ItemPath $_.FullName
    })
}

function Convert-FileManifestToLines {
    param([Parameter(Mandatory = $true)][array]$Manifest)

    return @($Manifest | ForEach-Object { '{0}|{1}|{2}' -f $_.path, $_.length, $_.sha256 })
}

function Assert-SequenceEqual {
    param(
        [Parameter(Mandatory = $true)][array]$Expected,
        [Parameter(Mandatory = $true)][array]$Actual,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $difference = @(Compare-Object -ReferenceObject $Expected -DifferenceObject $Actual -SyncWindow 0)
    if ($difference.Count -ne 0) {
        $details = ($difference | ForEach-Object { '{0} {1}' -f $_.SideIndicator, $_.InputObject }) -join '; '
        throw "$Label 校验失败：$details"
    }
}

function Assert-SafeStagingPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$FolderName
    )

    $pathFull = Get-FullPath -Path $Path
    $rootFull = (Get-FullPath -Path $Root).TrimEnd([char[]]'\/')
    $parentFull = (Get-FullPath -Path (Split-Path -Parent $pathFull)).TrimEnd([char[]]'\/')
    $leaf = Split-Path -Leaf $pathFull
    $expectedPrefix = ".$FolderName.creating-"

    if (-not $parentFull.Equals($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理根目录之外的临时路径：$pathFull"
    }
    if (-not $leaf.StartsWith($expectedPrefix, [System.StringComparison]::Ordinal)) {
        throw "拒绝清理名称不匹配的临时路径：$pathFull"
    }
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


function Get-StudioTemplateMode {
    param([Parameter(Mandatory = $true)][string]$TemplatePath)

    $entryPath = Join-Path $TemplatePath '开始这里.md'
    if (-not (Test-Path -LiteralPath $entryPath -PathType Leaf)) {
        throw '模板缺少开始这里.md。'
    }
    $entry = Read-Utf8Text -Path $entryPath
    $markers = [regex]::Matches($entry, '(?im)^[ \t]*(?:>[ \t]*)?workflowStudio[ \t]*:[ \t]*([^\r\n]*)\r?$')
    $capabilityPath = Join-Path $TemplatePath '工作室能力.json'
    if ($markers.Count -eq 0) {
        if ((Split-Path -Leaf $TemplatePath) -match '_v(?:0\.9|1\.0)$' -or (Test-Path -LiteralPath $capabilityPath)) {
            throw 'V0.9 模板缺少 workflowStudio 标识，拒绝按旧单岗位启动。'
        }
        return $false
    }
    if ($markers.Count -ne 1 -or $markers[0].Groups[1].Value.Trim() -cne 'studio-v0.9') {
        throw '不支持或不唯一的 workflowStudio 标识。'
    }
    if (-not (Test-Path -LiteralPath $capabilityPath -PathType Leaf)) {
        throw 'V0.9 模板缺少工作室能力.json。'
    }
    $capability = (Read-Utf8Text -Path $capabilityPath) | ConvertFrom-Json
    if ($capability.schema -ne 'xiaomo.studio-capabilities/v1' -or $capability.workflowStudio -ne 'studio-v0.9') {
        throw '工作室能力 schema 或 workflowStudio 不匹配。'
    }
    foreach ($name in @('candidate_design', 'workset_runtime', 'adoption_transactions', 'media_execution')) {
        if ($capability.$name -isnot [bool]) { throw "工作室能力 $name 必须为布尔值。" }
    }
    if (-not $capability.candidate_design -or $capability.media_execution) {
        throw 'V0.9 必须启用 candidate_design 且关闭 media_execution。'
    }
    if ($capability.workset_runtime -ne $capability.adoption_transactions) {
        throw 'workset_runtime 与 adoption_transactions 必须同时开启或同时关闭。'
    }
    if ([string]$capability.stage -eq 'active' -and -not $capability.workset_runtime) {
        throw '活动 V0.9 必须同时启用 workset_runtime 与 adoption_transactions；关闭能力只用于隔离候选。'
    }
    $hasRuntimeSchema = $capability.PSObject.Properties.Name -contains 'runtime_schema'
    if (($capability.workset_runtime -or $hasRuntimeSchema) -and $capability.runtime_schema -ne 'xiaomo.studio-runtime/v1') {
        throw '运行时能力需要 runtime_schema=xiaomo.studio-runtime/v1。'
    }
    if ($entry -match '(?m)^(?:>\s*)?workflowResume:' -or (Test-Path -LiteralPath (Join-Path $TemplatePath '续接缓存\C_同岗续接.json'))) {
        throw 'V0.9 模板不得启用旧单岗位续接缓存。'
    }
    return $true
}

function Get-TemplateWorkflowVersion {
    param(
        [Parameter(Mandatory = $true)][string]$TemplatePath,
        [Parameter(Mandatory = $true)][bool]$IsStudio
    )

    $entry = Read-Utf8Text -Path (Join-Path $TemplatePath '开始这里.md')
    $markers = [regex]::Matches($entry, '(?im)^[ \t]*(?:>[ \t]*)?workflowRelease[ \t]*:[ \t]*(?<value>[^\r\n]*)\r?$')
    if ($markers.Count -gt 0) {
        if ($markers.Count -ne 1 -or $markers[0].Groups['value'].Value.Trim() -cne '1.0') {
            throw '不支持或不唯一的 workflowRelease 标识；仅支持 1.0。'
        }
        if (-not $IsStudio) {
            throw 'workflowRelease: 1.0 必须配套 workflowStudio: studio-v0.9。'
        }
        $version = '1.0'
    } else {
        $version = if ($IsStudio) { '0.9' } else { '0.8' }
    }
    $namedVersion = [regex]::Match((Split-Path -Leaf $TemplatePath), '^小陌AI影视项目模板_v(?<version>0\.8|0\.9|1\.0)$')
    if ($namedVersion.Success -and $namedVersion.Groups['version'].Value -ne $version) {
        throw '模板目录版本与 workflowRelease/workflowStudio 标识不匹配。'
    }
    return $version
}

function Assert-NoReparsePath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $current = Get-FullPath -Path $Path
    while (-not [string]::IsNullOrWhiteSpace($current)) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "素材布局路径不允许符号链接或其他重解析点：$current"
            }
        }
        $parent = Split-Path -Parent $current
        if ($parent -eq $current) { break }
        $current = $parent
    }
}

function Assert-NoTemplateReparsePoints {
    param([Parameter(Mandatory = $true)][string]$TemplatePath)

    Assert-NoReparsePath -Path $TemplatePath
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push($TemplatePath)
    while ($pending.Count -gt 0) {
        foreach ($item in @(Get-ChildItem -LiteralPath $pending.Pop() -Force)) {
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "布局模板不允许符号链接或其他重解析点：$($item.FullName)"
            }
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
        }
    }
}

function Get-MediaLayoutProfile {
    param(
        [Parameter(Mandatory = $true)][string]$TemplatePath,
        [Parameter(Mandatory = $true)][bool]$IsStudio
    )

    $entry = Read-Utf8Text -Path (Join-Path $TemplatePath '开始这里.md')
    $markers = [regex]::Matches($entry, '(?m)^[ \t]*(?:>[ \t]*)?workflowMediaLayout[ \t]*:[ \t]*(?<value>[^\r\n]*)\r?$')
    if ($markers.Count -eq 0) { return $null }
    if ($markers.Count -ne 1 -or @('media-layout-v1', 'media-layout-v2') -cnotcontains $markers[0].Groups['value'].Value.Trim()) {
        throw '不支持或不唯一的 workflowMediaLayout 标识。'
    }
    $marker = $markers[0].Groups['value'].Value.Trim()
    if (-not $IsStudio) {
        throw "$marker 只适用于明确启用 studio-v0.9 的模板。"
    }
    Assert-NoTemplateReparsePoints -TemplatePath $TemplatePath

    $profilePath = Join-Path (Split-Path -Parent $PSScriptRoot) "references\$marker.json"
    Assert-NoReparsePath -Path $profilePath
    if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
        throw "启动 Skill 缺少 references/$marker.json。"
    }
    $profile = (Read-Utf8Text -Path $profilePath) | ConvertFrom-Json
    $fields = @($profile.PSObject.Properties.Name | Sort-Object)
    $expectedFields = @('schema', 'fixedDirectories', 'optionalImageDirectories', 'projectFolderPattern' | Sort-Object)
    $layoutVersion = if ($marker -ceq 'media-layout-v2') { 'v2' } else { 'v1' }
    $fixedCount = if ($layoutVersion -eq 'v2') { 16 } else { 11 }
    $optionalCount = if ($layoutVersion -eq 'v2') { 0 } else { 5 }
    if ($null -eq $profile -or $profile -is [array] -or ($fields -join '|') -cne ($expectedFields -join '|') -or
        $profile.schema -isnot [string] -or $profile.schema -cne "xiaomo.media-layout/$layoutVersion" -or
        $profile.projectFolderPattern -isnot [string] -or $profile.projectFolderPattern -cne 'yyyy-MM-dd_project_vN' -or
        $profile.fixedDirectories -isnot [array] -or $profile.fixedDirectories.Count -ne $fixedCount -or
        $profile.optionalImageDirectories -isnot [array] -or $profile.optionalImageDirectories.Count -ne $optionalCount) {
        throw "$marker 配置结构不匹配。"
    }
    $allDirectories = @($profile.fixedDirectories) + @($profile.optionalImageDirectories)
    if (@($allDirectories | Sort-Object -Unique).Count -ne $allDirectories.Count) {
        throw "$marker 配置目录不能重复。"
    }
    $roots = @('01_图片', '02_视频', '03_音频', '04_导出')
    foreach ($relativePath in $allDirectories) {
        if ($relativePath -isnot [string] -or [string]::IsNullOrWhiteSpace($relativePath) -or
            [System.IO.Path]::IsPathRooted($relativePath) -or $relativePath -match '[\\:]|(^/|/$|//)') {
            throw "$marker 目录必须是安全的项目内相对路径：$relativePath"
        }
        $parts = $relativePath.Split('/')
        if ($parts.Count -gt 2 -or $roots -cnotcontains $parts[0]) {
            throw "$marker 目录层级不匹配：$relativePath"
        }
        foreach ($part in $parts) {
            if ($part -in @('.', '..') -or $part -match '[. ]$' -or
                $part.IndexOfAny([System.IO.Path]::GetInvalidFileNameChars()) -ge 0 -or
                $part -match '(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') {
                throw "$marker 目录包含不安全的路径段：$relativePath"
            }
        }
        $null = Get-RelativeItemPath -BasePath $TemplatePath -ItemPath (Join-Path $TemplatePath $relativePath)
    }
    foreach ($mediaRoot in $roots) {
        if ($profile.fixedDirectories -cnotcontains $mediaRoot) {
            throw "$marker 缺少一级目录：$mediaRoot"
        }
    }
    foreach ($relativePath in $profile.optionalImageDirectories) {
        if (-not $relativePath.StartsWith('01_图片/', [System.StringComparison]::Ordinal)) {
            throw "$marker 按需目录只能位于 01_图片 下：$relativePath"
        }
        if (Test-Path -LiteralPath (Join-Path $TemplatePath $relativePath)) {
            throw "模板不得预建按需图片目录：$relativePath"
        }
    }
    foreach ($mediaRoot in $roots) {
        $expectedChildren = switch ($mediaRoot) { '01_图片' { if ($layoutVersion -eq 'v2') { 5 } else { 0 } }; '02_视频' { 3 }; '03_音频' { 2 }; '04_导出' { 2 } }
        $actualChildren = @($profile.fixedDirectories | Where-Object { $_.StartsWith($mediaRoot + '/', [System.StringComparison]::Ordinal) }).Count
        if ($actualChildren -ne $expectedChildren) {
            throw "$marker 固定子目录数量不匹配：$mediaRoot"
        }
    }
    if ($layoutVersion -eq 'v2') {
        $actualMediaDirectories = @(Get-DirectoryManifest -Root $TemplatePath | Where-Object { $roots -ccontains $_.Split('/')[0] } | Sort-Object)
        Assert-SequenceEqual -Expected @($profile.fixedDirectories | Sort-Object) -Actual $actualMediaDirectories -Label "$marker 模板素材目录"
    }
    $profile | Add-Member -NotePropertyName marker -NotePropertyValue $marker
    return $profile
}

function Get-ProductionDocsLayoutProfile {
    param(
        [Parameter(Mandatory = $true)][string]$TemplatePath,
        [Parameter(Mandatory = $true)][bool]$IsStudio
    )

    $entry = Read-Utf8Text -Path (Join-Path $TemplatePath '开始这里.md')
    $markers = [regex]::Matches($entry, '(?m)^[ \t]*(?:>[ \t]*)?workflowDocsLayout[ \t]*:[ \t]*(?<value>[^\r\n]*)\r?$')
    if ($markers.Count -eq 0) { return $null }
    if ($markers.Count -ne 1 -or $markers[0].Groups['value'].Value.Trim() -cne 'production-docs-v1') {
        throw '不支持或不唯一的 workflowDocsLayout 标识。'
    }
    if (-not $IsStudio) {
        throw 'production-docs-v1 只适用于明确启用 studio-v0.9 的模板。'
    }
    Assert-NoTemplateReparsePoints -TemplatePath $TemplatePath
    $skillsRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $profilePath = Join-Path $skillsRoot 'orchestrate-ai-film-project\references\studio-project-layout-v1.json'
    Assert-NoReparsePath -Path $profilePath
    if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
        throw '启动 Skill 缺少共享 studio-project-layout-v1.json。'
    }
    $profile = (Read-Utf8Text -Path $profilePath) | ConvertFrom-Json
    $fields = @($profile.PSObject.Properties.Name | Sort-Object)
    $expectedFields = @('schema', 'marker', 'directory', 'documentDirectories' | Sort-Object)
    if ($null -eq $profile -or $profile -is [array] -or ($fields -join '|') -cne ($expectedFields -join '|') -or
        $profile.schema -isnot [string] -or $profile.schema -cne 'xiaomo.production-docs-layout/v1' -or
        $profile.marker -isnot [string] -or $profile.marker -cne 'production-docs-v1' -or
        $profile.directory -isnot [string] -or $profile.directory -cne '90_制作资料' -or
        $profile.documentDirectories -isnot [array] -or $profile.documentDirectories.Count -ne 13 -or
        @($profile.documentDirectories | Sort-Object -Unique).Count -ne 13) {
        throw 'production-docs-v1 配置结构不匹配。'
    }
    foreach ($directory in $profile.documentDirectories) {
        if ($directory -isnot [string] -or [string]::IsNullOrWhiteSpace($directory) -or
            $directory -in @('.', '..') -or $directory -match '[\\/:]|[. ]$' -or
            $directory.IndexOfAny([System.IO.Path]::GetInvalidFileNameChars()) -ge 0 -or
            $directory -match '(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') {
            throw "production-docs-v1 工作资料目录名不安全：$directory"
        }
        if (Test-Path -LiteralPath (Join-Path $TemplatePath $directory)) {
            throw "production-docs-v1 模板根目录仍有旧工作资料目录：$directory"
        }
    }
    $docsRoot = Join-Path $TemplatePath $profile.directory
    if (-not (Test-Path -LiteralPath $docsRoot -PathType Container)) {
        throw "模板缺少制作资料目录：$($profile.directory)"
    }
    $actualDirectories = @(Get-ChildItem -LiteralPath $docsRoot -Force -Directory | ForEach-Object { $_.Name } | Sort-Object)
    Assert-SequenceEqual -Expected @($profile.documentDirectories | Sort-Object) -Actual $actualDirectories -Label 'production-docs-v1 模板工作资料目录'
    return $profile
}

function Resolve-ProjectDocumentPath {
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [AllowNull()][psobject]$DocsLayout
    )

    if ($null -ne $DocsLayout -and $DocsLayout.documentDirectories -ccontains $RelativePath.Split('/')[0]) {
        return $DocsLayout.directory + '/' + $RelativePath
    }
    return $RelativePath
}

function New-StudioLaunchPrompt {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][psobject]$Capabilities,
        [Parameter(Mandatory = $true)][ValidateSet('0.9', '1.0')][string]$WorkflowVersion
    )

    $modeLabel = if ([string]$Capabilities.stage -eq 'active') { '活动入口' } else { '隔离候选入口' }
    $scopeNote = if ([string]$Capabilities.stage -eq 'active') {
        '只在上述新项目范围内工作；其他项目是否启用 V0.9，以各自入口和能力文件为准，不因机器安装版本自动迁移。'
    } else { '本能力文件仍标记隔离候选，只在上述明确隔离项目工作，不扩展到真实项目。' }
    $imageNote = if ($WorkflowVersion -eq '1.0') {
        '图片生成或编辑仅在本次明确授权范围内，按共同契约“V1 原生图片工具”使用当前可用 Codex 原生工具，默认选择原生；media_execution=false 不阻挡该独立调用。视频生成、上传与发布权限不扩张。'
    } else { '该分支不扩张 C 生成、上传或发布权限。' }
    return @"
小陌 AI 影视工作室｜V$WorkflowVersion $modeLabel

预期项目根目录：$ProjectRoot

确认当前项目根目录一致后，读取“开始这里.md”的共享入口、“工作室能力.json”与“A_制片统筹启动词”。当前任务从 A 制片统筹开始，按本次问题请 B、C1、C2、D、E 提供受限专业意见，C2 在资产定稿前主持整片与片段设计。
不预建六个待命任务；用户明确要求新开/续接时才创建，能力开启后用 PrepareHandoff/ActivateHandoff/CancelHandoff 办理接管。C1/C2 是专业标签，C 总文件仅作导航，不调用旧单岗缓存或租约。
先按项目能力判断动作：运行时开启则用 Inspect/SaveDraft 保存恢复完整候选，明确采用精确版本后经 Adopt 统一发布，消费采用内容先 Resolve；中断按 ResumeSync 回执区分未采用、已采用待同步和历史提交。隔离演练能力关闭时仅保留演练决定。$scopeNote 日常不读维护 HANDOFF；完整候选保存不等于采用，采用文本不授权外部动作。当前 media_execution=false 仅限制运行时内置媒体功能；D 可按共同契约，通过已验证的独立达芬奇官方 API/MCP 执行用户本次明确授权的剪辑与调色。$imageNote
"@.Trim()
}

function Get-RolePrompt {
    param(
        [Parameter(Mandatory = $true)][string]$Content,
        [Parameter(Mandatory = $true)][ValidateSet('A', 'B', 'C', 'C1', 'C2', 'D', 'E')][string]$Role,
        [switch]$Studio
    )

    $heading = if ($Studio) {
        switch ($Role) {
            'A' { 'A_制片统筹启动词' }
            'B' { 'B_编剧开发启动词' }
            'C1' { 'C1_美术与资产启动词' }
            'C2' { 'C2_导演与视听制作启动词' }
            'D' { 'D_剪辑与声音后期启动词' }
            'E' { 'E_发行与复盘启动词' }
        }
    } else { switch ($Role) {
        'A' { 'A_总控流程启动词' }
        'B' { 'B_故事剧本启动词' }
        'C' { 'C_视觉生成启动词' }
        'D' { 'D_后期剪辑启动词' }
        'E' { 'E_发行复盘启动词' }
    } }
    $pattern = '(?ms)^## ' + [System.Text.RegularExpressions.Regex]::Escape($heading) + '\r?\n\s*```text\r?\n(?<prompt>.*?)\r?\n```'
    $matches = [System.Text.RegularExpressions.Regex]::Matches($Content, $pattern)
    if ($matches.Count -ne 1) {
        throw "无法唯一提取 $Role 岗位启动词。"
    }

    return $matches[0].Groups['prompt'].Value.Trim()
}

function Get-TextSha256 {
    param([Parameter(Mandatory = $true)][string]$Text)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($Text)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '')
    } finally {
        $sha.Dispose()
    }
}

function New-StandbyPrompt {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('B', 'C', 'D', 'E')][string]$Role,
        [Parameter(Mandatory = $true)][string]$TaskTitle,
        [Parameter(Mandatory = $true)][string]$Heading
    )

    return @"
$TaskTitle 待命。本轮只回复“$TaskTitle 已待命”；不读文件、不调用 Skill、不检查 A。
用户下次请求时，读“开始这里.md”共享契约与“$Heading”，再读“A_总控.md”和本岗位主文件；A 未派 $Role 才停。
"@.Trim()
}

function New-LaunchPrompt {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$StandbyB,
        [Parameter(Mandatory = $true)][string]$StandbyC,
        [Parameter(Mandatory = $true)][string]$StandbyD,
        [Parameter(Mandatory = $true)][string]$StandbyE
    )

    return @"
小陌 AI 影视项目｜五岗位轻量启动

这是用户对在当前 Codex 项目中创建或补齐 B、C、D、E 四个本地待命任务的明确授权。当前任务就是 A｜总控流程；不要创建第二个 A。

预期项目根目录：$ProjectRoot

1. 把当前任务标题精确设为“A｜总控流程”（不含引号）；确认 cwd 等于预期根目录，且六个根 Markdown 齐全。不一致就停止。
2. 本轮只建任务拓扑：不读“开始这里.md”、A/B/C/D/E 主文件或任何 Skill。
3. 前置查询：由绝对路径取得唯一目标 projectId，再查最近任务（limit≤50）。B/C/D/E 只统计 `projectId==目标` 且 `cwd==预期根目录` 的任务；其他项目同名任务忽略。当前 A 仅允许以当前 threadId＋精确 cwd 作为例外。目标项目内第二个 A、同岗位重复或无法确认时停止。
4. B/C/D/E 已有单一标准标题就复用，缺失才用本地独立任务创建能力和下方短待命消息补齐；可同批并行，否则 B→C→D→E。禁用子代理、fork、handoff、工作树、项目外任务及模型/推理覆盖。
5. 创建后再查一次目标项目任务，并结合创建回执核对 title/projectId/cwd。失败或结果歧义时保留现状、不删除、不重试，只报告已存在/已创建/缺失岗位和缺失岗位短消息。
6. 四岗位拓扑核验后立即结束本轮，留在 A，不打开、等待、汇总或操控子任务。用户下一条消息时再读取“开始这里.md”的共享契约与 A 启动词和“A_总控.md”，开始首个完整 B 故事阶段任务。

<<<ROLE_STANDBY_B_START>>>
$StandbyB
<<<ROLE_STANDBY_B_END>>>

<<<ROLE_STANDBY_C_START>>>
$StandbyC
<<<ROLE_STANDBY_C_END>>>

<<<ROLE_STANDBY_D_START>>>
$StandbyD
<<<ROLE_STANDBY_D_END>>>

<<<ROLE_STANDBY_E_START>>>
$StandbyE
<<<ROLE_STANDBY_E_END>>>
"@.Trim()
}

try {
    if ([string]::IsNullOrWhiteSpace($ProjectName)) {
        throw '项目名不能为空。请提供一个基础项目名。'
    }

    if ($ProjectName.IndexOfAny([System.IO.Path]::GetInvalidFileNameChars()) -ge 0) {
        throw '项目名包含 Windows 文件名非法字符。请重新命名。'
    }
    if ($ProjectName -match '(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') {
        throw '项目名是 Windows 保留名称。请重新命名。'
    }
    if ($ProjectName -match '[\. ]$') {
        throw '项目名不能以点或空格结尾。请重新命名。'
    }
    if ($ProjectName -match '(?i)_v\d+$') {
        throw '项目名不要自带 _vN；版本号由启动工具自动分配。'
    }

    $context = Resolve-WorkflowContext -RequestedWorkflowRoot $WorkflowRoot -RequestedProjectsRoot $ProjectsRoot -RequestedTemplateRoot $TemplateRoot
    $script:ConfigPath = $context.configPath
    $templateFull = Get-FullPath -Path $context.templateRoot
    if (-not (Test-Path -LiteralPath $templateFull -PathType Container)) {
        throw "模板目录不存在：$templateFull"
    }

    $isStudio = Get-StudioTemplateMode -TemplatePath $templateFull
    $templateVersion = Get-TemplateWorkflowVersion -TemplatePath $templateFull -IsStudio $isStudio
    $mediaLayout = Get-MediaLayoutProfile -TemplatePath $templateFull -IsStudio $isStudio
    $docsLayout = Get-ProductionDocsLayoutProfile -TemplatePath $templateFull -IsStudio $isStudio
    if ($null -ne $docsLayout -and (Test-Path -LiteralPath (Join-Path $templateFull (Resolve-ProjectDocumentPath -RelativePath '续接缓存/C_同岗续接.json' -DocsLayout $docsLayout)))) {
        throw 'V0.9 模板不得启用旧单岗位续接缓存。'
    }
    $creationDate = if ($null -ne $mediaLayout) { (Get-Date).ToString('yyyy-MM-dd', [System.Globalization.CultureInfo]::InvariantCulture) } else { $null }
    if ($context.expectedWorkflowVersion -and $context.expectedWorkflowVersion -ne $templateVersion) {
        throw "模板实际版本 v$templateVersion 与 manifest/配置版本 v$($context.expectedWorkflowVersion) 不一致；请重新安装或明确指定 -TemplateRoot。"
    }

    $projectsRootFull = Get-FullPath -Path $context.projectsRoot
    $script:ProjectsRootFull = $projectsRootFull
    if ($null -ne $mediaLayout -or $null -ne $docsLayout) { Assert-NoReparsePath -Path $projectsRootFull }

    $requiredFiles = @(
        '开始这里.md',
        'A_总控.md',
        'B_故事剧本.md',
        'C_视觉生成.md',
        'D_后期剪辑.md',
        'E_发行复盘.md'
    )
    $requiredDirectories = @(
        'assets',
        'assets/场景',
        'assets/声音',
        'assets/封面',
        'assets/视频',
        'assets/视频/剪辑成片',
        'assets/视频/生成素材',
        'assets/角色',
        'assets/道具',
        '资产设计',
        '色卡',
        '提示词卡',
        '生成记录',
        '表演设计',
        '场景制作',
        '镜头规划',
        '后期方案',
        '发布包',
        '剧本',
        '剧本/工作稿',
        '剧本/采用稿',
        '剧本/生产稿',
        '剧本/历史',
        '剧本/阶段完成包',
        '流程改进日志',
        '流程改进日志/待处理',
        '流程改进日志/已处理'
    )

    if ($null -ne $mediaLayout) {
        $requiredDirectories = @($requiredDirectories | Where-Object { $_ -ne 'assets' -and -not $_.StartsWith('assets/') }) + @($mediaLayout.fixedDirectories)
    }

    if ($isStudio) {
        $requiredFiles += @('工作室能力.json', '工作稿/C1/开始这里.md', '工作稿/C2/开始这里.md')
        $requiredDirectories += @('工作稿', '工作稿/C1', '工作稿/C2')
    }

    if ($null -ne $docsLayout) {
        $requiredFiles = @($requiredFiles | ForEach-Object { Resolve-ProjectDocumentPath -RelativePath $_ -DocsLayout $docsLayout })
        $requiredDirectories = @($requiredDirectories + $docsLayout.documentDirectories | ForEach-Object { Resolve-ProjectDocumentPath -RelativePath $_ -DocsLayout $docsLayout } | Select-Object -Unique)
        $requiredDirectories += $docsLayout.directory
    }

    foreach ($relativePath in $requiredFiles) {
        $candidate = Join-Path $templateFull $relativePath
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "模板缺少必需文件：$relativePath"
        }
    }
    foreach ($relativePath in $requiredDirectories) {
        $candidate = Join-Path $templateFull ($relativePath.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $candidate -PathType Container)) {
            throw "模板缺少必需目录：$relativePath"
        }
    }

    $templateFiles = @(Get-FileManifest -Root $templateFull)
    $templateDirectories = @(Get-DirectoryManifest -Root $templateFull)
    $templateFileLines = @(Convert-FileManifestToLines -Manifest $templateFiles)

    if (-not (Test-Path -LiteralPath $projectsRootFull)) {
        New-Item -ItemType Directory -Path $projectsRootFull | Out-Null
    }
    if (-not (Test-Path -LiteralPath $projectsRootFull -PathType Container)) {
        throw "项目父路径不是目录：$projectsRootFull"
    }

    $escapedName = [System.Text.RegularExpressions.Regex]::Escape($ProjectName)
    $versionPattern = if ($null -ne $mediaLayout) {
        '^(?:\d{4}-\d{2}-\d{2}_)?' + $escapedName + '_v(?<version>\d+)$'
    } else { '^' + $escapedName + '_v(?<version>\d+)$' }
    $maxVersion = 0
    foreach ($directory in @(Get-ChildItem -LiteralPath $projectsRootFull -Force -Directory)) {
        $match = [System.Text.RegularExpressions.Regex]::Match($directory.Name, $versionPattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($match.Success) {
            $candidateVersion = [int]$match.Groups['version'].Value
            if ($candidateVersion -gt $maxVersion) {
                $maxVersion = $candidateVersion
            }
        }
    }

    $versionNumber = $maxVersion + 1
    $version = "v$versionNumber"
    $folderName = if ($null -ne $mediaLayout) { "${creationDate}_${ProjectName}_$version" } else { "${ProjectName}_$version" }
    $targetPath = Get-FullPath -Path (Join-Path $projectsRootFull $folderName)
    if (Test-Path -LiteralPath $targetPath) {
        throw "目标项目目录已经存在，拒绝覆盖：$targetPath"
    }

    $stagingLeaf = ".$folderName.creating-$([guid]::NewGuid().ToString('N'))"
    $stagingPath = Get-FullPath -Path (Join-Path $projectsRootFull $stagingLeaf)
    Assert-SafeStagingPath -Path $stagingPath -Root $projectsRootFull -FolderName $folderName
    $script:StagingPath = $stagingPath

    New-Item -ItemType Directory -Path $stagingPath | Out-Null
    foreach ($item in @(Get-ChildItem -LiteralPath $templateFull -Force)) {
        Copy-Item -LiteralPath $item.FullName -Destination $stagingPath -Recurse -Force
    }

    $copiedFiles = @(Get-FileManifest -Root $stagingPath)
    $copiedDirectories = @(Get-DirectoryManifest -Root $stagingPath)
    Assert-SequenceEqual -Expected $templateFileLines -Actual @(Convert-FileManifestToLines -Manifest $copiedFiles) -Label '模板文件哈希'
    Assert-SequenceEqual -Expected $templateDirectories -Actual $copiedDirectories -Label '模板目录树'

    $startPath = Join-Path $stagingPath '开始这里.md'
    $aControlPath = Join-Path $stagingPath 'A_总控.md'
    $startContent = Read-Utf8Text -Path $startPath
    $aControlContent = Read-Utf8Text -Path $aControlPath

    $startHeadingPattern = '(?m)^# 开始这里\r?$'
    $headingMatches = [System.Text.RegularExpressions.Regex]::Matches($startContent, $startHeadingPattern)
    if ($headingMatches.Count -ne 1) {
        throw '模板“开始这里.md”缺少唯一的一级标题。'
    }

    $templateLeaf = Split-Path -Leaf $templateFull
    $templateLabel = $templateLeaf.Replace('_v', ' v')
    $identityBlock = @"

## 项目身份

项目名：$ProjectName

项目版本：$version

项目文件夹：$folderName

项目根目录：以本文件“开始这里.md”所在文件夹为准

来源模板：$templateLabel
"@
    $headingRegex = New-Object System.Text.RegularExpressions.Regex($startHeadingPattern)
    $headingEvaluator = [System.Text.RegularExpressions.MatchEvaluator]{
        param($match)
        return $match.Value.TrimEnd("`r") + $identityBlock
    }
    $startContent = $headingRegex.Replace($startContent, $headingEvaluator)

    $placeholderMatches = [System.Text.RegularExpressions.Regex]::Matches($startContent, '【项目名】')
    $expectedRoleCount = if ($isStudio) { 6 } else { 5 }
    if ($placeholderMatches.Count -ne $expectedRoleCount) {
        throw "模板中的项目名占位符数量应为 $expectedRoleCount，实际为 $($placeholderMatches.Count)。"
    }
    $startContent = $startContent.Replace('【项目名】', $folderName)

    $roleDefinitions = @()
    if (-not $isStudio) {
    $roleDefinitions = @(
        [pscustomobject]@{ templateTitle = "A_总控流程｜$folderName"; taskTitle = 'A｜总控流程' },
        [pscustomobject]@{ templateTitle = "B_故事剧本｜$folderName"; taskTitle = 'B｜故事剧本' },
        [pscustomobject]@{ templateTitle = "C_视觉生成｜$folderName"; taskTitle = 'C｜视觉生成' },
        [pscustomobject]@{ templateTitle = "D_后期剪辑｜$folderName"; taskTitle = 'D｜后期剪辑' },
        [pscustomobject]@{ templateTitle = "E_发行复盘｜$folderName"; taskTitle = 'E｜发行复盘' }
    )
    foreach ($roleDefinition in $roleDefinitions) {
        $templateTitle = [string]$roleDefinition.templateTitle
        $taskTitle = [string]$roleDefinition.taskTitle
        $titlePattern = '(?m)^' + [System.Text.RegularExpressions.Regex]::Escape($templateTitle) + '\r?$'
        $titleMatches = [System.Text.RegularExpressions.Regex]::Matches($startContent, $titlePattern)
        if ($titleMatches.Count -ne 1) {
            throw "无法在启动词中唯一定位岗位标题：$templateTitle"
        }
        $titleRegex = New-Object System.Text.RegularExpressions.Regex($titlePattern)
        $titleEvaluator = [System.Text.RegularExpressions.MatchEvaluator]{
            param($match)
            return $taskTitle + "`r`n`r`n先核对当前任务标题：只有标题不等于「$taskTitle」时，才调用 Codex 的任务重命名能力将其精确设置为该标题；已经一致时不要调用重命名工具。不要缩写、改写或另拟标题。`r`n`r`n请把当前在 Codex 中选择的项目文件夹作为本聊天唯一的项目根目录。`r`n该目录应直接包含开始这里.md和 A/B/C/D/E 五个岗位主文件。"
        }
        $startContent = $titleRegex.Replace($startContent, $titleEvaluator)
    }

    }

    $projectNamePattern = '(?m)^项目名：\s*$'
    $projectNameMatches = [System.Text.RegularExpressions.Regex]::Matches($aControlContent, $projectNamePattern)
    if ($projectNameMatches.Count -ne 1) {
        throw '模板“A_总控.md”缺少唯一的空白项目名字段。'
    }
    $projectNameRegex = New-Object System.Text.RegularExpressions.Regex($projectNamePattern)
    $projectNameEvaluator = [System.Text.RegularExpressions.MatchEvaluator]{
        param($match)
        return "项目名：$ProjectName"
    }
    $aControlContent = $projectNameRegex.Replace($aControlContent, $projectNameEvaluator)

    Write-Utf8BomText -Path $startPath -Content $startContent
    Write-Utf8BomText -Path $aControlPath -Content $aControlContent

    $finalFiles = @(Get-FileManifest -Root $stagingPath)
    $finalDirectories = @(Get-DirectoryManifest -Root $stagingPath)
    $finalPaths = @($finalFiles | ForEach-Object { $_.path })
    $templatePaths = @($templateFiles | ForEach-Object { $_.path })
    Assert-SequenceEqual -Expected $templatePaths -Actual $finalPaths -Label '定制后文件集合'
    Assert-SequenceEqual -Expected $templateDirectories -Actual $finalDirectories -Label '定制后目录树'

    foreach ($templateFile in $templateFiles) {
        if ($templateFile.path -notin @('开始这里.md', 'A_总控.md')) {
            $finalFile = @($finalFiles | Where-Object { $_.path -eq $templateFile.path })[0]
            if ($null -eq $finalFile -or $finalFile.sha256 -ne $templateFile.sha256) {
                throw "非定制文件被意外修改：$($templateFile.path)"
            }
        }
    }

    $verifiedStartContent = Read-Utf8Text -Path $startPath
    $verifiedAContent = Read-Utf8Text -Path $aControlPath
    if ($verifiedStartContent.Contains('【项目名】')) {
        throw '定制后的“开始这里.md”仍含项目名占位符。'
    }
    foreach ($requiredLine in @(
        "项目名：$ProjectName",
        "项目版本：$version",
        "项目文件夹：$folderName",
        '项目根目录：以本文件“开始这里.md”所在文件夹为准',
        "来源模板：$templateLabel"
    )) {
        if (-not $verifiedStartContent.Contains($requiredLine)) {
            throw "定制后的开始这里.md缺少项目身份：$requiredLine"
        }
    }
    if (-not $isStudio) {
    if ([System.Text.RegularExpressions.Regex]::Matches($verifiedStartContent, [System.Text.RegularExpressions.Regex]::Escape('请把当前在 Codex 中选择的项目文件夹作为本聊天唯一的项目根目录。')).Count -ne 5) {
        throw '五段启动词中的便携项目根目录说明数量不正确。'
    }
    if ([System.Text.RegularExpressions.Regex]::Matches($verifiedStartContent, [System.Text.RegularExpressions.Regex]::Escape('已经一致时不要调用重命名工具')).Count -ne 5) {
        throw '五段启动词中的条件式任务重命名指令数量不正确。'
    }
    foreach ($roleDefinition in $roleDefinitions) {
        $taskTitlePattern = '(?m)^' + [System.Text.RegularExpressions.Regex]::Escape([string]$roleDefinition.taskTitle) + '\r?$'
        if ([System.Text.RegularExpressions.Regex]::Matches($verifiedStartContent, $taskTitlePattern).Count -ne 1) {
            throw "标准任务标题没有在启动词首行唯一出现：$($roleDefinition.taskTitle)"
        }
    }
    }
    if ($verifiedStartContent.Contains($targetPath)) {
        throw '定制后的“开始这里.md”不应持久化当前电脑的绝对项目路径。'
    }
    if ([System.Text.RegularExpressions.Regex]::Matches($verifiedAContent, '(?m)^项目名：' + [System.Text.RegularExpressions.Regex]::Escape($ProjectName) + '\r?$').Count -ne 1) {
        throw '“A_总控.md”的项目名预填校验失败。'
    }

    if ($isStudio) {
        $departmentPrompts = [ordered]@{}
        $departmentHashes = [ordered]@{}
        $fullRolePromptChars = 0
        foreach ($department in @('A', 'B', 'C1', 'C2', 'D', 'E')) {
            $prompt = Get-RolePrompt -Content $verifiedStartContent -Role $department -Studio
            $departmentPrompts[$department] = $prompt
            $departmentHashes[$department] = Get-TextSha256 -Text $prompt
            $fullRolePromptChars += $prompt.Length
        }
        if ($templateVersion -eq '1.0') {
            . (Join-Path $PSScriptRoot 'get_role_launch.ps1')
            $roleLaunchPackage = Get-V1RoleLaunchPackage -ProjectRoot $targetPath -ReadRoot $stagingPath
            $launchPrompt = $roleLaunchPackage.launchPrompt
        } else {
            $launchPrompt = New-StudioLaunchPrompt -ProjectRoot $targetPath -Capabilities ((Read-Utf8Text -Path (Join-Path $stagingPath '工作室能力.json')) | ConvertFrom-Json) -WorkflowVersion $templateVersion
        }
    } else {
    $promptA = Get-RolePrompt -Content $verifiedStartContent -Role 'A'
    $promptB = Get-RolePrompt -Content $verifiedStartContent -Role 'B'
    $promptC = Get-RolePrompt -Content $verifiedStartContent -Role 'C'
    $promptD = Get-RolePrompt -Content $verifiedStartContent -Role 'D'
    $promptE = Get-RolePrompt -Content $verifiedStartContent -Role 'E'
    $standbyB = New-StandbyPrompt -Role 'B' -TaskTitle 'B｜故事剧本' -Heading 'B_故事剧本启动词'
    $standbyC = New-StandbyPrompt -Role 'C' -TaskTitle 'C｜视觉生成' -Heading 'C_视觉生成启动词'
    $standbyD = New-StandbyPrompt -Role 'D' -TaskTitle 'D｜后期剪辑' -Heading 'D_后期剪辑启动词'
    $standbyE = New-StandbyPrompt -Role 'E' -TaskTitle 'E｜发行复盘' -Heading 'E_发行复盘启动词'
    $launchPrompt = New-LaunchPrompt -ProjectRoot $targetPath -StandbyB $standbyB -StandbyC $standbyC -StandbyD $standbyD -StandbyE $standbyE
    $fullRolePromptChars = $promptA.Length + $promptB.Length + $promptC.Length + $promptD.Length + $promptE.Length

    }

    $templateAfterFiles = @(Get-FileManifest -Root $templateFull)
    Assert-SequenceEqual -Expected $templateFileLines -Actual @(Convert-FileManifestToLines -Manifest $templateAfterFiles) -Label '模板源文件未修改'

    if (Test-Path -LiteralPath $targetPath) {
        throw "正式落盘前发现目标目录已存在，拒绝覆盖：$targetPath"
    }
    Move-Item -LiteralPath $stagingPath -Destination $targetPath
    $script:StagingPath = $null

    $opened = $false
    $openError = $null
    if (-not $NoOpen) {
        try {
            Start-Process -FilePath 'explorer.exe' -ArgumentList @("`"$targetPath`"") | Out-Null
            $opened = $true
        } catch {
            $openError = $_.Exception.Message
        }
    }

    $result = [ordered]@{
        success      = $true
        projectName  = $ProjectName
        version      = $version
        folderName   = $folderName
        projectRoot  = $targetPath
        startFile    = Join-Path $targetPath '开始这里.md'
        templateRoot = $templateFull
        templateSource = $context.templateSource
        workflowVersion = $templateVersion
        workflowRoot = $context.workflowRoot
        configPath   = $script:ConfigPath
        opened       = $opened
        openError    = $openError
        launchPrompt = $launchPrompt
        launchPromptVersion = 2
        launchPromptChars = $launchPrompt.Length
        fullRolePromptChars = $fullRolePromptChars
        rolePromptSha256 = if ($isStudio) { $departmentHashes } else { [ordered]@{
            A = Get-TextSha256 -Text $promptA
            B = Get-TextSha256 -Text $promptB
            C = Get-TextSha256 -Text $promptC
            D = Get-TextSha256 -Text $promptD
            E = Get-TextSha256 -Text $promptE
        }
        }
        standbyPrompts = if ($isStudio) { $null } else { [ordered]@{
            B = $standbyB
            C = $standbyC
            D = $standbyD
            E = $standbyE
        } }
    }

    if ($null -ne $mediaLayout) {
        $result['mediaLayout'] = $mediaLayout.marker
        $result['creationDate'] = $creationDate
    }

    if ($null -ne $docsLayout) {
        $result['docsLayout'] = $docsLayout.marker
        $result['productionDocsDirectory'] = $docsLayout.directory
    }

    if ($isStudio) {
        $result.launchPromptVersion = 3
        $result.rolePromptSha256 = $departmentHashes
        $result.Remove('standbyPrompts')
        $result['departmentPrompts'] = $departmentPrompts
        $result['workflowStudio'] = 'studio-v0.9'
        $result['capabilities'] = (Read-Utf8Text -Path (Join-Path $targetPath '工作室能力.json')) | ConvertFrom-Json
        if ($templateVersion -eq '1.0') {
            $result.launchPromptVersion = $roleLaunchPackage.launchPromptVersion
            $result['standbyPrompts'] = $roleLaunchPackage.standbyPrompts
            $result['roleTitles'] = $roleLaunchPackage.roleTitles
            $result['requiredFiles'] = $roleLaunchPackage.requiredFiles
            $result['tasksCreated'] = $false
        }
    }

    $result | ConvertTo-Json -Depth 6
    exit 0
} catch {
    $failureMessage = $_.Exception.Message

    if ($script:StagingPath -and (Test-Path -LiteralPath $script:StagingPath)) {
        try {
            Assert-SafeStagingPath -Path $script:StagingPath -Root $script:ProjectsRootFull -FolderName $folderName
            Remove-Item -LiteralPath $script:StagingPath -Recurse -Force
        } catch {
            $failureMessage = "$failureMessage 临时目录清理失败：$($_.Exception.Message)"
        }
    }

    [Console]::Error.WriteLine("ERROR: $failureMessage")
    exit 1
}
