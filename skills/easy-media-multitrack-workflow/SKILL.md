---
name: easy-media-multitrack-workflow
description: "Plan, build and run ComfyUI Easy Media MultiTrack (MiniMax H3) videos: read the workflow tab open in ComfyUI, write H3 plan files (tasks, prompts, reference media, timeline audio and video) into it, run it, wait for the results, keep project records and optionally review frames; or patch workflow JSON files for resolution, Project settings and node changes."
---

# Easy Media MultiTrack 工作流

用 H3 规划文件描述影片内容，直接写进用户在 ComfyUI 开着的工作流分页再执行；分页里没存的修改也读得到。规划文件管不到的设置（模型、分辨率、采样方式等）由用户在分页里设定。

## 必守规则

- **用哪个分页要问**：用户没说用哪个分页或工作流时，用 `plan_workflow.py tabs` 列出分页后问用户，不要自己挑。用户说的名称跟分页对不上，或有几个名称相近的分页时，也先问。用户说「用默认」才用 `assets/templates/v1.3.0-blank-workflow.json`（`apply --file` 开新分页），并提醒用户它的模型文件名可能跟本机不同。
- **执行要明确授权**：只有用户说「执行、生成、出片、排队」时才排入队列；「规划、写进去」只改分页。
- **任务 id 固定**：规划里每段任务都写自己的 `id`（如 `s01`、`s02`），修改时保留。已存结果依 id 对应。
- **以分页为准**：每次修改都先 `read --tab 分页 -o current.json`，在读出来的规划上改再 `apply`，用户在分页里改过的地方（提示词、秒数等）才会留着。不要拿自己之前的规划文件直接套。
- **路径**：素材写绝对路径。ComfyUI 读不到时（agent 在 WSL、容器或另一台机器），脚本会自动上传。
- **生成时不要空等**：工具能在后台执行指令并在结束时通知的（如 Claude Code 的背景指令），用 `submit_workflow.py --tab 分页 --until-done` 放后台，结束前不要查询、不要另外排程。不能的，用 `--wait 540`；返回码 3 表示还在跑，之后 `--status <request_id> --wait 540` 继续等，不要重新提交。
- **一个专案一次一个执行**：上一次还没结束就不要再排同一个专案。
- **重跑上限**：同一个需求最多自动重新生成 3 轮，再要重跑先问用户。
- **看画面检查默认关闭**：只有 `config.yaml` 的 `AGENT_FRAME_REVIEW: true` 或用户要求检查结果时才用 `review_frames.py`；模型不能看图就不要用，改为回报输出文件。每轮只看这轮重新生成的段落。
- **环境**：需要一个开着的 ComfyUI 页面。Codex 等有沙盒的工具要允许连到 ComfyUI 地址（本机网络）。地址依序取用户给的地址、节点包 `config.yaml` 的 `COMFYUI_URL`、`http://127.0.0.1:8188`。

## 选路线

1. **分页 + 规划文件（默认）**：任务、秒数、提示词、参考素材、时间轴上的歌曲或影片、专案名称、从第几段跑。规划格式见 [references/plan-file.md](references/plan-file.md)，分段与提示词写法见 [references/writing.md](references/writing.md)，读分页、套用、执行、等待与纪录见 [references/automation.md](references/automation.md)。
2. **直接改工作流文件**：用户要求修改某个工作流文件，或要 agent 改分辨率、Project 采样设置、片段 LoRA、字幕、loader／attention 替换时。按下方「直接改工作流文件」。

## 自动循环

1. `plan_workflow.py tabs` 确认用哪个分页（用户没指定就问）。
2. `plan_workflow.py read --tab 分页 -o current.json`：看 Project（含 `project_save`）、Editor、分辨率、影音轨、输出节点、目前的规划，以及每段已存结果是否还跟内容相符（`status`）。分页内容跟专案纪录对不上时先问用户，要还原就 `apply --from-run last`。
3. `plan_workflow.py probe` 读出歌曲、影片长度，据此分段。
4. 在 `current.json` 上改出规划（空白分页就新写）；分段与提示词按 [references/writing.md](references/writing.md)，提示词用对应 mode 的 H3 指南写。
5. `plan_workflow.py apply 规划 --tab 分页 --project-name 名称`：有错误就修正规划再套用；确认警告、每段的标签对应（`labels`）、每段的 `status` 与要跑的范围。
6. 用户授权执行后：`submit_workflow.py --tab 分页 --until-done`（放后台）或 `--wait 540`。
7. `plan_workflow.py project --name 名称` 确认每段用哪一版、接在哪一版后面、有没有备用方式；（可选）`review_frames.py --project 名称 --tasks 重跑的段落`。
8. 需要修改时回到第 2 步，读出目前的规划再改。
9. 用 `plan_workflow.py record` 更新专案纪录的 `README.md`。

## 关键决策

- **专案名称**：同一支影片沿用同一个名称，没改到的段落沿用已存结果。用户要求，或故事、素材、风格大改到算另一支影片时，换新名称并告诉用户。
- **保存方式**：Project 的 `project_save` 由用户设定，agent 不改：`new` 让重跑的段落另存新版本、旧版本保留；`override` 让重跑的段落取代目前的版本。
- **跑哪些段**：`status` 来自 Project 自己的检查：`saved` 表示已存结果跟目前内容相符，`edited`（内容改过）、`parent_changed`（接着的前一段改过或换了版本）、`missing`（没有结果）都要跑。`apply` 默认从第一个要跑的段落一直跑到最后。只改了一小段时自己设范围，例如只重跑第 3 段用 `--start 3 --count 1`；范围外还有要跑的段落时 `apply` 会警告。
- **分辨率**：`status` 只比内容，改了分辨率或采样方式（single、dual、selflift）后旧段落仍是 `saved`；整支要换就 `--start 1`。只跑后面几段时，Project 在生成前检查接续的前一段：开了 `allow_vae_fallback` 就从那段的影片重建接续，没开就报错 `VAE_FALLBACK_REQUIRED`，把错误告诉用户，由用户选从第 1 段跑或开 fallback。模型、步数变了不影响接续。
- **锁定视频（主体或局部替换、照着影片的动作）**：视频放在视频轨并设 `audio_locked: true`，画面时间线照它走；未另锁音频时沿用视频原声，另有锁定音频轨时优先用它的音频。只要参考原声、不要输出时，影片轨加 `"soundtrack": "reference"`，原声成为参考 `<Audio N>`，提示词写成参考关系，并写明成品要听到什么（新生成的音乐，或只有环境声）。被锁定的任务用 `ref` 或 `edit`。视频必须盖满每个被锁定任务的整段，接续任务还要多盖前面 39 帧（约 1.6 秒），所以第一个被锁定的任务用 `shot`、之后的用 `context_drift`，片段写 `"until": <最后一个被锁定的任务>`，让它刚好结束在那段结尾，后面的任务就不会被锁住。盖不满时 `apply` 报错，分页不变。用户没给片段长度时按连续 10 秒拆分，末段用剩余时长。
- **主音频／影片原声**：需要原样沿用并驱动节奏（MV、歌词视频、旁白）时，在那条音频或视频轨设 `audio_locked: true`。同类型最多锁一条；音频与视频可各锁一条，并存时音频优先，视频继续约束画面。主音频不只一个候选时先问用户。
- **接续**：换场景用 `shot`，接着上一段用 `context_drift`；`context` 只在用户要求时用。`context_drift` 默认 `seam: "soft"`，用户要求接缝贴紧上一段时用 `"clean"`。
- **首帧、尾帧图**：会缩放成影片画面的大小，首帧直接拉伸、尾帧等比裁切。图的比例跟影片不同时，另存一份裁成相同比例的副本再用，不改用户的原档。
- **公用媒体**：多段要用同一张图、同一段音频或影片时设 `shared: true`。公用图片排在各段自己的图片之前，每段合计最多 9 张；每条音频或视频轨最多一个公用片段。
- **版本**：用户要某段用旧版本时（`new` 才会留旧版本），请用户在 Project Video Combine 节点切换该段的版本；接在它后面的段落会变成 `parent_changed`，要重跑。所有段落共用 Project 的 seed；要比较提示词效果时，请用户固定 seed。

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

说明用了哪个分页、专案名称、任务与时间轴摘要、锁定轨与公用媒体、要跑的范围，以及有没有上传素材。执行过的再报告 `request_id`、`prompt_id`、最终状态、输出文件与专案纪录的位置；不以「已排队」冒充「已生成」。输出文件的 `type` 是 `temp` 时，提醒用户那是预览档，ComfyUI 重开会被清掉；要保留就把输出节点（如 Save Video）改成存档，或另存一份。各段影片固定存在 `output/easy_media/projects/<专案>/`。
