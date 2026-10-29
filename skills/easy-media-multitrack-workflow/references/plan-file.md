# H3 规划文件（导入／导出）

MultiTrack Editor 在 MiniMax 格式下，工具栏有「导出规划」与「导入规划」两个按钮。规划文件是任务轨的 JSON，适合人工或会操作电脑的 agent 编辑后导回：导入时先显示变更预览，确认后才套用，套用算一步，可以 undo。

只编辑任务轨；时间轴的影音轨、Project 节点参数与片段 LoRA 不在规划里。需要改这些时，改用 `scripts/patch_workflow.py` 修改工作流。

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
- `prompt`：写入目前选用的 A/B 提示词。用官方标签 `<Picture N>`、`<Video N>`、`<Audio N>` 引用素材；编号规则与 Editor 相同，预览会提醒对不上的标签。

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

本机路径必须是绝对路径。文件找不到、格式不支持或网址下载失败时，预览会列为错误并禁止套用。

## 建议流程

1. 在 Editor 按「导出规划」，以导出的文件为基础修改，保留既有任务的 `id`，已存的生成结果才会沿用。
2. 按「导入规划」选文件，检查预览：变更类型、秒数、参考数量，以及“已存结果需要重新生成”的提示。
3. 有错误时修正文件后重新导入；只有警告时可以直接套用。
