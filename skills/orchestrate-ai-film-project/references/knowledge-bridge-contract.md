# KNOWLEDGE_BRIDGE v1

## 何时使用

用户要求“结合知识树/相关知识检查”当前影视材料，或岗位判断需要新依据、跨阶段知识或缺口说明时，由当前岗位调用知识桥。用户无需填写模式、文章名或检索词；常规岗位方法继续由岗位Skill执行。

材料审查用 `scripts/retrieve_workflow_knowledge.py --review`；单一明确问题可用普通快查。同一对象、材料版本、问题与知识证据未变化时复用已有阅读；版本和反馈变化后只重开受影响判断及依赖。

## 从材料到判断

按知识调用Skill的 `references/application-contract.md` 执行主动审查：先区分目标、事实、用户体验与假设；主动发现影响目标的问题；按原因及反证需要寻找知识；库内不足、冲突、需原始出处或现行规则时可外查原始来源；把实际取舍落到当前材料，原方案合理时可不改；后续仅复核变化影响。不固定要求大表、两方案或重新检查每个岗位。

使用重复的 `--question` 表达必要专业问题，`--query` 表达总目标，`--material` 提供当前必要摘录。对象、限制和权限说明不混入语义查询。

## 输入与配置

必填岗位、阶段、具体问题；能确定时传任务类型、精确对象、限制和期望输出。快查与review均转交 `--task-type`、`--object`、`--constraints`、`--expected-output`；review把这四项保留到 `context`，不参与检索词计算。

```powershell
python -B -X utf8 scripts/retrieve_workflow_knowledge.py `
  --review --query "<当前材料的审查目标>" --role C --stage "生成与镜头" `
  --question "<从材料发现的必要专业问题>" --material "<必要事实摘录>" `
  --task-type "连续性检查" --object "S04/V03" `
  --constraints "只读，不改剧本事实" --expected-output "判断与实际取舍"
```

唯一入口是工作流本机配置的 `knowledgeBridge`：启用状态、知识树配置绝对路径和知识调用Skill名称。工作流安装器负责验证；岗位Skill不复制Vault盘符，wrapper保持UTF-8并禁止Python字节码写入。

## 消费证据

按问题读取 `evidence_ids`、`support_ids`、`boundary_ids`，不足时检查有理由的 `alternative_ids`。这些是技术候选，不代表语义支持。表格行同时读取 `context_ids` 的表头、单位与限定，并保留 `parent_id` 以便定位。

`truncated/read_required` 为真时按 `continuation` 继续或用知识Skill的 `knowledge_review.py --read-id --snapshot` 读完整段落；旧ID失效时重取受影响证据。快查卡不足时也可按需深读，不设单章节硬上限。路径存在、来源简介、字段齐全和退出码0不代表关键方法已读。

判断连接当前观察、实际知识、来源定位和适用条件，缺口明确保留。外部URL是本地已有记录，实际访问后才能声称本次原文核验；模型补充和项目推断不冒充库内事实。`gap` 不授权使用无关知识硬答，可调整专业表述或补查后仍保留缺口。

地图 `route` 是导航；只有 `formal_relations` 是正式关系。新来源、依赖变化和待处理关联由followup摘要提醒，详细处理遵循知识Skill的 `references/incremental-followup.md`；显示限制和checkpoint不清除未处理项。

## 完成与权限

正文充分准确、读取正确足够、回答专业且忠于事实分别核对。用户掌握与真实项目效果各自需要真实证据，不由工具状态或上述检查自动升级。`traceability_pass` 仅是引用及检查项机械完整，不能改成语义通过。

知识调用、外部补证和缓存复用不构成知识树反馈、项目写入、生成、剪辑或发布授权。已有建议仍由相应岗位和用户的采用/执行范围决定；来源变化不自动改写已采用项目事实。
