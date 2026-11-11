内容：10Eros-Max Beta 7（含 Fix1）的提示词写法（省去官方固定栏位，分 default 与 ref 两种模式）；不默认的原因：仅适用于 TenStrip 10Eros Beta 7 系列模型，与原生 H3 官方栏位格式不同

# 10Eros Beta 7 提示词写法

用本文件时，提示词格式以本文件取代 [prompts/base.md](../prompts/base.md) 与 [prompts/ref.md](../prompts/ref.md) 的固定栏位；分镜分工照 [craft.md](../craft.md)，标签编号照 `apply` 报告的 `tasks[].labels`。

## 共通规则

- 不写官方栏位标题：省略 `subject_definitions:`、`summary:`、`retention_analysis:`、`integrated_multimodal_description:`、`overall_soundscape:`、`non_diegetic_music:`；尤其不写 `overall_soundscape`（在 Beta 7 上极易诱发异常音效或干扰主提示词）。
- 三段式纯英文现在式自然语言：`起始画面状态 → 按时间演进的动作与运镜 → 1–2 句克制的同步物理／环境音`。
- 单镜片段不写 `[Shot N]` 与时间戳；只有同一段内确需硬切多镜时，才加 `[Shot 2] At MM:SS.mmm, the camera cuts to …`。
- 台词照常给角色标 `(S1)` 并用 `<d>[Language] …</d>`；不要背景音乐时，末尾写明 `with no background music`（防模型自行配乐）。
- 光线写柔和具体的光源（如 `soft diffused indoor light`、`warm dim bedside lamp light`），少写强烈发光词以防过曝；细微声音加 `soft`、`subtle`、`low-volume` 等克制形容词。

## 模式一：`default`（首帧 I2V ／ 纯文字 T2V）

- 不加 `For the target video, at 0.00 seconds…` 开场句。
- 首帧 I2V（挂 1 张起始图）：第一段直接锚定图中可见的人物特征、服装、起始姿势、构图与光线（抓住辨识特征即可，不写成冗长静态图说），第二段写动作与镜头演进，第三段写同步声音。
- 纯文字 T2V（无图）：不写任何 `<Picture N>` 标签，直接从第一段建立场景、主体与光线写起。

首帧 I2V 范例：

```text
A medium close-up in a dimly lit bedroom frames the young woman with long dark hair and a loose white shirt sitting near the edge of the bed under warm bedside lamp light. Soft shadows fall across the sheets behind her, matching the opening frame's exact composition and lighting.

She slowly looks up toward the camera, tucks a loose strand of hair behind her ear, and lets out a quiet breath as her shoulders relax. A faint, gentle smile forms on her lips while she holds eye contact, then the woman (S1) speaks softly: <d>[Japanese] やっと落ち着いたね。</d> The camera holds a steady eye-level medium close-up with subtle breathing motion.

Soft fabric rustling against the bedding, her quiet breath, and close indoor room tone, with no background music.
```

## 模式二：`ref`（角色设定图 ／ 锁首帧 ／ 音色参考）

- **角色设定图换新场景**（原图不是第一帧）：第一段用 `<Subject 1> is the … from <Picture 1>, preserving her …` 绑定角色。Beta 7 单靠标签认脸较弱，句中须同时写出关键可见特征（发色发型、服装单品），且需认脸的镜头优先用中近景；不要写首帧锁定句。多个角色依次定义 `<Subject 2>`，参考图长宽比尽量对齐输出画面。
- **在 `ref` 节点拿成品图锁死首帧场景**（例如同时要挂 `<Audio N>` 音色，且 `<Picture 1>` 与输出影片同比例、就是起始画面）：开头改用场景锁定句：
  `<Picture 1> is the correct scene identity. All lighting, lighting hue, color palette, visual output style, camera angle, camera focal length, camera orbit, camera framing, background, background objects, and characters are fully preserved from <Picture 1>. <Picture 1> is the initial frame for the video and the scene continues from there.`
  之后直接接自然语言动作与结尾声音。
- **挂 `<Audio N>` 音色参考时**（建议约 15 秒语气有变化的干净语音）：开头补上音色定义与保留声明，正文说话前点明音色来源，其余仍不写 `overall_soundscape`：
  `<Audio 1>: the vocal-timbre reference providing pitch, tone, and delivery style for <Subject 1> (S1).`
  `<Audio 1>: reference - vocal timbre and delivery guide speech of <Subject 1> (S1) without copying audio signal.`
  正文说话句：`In the vocal timbre of <Audio 1>, <Subject 1> (S1) says: <d>…</d>`

角色设定图换场景范例（`<Picture 1>` 为角色图）：

```text
<Subject 1> is the blonde young woman from <Picture 1>, preserving her exact facial identity, pink-tipped blonde hair, and navy cardigan from <Picture 1>.

In a sunlit cafe booth, a medium close-up frames <Subject 1> (S1) sitting across the wooden table with both hands wrapped around a white ceramic mug. She glances down at the drink, lifts it for a small sip, sets the mug back onto the table, and looks up with a light, teasing smile as <Subject 1> (S1) says: <d>[Japanese] ねえ、さっきから何見てるの？</d> The camera stays in a stable eye-level medium close-up, keeping natural skin texture and warm afternoon window light consistent throughout.

Subtle porcelain clink on wood, quiet cafe room ambience, and her clear close voice, with no background music.
```

来源：[TenStrip/10Eros-Max](https://huggingface.co/TenStrip/10Eros-Max) 讨论区（#50、#63、#74、#77 及音色参考说明）与 [LokkenJP/TenEros_Max_optimized_learned](https://huggingface.co/LokkenJP/TenEros_Max_optimized_learned) 的 `Basic_prompt_enhancer-v3_lokken`。
