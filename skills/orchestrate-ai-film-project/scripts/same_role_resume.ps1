[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Inspect', 'Initialize', 'Claim', 'PrepareHandoff', 'ActivateHandoff', 'CancelHandoff', 'Update')]
    [string]$Mode,

    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [Parameter(Mandatory = $true)]
    [ValidateSet('B', 'C', 'D')]
    [string]$Role,

    [string]$InputJsonPath,
    [string]$InputJson,
    [int]$ExpectedRevision = -1,
    [string]$LeaseId,
    [string]$Holder,
    [ValidateRange(5, 1440)]
    [int]$LeaseMinutes = 240,
    [switch]$UserSwitchRequest,
    [switch]$PreviousHolderStopped,
    [string]$AdoptionEvidence,
    [string]$HandoffId,
    [string]$TargetTaskId,
    [string]$TargetHolder
)

$ErrorActionPreference = 'Stop'
$script:Schema = 'xiaomo.same-role-resume/v1'
$script:MaxCacheBytes = 6144
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$script:ProjectRootFull = $null
$script:CachePath = $null

try {
    [Console]::OutputEncoding = $script:Utf8NoBom
} catch {
    # Console encoding must not affect cache correctness.
}

function ConvertTo-NativeValue {
    param($Value)

    if ($null -eq $Value) { return $null }
    if ($Value -is [System.DateTimeOffset]) {
        return $Value.ToUniversalTime().UtcDateTime.ToString('o', [System.Globalization.CultureInfo]::InvariantCulture)
    }
    if ($Value -is [System.DateTime]) {
        if ($Value.Kind -eq [System.DateTimeKind]::Unspecified) {
            return $Value.ToString('o', [System.Globalization.CultureInfo]::InvariantCulture)
        }
        return $Value.ToUniversalTime().ToString('o', [System.Globalization.CultureInfo]::InvariantCulture)
    }
    if ($Value -is [System.Collections.IDictionary]) {
        $result = [ordered]@{}
        foreach ($key in $Value.Keys) {
            $result[[string]$key] = ConvertTo-NativeValue -Value $Value[$key]
        }
        return $result
    }
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        $result = [ordered]@{}
        foreach ($property in $Value.PSObject.Properties) {
            $result[$property.Name] = ConvertTo-NativeValue -Value $property.Value
        }
        return $result
    }
    if (($Value -is [System.Collections.IEnumerable]) -and -not ($Value -is [string])) {
        $items = @()
        foreach ($item in $Value) {
            $items += ,(ConvertTo-NativeValue -Value $item)
        }
        return ,$items
    }
    return $Value
}

function Get-MapValue {
    param(
        [System.Collections.IDictionary]$Map,
        [Parameter(Mandatory = $true)][string]$Name,
        $Default = $null
    )
    if ($null -ne $Map -and $Map.Contains($Name)) { return $Map[$Name] }
    return $Default
}

function Test-MapHasKey {
    param([System.Collections.IDictionary]$Map, [string]$Name)
    return ($null -ne $Map -and $Map.Contains($Name))
}

function ConvertTo-JsonLine {
    param($Value)
    return ($Value | ConvertTo-Json -Depth 64 -Compress)
}

function Write-Result {
    param($Value)
    Write-Output (ConvertTo-JsonLine -Value $Value)
}

function Get-UtcTimestamp {
    return [DateTime]::UtcNow.ToString('o', [System.Globalization.CultureInfo]::InvariantCulture)
}

function Get-UtcExpiryTimestamp {
    return [DateTime]::UtcNow.AddMinutes($LeaseMinutes).ToString('o', [System.Globalization.CultureInfo]::InvariantCulture)
}

function Get-CacheRelativePath {
    return ('续接缓存/{0}_同岗续接.json' -f $Role)
}

function Resolve-SafeProjectPath {
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [switch]$SkipReparseCheck
    )

    if ([string]::IsNullOrWhiteSpace($RelativePath)) { throw 'Project-relative path is empty.' }
    if ([System.IO.Path]::IsPathRooted($RelativePath) -or $RelativePath -match '^[A-Za-z]:') {
        throw "Absolute paths are not allowed in resume cache: $RelativePath"
    }

    $normalizedInput = $RelativePath.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    $full = [System.IO.Path]::GetFullPath((Join-Path $script:ProjectRootFull $normalizedInput))
    $root = $script:ProjectRootFull.TrimEnd([char[]]'\/')
    $prefix = $root + [System.IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escapes project root: $RelativePath"
    }

    if (-not $SkipReparseCheck) {
        $relativeNormalized = $full.Substring($prefix.Length)
        $current = $root
        foreach ($part in @($relativeNormalized -split '[\\/]')) {
            if ([string]::IsNullOrWhiteSpace($part)) { continue }
            $current = Join-Path $current $part
            if (Test-Path -LiteralPath $current) {
                $item = Get-Item -LiteralPath $current -Force
                if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "Reparse points are not allowed in resume-cache dependencies: $RelativePath"
                }
            }
        }
    }
    return $full
}

function Get-Sha256Bytes {
    param([byte[]]$Bytes)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Normalize-MarkdownH2SectionName {
    param([Parameter(Mandatory = $true)][string]$Section)

    $normalized = $Section.Trim()
    if ($normalized -match '^##[ \t]+(.+?)[ \t]*$') {
        $normalized = $Matches[1].Trim()
    }
    if ([string]::IsNullOrWhiteSpace($normalized) -or $normalized.Contains("`n") -or $normalized.Contains("`r")) {
        throw 'markdown_h2 requires one non-empty H2 title.'
    }
    return $normalized
}

function Get-WholeFileHash {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Dependency file does not exist: $Path" }
    $stream = [System.IO.File]::OpenRead($Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Get-MarkdownH2SectionText {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Section
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Dependency file does not exist: $Path" }
    if ([string]::IsNullOrWhiteSpace($Section)) { throw 'markdown_h2 requires a non-empty section name.' }

    $normalizedSection = Normalize-MarkdownH2SectionName -Section $Section

    $text = [System.IO.File]::ReadAllText($Path, $script:Utf8NoBom)
    $text = $text.Replace("`r`n", "`n").Replace("`r", "`n")
    $lines = @([System.Text.RegularExpressions.Regex]::Split($text, "`n"))
    $start = -1
    $finish = $lines.Count
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^##[ \t]+(.+?)[ \t]*$') {
            $title = $Matches[1].Trim()
            if ($start -lt 0 -and $title -ceq $normalizedSection) {
                $start = $i
                continue
            }
            if ($start -ge 0) {
                $finish = $i
                break
            }
        }
    }
    if ($start -lt 0) { throw "Markdown H2 section was not found: $normalizedSection in $Path" }

    $slice = @()
    if ($finish -gt $start) { $slice = @($lines[$start..($finish - 1)]) }
    $sectionText = ($slice -join "`n").TrimEnd([char[]]"`r`n") + "`n"
    return $sectionText
}

function Get-MarkdownLiteralLineText {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Selector
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Dependency file does not exist: $Path" }
    if ([string]::IsNullOrWhiteSpace($Selector)) { throw 'markdown_line requires a non-empty literal selector.' }

    $text = [System.IO.File]::ReadAllText($Path, $script:Utf8NoBom)
    $text = $text.Replace("`r`n", "`n").Replace("`r", "`n")
    $matched = @()
    foreach ($line in @([System.Text.RegularExpressions.Regex]::Split($text, "`n"))) {
        if ($line.Contains($Selector)) { $matched += ,$line }
    }
    if ($matched.Count -ne 1) {
        throw "markdown_line selector must match exactly one line; matched $($matched.Count): $Selector in $Path"
    }
    return ([string]$matched[0]) + "`n"
}

function Get-DependencyHash {
    param([System.Collections.IDictionary]$Dependency)

    $relativePath = [string](Get-MapValue -Map $Dependency -Name 'path')
    $hashMode = [string](Get-MapValue -Map $Dependency -Name 'hash_mode')
    $full = Resolve-SafeProjectPath -RelativePath $relativePath
    switch ($hashMode) {
        'whole' { return Get-WholeFileHash -Path $full }
        'markdown_h2' {
            $section = [string](Get-MapValue -Map $Dependency -Name 'section')
            $sectionText = Get-MarkdownH2SectionText -Path $full -Section $section
            return Get-Sha256Bytes -Bytes $script:Utf8NoBom.GetBytes($sectionText)
        }
        'markdown_line' {
            $selector = [string](Get-MapValue -Map $Dependency -Name 'selector')
            $lineText = Get-MarkdownLiteralLineText -Path $full -Selector $selector
            return Get-Sha256Bytes -Bytes $script:Utf8NoBom.GetBytes($lineText)
        }
        default { throw "Unsupported hash_mode '$hashMode' for $relativePath" }
    }
}

function Complete-DependencyHash {
    param([System.Collections.IDictionary]$Dependency)
    if ([string](Get-MapValue -Map $Dependency -Name 'hash_mode') -ceq 'markdown_h2') {
        $Dependency['section'] = Normalize-MarkdownH2SectionName -Section ([string](Get-MapValue -Map $Dependency -Name 'section'))
    }
    $expected = [string](Get-MapValue -Map $Dependency -Name 'sha256')
    if ([string]::IsNullOrWhiteSpace($expected)) {
        $Dependency['sha256'] = Get-DependencyHash -Dependency $Dependency
    }
}

function Normalize-PersistentCacheSchema {
    param([System.Collections.IDictionary]$Cache)

    $gate = Get-MapValue -Map $Cache -Name 'gate'
    $aTask = if ($gate -is [System.Collections.IDictionary]) { Get-MapValue -Map $gate -Name 'a_task' } else { $null }
    $source = if ($aTask -is [System.Collections.IDictionary]) { Get-MapValue -Map $aTask -Name 'source' } else { $null }
    if (($source -is [System.Collections.IDictionary]) -and
        [string](Get-MapValue -Map $source -Name 'hash_mode') -ceq 'markdown_h2') {
        $source['section'] = Normalize-MarkdownH2SectionName -Section ([string](Get-MapValue -Map $source -Name 'section'))
    }

    $readSets = Get-MapValue -Map $Cache -Name 'read_sets'
    if ($readSets -is [System.Collections.IDictionary]) {
        foreach ($setName in @('required', 'optional')) {
            foreach ($dependency in @((Get-MapValue -Map $readSets -Name $setName -Default @()))) {
                if (($dependency -is [System.Collections.IDictionary]) -and
                    [string](Get-MapValue -Map $dependency -Name 'hash_mode') -ceq 'markdown_h2') {
                    $dependency['section'] = Normalize-MarkdownH2SectionName -Section ([string](Get-MapValue -Map $dependency -Name 'section'))
                }
            }
        }
    }
}

function Get-InputMap {
    if (-not [string]::IsNullOrWhiteSpace($InputJson) -and -not [string]::IsNullOrWhiteSpace($InputJsonPath)) {
        throw 'Use only one of -InputJson or -InputJsonPath.'
    }
    $raw = $null
    if (-not [string]::IsNullOrWhiteSpace($InputJson)) {
        $raw = $InputJson
    } elseif (-not [string]::IsNullOrWhiteSpace($InputJsonPath)) {
        $inputFull = [System.IO.Path]::GetFullPath($InputJsonPath)
        if (-not (Test-Path -LiteralPath $inputFull -PathType Leaf)) { throw "Input JSON does not exist: $inputFull" }
        $raw = [System.IO.File]::ReadAllText($inputFull, $script:Utf8NoBom)
    }
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    $parsed = ConvertTo-NativeValue -Value ($raw | ConvertFrom-Json)
    if (-not ($parsed -is [System.Collections.IDictionary])) { throw 'Input JSON must be an object.' }
    return $parsed
}

function Merge-Maps {
    param(
        [System.Collections.IDictionary]$Base,
        [System.Collections.IDictionary]$Patch
    )
    foreach ($key in $Patch.Keys) {
        $patchValue = $Patch[$key]
        if (($patchValue -is [System.Collections.IDictionary]) -and
            (Test-MapHasKey -Map $Base -Name ([string]$key)) -and
            ($Base[$key] -is [System.Collections.IDictionary])) {
            Merge-Maps -Base $Base[$key] -Patch $patchValue
        } else {
            $Base[$key] = ConvertTo-NativeValue -Value $patchValue
        }
    }
}

function Assert-PatchAllowed {
    param([System.Collections.IDictionary]$Patch)
    $allowed = @('gate', 'cursor', 'adopted', 'pending', 'authority', 'read_sets', 'next_step', 'drafts')
    foreach ($key in $Patch.Keys) {
        if ($allowed -notcontains [string]$key) { throw "Update patch cannot modify reserved field: $key" }
    }
    if ((Test-MapHasKey -Map $Patch -Name 'adopted') -and [string]::IsNullOrWhiteSpace($AdoptionEvidence)) {
        throw 'Updating adopted facts requires -AdoptionEvidence that points to the authoritative adoption record.'
    }
}

function Set-AuthorityInvariants {
    param([System.Collections.IDictionary]$Cache)
    if (-not (Test-MapHasKey -Map $Cache -Name 'authority') -or -not ($Cache['authority'] -is [System.Collections.IDictionary])) {
        $Cache['authority'] = [ordered]@{}
    }
    $Cache['authority']['cache_is_authority'] = $false
    $Cache['authority']['resume_validation_grants_write'] = $false
    if (-not (Test-MapHasKey -Map $Cache['authority'] -Name 'observed')) {
        $Cache['authority']['observed'] = [ordered]@{}
    }
}

function Complete-AllMissingHashes {
    param([System.Collections.IDictionary]$Cache)
    $gate = Get-MapValue -Map $Cache -Name 'gate'
    $aTask = if ($gate -is [System.Collections.IDictionary]) { Get-MapValue -Map $gate -Name 'a_task' } else { $null }
    $source = if ($aTask -is [System.Collections.IDictionary]) { Get-MapValue -Map $aTask -Name 'source' } else { $null }
    if ($source -is [System.Collections.IDictionary]) { Complete-DependencyHash -Dependency $source }

    $readSets = Get-MapValue -Map $Cache -Name 'read_sets'
    if ($readSets -is [System.Collections.IDictionary]) {
        foreach ($setName in @('required', 'optional')) {
            foreach ($dependency in @((Get-MapValue -Map $readSets -Name $setName -Default @()))) {
                if ($dependency -is [System.Collections.IDictionary]) { Complete-DependencyHash -Dependency $dependency }
            }
        }
    }
}

function Test-Dependency {
    param(
        [System.Collections.IDictionary]$Dependency,
        [string]$SetName
    )
    $relativePath = [string](Get-MapValue -Map $Dependency -Name 'path')
    $expected = ([string](Get-MapValue -Map $Dependency -Name 'sha256')).ToLowerInvariant()
    $result = [ordered]@{
        set = $SetName
        path = $relativePath
        hash_mode = [string](Get-MapValue -Map $Dependency -Name 'hash_mode')
        section = Get-MapValue -Map $Dependency -Name 'section'
        selector = Get-MapValue -Map $Dependency -Name 'selector'
        status = 'valid'
        expected_sha256 = $expected
        actual_sha256 = $null
        message = $null
    }
    try {
        if ([string]::IsNullOrWhiteSpace($relativePath)) { throw 'Dependency path is empty.' }
        if ($expected -notmatch '^[0-9a-f]{64}$') { throw 'Expected sha256 must contain 64 lowercase hexadecimal characters.' }
        $actual = Get-DependencyHash -Dependency $Dependency
        $result.actual_sha256 = $actual
        if ($actual -cne $expected) {
            $result.status = 'changed'
            $result.message = 'Hash mismatch.'
        }
    } catch {
        $result.status = 'invalid'
        $result.message = $_.Exception.Message
    }
    return $result
}

function Test-CacheState {
    param(
        [System.Collections.IDictionary]$Cache,
        [int]$StoredBytes = -1
    )

    $errors = New-Object System.Collections.Generic.List[string]
    $gateChanges = New-Object System.Collections.Generic.List[object]
    $requiredChanges = New-Object System.Collections.Generic.List[object]
    $optionalChanges = New-Object System.Collections.Generic.List[object]
    $requiredLoad = New-Object System.Collections.Generic.List[object]

    if ($StoredBytes -gt $script:MaxCacheBytes) { $errors.Add("Cache exceeds $($script:MaxCacheBytes) UTF-8 bytes.") }
    if ([string](Get-MapValue -Map $Cache -Name 'schema') -cne $script:Schema) { $errors.Add('Unsupported or missing cache schema.') }
    if ([string](Get-MapValue -Map $Cache -Name 'role') -cne $Role) { $errors.Add('Cache role does not match requested role.') }
    $revisionValue = Get-MapValue -Map $Cache -Name 'revision' -Default 0
    if ([int64]$revisionValue -lt 1) { $errors.Add('Cache revision must be at least 1.') }

    $sourceTask = Get-MapValue -Map $Cache -Name 'source_task'
    if (-not ($sourceTask -is [System.Collections.IDictionary]) -or [string]::IsNullOrWhiteSpace([string](Get-MapValue -Map $sourceTask -Name 'task_id'))) {
        $errors.Add('source_task.task_id is required.')
    }

    $gate = Get-MapValue -Map $Cache -Name 'gate'
    $aTask = if ($gate -is [System.Collections.IDictionary]) { Get-MapValue -Map $gate -Name 'a_task' } else { $null }
    if (-not ($aTask -is [System.Collections.IDictionary])) {
        $errors.Add('gate.a_task is required.')
    } else {
        foreach ($field in @('task_id', 'target_role', 'stage_envelope', 'permission_boundary')) {
            if (-not (Test-MapHasKey -Map $aTask -Name $field) -or [string]::IsNullOrWhiteSpace([string]$aTask[$field])) {
                $errors.Add("gate.a_task.$field is required.")
            }
        }
        if ([string](Get-MapValue -Map $aTask -Name 'target_role') -cne $Role) {
            $errors.Add('A current-task target_role does not match the requested role.')
        }
        $gateSource = Get-MapValue -Map $aTask -Name 'source'
        if (-not ($gateSource -is [System.Collections.IDictionary])) {
            $errors.Add('gate.a_task.source is required.')
        } else {
            $gateResult = Test-Dependency -Dependency $gateSource -SetName 'a_gate'
            if ($gateResult.status -ne 'valid') { $gateChanges.Add($gateResult) }
        }
    }

    foreach ($field in @('cursor', 'adopted', 'pending', 'authority', 'read_sets')) {
        if (-not (Test-MapHasKey -Map $Cache -Name $field) -or -not ($Cache[$field] -is [System.Collections.IDictionary])) {
            $errors.Add("$field must be an object.")
        }
    }
    if ([string]::IsNullOrWhiteSpace([string](Get-MapValue -Map $Cache -Name 'next_step'))) {
        $errors.Add('next_step must be a non-empty string.')
    }

    $authority = Get-MapValue -Map $Cache -Name 'authority'
    if ($authority -is [System.Collections.IDictionary]) {
        if ((Get-MapValue -Map $authority -Name 'cache_is_authority' -Default $true) -ne $false) {
            $errors.Add('authority.cache_is_authority must be false.')
        }
        if ((Get-MapValue -Map $authority -Name 'resume_validation_grants_write' -Default $true) -ne $false) {
            $errors.Add('authority.resume_validation_grants_write must be false.')
        }
    }

    $readSets = Get-MapValue -Map $Cache -Name 'read_sets'
    if ($readSets -is [System.Collections.IDictionary]) {
        foreach ($setName in @('required', 'optional', 'skip')) {
            if (-not (Test-MapHasKey -Map $readSets -Name $setName)) { $errors.Add("read_sets.$setName is required.") }
        }
        foreach ($dependency in @((Get-MapValue -Map $readSets -Name 'required' -Default @()))) {
            if (-not ($dependency -is [System.Collections.IDictionary])) {
                $errors.Add('Every required read-set entry must be an object.')
                continue
            }
            $item = Test-Dependency -Dependency $dependency -SetName 'required'
            if ($item.status -eq 'valid') { $requiredLoad.Add($item) } else { $requiredChanges.Add($item) }
        }
        foreach ($dependency in @((Get-MapValue -Map $readSets -Name 'optional' -Default @()))) {
            if (-not ($dependency -is [System.Collections.IDictionary])) {
                $errors.Add('Every optional read-set entry must be an object.')
                continue
            }
            $item = Test-Dependency -Dependency $dependency -SetName 'optional'
            if ($item.status -ne 'valid') { $optionalChanges.Add($item) }
        }
        foreach ($skipEntry in @((Get-MapValue -Map $readSets -Name 'skip' -Default @()))) {
            if ($skipEntry -is [string]) {
                if ([string]::IsNullOrWhiteSpace($skipEntry)) { $errors.Add('read_sets.skip cannot contain an empty entry.') }
            } elseif ($skipEntry -is [System.Collections.IDictionary]) {
                $skipPath = [string](Get-MapValue -Map $skipEntry -Name 'path')
                if (-not [string]::IsNullOrWhiteSpace($skipPath)) {
                    try { [void](Resolve-SafeProjectPath -RelativePath $skipPath) } catch { $errors.Add($_.Exception.Message) }
                }
            } else {
                $errors.Add('Every skip entry must be a string or an object with a project-relative path.')
            }
        }
    }

    $resumeReady = ($errors.Count -eq 0 -and $gateChanges.Count -eq 0 -and $requiredChanges.Count -eq 0)
    $recoveryMode = 'none'
    if ($gateChanges.Count -gt 0) { $recoveryMode = 'stop_at_a_gate' }
    elseif ($errors.Count -gt 0 -or $requiredChanges.Count -gt 0) { $recoveryMode = 'sectional' }

    return [ordered]@{
        resume_ready = $resumeReady
        project_write_authorized = $false
        external_action_authorized = $false
        recovery_mode = $recoveryMode
        errors = @($errors | ForEach-Object { $_ })
        a_gate_changes = @($gateChanges | ForEach-Object { $_ })
        required_changes = @($requiredChanges | ForEach-Object { $_ })
        optional_changes = @($optionalChanges | ForEach-Object { $_ })
        required_load = @($requiredLoad | ForEach-Object { $_ })
    }
}

function Get-SerializedCache {
    param([System.Collections.IDictionary]$Cache)
    Normalize-PersistentCacheSchema -Cache $Cache
    $json = ConvertTo-JsonLine -Value $Cache
    $bytes = $script:Utf8NoBom.GetByteCount($json)
    if ($bytes -gt $script:MaxCacheBytes) {
        throw "Resume cache would be $bytes UTF-8 bytes; maximum is $($script:MaxCacheBytes)."
    }
    return [ordered]@{ json = $json; bytes = $bytes }
}

function Read-CacheUnlocked {
    if (-not (Test-Path -LiteralPath $script:CachePath -PathType Leaf)) { return $null }
    $rawBytes = [System.IO.File]::ReadAllBytes($script:CachePath)
    $raw = $script:Utf8NoBom.GetString($rawBytes)
    try {
        $cache = ConvertTo-NativeValue -Value ($raw | ConvertFrom-Json)
    } catch {
        throw "Resume cache is invalid JSON: $($script:CachePath)"
    }
    if (-not ($cache -is [System.Collections.IDictionary])) { throw 'Resume cache root must be an object.' }
    return [ordered]@{ cache = $cache; bytes = $rawBytes.Length }
}

function Write-CacheAtomic {
    param([System.Collections.IDictionary]$Cache)
    $serialized = Get-SerializedCache -Cache $Cache
    $directory = Split-Path -Parent $script:CachePath
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        New-Item -ItemType Directory -Path $directory | Out-Null
    }
    $temp = "$($script:CachePath).tmp.$([Guid]::NewGuid().ToString('N'))"
    $backup = "$($script:CachePath).bak.$([Guid]::NewGuid().ToString('N'))"
    try {
        [System.IO.File]::WriteAllText($temp, [string]$serialized.json, $script:Utf8NoBom)
        if (Test-Path -LiteralPath $script:CachePath -PathType Leaf) {
            [System.IO.File]::Replace($temp, $script:CachePath, $backup, $true)
            if (Test-Path -LiteralPath $backup -PathType Leaf) { Remove-Item -LiteralPath $backup -Force }
        } else {
            [System.IO.File]::Move($temp, $script:CachePath)
        }
    } finally {
        if (Test-Path -LiteralPath $temp -PathType Leaf) { Remove-Item -LiteralPath $temp -Force }
        if (Test-Path -LiteralPath $backup -PathType Leaf) { Remove-Item -LiteralPath $backup -Force }
    }
    return [int]$serialized.bytes
}

function Invoke-WithCacheLock {
    param([Parameter(Mandatory = $true)][scriptblock]$Action)

    $directory = Split-Path -Parent $script:CachePath
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        New-Item -ItemType Directory -Path $directory | Out-Null
    }
    $lockPath = "$($script:CachePath).lock"
    $lockStream = $null
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        try {
            $lockStream = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
            break
        } catch [System.IO.IOException] {
            Start-Sleep -Milliseconds 100
        }
    }
    if ($null -eq $lockStream) { throw 'Could not acquire resume-cache lock within 5 seconds.' }
    try {
        return & $Action
    } finally {
        $lockStream.Dispose()
    }
}

function Assert-ExpectedRevision {
    param([System.Collections.IDictionary]$Cache)
    if ($ExpectedRevision -lt 1) { throw '-ExpectedRevision is required for this mode.' }
    $actual = [int](Get-MapValue -Map $Cache -Name 'revision' -Default 0)
    if ($actual -ne $ExpectedRevision) { throw "Revision conflict: expected $ExpectedRevision, current $actual." }
}

function Test-LeaseActive {
    param([System.Collections.IDictionary]$Lease)
    if ($null -eq $Lease) { return $false }
    if ([string](Get-MapValue -Map $Lease -Name 'status') -cne 'active') { return $false }
    $expiresValue = Get-MapValue -Map $Lease -Name 'expires_at_utc'
    $expiresUtc = [DateTime]::MinValue
    if ($expiresValue -is [System.DateTimeOffset]) {
        $expiresUtc = $expiresValue.UtcDateTime
    } elseif ($expiresValue -is [System.DateTime]) {
        if ($expiresValue.Kind -eq [System.DateTimeKind]::Unspecified) { return $false }
        $expiresUtc = $expiresValue.ToUniversalTime()
    } else {
        $expiresRaw = [string]$expiresValue
        if ($expiresRaw -notmatch '(?:[zZ]|[+-]00:00)$') { return $false }
        $expires = [DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse($expiresRaw, [System.Globalization.CultureInfo]::InvariantCulture,
                [System.Globalization.DateTimeStyles]::RoundtripKind, [ref]$expires)) { return $false }
        $expiresUtc = $expires.UtcDateTime
    }
    return ($expiresUtc -gt [DateTime]::UtcNow)
}

function Test-UninitializedSkeleton {
    param([System.Collections.IDictionary]$Cache)

    if ([string](Get-MapValue -Map $Cache -Name 'schema') -cne $script:Schema) { return $false }
    if ([string](Get-MapValue -Map $Cache -Name 'role') -cne $Role) { return $false }
    if ([int](Get-MapValue -Map $Cache -Name 'revision' -Default -1) -ne 0) { return $false }
    $authority = Get-MapValue -Map $Cache -Name 'authority'
    if (-not ($authority -is [System.Collections.IDictionary])) { return $false }
    if ((Get-MapValue -Map $authority -Name 'cache_is_authority' -Default $true) -ne $false) { return $false }
    if ((Get-MapValue -Map $authority -Name 'resume_validation_grants_write' -Default $true) -ne $false) { return $false }
    if (-not [string]::IsNullOrWhiteSpace([string](Get-MapValue -Map $Cache -Name 'updated_at_utc'))) { return $false }

    $sourceTask = Get-MapValue -Map $Cache -Name 'source_task'
    if (-not ($sourceTask -is [System.Collections.IDictionary]) -or
        -not [string]::IsNullOrWhiteSpace([string](Get-MapValue -Map $sourceTask -Name 'task_id'))) { return $false }

    $gate = Get-MapValue -Map $Cache -Name 'gate'
    $aTask = if ($gate -is [System.Collections.IDictionary]) { Get-MapValue -Map $gate -Name 'a_task' } else { $null }
    if (-not ($aTask -is [System.Collections.IDictionary])) { return $false }
    foreach ($field in @('task_id', 'target_role', 'stage_envelope', 'permission_boundary')) {
        if (-not [string]::IsNullOrWhiteSpace([string](Get-MapValue -Map $aTask -Name $field))) { return $false }
    }
    $gateSource = Get-MapValue -Map $aTask -Name 'source'
    if (-not ($gateSource -is [System.Collections.IDictionary])) { return $false }
    if ([string](Get-MapValue -Map $gateSource -Name 'path') -cne 'A_总控.md') { return $false }
    if ([string](Get-MapValue -Map $gateSource -Name 'hash_mode') -cne 'markdown_h2') { return $false }
    if ((Normalize-MarkdownH2SectionName -Section ([string](Get-MapValue -Map $gateSource -Name 'section'))) -cne '当前任务') { return $false }
    if (-not [string]::IsNullOrWhiteSpace([string](Get-MapValue -Map $gateSource -Name 'sha256'))) { return $false }

    foreach ($field in @('cursor', 'adopted', 'pending', 'drafts')) {
        $value = Get-MapValue -Map $Cache -Name $field
        if (-not ($value -is [System.Collections.IDictionary]) -or $value.Count -ne 0) { return $false }
    }
    if (-not [string]::IsNullOrWhiteSpace([string](Get-MapValue -Map $Cache -Name 'next_step'))) { return $false }

    $readSets = Get-MapValue -Map $Cache -Name 'read_sets'
    if (-not ($readSets -is [System.Collections.IDictionary])) { return $false }
    foreach ($setName in @('required', 'optional', 'skip')) {
        if (-not (Test-MapHasKey -Map $readSets -Name $setName)) { return $false }
        $setItems = @((Get-MapValue -Map $readSets -Name $setName))
        if ($setItems.Count -ne 0) { return $false }
    }

    if ($null -ne (Get-MapValue -Map $Cache -Name 'lease')) { return $false }
    if ($null -ne (Get-MapValue -Map $Cache -Name 'handoff')) { return $false }
    return $true
}

function Assert-LeaseOwner {
    param([System.Collections.IDictionary]$Cache)
    $lease = Get-MapValue -Map $Cache -Name 'lease'
    if (-not ($lease -is [System.Collections.IDictionary]) -or -not (Test-LeaseActive -Lease $lease)) {
        throw 'No active lease. Inspect and Claim the cache before writing.'
    }
    if ([string]::IsNullOrWhiteSpace($LeaseId) -or [string](Get-MapValue -Map $lease -Name 'lease_id') -cne $LeaseId) {
        throw 'Lease conflict: lease_id does not match the active owner.'
    }
    if (-not [string]::IsNullOrWhiteSpace($Holder) -and [string](Get-MapValue -Map $lease -Name 'holder') -cne $Holder) {
        throw 'Lease conflict: holder does not match the active owner.'
    }
    return $lease
}

function Renew-Lease {
    param([System.Collections.IDictionary]$Lease)
    $Lease['renewed_at_utc'] = Get-UtcTimestamp
    $Lease['expires_at_utc'] = Get-UtcExpiryTimestamp
}

function Assert-ResumeReady {
    param([System.Collections.IDictionary]$Cache, [int]$Bytes)
    $validation = Test-CacheState -Cache $Cache -StoredBytes $Bytes
    if (-not $validation.resume_ready) {
        throw ('Resume cache is not ready: ' + (ConvertTo-JsonLine -Value $validation))
    }
    return $validation
}

function Apply-CachePatch {
    param(
        [System.Collections.IDictionary]$Cache,
        [System.Collections.IDictionary]$Patch
    )
    if ($null -eq $Patch) { return }
    Assert-PatchAllowed -Patch $Patch
    Merge-Maps -Base $Cache -Patch $Patch
    Set-AuthorityInvariants -Cache $Cache
    if (Test-MapHasKey -Map $Patch -Name 'adopted') {
        $Cache['authority']['last_adoption_evidence'] = $AdoptionEvidence
    }
    Complete-AllMissingHashes -Cache $Cache
}

try {
    $script:ProjectRootFull = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd([char[]]'\/')
    if (-not (Test-Path -LiteralPath $script:ProjectRootFull -PathType Container)) { throw "Project root does not exist: $($script:ProjectRootFull)" }
    foreach ($marker in @('开始这里.md', 'A_总控.md')) {
        if (-not (Test-Path -LiteralPath (Join-Path $script:ProjectRootFull $marker) -PathType Leaf)) {
            throw "Project marker is missing: $marker"
        }
        # Reject the studio route before A/role/cache/input processing or any lock creation.
        if ($marker -eq '开始这里.md') {
            $entryText = [System.IO.File]::ReadAllText((Join-Path $script:ProjectRootFull $marker), $script:Utf8NoBom)
            if ([regex]::IsMatch($entryText, '(?m)^[ \t]*(?:>[ \t]*)?workflowStudio:[ \t]*studio-v0\.9[ \t]*\r?$')) {
                throw 'Legacy same_role_resume modes are disabled for workflowStudio: studio-v0.9. Use studio_runtime instead.'
            }
        }
    }
    $roleFiles = [ordered]@{ B = 'B_故事剧本.md'; C = 'C_视觉生成.md'; D = 'D_后期剪辑.md' }
    $roleFile = $roleFiles[$Role]
    if (-not (Test-Path -LiteralPath (Join-Path $script:ProjectRootFull $roleFile) -PathType Leaf)) {
        throw "Role file is missing: $roleFile"
    }
    $script:CachePath = Resolve-SafeProjectPath -RelativePath (Get-CacheRelativePath)

    if ($Mode -eq 'Inspect') {
        if (-not (Test-Path -LiteralPath $script:CachePath -PathType Leaf)) {
            Write-Result -Value ([ordered]@{
                ok = $true; mode = $Mode; role = $Role; status = 'missing'; resume_ready = $false
                project_write_authorized = $false; recovery_mode = 'sectional'
                recovery_start = @('开始这里.md shared contract', 'A_总控.md ## 当前任务', "$roleFile current state/recent handoff")
                cache_path = Get-CacheRelativePath
            })
            exit 0
        }
        try {
            $loaded = Read-CacheUnlocked
            if (Test-UninitializedSkeleton -Cache $loaded.cache) {
                Write-Result -Value ([ordered]@{
                    ok = $true; mode = $Mode; role = $Role; status = 'uninitialized'; revision = 0
                    resume_ready = $false; initialization_required = $true
                    project_write_authorized = $false; external_action_authorized = $false
                    recovery_mode = 'sectional'
                    recovery_start = @('开始这里.md shared contract', 'A_总控.md ## 当前任务', "$roleFile current state/recent handoff")
                    cache_bytes = $loaded.bytes; cache_limit_bytes = $script:MaxCacheBytes
                    lease_active = $false; lease = $null; handoff = $null
                    cache_path = Get-CacheRelativePath
                })
                exit 0
            }
            $validation = Test-CacheState -Cache $loaded.cache -StoredBytes $loaded.bytes
            $lease = Get-MapValue -Map $loaded.cache -Name 'lease'
            Write-Result -Value ([ordered]@{
                ok = $true; mode = $Mode; role = $Role; status = if ($validation.resume_ready) { 'valid' } else { 'invalid' }
                resume_ready = $validation.resume_ready; project_write_authorized = $false
                external_action_authorized = $false; recovery_mode = $validation.recovery_mode
                revision = Get-MapValue -Map $loaded.cache -Name 'revision'
                cache_bytes = $loaded.bytes; cache_limit_bytes = $script:MaxCacheBytes
                lease_active = if ($lease -is [System.Collections.IDictionary]) { Test-LeaseActive -Lease $lease } else { $false }
                lease = $lease; handoff = Get-MapValue -Map $loaded.cache -Name 'handoff'
                validation = $validation; cache_path = Get-CacheRelativePath
            })
        } catch {
            Write-Result -Value ([ordered]@{
                ok = $true; mode = $Mode; role = $Role; status = 'corrupt'; resume_ready = $false
                project_write_authorized = $false; recovery_mode = 'sectional'; message = $_.Exception.Message
                recovery_start = @('开始这里.md shared contract', 'A_总控.md ## 当前任务', "$roleFile current state/recent handoff")
                cache_path = Get-CacheRelativePath
            })
        }
        exit 0
    }

    $result = Invoke-WithCacheLock -Action {
        $inputMap = Get-InputMap

        if ($Mode -eq 'Initialize') {
            if (Test-Path -LiteralPath $script:CachePath -PathType Leaf) {
                $existing = Read-CacheUnlocked
                if ($null -eq $existing -or -not (Test-UninitializedSkeleton -Cache $existing.cache)) {
                    throw 'Resume cache already exists and is not an uninitialized revision-0 skeleton; use Inspect and Update instead of overwriting it.'
                }
            }
            if ($null -eq $inputMap) { throw 'Initialize requires -InputJson or -InputJsonPath.' }

            $now = Get-UtcTimestamp
            $sourceTask = Get-MapValue -Map $inputMap -Name 'source_task'
            $cache = [ordered]@{
                schema = $script:Schema
                role = $Role
                revision = 1
                updated_at_utc = $now
                source_task = if ($sourceTask -is [System.Collections.IDictionary]) { $sourceTask } else { [ordered]@{ task_id = $Holder } }
                gate = Get-MapValue -Map $inputMap -Name 'gate' -Default ([ordered]@{})
                cursor = Get-MapValue -Map $inputMap -Name 'cursor' -Default ([ordered]@{})
                adopted = Get-MapValue -Map $inputMap -Name 'adopted' -Default ([ordered]@{})
                pending = Get-MapValue -Map $inputMap -Name 'pending' -Default ([ordered]@{})
                authority = Get-MapValue -Map $inputMap -Name 'authority' -Default ([ordered]@{ observed = [ordered]@{} })
                read_sets = Get-MapValue -Map $inputMap -Name 'read_sets' -Default ([ordered]@{ required = @(); optional = @(); skip = @() })
                next_step = Get-MapValue -Map $inputMap -Name 'next_step'
                drafts = Get-MapValue -Map $inputMap -Name 'drafts' -Default ([ordered]@{})
                lease = $null
                handoff = $null
            }
            Set-AuthorityInvariants -Cache $cache
            Complete-AllMissingHashes -Cache $cache
            if (-not [string]::IsNullOrWhiteSpace($Holder)) {
                $cache['source_task'] = [ordered]@{ task_id = $Holder }
                $cache['lease'] = [ordered]@{
                    status = 'active'; lease_id = [Guid]::NewGuid().ToString('D'); holder = $Holder
                    claimed_at_utc = $now; renewed_at_utc = $now
                    expires_at_utc = Get-UtcExpiryTimestamp
                }
            }
            $validation = Test-CacheState -Cache $cache -StoredBytes -1
            if (-not $validation.resume_ready) { throw ('Initial cache is invalid: ' + (ConvertTo-JsonLine -Value $validation)) }
            $bytes = Write-CacheAtomic -Cache $cache
            return [ordered]@{
                ok = $true; mode = $Mode; role = $Role; status = 'initialized'; revision = 1
                cache_bytes = $bytes; cache_limit_bytes = $script:MaxCacheBytes
                lease = $cache.lease; resume_ready = $true; project_write_authorized = $false
                cache_path = Get-CacheRelativePath
            }
        }

        $loaded = Read-CacheUnlocked
        if ($null -eq $loaded) { throw 'Resume cache is missing; use Initialize or sectional recovery.' }
        $cache = $loaded.cache

        if ($Mode -eq 'Claim') {
            if ([string]::IsNullOrWhiteSpace($Holder)) { throw 'Claim requires -Holder.' }
            Assert-ExpectedRevision -Cache $cache
            $validation = Assert-ResumeReady -Cache $cache -Bytes $loaded.bytes
            $existingLease = Get-MapValue -Map $cache -Name 'lease'
            $active = ($existingLease -is [System.Collections.IDictionary]) -and (Test-LeaseActive -Lease $existingLease)
            $existingHolder = if ($active) { [string](Get-MapValue -Map $existingLease -Name 'holder') } else { $null }
            if ($active -and $existingHolder -cne $Holder) {
                if (-not $UserSwitchRequest) {
                    throw "Cache has an active lease owned by '$existingHolder'. Explicit user switch evidence is required for takeover via -UserSwitchRequest."
                }
                if (-not $PreviousHolderStopped) {
                    throw "Cache has an active lease owned by '$existingHolder'. Evidence that the previous holder has stopped is required for takeover via -PreviousHolderStopped."
                }
            }
            $now = Get-UtcTimestamp
            $leaseIdValue = if ($active -and $existingHolder -ceq $Holder) {
                [string](Get-MapValue -Map $existingLease -Name 'lease_id')
            } else { [Guid]::NewGuid().ToString('D') }
            $cache['lease'] = [ordered]@{
                status = 'active'; lease_id = $leaseIdValue; holder = $Holder
                claimed_at_utc = $now; renewed_at_utc = $now
                expires_at_utc = Get-UtcExpiryTimestamp
            }
            $cache['handoff'] = $null
            $cache['source_task'] = [ordered]@{ task_id = $Holder }
            $cache['revision'] = [int]$cache['revision'] + 1
            $cache['updated_at_utc'] = $now
            $bytes = Write-CacheAtomic -Cache $cache
            return [ordered]@{
                ok = $true; mode = $Mode; role = $Role; status = if ($active -and $existingHolder -cne $Holder) { 'taken_over' } else { 'claimed' }
                revision = $cache.revision; lease = $cache.lease; cache_bytes = $bytes
                resume_ready = $true; project_write_authorized = $false; validation = $validation
                cache_path = Get-CacheRelativePath
            }
        }

        if ($Mode -eq 'Update') {
            if ($null -eq $inputMap) { throw 'Update requires -InputJson or -InputJsonPath.' }
            Assert-ExpectedRevision -Cache $cache
            $lease = Assert-LeaseOwner -Cache $cache
            $handoff = Get-MapValue -Map $cache -Name 'handoff'
            if (($handoff -is [System.Collections.IDictionary]) -and [string](Get-MapValue -Map $handoff -Name 'status') -ceq 'prepared') {
                throw 'Update is blocked while handoff is prepared; activate or cancel the handoff.'
            }
            Apply-CachePatch -Cache $cache -Patch $inputMap
            $cache['source_task'] = [ordered]@{ task_id = [string](Get-MapValue -Map $lease -Name 'holder') }
            Renew-Lease -Lease $lease
            $cache['revision'] = [int]$cache['revision'] + 1
            $cache['updated_at_utc'] = Get-UtcTimestamp
            $validation = Test-CacheState -Cache $cache -StoredBytes -1
            if (-not $validation.resume_ready) { throw ('Updated cache is invalid: ' + (ConvertTo-JsonLine -Value $validation)) }
            $bytes = Write-CacheAtomic -Cache $cache
            return [ordered]@{
                ok = $true; mode = $Mode; role = $Role; status = 'updated'; revision = $cache.revision
                lease = $cache.lease; cache_bytes = $bytes; resume_ready = $true
                project_write_authorized = $false; cache_path = Get-CacheRelativePath
            }
        }

        if ($Mode -eq 'PrepareHandoff') {
            if (-not $UserSwitchRequest) { throw 'PrepareHandoff requires explicit user switch evidence via -UserSwitchRequest.' }
            Assert-ExpectedRevision -Cache $cache
            $lease = Assert-LeaseOwner -Cache $cache
            $existingHandoff = Get-MapValue -Map $cache -Name 'handoff'
            if (($existingHandoff -is [System.Collections.IDictionary]) -and [string](Get-MapValue -Map $existingHandoff -Name 'status') -ceq 'prepared') {
                throw 'A handoff is already prepared.'
            }
            if ($null -ne $inputMap) { Apply-CachePatch -Cache $cache -Patch $inputMap }
            $validation = Test-CacheState -Cache $cache -StoredBytes -1
            if (-not $validation.resume_ready) { throw ('Cache cannot be handed off: ' + (ConvertTo-JsonLine -Value $validation)) }
            $handoffIdValue = [Guid]::NewGuid().ToString('D')
            $cache['handoff'] = [ordered]@{
                status = 'prepared'; handoff_id = $handoffIdValue
                source_holder = [string](Get-MapValue -Map $lease -Name 'holder')
                prepared_at_utc = Get-UtcTimestamp
            }
            Renew-Lease -Lease $lease
            $cache['revision'] = [int]$cache['revision'] + 1
            $cache['updated_at_utc'] = Get-UtcTimestamp
            $bytes = Write-CacheAtomic -Cache $cache
            return [ordered]@{
                ok = $true; mode = $Mode; role = $Role; status = 'handoff_prepared'
                revision = $cache.revision; handoff_id = $handoffIdValue; lease = $cache.lease
                cache_bytes = $bytes; project_write_authorized = $false
                next_action = 'Create the same-role task in the same project, then call ActivateHandoff. Call CancelHandoff if creation fails.'
                cache_path = Get-CacheRelativePath
            }
        }

        if ($Mode -eq 'ActivateHandoff') {
            foreach ($requiredValue in @(@{ name = 'HandoffId'; value = $HandoffId }, @{ name = 'TargetTaskId'; value = $TargetTaskId })) {
                if ([string]::IsNullOrWhiteSpace([string]$requiredValue.value)) { throw "ActivateHandoff requires -$($requiredValue.name)." }
            }
            Assert-ExpectedRevision -Cache $cache
            $oldLease = Assert-LeaseOwner -Cache $cache
            $handoff = Get-MapValue -Map $cache -Name 'handoff'
            if (-not ($handoff -is [System.Collections.IDictionary]) -or [string](Get-MapValue -Map $handoff -Name 'status') -cne 'prepared') {
                throw 'No prepared handoff exists.'
            }
            if ([string](Get-MapValue -Map $handoff -Name 'handoff_id') -cne $HandoffId) { throw 'handoff_id does not match the prepared handoff.' }
            [void](Assert-ResumeReady -Cache $cache -Bytes $loaded.bytes)
            $newHolder = if ([string]::IsNullOrWhiteSpace($TargetHolder)) { $TargetTaskId } else { $TargetHolder }
            $now = Get-UtcTimestamp
            $newLeaseId = [Guid]::NewGuid().ToString('D')
            $cache['lease'] = [ordered]@{
                status = 'active'; lease_id = $newLeaseId; holder = $newHolder
                claimed_at_utc = $now; renewed_at_utc = $now
                expires_at_utc = Get-UtcExpiryTimestamp
            }
            $cache['handoff'] = [ordered]@{
                status = 'activated'; handoff_id = $HandoffId
                source_holder = [string](Get-MapValue -Map $oldLease -Name 'holder')
                target_task_id = $TargetTaskId; activated_at_utc = $now
            }
            $cache['source_task'] = [ordered]@{ task_id = $TargetTaskId }
            $cache['revision'] = [int]$cache['revision'] + 1
            $cache['updated_at_utc'] = $now
            $bytes = Write-CacheAtomic -Cache $cache
            return [ordered]@{
                ok = $true; mode = $Mode; role = $Role; status = 'handoff_activated'
                revision = $cache.revision; lease = $cache.lease; handoff = $cache.handoff
                cache_bytes = $bytes; old_task_must_stop_writing = $true
                project_write_authorized = $false; cache_path = Get-CacheRelativePath
            }
        }

        if ($Mode -eq 'CancelHandoff') {
            if ([string]::IsNullOrWhiteSpace($HandoffId)) { throw 'CancelHandoff requires -HandoffId.' }
            Assert-ExpectedRevision -Cache $cache
            $lease = Assert-LeaseOwner -Cache $cache
            $handoff = Get-MapValue -Map $cache -Name 'handoff'
            if (-not ($handoff -is [System.Collections.IDictionary]) -or [string](Get-MapValue -Map $handoff -Name 'status') -cne 'prepared') {
                throw 'No prepared handoff exists.'
            }
            if ([string](Get-MapValue -Map $handoff -Name 'handoff_id') -cne $HandoffId) { throw 'handoff_id does not match the prepared handoff.' }
            $cache['handoff'] = $null
            Renew-Lease -Lease $lease
            $cache['revision'] = [int]$cache['revision'] + 1
            $cache['updated_at_utc'] = Get-UtcTimestamp
            $bytes = Write-CacheAtomic -Cache $cache
            return [ordered]@{
                ok = $true; mode = $Mode; role = $Role; status = 'handoff_cancelled'
                revision = $cache.revision; lease = $cache.lease; cache_bytes = $bytes
                old_task_may_continue = $true; project_write_authorized = $false
                cache_path = Get-CacheRelativePath
            }
        }

        throw "Unhandled mode: $Mode"
    }

    Write-Result -Value $result
} catch {
    $errorResult = [ordered]@{
        ok = $false
        mode = $Mode
        role = $Role
        project_write_authorized = $false
        external_action_authorized = $false
        error = $_.Exception.Message
        error_type = $_.Exception.GetType().FullName
        error_at = $_.InvocationInfo.PositionMessage
        error_stack = $_.ScriptStackTrace
    }
    [Console]::Error.WriteLine((ConvertTo-JsonLine -Value $errorResult))
    exit 1
}
