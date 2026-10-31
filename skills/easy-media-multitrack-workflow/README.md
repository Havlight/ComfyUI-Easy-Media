# Easy Media MultiTrack Workflow Skill

这是 ComfyUI Easy Media 节点包附带的 Codex Skill，用于把 MultiTrack（MiniMax H3）影片规划写进用户在 ComfyUI 开着的工作流分页，或修改工作流 JSON 文件，并可在用户明确要求时在 ComfyUI 执行。

## 它能做什么

- 读取 ComfyUI 开着的工作流分页（含未存档的修改），用 H3 规划文件描述影片（任务、提示词、参考素材、时间轴上的歌曲或影片）并写回分页，执行、等待结果，在 `output/easy_media/agent/<专案>/` 留下规划与执行纪录，并可选择抽画面检查后自行修改重跑。流程见 [自动循环](references/automation.md)。
- 按 H3 提示词指南写每段提示词（`references/prompts/` 下的完整参考与一般模式两份），多段时保持角色、风格与动作衔接一致，见 [写提示词与多段规划](references/writing.md)。
- 将本地图片、视频和音频上传到 ComfyUI `input` 目录或指定子目录。
- 自动创建和编排任务、视频、音频时间线片段。
- 把用户提示词、系统提示词和参考图片放入对应任务片段。
- 根据 MV、歌词视频、音乐驱动剪辑或旁白驱动等上下文，自动锁定主音频轨道，并可独立锁定视频画面时间线。
- 用户要求新建流程时，基于内置 v1.3.0 模板生成新的工作流。
- 用户提供已有工作流/模板时，以该文件为唯一基线生成，不覆盖其节点图。
- 用户明确要求实际生成时，上传素材并提交到指定 ComfyUI；未提供地址时先读取 Easy Media 节点包 `config.yaml` 的 `COMFYUI_URL`，文件或字段不存在（含空值）时才回退至 `http://127.0.0.1:8188`。
- 通过 UI 工作流接口运行开着的分页，或把工作流文件开成新分页，然后原生入队；不直接提交 API prompt。多个在线页面时必须指定目标。接口与脚本用法见 [提交与执行](references/execution-api.md)。
- 在用户明确要求时，将 `MiniMaxH3HybridLoader` 安全替换为 `UNETLoader`，或替换兼容的 attention 后端。
- 调整帧率、片段起止位置、时长、连续性模式和参考图策略。
- 视频只作动作参考时，放在不锁定的视频轨，跟它重叠的每段各自参考自己那一段，不用另外剪档；要动作对准时间又要镜头不切时，先说明取舍让用户选。
- 视频作为主体或局部替换参考时，锁定该视频轨与原声，第一个被锁定的任务用新镜头、之后的接续，片段用 `until` 结束在最后一个被锁定的任务，并在未指定片段长度时按 10 秒拆分任务。
- 调整 `easy multitrackProject` 的采样方案、单/双阶段模式、开始片段索引和本次处理片段数。
- 在包含多个多轨编辑器时，通过节点连线定位实际连接到项目节点的编辑器。
- 保留用户当前工作流中的节点、连线、节点 ID、布局和其他配置，只替换目标多轨编辑器与多轨项目参数。

Skill 默认先执行 dry-run，并输出一份新的工作流文件，不会直接覆盖用户原始 JSON。

当存在多条可能作为主音频的轨道时，Skill 会结合轨道名称、文件名、时长和用户描述判断。如果仍不能唯一确认，它会先询问用户选择，不会默认锁定第一条或最长的音轨。

## 安装

### 方法一：复制到 Codex Skills 目录

在 Easy Media 节点包根目录运行：

```bash
mkdir -p ~/.codex/skills
cp -R skills/easy-media-multitrack-workflow ~/.codex/skills/
```

如果目标目录中已经安装了旧版本，请先备份或移走旧目录，再复制新版本。不要把新目录嵌套成：

```text
~/.codex/skills/easy-media-multitrack-workflow/easy-media-multitrack-workflow
```

正确入口应为：

```text
~/.codex/skills/easy-media-multitrack-workflow/SKILL.md
```

### 方法二：开发环境使用符号链接

如果希望节点包更新后立即使用最新版 Skill，可在节点包根目录运行：

```bash
mkdir -p ~/.codex/skills
ln -s "$(pwd)/skills/easy-media-multitrack-workflow" \
  ~/.codex/skills/easy-media-multitrack-workflow
```

目标位置已有同名文件或目录时，`ln` 会停止并报错，不会覆盖它。安装完成后，新建一个 Codex 任务以刷新可用 Skill 列表。

### 其他 agent 工具

Claude Code 的目录是 `~/.claude/skills/`，做法同上（复制或符号链接整个 `easy-media-multitrack-workflow` 目录）。没有 skill 机制的工具，在对话开头请它先读 `SKILL.md`。

Windows 的 Codex 若跑在 WSL 里，本机路径与 ComfyUI 看到的不同；`plan_workflow.py` 会自动上传 ComfyUI 读不到的文件。

抽画面检查默认关闭；要让 agent 自己看结果，在节点包 `config.yaml` 设 `AGENT_FRAME_REVIEW: true`，或在需求里请它检查。

## 使用方法

可以显式调用：

```text
使用 $easy-media-multitrack-workflow，把这些图片和视频上传到 input/project-a，
按每段 5 秒编排到当前工作流，并把每条提示词放入对应任务片段。
这是一个 MV 项目，请将完整歌曲作为锁定音轨，让画面按歌曲时间生成。
```

也可以直接描述任务；当请求涉及 Easy Media 多轨工作流时，Codex 可以自动选择该 Skill。例如：

```text
基于我当前的工作流，只修改多轨编辑器：从第 2 个任务片段开始运行 3 段，
使用 medium 采样方案，不要替换或删除现有节点。
```

新建工作流示例：

```text
使用 $easy-media-multitrack-workflow，按内置 v1.3.0 模板新建一个 MV 工作流。
将主模型改成默认 UNETLoader，attention 使用 MiniMax H3 省显存 patch，
上传歌曲并锁定主音轨，再按上下文生成任务片段和提示词。
```

建议同时提供：

- 当前需要编辑的工作流 JSON。
- 素材文件或可访问的绝对路径。
- 每个片段的提示词及期望时长。
- 目标帧率、片段排列方式和参考图对应关系。
- 目标分辨率、横竖屏/画幅比例、是否保持源素材比例，以及期望的缩放或裁切方式。
- MV、歌词视频或旁白驱动项目中，哪条音频是应当原样保留并驱动画面的主音频。
- 上下文分段中，是否要把同一张图片、同一段音频或同一段视频设为所有任务共用的公用参考媒体。
- 是否需要实际上传或执行，以及 ComfyUI 服务地址；未提供地址时先读取 Easy Media 节点包 `config.yaml` 的 `COMFYUI_URL`，文件或字段不存在（含空值）时才回退至 `http://127.0.0.1:8188`。
- 新建流程时是否需要替换模型加载器或 attention 后端，以及用户环境中实际可用的模型文件和节点。

## 安全行为

Skill 默认在用户指定的 ComfyUI 分页上工作，只改 Editor 时间轴与 Project 的专案名称、开始段、段数（算一步，可 undo），不存档；用户没指定分页时先问，说「用默认」才用内置 v1.3.0 模板开新分页。修改工作流文件时，提供了文件就以它为唯一基线，不用内置模板覆盖；空模板时间线为空，交付前必须写入完整的目标轨道和片段数据。

补丁脚本会检查：

- 目标编辑器是否确实连接到 `easy multitrackProject`。
- 时间线片段是否排序、重叠或包含无效帧范围。
- 轨道与片段 ID 是否重复。
- 媒体类型及任务参考图数量是否有效。
- 是否最多只有一条音频轨道和一条视频轨道被设置为 `audio_locked: true`；两者并存时音频轨道优先提供锁定音频，视频轨道保留画面时间线。
- DynamicCombo 分辨率标签与其子 widget 是否匹配，并同步命名 widget 值。
- `shared_reference` 是否只出现在任务图片项或匹配类型的音频/视频片段中，且同一音频/视频轨最多一个。
- 修改前后的节点数、连线和非目标节点内容是否保持不变。

上传媒体和提交 ComfyUI 队列属于外部状态变更。“生成一个工作流”只生成 JSON；只有用户明确要求运行、执行、开始生成或提交队列时才执行。未提供地址时先读取 Easy Media 节点包 `config.yaml` 的 `COMFYUI_URL`，文件或字段不存在（含空值）时才回退至 `http://127.0.0.1:8188`。

## 目录结构

```text
easy-media-multitrack-workflow/
├── SKILL.md
├── README.md
├── agents/openai.yaml
├── assets/templates/v1.3.0-blank-workflow.json
├── references/
│   ├── automation.md
│   ├── multitrack-schema.md
│   ├── execution-api.md
│   ├── plan-file.md
│   ├── prompts/
│   │   ├── base.md
│   │   └── ref.md
│   ├── template-workflow.md
│   ├── upload-api.md
│   └── writing.md
└── scripts/
    ├── customize_template.py
    ├── patch_workflow.py
    ├── plan_workflow.py
    ├── review_frames.py
    └── submit_workflow.py
```

`SKILL.md` 是 Codex 入口；`README.md` 面向安装和使用者；模板资产用于新建流程；`references` 保存数据结构与节点替换规则；脚本负责安全定制模板、定位节点、校验补丁并生成新的工作流文件。
