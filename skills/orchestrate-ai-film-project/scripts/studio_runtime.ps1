[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)]
    [ValidateSet('Create','List','Inspect','Claim','SaveDraft','PrepareHandoff','ActivateHandoff','CancelHandoff','Adopt','ResumeSync','CancelAdoption','Resolve')]
    [string]$Action,
    [string]$RequestFile
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$runtimeScript = Join-Path $PSScriptRoot 'studio_runtime.py'
$runtimePython = (Get-Command python -ErrorAction Stop).Source
$runtimeArgs = @('-B', '-X', 'utf8', $runtimeScript, '--project-root', $ProjectRoot, '--action', $Action)
if (-not [string]::IsNullOrWhiteSpace($RequestFile)) {
    $runtimeArgs += @('--request-file', $RequestFile)
}
& $runtimePython @runtimeArgs
exit $LASTEXITCODE

