$ErrorActionPreference='Stop'
$script:StudioDocumentLayoutDefinition=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\references\studio-project-layout-v1.json'))

function Get-StudioDocumentRelativePath {
    param([Parameter(Mandatory=$true)][string]$ProjectRoot,[Parameter(Mandatory=$true)][string]$RelativePath)
    $relative=$RelativePath.Replace('\','/')
    if([IO.Path]::IsPathRooted($relative) -or $relative -match '(^|/)(\.|\.\.)(/|$)' -or $relative -match ':'){throw 'Document path must be a safe project-relative path'}
    $entry=Join-Path ([IO.Path]::GetFullPath($ProjectRoot)) '开始这里.md'
    if(-not(Test-Path -LiteralPath $entry -PathType Leaf)){return $relative}
    $text=[IO.File]::ReadAllText($entry,[Text.Encoding]::UTF8)
    $markers=[regex]::Matches($text,'(?m)^[ \t]*(?:>[ \t]*)?workflowDocsLayout[ \t]*:[ \t]*(?<value>[^\r\n]*)\r?$')
    if($markers.Count -eq 0){return $relative}
    if($markers.Count -ne 1 -or $markers[0].Groups['value'].Value.Trim() -cne 'production-docs-v1'){throw 'Unknown or duplicate workflowDocsLayout marker'}
    $studio=[regex]::Matches($text,'(?m)^(?:>\s*)?workflowStudio:\s*(\S+)\s*$')
    if($studio.Count -ne 1 -or $studio[0].Groups[1].Value -cne 'studio-v0.9'){throw 'Production document layout requires explicit studio-v0.9'}
    $definition=Get-Content -LiteralPath $script:StudioDocumentLayoutDefinition -Raw -Encoding UTF8|ConvertFrom-Json
    if($definition.schema -cne 'xiaomo.production-docs-layout/v1' -or $definition.marker -cne 'production-docs-v1' -or $definition.directory -cne '90_制作资料' -or $definition.documentDirectories -isnot [array] -or $definition.documentDirectories.Count -ne 13 -or @($definition.documentDirectories|Sort-Object -Unique).Count -ne 13){throw 'Invalid shared production document layout'}
    foreach($directory in $definition.documentDirectories){
        if($directory -isnot [string] -or [string]::IsNullOrWhiteSpace($directory) -or $directory -in @('.','..') -or $directory -match '[\\/:]|[. ]$' -or $directory.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0){throw 'Unsafe production document directory'}
    }
    foreach($required in @('剧本','工作稿','续接缓存','流程改进日志')){if($definition.documentDirectories -cnotcontains $required){throw 'Required production document directory missing'}}
    $head=$relative.Split('/')[0]
    if(@($definition.documentDirectories) -contains $head){return $definition.directory+'/'+$relative}
    return $relative
}
