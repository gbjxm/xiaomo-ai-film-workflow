# 来源与适配

- 上游：[lordhoell/davinci-resolve-mcp](https://github.com/lordhoell/davinci-resolve-mcp/tree/b134a4d5bb9110a23af7669d5bbe183b9f626aa5)
- 固定提交：`b134a4d5bb9110a23af7669d5bbe183b9f626aa5`；源码包版本 0.1.0。
- 适配来源：上游 `skill/davinci-resolve-mcp/SKILL.md`、`references/workflows.md`，以及该提交 `src/davinci_resolve_mcp/` 中真实工具签名。
- 许可证：MIT，完整版权与许可声明随本 Skill 的 `LICENSE.upstream` 保留。

本适配仅保留对象注册、真实 ID 传递、版本/参数核对、原生保存与渲染等接口方法。修正了上游示例与实际工具签名不一致之处；不保留任意脚本、Fusion 设置/表达式、静音与音频指纹固定阈值、全部 API 已验证的承诺。

运行时为本机隔离安装的受限入口，外层策略 `xiaomo.resolve-background/v1.1`；上游源码未改，能力限制由独立启动器实际执行。本 Skill 不包含服务安装脚本，不自动安装、升级或写客户端配置。

上游 README 提及的裸 `pip install davinci-resolve-mcp` 与本次审查仓库不能等同：核查时 PyPI 同名项目归属 filmcademy。部署应使用固定提交构建的本机包及已确认入口，不能用裸同名包替换。

验收依据为本批 `tool-evidence/mcp/` 下的 source-manifest、tool-policy、installation-receipt、verification-save-guard、stdio-readonly-smoke 回执。维护材料只供核查；日常调用以当前连接及专业任务要求为准。
