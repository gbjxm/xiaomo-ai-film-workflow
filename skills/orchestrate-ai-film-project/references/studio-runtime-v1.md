# V0.9 工作集、采用与恢复

本文件给执行岗位使用，用户不用填写 JSON 或管理 revision/lease。产品 V1 沿用本协议；入口可另声明唯一 `workflowRelease: 1.0`，缺省为旧发布分支，未知或重复值拒绝执行。先核对项目入口和能力：studio-v0.9、runtime_schema=xiaomo.studio-runtime/v1、workset_runtime=true；采用另要求 adoption_transactions=true。早期 false/false 项目仍只能做隔离候选设计。当前运行时只处理文本记录，不调用生成、剪辑、上传或发布工具。

## 项目资料路径

入口声明 `workflowDocsLayout: production-docs-v1` 时，工作稿在 `90_制作资料/工作稿/<部门>/<key>/`，缓存在 `90_制作资料/续接缓存/<key>.json`；其他业务资料按 [制作资料路径约定](studio-project-layout-v1.md) 使用实际路径。下文未带前缀的工作稿/缓存示例是旧布局写法，在新布局须按该约定替换；状态、索引、事务和恢复备份仍位于根 `.工作室/`。Inspect 返回可直接读取的真实路径；Adopt/Resolve 的 path、source、dependencies 均使用真实项目相对路径，不隐式改写输入或历史采用键。

## 日常动作

从当前任务直接调用本 Skill 的 scripts/studio_runtime.ps1 或同目录 Python CLI，不切换 A 聊天，不调用旧 same_role_resume.ps1。

PowerShell 参数为 -ProjectRoot、-Action、-RequestFile；Python 对应 --project-root、--action、--request-file。请求文件由执行岗位根据当前材料生成并保留，编码 UTF-8；JSON 内业务路径均为项目相对路径，命令行项目根为已核对绝对路径。检查返回的 ok、status 和阻断详情，不能仅凭退出或文件存在宣称完成。

| 用户意图 | 执行岗位动作 | 返回后处理 |
| --- | --- | --- |
| 新设计一个角色/片段 | Create，为该内容选择唯一运行键，当前任务 ID 作 holder | 保存实际 revision/lease；该键不是影视 ID |
| 查看或恢复当前工作 | Inspect；不知道键时先 List 元数据 | 只读 required_read_set、完整稿、依赖差量和下一步，不读旧聊天/维护 HANDOFF |
| 给出首稿或局部修改 | 读当前可编辑稿，再 SaveDraft 完整稿与精确基线 | 保留请求与原稿，用户手改不得覆盖；保存返回会话值 |
| 采用整案或点名部分 | 所属专业准备精确文本和 owner，再 Adopt | 完整采用且视图同步完成才报告全部保存，外部执行仍未发生 |
| 保存中断或视图待同步 | Inspect 后 ResumeSync | 复用原采用证据与不可变内容，不再次索要相同授权 |
| 取消尚未提交的采用准备 | CancelAdoption | 已发布的采用不能直接取消，需要新候选/新版本 |
| 使用已采用资产或镜头记录 | Resolve，给 paths 或 transaction_id；明确需要全表才给 all=true | 消费返回的不可变 snapshot/sha/version，不从半套视图推断事实 |

保存、恢复与跨工作内容承接时，执行共同契约的 [创作理由随稿交接](studio-v09-contract.md#创作理由随稿交接)，复用现有正文、metadata 和 dependencies；不增加接口字段或独立写入动作。

Resolve 不给选择器时只返回总数概览；paths、transaction_id、all=true 三种选择互斥，避免恢复时无意读入全部对象。

使用实际返回值，不猜 revision。用结构化序列化写请求，不拼接 shell 字符串或把多行提示词塞进命令行。

## 字段最小约定

- Create：workset_key、department（A/B/C1/C2/D/E）、title、holder；可选 ttl_seconds。
- 写操作：workset_key、holder、lease_id、expected_revision。
- SaveDraft：会话字段加 content、expected_draft_sha256（首次 null）、dependencies、next_step；metadata 保存参考职责、不变量和取舍。只存用户可见完整稿，不保存聊天、隐藏推理或媒体编码。
- dependency：kind=file 给 path/sha256；kind=adopted 给逻辑 path/sha256/version。最新采用变更会被报告，不能静默切新版。明确选择历史已采用版时，可把其不可变 snapshot 当 file 依赖，并在 metadata 记录选择理由。
- Adopt：会话字段加唯一 transaction_id、draft_sha256、adoption_evidence{text,source}、writes；每项含 owner/path/source/source_sha256/expected_sha256/expected_version，新目标两个 expected 都为 null。源和目标只接受 .md/.txt/.json。
- 主要影视对象由所属专业显式给 object_id：B认领S、C1认领A、C2认领V、D认领CUT、E认领PUB。引用其他对象不算认领。运行时不分配编号；同号不能指向不同对象，重号由所属专业修正，不把编号冲突转为用户开权限。

采用默认指向用户刚审阅的精确整稿；局部采用只包括点名范围。所有者必须实际审核专业内容，代码检查白名单及证据字段不等于验证创作质量或用户授权语义。

## 一致性与阅读来源

完整候选先写不可变快照，再更新工作集指针和当前稿；缓存可删。.工作室/工作集/<key>/state.json 保存运行登记，不能证明采用。当前稿被用户手动保存修改时，Inspect 同时返回实际视图和保存快照；先读取、合并、重存，不用旧 hash 覆盖。

采用时先准备所有专业文本及 manifest，再通过 .工作室/采用索引.json 一次发布全部对象。索引和所引用的不可变内容是采用事实。普通专业 Markdown、.工作室/采用索引.md 与缓存是可重建视图；C 总文件链接运行时索引，不由 C1/C2 互相覆盖。

- prepared：内容已准备但尚未采用，继续读取旧采用索引。
- committed_sync_pending：整包快照已同时采用，但可读视图待同步；如实指出未完成项，运行 ResumeSync。
- committed 或完整同步结果：根据返回 receipt 和实际投影判断全部完成。
- 历史事务已被新版本替代：可读历史，不能把旧投影写回当前对象；完全被替代的旧事务不得重新挂起当前工作集。
- 已同步视图后来被手改或回退，不视为待补写的旧基线；未完成视图只有内容和提交时文件身份都仍匹配时才自动续写。视图完成状态逐项登记，进程中断后仍保留外部修改。

跨对象发布持短索引锁，每个工作集单独持租约。不同片段可并行；同对象/稳定编号冲突停止该次提交，保留候选和原有采用内容。依赖失效只沿实际引用比较，其他工作不会被整个 C 岗锁住。

## 换聊天和中断

用户明确要求换任务继续同一工作集时，旧任务先按 [创作理由随稿交接](studio-v09-contract.md#创作理由随稿交接) 核对并 SaveDraft 当前完整稿，再 PrepareHandoff（user_switch_evidence）。新任务真实创建成功才 ActivateHandoff（handoff_id、target_holder），失败则 CancelHandoff。这组接口只转移同一项目内同一工作集；跨部门新工作集或跨项目承接按共同契约另行传递内容，不用旧租约替代。脚本不创建 Codex 任务、不修改模型配置。

手动新任务先 Inspect 再 Claim。旧租约未过期时，必须同时有用户切换证据、previous_holder_stopped=true 和 stop_evidence 才接替；无法确认停止则走旧任务的准备/激活。成功后旧 token 失效，只影响本工作集。

Claim 或下一次合法写入根据保存状态修复缺失/未完成的当前稿和缓存，未知手改不会被重建覆盖。请求文件、不可变快照和失败回执保留，恢复不需复制完整旧聊天。

## 迁移前的采用事实

运行时的 Resolve 只返回已登记的事务对象。维护迁移可以基于既有准确采用证据，把原字节、原版本及原稳定编号映射保真导入，另存导入回执；这不代表用户重新采用创作。仍在项目入口明确兼容基线中的旧输入，读取其精确快照和 file 依赖。新创作的采用继续经 Adopt/Resolve，不通过修改旧基线绕过用户决定或所有权。

## 约束

纯读取不创建文件，仅核验当前显式依赖，不扫描素材树。运行时拒绝项目内路径穿越、ADS、设备名、大小写别名冲突与 reparse/Junction。项目根解析后的目录是边界，根本身不能为 reparse。

文本事务不改 Word、图像、音视频或工程二进制；它们通过原专业流程的已保存版本作为依赖引用。隔离版本不代表真实项目已经迁移。并发保护面向遵守运行时的任务；用户直接保存的外部修改通过 hash 检出并保留，不虚称锁住外部软件。


## Windows 编辑保护与恢复备份

既有可编辑/专业视图的替换在 Windows 下用禁止写入和删除共享的文件句柄重新核对字节，按该句柄保留原件，然后以不覆盖方式发布新文件；新目标也不覆盖竞争期间出现的文件。原件保存在 .工作室/恢复备份，当前不自动清理。文件正被编辑器占用时可以返回待同步，不声称已经写入。

可读视图可能短暂缺失，或在进程退出后待重建；采用索引/state仍原子更新，消费者读取已存在的不可变快照。对无法建立安全替换的非Windows环境，拒绝覆盖已有可编辑视图。此机制不声称阻止所有外部应用操作，只保证本操作不通过覆盖来丢弃其间已保存的新文件。

实现依据：[CreateFileW共享模式](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)、[按句柄修改文件信息](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle)。验证以本批Windows并发、重建目标和进程硬退出试验为准，不以文档存在代替测试。
