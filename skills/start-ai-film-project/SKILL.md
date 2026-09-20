---
name: start-ai-film-project
description: 为小陌 AI 影视工作流创建递增版本项目并交付完整启动包，或为已存在的 V1 项目初始化、补齐 A/B/C1/C2/D/E 本地岗位任务。用于新建项目、准备总启动词、启动六岗位或补齐岗位入口；不用于迁移项目、接替已有工作集、具体创作或媒体执行。v0.9/v0.8 保持各自兼容分支。
---

# 启动 AI 影视项目

建档过程只创建新项目、写入身份并交付脚本返回的启动词；不自动读取旧项目内容，不发展故事或执行媒体操作。V0.9 模式遵守 [共同契约](../orchestrate-ai-film-project/references/studio-v09-contract.md)；用户明确承接已有讨论时，另按下方条件入口转交上下文，不改变建档脚本职责。

## 先区分建档与岗位初始化

- 新建项目：按下文运行 `scripts/start_project.ps1`。V1 返回一段完整六岗位轻量启动包；用户发送到目标项目后，当前任务为 A，查询并补齐 B/C1/C2/D/E 五个独立待命任务，首轮只建入口。
- 已有 V1 项目要求总启动词或初始化/补齐岗位：读取[岗位任务初始化](references/role-task-launch.md)，调用 `scripts/get_role_launch.ps1 -ProjectRoot <绝对路径>`；仅生成启动包，不创建新项目、不修改已有项目。用户已明确要求创建或补齐任务时，按启动包检查工具并执行，不再以“本技能只新建项目”为由拒绝。
- 继续某个已有工作集或开展创作：回到项目专业入口，本技能不转移租约、不恢复旧派工。

岗位初始化首轮只检查启动所需入口与工具，不加载 A/B/C/D/E 创作正文或专业 Skill；缺少 Codex 原生任务工具时如实报告，不能用子代理或命令行会话代替用户可见岗位任务。

## 定位与调用

日常只需要项目名。未提供时只问项目叫什么。脚本按显式参数、本机安装配置、源码相对位置定位工作流；配置缺失才回退源码，不从聊天历史猜路径。

调用 scripts/start_project.ps1，日常传 -ProjectName；用户指定位置时增加 -WorkflowRoot 或 -ProjectsRoot。仅隔离测试与明确模板选择使用 -TemplateRoot，测试总是带 -NoOpen。

隐式模板由工作流 skills/manifest.json 的 bundleVersion 决定，安装配置的 workflowVersion 和实际模板必须一致；显式 -WorkflowRoot 优先于本机配置。配置缺失时从当前源码最近的工作流入口与 manifest 定位，不扫描历史候选或自动回退较老模板。明确 -TemplateRoot 可为新项目选择 v0.8 兼容模板；机器安装版本不改变任何已有项目。隔离测试始终使用隔离 -ProjectsRoot、-NoOpen，并在模板测试中明确 -TemplateRoot。

## 版本分支

产品版本与协议版本分别识别：新 V1 模板只声明一处 workflowRelease: 1.0，继续使用 workflowStudio: studio-v0.9 与 runtime_schema=xiaomo.studio-runtime/v1，不新增运行时字段。没有 release 的旧模板按原有标识识别为 0.9 或 0.8。未知、重复、冲突标识或与 manifest/配置/标准模板目录不符时拒绝启动，不能把缺失 release 的 V1 模板降级为旧版。

V1 的保存、恢复与六部门分工沿用下方工作室协议，JSON 的 workflowVersion 明确为 1.0，launchPromptVersion=4；launchPrompt 为一个完整六岗位启动包，同时返回 B/C1/C2/D/E 的 standbyPrompts 与六部门 roleTitles。创建项目或准备提示词不等于创建任务；用户发送启动包是本次批量建岗请求，已有项目也可只补缺失岗位。图片生成/编辑只在用户本次明确授权范围内，按共同契约“V1 原生图片工具”执行；建档与建岗都不授权生图，media_execution 及其他媒体边界不变。


脚本以模板“开始这里.md”的 workflowStudio 标识识别 V0.9，再验证工作室能力.json。隔离演练可保留 workset_runtime/adoption_transactions=false/false；stage=active 必须同为 true 且 runtime_schema=xiaomo.studio-runtime/v1。当前媒体能力始终为 false；能力配对不一致、错误 schema、未知标识或缺失 V0.9 标识必须失败，禁止降级为旧单岗位启动。

V0.9（无 workflowRelease: 1.0）：保留六个根 Markdown（开始这里及 A/B/C/D/E），另建能力文件与 C1/C2 工作稿入口。JSON 返回六部门短启动词、哈希与原 launchPrompt；能力 stage=active 才交付活动入口，其他阶段保留隔离候选范围。首个任务按 A 统筹入口开始，按需进入专业，不预建六个待命任务、不调用旧同岗缓存。V1 的建岗入口不回写或迁移旧项目。只有明确接替同一项目内同一工作集时，才通过 PrepareHandoff/ActivateHandoff/CancelHandoff 办理接管；空的岗位待命任务不使用工作集交接。

V0.9 从已有讨论建立新项目：仅在用户明确要求承接时，由当前前台按 [创作理由随稿交接](../orchestrate-ai-film-project/references/studio-v09-contract.md#创作理由随稿交接) 整理已提供或精确指定的必要来源内容，与脚本返回的完整 launchPrompt 分开并列交付；不把上下文塞进脚本 JSON 或改写启动词。接收专业在目标现有工作稿中保存必要可读内容，保留来源与状态；建档 Skill 不代写剧本或自动迁移旧项目。纯新建不增加资料包、旧项目读取或背景追问，任务创建仍需用户明确要求。

V0.8：保留五岗位原行为。未命中 V0.9 且通过旧模板校验时，在创建或复用岗位任务前必须读取 [旧版启动完整规则](references/legacy-v08-start.md)，按其中的项目匹配、重复任务与首轮拓扑规则执行；存档内的脚本相对路径按本 Skill 根目录解析。新分支不加载这份旧规则。JSON 包含原五段启动词哈希、B/C/D/E 待命消息与 launchPrompt；用户把启动包发送到新项目首个任务后，当前任务成为 A，按启动包创建或复用四个本地待命任务。首次只建立拓扑，下一条消息进入业务。不得向这个旧分支添加 V0.9 契约或能力。

## 新项目素材与工作资料目录

仅 V0.9 模板可声明媒体布局。唯一 `workflowMediaLayout: media-layout-v2` 使用 [v2 目录定义](references/media-layout-v2.json) 校验并复制 16 个固定目录：一级 `01_图片`、`02_视频`、`03_音频`、`04_导出`；图片固定含 `人物`、`场景`、`道具`、`镜头图`、`封面`，视频含 `Aroll`、`Broll`、`其他素材`，音频含 `音效`、`BGM`，导出含 `小样`、`成片`。已部署 `media-layout-v1` 继续使用原 [v1 定义](references/media-layout-v1.json)：11 个固定目录，五类图片目录按需再建，不预建。

唯一 `workflowDocsLayout: production-docs-v1` 按 [工作资料共享定义](../orchestrate-ai-film-project/references/studio-project-layout-v1.json) 把 13 类工作资料整组置于 `90_制作资料/`；工作稿及 C1/C2 入口路径随之映射。`开始这里.md`、A/B/C/D/E 主文件、`工作室能力.json` 与 `.工作室/` 仍在项目根目录。无该标识保持原工作资料位置，不根据目录存在猜测布局。JSON 另返回 `docsLayout` 与相对目录 `productionDocsDirectory`。

启用任一媒体布局标识的新项目文件夹为 `yyyy-MM-dd_片名_vN`，日期在创建时按本地时间一次捕获；同片名的版本号同时识别旧无日期目录和跨日期目录的历史最大版本，避免跨天重置。JSON 另返回实际 `mediaLayout` 与 `creationDate`。没有媒体标识的 V0.9 和 V0.8 模板保留原媒体目录及 `片名_vN` 命名。已有项目不改名、不搬运、不补目录；这里只创建新项目，不执行素材整理。

未知或重复布局标识、非 V0.9 模板声明布局、配置缺失或不符、实际模板目录不匹配、路径越界或重解析点均失败关闭，不静默回退。新模板采用媒体 v2 与工作资料 v1；标识不会改变 `workflowStudio: studio-v0.9`、任务创建规则或媒体执行能力。

## 结果与失败

成功后给出 JSON 返回的项目绝对路径及完整 launchPrompt，原样放在一个代码块，不凭记忆重写、不拆成多份，也不改为单任务统管六岗位。角色完整启动词保存在项目入口，departmentPrompts 保留兼容信息；V1 首次批量启动使用轻量 standbyPrompts。调用 get_role_launch.ps1 的 success 只代表启动包准备成功，tasksCreated=false；必须以真实任务回执分别报告创建和待命。

非零退出只报告精确原因；不手工补半个项目，不覆盖目录。项目名必须合法、无自带版本后缀；版本号为历史最大值加一。复制和身份写入先在受限临时目录完成，验证源模板未改后再落盘；只可清理本次 .creating-* 临时目录。-NoOpen 时不得打开用户 UI。

## 验证

对 V1、v0.9 与 v0.8 分别在隔离 -ProjectsRoot、明确 -TemplateRoot、-NoOpen 下创建同名 v1/v2；另验配置、无配置源码发现、显式 -WorkflowRoot、清单与模板不一致、缺模板失败无残留及根目录可移动。布局另验无日期旧名与跨日期版本递增、未知/重复/跨版本标识失败、共享配置缺失或不匹配、v2 的 16 个固定目录与 v1 图片目录不预建、13 类工作资料与 C1/C2 入口映射、旧无布局目录兼容及重解析点拒绝。另验未知/重复/冲突 release、V1 漏标识、manifest/schema 不配套及重复目标拒绝；验证版本递增、身份与提示词哈希、源模板不变、便携相对入口、无旧租约误路由及能力失败关闭。Windows 脚本保持 UTF-8 BOM；V0.9 启动测试只证明入口和能力路由；并发、采用及恢复由运行时专项测试证明，真实媒体仍待外部验证。

本次启动包修订另验证：新建与已有 V1 项目调用得到相同 launchPrompt；五段短待命消息分别完整可用；已有项目调用前后所有文件及目录不变；错版本、重复标识、缺入口、能力不匹配拒绝；v0.9/v0.8 启动输出保持原行为。任务工具缺失时不得宣称创建成功；真实侧栏创建、同项目复用和防重复另做工具层验收。
