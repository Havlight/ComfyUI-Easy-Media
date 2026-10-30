# ComfyUI UI 工作流提交与执行

仅创建/修改 JSON 不触发提交；要求在界面打开时只加载，明确要求运行、执行或生成媒体时才自动入队。

## 地址与预检

- 地址优先级：用户明确提供的地址（`--url`）→ Easy Media 节点包 `config.yaml` 的 `COMFYUI_URL` → `http://127.0.0.1:8188`。文件、字段不存在或值为空时才回退；只给 `host:port` 时补 `http://`。配置无效或连接失败时报告错误，不切换实例。
- 脚本在仓库内或以符号链接安装时自动定位节点包配置；复制安装时，从节点包根目录或其子目录运行，或传 `--config /path/to/ComfyUI-Easy-Media/config.yaml`。其他目录默认检查当前目录的 `config.yaml`；不扫描机器上其他实例。
- 存在配置文件时需要 PyYAML，可使用 ComfyUI 的 Python 环境。仅读取 `COMFYUI_URL`，不要输出整个配置文件中的 API key。上传、打开/入队和状态查询共用解析后的地址；用户未指定地址时不要主动传默认 `--url` 覆盖配置。
- 安装包含此接口的 Easy Media 后重启 ComfyUI，并刷新浏览器页面。必须保持至少一个已加载工作流的 ComfyUI 页面在线。
- 请求 `GET /object_info` 检查目标节点；素材必须上传到同一实例，见 [upload-api.md](upload-api.md)。
- 接口沿用 ComfyUI 的访问权限，不提供独立认证；勿把未鉴权的 ComfyUI 暴露到公网。代理鉴权需使用已有授权凭据；脚本不接管浏览器登录。

## 从 UI 运行

在 skill 目录执行：

```bash
python3 scripts/submit_workflow.py --tab "workflows/MV.json"   # 运行开着的分页
python3 scripts/submit_workflow.py RESULT.json                  # 把工作流文件开成新分页再运行
```

运行开着的分页时，页面切到那个分页、调用与“运行”按钮相同的原生入队入口，再切回用户原本看的分页；分页 key 由 `plan_workflow.py tabs` 列出。工作流文件只接受带 `nodes`、`links`、布局和控件值的 **UI workflow JSON**，开成新分页后同样原生入队。两种都保留自定义控件和种子的提交钩子。不要手写 API prompt，也不要直接请求 `/prompt`。

- `--tab` 运行前先读取分页，排队成功后把这次执行写进专案纪录（`history.json`、`plans/run-NNN.json`，见 [automation.md](automation.md)）；读取之后有人改了 Editor 或 Project 时页面拒绝运行。
- 工作流文件总是开成新分页，不覆盖已开的分页，也不存档；`--no-queue` 只打开，不运行，结果附带新分页的 `tab`。
- `--name 名称` 指定 tab 名称前缀；新 tab 附加请求 ID，避免与已打开流程重名。
- `--clients` 列出在线页面；一个页面时可省略 `--client-id`，多个页面必须指定目标，不能选择第一个或广播。
- `client_id` 是本接口为每个页面生成的 ID；`session_id` 是对应 ComfyUI WebSocket ID。同一个页面里的多个工作流 tab 不计为多个客户端。
- `--request-id ID` 提供重试标识；脚本在 POST 前把它输出到 stderr。相同 ID 和相同参数只返回已有请求，不重复执行；相同 ID 更换参数会报错。
- `--status ID` 查询已有请求，不再次提交。`--timeout` 仅控制等待浏览器回执的秒数，不代表生成时限，不取消已经提交的任务。
- `--wait 秒数` 在入队后（或配合 `--status`）等待生成结束：返回码 0 成功并列出 `generation.outputs`，2 失败并给出 `node_type` 与 `message`，3 仍在运行或排队。单次最多等 540 秒，避免超过 agent 工具的指令时间上限；返回 3 时用 `--status ID --project 名称 --wait 540` 继续等，不重新提交（`--project` 让结束时更新专案纪录）。
- `--until-done` 不限时间等到结束，给能在背景执行指令、结束时通知的工具用；等待期间不必查询。

提交开始后到返回 `queued` / `loaded` 前，不要在目标页面手动切换工作流或连续点击运行；冲突会停止自动提交并报告错误。

## HTTP 接口

`GET /easy-media/workflow/clients` 返回 `{"clients": [{"client_id": "...", "session_id": "...", "title": "...", "last_seen": 0}]}`。

`POST /easy-media/workflow/submit`，要求 `Content-Type: application/json`：

```json
{
  "workflow": { "nodes": [], "links": [], "version": 0.4 },
  "auto_queue": true,
  "name": "My workflow",
  "request_id": "unique-request-id",
  "client_id": "client-id-from-clients-endpoint"
}
```

这里的空图只展示格式，运行时必须替换成完整工作流。`workflow` 必填，其余字段可选；`auto_queue` 默认 `true`。工作流总是开成新分页，完成时附带新分页的 `tab`。只有一个在线页面时可省略 `client_id`。

返回 `request_id`、目标 `client_id` 和初始 `status`。用 `GET /easy-media/workflow/submissions/{request_id}` 查询：

| status | 含义 |
|---|---|
| `pending` | 等待前端领取，尚未打开 |
| `loading` | 已领取，正在加载或入队 |
| `loaded` | 已打开，未要求入队 |
| `read` | 分页已读取（`tabs` 请求） |
| `applied` | 分页已修改，未要求入队（`apply` 请求） |
| `queued` | 原生入队成功，附带 `prompt_id`；不代表生成完成 |
| `failed` | 加载失败或尚未入队就停止，附带 `error` |
| `unknown` | 执行结果未确认；可能已入队，先检查 queue/history，不能自动重跑 |

HTTP 400 表示参数错误，409 表示多个页面未指定目标、目标忙或请求 ID 冲突，503 表示没有可用前端或服务容量已满。服务器只通过目标页面的 WebSocket 通知它领取（不广播），页面在背景时也会立即领取；页面本身也每秒轮询作为备援。WebSocket 连着的页面在 5 分钟内都算在线。无回执时不会重新派发已领取的工作流。

### 规划转换

`POST /easy-media/workflow/convert` 让页面用 Editor 的导入逻辑把 H3 规划转换成 track data，不开分页、不改画布：

```json
{ "track_data": { "tracks": [] }, "format": "MiniMax", "plan": { "version": 1, "tasks": [] }, "request_id": "...", "client_id": "..." }
```

状态查询同上；完成时 `status` 为 `converted`，`conversion` 含 `track_data`（有错误时为 null）、`errors`、`warnings`、`tasks`、`tracks`。转换没有副作用，页面 120 秒内没完成时直接 `failed`，可以重试。一般用 `scripts/plan_workflow.py apply`，见 [automation.md](automation.md)。

### 读取与修改分页

`POST /easy-media/workflow/tabs` 读取页面开着的分页：

```json
{ "tab": "workflows/MV.json", "request_id": "...", "client_id": "..." }
```

`tab` 可省略，只列分页。完成时 `status` 为 `read`，`tabs` 列出每个分页的 `key`、`name`、`active`、`modified`、`temporary`；指定 `tab` 时另有 `tab`：`workflow`（含未存档的修改）、`fingerprint`、`plans`（每个 Editor 导出的规划，依节点 id）。

`POST /easy-media/workflow/apply` 修改或运行一个分页：

```json
{
  "tab": "workflows/MV.json",
  "expected": "fingerprint-from-the-read",
  "changes": {
    "editor_id": 14, "track_data": { "tracks": [] },
    "project_id": 15, "project": { "project_name": "mv", "segment_start_number": 1, "segment_count": -1 }
  },
  "queue": false
}
```

页面切到该分页；`expected` 与目前不符时（读取后有人改了 Editor 或 Project）直接 `failed`。`changes` 设定 Editor 的 track data 与 Project 的这三个字段，像人的一次编辑（可 undo、标示未存档），完成时 `applied` 并附 `tab_plan`（修改后 Editor 导出的规划）。`queue: true` 接着原生入队，完成时 `queued` 并附 `prompt_id`。最后切回原本在前景的分页。只改不运行的请求 120 秒没完成时 `failed`，可以重试；有入队的同 `submit`，会变成 `unknown`。

### 专案纪录

`GET /easy-media/agent/records?project=名称` 列出 `output/easy_media/agent/<名称>/` 的文件；加 `&path=README.md` 读一个文件（不存在时 `text` 为 null）。`POST /easy-media/agent/records`（`{"project", "path", "text"}`）写入一个文件。`path` 只能是 `README.md`、`history.json`、`tab-plan.json` 或 `plans/<名称>.json`，最大 4 MB。

请求状态只保存在当前 ComfyUI 进程内：完成记录保留一小时；未领取请求 60 秒后失败，领取后超过 120 秒无回执变成 `unknown`。`unknown` 会阻止向同一页面再次自动提交；先检查队列和历史，确认后可刷新页面重新注册。重启或记录过期后不能依赖原 ID 去重。

## 生成完成判定

`submit_workflow.py --wait` 已按下列步骤判定；自己查询时：

1. 保存 `request_id`，等待到 `queued` 并记录 `prompt_id`。`loaded`、`pending`、`loading` 都不表示已运行。
2. 用 `GET /queue` 查看 pending/running；用 `GET /history/{prompt_id}` 等待终态。
3. 只有对应记录的 `status.completed` 为 `true` 且 `status.status_str` 为 `success` 才算成功；错误/中断按失败报告。
4. 从 history 的 `outputs` 汇总文件。用户未要求长期监控且仍在执行时，返回 ID 和当前状态，不取消任务；取消或清空队列需要明确要求。

## 执行前复核

- 工作流通过 dry-run、write 后 inspect 和不变量检查。
- 分辨率与 DynamicCombo 子字段一致；目标 task 范围、`project_save`、sampling 符合用户要求。
- `audio_locked` 唯一且覆盖目标任务；`shared_reference` 指向预期的公用图片、音频或视频。
- 媒体路径存在于当前 ComfyUI 实例；要修改的分页是用户指定的。
