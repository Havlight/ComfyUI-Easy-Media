内容：Ultima（含 Ultima NSFW Hybrid）系列的提示词写法（保留 <Subject N> 与 (s1) 绑定、写实与骨骼关节力学锚点、直白精确词汇与尾端百分比物理滑杆，SFW／NSFW 通用，分 default 与 ref 两种模式）；不默认的原因：仅适用于 Ultima 系列模型，与原生 H3 官方栏位格式及 10Eros Beta 7 无标签三段式不同

# Ultima 系列提示词写法

用本文件时，提示词格式以本文件取代 [prompts/base.md](../prompts/base.md) 与 [prompts/ref.md](../prompts/ref.md) 的固定栏位；分镜分工照 [craft.md](../craft.md)（成人题材并读 [modules/type-adult.md](../modules/type-adult.md)），多模态标签编号严格照 `apply` 报告的 `tasks[].labels`。

## 共通规则

- **精简四段式骨架（保留 `<Subject N>`，省去冗长摘要栏）**：
  1. **`subject_definitions:` 主体定义**：逐行定义 `<Subject 1>`、`<Subject 2>`……正文中统一混用 `<Subject 1>` 或简写 `(s1)`、`(s2)` 指代同一主体；省略冗长的 `summary:` 与 `retention_analysis:`。
  2. **真人实拍与骨骼关节力学锚点**：紧接 1–2 句固定写实锚点，锁定关节铰接、重心分配与皮肉挤压（见下方范例原句，按室内／日光微调光源词；挂有 `<Picture 1>` 角色图时删去 `realistic skin texture` 以保留参考图原本肤质，仅无图 T2V 保留）。
  3. **`[Shot 1]` 镜头与动作正文**：写明机位景别、人物所处的固定支撑面（床垫、座椅、地板、台面）、主词集中的连续动作与表情细节。
  4. **`overall_soundscape:` ＋ 百分比物理滑杆 ＋ `Non_diegetic_music:`**：先用 1–2 句写与当前画面完全同步的物理／环境实音，接着列出 4–8 条按需选配的 `*_physics: XX%` 控制滑杆，最后以 `Non_diegetic_music: N/A`（无配乐）或 `<Audio N>` 配乐说明收尾。
- **多模态标签严格首字母大写**：底层分词器只识别首字母大写的 `<Picture 1>`、`<Video 1>`、`<Audio 1>`，禁止写成小写 `<picture 1>` 或 `<audio 1>`。
- **对嘴台词与语气微表情外置**：口音、神态与动作停顿括号一律放在 `<d>` 标签**外侧**，需发声对嘴的台词收进 `<d>[Language] …</d>` 内，例如：
  `(s1) (cheerful sarcastic British accent, lazily raising an eyebrow) says: <d>[English] Oh, don't mind him, hope you like my setup.</d>`
  既保留 Ultima 对语气与微表情括号的高响应度，又避免把舞台指示当台词念出声。口部被亲吻或含覆占用时严禁放 `<d>` 台词。
- **SFW／NSFW 直白精确原则（零委婉词、零负面句）**：
  - SFW 镜头具体写出服装材质、器械交互、脚步重心与自然光影。
  - NSFW 镜头**必须直球写出通俗触发词与精确解剖／流体光学描述**（直接选用 [modules/type-adult.md](../modules/type-adult.md)「直白精确英文词汇表」），严禁 `intimate act`、`member`、`manhood`、`without being overly explicit` 等自我审查委婉词（会导致接触部位模糊融化或动作悬空），也严禁写 `no face`、`not white` 等否定句（H3 无负面框，出现否定名词反而会画出该特征）。
- **画风纯粹与音效对齐**：不混写冲突画风（如在真人实拍里混写 `anime` 或同时堆叠多位风格相悖的导演名）；`overall_soundscape:` 与尾端滑杆只写本段实际存在的环境与动作，换场后立即清除上一段残留的环境音或流体词。

## 模式一：`default`（纯文字 T2V ／ 首帧 I2V）

- **纯文字 T2V（无参考图）**：仍然保留 `subject_definitions:`，每个主体句尾显式注明 `without a reference.`（如 `<Subject 1> is a 26-year-old athletic woman in a grey cropped tank top without a reference.`），全文不写任何 `<Picture N>` 标签。
- **首帧 I2V（挂 1 张起始图 `<Picture 1>`）**：`<Subject 1>` 绑定 `from <Picture 1>, characters are brought to life.`，`[Shot 1]` 首句直接锚定首帧已有的构图、光线与起始支撑姿势，再往下推进动作。

### SFW 范例（手持 Vlog 日常喜剧互动，`default` 首帧或单角色）

```text
subject_definitions:
<Subject 1> is a young woman with exact face, age, body, hair, hairstyle, skin-tone, body-size, and outfit from <Picture 1>, characters are brought to life.
<Subject 2> is a 24-year-old man in casual athletic shorts without a reference.

Photorealistic, warm indoor ambient lighting with colourful RGB monitor glow, high res, detailed, real-life footage, real living person. Anatomically consistent adult characters, natural human proportions, realistic skeletal structure, accurate joint articulation, physically believable weight distribution, subtle muscle contractions, and realistic body movement.

[Shot 1]
Single continuous handheld GoPro-style POV medium shot inside (s1)'s cozy gaming bedroom. Walking smoothly across the wooden floor while holding the wide-angle camera at arm's length, (s1) shows off her glowing dual-monitor desk setup with a smug, playful expression as the camera sways naturally with her steps.

(s1) (cheerful sarcastic British accent, lazily raising an eyebrow) says: <d>[English] Hi guys, this is where I play games when I'm bored. Hope you like it.</d>

She casually pans the handheld camera toward her bed, revealing (s2) lying flat on his back on the mattress with his wrists tied loosely to the headboard with soft prop ribbons as part of a staged prank, staring into the lens in wide-eyed disbelief. (s1) freezes briefly, covers a mischievous smirk, rolls her eyes playfully, and swings the camera smoothly back toward her monitors.

overall_soundscape: close clear female voice, soft bare footsteps on bedroom wood flooring, subtle clothing rustle, quiet computer fan hum in a small furnished room.

Dialogue_Clarity: 100%
Natural_Human_Speech: 100%
Character_Voice_Consistency: 100%
Facial_Microexpressions: 100%
Clothing_And_Movement_Sounds: 85%
Camera_shaking: 20%

Non_diegetic_music: N/A
```

### NSFW 范例（直白解剖与微动作，`default` T2V）

```text
subject_definitions:
<Subject 1> is a 32-year-old curvy woman with dark wavy hair in an unbuttoned silk blouse without a reference.
<Subject 2> is a 25-year-old athletic man seated in casual open trousers without a reference.

Photorealistic, soft diffused warm bedroom lamp light, high res, detailed, real-life footage, realistic skin texture, real living person. Anatomically consistent adult characters, natural human proportions, realistic skeletal structure, accurate joint articulation, physically believable weight distribution, subtle muscle contractions, natural skin compression, and realistic body movement.

[Shot 1]
High-angle first-person POV medium close-up looking down from (s2)'s seated perspective on the edge of the bed. Kneeling steadily on the carpet between (s2)'s thighs with her left hand braced firmly on his bare upper thigh and her right fingers wrapped around the base of his erect penis, (s1) hollow-cheeks and sucks his slick shaft rhythmically past her glossy stretched lips while glancing up to hold teasing eye contact. Smooth strands of clear glistening saliva stretch from her chin along the wet shaft with its distinct mushroom-shaped glans as her head bobs at a steady, unhurried pace, her soft breasts shifting subtly inside the open blouse with each breath.

overall_soundscape: rhythmic wet sucking and slick saliva sounds, soft nasal breathing from (s1), subtle fabric rustle against the mattress edge, quiet bedroom room tone.

Natural_sucking_motion: 80%
Natural_bobbing_head_while_sucking: 65%
VFX_natural_devouring_penis_physics: 75%
Naturalistic_body_physics: 100%
Naturalistic_hands_physics: 100%
Naturalistic_fingers_physics: 100%
Naturalistic_water_physics: 05%

Non_diegetic_music: N/A
```

## 模式二：`ref`（多图参考 ／ 性器子主体拆分 ／ 音色与配乐卡点）

- **角色唤醒绑定句**：有参考图的角色在 `subject_definitions:` 写：
  `<Subject 1> is the woman with exact face, age, body, hair, hairstyle, skin-tone, body-size, and outfit from <Picture 1>, characters are brought to life.`
  同场无参考图的次要角色写 `without a reference.`。正文前可加对齐句：`For the target video, at 0.00 seconds into the target video, (s1) and (s2) are fully referenced.`。
- **NSFW 性器／局部独立子主体拆分（高精细接触镜推荐）**：当参考槽挂有勃起阴茎或女性私处特写板 `<Picture M>` 时（遵守 [modules/type-adult.md](../modules/type-adult.md) 的槽位隔离原则，单人私处板不与多人交合灰模混挂），在 `subject_definitions:` 将器官拆成独立 `<Subject K>` 隶属到对应角色，正文中直接指派 `(sK)` 参与互动：
  - 男性性器子主体：`<Subject 2> is (s1)'s erect penis, mushroom-shaped glans, and testicles from <Picture 2>.`
  - 女性私处子主体：`<Subject 2> is (s1)'s wet juicy pussy, defined pussy-lips, and anus from <Picture 2>.`
- **环境参考与 3D 灰模姿势图隔离**：环境图 `<Picture E>` 点名 2–3 个具体家具与光源地标；末位若挂 3D 无五官灰模姿势图 `<Picture K>`，在 `[Shot 1]` 首句写明：`following only the camera perspective, body posture, and coupling geometry from <Picture K> while ignoring its grey clay mannequin surface and simple block furniture.`。
- **挂 `<Audio N>` 音色参考**：在 `subject_definitions:` 下方补写 `<Audio 1> provides the vocal timbre, pitch, and delivery style for (s1) without copying the audio signal.`，台词写 `In the vocal timbre of <Audio 1>, (s1) (soft breathy tone, half-lidded eyes) says: <d>[Japanese] …</d>`。
- **三种时间推进模式（依镜头需求择一）**：
  1. **单镜连续顺叙（默认最稳）**：`[Shot 1]` 不标细碎秒数，顺叙固定支撑点上的连续动作与视线转换。
  2. **段内秒数节拍（单镜蓄力→爆发）**：同一镜内有明确阶段转折（如快节奏抚弄后在特定时刻潮吹喷涌或停顿抬头），用 `At 0.00s … Within the first 1.5 seconds … At 3.0s …` 分段推进。
  3. **多镜硬切或配乐卡点（动作快剪／MV）**：同组参考素材内多镜快切写 `SHOT 1 — …` 接 `HARD CUT — SHOT 2 — …`；挂配乐 `<Audio 1>` 卡点运镜时，在音效段写 `Background music is <Audio 1>. The camera tracks (s1) in sync with <Audio 1>: on every strong bass hit, the camera makes a quick push-in; during melodic phrasing, it arcs smoothly around her.`。

### 多图参考＋性器子主体拆分范例（`ref` 模式）

```text
subject_definitions:
<Subject 1> is the woman with exact face, age, body, hair, hairstyle, skin-tone, body-size, and sheer black stockings from <Picture 1>, characters are brought to life.
<Subject 2> is (s1)'s wet juicy pussy, defined glistening pussy-lips, and anus from <Picture 2>.

Photorealistic, warm dim bedside lamp light, high res, detailed, real-life footage, real living person. Anatomically consistent adult characters, natural human proportions, realistic skeletal structure, accurate joint articulation, physically believable weight distribution, subtle muscle contractions, natural skin compression, and realistic body movement.

For the target video, at 0.00 seconds into the target video, (s1) and (s2) are fully referenced.

[Shot 1]
Tight low-angle close-up on the dark grey linen bed from <Picture 3>, framing (s1)'s hips, sheer-stockinged thighs spread wide on the mattress, and fully exposed wet pussy from (s2) while her upper back rests against stacked pillows. At 0.00s her right middle and index fingers rub rapidly in wet circular motions between the glistening pink pussy-lips of (s2) while her left hand grips the crumpled bedsheet beside her hip, her lower stomach tensing and thighs trembling softly. Within 2.0 seconds her pelvis arches sharply off the mattress and a pressurized continuous stream of water-clear transparent squirt liquid spurts outward from (s2), splashing across her inner thighs and darkening the linen sheet beneath her until the end of the clip.

overall_soundscape: heavy rhythmic breathing and sharp gasps from (s1), rapid wet finger rubbing sounds, clear continuous liquid spurting and splashing onto fabric bedding, quiet indoor room tone.

VFX_naturalistic_tender_pussy_physics: 75%
Naturalistic_body_physics: 100%
Naturalistic_legs_physics: 100%
Naturalistic_hands_physics: 100%
Naturalistic_fingers_physics: 100%
Naturalistic_urinating_water_physics: 50%
Naturalistic_water_physics: 50%
natural_sexually_sitting_pose: 100%

Non_diegetic_music: N/A
```

## 按需选配：尾端百分比物理滑杆速查表（`*_physics: XX%`）

放在 `overall_soundscape:` 之后、`Non_diegetic_music:` 之前，每段只挑与画面直接相关的 **4–8 条**（统一用 `VFX_`，不堆砌无关词）：

- **通用人体骨骼、重量与动作力学（SFW／NSFW 通用基底）**
  - `Naturalistic_body_physics: 100%`、`Naturalistic_legs_physics: 100%`、`Naturalistic_arms_physics: 100%`
  - `Naturalistic_hands_physics: 100%`、`Naturalistic_fingers_physics: 100%`（手部抓握、抚弄或持物特写必带）
  - `VFX_realistic_gravity_and_weight_physics: 80%`、`Real_time_motion: 100%`（跑跳、跨坐起伏、身体下沉压床）
- **手持运镜与配乐节拍同步（Vlog／动作／MV）**
  - `Camera_shaking: 20%`（手持微晃设 `15%–35%`；固定机位或滑轨镜头不写）
  - `SFX_body_motion_match_song: 88%`、`VFX_body_motion_physics_match_song: 88%`（仅在挂 `<Audio N>` 音乐卡点时启用）
- **对白清晰度与微表情（含 `<d>` 说话镜）**
  - `Dialogue_Clarity: 100%`、`Natural_Human_Speech: 100%`、`Character_Voice_Consistency: 100%`
  - `Facial_Microexpressions: 100%`、`Clothing_And_Movement_Sounds: 85%`
- **NSFW 口部奉仕、私处张力与体液水柱两档开关**
  - 口交奉仕：`Natural_sucking_motion: 80%`、`Natural_bobbing_head_while_sucking: 65%`、`VFX_natural_devouring_penis_physics: 75%`
  - 女性私处与体位张力：`VFX_naturalistic_tender_pussy_physics: 75%`、`natural_sexually_pose: 100%`（坐姿可写 `natural_sexually_sitting_pose: 100%`）
  - **体液流体两档控制（严禁混用错档）**：
    - *第一档（常规湿润／唾液拉丝，防乱喷水）*：`Naturalistic_water_physics: 05%`（或直接省略水物理项）。
    - *第二档（高压潮吹喷涌／大剂量射精水柱）*：Ultima 对 `urinating_water` 水流物理响应极强，正文写明 `pressurized stream` 与流体透明／半透明质感，滑杆同步开启 `Naturalistic_urinating_water_physics: 50%` 与 `Naturalistic_water_physics: 50%`。
