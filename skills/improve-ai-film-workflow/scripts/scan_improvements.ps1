[CmdletBinding()]
param(
    [string]$ProjectsRoot,
    [string]$ProjectName
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\..\orchestrate-ai-film-project\scripts\project_document_paths.ps1')

function Get-FullPath([string]$Path) { return [IO.Path]::GetFullPath($Path) }

function Resolve-ProjectsRoot([string]$RequestedRoot) {
    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) {
        $resolved = Get-FullPath $RequestedRoot
        if (-not (Test-Path -LiteralPath $resolved -PathType Container)) { throw "项目父目录不存在：$resolved" }
        return $resolved
    }
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) { $candidates += (Join-Path $env:CODEX_HOME 'xiaomo-ai-film-workflow.json') }
    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) { $candidates += (Join-Path (Join-Path $env:USERPROFILE '.agents') 'xiaomo-ai-film-workflow.json') }
    foreach ($candidate in @($candidates | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $config = [IO.File]::ReadAllText($candidate, [Text.Encoding]::UTF8) | ConvertFrom-Json
            if (-not [string]::IsNullOrWhiteSpace([string]$config.projectsRoot)) {
                $resolved = Get-FullPath ([string]$config.projectsRoot)
                if (Test-Path -LiteralPath $resolved -PathType Container) { return $resolved }
            }
        }
    }
    throw '无法定位 projectsRoot；请重新安装工作流或显式传入 -ProjectsRoot。'
}

function Read-Field([string]$Text, [string]$Label) {
    $match = [regex]::Match($Text, "(?m)^$([regex]::Escape($Label))：(.*)$")
    if ($match.Success) { return $match.Groups[1].Value.Trim() }
    return ''
}

$root = Resolve-ProjectsRoot $ProjectsRoot
$projects = if ([string]::IsNullOrWhiteSpace($ProjectName)) {
    @(Get-ChildItem -LiteralPath $root -Directory)
} else {
    $target = Join-Path $root $ProjectName
    if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw "项目不存在：$ProjectName" }
    @((Get-Item -LiteralPath $target))
}

$items = New-Object System.Collections.Generic.List[object]
foreach ($project in $projects) {
    $logRelative=Get-StudioDocumentRelativePath -ProjectRoot $project.FullName -RelativePath '流程改进日志'
    $pending = Join-Path (Join-Path $project.FullName $logRelative) '待处理'
    if (-not (Test-Path -LiteralPath $pending -PathType Container)) { continue }
    foreach ($file in Get-ChildItem -LiteralPath $pending -Filter 'WFI-*.md' -File | Sort-Object Name) {
        $text = [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8)
        $title = ([regex]::Match($text, '(?m)^#\s+[^｜]+｜(.*)$')).Groups[1].Value.Trim()
        $items.Add([ordered]@{
            id = [IO.Path]::GetFileNameWithoutExtension($file.Name)
            project = $project.Name
            role = Read-Field $text '岗位'
            category = Read-Field $text '问题类别'
            status = Read-Field $text '状态'
            taskPhase = Read-Field $text '任务阶段'
            patternKey = Read-Field $text 'Pattern-Key'
            suggestedTarget = Read-Field $text '建议修改位置'
            summary = $title
            path = $file.FullName
            sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        })
    }
}

[ordered]@{ success = $true; projectsRoot = $root; count = $items.Count; items = @($items.ToArray()) } | ConvertTo-Json -Depth 5
