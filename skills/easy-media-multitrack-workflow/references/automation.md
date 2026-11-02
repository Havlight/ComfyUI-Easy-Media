# 自动循环：读分页、套用、执行、等待、纪录

在 skill 目录执行下列脚本。用 ComfyUI 的 Python（读 `config.yaml` 需要 PyYAML）。需要一个开着的 ComfyUI 页面：读分页、转换规划和执行都由页面完成，转换用 Editor 自己的导入逻辑，执行用原生的「执行」。

## 准备

- 用户在 ComfyUI 开好要用的工作流分页，或指定一个存在 ComfyUI 里的工作流：模型设好，时间轴可以是空的。没存档也可以，agent 读得到分页里没存的修改。
- 用户没说用哪个工作流时先问。用户说「用默认」时用 `assets/templates/v1.3.0-blank-workflow.json`：`apply --file` 会把它开成新的未存档复本。`--file` 只给 ComfyUI 工作流文件夹以外的文件用。
- agent 改分页的 Editor 时间轴与 Project 的专案名称、开始段、段数；用户要求时还有其他设置（`--sampling`、`--set`，见第 5 步）。算一步，用户按 Ctrl+Z 可以还原（重新整理页面会清掉还原纪录）；不存档、不改其他节点。
- 分页里已有的影音轨若要保留，规划的 `tracks` 要列出它们的 `id`；没有 `id` 的轨道是新轨道，分页原有、规划没列出的轨道会被删除。

## 1. 找分页

```bash
python scripts/plan_workflow.py tabs
python scripts/plan_workflow.py tabs --saved "mmh3 blank"
```

列出每个 ComfyUI 页面开着的分页（`tabs`：`key`、名称、是否在前景、有没有没存的修改），以及存在 ComfyUI 但没打开的工作流有几个（`saved_count`）。这些工作流可能有几百个，`--saved 关键字` 只列出 key 含每个词的（不分大小写；`saved`：`key`、名称），只写 `--saved` 列出全部。之后的 `--tab` 用 `key`；名称只有一个用时也可以，开着的分页优先。没打开的工作流：`read` 直接读文件，不打开；`apply`、`submit_workflow.py --tab` 会先照侧边栏的方式打开原文件（报告有 `opened: true`），存档会写回原文件。有多个页面时，后面的指令都加 `--client-id`。

## 2. 读分页

```bash
python scripts/plan_workflow.py read --tab "workflows/mmh3/MV.json" -o current.json
```

输出 Project 与 Editor 的设置（`settings`）、Editor 的帧率、每段的摘要与 `status`、每条影音轨（是否锁定、`soundtrack`、静音、片段起讫秒数）、Project 接了 H3 Segment LoRA 节点时它的规则（`loras`，写法同规划；接了但规划写不进去时改为 `loras_unavailable`：`loras_ambiguous` 几个 Project 接不同的 LoRA 节点或有的没接，`loras_shared` 节点也接到这个 Editor 以外的 Project，`loras_linked` 规则由连线提供，`loras_unreadable` 读不出规则，告诉用户；LoRA 节点被绕过（Bypass）或静音（Mute）时多一项 `loras_inactive`（`bypassed` 或 `muted`）：规则照样读写，但执行时一条都不套用，`status` 也当作没有 LoRA 来检查，要用 LoRA 就请用户取消节点的绕过或静音）、输出节点（`outputs`：`temp: true` 表示只是预览，ComfyUI 重开会被清掉），`-o` 写出分页目前的规划（含 `loras`）。要修改时就在这份规划上改，用户在分页里改过的地方才会留着。

`settings` 用 ComfyUI 自己的参数名称（与工作流文件的 `widgets_values_named` 相同），`apply --set` 也用这些名称。选项底下的设置写成 `选项名.设置名`，例如：

```json
"project": {"settings": {"project_name": "mv-alice", "project_save": "new", "seed": 7, "control_after_generate": "randomize",
  "sampling_plan": "medium", "sampling_mode": "dual", "sampling_mode.use_saved_1st_pass": false,
  "sampling_mode.upscale_by": 1.25, "sampling_mode.upscale_model": "…", "sampling_mode.keep_1st_pass_audio": false,
  "sampling_mode.disable_2nd_noise": false, "sampling_mode.enabled_tiling": "false", "allow_vae_fallback": false}},
"editor": {"settings": {"resolution": "width x height (megapixels)", "resolution.aspect_ratio": "16:9 (Widescreen)",
  "resolution.megapixels": 0.9, "format": "MiniMax"}, "frame_rate": 24}
```

分页内容跟专案纪录对不上时（例如 ComfyUI 重开后分页变回空白范本），先问用户要不要还原；要的话用 `apply --from-run last --tab 分页 --project-name 名称`（或指定执行编号）套回那次执行用的规划。还原只套回规划，设置不动；跟那次执行不同的设置列在 `settings_since_run`（`run` 是当时的值，`now` 是现在的），告诉用户，要改回去时再用 `--set`。

工作流存档时没有参数名称（很旧的文件）时，`read` 照样读出规划与影音轨，设置改为 `settings_error` 说明原因；`apply`、`patch_workflow.py` 与执行纪录要参数名称，请用户在 ComfyUI 打开后另存。

`status` 是 Project 对每段已存结果的检查（与 Editor 显示的一样）：`saved` 已存结果跟目前内容相符；`edited` 内容改过；`parent_changed` 接着的前一段改过或换了版本；`missing` 没有结果；`pending` 片段 LoRA 要到执行时才能确认：读不到 LoRA 节点的设置时当作相符，用到还没算过文件哈希的 LoRA（第一次用的文件）时算要跑；`legacy` 旧格式结果。

## 3. 读素材长度

```bash
python scripts/plan_workflow.py probe "D:/mv/song.wav" "D:/mv/dance.mp4"
```

输出每个文件的 `seconds`，视频另有 `has_audio`。用来决定分几段、每段几秒。

## 4. 写规划文件

从第 2 步写出的规划改起（空白分页就新写）。格式见 [plan-file.md](plan-file.md)，分段与提示词见 [writing.md](writing.md)。每段任务写固定的 `id`；歌曲、影片用 `tracks` 放到某段开头。规划文件放在 agent 自己的工作目录即可，执行时脚本会把实际跑的规划存进专案纪录。

## 5. 套用到分页

```bash
python scripts/plan_workflow.py apply plan.json --tab "workflows/mmh3/MV.json" --project-name mv-alice
```

- 规划里的本机文件先复制进 ComfyUI 的 `input/easy_media/imports/<哈希>/<原文件名>`；ComfyUI 读不到的文件会上传到同一位置（`uploaded` 列出上传了哪些）。
- 有错误时返回码 2，输出 `errors`，分页不变：修正规划后重新套用。
- 成功时分页已改好（未存档），并输出：
  - `warnings`：例如秒数为对齐帧格被调整（合成一句，实际秒数看 `tasks`）、提示词标签找不到素材、任务超过锁定音轨的长度、执行范围外还有要跑的段落、同一段重复用了同一个 LoRA（执行会在采样前停下，要先改）或套用不到任何段的 LoRA 规则；
  - `tasks`：每段对齐后的秒数、模式、`labels`（每个提示词标签对应的素材）、`status`；
  - `tracks`：每条影音轨的片段与起讫秒数；
  - `loras`：规划写了 `loras`，或增删任务让规则跟着移动时，列出每条规则的变更（`change`）、LoRA、强度、阶段与套用的段号（`segments`，`end` 表示到最后）；LoRA 改过的段落 `status` 是 `edited`，用到第一次用的 LoRA 文件时是 `pending`，都算要跑；
  - `loras_inactive`：同 `read`，LoRA 节点被绕过或静音（`bypassed`／`muted`）：规则照样写入，但执行与 `status` 都当作没有 LoRA，也不检查 LoRA 冲突；规划没写 `loras` 时也会出现，告诉用户；
  - `run`：`start` 是第一个要跑的段落（`status` 不是 `saved`），已写进 Project；`nothing_to_run: true` 表示每段的结果都相符，不需要执行。
- `--start N` 从指定段落开始，`--count N` 只跑 N 段（默认 `-1` 跑完剩下的）。Project 会把范围内的每一段都重新生成，只改了一小段时自己设范围；范围外还有要跑的段落时会警告。
- 专案名称决定结果存在哪里。同一支影片沿用原名称，没改到的段落才能沿用。重跑的段落是另存新版本还是取代目前版本，看 `project_save`（`new` / `override`）。
- `--sampling single|dual|upscale` 同时设定 Project 的采样方式：`single` 是预览，`dual` 一次跑完一采与二采，`upscale` 是 dual 加 `use_saved_1st_pass`（放大已存的一采），必须给 `--start`。不加就不改。
- `--set 节点.名称=值` 改其他设置，可以重复；节点是 `project` 或 `editor`，名称照 `read` 的 `settings`。只在用户要求时用，没提到的设置照分页。

  ```bash
  python scripts/plan_workflow.py apply plan.json --tab "workflows/mmh3/MV.json" --project-name mv-alice \
    --sampling dual --set project.sampling_mode.upscale_by=1.5 --set project.seed=7 \
    --set editor.resolution="width x height (custom)" --set editor.resolution.width=1024 --set editor.resolution.height=576
  ```

  - 值照设置的类型转换：数字、`true`／`false`、选项照写。
  - 选项底下的设置要先选那个选项：`upscale_by` 属于 `sampling_mode` 的 `dual`，分页是 single 时要同时 `--sampling dual`（或 `--set project.sampling_mode=dual`）；分块数量要 `sampling_mode.enabled_tiling=true`。切换选项时，新选项的其他设置是 ComfyUI 记得的值或默认值，套用后可以 `read` 确认。
  - 专案名称、开始段、段数用 `--project-name`、`--start`、`--count`，不用 `--set`；`--sampling` 设的两项也不能再 `--set`。
  - 名称、选项或数值不对时（不存在的设置、超出范围、不是步进的倍数、由连线提供的设置、大过页面能精确保存的整数 9007199254740991），返回码 2，`errors` 说明原因与可用的设置，分页不变。设成跟连线目前相同的值不算修改，可以通过。
  - 专案名称、开始段或段数由连线提供时，`apply` 不改它们，照连线的值跑，并在 `warnings` 说明。
  - 成功时输出的 `settings` 列出这次改了哪些（不含专案名称与范围），交付时告诉用户。

## 6. 执行并等待

用户授权执行后：

```bash
python scripts/submit_workflow.py --tab "workflows/mmh3/MV.json" --until-done
```

- 页面切到那个分页按原生执行，再切回用户原本看的分页；返回 `request_id`、`prompt_id`。
- 排队成功后，这次执行写进专案纪录：`history.json` 新增一笔，`plans/run-NNN.json` 存这次跑的规划。
- `--until-done` 一直等到生成结束，适合放在后台执行：工具能在指令结束时通知（如 Claude Code 的背景指令）就这样做，等待时不要查询、不要另外排程。
- 工具不能放后台时用 `--wait 540`，最多等 9 分钟，避免超过工具的指令时间上限。返回码 3 时稍后继续等，不要重新提交：

  ```bash
  python scripts/submit_workflow.py --status <request_id> --wait 540
  ```

- 返回码：0 成功（`generation.outputs` 列出输出文件），2 失败（`generation.node_type`、`message`），3 还在跑（`generation.state` 为 `running` 或 `pending`）。改了分辨率或采样方式后接续旧段落，可能在开始生成前失败（`VAE_FALLBACK_REQUIRED`），见 SKILL.md 的「分辨率」。

执行结束后用 `plan_workflow.py project --name 名称` 确认每段的版本：目前用哪一版（`active`）、怎么生成的（`sampling_pass`：`single`、`second` 即 dual、`selflift`；`size` 成品尺寸，dual 另有 `first_pass_size`；`seed`；从已存一采放大的有 `upscaled_from` 版本号与 `keep_1st_pass_audio`）、接在哪一版后面（`continued_from`，如 `f05@7`）、有没有沿用锁定音频（`locked_audio`）、有没有改用备用方式（`fallback`）、是否因前一段换了版本而过期（`stale`）。

切换某段用哪一版：

```bash
python scripts/plan_workflow.py select --name mv-alice --task 3 --version 12
```

`--task` 是段落编号（从 1 起），`--version` 用 `project` 列出的版本号。输出这次的选择与切换后的版本清单；接在这段后面、原本接的是别的版本的段落会变成 `stale`，要重跑。打开着的 Project Video Combine 节点会跟着更新。

## 7. 看画面检查（选用）

默认关闭。只有 `config.yaml` 设 `AGENT_FRAME_REVIEW: true`，或用户要求检查结果（加 `--user-asked`）时才用；模型不能看图就不要用。

```bash
python scripts/review_frames.py --project mv-alice --tasks s03,s04 --user-asked
```

每段输出一张图（默认 4 格、宽 1024），存到专案纪录的 `review/`（agent 在别台机器时存到目前目录的 `review/<专案名称>/`），输出列出每张图的本机路径。看图后判断要不要修改；每轮只看这轮重新生成的段落。加 `--versions` 时每段的每个版本各一张（`01-s01-v3.jpg`，输出有 `version`、`active`），用来比较几个版本。

## 8. 先预览再放大

single 就是 dual 的一采：尺寸、默认 sigmas 与接续方式都相同，所以可以先用 single 看效果，满意的再放大。

1. `apply 规划 --tab 分页 --project-name 名称 --sampling single`，执行。要比较几种一采时 `project_save` 要是 `new`，每次执行都会另存一版；分页是 `override` 时先问用户，同意了再 `--set project.project_save=new`。
2. `review_frames.py --project 名称 --tasks s01,s02 --versions --user-asked` 比较各版本（或请用户看），`select` 选定每段要用的版本。新版本生成后自动成为使用中的版本。
3. 用户同意放大后：`apply 规划 --tab 分页 --project-name 名称 --sampling upscale --start 1 --count 2`，执行。每段放大使用中版本的一采，用它当初的种子，结果另存为 dual 版本（`upscaled_from` 记着来源版本）；dual 版本也能从它自己的一采再放大一次。
4. 之后再用 single 预览后面的段落时，会接在已放大段落的一采后面。

生成前会报错、不开始生成的情况（错误码在 `SAVED_FIRST_PASS` 开头，`generation.message` 有说明）：

- 段落没有编辑器尺寸的一采（没跑过、改了分辨率，或使用中的版本是 selflift）；
- 一采之后改过该段的内容或时间：用 single 重跑该段；
- 它接着的前一段还没有二采：先放大前一段，或在同一次一起放大；
- 同一次放大相邻两段时，后一段的一采不是接在前一段使用中的那个一采后面：先放大前一段，再用 single 重跑后一段；
- 前一段是直通（passthrough）任务又在同一次执行里：每次执行都会重新导入它，从它的下一段开始跑。

二采的设置、模型与 LoRA 可以随意改；一采的采样方案、模型与片段 LoRA（`all`、`first` 阶段）改了也不影响，放大的是已存的一采，新版本记下的是它当初用的 LoRA。一采阶段的 LoRA 改过时，`apply` 会警告是哪几段：放大保留原本的一采，新规则只用在之后的预览；要让新 LoRA 生效，就用 single 重跑那几段再放大。`status` 也照这次的采样方式判断：放大时这些段落仍是 `saved`；用 single 或从头跑 dual 时会重采一采，就算要跑（`edited`）。

## 9. 修改与重跑

回到第 2 步读出目前的规划，改对应段落（保留 `id`），再套用，沿用同一个专案名称。同一个需求最多自动重跑 3 轮，再要重跑先问用户。

## 专案纪录

每个专案在 ComfyUI 的 `output/easy_media/agent/<专案名称>/` 有一个纪录资料夹（经由 ComfyUI 读写，agent 在别台机器也能用）：

| 文件 | 内容 | 谁写 |
|---|---|---|
| `README.md` | 影片描述、分段表（每段做什么、用哪些素材）、每次改了什么 | agent：`plan_workflow.py record README.md --project 名称` |
| `history.json` | 每次排队：编号、时间、`request_id`、`prompt_id`、分页、范围、Project 与 Editor 的设置（`settings`）、规划文件 | `submit_workflow.py` |
| `plans/run-NNN.json` | 每次执行跑的规划，可以用 `apply` 套回分页还原 | `submit_workflow.py` |
| `review/` | 看画面检查的图 | `review_frames.py` |

执行的结果看 `--until-done` 的返回内容；之后要查，用 `prompt_id` 查 ComfyUI 的历史，或看 Project Video Combine 节点里每段的版本。每轮执行后更新 `README.md`，简短写下这轮改了什么。不在本机另存工作流文件。

## LoRA 说明档

所有专案共用一份 LoRA 说明档 `output/easy_media/loras.md`（经由 ComfyUI 读写，agent 在别台机器也能用），写每个 LoRA 用来做什么、怎么用。agent 选片段 LoRA 时照它。

```bash
python scripts/plan_workflow.py loras
python scripts/plan_workflow.py loras --find "mmh3 realism"
python scripts/plan_workflow.py loras --write loras.md
```

- 输出说明档全文（`text`，还没有时为 null）、说明档写到的 LoRA（`documented`：是否已安装，`installed: false` 表示文件名写错或文件不在了）与已安装 LoRA 的数量。有 LoRA Manager 时另附它记的基础模型、触发词、使用提示（如强度范围）、备注（`lora_manager`），只作参考。
- `--find 关键字` 另外列出文件名含每个词的已安装 LoRA（不分大小写；只写 `--find` 列出全部），用来查正确的文件名。
- `--write 文件` 用这个 Markdown 文件取代说明档。要加一节时先读出全文，在上面加，再整份写回，不要删掉别的 LoRA 的说明。

每个 LoRA 一节，标题是 ComfyUI LoRA 列表里的文件名（与规划 `loras` 的 `lora` 相同）：

```markdown
## mmh3/h3-realism-people-t2v-i2v-r2v.safetensors
- 用途：人物写实质感
- 触发词：无
- 建议强度：0.4–0.6
- 阶段：all（一采二采都用）
- 注意：强度太高脸会变形
```

## 环境注意

- ComfyUI 页面要开着；电脑不能睡眠。浏览器的省内存功能可能会关掉闲置分页，把 ComfyUI 设为一律保持运行。
- 页面在背景时由服务器直接通知，不必保持在前景。
- 有多个 ComfyUI 页面时，指令都要用 `--client-id` 指定；`plan_workflow.py tabs` 列出每个页面与它的分页。
- Codex 等有沙盒的工具要允许连到 ComfyUI 地址。
