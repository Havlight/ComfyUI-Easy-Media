适用：2D 动画、日系动画、anime 风格；或参考图是 2D 动画角色

# 2D 日系动画

## 风格句

每段 `[Shot 1]` 开头（ref 模式写在 `detailed_description` 开头），每段相同：

```text
2D anime style, <类型与气氛>, <时间与地点>.
```

类型与气氛照用户的内容写，例如 `delicate slice-of-life`、`cinematic drama`、`psychological horror`；接着写这个场景的光线。

## 角色与声音

- 角色参考图写得很硬：`whose exact appearance is fully defined by <Picture 1>`。`ref` 模式写在 `subject_definitions`（`<Subject 1> is the girl in <Picture 1>, whose exact appearance is fully defined by <Picture 1>.`），正文用 `<Subject 1> (S1)`。
- 几个人时之后用看得到的特征区分（`the black-haired girl`、`the black-haired boy`）。
- 有音色参考时，正文说话前写 `Her voice timbre is referenced from <Audio 1>.`

## 表演与台词

- 情绪写成看得到的身体变化：眉头收紧、下颚绷紧、眼眶泛泪、嘴唇几乎不动、手按胸口、歪头。
- 每句台词前写语气：`in a low, controlled, angry voice`、`murmurs softly to herself, almost swallowed by the rain`。
- 台词拆成短句，每句一个 `<d>`，中间写一个小动作或停顿（`She pauses, eyes still on the distance.`、`A faint breath.`）。全部台词写完后可以加一句 `No other dialogue.`。
- 日文台词照原文：`<d>[Japanese] 雨、やまないね。</d>`。

## 分镜

- 可以多切：对话戏 2–3 秒一镜很常见，十几秒切五六镜也能用。切镜语法照指南（`[Shot N] At MM:SS.mmm`）。
- 常用镜头：过肩、反应特写、45 度仰拍全身、极低角度仰拍、俯视远景加前景遮挡（树枝）、侧面中景、POV。
- 双人戏写明左右（`the girl on the left, the boy on the right`），之后各镜照这个位置。
- 上一镜的动作在下一镜接上：`The boy lowers the hand he raised in the previous shot.`
- 局部特写写清楚画面边界：`an extreme close-up between her mouth and chest, with everything above her mouth out of frame`。

## 声音

- 环境声写成跟着动作的具体声音（`the tiny squeak of her fingertip sliding on wet glass`），人声远近也写出来（`close and intimate`）。
- 配乐写乐器与速度，再写它在哪个剧情时间点起伏、怎么结束，结束在这段的确切秒数：`rises slightly with her whispered line, and fades on a soft unresolved note at 7.000 seconds`、`cuts off sharply at 7.000 seconds`。

## 范例

`ref` 模式，7 秒，两镜，角色参考图 `<Picture 1>`、音色参考 `<Audio 1>`：

```text
subject_definitions:
<Subject 1> is the girl in <Picture 1>, whose exact appearance is fully defined by <Picture 1>.
<Audio 1> is the voice-timbre reference for <Subject 1> (S1).

summary:
[reference generation + audio reference] <Subject 1> sits on a bedroom windowsill on a rainy afternoon and murmurs to herself in two shots; <Audio 1> provides her voice timbre.

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - her face, hair and outfit follow <Picture 1>.
<Audio 1>: reference - her voice follows the timbre of <Audio 1> without copying its words.

detailed_description:
2D anime style, delicate slice-of-life, a rainy afternoon in a dim bedroom. Rain streams down the window; cool gray-blue light fills the room.
[Shot 1] A medium close-up frames <Subject 1> (S1) sitting sideways on the windowsill, one knee drawn up, shoulder resting against the frame. She watches the rain, breathing slowly.
[Shot 2] At 00:04.000, the shot cuts to a closer three-quarter profile from the same side. She lowers her gaze and traces a raindrop on the glass with one fingertip. Her voice timbre is referenced from <Audio 1>. <Subject 1> (S1) murmurs softly to herself, almost swallowed by the rain: <d>[Japanese] 雨、やまないね。</d> A faint breath. <d>[Japanese] ……私、ここにいていいのかな。</d> Her lips barely move; her eyes glisten. The frame ends at 7.000 seconds on her fingertip resting against the cold glass. No other dialogue.

overall_soundscape:
Steady rain tapping and streaming against the window, distant low thunder, quiet bedroom room tone, her soft breathing, and the tiny squeak of her fingertip sliding on wet glass. Her voice is close and intimate.

non_diegetic_music:
A sparse piano melody over sustained strings at a very slow tempo. It stays under the rain, rises slightly with her whispered line, and fades on a soft unresolved note at 7.000 seconds.
```
