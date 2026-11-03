# H3 Anchor 改用 Geiru 完整做法

日期：2026-11-03。實作基線：`main` `d65be91`（Anchor 接續已合併）。依據：roycho87/geiru_context_suite `9279896`（原 clown_context_suite，我們移植自 `8dab818`）與 roycho87/NerdNotes `30095e9`。狀態：P1–P6 已實作並通過單元測試；實機驗證完成（見下方「對照組結果」與「驗證結果」），換場景測試未跑。

## 實作時補充的決定

- 計畫檔的 `anchor` 多一種寫法 `{"restart": true, "noise": 0}`：restart 段也用 Context Noise，只寫 `"restart"` 時讀出再套用會把 noise 重設成預設。restart 不能和 `placement`、`late` 一起寫。
- 預覽播開頭片段時，前一段若用鎖定的歌曲或影片原聲（`audio_locked`），照合併結果保留前一段的聲音；其他情況改播開頭片段的聲音。
- 在 Project Video Combine 刪除版本時一併刪除開頭片段檔。

## 對照組結果

`main` 的 Anchor（全鎖 39 幀、沒有 Context Noise），石造長廊固定機位 10 段，single、`medium`、seed 20261104、960×544。量每段結尾前 45 幀往前 10 幀，左右牆兩個固定區塊，對第 1 段：

| 段 | 色偏 左/右 | 亮度 左/右 | 紋理 左/右 |
| --- | --- | --- | --- |
| g01 | 0 / 0 | 0 / 0 | 9.68 / 6.80 |
| g05 | 0.65 / 0.70 | −0.53 / +0.51 | 12.43 / 9.13 |
| g10 | 0.89 / 2.02 | −0.71 / +1.71 | 16.95 / 13.49 |

接縫跳動：0.89、2.06、0.77、1.64、2.81、1.30、1.39、1.29、0.69。顏色幾乎沒漂，紋理逐段變硬到約 1.75–2 倍，新做法主要看紋理。

## 驗證結果

同計畫、同設定，另跑新做法（`geiru-new`）、新做法關噪聲（`geiru-noise0`）與 Drift（`geiru-drift`，接縫 `soft`）。g10 左/右：

| 做法 | 色偏 | 亮度 | 紋理 |
| --- | --- | --- | --- |
| 舊 Anchor（全鎖 39） | 0.89 / 2.02 | −0.71 / +1.71 | 16.95 / 13.49 |
| 新 Anchor（鎖 22、噪聲 0.8） | 1.78 / 3.01 | −0.73 / +2.17 | 16.34 / 14.20 |
| 新 Anchor、噪聲 0 | 0.58 / 2.97 | +0.20 / +2.61 | 19.74 / 16.11 |
| Drift（soft） | 4.10 / 8.76 | +3.64 / +7.65 | 20.69 / 17.71 |

- 接縫：舊做法 8 個接續段的結尾都被 capstone 拉回 g01 的結尾姿勢（看鏡頭微笑、書抱胸），下一段再從那裡接；新做法合併影片在接縫處播下一段重畫的版本，姿勢照提示詞走，使用者看片確認接縫順很多。
- 紋理：新做法與舊做法相近；新鎖法下關掉噪聲明顯更硬，所以噪聲預設維持 0.8。
- 顏色：新做法比舊做法略多（capstone 的修正不再傳下去），仍遠小於 Drift。
- 合併與預覽：合併影片在每個接縫都播開頭片段；Project Video Combine 預覽會載入開頭片段。
- Dual 加 restart（`geiru-dual`，5 段，d03 restart）：一采、二采都照新鎖法；restart 段只有噪聲、沒有錨點幀，之後的段落以它為錨點；4 個開頭片段是二采解析度，合併影片都播開頭片段。
- 比較影片在 `output/easy_media/compare/geiru_anchor/`。

另發現：內建瀏覽器窗格隱藏時頁面被節流，套用超過 120 秒會被標為失敗，頁面晚到的回報收到 409 後，網橋就不再接工作，要重新整理頁面。這是既有的網橋問題，與本計畫無關。

作者所有測試數據都來自同一組設定：重疊 39 幀只鎖前 22 幀、Context Noise、Keystone、Late Lock capstone，成片時重疊由下一段播。我們現在的 Anchor 只移植了 Keystone 與 Late Lock，接續則全鎖 39 幀，與他的前提不同。這次把 Anchor 改成他的完整做法，Drift、Context、Shot 不變。

## 決定事項

| 項目 | 決定 | 理由 |
| --- | --- | --- |
| 接續幾何 | 重疊 39 幀，鎖前 22 幀，後 17 幀由這段重畫 | 作者的推薦設定；段尾常已收尾，全鎖會讓動作接不下去 |
| 成片接縫 | 前一段最後 39 幀改播下一段的開頭 39 幀 | 作者預設接法；與現有聲音規則（下一段擁有重疊）一致 |
| Context Noise | Anchor 預設開，強度 0.8 | 作者最佳組合；50 段海灘基準全程開啟 |
| 舊的全鎖 39 幀 | 直接取代，不保留選項 | 不需要向後相容；少一種組合 |
| SelfLift | 維持執行前報錯 `ANCHOR_SELFLIFT` | 低解析度階段的 context 注入與新幾何衝突，另案處理 |

## 新的 Anchor 行為

H3 影片 latent 以 17 幀為一個區塊（1、4、4、4、4 幀）。這段的開頭 39 幀是 latent 0–11，與前一段最後 39 幀是同一段時間。

```text
前一段        … L-40 | L-39 ……… L-18 | L-17 ……… L-1 |
這段 raw          0 ………… 21   | 22 ………… 38  | 39 → 交付範圍
                  鎖住（latent 0–6）  重畫（latent 7–11）
成片         前一段播到 L-40，接著播這段的 0–38，再接這段的交付範圍
```

### 一採（Single 與 Dual 的第一次採樣）

- **接續**：前一段來源窗口不變（最後 39 幀、12 個 latent），只抄前 7 個到這段的 latent 0–6，遮罩 0；latent 7–11 遮罩 1。
- **聲音**：只鎖前 22 幀底下的聲音，也就是來源窗口的前 `round(22 × 5 / 3) = 37` 個 tick，與鎖住的畫面尾端對齊。後 17 幀的聲音重新生成。不加 feather（作者預設），實跑時聽接縫再決定。
- **Context Noise**：latent 1–6 的遮罩設為 `strength × (1 − k / 5)`，k = 0…5，預設依序 0.8、0.64、0.48、0.32、0.16、0。latent 0 留給 keystone。ComfyUI 的 H3 原生支援部分遮罩：遮罩值 m 讓該列噪聲是當前的 m 倍，模型也被告知（`comfy/ldm/minimax/model.py`）。
- **Keystone**：照舊鎖進 latent 0。
- **Capstone**：照舊從 late 指定的步數起接在影片尾端，每次呼叫後切掉。預設 late 0.5，在 8 或 9 步下即第 5 步起，與作者相同。
- **放回**：採樣後把 latent 0–6 全部換回前一段的真 latent，包含輸出與每步預覽的 x0。現有 `_restore_wrapper` 已支援多個位置。

### 二採（Dual 的第二次採樣）

- 鎖前 22 幀的高解析度來源，keystone 用高解析度版本，capstone 照舊從第一步接上，聲音整段沿用一採。
- 不加 Context Noise：作者沒有二採，二採的噪聲本來就從 0.5 起。
- 被重畫的 17 幀來自一採放大，由二採和這段其他新畫面一起精修。

### 錨點與 restart

- 錨點規則不變：鏈的第一段，或最後一個 restart 段。
- Keystone 取「錨點段自己畫出的第一個單幀 latent」：Shot 是 latent 0；接續段改為 `5 × ceil(22 / 17) = 10`（第 34 幀），不再是現在的 latent 15。Capstone 照舊取最後一個單幀 latent。Passthrough 來源維持現行取法。
- Restart 段：鎖 22 幀加 Context Noise，不放 keystone 與 capstone。這對應作者轉場段「keystone 關、noise 開」的處理。連續標 restart 等於作者關閉保護，最後一個 restart 成為新錨點，因此不另做「關閉保護」選項。

## 成片與預覽的接縫歸屬

### 歸屬條件（前後端共用同一份規則）

下一段擁有前一段最後 39 幀的畫面，需同時符合：

1. 下一段是 native Anchor 接續段，且有開頭片段檔。
2. 兩段在清單中相鄰且都啟用。
3. 下一段的 `parent_artifact_id` 等於前一段目前選用版本的 `artifact_id`。
4. 前一段結尾沒裁切，下一段開頭沒裁切。

任一條件不成立就各播各的，與現有聲音規則相同。最後一段整段保留。

### 生成端

- `easy h3NativeMediaView` 多輸出開頭片段：raw 第 0 幀到 context 結束的畫面與聲音（39 幀）。只有 Anchor 接續段輸出。
- `_h3_native_output` 另存一個小影片檔 `.staging_lead_in_{task_index}`，交給 `easy h3NativeArtifact`。`GENERATION_FILE_FIELDS` 加入 `lead_in`，專案檔名清理規則一併加入。
- 開頭畫面原本就隨整段解碼，不增加採樣或解碼次數。

### Project Video Combine「合併」

- `compose_h3_project_video`：符合歸屬條件時，前一段只取到結尾前 39 幀，插入下一段的開頭片段，再接下一段。時間軸總長與各段位置不變。
- 聲音沿用 `build_native_audio_views`。把它的判斷抽成共用函式，畫面與聲音用同一個判斷。

### Project Video Combine 預覽播放器

- `load_h3_project_data` 在每個版本附上開頭片段路徑。
- 前端新增 `lib/h3-project-playback.ts`，照後端規則把 clip 清單轉成播放序列。`ProjectVideoCombineWidget` 依播放序列播放、預載與跳轉，時間軸上的 clip 顯示不變。
- 預覽播開頭片段時用它的聲音，所以預覽聲音也會跟成片一致。這也修掉現在預覽與成片在重疊處聲音不同的落差。
- MultiTrack 編輯器預覽（播輸入素材）與版本比較（各播各的）不改。

## 舊結果過期

- Anchor 段的設定簽章加入方法版本，讓改版前的 Anchor 結果顯示為需要重跑；其他模式的結果不受影響。不提高全域的 `TASK_FINGERPRINT_VERSION`。
- 先跑 single 再放大成 dual 時，必須使用新做法的一採；舊的 single Anchor 結果過期。

## 設定介面與 agent

- `utils/h3_anchor.py` 與 `frontend/src/lib/h3-anchor.ts` 加入 `anchor_noise`，選項 0（關）、0.6、0.8、1.0，預設 0.8。0.6 與 1.0 是作者列為待測的強度。Restart 段的 noise 照該段設定。
- Editor 的 Anchor 設定加入 Context Noise 選項與說明，中英文訊息同步。
- 計畫檔 `anchor` 維持只用名稱：`"default"` 即新的完整做法，`"restart"` 不變；使用者給數值時才寫 `{placement, late, noise}`。
- Agent 規則改照作者：
  - 轉場段、鏡頭一直在變的段標 restart。
  - 新場景會持續數段時，第一個完整呈現新場景的段也標 restart，之後的段自動以它為錨。
  - 使用者抱怨固定機位逐段變暗時，可建議 noise 0（作者在固定機位的結果：noise 改善紋理但顏色偏暗）。
  - 移除「段尾重影就換 seed 重跑」的指引，改為說明只有最後一段或接縫被改過時才可能看到。

## 不做的

- Geiru Hybrid Windows、Geiru Anchoring、Keystone `latent_folder`、Late Lock 的 keystone 晚鎖。
- Step Echo、Fresh Singles：作者的私有節點，未發布。
- Anchor 支援 SelfLift。
- Drift、Context 的幾何與接縫歸屬。

## 實作步驟

分支：從 `main` 開 `feature/h3-geiru-anchor`。

1. **P1 後端接續**
   - `modules/motion_context/anchor.py`：22/17 幾何、聲音 37 tick、Context Noise 斜坡、放回 latent 0–6、keystone 索引。
   - `nodes/h3_native.py`：Anchor 節點加 `noise` 輸入，restart 路徑也加 noise；`anchor_source_latents` 的呼叫改傳鎖住幀數。
   - `nodes/project.py`：一採傳 noise，二採傳 0。
   - `utils/h3_anchor.py`：`anchor_noise` 設定、簽章方法版本。
2. **P2 生成端開頭片段**
   - `easy h3NativeMediaView`、`_h3_native_output`、`easy h3NativeArtifact`、`utils/h3_native_artifacts.py`、專案檔名規則。
3. **P3 合成歸屬**
   - `utils/h3_native_assembly.py` 共用判斷。
   - `utils/h3_project.py` 的 `load_h3_project_data` 與 `compose_h3_project_video`。
4. **P4 前端**
   - `lib/h3-project-playback.ts` 與 `ProjectVideoCombineWidget` 播放序列。
   - `lib/h3-anchor.ts`、`lib/h3-plan.ts` 的 noise。
   - Anchor 設定介面。
   - `messages/en.json`、`messages/zh.json`。
5. **P5 文件**
   - README、README_CN 的 Anchor 說明與 credit（seitanism、NikoDemon80、ethanfel、drozbay，改用 geiru_context_suite 名稱）。
   - skill 的 `SKILL.md`、`references/plan-file.md`、`references/continuity-values.md`，並同步 Codex 的 skill 副本。
6. **P6 建置**：WSL 跑 `bun run build:release`，`dist/` 另一個 commit。

Commit 依責任分組：計畫、後端接續、開頭片段與合成、前端播放、設定與計畫檔、文件、`dist/`。

## 測試

- **Python**：
  - 鎖住範圍與遮罩值、聲音 tick、keystone 索引、多個 latent 放回、restart 路徑。
  - 開頭片段只在 Anchor 接續段產生。
  - 歸屬條件的每一項（相鄰、啟用、版本、裁切、最後一段）。
  - 合成時畫面與聲音的範圍。
  - 簽章讓舊 Anchor 結果過期、其他模式不變。
  - SelfLift 仍報錯。
- **前端**（WSL vitest）：
  - 播放序列與後端規則逐項對照。
  - 預覽跳轉與預載跨過開頭片段。
  - 計畫檔 noise 匯入匯出與無效值。

## 實機驗證（需同意才排 GPU）

1. **舊做法對照組先跑**：ComfyUI 直接跑工作目錄，所以要在切換分支前用 `main` 跑同一場景、同一種子的 8–10 段 Anchor 鏈。
2. **新做法同條件重跑**，比較：
   - 量測方式照作者：只量主體不會經過的固定區塊，避開會被下一段取代的段尾。
   - 色偏、紋理、接縫跳動。
   - 接縫處的動作是否接得順，聲音有沒有爆音。
3. **Dual**：至少一條 4 段以上的 dual 鏈，確認二採接縫與開頭片段正常。
4. **段尾重影**：用 anchor-scene 重跑 s5、s6，確認成片看不到 s5 段尾的拉回。
5. **換場景**：轉場後接 6 段固定機位，比較第一個完整段有沒有標 restart。
6. **預覽**：Project Video Combine 預覽與合併結果在接縫處畫面、聲音一致；裁切或換版本後退回各播各的。

## 風險與待確認

- 作者的數據都是單次執行，NikoDemon80 測到種子差異可能大過設定效果。對照時看方向，不看小數。
- 作者的固定機位測試顯示 noise 會讓顏色變暗，預設開 0.8 是照他的最佳組合。若我們的固定機位結果明顯更暗，再討論預設值。
- 在 Project Video Combine 裡裁切前一段結尾或下一段開頭時，該接縫退回各播各的，capstone 的段尾拉回會重新出現。要在介面說明裡註明。
- 鎖 22 加 noise 會讓 capstone 的修正不再傳到下一段。這是作者做法的一部分，他在固定機位的筆記中列為未解決。
