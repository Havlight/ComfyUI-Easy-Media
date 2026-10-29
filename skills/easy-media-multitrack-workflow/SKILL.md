---
name: easy-media-multitrack-workflow
description: "Plan, build and run ComfyUI Easy Media MultiTrack (MiniMax H3) videos: write H3 plan files (tasks, prompts, reference media, timeline audio and video), turn them into workflows from the user's templates, queue them, wait for the results and optionally review frames; or patch workflow JSON directly for resolution, Project settings and node changes."
---

# Easy Media MultiTrack 工作流

用 H3 规划文件描述影片内容，套到用户准备好的模板工作流后在 ComfyUI 执行；规划文件管不到的设置，直接修改工作流 JSON。

## 必守规则

- **执行要明确授权**：只有用户说「执行、生成、出片、排队」时才排入队列；「做一个工作流／规划」只产生文件。
- **任务 id 固定**：规划里每段任务都写自己的 `id`（如 `s01`、`s02`），修改时保留。已存结果依 id 对应，没有 id 每次修改都会全部重新生成。
- **路径**：素材写绝对路径。ComfyUI 读不到时（agent 在 WSL、容器或另一台机器），`plan_workflow.py` 会自动上传。
- **不要卡着等生成**：`submit_workflow.py --wait 540` 最多等 9 分钟；返回码 3 表示还在跑，之后用 `--status <request_id> --wait 540` 继续等，不要重新提交。
- **重跑上限**：同一个需求最多自动重新生成 3 轮，再要重跑先问用户。
- **看画面检查默认关闭**：只有 `config.yaml` 的 `AGENT_FRAME_REVIEW: true` 或用户要求检查结果时才用 `review_frames.py`；模型不能看图就不要用，改为回报输出文件。每轮只看这轮重新生成的段落。
- **环境**：需要一个开着的 ComfyUI 页面。Codex 等有沙盒的工具要允许连到 ComfyUI 地址（本机网络）。地址依序取用户给的地址、节点包 `config.yaml` 的 `COMFYUI_URL`、`http://127.0.0.1:8188`。

## 选路线

1. **规划文件（默认）**：任务、秒数、提示词、参考素材、时间轴上的歌曲或影片。写规划文件（[references/plan-file.md](references/plan-file.md)），按 [references/automation.md](references/automation.md) 转换、执行、等待与检查。
2. **直接改工作流**：分辨率、Project 采样设置、片段 LoRA、字幕、loader／attention 替换，或用户要求修改某个工作流文件。按下方「直接改工作流」。

用户没有给模板时，两条路线都以 `assets/templates/v1.3.0-blank-workflow.json` 为基线；给了工作流或模板时，以它为唯一基线，不用内置模板覆盖。

## 自动循环

1. `plan_workflow.py probe` 读出歌曲、影片长度，据此分段。
2. 写规划文件。
3. `plan_workflow.py convert 模板 规划 --project-name 名称`：有错误就修正规划再转换；确认警告、已有结果的段落与开始段。
4. 用户授权执行后：`submit_workflow.py 输出.workflow.json --wait 540`。
5. （可选）`review_frames.py --project 名称 --tasks 重跑的段落`。
6. 需要修改时改规划，回到第 3 步；没改到的段落沿用已存结果，从第一个需要重跑的段落开始。

## 关键决策

- **主体或局部替换**：用户提供视频作为主体或局部替换参考时，全部目标任务用 `continuity: context_drift`，该视频轨设 `audio_locked: true` 保留画面时间线。未另锁音频时沿用视频原声；另有锁定音频轨时优先用它的音频。用户没给片段长度时按连续 10 秒拆分，末段用剩余时长。
- **主音频／影片原声**：需要原样沿用并驱动节奏（MV、歌词视频、旁白）时，在那条音频或视频轨设 `audio_locked: true`。同类型最多锁一条；音频与视频可各锁一条，并存时音频优先，视频继续约束画面。主音频不只一个候选时先问用户。
- **接缝**：`context_drift` 任务默认 `seam: "soft"`，用户要求接缝贴紧上一段时用 `"clean"`。
- **公用媒体**：多段要用同一张图、同一段音频或影片时设 `shared: true`。公用图片排在各段自己的图片之前，每段合计最多 9 张；每条音频或视频轨最多一个公用片段。
- **分辨率**：用户没提就保留模板的设置；要改时走直接改工作流，规则见 [references/multitrack-schema.md](references/multitrack-schema.md)。

## 直接改工作流

1. 保留源文件，`scripts/patch_workflow.py inspect WORKFLOW.json` 确认连接的 `easy multiTrackEditor` / `easy multitrackProject`、分辨率、format、FPS、轨道和项目参数。
2. 改时间线、分辨率或音频角色时读 [references/multitrack-schema.md](references/multitrack-schema.md)，写完整的 `track_data`：保留既有对象的 ID 和未知字段，只为新增对象生成 UUID。
3. loader／attention 替换按 [references/template-workflow.md](references/template-workflow.md)；其他情况不改节点图。
4. 写只含实际变更的补丁 plan，先 dry-run，再写入新文件并重新 inspect：

   ```bash
   python3 scripts/patch_workflow.py apply SOURCE.json --plan PLAN.json
   python3 scripts/patch_workflow.py apply SOURCE.json --plan PLAN.json --output RESULT.json --write
   python3 scripts/patch_workflow.py inspect RESULT.json
   ```

5. 复核节点数、link 数和非目标节点不变量。脚本报告歧义、schema 不兼容或媒体无效时停止，不绕过校验。上传素材见 [references/upload-api.md](references/upload-api.md)，提交与底层接口见 [references/execution-api.md](references/execution-api.md)。

## 交付

说明用了哪个模板或基线、输出文件、项目名称、任务与时间轴摘要、锁定轨与公用媒体、开始段，以及有没有上传素材。执行过的再报告 `request_id`、`prompt_id`、最终状态和输出文件；不以「已排队」冒充「已生成」。
