# 素材整理 helper 接口

`scripts/media_catalog.py` 使用 Python 3 标准库。只处理用户点名的完整视频文件及其来源时间备注；不读取剪映工程、不生成影视编号、不导出片段、不作艺术采用判断。扩展名检查不证明文件可解码或具备艺术可用性。

## 项目与索引

项目入口必须已有唯一 `workflowStudio: studio-v0.9` 和 `workflowMediaLayout: media-layout-v1` 或 `media-layout-v2`。三个视频分类目录必须已存在；脚本不迁移、修补旧布局。媒体分类复用兄弟 `start-ai-film-project/references/media-layout-v1.json` 或 `media-layout-v2.json`，制作资料映射复用 `orchestrate-ai-film-project/references/studio-project-layout-v1.json`。

- 有 `workflowDocsLayout: production-docs-v1`：`90_制作资料/生成记录/素材整理/素材索引.json`。
- 无文档布局标识：`生成记录/素材整理/素材索引.json`。
- 索引 schema：`xiaomo.media-catalog/v1`。只有这一份持久索引；展示表从查询结果生成，不另存第二份状态台账。
- `entry_key` 为随机机械主键，不是 S/A/V/CUT/PUB 编号。项目内 `path` 存相对路径，项目外 reference 存绝对路径；`list` 返回用于打开的 `resolved_path`。

项目整体移动后，内部路径随项目根解析；外部 reference 和历史下载来源不自动搬动。旧 prepare 计划绑定当时绝对根，移动项目后须重新准备。

## 调用形式

以下变量使用当前真实路径，不把示例盘符写入项目配置。`--request-file` 必须为绝对路径。JSON 采用 UTF-8，可带 BOM；脚本 stdout/stderr 均输出 UTF-8。计划保存位置由调用者决定，建议留在任务临时目录，不在项目里预建重复台账。

```powershell
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
$helper = '<当前安装技能目录>\organize-ai-film-media\scripts\media_catalog.py'
$project = '<项目绝对路径>'
$request = '<临时请求JSON绝对路径>'
$plan = '<临时计划JSON绝对路径>'
python -B $helper --project-root $project --action prepare --request-file $request |
    Set-Content -LiteralPath $plan -Encoding UTF8
python -B $helper --project-root $project --action apply --request-file $plan
python -B $helper --project-root $project --action list --segment '巷口回头'
```

调用者检查 prepare 退出码后才能使用保存的计划。`prepare` 和 `list` 不创建项目文件；`prepare` 读取并哈希点名源，为即将执行的写入做准备，不自动搜索下载目录或素材库。

## prepare：准备明确文件

```json
{
  "segment": "巷口回头",
  "category": "Aroll",
  "probe_duration": true,
  "sources": [
    {
      "path": "D:\\Downloads\\result-1.mp4",
      "in_editor": false,
      "note": "后段眼神可考虑，尚未定位"
    },
    {
      "path": "D:\\Downloads\\result-2.mp4",
      "in_editor": false,
      "duration_ms": 10000,
      "duration_source": "用户报告原片时长为10秒",
      "ranges": [
        {"clock": "source", "in_ms": 1000, "out_ms": 3500,
         "evidence": "用户审看后指出", "note": "回头动作"},
        {"clock": "source", "in_ms": 8000, "out_ms": 9500,
         "evidence": "用户审看后指出", "note": "结尾反应"}
      ]
    }
  ]
}
```

`category` 仅为 `Aroll`、`Broll`、`其他素材`。`segment` 是一个安全目录名，不接受斜杠、上级跳转、Windows 保留名等。`sources` 只列本批文件；用户已声明整批未导入时，可机械展开为每条 `in_editor:false`，无须逐文件重复问。

| `in_editor` | 操作 |
| --- | --- |
| 明确 `false` | 复制完整原片到 `02_视频/<category>/<segment>/<segment>_生成01.<ext>` 等空闲文件名，下载原件保留。只与同组 copy 条目按完整字节 SHA-256 去重。 |
| `true` | 只登记原路径；只复用同组、同一真实路径且字节相同的既有条目，包括此前已归档到此路径的 copy。 |
| `null` 或省略 | 按原位登记处理，不推测尚未导入。 |

同名不同内容分配新的生成序号，不覆盖已有文件；同内容但跨片段/分类保留各自用途。不同真实路径的 copy 和 reference 不因字节相同而相互吞并，两个不同原位路径也保留各自入口。此前已归档的文件再以 `true/null` 原位登记时，同组同路径同 hash 复用原条目，保留其历史 storage，只补 `origins`，不复制或另建主键。重复 copy 的新来源也追加到 `origins`；已登记条目的备注和区间不被 prepare 静默覆盖，后续用 set-ranges 明确更新。

`duration_ms`、`duration_source`、`ranges`、`note` 均可不填。数字时长必须附来源说明。默认只尝试本机已存在的 `ffprobe` 读取 format.duration，超时、不可用或读取失败则记 `duration_ms:null`、`duration_source:unknown`；不安装、不转码。`probe_duration:false` 可跳过探测。元数据时长和用户报告时长均不是影片审看。

prepare 返回 `xiaomo.media-catalog-plan/v1`：含项目入口 hash、当前索引 hash（尚不存在时为 null）、点名来源 hash、目标路径及 add/reuse 操作。不要手工改计划来绕过导入状态、目标冲突或并发变化。

## apply：执行已准备的归档

apply 接收完整 prepare 输出。执行前重读项目入口、源、已存在目标和索引；不一致就停止。写入只限计划所属的既有视频分类下本组复制件，以及对应 `素材整理` 索引目录。

成功回执中，`copied_paths` 列出本次新复制的完整目标路径，`reused_paths` 列出经核验复用的完整目标路径，包含正常重复归档/原位复用及失败重试时复用已复制件；`entry_keys` 对应计划中的每项输入。`reused_paths` 不代表新增复制或艺术采用。

复制先写同目录随机临时文件，核对源和临时副本字节，再排他发布：Windows 使用不覆盖已存在目标的 `os.rename`；其他系统使用排他硬链接。索引采用临时 JSON + `os.replace` 原子发布，并用 `素材索引.lock` 协调本 helper 的并发写入；它不使用或改变电影工作集租约。

失败时 stderr 为 JSON，退出码为 1。执行阶段错误含 `copied_paths` 和 `catalog_updated:false`。已经完整复制的文件保留，旧索引保留；相同计划重试可复用内容完全一致的已计划目标。如果索引、项目入口或来源已变化，重新 prepare，不强行重放。普通失败会删除本调用临时文件与锁；进程被强制结束可能留下锁/临时文件，确认原进程已停止后再人工处理这些明确路径，脚本不自动抢锁或删媒体。

锁只协调遵守该协议的 helper；操作系统路径检查和多次哈希不能保证对抗另一个程序在极短窗口恶意替换目录或文件。不要把这一机械防并发能力描述成跨应用事务或安全沙箱。

## set-ranges：修改一个文件的源时间记录

先 `list` 取得该记录完整区间及当前索引 hash。仅修改点名区间时，先与未点名区间合并；接口的 `ranges` 会替换该文件完整范围集。省略 `ranges` 则保留原范围，适合只补未定位 note。

也可成对传入 `duration_ms` 和 `duration_source`，为这一条补录或修正原片时长：前者为正整数毫秒，后者为非空出处；缺一即拒绝。两项均省略则保留现有时长。脚本先更新本条时长，再重新校验本次完整范围集；省略 `ranges` 时也会重新校验原有全部区间并更新 `boundary_check`。若缩短时长使保留区间越界，整次更新拒绝，不会静默删区间。

```json
{
  "entry_key": "从查询结果复制的32位机械键",
  "expected_catalog_sha256": "从查询结果复制的64位SHA256",
  "expected_source_sha256": "从该条记录复制的source_sha256",
  "ranges": [
    {"clock": "source", "in_ms": 1000, "out_ms": 3000,
     "evidence": "用户实际审看后给定", "note": "动作前半段"},
    {"clock": "source", "in_ms": 2000, "out_ms": 3500,
     "evidence": "用户给出的另一种截取意见", "note": "备选范围"}
  ],
  "note": "这些是整理备注，尚未作艺术采用"
}
```

源时间以整数毫秒记录 `[in_ms, out_ms)`，不是剪映时间线。允许多个、不连续或重叠区间；要求 `0 <= in_ms < out_ms`，每个数字区间必须有用户意见或实际审看 `evidence`。已知时长时不得超界，标 `within_recorded_duration`；未知时长只标 `unverified_duration`，不冒充已验证边界。脚本检查字段与数学边界，不核实证据真实性或画面质量。

必须同时给索引及点名源 hash；原片发生变化拒绝写入。此动作只改该条范围、note 及明确给出的时长/出处，不改其他条目、不移动已导入文件、不改分类或剪映工程。

## list：快速查找

```powershell
python -B $helper --project-root $project --action list --category Aroll --text '眼神'
```

`--segment`、`--category` 精确筛选，`--text` 搜索记录文字。stdout 含当前索引 hash、实际 `resolved_path`、原片区间和备注，可据此展示表格；不会保存第二份台账、播放或剪辑。筛中条目仅检查文件是否存在：`available:false` 显示失效路径，`content_checked:false` 表示本次未重算媒体 hash，不自动寻找同名替换。

## 本批验证边界

`tests/test_media_catalog.py` 在候选批次内部建立并清理临时项目。二进制占位文件明确不是有效视频，只验证复制/原位/去重/碰名、便携路径、并发与越界拒绝、区间数学、失败重试和查询零写入；不证明视频可解码、真实剪映链接或创作效果。
