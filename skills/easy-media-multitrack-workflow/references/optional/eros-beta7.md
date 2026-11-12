内容：10Eros-Max Beta 7（含 Fix1）的提示词写法（省去官方固定栏位与 `<Subject N>` 转址，分 default 与 ref 两种模式）；不默认的原因：仅适用于 TenStrip 10Eros Beta 7 系列模型，与原生 H3 官方栏位格式不同

# 10Eros Beta 7 提示词写法

用本文件时，提示词格式以本文件取代 [prompts/base.md](../prompts/base.md) 与 [prompts/ref.md](../prompts/ref.md) 的固定栏位；分镜分工照 [craft.md](../craft.md)，标签编号照 `apply` 报告的 `tasks[].labels`。

## 共通规则

- **不写官方栏位标题与 `<Subject N>` 标签**：省略 `subject_definitions:`、`summary:`、`retention_analysis:`、`detailed_description:`、`overall_soundscape:`、`non_diegetic_music:`（`overall_soundscape` 易诱发异常音效；`<Subject N>` 符号转址在 Beta 7 Fix1 上会稀释五官与发型细节）。
- **紧凑三段式纯英文现在式自然语言**：`参考锁定与场景地标 → 空间基准与主词集中的动作演进 → 1–2 句克制的同步物理／环境实音`，不在动作段末尾补写「镜头保持稳定、自然肤色」等画质套话。
- 单镜片段不写 `[Shot N]` 与时间戳；只有同一段内确需硬切多镜时，才加 `[Shot 2] At MM:SS.mmm, the camera cuts to …`。
- 台词直接给说话角色标 `(S1)` 并用 `<d>[Language] …</d>`；不要背景音乐时，末尾写明 `with no background music`（防模型自行配乐）。
- 光线写柔和具体的光源（如 `soft diffused indoor light`、`warm dim bedside lamp light`），少写强烈发光词以防过曝；细微声音加 `soft`、`subtle` 等克制形容词。

## 模式一：`default`（首帧 I2V ／ 纯文字 T2V）

- 不加 `For the target video, at 0.00 seconds…` 开场句。
- 首帧 I2V（挂 1 张起始图）：第一段直接锚定图中可见的人物辨识特征、起始姿势、构图与光线（不写成冗长静态图说），第二段写支撑重心与动作演进，第三段写同步声音。
- 纯文字 T2V（无图）：不写任何 `<Picture N>` 标签，直接从第一段建立场景、主体与光线写起。

首帧 I2V 范例：

```text
A medium close-up in a dimly lit bedroom frames the young woman with long dark hair and a loose white shirt sitting near the edge of the bed under warm bedside lamp light, matching the opening frame's exact composition and soft shadows across the sheets.

She shifts her weight slightly onto one hand on the mattress, slowly looks up toward the camera, and tucks a loose strand of hair behind her ear as her shoulders relax. Holding eye contact with a faint smile, the woman (S1) speaks softly: <d>[Japanese] やっと落ち着いたね。</d>

Soft fabric rustling against the bedding, her quiet breath, and close indoor room tone, with no background music.
```

## 模式二：`ref`（多图参考 ／ 锁首帧 ／ 动作与音色参考）

- **角色、环境与构图参考生成**（原图不是第一帧）：
  - **第一段直接用 `<Picture N>` 绑定简短代称**：如 `<Picture 1> is the … (Woman A, 极简发色发型／穿搭区分词), fully preserving her exact facial identity and body proportions from <Picture 1>.`（多格拼接设定板可顺带点明 `shown across the panels of <Picture 1>` 防误认成多人）。文字只写区分角色所需的极简特征，五官与身材交由参考图决定。
  - **环境图点名具体地标、弱参考指明取舍**：环境图 `<Picture M>` 写出 2–3 个具体材质、色调或家具地标；末位若挂姿势／构图图 `<Picture K>`，指明 `is used only as a loose layout guide for camera angle and posture, ignoring its [非本片背景或特征]`（若为多格分镜图则对应到 `[Shot 1]`、`[Shot 2]`）；若挂 `<Video N>` 参考动作或运镜，同样以一句自然语言说明借用 `<Video N>` 的动作节奏或镜头轨迹。
  - **第二段锚定内部支撑面并集中主词分工**：先写明人物所处的空间内侧与支撑面（如阳台栏杆内侧木地板、床垫上，防穿墙悬空）；多人或多部位同时互动时，**以核心角色为主词，在同一句内紧凑写完双手与身体各处的同步分工**，用具体的支撑点与平滑微动作取代幅度过大的位移词。
- **在 `ref` 节点拿成品图锁死首帧场景**（例如同时要挂 `<Audio N>` 音色或后续角色板，且 `<Picture 1>` 就是同比例起始画面）：开头改用场景锁定句：
  `<Picture 1> is the correct scene identity. All lighting, lighting hue, color palette, visual output style, camera angle, camera focal length, camera orbit, camera framing, background, background objects, and characters are fully preserved from <Picture 1>. <Picture 1> is the initial frame for the video and the scene continues from there.`
  之后直接接自然语言动作与结尾声音。
- **挂 `<Audio N>` 音色参考时**（建议约 15 秒语气有变化的干净语音）：第一段末尾补上 `<Audio 1> provides the vocal timbre, pitch, and delivery style for Woman A (S1) without copying the audio signal.`，正文说话句写 `In the vocal timbre of <Audio 1>, Woman A (S1) says: <d>…</d>`。

多参考图（角色＋环境）范例：

```text
<Picture 1> is the young woman (Woman A, blonde hair with pink tips, navy cardigan) and <Picture 2> is the young woman (Woman B, straight black bob, cream blouse), fully preserving each woman's exact facial identity, hairstyle, and proportions from her reference image. The setting is fully preserved from the sunlit corner cafe in <Picture 3>, featuring dark-stained wooden booth tables, brass pendant lamps, and rain-streaked street windows on the left.

Seated side by side on the indoor leather bench inside the booth beside the rain-streaked window, a medium shot frames Woman A wrapping her left hand around a white ceramic mug while resting her right hand lightly on Woman B's wrist across the tabletop. Woman A takes a small sip, sets the mug down on the wood surface, and turns in profile with a teasing smile toward Woman B as Woman A (S1) says: <d>[Japanese] ねえ、さっきから何見てるの？</d>

Subtle porcelain clink on wood, soft rain pattering outside, and her clear close voice, with no background music.
```

来源：[TenStrip/10Eros-Max](https://huggingface.co/TenStrip/10Eros-Max) 讨论区与 Discord 实测整理、[LokkenJP/TenEros_Max_optimized_learned](https://huggingface.co/LokkenJP/TenEros_Max_optimized_learned) 的 Fix1 自然语言实证，以及多场景控制变因对测。
