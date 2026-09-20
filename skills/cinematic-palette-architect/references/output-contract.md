# 输出契约

## 目录

1. 资产命名
2. 色卡标题
3. 色彩顺序
4. 图片布局
5. 标签
6. 三层输出
7. 英文提示词模板
8. JSON 契约
9. 质量控制

## 1. 资产命名

使用：

`[OBJECT TYPE] — [SUBJECT / ASSET NAME] — [PALETTE MODE]`

示例：

- `CHARACTER — MALE LEAD — BASE ASSET`
- `COSTUME — FEMALE LEAD OUTERWEAR — BASE ASSET`
- `PROP — PORTABLE MEDICAL SCANNER — PROFESSIONAL REDESIGN`
- `SET — UNDERGROUND CONTROL ROOM — FINAL SHOT`
- `VEHICLE — DESERT PATROL TRUCK — BASE ASSET`
- `SHOT — RAINY NIGHT CONFRONTATION — FINAL SHOT`

若用户提供项目名，将其单独存储；仅在有助于识别时置于资产名前。不要虚构项目名。

## 2. 色卡标题

使用：

`[ASSET NAME] — [PALETTE MODE] — [COLOR COUNT] COLOR PALETTE`

可选项目名版本：

`[PROJECT NAME] — [ASSET NAME] — [PALETTE MODE] — [COLOR COUNT] COLOR PALETTE`

保持易读，不添加无用措辞。

## 3. 色彩顺序

设计色卡依次排列：

1. 主导色
2. 支撑色
3. 主要材料色
4. 次要材料或结构色
5. 身份色
6. 叙事强调色
7. 功能色或自发光色
8. 阴影和黑位控制色
9. 高光、污染或氛围色

取样色卡按视觉面积、叙事重要性和视觉显著性排列。除非用户要求，不按彩虹顺序排列。

## 4. 图片布局

### 5–13 色

- 横向 16:9
- 单行
- 等距
- 扁平矩形色块
- 每个色块约 2:3 竖向比例

### 14–20 色

- 横向 16:9
- 两行对齐
- 间距均衡
- 严格保留阅读顺序
- 确保标签与 HEX 可读

图片只能包含标题、扁平色块、简短英文功能标签和 HEX。禁止百分比、长描述、插画、场景、摄影、渐变、纹理、阴影和装饰图形。

图像生成模型可能无法严格保持 HEX、文字或纯色色块。把生成图视为视觉预览；当用户要求颜色真值时，以 JSON/HEX 为准，不声称生成图片像素绝对准确。

## 5. 标签

标签必须使用英文，简短、具体、面向制作并易读；在提示词中逐字写明。

推荐：

- `PRIMARY WEATHERED WOOL`
- `OXIDIZED BRASS HARDWARE`
- `LOW-CHROMA SKIN MIDTONE`
- `COLD CONCRETE SHADOW`
- `EMERGENCY CYAN DISPLAY`
- `RAIN-WET ASPHALT BLACK`

避免：

- `NICE BLUE`
- `CINEMATIC COLOR`
- `BEAUTIFUL GOLD`
- `DARK COLOR`
- `MOODY TONE`

## 6. 三层输出

除非用户另有要求，每套色卡都输出三层。

### SCENE SWATCH BRIDGE 单层输出

v0.8 项目中，只有 `cinematic-palette-architect/SKILL.md` 定义的 `SCENE SWATCH BRIDGE` 可以覆盖默认三层输出。它必须满足：

- 上游纯场景文字已经由用户确认；未确认时停止，不交付任何色卡内容。
- 只交付恰好一个 `text` fenced code block，代码块内全部为英文，可直接复制给外部图片平台生成一张色卡参考图。
- 不输出中文分析、JSON、HEX/色值列表、机器码、第二套方案或代码块外的附加色卡说明。
- 英文提示词可以用清楚的颜色功能名称、相对冷暖、明度、彩度、面积、材料与光影关系描述目标，但不得显示 HEX 文字、色卡标签数据表或机器可读值。
- 该图只是综合色、材质配色、面积层级和光影倾向的视觉参考，不是颜色真值，也不提供场景物体、构图、人物身份或最终 D Look。
- 不生成图片；状态保持等待用户外部执行和带回参考图。

色卡图带回后，最终场景或人物组合提示词只允许继承颜色关系、面积层级、材质职责、环境光与高光倾向以及已经确认的氛围方向；必须明确排除白底、色块、标题、标签、HEX 文字、字体、版式、插画、场景物体和构图。若这些职责未写清，不得把该图交给末端格式器。

### LAYER 1 — 中文专业分析

按分析深度压缩以下字段：

- 任务识别：
- 对象类型：
- 当前模式：
- 视觉命题：
- 设计野心：
- 环境关系：
- 推荐色数：
- 配色结构：
- 影调策略：
- 色相与彩度策略：
- 面积与位置策略：
- 方案选择理由：
- 核心颜色角色：
- 纠偏重点：

纯取样模式将“视觉命题、设计野心、环境关系、方案选择理由”标为不适用或省略，不得伪造创意判断。

图片双输出时，分别说明“原图取样逻辑”和“专业重设计逻辑”。

### LAYER 2 — ENGLISH CHATGPT IMAGE PROMPT

为每套色卡提供一个独立、完整、可复制的英文提示词，并放入 fenced code block。必须写明：

- 专业影视制作色卡风格
- 横向 16:9
- 白色背景
- 精确标题
- 精确色数
- 单行或双行布局
- 精确阅读顺序
- 精确英文标签
- 精确大写 HEX
- 扁平纯色色块
- 精确文本渲染
- 无渐变、纹理、阴影、插画、场景、摄影或装饰
- 不得缺失、重排、重复或改写条目

### LAYER 3 — ENGLISH JSON PALETTE SHEET

为每套色卡输出独立的有效 JSON fenced code block。提示词与 JSON 的标题、顺序、标签和 HEX 必须完全一致。

## 7. 英文提示词模板

按实际色卡完整替换方括号：

```text
A clean professional cinematic color palette swatch sheet for film and streaming production reference, landscape 16:9 ratio, pure white background.

Top header text centered above the swatches in bold clean sans-serif typography:
"[EXACT TITLE]"

[LAYOUT DESCRIPTION]

Each swatch is a flat solid-color rectangle with hard edges, no gradient, no texture, no border, and no shadow.

Between each swatch and its HEX code, display its exact English functional label in small clean sans-serif black typography, centered. Below each label, display its exact uppercase HEX code in small clean monospace black typography, centered.

The colors must appear in this exact reading order:
1. Color: [HEX] — Label: "[LABEL]"
2. Color: [HEX] — Label: "[LABEL]"

Art direction: minimal professional film-production color reference sheet, precise alignment, generous white space, highly legible typography, visually neutral presentation.

ONLY show the header title, flat color swatches, exact functional labels, and exact HEX codes.

NO illustrations.
NO scene imagery.
NO photographic content.
NO gradients.
NO textures.
NO shadows.
NO decorative elements.
NO missing colors.
NO duplicated colors.
NO reordered colors.
NO altered labels.
NO altered HEX text.
Maintain the listed sRGB HEX targets as closely as possible.
```

列出全部颜色，不要只保留模板中的两个示例条目。

## 8. JSON 契约

使用以下结构：

```json
{
  "project_name": "",
  "asset_name": "",
  "asset_code": "",
  "palette_mode": "",
  "object_type": "",
  "color_space": "sRGB D65",
  "design_ambition": "",
  "visual_thesis": "",
  "environment_relationship": "",
  "color_count": 0,
  "title": "",
  "palette_structure": "",
  "colors": [
    {
      "order": 1,
      "label": "",
      "hex": "#000000",
      "role": "",
      "source": "sampled | redesigned | established",
      "material": "",
      "placement": "",
      "usage_weight": "dominant | secondary | limited | trace",
      "state": "base | worn | contaminated | lit | atmospheric"
    }
  ]
}
```

规则：

- 输出严格有效的双引号 JSON，不添加注释。
- 未知或不适用的可选信息使用空字符串。
- `design_ambition` 使用 `REALIST`、`AUTHORIAL`、`ICONIC` 或空字符串。
- `environment_relationship` 使用 `ASSIMILATE`、`SEPARATE`、`CONTAMINATE`、`EVOLVE` 或空字符串。
- `source` 只填一个实际值：`sampled`、`redesigned` 或 `established`。
- `usage_weight` 只填一个实际值：`dominant`、`secondary`、`limited`、`trace` 或空字符串。
- `state` 只填一个实际值：`base`、`worn`、`contaminated`、`lit`、`atmospheric` 或空字符串。
- `color_count` 必须等于 `colors` 数组长度。
- 顺序、标签和 HEX 必须与提示词逐项一致。
- 多套色卡使用多个独立 JSON 对象。
- 颜色面积与位置保存在 JSON 中，不显示在色卡图片上。

## 9. 质量控制

交付前逐项确认：

- 模式选择正确，纯取样未被重设计污染。
- 视觉命题具有对象专属性。
- 设计野心和环境关系与需求一致。
- 设计任务已比较逻辑不同的候选，而非采用第一反应。
- 方案通过反套路审查。
- 资产本色和镜头效果没有错误混合。
- 每个颜色都有明确功能、材料、位置或状态依据。
- 影调层级以面部或主体曝光为锚点。
- 色相与彩度层级受控，不只是统一降饱和。
- 颜色面积和显露条件合理。
- 皮肤、织物、金属、玻璃、植被和自发光色符合材料行为。
- 方案在相关光源、曝光、景别和最终 Look 下仍成立。
- 已删除无意义的近似色。
- 色数由功能决定且未超过 20。
- 标签简短、明确、不重复。
- HEX 为大写六位 sRGB。
- 提示词与 JSON 的标题、顺序、标签和 HEX 完全一致。
- 布局与色数匹配。
- 已确认的连续性得到保留。
- 未把不受控截图中的显示色宣称为精确物理材料色。
- 未把图像生成结果宣称为严格颜色真值。

## 10. v0.8 项目接口

项目级输出按需要增加：

```text
项目视觉弧线版本：
故事阶段 / 人物状态：
本阶段允许变化：色彩 / 反差 / 光线 / 曝光 / 运动强度
例外与原因：
明确禁区：
```

场景级输出只写增量：

```text
关联 Sxx / 场景制作卡版本：
相对项目基准的变化：
资产本色：
光源色与环境染色：
摄影显示意图：
交 D 的最终 Look 目标（尚未完成）：
```

给 `Vxx Setup` 的精确引用：

```text
色卡或视觉弧线版本 / 路径 / SHA-256：
参考图路径 / SHA-256：
适用 Vxx：
```

不得把资产本色、光源色、拍摄意图和最终 D Look 合并成含义不清的一栏。C 可规定叙事目标与生成前显示意图，D 对采用素材完成镜头匹配和最终 Look。

给图片或视频格式器的 prompt-ready `FINAL SHOT` 显示意图不得只写项目名、版本号、机型/胶片名或“电影感”。至少展开：

```text
现实光源 / 方向 / 软硬 / 冷暖关系：
主导色 / 支撑色 / 有限强调色及材料与位置：
曝光锚点 / 反差 / 中间调 / 黑位 / 高光 / 阴影倾向：
空气 / 天气 / 湿润 / 反射 / 颗粒 / 晕光 / 柔化 / 成像损失：
肤色 / 发色 / 服装 / 资产本色保护：
观众第一感受 / 本场专属禁区：
```

字段可以按任务压缩为自然语言，但每项决定必须可被外部模型观察或可由生成结果验收。只有器材名、抽象审美词或负面约束时，接口不完整；只有用户明确采用中性无偏移显示且仍写清物理光源、曝光、材质和保护项时，才可合法不增加明显色偏或滤镜。
