# H3 规划文件（导入／导出）

MultiTrack Editor 在 MiniMax 格式下，工具栏有「导出规划」与「导入规划」两个按钮。规划文件是任务轨与时间轴影音轨的 JSON，适合人工或会操作电脑的 agent 编辑后导回：导入时先显示变更预览，确认后才套用，套用算一步，可以 undo。导出的文件导入另一个工作流，会重建同样的任务与影音轨。

字幕轨、系统提示词、提示词 B、分辨率、Project 节点参数与片段 LoRA 不在规划里，导入时保持原样。需要改这些时，改用 Editor 或 `scripts/patch_workflow.py`。

## 格式

```json
{
  "format": "easy-media-h3-plan",
  "version": 1,
  "frame_rate": 24,
  "tasks": [
    {
      "seconds": 6,
      "mode": "ref",
      "continuity": "shot",
      "prompt": "<Picture 1> 站在 <Picture 2> 前，用 <Audio 1> 的声音说话",
      "images": ["C:/素材/alice.png", "C:/素材/castle.jpg"],
      "audios": [{ "file": "D:/voice/line01.wav", "fit": false }]
    },
    {
      "id": "导出时带上的既有任务 id",
      "seconds": 5,
      "continuity": "context_drift",
      "images": ["previous_frame", "C:/素材/alice.png"],
      "prompt": "……"
    }
  ],
  "tracks": [
    { "type": "audio", "audio_locked": true, "clips": [{ "file": "D:/music/song.wav", "at": 1 }] }
  ]
}
```

## 任务

- 文件代表**完整的任务列表**，顺序就是时间轴上的顺序。
  - 有 `id` 的任务更新原本那段；没有 `id` 的是新任务；文件里没出现的任务会被删除。
  - 更新时没写的字段保持原值；新任务没写的字段用默认值（`mode: default`、`continuity: shot`、空提示词）。
- `seconds`：新任务必填。会依 H3 的帧格对齐，例如 5 秒的独立镜头会变成 124 帧（约 5.17 秒），预览会列出调整。
- `gap_before`：与前一段之间的空白秒数，默认 0；接续段（`context`、`context_drift`）一定紧接前一段。
- `mode`：`default`、`ref`、`edit`、`l2v`、`passthrough`。
- `continuity`：`shot`、`context`、`context_drift`；第一段只能是 `shot`。
- `seam`（只用于 `context_drift`）：接缝的处理。新的 `context_drift` 任务不写时用 `"soft"`；用户要求接缝贴紧上一段时写 `"clean"`。导出文件里若是其他写法，保持原样。
- `prompt`：写入目前选用的提示词。用官方标签 `<Picture N>`、`<Video N>`、`<Audio N>` 引用素材；编号规则与 Editor 相同，预览会提醒对不上的标签。
- 导出文件里的 `ref_image_size` 保持原样。

## 参考素材

`images`、`videos`、`audios` 是列表；写了就整份取代，没写就保持原样。每一项可以是字符串或对象：

| 写法 | 意思 |
|---|---|
| `"C:/素材/a.png"` 或 `{"file": "C:/素材/a.png"}` | 本机文件，复制到 `input/easy_media/imports/` 并以内容哈希命名，重复导入只存一份 |
| `"https://…"` 或 `{"file": "https://…"}` | 下载后同样存进 `input/easy_media/imports/` |
| `{"input": "子目录/a.png"}`、`{"output": "a.mp4"}` | ComfyUI input／output 里已有的文件 |
| `{"local": "C:/素材/a.png"}` | 本机文件留在原处，不复制 |
| `{"url": "https://…"}` | 保留为网址，不下载 |
| `"previous_frame"` | 上一段尾帧（只用于 `images`，第一段不能用） |
| `{"track": "<轨道 id>"}`、`{"track": "<轨道 id>", "clip": "<公用片段 id>"}` | 时间轴参考的占位项（只用于 `videos`、`audios`），用来调整顺序与设置 |

对象可以加：

- `muted: true`：只在这一段排除。时间轴占位项的 `muted: false` 会让静音或锁定音频的音频轨也在这一段作参考。
- `shared: true`：设为公用，加到所有片段（占位项不适用）。
- `fit`（只用于音频）：`true` 截到片段长度；上传的音频默认整段、最长 15 秒，时间轴音频默认截到片段。
- `soundtrack`（只用于视频）：是否带原声并占用一个 `<Audio N>`。

本机路径必须是绝对路径。文件找不到、格式不支持或网址下载失败时，预览会列为错误并禁止套用。占位项的轨道或公用片段不存在时，这一项会略过并列为警告。

## 时间轴影音轨

`tracks` 列出全部视频与音频轨，顺序就是时间轴上的顺序（依序叫 Video 0、Video 1、Audio 0……）。没写 `tracks` 时影音轨完全不动；写了就整份取代：没列出的轨道与片段会被删除。每种最多 3 条。

agent 可以做的只有这些：

- **放上整个文件**：在某条轨道的 `clips` 加 `{"file": "<路径或网址>", "at": <任务>}`。`at` 是任务 id，或任务在文件里的序号（1 是第一段，也就是专案开头）。片段从那一段的开头开始，长度是整个文件。
- **搬动现有片段**：把它写成 `{"id": "<片段 id>", "at": <任务>}`，人做过的裁切与设置都保留。
- **删除**：把片段或整条轨道从列表拿掉。
- **轨道设定**：`"audio_locked": true` 锁定这条轨道（音频轨提供锁定音频，视频轨锁定画面时间线；每种最多一条），`"muted": true` 静音。不写就是不锁定、不静音。
- **新轨道**：写 `type`（`"video"` 或 `"audio"`）和 `clips`；可以自己取一个 `id`，任务的占位项就能指向它。

导出文件里片段的 `start`、`end`、`from`（从素材第几秒开始）、`volume`、`shared` 和轨道的 `volume` 是还原用的资料，保持原样，不要自行裁切或调整；裁切、分割、音量这类编辑由人在 Editor 里做。同一条轨道的片段不能重叠，预览会列为错误。

## 导入到另一个工作流

导出的文件可以导入别的工作流，重建同样的任务与影音轨。任务 id 都对不上时，全部任务作为新任务加入，现有任务被删除；预览会用一条警告说明。规划里没有视频轨时，目标工作流的第一条视频轨会保留并清空。

## 建议流程

1. 在 Editor 按「导出规划」，以导出的文件为基础修改，保留既有任务与片段的 `id`，已存的生成结果才会沿用。
2. 按「导入规划」选文件，检查预览：任务的变更、秒数、参考数量，影音轨的片段，以及“已存结果需要重新生成”的提示。
3. 有错误时修正文件后重新导入；只有警告时可以直接套用。
