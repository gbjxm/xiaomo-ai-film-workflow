# B 故事阶段完成契约：B_STAGE_READY v1

本契约是 A 与 B 判断“故事阶段是否可以只交接一次并由 A 直接派 C”的唯一共享标准。B 负责专业判断与逐项自检；A 只做本契约规定的完整性验收，不另建影子清单，也不重新逐场审对白、人物、因果或其他创作细节。启用外部 Word 时，版本谱系与协作细则另见 [Word 协作生命周期](../../develop-screenplay-from-idea/references/word-collaboration-lifecycle.md)。

`B_STAGE_READY` 不是用户创作采用、文件写入授权、制作释放或外部动作授权本身。它只记录这些独立决定及其证据是否已经齐全并互相一致。

## 结果语义

- `NOT_PASS`：任一必填项、专业自检、精确版本/hash、用户决定、制作释放、Sxx 映射、任务包络或当前阶段阻塞不满足。任务留在原 B；不生成外部阶段完成包，不提交正常 B→A 阶段交接，也不派 C。
- `PASS`：本契约全部必填项成立，释放到制作的精确范围已分配或升版 Sxx，且没有阻止该范围进入 C 的未决项。B 定稿后按 [B 阶段完成包 JSON 模板](../assets/b-stage-ready-packet-template.json) 生成一次不可变外部完成包；A 完整性验收通过后直接派 C。
- 机械缺项的后台刷新沿用同一个 `handoff_id`，增加 `handoff_revision`，并使用目标目录中下一个未占用的 `剧本/阶段完成包/B阶段完成包_vNN.json`；目标已存在即停止，绝不覆盖旧包，也不算新的阶段交接。若刷新需要新的专业判断、用户决定或任务边界变化，原结果立即变为 `NOT_PASS`。

`NOT_PASS` 只存在于 B 的内部自检状态，不生成阶段完成包。正式 JSON 完成包因此只允许 `result: PASS`，专业分类在 PASS 包中只允许 `PASS` 或有理由的 `N/A`；任何 `NOT_PASS` 项都必须留在 B。

## 四个独立决定门

| 决定门 | PASS 所需证据 | 明确不代表 |
| --- | --- | --- |
| 用户创作采用 | 用户采用精确故事载体、版本/hash 与范围的可定位证据 | 不代表允许 B 写文件、分配 Sxx、派 C 或执行外部动作 |
| B 记录写入 | 用户允许 B 把上述采用决定写入 `B_故事剧本.md`，并在定稿后生成 `剧本/阶段完成包/B阶段完成包_vNN.json` 的可定位证据；包含 B 写后路径、版本/锚点和 SHA-256 | 不代表进入制作或允许 A/C 执行外部动作 |
| 制作释放 / Sxx | 用户明确让精确采用范围进入制作的可定位证据；B 已为该范围分配或升版 Sxx，并写清制作边界 | 只允许登记 Sxx、同步必要 A 指针并在 A 完整性通过后派 C；不代表 TTS、图片/视频/声音生成、剪辑、导出、封面、上传或发布 |
| 外部动作授权 | 按动作类别分别记录 `未授权` 或精确授权证据、范围和限制 | `PASS`、创作采用、B 写入、制作释放或已有 Sxx 均不能替代任何外部动作授权 |

同一条用户消息可以同时承载多个决定，但每个决定必须分别可定位且语义明确。只说“采用”“可以”“就这样”时，不得自动推断 B 写入、制作释放或外部动作授权。

## B 的专业自检

B 必须先完成详细自检，才可填写 `result: PASS`。自检可以记录在 B 主文件的精确区段或其已登记证据中；交接包只保存路径、版本/hash、锚点和结论，不复制整份剧本。每项使用 `PASS`、`NOT_PASS` 或 `N/A + 原因`，不得用空白、`大致没问题` 或待补说明冒充完成。

### 全剧层

1. 核心前提、观众承诺、世界/异常规则、代价与结局兑现互相一致。
2. 主角目标、动机、选择、人物弧光及关键关系结果完整，人物状态变化可追溯。
3. 事件因果、信息揭示、升级、转折、伏笔与回收成立；不存在会改变理解的断链。
4. 场次顺序、时间、空间、人物、道具与已建立事实连续；保留的例外有明确理由。
5. 对白具有人物区分、潜台词和可说性，不以重复说明替代戏剧行动。
6. 动作和反应可被看见或听见，场景功能、制作边界与必须保持的故事事实明确。
7. 目标时长、节奏、格式、场次编号、权威正文覆盖和 Sxx 释放范围满足 A 任务开始时声明的标准；计时方法、日期与真实结果可定位。
8. 已知权利、安全、隐私与合成内容风险已标出；不存在阻止本次释放范围进入 C 的未决风险。只能在后续里程碑解决的条件必须写明责任岗位和停止门。

### 逐场覆盖层

对本次释放范围内每个场次逐行记录：场次 ID/正文锚点、精确正文 hash 或可验证范围、场景功能、因果与转折、人物目标/反应、对白与动作、连续性、时长/节奏、制作边界，以及该行 `PASS | NOT_PASS | N/A + 原因`。`expected_scene_count` 必须等于 `checked_scene_count`，并能与最终 Sxx 映射逐项对应。

A 只检查上述类别和逐场行是否齐全、结果是否合法、覆盖数是否一致及证据能否定位；A 不重新判断 B 的专业结论是否“写得更好”。

## 固定交接字段

B 的固定顺序是：先把专业自检、采用/释放事实、Sxx 和最近交接摘要最终写入 `B_故事剧本.md`；关闭并计算 B 写后 SHA-256；再从模板新建不可变的 `剧本/阶段完成包/B阶段完成包_vNN.json`，让完成包单向引用 B hash；关闭并计算完成包 SHA-256；最后在 B 任务交接消息中只提交完成包路径、版本、hash 与 `handoff_id/revision`。严禁把完成包或完成包 hash 回写 B，也不得把完成包自身 hash 写进完成包。

A 的审查对象是这个外部完成包。完成包使用 [B 阶段完成包 JSON 模板](../assets/b-stage-ready-packet-template.json)，结构、类型与枚举由 [固定 Schema](../assets/b-stage-ready-packet.schema.json) 约束；字段或语义冲突时以本契约为准。包必须精确符合固定字段，长值可用项目内相对路径与锚点，不得用模糊摘要替代版本/hash。

JSON 模板是待填写 scaffold，故意保留 `vNN`、`Sxx`、hash/时间占位符和 0 场次数，本身必须被验证器拒绝；B 必须复制到下一个未占用真实版本、替换全部占位符并填入大于 0 且相等的场次数。模板不是一个可直接提交的 PASS 示例。

```yaml
contract: B_STAGE_READY
contract_version: v1
packet_version: vNN
supersedes_packet: <首包为 null；机械刷新写 { path, packet_version, sha256, handoff_revision }>
handoff_id: <本次故事阶段唯一 ID>
handoff_revision: <从 1 递增；机械刷新不换 handoff_id>
result: PASS
checked_at: <带时区时间>
source_b_task_id: <原 B 任务>

a_task_envelope:
  a_task_id: <A 当前任务 ID>
  a_task_version: <精确版本>
  a_file_sha256: <B 自检时看到、A 验收登记前的 A_总控.md 基线 SHA-256；A 通过后写入自身会正常改变 hash，不反向使本包失效>
  target_role: B
  stage_goal: <完整故事阶段目标>
  input_scope: <输入及精确版本/hash>
  out_of_scope: <本次不做>
  writeback_scope: [B_故事剧本.md, 剧本/阶段完成包/B阶段完成包_vNN.json]
  external_action_boundary: <逐类授权观察值；默认均未授权>
  envelope_match: PASS

b_record:
  path: B_故事剧本.md
  version_or_anchor: <当前记录版本或唯一锚点>
  sha256: <写后当前 SHA-256>

authoritative_story:
  carrier: word | b_markdown | other_registered
  path: <权威正文精确路径>
  version_id: <精确版本>
  sha256: <原始文件 SHA-256>
  exact_scope: <本次采用并释放的唯一 Sxx ID 数组>
  word_lineage_status: released | not_applicable
  word_lineage_evidence: <Word/released 时为 { path, version_or_anchor, sha256 }；非 Word 时为 null>
  word_conflicts: none

professional_self_check:
  overall: PASS
  evidence_path: <B 或已登记证据路径>
  evidence_version_or_anchor: <精确版本或锚点>
  evidence_sha256: <文件 SHA-256>
  whole_story_categories: <8 项结果与证据定位>
  expected_scene_count: <大于 0 的整数>
  checked_scene_count: <与 expected_scene_count 相等且大于 0 的整数>
  scene_coverage_anchor: <逐场覆盖表锚点>

user_decisions:
  creative_adoption: { status: granted, exact_scope: <唯一 Sxx ID 数组>, artifact_version: <版本>, artifact_sha256: <hash>, evidence: <定位> }
  b_record_write: { status: granted, recorded_scope: <已写入 B 的唯一 Sxx ID 数组，必须覆盖 production_release.exact_scope>, evidence: <定位>, b_write_completed: true, packet_write_completed: true }
  production_release: { status: granted, exact_scope: <唯一 Sxx ID 数组>, evidence: <定位> }

released_sxx:
  - { id: Sxx, version: vNN, b_anchor: <锚点>, story_scope: <场次/范围>, production_boundary: <固定项与边界> }

rights_and_safety:
  stage_status: CLEAR
  current_blockers: []
  downstream_conditions: <无则写 none；否则写责任岗位与停止门>
  evidence: <定位>

external_actions:
  tts: <{ status: not_authorized, evidence: null, scope: [] } 或 { status: authorized, evidence: <定位>, scope: [<范围>] }>
  document_editing: <同上>
  image_video_audio_generation: <同上>
  editing_export_cover_upload_publish: <同上>

blocking_items: []
non_blocking_items: <无则写 []；否则逐项为 { description, owner, milestone }>
unique_next_step: A 按 B_STAGE_READY v1 做完整性验收；通过后直接派 C
```

B 的最终任务交接消息不得复制完整包，只传：`contract: B_STAGE_READY`、`handoff_id`、`handoff_revision`、`packet_path`、`packet_version`、`packet_sha256`。A 先重算包 hash，再读取包；指针不匹配时不验收内容。

使用外部 Word 时，`authoritative_story` 的版本谱系、冲突、采用和 `released` 证据必须符合 Word 协作生命周期；Word 文件存在、已保存、逐场确认或 `adopted` 均不能单独产生 `B_STAGE_READY PASS`。不使用 Word 时，`word_lineage_status` 写 `not_applicable` 并说明实际权威载体，不能伪造 Word 记录。

## PASS 的确定性条件

只有以下条件全部成立，B 才可输出 `PASS`：

1. 外部完成包按固定顺序生成且未被回写，字段齐全，`contract/version` 精确为 `B_STAGE_READY/v1`，任务包络与 A 当前 B 阶段一致。
2. B 记录和权威正文路径存在，版本/hash 为当前值；启用 Word 时无未解决冲突且谱系已到 `released`。
3. 专业自检总体及全部必需项通过，逐场覆盖数均大于 0 且一致；`N/A` 均有理由且不规避 A 预先声明的标准。校验器只检查计数与锚点，不重判锚点所指逐场专业结论。
4. 用户创作采用、B 记录写入和制作释放三项决定分别有精确证据；`production_release.exact_scope` 可以是 `creative_adoption.exact_scope` 的全部或明确子集，但不得超出它，且必须被 `b_record_write.recorded_scope` 覆盖；B 写入与完成包写入均已完成。
5. `released_sxx` 非空，逐项对应精确采用/释放范围，并满足当前 A 阶段目标，而非任意挑选局部场次冒充整阶段完成。
6. `blocking_items` 为空，`rights_and_safety.stage_status` 为 `CLEAR`；下游条件有明确责任岗位和停止门。
7. 外部动作逐类写明授权观察值；未授权是合法值，不妨碍派 C 做无需该动作的工作，但 C 执行对应动作前必须另验授权。

任何一项不满足都写 `NOT_PASS`，并由 B 在当前阶段内继续处理。B 不得为了赶交接把开放项改名为“非阻塞”，也不得让 A 代做专业判断。

## A 的完整性验收

A 先核对 B 最终任务消息中的外部完成包路径、版本/hash，并运行 `scripts/validate_b_stage_ready.ps1 -ProjectRoot <项目根> -PacketPath <包路径> -ExpectedPacketSha256 <交接 hash>`。该脚本按 [固定 Schema](../assets/b-stage-ready-packet.schema.json) 的机器可判字段只读返回 `valid/errors` JSON；只有 `valid: true` 才继续以下完整性登记，任何错误都保持待定并按错误定位刷新。逐场专业结论、用户证据真实性与任务包络创作语义仍由 A 按既有边界核对，脚本不重判：

1. 完成包路径/命名/版本、必填字段、合法枚举、覆盖数量和空值是否符合本契约，且包未包含自己的 SHA-256。
2. `A_总控.md`、B 记录、权威正文及自检证据的当前版本/hash 是否与交接一致。
3. `source_b_task_id`、A 任务 ID/版本、目标岗位、输入范围、不做事项、写回位置和阶段目标是否仍处于同一任务包络；`a_file_sha256` 只作为 A 验收登记前的基线核对，A 通过后写入包指针造成的新 A hash 不反向使本包失效。
4. 三项用户决定是否分别存在并能定位；制作释放范围是否属于创作采用范围且被 B 记录写入范围覆盖。A 不重解释用户的创作选择。
5. `released_sxx` 是否非空、格式完整、范围与采用/释放证据一致。
6. `blocking_items` 是否为空，权利/安全状态与下游停止门是否明确；`external_actions` 每一类观察值不得超出 `a_task_envelope.external_action_boundary`，未授权是合法值。

A 不得逐场重审对白、人物、因果、结构、节奏或创作质量，不得要求 B 再做本契约外的“保险检查”，也不得在交接时临时增加项目标准。项目特定的时长、格式或内容要求只有在 A 阶段任务开始时已写入任务包络，才能成为本次完成门；用户后续改变目标时按重大变化重建包络，不能倒算成 B 的漏项。

六项完整性检查通过后，A 在自己的文件中登记完成包路径/版本/hash、精确 B/权威正文/Sxx 指针并派 C；不得再让用户回 B 确认一次。A 的登记和派工只执行制作释放已覆盖的必要调度，不扩大任何外部动作授权。

## 机械缺项后台刷新

字段漏填、枚举/格式错误、证据定位漏写、覆盖计数或 Sxx 映射的机械不一致，以及完成包生成后发生但未改变内容决定的 hash 更新，走后台刷新：

1. A 保持当前交互前台，把精确缺项、当前预期 B/完成包 hash、同一 `handoff_id` 和当前 revision 发给 `source_b_task_id`；不要求用户切换岗位或复制交接。
2. 原 B 只核对已存在事实并补字段、重算 hash 或修正映射。若 B 主文件无需变化，且原完成包写入授权明确覆盖同一 `handoff_id` 的机械 successor 包，才可从旧包派生目标目录中下一个未占用的 `B阶段完成包_vNN`；授权未覆盖时停止等待补充授权。若需写 B，必须已有覆盖该精确记录的 B 写入授权并重新过写前 hash 门，再按“定稿 B→算 B hash→新建下一未占用完成包→算包 hash”重走固定顺序。目标存在即停止，旧完成包保留不改。
3. 原 B 沿用 `handoff_id`、增加 `handoff_revision`，只返回新包路径/版本/hash。A 从新包重跑六项完整性检查。相同机械缺陷重复出现、原 B 不唯一/不可验证或 revision/lease 冲突时停止，保持待定并报告真实阻塞，不猜测补全。

以下不属于机械刷新：补做专业自检、改变故事内容或释放范围、取得新的用户决定、分配新制作功能、处理权利/安全取舍、改变任务包络。遇到这些情况，结果回到 `NOT_PASS` 并留 B；达到既有重大变化条件时逻辑上回 A 重建任务，但不把“回 A”解释成强迫用户反复切换界面。

## 重大变化与安全边界

核心事件、人物动机、关系结果、制作功能、项目目标/里程碑、任务边界、权利/安全条件或外部动作类别变化，仍按重大变化回 A。`B_STAGE_READY` 不削弱岗位独占写入、写前 hash、Word 冲突/并发停止、权利隐私检查、真实媒体生成、剪辑、导出、封面、上传和发布的独立授权门。
