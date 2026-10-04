# H3 純 latent 接續可行性紀錄

檢查日期：2026 年 10 月 3 日。Easy Media 基線：`22c91618edd05e04e2a0192525a580e8bae5d28f`。本文件記錄程式碼檢查、基線測試和待驗證事項；產品設計與實作順序見[完整計畫](h3-native-latent-continuation.md)。本次只提交文件。

## 結論與信心水準

**可以在現有架構內實作純 latent 接續，並保留原本 Editor 的主要操作。** 倉庫已有原生 AV latent 裁切、mask、Drift、latent upscaler、SelfLift 和 safetensors 儲存能力。主要工作是統一時間規則、保存完整來源與階段資訊、移除生成鏈上的 VAE round-trip，並讓編輯器與後端使用相同的合法性判定。

這個結論是「有程式碼基礎、路徑可整合」，不是「新方案已通過生成驗證」。本輪沒有修改產品、沒有跑 H3 GPU 推論，也沒有證明某一種接續方法的畫質普遍最好。AV 相位、跨階段 mask、連續多段畫質仍是實作前半段的必要驗證門檻；通過之前，不發布純 latent 保證、不更換預設接續方法。

## 已確認的程式碼依據

以下連結相對於本倉庫；函式名稱比行號更適合後續追蹤。結論適用於上述基線。

| 區域 | 已確認的行為 | 對實作的影響 |
| --- | --- | --- |
| [Project](../nodes/project.py) 的 `_h3_encode_context_media` 與輸出處理 | delivered media 經過 trim 後會重新編碼；另外會傳 `anchor_images` 和 VAE 建立短 anchor | 只改 context 儲存檔不夠，主 context、短 anchor、低解析度分支都要改 |
| Project 時間計算 | context source 固定 22 幀，generation 額外加 34 幀，再處理重複 prefix 和多餘尾幀 | 必須用明確的 raw／overlap／delivered 映射取代常數補償 |
| [Motion Context](../modules/motion_context/core.py) 的 `_video_tail_bounds` | 已能依 `(1,4,4,4,4)` 的 video token 週期裁切，要求來源起點 `% 5 == 0` | 可重用裁切能力；內部切點仍須新增完整 raw 來源與 offset 驗證 |
| 同檔 `_hard_av_latent`、`_video_anchor_from_context_latent` | 已能複製 AV prefix、合併 mask、從 native context 取得短 video anchor | Context 可保留 guide 加短 anchor 的特性；新 Masked 不必從零重寫 |
| 同檔 `_audio_tail_from_latent` | 以 40 Hz 和 24 fps 換算，估算 audio overhang；異常時目前會 warning 後忽略 | strict 路徑不能忽略異常，必須保存相位並拒絕無法解釋的來源 |
| [Drift](../modules/motion_context/drift_control_av.py) | 複製前段 latent，按 sigma 更新 denoise mask；不往儲存的來源直接加噪 | Drift 本身可以是純 latent；要修正來源、雙模型 patch 與 audio ownership |
| [SelfLift](../modules/selflift/sampling.py) 的 `progressive_sample_h3` | `rho > 0` 且 `w_max > 0` 會要求 pixel anchor；Project 的 context 路徑目前設 `rho=0.1` | 現行 SelfLift continuation 不能直接宣稱純 latent；需要明確的 latent-only recipe |
| 同檔 `low_result` | 保存低解析階段最後一次預測的 clean `x0`，不是完成整個 schedule 的低解析成品，也不是 noisy resume state | 必須分開記錄 artifact 的用途、sigma 和階段，不能只用 low／high 命名 |
| Project Dual upscale | 有合適的 `upscale_model` 時使用 latent upscaler；缺少模型且放大時走 decode／resize／encode | strict 必須阻止像素放大回流；fallback 才能使用該路徑 |
| Project `_h3_second_pass_model` | 第二 loader 只替換 MODEL；CLIP、video VAE、audio VAE 仍來自第一 loader | 保留 model LoRA patch；不能暗示第二 loader 的 CLIP LoRA 也會重新編碼文字 |
| Project 採樣解析 | Dual 支援各 pass 的 sampler／sigmas；custom 需成對。SelfLift 使用單一完整 schedule 和 Euler | 保留明確模式語義，不能把 Dual 的第二段 sigmas 強塞進 SelfLift |
| [Project 儲存](../utils/h3_project.py) | 已有原子 safetensors 寫入及版本 manifest；checkpoint 判定仍不足以證明相位、階段、依賴相容 | 擴充既有儲存工具，不另造互不相通的 sidecar 系統 |
| [Last frame](../utils/h3_previous_frame.py) | 取已選版本的實際末張影像作 reference，之後進入 image encoder | 這個語義不等於複製 native video tail；strict 不能偷偷替換成另一種功能 |
| [時間軸工具](../frontend/src/lib/multitrack-utils.ts) 與 [Smart Split](../frontend/src/lib/smart-split.ts) | 多個操作入口按一般 frame 切割、移動、resize；複製 content 不會自動修正接續角色 | 必須集中成編輯交易，不能只修 duration input |

本機 ComfyUI 的 Git HEAD 是 `830232b856045ca2892833212d7771078a13edd5`。實際工作目錄有既存變更，本輪沒有更改它。讀到的 `comfy/model_base.py` 已有 H3 token mask pooling／量化與 inpaint 處理，`comfy/ldm/minimax/model.py` 已有 masked velocity 修正。這支持整合方向，但未在本輪證明真實 sampler 的完整行為；後續須做能力測試，不能只比版本號或重複安裝全域 monkey patch。

## 時間與音訊推導

令完整生成視窗為 `F`、接續重疊為 `C`、新增可見幀數為 `D`，H3 使用 24 fps：

```text
Shot：                  F = D = 17k + 5
Continuation：          C = 17m + 5
                        F = C + D = 17k + 5
因此：                  D = 17(k - m)
Video latent steps：    T(F) = 2 + 5k
Audio latent ticks：    A(F) = round(40F / 24)
```

所以「每一段 UI duration 都 snap 到 17k+5」不正確。Shot 和 continuation 的可見長度有不同餘數；接續切點還要相對於來源 raw window 計算，不能直接檢查全時間軸位置 `% 17`。

本輪使用現有 `_pixel_frames`、`_steps_for_frames`，逐一驗證 `1 <= k < 100`、`0 <= m < k` 的 **4,950 組**長度：token 數、來源 tail 起點相位與 `D % 17 == 0` 均符合推導。這是幾何檢查，沒有測新編輯器。

同時符合 video 格點且有整數 audio prefix 長度的序列是 **39、90、141、192、243……**，即 `39 + 51n`。

| 幀數 | 精確 audio ticks | 取整殘差 |
| --- | --- | --- |
| 22 | 110/3 | +1/3 tick |
| 39 | 65 | 0 |
| 243 | 405 | 0 |
| 260 | 1300/3 | −1/3 tick |
| 277 | 1385/3 | +1/3 tick |

**選 39 幀只解決 prefix 長度，沒有自動解決整段來源的 audio 相位。** 新方案需保存來源 audio tick 起點、raw 時鐘偏移、取整殘差和可見 sample 範圍；輸出以累積 frame 邊界計算 sample budget。只加一個 `round()` 或只在 metadata 填相位，不能視為已解決接續誤差。

實作時必須用人工可識別的 video token／audio tick 與 waveform impulse 驗證：slice、跨段映射、overlap ownership、trim、resume 後的時間都一致。對無法精確裁切的 audio 邊界，僅允許已驗證的原生取樣與明示的輸出對齊策略；不得在 strict 中插入 decode→encode、偷偷平移來源音軌或捏造 latent。若初版只能支持較小的合法集合，編輯器和後端必須同時使用該集合並說明限制。

## 外部方法的可借用部分

以下是 2026 年 10 月 3 日讀取的固定版本。它們提供實作參考與作者測試，並不構成本 fork 的畫質驗證或廣泛共識。

| 來源 | 固定版本與文件 | 本計畫使用範圍 |
| --- | --- | --- |
| Context Loop | [`5d62cbc`](https://github.com/ethanfel/ComfyUI-MiniMaxH3-Context-Loop/blob/5d62cbc76b1d42f08391c97d47073ecca6dd7301/docs/AUDIO_AND_CONTINUITY.md) | Soft AV 保留 video prefix、只釋放末端 generated audio；39 幀 AV 基線；Drift 按完整 schedule 處理模型切換。其 Color-Stable Drift 仍有 VAE round-trip，不納入 strict |
| OBVPM | [`4a027a0`](https://github.com/chanon/comfyui-obvpm-timeline/blob/4a027a09cae607f9b36f8227a4f46ed05ccc8123/docs/h3-nodes.md) | raw sampler latent 與交付影片分離、接縫來源映射。借用設計，不導入整套 timeline／檔案格式 |
| Continuity | [`c9c8ba9`](https://github.com/roadmaus/ComfyUI-Continuity/blob/c9c8ba9c1c208de938d22daf0eb3f18dbffbe3c8/docs/timeline.md) | masked handoff 的作者測試支持列為候選；其跨配置測試有限，不能據此直接定為普遍最佳 |
| TimelineDirector | [`a81f13b`](https://github.com/Songssx/ComfyUI-MiniMaxH3-TimelineDirector/blob/a81f13b8af4a162467cec4dc377f40b7354d7ffc/README.md#finite-direct-latent-continuation) | low／high lineage、原生 AV carry、保留 incoming overlap audio 的組裝方式。該庫仍把 Drift 長鏈比較標為 experimental |
| H3 Extender | [`67d3c12`](https://github.com/tritant/ComfyUI_MiniMax_H3_Extender/blob/67d3c127fcae80d2e56264fb64c450186b55fd78/motion_context_ram.py) | token phase 與來源 audio overhang 的裁切檢查 |

因此目前最合理的是保留 Context／Drift 的可辨識用途，先修共同來源，再增加一個 Masked 方法做比較。沒有證據支持現在就移除舊方法，或把所有看似 latent 的第三方方法都算作純 latent。

## 本輪實際驗證

環境：Python 3.12.3、PyTorch `2.14.1+cpu`、pytest 9.1.1、Bun 1.4.2、Node 22.20.0。這個驗證環境無 CUDA；不代表使用者其他 ComfyUI 執行環境沒有 GPU。

為避開原路徑 `#` 的 Vite 解析問題與上層 ComfyUI pytest 設定，從基線 `git archive HEAD` 建立暫存副本。前端使用已安裝的暫存依賴；後端停用副本的頂層插件 `__init__.py` 啟動入口，使用既有測試的 mock。產品 source 和測試內容未修改，新增依賴只在暫存 Python 環境。這是隔離單元驗證，不是完整 ComfyUI 啟動測試。

| 驗證 | 結果 | 可支持的結論 |
| --- | --- | --- |
| 4,950 組 frame／context 算術 | 通過 | 目前 video token 幾何可支持新時間規則 |
| `multitrack-utils`、`smart-split`、`MultiTrackWidget`、`MultiTrackToolbar` 的 Vitest | **163 通過** | 既有時間軸操作有可重用的回歸測試基線 |
| 下列 7 個 Python 測試檔 | **251 通過、2 失敗、2 略過** | 部分現有 native mask、SelfLift、audio lock 和 preview 行為已有 CPU 測試；不能稱為全綠 |
| `bun run build:release` | **成功，11 個 JS outputs** | 基線前端可以打包；產物只留在暫存副本，沒有混入文件 commit |
| H3 GPU 推論、畫質比較、瀏覽器互動驗收 | **未執行** | 不可宣稱新方法品質或整合已通過 |
| `test_h3_project.py`、`test_h3_previous_frame.py` | **本輪未完成** | 直接收集受 ComfyUI `folder_paths` 等環境依賴阻擋；後續需在正式 harness 補上 |

Python 執行範圍：

```text
tests/test_h3_motion_context.py
tests/test_h3_audio_lock.py
tests/test_h3_latent_upscale.py
tests/test_selflift_sampling.py
tests/test_sampling_preview.py
tests/test_multitrack_info_output.py
tests/test_minimax_utils.py
```

兩個失敗都位於既有 `test_multitrack_info_output.py`：

```text
test_multi_images_loader_resizes_ordered_image_list
test_multi_images_loader_rejects_more_than_25_images
TypeError: MultiImagesLoader.execute() missing 1 required positional argument: 'image_data'
```

這些失敗是在未修改的基線 source 上得到；本輪不順便改功能。功能實作前應先釐清是測試呼叫方式還是 API 回歸，必要時獨立修復與 commit。兩個略過項目均因隔離環境無 FFmpeg，不算通過。

重現指令，工作目錄為上述隔離副本：

```bash
python -m pytest -q --tb=short -c /dev/null --rootdir=. --confcutdir=. \
  tests/test_h3_motion_context.py tests/test_h3_audio_lock.py \
  tests/test_h3_latent_upscale.py tests/test_selflift_sampling.py \
  tests/test_sampling_preview.py tests/test_multitrack_info_output.py \
  tests/test_minimax_utils.py

# 在 frontend 執行
bun run test src/tests/multitrack-utils.test.ts src/tests/smart-split.test.ts \
  src/tests/MultiTrackWidget.test.tsx src/tests/MultiTrackToolbar.test.tsx
bun run build:release
```

## 實作前半段必須關閉的疑點

1. **AV 時鐘**：連續 20 段、非零 source offset、切割後再接續、不同 raw 長度的 phase 與 sample ownership 必須可重現且不累積誤差。
2. **SelfLift**：證明關閉 pixel correction 的 native 路徑不再呼叫 encoder，low `x0`、noisy state、high final 各自用途正確；再比較品質差異。
3. **跨模型與 sigma**：在本機 ComfyUI 真 sampler 上證明 mask pooling、velocity、完整 schedule 與 split resume 一致，模型 patch 不重複、不遺失 LoRA。
4. **Last frame 和外部來源**：保留功能原意，strict 能辨認由已生成畫面回流的 encoder；沒有 native 等價物時清楚拒絕，不製造假的 native reference。
5. **方法品質**：固定 seed／prompt／尺寸／schedule 對照原生 Context、原生 Drift、Masked，記錄接縫、動作、顏色、聲音與資源；只有通過才提升 Masked 為新接續預設。

任一門檻失敗，先縮小明確支援的配置或修正底層，不能以「允許 fallback」掩蓋資料損壞、階段狀態錯誤或不相容的模型。
