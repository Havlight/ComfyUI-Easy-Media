内容：Ultima（含 Ultima NSFW Hybrid）系列的提示词写法（结构化栏位、极简特征与 <Subject N> 双重定位、retention_analysis 权重分级、写实骨骼锚点与精简物理辅助词，分 default 与 ref 两种模式）；不默认的原因：仅适用于 Ultima 系列模型，与原生 H3 完整六栏格式及 10Eros Beta 7 无标签三段式不同

# Ultima 系列提示词写法

用本文件时，提示词格式以本文件取代 [prompts/base.md](../prompts/base.md) 与 [prompts/ref.md](../prompts/ref.md) 的固定栏位；分镜分工照 [craft.md](../craft.md)（成人题材并读 [modules/type-adult.md](../modules/type-adult.md)），多模态标签编号严格照 `apply` 报告的 `tasks[].labels`。

## 共通规则

- **精简结构化栏位（省去 `summary:` 与 `For the target video…` 开场句）**：依序组织 `subject_definitions:` ➔ 独立一行写实与人体骨骼关节力学锚点句 ➔（`ref` 模式加 `retention_analysis:`）➔ `detailed_description:` 正文 `[Shot 1]` ➔ `overall_soundscape:` ＋ 4–6 条贴合当镜动作的 `*_physics: XX%` 辅助词 ➔ `non_diegetic_music: N/A`（或配乐说明）。
- **视觉 `<Subject N>` 搭配极简特征定位，`(S1)` 仅作发声编号**：
  - 视觉主体统一用 `<Subject 1>`、`<Subject 2>`……定义时点破区分角色所需的**极简发色发型／穿搭特征**（五官细节交由参考图决定）；正文首次出场及多人肢体交错处以「`<Subject N>` ＋ 极简特征／性别部位词」双重锁定归属（如 `<Subject 1>, the black-haired woman`、`<Subject 1>'s slender hands`、`<Subject 3>'s muscular thighs`），**严禁用自创小写 `(s1)`、`(s2)` 指代视觉主体或身体器官**（易导致跨角色长错手脚）。
  - 大写 **`(S1)`、`(S2)` 严格只代表开口发声者编号（Audio Speaker ID）**，仅标在有 `<d>` 台词的角色后方，不发声的角色与器官一律不标。
- **标签大写与同段切镜语法**：参考标签严格首字母大写（`<Picture 1>`、`<Video 1>`、`<Audio 1>`）。单镜片段只写 `[Shot 1]`（不标时间戳）；同一段内需硬切多镜时，每个镜头独立换行写 `[Shot 2] At MM:SS.mmm, the camera cuts to …`、`[Shot 3] At MM:SS.mmm, the camera cuts to …`（单镜内有蓄力到爆发的阶段转折亦可标 `At 0.00s … Within 2.0s …`）。
- **对嘴台词与外置语气微表情**：口音、情绪与神态括号一律放在 `<d>` 标签**外侧**，发声内容收在 `<d>[Language] …</d>` 内，如 `<Subject 1> (S1) (teasing whisper, half-lidded eyes) says: <d>[English] …</d>`；口部被亲吻或含覆占用时严禁放对嘴 `<d>` 台词。
- **SFW／NSFW 直白精确与遮挡一致**：SFW 具体写出材质与重心交互；NSFW 直接选用 [modules/type-adult.md](../modules/type-adult.md)「直白精确英文词汇表」，禁委婉词与否定词（`no / not X`），且只描写当前画面外部可见的部位（已插入时不写体内的龟头外观）。

## 模式一：`default`（纯文字 T2V ／ 首帧 I2V）

- **纯文字 T2V（无参考图）**：`subject_definitions:` 中每个角色句尾注明 `without a reference.`，全文不写 `<Picture N>`，省去 `retention_analysis:`。
- **首帧 I2V（挂 1 张起始图 `<Picture 1>`）**：`<Subject 1>` 绑定 `from <Picture 1>, characters are brought to life.`（不加 `For the target video…` 句），`[Shot 1]` 首句直接锚定首帧构图、光线与起始支撑姿态后顺势推进动作。

`default` 范例（单镜连续手持互动）：

```text
subject_definitions:
<Subject 1> is a young woman (long dark hair, cropped grey tank top) with exact face, hairstyle, skin-tone, body proportions, and outfit from <Picture 1>, characters are brought to life.
<Subject 2> is a 24-year-old athletic man in casual shorts without a reference.

Photorealistic, warm indoor ambient lighting with colourful RGB monitor glow, high res, detailed, real-life footage, realistic skin texture, real living person. Anatomically consistent adult characters, natural human proportions, realistic skeletal structure, accurate joint articulation, physically believable weight distribution, subtle muscle contractions, and realistic body movement.

detailed_description:
[Shot 1] Single continuous handheld GoPro-style POV medium shot inside <Subject 1>'s cozy gaming bedroom. Walking smoothly across the wooden floor while holding the wide-angle camera at arm's length, <Subject 1>, the dark-haired woman, shows off her glowing dual-monitor desk setup with a playful expression as the camera sways naturally with her steps. <Subject 1> (S1) (cheerful sarcastic British accent, lazily raising an eyebrow) says: <d>[English] Hi guys, this is where I play games when I'm bored.</d> She casually pans the handheld camera toward the bed, revealing <Subject 2> lying flat on the mattress with his wrists loosely tied to the headboard with prop ribbons in wide-eyed disbelief while <Subject 1> covers a mischievous smirk and swings the lens back toward her monitors.

overall_soundscape:
Close clear female voice, soft bare footsteps on bedroom wood flooring, subtle clothing rustle, and quiet computer fan hum.
Dialogue_Clarity: 100%
Character_Voice_Consistency: 100%
Facial_Microexpressions: 100%
Naturalistic_body_physics: 100%
Naturalistic_hands_physics: 100%
Camera_shaking: 20%

non_diegetic_music:
N/A
```

## 模式二：`ref`（多图参考 ／ 局部与灰模权重分级 ／ 音色参考）

- **`subject_definitions:` 角色唤醒与单一主体原则**：
  - 露脸角色写 `<Subject 1> is the young woman (极简发色发型／穿搭词) with exact face, exact hairstyle, exact skin-tone, exact body proportions, and exact outfit from <Picture 1>, characters are brought to life.`。
  - **禁止把同一张全身图拆成两个 Subject**：全身男角板 `<Picture M>` 只定义一个 `<Subject>`，且直接只列出该镜实际入镜的局部（如 `<Subject 3> is the seated athletic man's taut lower abdomen, muscular thighs, and single upright erect penis from <Picture 3>.`）；**仅当参考槽额外挂了一张独立的局部私处特写图时**，才为该图单独设 `<Subject>`。
  - 环境图定义为一个 `<Subject>` 并点名 2–3 个具体家具与光源地标；末位 3D 灰模或姿势图 `<Picture K>` 只写 `<Picture K> is a loose camera-angle and spatial posture guide only.`（不写 `brought to life`）。
- **`retention_analysis:` 三级权重与分镜出场隔离（必写，防长错手脚与灰模污染）**：
  - 完整入镜主角与场景标 `fully_preserved`（同段内含多镜硬切且某角色不在所有 Shot 出场时，注明 `(appears in [Shot 1], [Shot 3])`，防模型把未出场角色硬塞进局部特写镜）；
  - **仅局部入镜的角色板（如只露腰腿或扶握双手的男角板）标 `partially_preserved`**，指明仅哪些部位出现在画面边缘（防止模型把未入镜的男方上半身或肌肉手臂强行嫁接到女角身上）；
  - 末位 3D 灰模 `<Picture K>` 标 `weak_reference`，指明对应哪个 `[Shot N]` 的机位角度与摆位几何，忽略灰泥人偶材质与几何块。
- **挂 `<Audio N>` 音色或配乐**：在 `subject_definitions:` 写 `<Audio 1> is the [语气] voice-timbre reference for <Subject 1> (S1).`，`retention_analysis:` 标 `reference - guides vocal timbre without copying the raw signal.`。

`ref` 多参考图＋同段多镜切换范例（`[Shot 1] ➔ [Shot 2] ➔ [Shot 3]`）：

```text
subject_definitions:
<Subject 1> is Sena (long blonde hair, open-chest burgundy knit dress exposing bare full breasts, sheer black stockings) with exact face, exact hairstyle, exact fair skin-tone, exact body proportions, and exact outfit from <Picture 1>, characters are brought to life.
<Subject 2> is Rin (sleek black bob, open black shrug exposing bare perky breasts, black garter stockings) with exact face, exact hairstyle, exact fair skin-tone, exact body proportions, and exact outfit from <Picture 2>, characters are brought to life.
<Subject 3> is the seated athletic man's taut lower abdomen, spread muscular thighs, and single upright erect penis from <Picture 3>.
<Subject 4> is the warm lamp-lit living room with charcoal velvet sofa and cream plush rug from <Picture 4>.
<Picture 5> is a loose camera-angle and spatial framing guide for [Shot 1] only.
<Audio 1> is the teasing voice-timbre reference for <Subject 2> (S1).

Photorealistic, warm dim indoor tungsten lamp light, high res, detailed, real-life footage, realistic skin texture, real living person. Anatomically consistent adult characters, natural human proportions, realistic skeletal structure, accurate joint articulation, physically believable weight distribution, subtle muscle contractions, natural skin compression, and realistic body movement.

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 2], [Shot 3]): fully_preserved - her face, long blonde hair, fair skin-tone, and bare full breasts stay sharp and undistorted.
<Subject 2> (appears in [Shot 1], [Shot 3]): fully_preserved - her face, sleek black bob, fair skin-tone, and bare perky breasts stay sharp and undistorted.
<Subject 3> (appears in [Shot 1], [Shot 2]): partially_preserved - only the seated man's lower abdomen, thighs, and single erect shaft appear at the frame margin.
<Subject 4>: fully_preserved - the charcoal velvet sofa, cream rug, and warm lamp glow are retained across all shots.
<Picture 5> ([Shot 1] composition guide): weak_reference - only the 45-degree downward camera angle and three-person placement in [Shot 1] are referenced, completely ignoring its matte grey mannequin material and plain studio blocks.
<Audio 1>: reference - guides <Subject 2>'s teasing vocal timbre in [Shot 3] without copying the raw signal.

detailed_description:
Warm dim indoor tungsten lamp light illuminates <Subject 4> with natural skin highlights and shallow depth of field.
[Shot 1] In a 45-degree downward medium close-up inside <Subject 4>, only three people exist in the frame: along the bottom-right edge, <Subject 3> shows only the seated man's lower abdomen, thighs on the charcoal velvet sofa, and upright erect cock. Kneeling on the cream rug against his thigh, <Subject 1>, the blonde woman, rests her slender hand on his quadriceps and leans in to wrap her glossy stretched lips around the upper half of <Subject 3>'s wet shaft while <Subject 2>, the black-haired woman on the left, cups and gently squeezes <Subject 1>'s bare full breast.
[Shot 2] At 00:03.200, the camera cuts to a tight close-up focused on <Subject 1>'s lower face and <Subject 3>'s wet shaft, where <Subject 1> hollows her cheeks and sucks rhythmically along the slick shaft and distinct mushroom-shaped glans with thin strands of clear saliva glistening at her chin while her slender fingers stroke the base.
[Shot 3] At 00:05.800, the camera cuts to a chest-up medium close-up of both women pressed bare-chested together beside the sofa; <Subject 1> catches her breath with glossy parted lips while <Subject 2> turns her dark eyes from <Subject 1> straight into the lens. <Subject 2> (S1) (sultry teasing whisper, subtle smirk) says in the vocal timbre referenced from <Audio 1>: <d>[English] Look how eagerly she takes you in her mouth.</d>

overall_soundscape:
Rhythmic wet sucking and slick saliva sounds, soft nasal breaths from <Subject 1>, <Subject 2>'s clear close teasing voice, and subtle plush rug rustle.
Dialogue_Clarity: 100%
Character_Voice_Consistency: 100%
Facial_Microexpressions: 100%
Naturalistic_body_physics: 100%
Naturalistic_hands_physics: 100%
Natural_sucking_motion: 85%
Natural_bobbing_head_while_sucking: 70%
Naturalistic_water_physics: 05%

non_diegetic_music:
N/A
```

## 尾端物理辅助词选配（每段挑 4–6 条贴合当镜动作，不堆砌无关词）

- **通用骨骼与重力**：`Naturalistic_body_physics: 100%`、`Naturalistic_legs_physics: 100%`、`Naturalistic_hands_physics: 100%`、`Naturalistic_fingers_physics: 100%`、`VFX_realistic_gravity_and_weight_physics: 85%`、`Real_time_motion: 100%`
- **对白、手持与配乐同步**：有 `<d>` 台词加 `Dialogue_Clarity: 100%`、`Character_Voice_Consistency: 100%`、`Facial_Microexpressions: 100%`；手持微晃加 `Camera_shaking: 20%`；挂 `<Audio N>` 音乐卡点加 `SFX_body_motion_match_song: 88%`、`VFX_body_motion_physics_match_song: 88%`
- **NSFW 动作与流体两档控制**：口交加 `Natural_sucking_motion: 85%`、`Natural_bobbing_head_while_sucking: 70%`、`VFX_natural_devouring_penis_physics: 75%`；私处与体位张力加 `VFX_naturalistic_tender_pussy_physics: 75%`、`natural_sexually_pose: 100%`。**常规湿润只写 `Naturalistic_water_physics: 05%`（防乱喷水）；仅在潮吹喷涌或大剂量射精水柱镜同时开启 `Naturalistic_urinating_water_physics: 50%` 与 `Naturalistic_water_physics: 50%`**。
