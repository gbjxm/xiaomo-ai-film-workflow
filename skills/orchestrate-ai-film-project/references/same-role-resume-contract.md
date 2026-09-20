# B/C/D 同岗续接契约 v1

本契约只解决同一项目、同一岗位在新 Codex 任务中的低成本续办。它不复制旧聊天，不替代 A/B/C/D 权威文件，也不扩大写入、生成、剪辑、导出或外部动作授权。

## 两个入口

### 旧任务自动切换

只有用户在旧 B/C/D 任务中明确说“切换到新的【岗位】同岗续接任务”或等义表达时才启用：

1. 旧任务把当前对象、已采用事实指针、待定差量、唯一下一步、授权观察值和精确读取集刷新进缓存。
2. 旧任务调用脚本 `PrepareHandoff`；准备状态不转移 lease，旧任务仍是唯一所有者。
3. 旧任务在同一个项目中创建同岗位新任务，只发送固定短启动词以及脚本返回的 `handoff_id`，不复制旧聊天。
4. 创建成功后旧任务调用 `ActivateHandoff`，把 lease 转给新任务。激活后旧任务立即停止项目与缓存写入，只可查看或汇报。
5. 创建失败或被用户取消时调用 `CancelHandoff`；旧任务保留 lease。未经成功激活，不得声称新任务已接管。

固定短启动词：

```text
【B/C/D】同岗续接。执行项目中的 same-role-resume-v1，领取最新【岗位】续接缓存；只读恢复，不写项目，不执行生成、剪辑、导出或其他外部动作。
```

### 用户手动新建任务

用户可在同一项目手动新建 B/C/D 任务并发送上述短启动词。新任务先 `Inspect`，再 `Claim`。若另一任务持有未过期 lease，默认停止；受控接替必须同时具备当前用户明确要求切换或续接的 `-UserSwitchRequest`，以及旧 holder 已停止写入的 `-PreviousHolderStopped` 证据，缺任一项都拒绝接替。无法确认旧 holder 已停止时，使用 `PrepareHandoff → ActivateHandoff`，不得把用户切换意图单独当作停写事实。无活动 lease 或 lease 已过期时按正常 Claim 处理，不要求这两个接替证据。成功接替会增加 revision 并使旧 lease 立即失效。

## 缓存位置与性质

每个兼容项目最多维护：

```text
续接缓存/B_同岗续接.json
续接缓存/C_同岗续接.json
续接缓存/D_同岗续接.json
```

- 单份 JSON 必须不超过 6144 个 UTF-8 字节；超限拒绝写入，不截断。
- 缓存是非权威、可删除、可重建的运行缓存。正式事实仍以 `A_总控.md`、岗位主文件、稳定 ID 卡、真实媒体、CUT/组件/Master 及其 hash 为准。
- `adopted` 只保存正式采用事实的短指针与来源；`pending` 单独保存未采用草稿、未答问题和差量。脚本不允许把 `pending` 自动并入 `adopted`。
- `authority.cache_is_authority` 与 `authority.resume_validation_grants_write` 永远为 `false`。缓存中记录的授权只是一条带来源的观察值；每次正式动作仍按原岗位契约重新核验。
- JSON 字段类型必须稳定：即使只有一项，工作集与 `required/optional/skip` 等数组仍保存为数组；`updated_at_utc`、lease 与 handoff 的时间统一保存为带 `Z` 的 UTC ISO8601 字符串，不能受 PowerShell 版本或本机时区影响。
- 不保存完整聊天、隐藏推理、图片/音频编码、长正文、整份剧本、整份资产清单或整个时间线。长草稿只保存项目内路径、版本和 SHA-256。

## 最小模式

缓存固定包含：

- `schema`、`role`、`revision`、更新时间；
- `source_task`：写入或交接该缓存的任务 ID；
- `gate.a_task`：A 当前任务 ID、目标岗位、阶段包络、权限边界，以及 `A_总控.md` 当前任务 H2 区段或整文件 hash；
- `cursor`：当前执行游标，例如 B 的活动 Sxx/开发层面、C 的活动 Sxx/功能站/工作集、D 的活动后期方案/CUT/组件/原子动作；
- `adopted` 与 `pending`；
- `authority.observed`：当前观察到的写入与外部动作授权及证据定位；
- `read_sets.required`、`read_sets.optional`、`read_sets.skip`；
- `next_step`：唯一下一步；
- `lease` 与可选 `handoff`。

`required` 与 `optional` 条目只允许项目根内相对路径，并使用以下一种 hash：

- `whole`：文件原始字节的 SHA-256；
- `markdown_h2`：持久字段 `section` 统一只写裸标题（如 `当前任务`）；读取时兼容旧输入带可选 `## ` 前缀。从精确 `## 标题` 行开始，到下一个 `##` 标题前为止；换行统一为 LF，去掉区段末尾空换行后补一个 LF，再以无 BOM UTF-8 计算 SHA-256。脚本每次成功写缓存时会把兼容输入规范化为裸标题。
- `markdown_line`：`selector` 是一个非空字面子串；文件中必须恰好只有一行包含它。该行换行统一为 LF 并补一个 LF，再以无 BOM UTF-8 计算 SHA-256。零行或多行命中都判为失效，不猜测目标。它用于只绑定一个 `Sxx/Axx/Vxx/CUTxx` 索引行，避免为了验证单一对象而把整张索引表加载给模型。

`skip` 是启动禁止读取的路径或语义标签。脚本不打开、不计算其中内容。路径不得通过绝对路径、`..`、盘符、符号链接或 Junction 越出项目根。

## 读取与失效

- `required` 是恢复当前唯一下一步所必需的最小集合。有效缓存只向模型加载这些区段/文件，不把 A 阶段包络展开成读取列表。
- `optional` 只在当前动作明确需要时加载。Inspect 可在模型外计算 hash；可选项变化只报告，不自动扩大读取。
- `skip` 启动时始终为零读取，包括完整旧聊天、无关 Sxx、整份 Word、完整候选资产树和全部媒体等岗位对应禁区。
- A 当前任务 ID、目标岗位、阶段范围、权限边界或其绑定 hash 改变时，A 门失效并停止续接；返回 A 重新绑定，不搜索磁盘猜测新任务。
- `required` 缺失或 hash 改变时只恢复该条目或其 H2 区段，并沿显式依赖更新；不得退化为全项目扫描。
- `optional` 改变不阻塞冷启动，只有本轮动作使用它时才局部恢复。
- 缓存缺失或损坏时，从 `开始这里.md` 的共享契约、`A_总控.md` 的 `## 当前任务`、对应岗位主文件的当前状态/最近交接区段开始恢复。仍不充分时可读取上一同岗任务最多两条完成摘要；不读取旧任务的长工具输出或完整历史。

## Revision 与 lease

- 每次缓存写入都在同目录锁内比较 `revision`，成功后递增并原子替换；调用方必须传入刚刚 Inspect/Claim 得到的期望 revision。序列化、大小检查或原子替换失败时保留原缓存，不把临时文件当成新状态。
- lease 只保护该岗位缓存及岗位正式写入的并发所有权，不表示内容采用或动作授权。活动任务在缓存更新时续期。
- 新任务 Claim、具备用户切换与旧 holder 已停写双证据的手动受控接替，或 ActivateHandoff 后，旧 lease 立即失效。旧任务在任何岗位文件写入前发现 lease/revision 不符，必须零写入停止。
- `PrepareHandoff` 期间旧任务仍持有 lease，但除刷新缓存、激活或取消交接外不再推进正式工作。`ActivateHandoff` 必须匹配 handoff ID、旧 lease 和 expected revision。
- 不得用强制覆盖、删除缓存或延长过期 lease 绕过并发冲突；异常时重新 Inspect 并按真实当前状态处理。

## 脚本模式

脚本：`scripts/same_role_resume.ps1`

- `Inspect`：只读检查大小、schema、A 门、路径与 whole/H2 hash；输出有效必读集、局部变化和停止原因。模板预置的合法 revision 0 空骨架明确返回 `status: uninitialized` 与 `initialization_required: true`，不把它误报为损坏或可续接。
- `Initialize`：从短 JSON 快照建立首份缓存；若模板预置的是同 schema/role、`revision: 0`、两项 authority 均为 false、无活动 lease 且无 handoff 的空骨架，可原子替换为 revision 1；任何其他已存在缓存都停止，不能覆盖。
- `Claim`：新任务领取或续期 lease；无活动或已过期 lease 时正常领取。未过期的他人 lease 只有 `-UserSwitchRequest` 与 `-PreviousHolderStopped` 同时提供才可接替，缺任一均失败。
- `PrepareHandoff`：旧任务刷新可选 patch 并建立准备态；必须带明确用户切换证据开关。
- `ActivateHandoff`：新任务已成功创建后，旧任务把 lease 转给目标任务。
- `CancelHandoff`：创建失败或取消时撤销准备态，旧任务继续持有 lease。
- `Update`：当前 lease 所有者写入短 patch；不能改 schema、role、revision、lease、handoff 或缓存权威边界。

脚本输出的 `resume_ready`、`lease_valid` 或 hash 一致，只说明缓存可用于低成本恢复。它永远不输出“已获项目写入/生成/剪辑授权”的结论。
