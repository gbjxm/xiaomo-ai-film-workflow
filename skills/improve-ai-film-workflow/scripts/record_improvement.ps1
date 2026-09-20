[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [Parameter(Mandatory = $true)]
    [ValidateSet('A', 'B', 'C', 'D', 'E')]
    [string]$Role,
    [Parameter(Mandatory = $true)]
    [ValidateSet('重复返工', '遗漏', '交接失败', '职责冲突', '重复手工步骤', '工具失败', '证据缺口', '契约不清')]
    [string]$Category,
    [Parameter(Mandatory = $true)]
    [string]$Summary,
    [Parameter(Mandatory = $true)]
    [string]$Observation,
    [Parameter(Mandatory = $true)]
    [string]$Impact,
    [string]$TaskPhase = '未提供',
    [string[]]$EvidencePath = @(),
    [string]$Workaround = '无',
    [string]$SuggestedTarget = '待判断',
    [string]$PatternKey,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\..\orchestrate-ai-film-project\scripts\project_document_paths.ps1')

function Get-FullPath([string]$Path) {
    return [IO.Path]::GetFullPath($Path)
}

function Find-ProjectRoot([string]$RequestedRoot) {
    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) {
        return Get-FullPath $RequestedRoot
    }
    $current = Get-FullPath (Get-Location).Path
    while ($true) {
        $required = @('开始这里.md', 'A_总控.md', 'B_故事剧本.md', 'C_视觉生成.md')
        $valid = $true
        foreach ($name in $required) {
            if (-not (Test-Path -LiteralPath (Join-Path $current $name) -PathType Leaf)) {
                $valid = $false
                break
            }
        }
        $hasD = (Test-Path -LiteralPath (Join-Path $current 'D_后期剪辑.md') -PathType Leaf) -or
            (Test-Path -LiteralPath (Join-Path $current 'D_后期发行复盘.md') -PathType Leaf)
        if ($valid -and $hasD) { return $current }
        $parent = Split-Path -Parent $current
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current) { break }
        $current = $parent
    }
    throw '无法定位影视项目根目录；请在项目目录中运行或显式传入 -ProjectRoot。'
}

function Normalize-OneLine([string]$Value, [string]$Name) {
    $result = ($Value -replace '[\r\n]+', ' ').Trim()
    if ([string]::IsNullOrWhiteSpace($result)) { throw "$Name 不能为空。" }
    return $result
}

function Get-PatternKey([string]$Value) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Value.ToLowerInvariant())
        $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant()
        return "film-workflow.$($hash.Substring(0, 16))"
    } finally {
        $sha.Dispose()
    }
}

$root = Find-ProjectRoot $ProjectRoot
$requiredFiles = @('开始这里.md', 'A_总控.md', 'B_故事剧本.md', 'C_视觉生成.md')
foreach ($name in $requiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $name) -PathType Leaf)) {
        throw "项目结构不完整：缺少 $name"
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $root 'D_后期剪辑.md') -PathType Leaf) -and
    -not (Test-Path -LiteralPath (Join-Path $root 'D_后期发行复盘.md') -PathType Leaf)) {
    throw '项目结构不完整：缺少 D_后期剪辑.md（未迁移项目可使用 D_后期发行复盘.md）'
}
if ($Role -eq 'E' -and -not (Test-Path -LiteralPath (Join-Path $root 'E_发行复盘.md') -PathType Leaf)) {
    throw '当前项目没有 E_发行复盘.md，不能以 E 岗位记录流程问题；不得自动迁移旧项目。'
}

$summaryOneLine = Normalize-OneLine $Summary 'Summary'
$phaseOneLine = Normalize-OneLine $TaskPhase 'TaskPhase'
$targetOneLine = Normalize-OneLine $SuggestedTarget 'SuggestedTarget'
if ([string]::IsNullOrWhiteSpace($PatternKey)) {
    $PatternKey = Get-PatternKey "$Category|$summaryOneLine|$targetOneLine"
} else {
    $PatternKey = Normalize-OneLine $PatternKey 'PatternKey'
}

$evidenceRelative = New-Object System.Collections.Generic.List[string]
$rootPrefix = $root.TrimEnd('\') + '\'
foreach ($item in @($EvidencePath)) {
    if ([string]::IsNullOrWhiteSpace($item)) { continue }
    if ([IO.Path]::IsPathRooted($item)) { throw "证据路径必须相对于项目根目录：$item" }
    $full = Get-FullPath (Join-Path $root $item)
    if (-not $full.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "证据路径越出项目根目录：$item"
    }
    if (-not (Test-Path -LiteralPath $full)) { throw "证据路径不存在：$item" }
    $evidenceRelative.Add($full.Substring($rootPrefix.Length).Replace('\', '/'))
}

$logRelative=Get-StudioDocumentRelativePath -ProjectRoot $root -RelativePath '流程改进日志'
$pending = Join-Path (Join-Path $root $logRelative) '待处理'
$processed = Join-Path (Join-Path $root $logRelative) '已处理'
if (Test-Path -LiteralPath $pending -PathType Container) {
    foreach ($existing in Get-ChildItem -LiteralPath $pending -Filter 'WFI-*.md' -File) {
        $text = [IO.File]::ReadAllText($existing.FullName, [Text.Encoding]::UTF8)
        if ($text -match "(?m)^岗位：$([regex]::Escape($Role))$" -and
            $text -match "(?m)^任务阶段：$([regex]::Escape($phaseOneLine))$" -and
            $text -match "(?m)^Pattern-Key：$([regex]::Escape($PatternKey))$") {
            $existingId = [IO.Path]::GetFileNameWithoutExtension($existing.Name)
            [ordered]@{ success = $true; created = $false; deduplicated = $true; id = $existingId; path = $existing.FullName } | ConvertTo-Json -Compress
            exit 0
        }
    }
}

$now = Get-Date
$id = 'WFI-{0}-{1}-{2}' -f $now.ToString('yyyyMMdd-HHmmss'), $Role, ([guid]::NewGuid().ToString('N').Substring(0, 6).ToUpperInvariant())
$path = Join-Path $pending "$id.md"
$evidenceLines = if ($evidenceRelative.Count -gt 0) { @($evidenceRelative | ForEach-Object { '- `{0}`' -f $_ }) } else { @('- 无项目内证据文件；事实来自本轮已观察的流程行为。') }
$content = @(
    "# $id｜$summaryOneLine",
    '',
    '状态：待定',
    "记录时间：$($now.ToString('yyyy-MM-ddTHH:mm:sszzz'))",
    "来源项目：$([IO.Path]::GetFileName($root))",
    "岗位：$Role",
    "任务阶段：$phaseOneLine",
    "问题类别：$Category",
    "Pattern-Key：$PatternKey",
    "建议修改位置：$targetOneLine",
    '',
    '## 已核验事实',
    '',
    $Observation.Trim(),
    '',
    '## 影响',
    '',
    $Impact.Trim(),
    '',
    '## 证据',
    '',
    ($evidenceLines -join "`n"),
    '',
    '## 当前临时处理',
    '',
    $Workaround.Trim(),
    '',
    '## 建议方向',
    '',
    $targetOneLine,
    ''
) -join "`n"

if ($DryRun) {
    [ordered]@{ success = $true; dryRun = $true; id = $id; path = $path; patternKey = $PatternKey } | ConvertTo-Json -Compress
    exit 0
}

New-Item -ItemType Directory -Path $pending -Force | Out-Null
New-Item -ItemType Directory -Path $processed -Force | Out-Null
$utf8 = New-Object Text.UTF8Encoding($false)
$stream = [IO.FileStream]::new($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
try {
    $writer = New-Object IO.StreamWriter($stream, $utf8)
    try { $writer.Write($content) } finally { $writer.Dispose() }
} finally {
    if ($null -ne $stream) { $stream.Dispose() }
}

[ordered]@{
    success = $true
    created = $true
    deduplicated = $false
    id = $id
    path = $path
    sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    patternKey = $PatternKey
} | ConvertTo-Json -Compress
