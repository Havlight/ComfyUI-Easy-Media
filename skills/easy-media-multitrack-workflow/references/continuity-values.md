# 接续的细部数值

只在用户给了具体数值时用（例如「渐变 6 步」「后置帧四分之一步数后加入」）。用户只描述感受（「接缝顺一点」「更稳一点」）时，用 [plan-file.md](plan-file.md) 里的名称，不要自己试数值：每试一次都要重跑 GPU，看截图也分辨不出细部数值的差别。

数值写成对象，写在规划的 `seam` 或 `anchor` 里。各项都可省略，省略的项用默认值；写了就整个取代这段的设置。

## `seam`（`context_drift`）

| 项 | 取值 | 默认 | 作用 |
|---|---|---|---|
| `taper` | 0–12 的整数 | 4 | 衔接处有几个上下文步（每步约 4 帧）逐步过渡到最低权重；0 为不渐变，整段上下文随噪声调度放开 |
| `floor` | 0–1 | 0 | 紧贴衔接处那一步的权重；越高越少照抄上一段的外观与色彩，但构图固定较弱 |
| `low_only` | true／false | false | 只对 SelfLift 与 Dual 生效：只在低分辨率阶段渐变 |

`"soft"` 等于 `{"floor": 0.4}`，`"clean"` 等于 `{}`。

## `anchor`（`context_anchor`）

| 项 | 取值 | 默认 | 作用 |
|---|---|---|---|
| `placement` | `keystone_front_capstone_back`、`keystone_front`、`keystone_front_back`、`capstone_front_back` | `keystone_front_capstone_back` | 用锚点片段的哪一帧拉回：keystone 是锚点片段的第一帧，capstone 是最后一帧；front 锁进这段开头，back 采样时接在这段结尾之后 |
| `late` | 0、0.25、0.5、0.75 | 0.5 | 接在结尾之后的那一帧从一采进行到多少比例的步数时开始作用；越晚越能自己决定姿势，越早越被拉向锚点。`keystone_front` 没有后置帧，不用写 |
| `noise` | 0、0.6、0.8、1 | 0.8 | 上下文噪声：一采时，从上一段保留的画面带多少当前噪声，越接近新画面越少，让这段不精确照抄它们。可防长镜头纹理越来越硬；固定机位时颜色可能稍微更暗。0 为关闭 |

`"default"` 等于 `{}`。从这段重新锚定写 `"restart"`；要同时改上下文噪声写 `{"restart": true, "noise": 0}`，不能和 `placement`、`late` 一起写：重新锚定的那段本身不被拉回，只有上下文噪声对它有作用。
