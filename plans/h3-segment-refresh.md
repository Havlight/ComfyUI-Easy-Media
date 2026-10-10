# H3 片段 Refresh 實作計畫

日期：2026-10-10。設計基線：`main` @ `07fa7da`（已含 segment LoRA）。狀態：設計定案，尚未實作。

新增每段可選的 refresh：在**低解析軌跡上**，把本段結果的後段重新採樣一次，再交給放大／lift 與下一段，用來減輕長鏈接續的「影印效應」（每段都把上一段輸出當標準答案，誤差逐段累積）。

不改時間格點、39 幀 context、stage 名稱、artifact 格式與交付範圍。**關閉時 graph、recipe 與任務指紋必須與基線位元相同。**

分支：實作時從 `main` 開 `feat/h3-segment-refresh`。

## 1. 定位

| | 內容 |
|---|---|
| 能處理 | 重新生成中高頻外觀（蠟感、燒焦感、細節流失）；用重新加噪、重跑一段的方式收斂採樣誤差（Restart sampling）；減弱 context 前綴對新內容逐格複製的程度 |
| 不能處理 | 低頻色彩漂移（飽和度逐段升高）。中等強度的重新去噪會保留低頻，跟 Reddit 作者的觀察一致，另列在第 10 節的後續項目 |
| 排除的成因 | Easy-Media 用 `BasicGuider`，SelfLift 用 cfg 1.0，所以 CFG 逐段放大**不是**本專案的漂移來源 |

## 2. 設計決策

| 決策 | 結論 | 理由 |
|---|---|---|
| refresh 對象 | 低解析鏈，放在主路徑上 | carry、成品、hi-res 鏈要同源。只 refresh 存下來的 carry，兩條鏈就不再同源，會重現 AIMixer 關掉 `native_low_carry` 時的接縫閃爍 |
| 解析度 | 與被 refresh 的結果相同 | 不需要 hi-res 和低解析之間的轉換 |
| 接縫處理 | 單次採樣內用靜態分數遮罩，前綴沿用 Drift 的 taper | 不做事後交叉溶接，因此沒有重影 |
| 設定層級 | 每段 task `content` 欄位，由產生結果的那一段決定 | 只影響本段和後續段；上一段的存檔不變 |
| 與接續模式的關係 | 獨立欄位，只在 Context／Drift 段生效 | 避免 `context_refresh`、`drift_refresh` 這類組合越來越多 |
| 實作方式 | Dual／Single 用標準 sampler 節點，加兩個小節點；SelfLift 在 sampler 內部 restart | 沿用 preview、stage timing、中斷、segment LoRA |

### 否決的方案

| 方案 | 否決理由 |
|---|---|
| 把 hi-res 縮小，回饋給低解析鏈 | AIMixer `native_low_carry=False` 的實測結果是接縫閃爍、變糊；Dual 還要另外加轉換步驟 |
| 讓下一段的 Prepare 改選 context 來源 | 問題同上，而且要改 stage 契約 |
| Reddit 作者的 refine 加 latent 交叉混合 | 兩個分開採樣的結果線性混合，會產生重影和背景跳動 |
| 每段統計值拉回第一段 | 場景和光線本來就會變，硬拉回去會出錯 |

## 3. 演算法

### 3.1 強度

`refresh_strength` = s，範圍 [0.1, 0.9]，預設 0.5。意思是「**重跑低解析軌跡的最後 s 比例**」，跟 ComfyUI denoise 的直覺一致（Reddit 作者用的是 0.5–0.6）。

- **Dual／Single／first_pass_only**：N 是第一階段步數，r = clamp(ceil(s·N), 1, N−1)，refresh sigmas = `first_pass_sigmas[N−r:]`。
- **SelfLift**：T 是 `transition_step`（低解析評估次數），r = clamp(ceil(s·T), 1, T−1)，k = T−r，restart sigmas = `sigmas[k:T+1]`。最後一次評估仍落在 `sigma_prediction`，所以 x0 的意義跟原本相同。
- N < 2 或 T < 2 時，回報 `REFRESH_SCHEDULE` 錯誤。上限設為 N−1 或 T−1，確保 refresh 永遠不會從純噪聲開始。
- 用步數比例而不是 sigma 值來定義：H3 的 shift 排程讓 sigma 長時間停在高值，用 sigma 定義的話，0.5 常常只剩最後一步。

### 3.2 遮罩

遮罩只沿 latent 時間軸變化，空間上均勻。P 是前綴的 latent 步數，等於 `video_steps(context_frames)`（39 幀對應 12 步）。

- **影像前綴**：用 `temporal_prefix_weights(P, 4)`（[drift_control_av.py:74](../modules/motion_context/drift_control_av.py)）。遠端是 1.0，接縫端最後 4 步依序是 0.75、0.5、0.25、0。
- **影像生成區**：1.0。
- **音訊**：全部 0，也就是凍結。音訊在第一階段已經決定，refresh 不動它，所以 Lock Audio、音訊時鐘和 assembly 都不受影響。
- 遮罩值量化到 1/256，跟 ComfyUI H3 token 標籤的量化方式一致。
- **遮罩從頭建立，不和第一階段的 `noise_mask` 取 min。** 第一階段的前綴全鎖（Hard）正是 refresh 要放鬆的部分。原生流程中，前綴以外沒有影像鎖：locked video 是走 conditioning，不是遮罩。
- ComfyUI H3 原生支援分數遮罩：遮罩值 m 的 token 以 m·σ 的噪聲等級執行，時間步標籤也同步（`comfy/ldm/minimax/model.py` 的 `_forward`）。**所以不需要 patch 模型。**

前綴之後會被裁掉。放開遠端的前綴，是為了讓模型不要把已劣化的參考看得太清楚；接縫端最後一步完全鎖住，確保接縫連續。這套放開方式 Drift 已經在實際使用中驗證過。Context（Hard）段的 keyframe conditioning 仍然保留，所以錨定程度比 Drift 段強。

### 3.3 模型、conditioning、噪聲

- **模型**：用第一階段的 `task_model`（含 segment LoRA 的第一階段 patch），**不帶 Drift patch**。Drift 的 dynamic mask 會覆寫前綴遮罩，而且不能和其他 dynamic mask 共存。
- **conditioning**：跟第一階段相同的 `positive`，包含 Hard 模式的 keyframes。SelfLift 用 `positive_low`。
- **sampler**：用第一階段的 sampler。SelfLift 固定用 Euler。
- **seed**：`(first_pass_seed + 7919) & 0xFFFFFFFFFFFFFFFF`，跟第一、第二階段都不同。
- **LoRA 階段歸屬**：refresh 屬於第一階段。

### 3.4 各模式流程

```text
Dual:      第一階段 → [refresh] → 同一份 latent ─┬→ 存成 dual_low_prediction（下一段的低解析 context）
                                                 └→ 放大 → 第二階段 → dual_high_final
Single:    唯一一次採樣 → [refresh] → single_final
1st-only:  第一階段 → [refresh] → dual_low_prediction（同時是成品）
SelfLift:  低解析跑到 transition → x0 → [restart：x0 加噪到 sigmas[k]，重跑到 sigma_prediction]
           → 新 x0 ─┬→ 存成 selflift_low_prediction
                    └→ lift → hi-res 照常 → selflift_high_final
```

## 4. 資料契約

- **欄位**：task `content.refresh_strength?: number`。沒有這個欄位代表關閉；關閉時移除欄位，不存 0。
- **有效規則** `effective_refresh_strength(content)`：continuity 是 `context` 或 `context_drift`，而且不是 passthrough，才回傳數值，其餘回傳 `None`。型別或範圍錯誤時回報 `REFRESH_VALUE`。
- **任務指紋**：[task_content_signature](../utils/h3_native_status.py) 只在有效時才加入 `refresh_strength`。這樣關閉時現有指紋位元不變，不會讓所有舊版本都變成過期。
- **recipe**：recipe 已經包含 `task_content`。另外只在有效時加入 `refresh_strength`，方便狀態顯示和續跑驗證。
- **stage 名稱與契約不變**：`dual_low_prediction`、`selflift_low_prediction`、`single_final` 只是內容換成 refresh 後的版本。**下一段的 Prepare 不用改。**
- **切換開關的影響**：本段和後續段會被標成需要重新生成，沿用現有的指紋機制。

## 5. 後端

### 5.1 `utils/h3_segment_refresh.py`（新增，不依賴 torch）

```python
REFRESH_DEFAULT = 0.5
REFRESH_MIN, REFRESH_MAX = 0.1, 0.9
REFRESH_PREFIX_TAPER_STEPS = 4
REFRESH_SEED_OFFSET = 7919

def effective_refresh_strength(content: dict[str, Any]) -> float | None: ...
def refresh_step_count(step_count: int, strength: float) -> int: ...       # r，範圍 [1, step_count-1]
def refresh_video_weights(total_steps: int, prefix_steps: int) -> tuple[float, ...]: ...
```

`refresh_video_weights` 前綴部分直接重用 `temporal_prefix_weights`，再量化到 1/256。沿用 `video_steps` 換算前綴步數，不重寫幀與 latent 步數的換算。

### 5.2 `modules/motion_context/segment_refresh.py`（新增）

- `build_refresh_latent(latent, sigmas, strength, context_frames) -> (latent, sigmas)`：複製 latent，用 `_official_nested_tensor` 建立 `(video_mask, audio_mask)` 遮罩並切出 sigmas。重用 `core.py` 的 `_streams_from_latent` 處理 latent 串流。
- `restore_source_latent(source, refreshed) -> dict`：回傳 `source.copy()`，只替換 `samples`。

### 5.3 節點（加在 `nodes/h3_native.py`）

兩個節點都是 dev-only，跟其他 h3 dev 節點一樣不加 locales。註冊在 [`__init__.py`](../__init__.py) 現有的 h3_native 清單。

| 節點 | 輸入 | 輸出 |
|---|---|---|
| `easy h3SegmentRefreshPrepare` | `latent`、`sigmas`、`strength`（Float）、`context_frames`（Int） | `latent`（附 refresh 遮罩）、`sigmas`（已切） |
| `easy h3SegmentRefreshFinish` | `source`、`refreshed` | `latent`（`source` 的副本，`samples` 換成 refresh 後的版本） |

一定要有 Finish 節點：`SamplerCustomAdvanced` 輸出時會保留輸入的 `noise_mask`。`upscale_by=1` 時，第一階段結果會直接進入 HiResContinuity 和第二階段，如果帶著 refresh 遮罩就會被誤用。Finish 確保下游拿到的 dict 跟沒開 refresh 時一模一樣。

### 5.4 `nodes/project.py`

1. **每段驗證**（約 1210 行的 validation 迴圈）：計算有效強度。`audio_only` 又開了 refresh 時回報 `REFRESH_AUDIO_ONLY`；排程太短時回報 `REFRESH_SCHEDULE`。
2. **recipe**（約 1558 行）：有效時加入 `"refresh_strength": s`。
3. **Dual／Single 分支**：在 `first_pass_latent = first_pass_sample.out(1)`（約 1752 行）之後、`final_latent = first_pass_latent` 之前插入：

```python
if refresh_strength is not None:
    report_segment_step(0.43)
    refresh_prepare = graph.node("easy h3SegmentRefreshPrepare", id=f"refresh_prepare_{task_index}",
                                 latent=first_pass_latent, sigmas=first_pass_sigmas,
                                 strength=refresh_strength,
                                 context_frames=native_plans[task_index].context_frames)
    refresh_guider = graph.node("BasicGuider", id=f"refresh_guider_{task_index}",
                                model=task_model, conditioning=positive)
    refresh_noise = graph.node("RandomNoise", id=f"refresh_noise_{task_index}",
                               noise_seed=(first_pass_seed + REFRESH_SEED_OFFSET) & 0xFFFFFFFFFFFFFFFF)
    refresh_start = graph.node("easy h3SegmentSamplingStart", id=f"refresh_start_{task_index}",
                               sampling_pass="refresh", noise=refresh_noise.out(0),
                               guider=refresh_guider.out(0), sampler=first_pass_sampler,
                               sigmas=refresh_prepare.out(1), latent_image=refresh_prepare.out(0),
                               project_name=safe_project_name, segment_index=task_index)
    refresh_sample = ...  # 與第一階段相同：有 preview 時用 easy h3SamplingPreviewSampler（sampling_pass="refresh"），否則用 SamplerCustomAdvanced
    first_pass_latent = graph.node("easy h3SegmentRefreshFinish", id=f"refresh_finish_{task_index}",
                                   source=first_pass_latent, refreshed=refresh_sample.out(1)).out(0)
```

   `final_latent`（放大輸入）和 `low_stage_context_latent`（存成 carry）都讀 `first_pass_latent`，所以兩者自然是同一份。這段不用 Drift patch 過的 `first_pass_sampling_model`。

4. **SelfLift 分支**（約 1653 行）：有效時，在 `selflift_inputs` 加上 `refresh_strength` 和 `refresh_model=task_model`。

### 5.5 SelfLift sampler

- **`easy minimaxH3SelfLiftSampler`**：在 inputs 尾端新增 optional `refresh_strength`（Float，預設 0，代表關閉）和 `refresh_model`（Model），並轉傳給 `progressive_sample_h3`。
- **`progressive_sample_h3`** 新增同名 kwargs。插入點在 [sampling.py](../modules/selflift/sampling.py) 低解析階段結束後（512 行）、transition 準備前（515 行），而且要在刪除 `positive_low` 之前：
  1. 從 `transition["x0"]` 取出 x0。用 `model.model.process_latent_out` 轉回原始空間，作為 restart 的 `latent_image`（影像和音訊都放進去）。
  2. 遮罩：影像用 `refresh_video_weights`，P 由現有的 `_video_context_prefix_steps(latent_image["noise_mask"])` 取得；音訊全部 0；尺寸配合低解析 shape。
  3. 執行 `comfy.samplers.sample(refresh_model or model, noise(seed+2), positive_low, [], 1.0, ..., sigmas[k:T+1], denoise_mask=refresh_mask, ...)`，在最後一次評估時捕捉 x0。
  4. **只替換 `x0_streams[0]`（影像）**。音訊的 `x0_streams[1:]` 和 `low_streams[1:]` 保持原樣，`auxiliary_next` 照舊計算。後面的 `low_result`（carry）和 lift 都自動吃到新的 x0。
  5. restart 不呼叫 preview 或進度 callback（避免打亂 step_count），只記一筆 `_StageTimer("refresh")`。
  6. 如果 `refresh_model` 是 None，而 `model.model_options` 裡有 Drift wrapper key，就丟出 ValueError。Project 一定會傳入 `refresh_model`。

### 5.6 不需要修改的部分

Prepare、artifact 儲存與 checksum、context slicing、HiResContinuity、第二階段、assembly、音訊時鐘、previous tail frame。

## 6. 前端

1. **型別**：在 `types/multitrack.ts` 的 `MultiTrackSegmentContent` 加上 `refresh_strength?: number`。
2. **正規化**（`lib/multitrack-utils.ts` 約 1688 行）：非有限數值或超出 [0.1, 0.9] 時移除欄位。Shot 和 passthrough 保留原值不刪，因為後端會忽略，指紋也不受影響。
3. **新任務繼承**（約 415–423 行）：跟 `continuity_mode` 一樣繼承上一段的設定，方便延伸長鏈。
4. **Editor 控制項**：放在 `TaskSegmentEditor` 底部工具列，位於接續模式 Select 和任務模式 Select 之間：

   ```text
   [單獨編輯|合併編輯]        任務 3 / 00:00:05:16 ✎        [上下文 ▾] [重採樣 0.5 ▾] [文生視頻 ▾]
   ───────────────────────────────────────────────────────────────────────────────────────────
   接續上一段，第一階段後重採樣後段 0.5（多跑約 50% 的低解析步數，不修正色彩漂移）。
   ```

   - 用 shadcn `Select`（`h-8 w-24 bg-card text-[10px]`，與相鄰 Select 一致），加 Tooltip。選項為「重採樣：關、0.3、0.4、0.5、0.6、0.7」；透過 API 設定的其他值要另外顯示成一個選項，不可被改掉。
   - 顯示條件：MiniMax 格式、不是 passthrough、continuity 是 `context` 或 `context_drift`。Shot 段和第一段不渲染這個控制項，不用 disabled。
   - 修改走 `handleDropdownContentChange`，支援時間軸多選批次修改。批次時跳過 Shot 段；多選時若各段值不同，顯示「混合」。
   - 切到 Shot 時移除欄位；第一段強制移除，比照現有強制 continuity 的處理。從 Shot 切回 Context 時不自動恢復舊值。
   - 底部狀態列（現有的 `h3Native.contextStatus`）在開啟時改用新字串 `h3Native.refreshStatus`，帶入強度並說明耗時、不處理色彩。
   - 中文標籤暫定「重採樣」（不用「刷新」，以免和重新整理專案清單混淆）；英文用 "Refresh"。
5. **i18n**（`frontend/messages/en.json`、`zh.json`）：新增 `multitrack.refresh`、`multitrack.refreshOff`、`multitrack.refreshMixed`、`multitrack.refreshTooltip`、`h3Native.refreshStatus`、`samplingPreview.refresh`。tooltip 要說明：重跑低解析後段，會增加耗時，不修正色彩漂移。
6. **v1 不做**：PreviewArea 的 continuity 選單、ProjectVideoCombine 的 refresh 標記。

## 7. 相容性

| 項目 | 結果 |
|---|---|
| Single／Dual／first_pass_only | 支援，refresh 在第一階段之後 |
| SelfLift | 支援，在 transition 前 restart |
| passthrough、Shot、第一段 | 不適用（有效規則回傳 `None`） |
| audio_only | 回報 `REFRESH_AUDIO_ONLY` |
| Context／Drift | 都支援；refresh 不用 Drift patch |
| segment LoRA | 用第一階段的 `task_model` |
| Lock Audio | 不受影響，音訊遮罩為 0 |
| Locked video（Reference/Edit） | 允許：conditioning 相同。列入 GPU 檢查 |
| tiling | Dual 低解析和 Single 原本就不 tile，refresh 跟著不 tile；SelfLift 的 restart 在低解析，也不 tile |
| sampling preview | Dual／Single 顯示「Refresh」階段；SelfLift 的 restart 不顯示 preview |
| previous tail frame、VAE fallback | 不受影響 |

## 8. 測試

**單元測試**（新增 `tests/test_h3_segment_refresh.py`）：
- `refresh_step_count`：N=8 時，s=0.5 得 4、s=0.1 得 1、s=0.9 得 7；N=1 時報錯。
- 權重：P=0、P=12，taper 的值、量化結果、生成區全部為 1。
- 有效規則：shot、passthrough 回傳 `None`；型別或範圍錯誤時報錯。
- **指紋回歸**：沒有欄位的既有 fixture，hash 必須與基線相同。
- Prepare 節點：遮罩是 nested 結構、音訊遮罩全 0、sigmas 切片正確。Finish 節點：還原來源的所有 key，包含 `noise_mask` 本來存不存在。

**Graph 測試**（`tests/test_h3_native_graph.py`）：
- Dual + Context 開 refresh：refresh 節點位於第一階段和放大之間，`h3NativeResult` 的 `latent` 和 `low_latent` 都接 `refresh_finish`。
- Single、first_pass_only 開 refresh 的接線正確。
- Drift 段的 refresh guider 用 `task_model`，不是 `context_swap.out(0)`。
- SelfLift 節點收到 `refresh_strength` 和 `refresh_model`。
- **關閉時 graph 與基線完全相同。** 既有的 `tests/test_minimax_node.py:3481` 等斷言不應改變。

**SelfLift**（`tests/test_selflift_sampling.py`）：mock `comfy.samplers.sample`，確認：
- restart 用 `sigmas[k:T+1]`。
- `low_result` 和 lift 輸入都用 restart 的 x0。
- 音訊串流沒有被替換。
- 沒有呼叫 preview callback。

**前端**（`src/tests`）：
- 控制項的顯示和隱藏規則。
- 批次修改、切到 Shot 時移除欄位、正規化、繼承。
- en／zh 的 i18n key 都存在。

**手動 GPU 測試**（`tests/manual/h3_native_gpu_matrix.py` 增加 refresh 欄）：
- Dual／Single／SelfLift × Context／Drift × refresh 開或關。
- 跑 8 段以上的長鏈，觀察第 6 段之後的細節。
- 接縫不能閃爍或跳色。
- 強度測 0.3、0.5、0.7。
- 記錄增加的耗時。

## 9. 提交順序

依 AGENTS.md：source 和 `dist/` 分開提交；前端提交前執行 `bun run build:release`。

| # | commit | 內容 |
|---|---|---|
| 1 | `feat: add h3 segment refresh helpers and nodes` | 5.1–5.3，以及單元測試 |
| 2 | `feat: refresh dual and single first-pass output` | 5.4 的 1–3 項、recipe、驗證，以及 graph 測試 |
| 3 | `feat: restart selflift low phase for segment refresh` | 5.4 的第 4 項、5.5，以及 SelfLift 測試 |
| 4 | `feat: add segment refresh control to task editor` | 第 6 節，以及前端測試 |
| 5 | `chore: build release assets for segment refresh` | `dist/` |
| 6 | `docs: document segment refresh` | README、README_CN、CHANGELOG；`skills/easy-media-multitrack-workflow` 的 schema 文件，`patch_workflow.py` 驗證 `refresh_strength`；依 AGENTS.md 同步 Codex skill |

## 10. 已知限制與後續

- **色彩漂移**：refresh 不處理。候選做法是「接縫局部 DC 校正」：比較生成區開頭幾步與前綴最後幾步，算出每個 channel 的均值差，限制在 ±0.05 以內後扣除，參考 AIMixer 的 `match_lift_prefix_dc`。另開計畫處理，不併入本次。
- **常數待調整**：前綴 taper 的 4 步，以及「遠端前綴完全放開」的作法，先固定成常數。GPU 測試有需要時再考慮開放成進階設定。
- **耗時**：Dual／Single 增加 r 步低解析採樣；SelfLift 增加 r 步低解析 restart。
- **Shot 段不提供 refresh**：Shot 的輸出本來就是全新生成，refresh 只會增加耗時。
