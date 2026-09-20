# 小陌 AI 影视工作流 V1

在 Codex 中使用的个人 AI 影视工作室：由人决定创作方向，AI 协助故事开发、视觉设计、制作组织、后期与发行准备。

当前产品版本 **1.0**，包含 **23 个随包技能**，沿用 `studio-v0.9` 工作室协议。适用于 Windows / PowerShell。

## 开始使用

1. 将仓库完整下载并解压，或使用 Git 克隆到本地，保留目录结构。
2. 在 Codex 中打开这个文件夹，发送：

```text
请读取根目录 AGENTS.md、00_开始这里.md、skills/manifest.json 和 安装工作流.ps1，安装随包23个Skills并验证本机路径配置。不要迁移或修改现有项目。
```

也可以在工作流根目录的 PowerShell 中执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\安装工作流.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\安装工作流.ps1" -VerifyOnly
```

安装器仅安装清单内的技能并写入本机路径配置；已有同名但内容不同的技能会先备份。移动仓库文件夹后重新运行安装器以更新路径。若 Windows PowerShell 缺少 `Get-FileHash`，可在已有 PowerShell 7 中将命令开头换成 `pwsh`。

安装后，在 Codex 中发送：

```text
使用 $start-ai-film-project，新建影视项目《项目名》。
```

仓库中的 `项目/` 仅包含 `.gitkeep` 占位文件。新项目保存在本地 `项目/` 中，使用项目内的 `开始这里.md` 继续工作；项目内容默认不进入 Git。

## 六个专业部门

| 部门 | 职责 |
| --- | --- |
| A 制片统筹 | 目标、资源、进度、依赖与完成证据 |
| B 编剧开发 | 故事、人物、因果与对白 |
| C1 美术与资产 | 角色、场景、服化道与视觉连续性 |
| C2 导演与视听制作 | 创作整合、表演、摄影灯光与镜头设计 |
| D 剪辑与声音后期 | 素材接管、剪辑、声音、调色与成片检查 |
| E 发行与复盘 | 发布包装、资格检查与真实反馈 |

部门按任务协作，不预建六个永久聊天。完整候选与明确采用的正式内容分别保存。媒体生成、剪辑执行、上传和公开发布依赖当次授权与实际可用工具。

## 仓库内容

- [使用入口](00_开始这里.md)、[V1 总框架](工作流/小陌AI影视工作流总框架_v1.0.md)与[工作室共同契约](skills/orchestrate-ai-film-project/references/studio-v09-contract.md)。
- [23 个技能清单](skills/manifest.json)、随包脚本和专业参考。
- V1 与上一版 v0.9 的框架、项目模板；默认新建 V1 项目。
- [发布交接](HANDOFF.md)与[发布说明](RELEASE_NOTES.md)。

个人影片、真实素材、知识库原文、机器配置、维护备份和未采用的升级候选不包含在发布副本中。模板中的 `.gitkeep` 用于保留必要空目录，请随仓库一起保留。

## 可选能力与验证范围

Codex 原生图片、视频平台、DaVinci Resolve 及其 API/MCP、个人知识树均需按实际环境单独具备。安装 23 个技能不等于这些服务已连接，也不代表真实影片的画质、声音或剪接已通过验收。

旧项目按自身入口运行，安装本版本不会自动迁移旧项目。随包第三方许可见 [DaVinci Resolve MCP 上游许可](skills/davinci-resolve-mcp/LICENSE.upstream)；仓库尚未单独授予整体开源许可证。
