# 自动循环：读分页、套用、执行、等待、纪录

在 skill 目录执行下列脚本。用 ComfyUI 的 Python（读 `config.yaml` 需要 PyYAML）。需要一个开着的 ComfyUI 页面：读分页、转换规划和执行都由页面完成，转换用 Editor 自己的导入逻辑，执行用原生的「执行」。

## 准备

- 用户在 ComfyUI 开好要用的工作流分页：分辨率、Project 的采样设置、模型都设好，时间轴可以是空的。没存档也可以，agent 读得到分页里没存的修改。
- 用户没说用哪个分页时先问。用户说「用默认」时用 `assets/templates/v1.3.0-blank-workflow.json`：`apply --file` 会把它开成新分页。
- agent 只改分页的 Editor 时间轴与 Project 的专案名称、开始段、段数，算一步，用户按 Ctrl+Z 可以还原；不存档、不改其他节点。
- 分页里已有的影音轨若要保留，规划的 `tracks` 要列出它们的 `id`；没有 `id` 的轨道是新轨道，分页原有、规划没列出的轨道会被删除。

## 1. 找分页

```bash
python scripts/plan_workflow.py tabs
```

列出每个 ComfyUI 页面开着的分页：`key`（之后的 `--tab` 用它）、名称、是否在前景、有没有没存的修改。有多个页面时，后面的指令都加 `--client-id`。

## 2. 读分页

```bash
python scripts/plan_workflow.py read --tab "workflows/mmh3/MV.json" -o current.json
```

输出 Project（专案名称、开始段、段数）、Editor（format、分辨率、帧率）、每段的摘要，`-o` 写出分页目前的规划（可以直接拿来改）。`tab_edits` 列出用户在上次 apply 之后在分页里改过的地方（例如 `s03: prompt`）；`null` 表示这个专案还没 apply 过。

## 3. 读素材长度

```bash
python scripts/plan_workflow.py probe "D:/mv/song.wav" "D:/mv/dance.mp4"
```

输出每个文件的 `seconds`，视频另有 `has_audio`。用来决定分几段、每段几秒。

## 4. 写规划文件

格式见 [plan-file.md](plan-file.md)，分段与提示词见 [writing.md](writing.md)。每段任务写固定的 `id`；歌曲、影片用 `tracks` 放到某段开头。规划文件放在 agent 自己的工作目录即可，执行时脚本会把实际跑的规划存进专案纪录。

## 5. 套用到分页

```bash
python scripts/plan_workflow.py apply plan.json --tab "workflows/mmh3/MV.json" --project-name mv-alice
```

- 规划里的本机文件先复制进 ComfyUI 的 `input/easy_media/imports/<哈希>/<原文件名>`；ComfyUI 读不到的文件会上传到同一位置（`uploaded` 列出上传了哪些）。
- 用户在上次 apply 之后改过分页时（`tab_edits`），返回码 2、不套用：先 `read` 看改了什么，把修改带进规划，再加 `--overwrite-tab-edits` 套用。
- 有错误时返回码 2，输出 `errors`，分页不变：修正规划后重新套用。
- 读到分页之后用户又改了 Editor 或 Project 时，页面会拒绝套用；重新执行即可。
- 成功时分页已改好（未存档），并输出：
  - `warnings`：例如秒数为对齐帧格被调整、提示词标签找不到素材、任务超过锁定音轨的长度、分辨率跟上次执行不同；
  - `tasks`：每段对齐后的秒数、模式、`labels`（每个提示词标签对应的素材），`saved` 表示这个专案名称下已有对应的生成结果；
  - `tracks`：每条影音轨的片段与起讫秒数；
  - `run`：`start` 是第一个需要生成的段落，已写进 Project；`nothing_to_run: true` 表示全部都有结果，不需要执行。
- `--start N` 从指定段落开始，`--count N` 只跑 N 段（默认 `-1` 跑完剩下的）。Project 会把范围内的每一段都重新生成，只改了一小段时自己设范围；后面接着它的段落（`context`、`context_drift`、`previous_frame`）要包含在范围内。
- 专案名称决定结果存在哪里。同一支影片沿用原名称，没改到的段落才能沿用；Project 每次生成都另存新版本，不会覆盖旧影片。

## 6. 执行并等待

用户授权执行后：

```bash
python scripts/submit_workflow.py --tab "workflows/mmh3/MV.json" --until-done
```

- 页面切到那个分页按原生执行，再切回用户原本看的分页；返回 `request_id`、`prompt_id`。读取后有人改了 Editor 或 Project 时不执行，重新执行即可。
- 排队成功后，这次执行写进专案纪录：`history.json` 新增一笔，`plans/run-NNN.json` 存这次跑的规划。
- `--until-done` 一直等到生成结束，适合放在后台执行：工具能在指令结束时通知（如 Claude Code 的背景指令）就这样做，等待时不要查询、不要另外排程。
- 工具不能放后台时用 `--wait 540`，最多等 9 分钟，避免超过工具的指令时间上限。返回码 3 时稍后继续等，不要重新提交：

  ```bash
  python scripts/submit_workflow.py --status <request_id> --project mv-alice --wait 540
  ```

- 返回码：0 成功（`generation.outputs` 列出输出文件），2 失败（`generation.node_type`、`message`），3 还在跑（`generation.state` 为 `running` 或 `pending`）。结束时 `history.json` 记下结果与每段目前用的影片版本。

## 7. 看画面检查（选用）

默认关闭。只有 `config.yaml` 设 `AGENT_FRAME_REVIEW: true`，或用户要求检查结果（加 `--user-asked`）时才用；模型不能看图就不要用。

```bash
python scripts/review_frames.py --project mv-alice --tasks s03,s04 --user-asked
```

每段输出一张图（默认 4 格、宽 1024），存到专案纪录的 `review/`（agent 在别台机器时存到目前目录的 `review/<专案名称>/`），输出列出每张图的本机路径。看图后判断要不要修改；每轮只看这轮重新生成的段落。

## 8. 修改与重跑

改规划里对应段落（保留 `id`），回到第 5 步，沿用同一个专案名称。用户在分页里动过就先回第 2 步。同一个需求最多自动重跑 3 轮，再要重跑先问用户。

## 专案纪录

每个专案在 ComfyUI 的 `output/easy_media/agent/<专案名称>/` 有一个纪录资料夹（经由 ComfyUI 读写，agent 在别台机器也能用）：

| 文件 | 内容 | 谁写 |
|---|---|---|
| `README.md` | 影片描述、分段表（每段做什么、用哪些素材）、每次改了什么 | agent：`plan_workflow.py record README.md --project 名称` |
| `history.json` | 每次执行：编号、时间、`request_id`、`prompt_id`、分页、范围、分辨率、规划文件、结果、每段的影片 | `submit_workflow.py` |
| `plans/run-NNN.json` | 每次执行跑的规划，可以用 `apply` 套回分页还原 | `submit_workflow.py` |
| `tab-plan.json` | 上次 apply 后分页的规划，用来找出用户之后在分页里的修改 | `plan_workflow.py apply` |
| `review/` | 看画面检查的图 | `review_frames.py` |

每轮执行后更新 `README.md`，简短写下这轮改了什么。不在本机另存工作流文件。

## 环境注意

- ComfyUI 页面要开着；电脑不能睡眠。浏览器的省内存功能可能会关掉闲置分页，把 ComfyUI 设为一律保持运行。
- 页面在背景时由服务器直接通知，不必保持在前景。
- 有多个 ComfyUI 页面时，指令都要用 `--client-id` 指定；`plan_workflow.py tabs` 列出每个页面与它的分页。
- Codex 等有沙盒的工具要允许连到 ComfyUI 地址。
