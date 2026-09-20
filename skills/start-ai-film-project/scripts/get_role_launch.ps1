[CmdletBinding()]
param([string]$ProjectRoot)

$ErrorActionPreference = 'Stop'

function Get-V1RoleLaunchPackage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [string]$ReadRoot
    )

    if (-not [System.IO.Path]::IsPathRooted($ProjectRoot) -or
        ($ProjectRoot -notmatch '^[A-Za-z]:[\\/]' -and $ProjectRoot -notmatch '^\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$)')) {
        throw 'ProjectRoot 必须为明确的绝对路径。'
    }
    $targetRoot = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd([char[]]'\/')
    if ([string]::IsNullOrWhiteSpace($ReadRoot)) { $ReadRoot = $targetRoot }
    $sourceRoot = [System.IO.Path]::GetFullPath($ReadRoot).TrimEnd([char[]]'\/')
    if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
        throw "项目目录不存在：$sourceRoot"
    }
    $probe = $sourceRoot
    while (-not [string]::IsNullOrWhiteSpace($probe)) {
        $item = Get-Item -LiteralPath $probe -Force
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "项目路径包含重解析点：$probe"
        }
        $probe = Split-Path -Parent $probe
    }
    $entryPath = Join-Path $sourceRoot '开始这里.md'
    $capabilityPath = Join-Path $sourceRoot '工作室能力.json'
    foreach ($path in @($entryPath, $capabilityPath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "项目缺少入口文件：$path" }
        if ((Get-Item -LiteralPath $path -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "入口文件不能为重解析点：$path"
        }
    }
    $entry = [System.IO.File]::ReadAllText($entryPath, [System.Text.Encoding]::UTF8)
    foreach ($marker in @(@('workflowRelease', '1.0'), @('workflowStudio', 'studio-v0.9'))) {
        $matches = [regex]::Matches($entry, ('(?im)^[ \t]*(?:>[ \t]*)?' + $marker[0] + '[ \t]*:[ \t]*([^\r\n]*)\r?$'))
        if ($matches.Count -ne 1 -or $matches[0].Groups[1].Value.Trim() -cne $marker[1]) {
            throw "岗位初始化只支持入口唯一声明 workflowRelease: 1.0 与 workflowStudio: studio-v0.9 的 V1 项目；$($marker[0]) 不匹配。"
        }
    }
    if ($entry -match '(?im)^[ \t]*(?:>[ \t]*)?workflowResume[ \t]*:' -or
        [regex]::Matches($entry, '(?m)^项目版本：v[1-9][0-9]*\r?$').Count -ne 1) {
        throw '需要已建档的 V1 项目，不能使用旧续接入口或未建档模板。'
    }
    $cap = [System.IO.File]::ReadAllText($capabilityPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($cap.schema -cne 'xiaomo.studio-capabilities/v1' -or $cap.workflowStudio -cne 'studio-v0.9') {
        throw '工作室能力 schema 或协议不匹配。'
    }
    foreach ($name in @('candidate_design', 'workset_runtime', 'adoption_transactions', 'media_execution')) {
        if ($cap.$name -isnot [bool]) { throw "工作室能力 $name 必须为布尔值。" }
    }
    if (-not $cap.candidate_design -or $cap.media_execution -or
        $cap.workset_runtime -ne $cap.adoption_transactions -or
        ($cap.stage -eq 'active' -and -not $cap.workset_runtime) -or
        (($cap.workset_runtime -or $cap.PSObject.Properties.Name -contains 'runtime_schema') -and $cap.runtime_schema -cne 'xiaomo.studio-runtime/v1')) {
        throw '工作室能力与 V1 契约不匹配；不修改能力文件或回退旧版。'
    }
    $docsMarkers = [regex]::Matches($entry, '(?im)^[ \t]*(?:>[ \t]*)?workflowDocsLayout[ \t]*:[ \t]*([^\r\n]*)\r?$')
    $docsPrefix = ''
    if ($docsMarkers.Count -gt 0) {
        if ($docsMarkers.Count -ne 1 -or $docsMarkers[0].Groups[1].Value.Trim() -cne 'production-docs-v1') {
            throw '不支持或不唯一的 workflowDocsLayout。'
        }
        $docsPrefix = '90_制作资料/'
    }
    $requiredFiles = @('开始这里.md', 'A_总控.md', 'B_故事剧本.md', 'C_视觉生成.md', 'D_后期剪辑.md', 'E_发行复盘.md', '工作室能力.json', ($docsPrefix + '工作稿/C1/开始这里.md'), ($docsPrefix + '工作稿/C2/开始这里.md'))
    foreach ($relative in $requiredFiles) {
        $path = Join-Path $sourceRoot $relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "项目缺少必需文件：$relative" }
        $probe = $path
        while ($probe -and $probe -ne $sourceRoot) {
            if ((Get-Item -LiteralPath $probe -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw "岗位入口路径包含重解析点：$relative"
            }
            $probe = Split-Path -Parent $probe
        }
    }
    $titles = [ordered]@{ A='A｜制片统筹'; B='B｜编剧开发'; C1='C1｜美术与资产'; C2='C2｜导演与视听制作'; D='D｜剪辑与声音后期'; E='E｜发行与复盘' }
    $headings = [ordered]@{ A='A_制片统筹启动词'; B='B_编剧开发启动词'; C1='C1_美术与资产启动词'; C2='C2_导演与视听制作启动词'; D='D_剪辑与声音后期启动词'; E='E_发行与复盘启动词' }
    foreach ($role in $titles.Keys) {
        if ([regex]::Matches($entry, ('(?m)^## ' + [regex]::Escape($headings[$role]) + '\r?$')).Count -ne 1) {
            throw "岗位启动词标题缺失或重复：$($headings[$role])"
        }
    }
    $standby = [ordered]@{}
    foreach ($role in @('B', 'C1', 'C2', 'D', 'E')) {
        $title = $titles[$role]
        $heading = $headings[$role]
        $standby[$role] = @"
$title 待命。项目根目录：$targetRoot
本轮只回复“$title 已待命”；不读文件、不调用工具或 Skill、不检查 A、不写入或接管工作集。
用户下次提出具体请求时，读“开始这里.md”的共享入口与“$heading”、工作室能力.json、A_总控.md 当前任务，再按请求读取必要专业材料。按已启用 V1 契约处理工作集与采用；前期咨询不要求 A 另行派岗。沿用已确认设定，不自动重审全片、采用历史候选或恢复旧授权；媒体执行、剪辑工程及调色暂停等选择以本项目当前记录和本次请求为准。
"@.Trim()
    }
    $fileList = $requiredFiles -join '、'
    $scope = if ($cap.stage -eq 'active') { '本项目为 V1 活动入口。' } else { '本项目仍为隔离候选；只在该隔离范围内建立待命入口。' }
    $prompt = @"
小陌 AI 影视项目｜V1 六岗位轻量启动

这是用户在当前 Codex 项目中创建或补齐 B、C1、C2、D、E 五个本地独立待命任务的明确授权。当前任务作为 A｜制片统筹，不创建第二个 A。
预期项目根目录：$targetRoot
$scope

1. 核实当前 cwd 等于预期根目录，且以下文件存在：$fileList。不一致就停止；本轮只建岗位入口，不加载业务正文、专业 Skill、历史候选或媒体。
2. 先发现当前实际可调用的项目查询、任务查询、改名和本地任务创建工具，再按真实 schema 调用。由绝对路径确定唯一目标 projectId，查询任务时每次 limit≤50；只统计该 projectId 且 cwd 精确匹配的任务。当前 A 仅以当前 threadId＋精确 cwd 作为例外。若结果不完整则定向补查；仍无法确认、存在第二个 A、同岗重复或职责歧义时停止，不猜测、不删除。工具缺失时明确列出缺失能力及“未执行”，并原样交付下面五段短消息供手动建岗；不能把未知岗位状态当作不存在，不能用子代理、命令行会话或数据库写入代替可见任务。
3. 前置核验通过后，将当前标题设为“A｜制片统筹”，已正确则跳过。已有唯一同岗任务就复用，不唤醒、不接管；缺失才依次创建 B｜编剧开发、C1｜美术与资产、C2｜导演与视听制作、D｜剪辑与声音后期、E｜发行与复盘。新任务属于目标项目，cwd 为预期根目录，仅发送对应短待命消息。禁用子代理、fork、handoff、工作树、项目外任务及模型/推理覆盖。
4. 结合创建回执和创建后的任务查询核对 title、projectId、cwd；异步创建用可用 wait_threads 读取简短待命状态，单次等待≤60秒，不读取完整任务历史、不追加业务。失败或歧义时停止后续创建，保留现状，不删除、不自动重试；区分已存在、已创建、缺失、状态未核实，附缺失岗位的短消息。无待命回执时不能宣称已待命。
5. 完成后留在 A 等我下一条具体请求。本轮不自动开始 B 故事阶段、不新建或接管工作集、不运行 SaveDraft/Adopt。空的待命任务不办理 PrepareHandoff；只有明确接替同一个已有工作集才走 V1 交接。用户请求专业咨询不恢复旧版“A 未派岗就停止”的门禁。
6. 已确认的剧本、人物与视觉设定沿用；不自动重审、采用历史候选或恢复旧返修游标与执行授权。其他项目及已有剪辑工程保持不动，不生成、剪辑、调色、导出、上传或发布。
"@.Trim()
    foreach ($role in $standby.Keys) {
        $prompt += "`n`n<<<ROLE_STANDBY_${role}_START>>>`n$($standby[$role])`n<<<ROLE_STANDBY_${role}_END>>>"
    }
    return [ordered]@{
        success=$true; action='prepare-role-launch'; projectRoot=$targetRoot; workflowVersion='1.0'; workflowStudio='studio-v0.9'
        projectModified=$false; tasksCreated=$false; requiredFiles=$requiredFiles; roleTitles=$titles
        standbyPrompts=$standby; launchPrompt=$prompt; launchPromptVersion=4; launchPromptChars=$prompt.Length
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        if ([string]::IsNullOrWhiteSpace($ProjectRoot)) { throw '请用 -ProjectRoot 指定已存在的 V1 项目绝对路径。' }
        Get-V1RoleLaunchPackage -ProjectRoot $ProjectRoot | ConvertTo-Json -Depth 6
        exit 0
    } catch {
        [ordered]@{success=$false; action='prepare-role-launch'; projectModified=$false; tasksCreated=$false; error=$_.Exception.Message} | ConvertTo-Json -Depth 3
        exit 1
    }
}
