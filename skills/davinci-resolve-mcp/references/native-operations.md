# 原生接口约束

只在执行对应操作时读取。以下名称来自当前受限工具集；参数最终以已连接 MCP 的 `inputSchema` 为准。

## 最小读取链

```text
project_manager_get_current_project() -> 当前工程 id/name
project_get_current_timeline() -> 当前时间线 id/name
timeline_get_item_list_in_track(timeline_id, track_type, track_index) -> 时间线 item_id
timeline_item_get_media_pool_item(item_id) -> 对应媒体池对象
timeline_item_get_node_graph(item_id) -> graph_id
```

前两个调用不接收 `project_id`。媒体池相关接口也多从当前工程取对象，不能照搬上游工作流示例里不存在的 `mp_id` 参数。

`track_index` 和节点 `node_index` 按 API 为 1 起始；源帧、时间线起始帧、recordFrame 不能混用。时间线默认起点可能不是 0。导入片段后以实际时长和首尾帧验证源区间，不能把“秒数”直接当帧号，也不假定所有 end 参数都有相同包含规则。

## 素材导入与基础编辑

- `media_pool_import_media(items)` 接受明确的本地绝对路径或图片序列描述；来源仍为原文件引用，不应重新编码或覆盖原片。
- `media_pool_append_to_timeline` 使用 `clip_ids` 或 `clip_infos` 二选一。`clip_infos` 的 `media_pool_item_id` 必须由真实媒体池对象取得，配合实际的源帧区间、轨道和 recordFrame。
- 新建、复制、片段删除、轨道启停等都是工程写入；执行前核对当前目标与可恢复基线，完成后检查数量、时长、轨道、源引用、衔接和声音。
- `timeline_item_set_property` 只支持实际 SDK 属性。不要把重定时质量参数当作速度修改，也不要把不存在的色轮/HSL 参数塞入通用属性方法。
- 纯接口调用不决定哪个表演更好或哪个片段应删，不自动运行静音剪辑、AI 分析或固定时长组接。

## 基础调色接口

| 接口 | 实际范围 | 关键限制 |
|---|---|---|
| `timeline_item_set_cdl` | 现有节点的 Slope、Offset、Power、Saturation | node_index 使用字符串；RGB 为三个空格分隔的有限数值；不是完整局部调色 |
| `graph_set_lut` | 给现有节点使用已发现的 `.cube` | 本策略要求已有本地绝对 `.cube` 路径；先核对输入输出与烘焙状态，禁止 DCTL 替代 |
| `graph_apply_grade_from_drx` | 应用已有 DRX 调色 | 替换目标节点图，不是追加；先保留工程/颜色版本，不能声称自动适配窗口或跟踪 |
| `timeline_item_copy_grades` | 从来源复制调色到目标 | 会影响目标已有调色；先确认目标列表和恢复点，再核验实际画面 |
| 颜色版本/组、节点启停 | 管理已存在的调色结构 | 不是从参数创建任意曲线、窗口或完整节点树的接口 |

不得把已有烘焙调色视频导入成功说成“调色恢复可编辑”。CDL/LUT/DRX 的具体配方与使用判断由专业调色任务提供；本接口只负责正确目标、参数、路径和可观察结果。

本首批服务未开放 Gallery、任意 Fusion 工程写入和 DRX 格式逆向工具。需要局部窗口、动态跟踪或复杂效果时，先核对已验证实现，不能通过任意脚本接口绕过当前范围。

## 原生调色预览

按明确调色任务，可用 `resolve_open_page(page_name="color")` 进入软件内部 Color 页；策略只允许该值，其他页面仍拒绝。它调用官方 OpenPage，不激活系统窗口或模拟键鼠。不要把原生内部页面切换与抢占系统焦点混为一谈。预览时核对当前工程/时间线，读取现有缩略图；不能为取图而导出未获授权的文件或改时间码。缩略图是片段级预览，不等于精确逐帧或动态审看。

## 保存与导出

- `project_manager_save_project()` 在服务端先读取同一个 ProjectManager 的当前工程与 `GetProjectListInCurrentFolder()`。当前名称不在已保存清单时拒绝调用 SaveProject，避免未保存默认工程弹出命名框。先按已获授权创建有名工程；真正登记为 `Untitled Project` 的工程不因字面名称被拒绝。
- 保存前会再次核对当前名称，发现切换即拒绝；SDK 没有原子的“只保存同一 ID”接口，因此不要并发切换或写入工程。
- `project_manager_export_project` 要求明确的非空工程名及本地绝对文件路径。DRP 是工程导出，不是成片视频。
- 时间线、LUT、静帧导出也各自是文件写入；封装没有隐式 SaveProject 不代表所有原生软件条件均已验证。

## 本地渲染

先查询当前可用格式、编码器和分辨率，再按本次输出目标设置。不要沿用上游示例的固定 H.265、分辨率或帧率。`project_set_render_settings` 的字段是工具参数，不要额外包在不存在的 `project_id` 或 `settings` 层中。

```text
project_set_current_render_format_and_codec(format, codec)
project_set_render_settings(TargetDir=本地绝对目录, CustomName=当前版本名, ...)
project_add_render_job() -> 本次 job_id
project_start_rendering(job_ids=[本次 job_id], is_interactive_mode=false)
project_get_render_job_status(job_id=本次 job_id)
```

服务器拒绝省略 job_ids 渲染整条队列、交互渲染错误框和 `ReplaceExistingFilesInPlace=true`。Quick Export 不开放，避免与平台上传混为一谈。轮询只读取本次 job，不能停止其他人的任务。

文件存在、任务完成、完整解码、颜色标签、首尾画面/动态审看及保存重开是不同证据。按改动范围验证，尚未做的检查保持未验证，不靠工具数量或状态码推断质量。
