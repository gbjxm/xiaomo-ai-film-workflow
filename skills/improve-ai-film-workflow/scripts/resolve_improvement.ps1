[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EntryPath,
    [Parameter(Mandatory = $true)]
    [ValidateSet('采用', '停用')]
    [string]$Status,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Fa-f]{64}$')]
    [string]$ExpectedSha256,
    [Parameter(Mandatory = $true)]
    [string]$Resolution,
    [string]$Verification = '未提供'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\..\orchestrate-ai-film-project\scripts\project_document_paths.ps1')
$full = [IO.Path]::GetFullPath($EntryPath)
if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw "记录不存在：$full" }
$parent = Split-Path -Parent $full
if ([IO.Path]::GetFileName($parent) -ne '待处理') { throw '只能处理“流程改进日志/待处理”中的记录。' }
$logRoot = Split-Path -Parent $parent
if ([IO.Path]::GetFileName($logRoot) -ne '流程改进日志') { throw '记录不位于有效的流程改进日志目录。' }
$projectRoot = Split-Path -Parent $logRoot
if([IO.Path]::GetFileName($projectRoot) -eq '90_制作资料'){$projectRoot=Split-Path -Parent $projectRoot}
$expectedLog=[IO.Path]::GetFullPath((Join-Path $projectRoot (Get-StudioDocumentRelativePath -ProjectRoot $projectRoot -RelativePath '流程改进日志')))
if(-not $expectedLog.Equals([IO.Path]::GetFullPath($logRoot),[StringComparison]::OrdinalIgnoreCase)){throw 'Log location does not match the project layout'}
$actualSha256 = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash
if ($actualSha256 -ne $ExpectedSha256.ToUpperInvariant()) { throw '记录在扫描后已发生变化，停止处理。' }
$processed = Join-Path $logRoot '已处理'
$destination = Join-Path $processed ([IO.Path]::GetFileName($full))
if (Test-Path -LiteralPath $destination) { throw "已处理目录存在同名记录：$destination" }

$text = [IO.File]::ReadAllText($full, [Text.Encoding]::UTF8)
if ($text -notmatch '(?m)^状态：待定$') { throw '记录状态不是待定，停止处理。' }
$updated = [regex]::Replace($text, '(?m)^状态：待定$', "状态：$Status", 1)
$updated = $updated.TrimEnd() + "`n`n## 处理结果`n`n处理时间：$((Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz'))`n处理状态：$Status`n处理说明：$($Resolution.Trim())`n验证：$($Verification.Trim())`n"

New-Item -ItemType Directory -Path $processed -Force | Out-Null
$temp = Join-Path $parent ('.resolving-' + [guid]::NewGuid().ToString('N') + '.tmp')
[IO.File]::WriteAllText($temp, $updated, (New-Object Text.UTF8Encoding($false)))
[IO.File]::Move($temp, $destination)
Remove-Item -LiteralPath $full

[ordered]@{ success = $true; status = $Status; path = $destination; sha256 = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash } | ConvertTo-Json -Compress
