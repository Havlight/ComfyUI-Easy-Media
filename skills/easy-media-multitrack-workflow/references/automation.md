# 自动循环：转换、执行、等待、检查

在 skill 目录执行下列脚本。用 ComfyUI 的 Python（读 `config.yaml` 需要 PyYAML）。需要一个开着的 ComfyUI 页面：转换由页面上的 Editor 导入逻辑完成，执行用页面原生的「执行」。

## 准备

- 用户准备好模板工作流：分辨率、Project 的采样设置、模型都设好，时间轴可以是空的。没有模板时用 `assets/templates/v1.3.0-blank-workflow.json`。
- 模板里已有的影音轨若要保留，规划的 `tracks` 要列出它们的 `id`；没有 `id` 的轨道是新轨道，模板原有、规划没列出的轨道会被删除。

## 1. 读素材长度

```bash
python scripts/plan_workflow.py probe "D:/mv/song.wav" "D:/mv/dance.mp4"
```

输出每个文件的 `seconds`，视频另有 `has_audio`。用来决定分几段、每段几秒。

## 2. 写规划文件

格式见 [plan-file.md](plan-file.md)。每段任务写固定的 `id`；歌曲、影片用 `tracks` 放到某段开头。

## 3. 转换成工作流

```bash
python scripts/plan_workflow.py convert "D:/templates/H3 720p.json" plan.json --project-name mv-alice
```

- 规划里的本机文件先复制进 ComfyUI 的 `input/easy_media/imports/<哈希>/<原文件名>`；ComfyUI 读不到的文件会上传到同一位置（`uploaded` 列出上传了哪些）。
- 有错误时返回码 2，输出 `errors`，不写工作流文件：修正规划后重新转换。
- 成功时写出 `plan.workflow.json`（`-o` 可指定），并输出：
  - `warnings`：例如秒数为对齐帧格被调整、提示词标签找不到素材、任务超过锁定音轨的长度；
  - `tasks`：每段的秒数、模式、参考数量，`saved` 表示这个项目名称下已有对应的生成结果；
  - `tracks`：每条影音轨的片段与起讫秒数；
  - `run`：`start` 是第一个需要生成的段落，已写进工作流；`nothing_to_run: true` 表示全部都有结果，不需要执行。
- `--start N` 从指定段落开始，`--count N` 只跑 N 段（默认 `-1` 跑完剩下的）。Project 不会跳过已有结果的段落：从开始段之后的每一段都会重新生成。
- 项目名称决定结果存在哪里。新影片用新名称；修改同一支影片时沿用原名称，没改到的段落才能沿用。

## 4. 执行并等待

用户授权执行后：

```bash
python scripts/submit_workflow.py plan.workflow.json --wait 540
```

- 页面开新分页载入工作流并按原生执行，返回 `request_id`、`prompt_id`。
- `--wait 540` 最多等 9 分钟，避免超过工具的指令时间上限。返回码：0 成功（`generation.outputs` 列出输出文件），2 失败（`generation.node_type`、`message`），3 还在跑（`generation.state` 为 `running` 或 `pending`）。
- 返回码 3 时稍后继续等，不要重新提交：

  ```bash
  python scripts/submit_workflow.py --status <request_id> --wait 540
  ```

## 5. 看画面检查（选用）

默认关闭。只有 `config.yaml` 设 `AGENT_FRAME_REVIEW: true`，或用户要求检查结果（加 `--user-asked`）时才用；模型不能看图就不要用。

```bash
python scripts/review_frames.py --project mv-alice --tasks s03,s04 --user-asked
```

每段输出一张图（默认 4 格、宽 1024），存到 `review/<项目名称>/`，输出列出每张图的本机路径。看图后判断要不要修改；每轮只看这轮重新生成的段落。

## 6. 修改与重跑

改规划里对应段落（保留 `id`），回到第 3 步，沿用同一个项目名称。同一个需求最多自动重跑 3 轮，再要重跑先问用户。

## 环境注意

- ComfyUI 页面要开着；电脑不能睡眠。浏览器的省内存功能可能会关掉闲置分页，把 ComfyUI 设为一律保持运行。
- 页面在背景时由服务器直接通知，不必保持在前景。
- 有多个 ComfyUI 页面时，`convert` 和 `submit_workflow.py` 都要用 `--client-id` 指定；`submit_workflow.py --clients` 列出页面。
- Codex 等有沙盒的工具要允许连到 ComfyUI 地址。
