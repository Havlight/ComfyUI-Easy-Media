---
name: easy-media-multitrack-workflow
description: "Plan, build and run ComfyUI Easy Media MultiTrack (MiniMax H3) videos: read the workflow tab open in ComfyUI, write H3 plan files (tasks, prompts, reference media, timeline audio and video) into it, run it, wait for the results, keep project records and optionally review frames; or patch workflow JSON files for resolution, Project settings and node changes."
---

# Easy Media MultiTrack 工作流

用 H3 规划文件描述影片内容，直接写进用户在 ComfyUI 开着的工作流分页再执行；分页里没存的修改也读得到。规划文件管不到的设置（模型、分辨率、采样方式等）由用户在分页里设定。

## 必守规则

- **用哪个分页要问**：用户没说用哪个分页或工作流时，用 `plan_workflow.py tabs` 列出分页后问用户，不要自己挑。用户说「用默认」才用 `assets/templates/v1.3.0-blank-workflow.json`（`apply --file` 开新分页），并提醒用户它的模型文件名可能跟本机不同。
- **执行要明确授权**：只有用户说「执行、生成、出片、排队」时才排入队列；「规划、写进去」只改分页。
- **任务 id 固定**：规划里每段任务都写自己的 `id`（如 `s01`、`s02`），修改时保留。已存结果依 id 对应。
- **保留用户在分页里的修改**：`read` 与 `apply` 会列出 `tab_edits`（用户在上次 apply 之后改过的段落）。把这些修改写进规划后才用 `--overwrite-tab-edits` 套用，不要直接盖掉。
- **路径**：素材写绝对路径。ComfyUI 读不到时（agent 在 WSL、容器或另一台机器），脚本会自动上传。
- **生成时不要空等**：工具能在后台执行指令并在结束时通知的（如 Claude Code 的背景指令），用 `submit_workflow.py --tab 分页 --until-done` 放后台，结束前不要查询、不要另外排程。不能的，用 `--wait 540`；返回码 3 表示还在跑，之后 `--status <request_id> --project 名称 --wait 540` 继续等，不要重新提交。
- **一个专案一次一个执行**：上一次还没结束就不要再排同一个专案。
- **重跑上限**：同一个需求最多自动重新生成 3 轮，再要重跑先问用户。
- **看画面检查默认关闭**：只有 `config.yaml` 的 `AGENT_FRAME_REVIEW: true` 或用户要求检查结果时才用 `review_frames.py`；模型不能看图就不要用，改为回报输出文件。每轮只看这轮重新生成的段落。
- **环境**：需要一个开着的 ComfyUI 页面。Codex 等有沙盒的工具要允许连到 ComfyUI 地址（本机网络）。地址依序取用户给的地址、节点包 `config.yaml` 的 `COMFYUI_URL`、`http://127.0.0.1:8188`。

## 选路线

1. **分页 + 规划文件（默认）**：任务、秒数、提示词、参考素材、时间轴上的歌曲或影片、专案名称、从第几段跑。规划格式见 [references/plan-file.md](references/plan-file.md)，分段与提示词写法见 [references/writing.md](references/writing.md)，读分页、套用、执行、等待与纪录见 [references/automation.md](references/automation.md)。
2. **直接改工作流文件**：用户要求修改某个工作流文件，或要 agent 改分辨率、Project 采样设置、片段 LoRA、字幕、loader／attention 替换时。按下方「直接改工作流文件」。

## 自动循环

1. `plan_workflow.py tabs` 确认用哪个分页（用户没指定就问）。
2. `plan_workflow.py read --tab 分页 -o current.json`：看 Project、Editor、分辨率与目前的规划；有 `tab_edits` 时把用户的修改带进规划。
3. `plan_workflow.py probe` 读出歌曲、影片长度，据此分段。
4. 写规划文件；分段与提示词按 [references/writing.md](references/writing.md)，提示词用对应 mode 的 H3 指南写。
5. `plan_workflow.py apply 规划 --tab 分页 --project-name 名称`：有错误就修正规划再套用；确认警告、每段的标签对应（`labels`）、已有结果的段落与要跑的范围。
6. 用户授权执行后：`submit_workflow.py --tab 分页 --until-done`（放后台）或 `--wait 540`。
7. （可选）`review_frames.py --project 名称 --tasks 重跑的段落`。
8. 需要修改时改规划，回到第 5 步（用户在分页里动过就先回第 2 步）。
9. 用 `plan_workflow.py record` 更新专案纪录的 `README.md`。

## 关键决策

- **专案名称**：同一支影片沿用同一个名称；Project 每次生成都另存新版本，不会覆盖旧影片，没改到的段落沿用已存结果。用户要求，或故事、素材、风格大改到算另一支影片时，换新名称并告诉用户。
- **跑哪些段**：`apply` 默认从第一个没有结果的段落一直跑到最后。只改了一小段时自己设范围，例如只重跑第 3 段用 `--start 3 --count 1`；后面接着它的段落（`context`、`context_drift`，或图片用了 `previous_frame`）要一起跑。
- **分辨率**：改了分辨率后，从已存段落接续的段落会接不上。`apply` 会警告，可以继续跑，但要告诉用户，必要时从第 1 段跑。模型、步数这类设定变了不影响接续。
- **主体或局部替换**：用户提供视频作为主体或局部替换参考时，全部目标任务用 `continuity: context_drift`，该视频轨设 `audio_locked: true` 保留画面时间线。未另锁音频时沿用视频原声；另有锁定音频轨时优先用它的音频。用户没给片段长度时按连续 10 秒拆分，末段用剩余时长。
- **主音频／影片原声**：需要原样沿用并驱动节奏（MV、歌词视频、旁白）时，在那条音频或视频轨设 `audio_locked: true`。同类型最多锁一条；音频与视频可各锁一条，并存时音频优先，视频继续约束画面。主音频不只一个候选时先问用户。
- **接续**：换场景用 `shot`，接着上一段用 `context_drift`；`context` 只在用户要求时用。`context_drift` 默认 `seam: "soft"`，用户要求接缝贴紧上一段时用 `"clean"`。
- **公用媒体**：多段要用同一张图、同一段音频或影片时设 `shared: true`。公用图片排在各段自己的图片之前，每段合计最多 9 张；每条音频或视频轨最多一个公用片段。
- **版本**：用户要某段用旧版本时，请用户在 Project Video Combine 节点切换该段的版本；接在它后面的段落要重跑。所有段落共用 Project 的 seed；要比较提示词效果时，请用户固定 seed。

## 直接改工作流文件

1. 保留源文件，`scripts/patch_workflow.py inspect WORKFLOW.json` 确认连接的 `easy multiTrackEditor` / `easy multitrackProject`、分辨率、format、FPS、轨道和项目参数。
2. 改时间线、分辨率或音频角色时读 [references/multitrack-schema.md](references/multitrack-schema.md)，写完整的 `track_data`：保留既有对象的 ID 和未知字段，只为新增对象生成 UUID。
3. loader／attention 替换按 [references/template-workflow.md](references/template-workflow.md)；其他情况不改节点图。
4. 写只含实际变更的补丁 plan，先 dry-run，再写入新文件并重新 inspect：

   ```bash
   python3 scripts/patch_workflow.py apply SOURCE.json --plan PLAN.json
   python3 scripts/patch_workflow.py apply SOURCE.json --plan PLAN.json --output RESULT.json --write
   python3 scripts/patch_workflow.py inspect RESULT.json
   ```

5. 复核节点数、link 数和非目标节点不变量。脚本报告歧义、schema 不兼容或媒体无效时停止，不绕过校验。要在 ComfyUI 打开结果时用 `submit_workflow.py RESULT.json --no-queue`（开新分页）。上传素材见 [references/upload-api.md](references/upload-api.md)，提交与底层接口见 [references/execution-api.md](references/execution-api.md)。

## 交付

说明用了哪个分页、专案名称、任务与时间轴摘要、锁定轨与公用媒体、要跑的范围，以及有没有上传素材。执行过的再报告 `request_id`、`prompt_id`、最终状态、输出文件与专案纪录的位置；不以「已排队」冒充「已生成」。
