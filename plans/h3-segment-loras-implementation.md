# H3 每片段 LoRA 實作計畫

日期：2026-10-06。實作基線：`57b7d44`，已合併的 native H3 workflow。狀態：P1–P4 已實作，進行 P5 整合與回歸驗收。

新增獨立的 H3 Segment LoRA 節點，以片段範圍及採樣階段描述額外的 MODEL LoRA，接入 Project 統一執行。Editor 保持原有時間軸操作。第一個里程碑先驗證真實模型的隔離、切換與回復，再接入完整生成流程及介面。

此功能延伸現有原生接續，不更改時間格點、39 幀 context 或交付範圍。LoRA 切換本身不要求 VAE 回退；既有 stage、尺寸、來源與版本契約仍需成立。

## 第一版範圍

- 以目前專案的任務編號指定 LoRA 起始片段與數量，可新增、停用及刪除多列規則。
- 支援全部、第一階段、第二階段，對應 Single、Dual、SelfLift 的實際模型路徑。
- 保留上游 Loader 已有設定；逐段 LoRA 是額外疊加，不移除或反推 Loader 的 patch。
- 包含實際套用摘要、整輪預檢、版本紀錄及需要重生成的依賴判定。
- 優先驗證現用 H3 模型及其相容 MODEL LoRA。不能以所有 safetensors 都能讀取，推論所有 LoRA 格式與量化後端都受到支援。

第一版不加入 CLIP LoRA、逐幀或逐採樣步強度曲線、自動 trigger words、固定片段 ID 綁定、逐段 checkpoint 切換或通用參數排程器。規則不暗中變更 sampler、sigma、Turbo 判定結果或 SelfLift transition ratio；加速與蒸餾設定繼續由基礎 Loader 和 Project 控制。日後若支援逐段加速方案，需把權重與採樣設定一同定義。

## 完成後的接線與操作

```text
Editor ── tracks_info ────────────────────┐
主要 Model Loader ── model_loader ───────┤
第二 Model Loader ── model_loader_2nd ───┤→ Project → 既有生成與輸出
H3 Segment LoRA ── segment_loras ────────┘
```

公開節點暫定 ID 為 `easy h3SegmentLoras`，輸出自訂型別 `H3_LORA_PLAN`。Project 增加可選輸入 `segment_loras`，加在既有 schema 輸入之後，保留舊 workflow 的 widget 序列位置。節點只輸出設定資料，不載入 MODEL，也不改寫 `TRACKS_INFO`。

### LoRA 設定節點

基本介面為四欄；作用階段放在每列可展開的進階設定，預設全部。

| 啟用 | LoRA | 起始片段 | 片段數 | 強度 | 進階作用階段 |
| --- | --- | --- | --- | --- | --- |
| 開 | character_A | 1 | 直到最後 | 0.8 | 全部 |
| 開 | motion_B | 3 | 2 | 0.5 | 第一階段 |
| 開 | detail_C | 3 | 2 | 0.3 | 第二階段 |

每列提供啟用與刪除，節點底部提供「新增 LoRA」。新節點起始為空規則；新增列預設起始 1、片段數 -1、強度 1.0、作用全部，尚未選檔時顯示待完成。使用 ComfyUI 已設定的 LoRA 模型目錄與相對檔名，支援搜尋及重新整理清單。

片段數 -1 顯示為「直到最後」，保留原始數值供 API 使用。列內即時顯示「第 3–4 段」等範圍文字。刪除、停用與修改參與節點所在 workflow 的 undo、redo 和儲存還原，不建立第二套持久化設定。

### 實際套用摘要

LoRA 節點底部設可展開的摘要，按所連接的 Project 分別顯示。多個 Project 共用一個設定節點時，可切換查看，不假設它們有相同的時間軸或採樣模式。

| 任務 | 第一階段 | 第二階段 | 本次執行 |
| --- | --- | --- | --- |
| 第 3 段 | A 0.8、B 0.5 | A 0.8、C 0.3 | 已選入 |
| 第 4 段 | A 0.8、B 0.5 | A 0.8、C 0.3 | 已選入 |
| 第 5 段 | A 0.8 | A 0.8 | 未選入 |

摘要列出額外 LoRA，並註明仍保留 Loader 原有設定；無法從任意第三方 MODEL 重建完整全域 LoRA 清單。停用、強度 0、無相符片段、素材直通或未執行階段，均顯示具體未使用原因。

平常編輯使用輕量的範圍預覽，不讀取權重、不進行 VAE 或影片解碼。執行前的完整預檢再確認實際檔案及模型相容性。動態上游、執行期 prompt override 等無法在編輯時解析的資料，顯示「執行時確認」，不可顯示已驗證。正式執行前發布當次確定的套用摘要。

Editor 不新增 LoRA 設定區。沿用既有需要重生成的提示，把 LoRA 造成的過期狀態納入；不增加編輯確認彈窗，也不占用或取代 Project 的 sampling preview。

## 規則契約

### 範圍與編號

1. 起始片段是從 1 開始的全專案任務編號，與 Project 的生成選段語義一致。
2. 起始 3、數量 2 代表第 3、4 段，包含起始段；-1 代表後面全部。0、其他負數及非整數不合法。
3. 先完成既有正規化、split、marker 與 prompt override 的任務展開，再解析 LoRA 規則。素材直通任務仍占用編號，但不套用 LoRA。
4. 從第 6 段開始重跑，不把第 6 段重新編為 1；先對完整計畫解析，再挑選本次執行及其必要依賴。
5. 插入、刪除、重排或 split 後，規則依新編號重新套用。UI 摘要清楚反映改變，不暗中轉成跟隨鏡頭 ID。
6. 起始超出目前任務數時，規則保留並標示無符合片段；正數範圍超出結尾時，只套到目前最後一段並顯示實際範圍。

編輯規則只保存位置範圍。執行時產生的結果則對應穩定 `segment_id`，供當次執行及版本依賴使用；兩者用途不同。

### 疊加與停用

- 不同 LoRA 可在同一任務與階段疊加，套用順序固定為列順序。
- 同一 LoRA 在同一任務與有效階段重複出現時，指出衝突列並拒絕該執行範圍。相同檔案內容以不同檔名重複指定也納入完整預檢。
- 全部階段規則須先展開成實際階段，再判斷重複；第一階段與第二階段各自一列使用同名 LoRA 是合法的。
- 停用或強度 0 的列不載入權重、不加入模型設定指紋，也不參與重複檢查。強度必須是有限數值；支援核心 loader 接受的正負強度，不自動裁成 0–1。
- 上游 Loader 已加入的 LoRA 不會被逐段強度 0 移除。可辨認到重複的上游 LoRA 時提供提示；不得宣稱能辨認所有第三方 MODEL 的來源。
- 完全不連接、空計畫、或當段無有效規則，均使用原來的基礎 MODEL，不建立多餘的 patch。

### 採樣階段

| 模式 | 第一階段 | 第二階段 | 未執行規則 |
| --- | --- | --- | --- |
| Single | 唯一採樣 | 不存在 | 第二階段列保留，標示本次未使用 |
| Dual | 第一採 | 第二採 | 第一採預覽時，第二階段列標示未使用 |
| SelfLift | 低解析採樣部分 | 高解析採樣部分 | 仍使用原本一份完整 sigma schedule |
| Passthrough | 無採樣 | 無採樣 | 列保留，標示素材直通未使用 |

規則中的 `all` 表示所有實際執行的採樣階段。未執行階段中的檔案不需要載入或做模型相容性檢查；真正參與採樣的錯誤則在第一個 sampler 前拒絕。已存在的 SelfLift、第一採預覽與模式切換語義繼續生效。

## 資料與後端邊界

輸出計畫採有版本的可序列化資料，不能包含 MODEL、tensor、絕對檔案路徑或前端圖形物件。例如：

```json
{
  "version": 1,
  "rules": [
    {
      "id": "rule-a",
      "enabled": true,
      "lora": "characters/character_A.safetensors",
      "start_segment": 1,
      "segment_count": -1,
      "strength": 0.8,
      "stage": "all"
    }
  ]
}
```

`stage` 只接受 `all`、`first`、`second`。規則 ID 用於 UI、錯誤定位及 undo，不構成模型效果指紋。未知版本與不合法 schema 明確拒絕。

規則解析集中在不依賴 GPU 的 Python utility，輸入為既有 compiler 產生的完整任務計畫、模式及 LoRA 規則。輸出包含每個 `segment_id` 的有序階段規則、本次啟用階段、未使用原因及規則來源。範圍摘要、預檢、生成與狀態檢查共用此解析器；前端只處理輸入及顯示，不另寫權威版本的範圍演算法。

在完整執行解析後，加入檔案 SHA-256、目標模型相容性結果及有效設定指紋。永久版本紀錄保存有序檔案內容身分、相對檔名、強度和階段；相對檔名供定位及顯示，效果指紋以內容身分為準，並排除 UI ID、摘要狀態及未使用規則。只移動列範圍但沒有改變某段實際設定時，不應讓該段因原始 JSON 不同而過期。

編輯時的摘要與狀態 API 接收目前設定快照。新增讀取 Project、連接的 LoRA 節點及可辨認 Editor 資料的共用 hook，支援 reroute、取消舊請求及多 Project。無法取得完整快照時回傳待確認，不回退成空 LoRA 計畫。正式執行仍使用實際節點輸出，不能以瀏覽器快照取代。

## 模型套用與快取

Project 為每段取得第一、第二階段的基礎 MODEL，套用該階段額外 LoRA，之後再安裝該段 Context、Drift 或 tiling 所需處理。兩個階段各自以 Loader 的基礎 MODEL 建立，不以第一階段已套 LoRA 的 MODEL 充當第二階段基底。

未接第二 Loader 時，兩階段共享基礎權重來源，但仍可有不同 LoRA 組合。接第二 Loader 時，只使用它提供的 MODEL；CLIP、video VAE、audio VAE 仍遵守現有第一 Loader 規則。SelfLift 高解析 Drift wrapper 的套用須依有效階段 MODEL 決定，不能再只依「是否接了第二 Loader」決定是否需要獨立模型路徑。

沿用 ComfyUI 的 key mapping、模型 clone 與 patch 機制，不自行原地合併權重或直接修改上游 `patches`。使用不相容的檔案時，必須回報目標及未匹配情況；完全沒有可用 MODEL patch 不能視為成功。第一版遇到必須修改 CLIP 的權重，需明確報告不支援，不能悄悄只套一部分後宣稱完整套用。

模型準備的內部節點依賴整輪 preflight 及前段保存邊界，避免圖形排程提前為所有片段準備權重。使用既有 segment 標記與回收邊界，不在 `run_state` 或 manifest 保留 MODEL、LoRA tensor 或所有片段的 clone。

快取區分三種資料：

| 資料 | 策略 |
| --- | --- |
| 檔案身分與相容性描述 | 每輪對必要的唯一檔案和模型組合重用；保存小型資料 |
| CPU LoRA 權重 | 有 byte 上限的快取；在用工作集與可逐出項目分開統計 |
| 已加 LoRA 的模型描述 | 僅重用相同基礎 MODEL、runtime patch revision、檔案指紋、強度、順序和階段組合；不快取含片段 context 的 Drift wrapper |

P1 根據量測訂定快取預設上限並補入報告，第一版不增加使用者調校旋鈕。反覆使用少數 LoRA 時，存活物件及 tensor 的持有量不得跟片段總數持續增加。MODEL clone 不代表每段都複製完整模型，也不代表沒有 CPU／GPU 切換成本。

## 預檢與原生接續

第一個 sampler 執行前，完成以下檢查：

1. 解析完整任務計畫與階段，保留目前所有原生時間、來源、sigma 及 Lock 檢查。
2. 對本次將使用的 LoRA 檢查檔案、非有限強度、重複規則、目標 key／shape 及可驗證的格式相容性。
3. 對不同目標基礎 MODEL 各自驗證；同名 H3 不構成兩個模型都相容的證明。
4. 驗證本次依賴的已保存父版本，其逐段 LoRA 設定是否仍符合目前計畫。
5. 固定本輪檔案身分與解析結果；實際載入時再檢查檔案變更，變更則停止，不在同一輪混用新舊權重。

權重檢查按唯一檔案與目標組合逐一進行，釋放暫存資料；不藉預檢把所有模型變體載入 GPU，也不增加完整影格解碼。編輯時的輕量預覽不做上述完整權重檢查。

MODEL 的一般結構檢查無法保證任意第三方 patch 的實際運算或畫質，因此 P1 的真實量化模型驗證是支援範圍的必要依據。Turbo／蒸餾 LoRA 不能只憑檔名可靠分類；支援清單及不適用條件依已驗證的模型和採樣組合記錄，不把「成功載入」當作採樣方案正確。

不同片段刻意採用不同 LoRA，是合法的 native continuation。不能比較父子兩段的 LoRA 是否相同，作為 latent 可否接續的條件。VAE 回退也不能用來掩蓋錯誤 LoRA、缺少權重或過期父版本。

## 版本與過期判定

新增可選的逐段 LoRA 紀錄，納入 saved recipe 與狀態判定，不破壞既有 native sidecar 的讀取。舊版沒有此欄位時，代表沒有這個節點管理的額外 LoRA；不代表 Loader 沒有全域 LoRA。

runtime `patches_uuid` 可供記憶體快取使用，但不作為跨程序重啟後的逐段 LoRA 設定身分。相同檔案內容、強度、順序及有效階段，重新啟動後應有相同的設定指紋。無法確定的外部 Loader 身分仍交給既有 runtime 檢查，不宣稱本功能能補齊所有第三方模型 provenance。

| 情況 | 必要行為 |
| --- | --- |
| 第 2 段 A，第 3 段刻意用 B | 原生來源契約成立即可接續 |
| 事後把第 2 段 A 改為 C | 第 2 段標示設定變更，依賴它的結果沿實際來源關係過期 |
| 第 3 段重跑，但第 2 段設定已變且未選入 | 在採樣前指出最早需要重算的依賴，不默用舊父版本 |
| 第 3 段後是無來源依賴的獨立 Shot | 不僅因排列在後面就一律過期 |
| Shot 使用前段末幀或其他實際父來源 | 依既有來源關係傳遞失效，不能只看 Context 名稱 |
| 只改停用列或當前不存在的階段 | 沒有改變有效設定時不使結果過期 |
| 同檔名的 LoRA 內容被替換 | 完整預檢辨認新身分，不沿用舊快取或已核驗狀態 |
| 拔除 LoRA 節點或清空規則 | 預期設定回到無額外 LoRA，先前使用它的相關結果需重算 |

狀態檢查是唯讀操作；不刪除影片、sidecar 或 generation，不自動改 active version，也不擅自擴大使用者選定的生成範圍。資料不足時顯示未核驗，待 preflight 核對。

## 實作位置

| 範圍 | 預計位置與責任 |
| --- | --- |
| 設定節點與內部模型準備 | `nodes/h3_segment_loras.py`，登錄於既有 node registry |
| 規則與 stage 解析 | `utils/h3_segment_loras.py`，純資料編譯及錯誤定位 |
| 模型 adapter 與有界快取 | 共用 utilities，先檢查 `utils/models.py` 等可重用功能，再依 P1 結果拆分 |
| 採樣圖接線 | `nodes/project.py`，新增可選輸入與每段模型準備呼叫，不另建一套 Project 執行引擎 |
| 來源與預檢 | `nodes/h3_native.py`、`utils/h3_native_preflight.py`，納入必要 LoRA 及父版本檢查 |
| 保存及狀態 | `utils/h3_native_status.py` 與現有 recipe／artifact 路徑，新增有效設定比較 |
| 摘要 API | `routes.py`，共用規則解析；不載入模型、素材或改寫 manifest |
| 設定 UI | `frontend/src/components/widgets/H3SegmentLorasWidget.tsx`，沿用 widget 註冊及大小管理 |
| 共用 UI 行為 | `frontend/src/hooks/` 的規則編輯及 Project 摘要 hook，沿用現有狀態提示 |
| 測試 | 後端 `tests/`、前端 `frontend/src/tests/`、真實模型腳本 `tests/manual/` |

新 UI 使用既有 shadcn/ui、主題色及 i18n。schema 與範圍編譯保持獨立，未來增加新的 UI 入口仍使用同一個設定來源，不在 Editor 另存一份 LoRA 規則。

## 分段提交與驗收關卡

開始程式實作時，從最新 `main` 建立或重用 `feat/h3-segment-loras`。本次文件整理只提交 P0，不代表開始後續功能實作。

| 階段 | 交付與建議 commit | 通過條件 |
| --- | --- | --- |
| P0 計畫 | `docs: plan per-segment H3 LoRA support` | UI、範圍、階段、過期與驗證契約完整 |
| P1 模型驗證 | `test: verify H3 segment LoRA isolation and switching` | 真實模型的 A／B／A／無額外 LoRA 切換、基礎 patch 保留、兩階段隔離與記憶體證據通過；記錄支援邊界與快取預算 |
| P2 規則與模型 adapter | `feat: compile segment LoRA plans and prepare stage models` | 確定的範圍解析、衝突處理、檔案身分與有界模型準備通過測試 |
| P3 Project 與版本 | `feat: apply segment LoRAs with whole-run validation` | Single／Dual／SelfLift 接線、前置拒絕、保存、重跑及依賴失效通過；無規則路徑保持相容 |
| P4 UI 與摘要 | `feat: add segment LoRA controls and effective-plan preview` | 新增列、進階階段、undo／redo、序列化、多 Project 摘要及狀態提示通過 |
| P5 整合與文件 | `test: validate segment LoRAs across native H3 workflows`，再以 `docs:` 更新指南 | 完成實際 API／GPU／UI 驗收，分開記錄結構、性能與畫質結果 |
| 產物 | `chore: build release assets for segment LoRAs` | release 產物與已驗收 source 一致 |

各次 commit 前依 AGENTS.md 執行 release build、檢查 diff；產物有變動時與 source 分開提交，沒有變動則不製造空產物提交。推送前完成適用的 Python／ComfyUI、React／TypeScript 及 i18n review。

P1 未通過的模型或階段，不以普通輸出成功代替驗證。先修正 adapter 或記錄需要收窄的支援範圍，再推進相依部分；不自行以 VAE 回退或關閉隔離檢查讓功能繼續。完整驗收前不將開發中的功能合併到供使用者測試的本地 main。發布範圍另依當時使用者指示處理。

## 驗證矩陣

### 模型與記憶體

- 在相同 seed、sigma、conditioning 及輸入 latent 下，以獨立對照執行驗證 A → B → A → 無額外 LoRA；確認 A 恢復以及原本全域 patch 保留。
- 完整接續鏈本來就有不同 context，不能要求兩個使用 A 的不同片段輸出相同。階段隔離以實際 patch、相同輸入的對照運算及呼叫路徑判定，也不能要求第一階段變動後的第二階段輸出保持不變。
- 使用現有 H3 量化模型、全域 Turbo 基底及相容的一般 MODEL LoRA。若只具備合成 patch 或單一加速 LoRA，相關結果只算機制驗證，不能宣稱一般角色／風格 LoRA 已完成實測。
- Dual 與 SelfLift 都驗證共享 Loader、不同第二 Loader，以及僅第一、僅第二、全部階段；Context／Drift 皆需覆蓋。
- 反覆循環少數 LoRA 組合，記錄 CPU 工作集、CUDA allocated／reserved 峰值、切換時間及存活快取持有量。allocator 保留值與實際存活物件分開解讀，不把 clone 數量當作完整模型份數。

### 規則與來源

- 全範圍、有限範圍、從中間重跑、超出結尾、marker 展開、插入、刪除、重排及 split。
- 多 LoRA 疊加、同檔案重複、跨階段同名、強度 0、停用、負強度、非法數值及未知 schema。
- 最後一段使用不存在或不相容的 LoRA，第一個 sampler 的執行次數仍為 0。
- 執行前或執行中換檔、內容指紋變更、重啟後相同規則、已過期但未選入的父版本。
- 刻意 A → B 接續不觸發 VAE 回退；父版本設定過期則明確拒絕，不被允許回退掩蓋。
- 停用規則、改不使用的階段、不相關的獨立 Shot 不被錯誤失效；有實際父來源的結果則正確傳遞。
- 不連新節點、全停用與空計畫的正常路徑，與基線使用相同 MODEL／採樣設定，沒有新增 patch、VAE 呼叫或 LoRA 權重載入。

### 整合與 UI

- 真實 Editor → Project → Combine → SaveVideo 路徑，確認 raw `17k+5`、delivered 長度、context trimming 與音訊樣本邊界保持原契約。
- 保留第二 MODEL 的上游 LoRA、自訂第二 sigma、latent upscale、SelfLift learned／direct lift、Lock Video／Audio、previous-frame 回退及第一採預覽的既有語義。
- 檢查第二階段規則在 Single 的未使用提示、Passthrough、其他 Editor 格式切換、動態輸入待確認及多 Project 摘要。
- 前端測試新增／刪除／停用、進階欄位、窄寬度、鍵盤操作、undo／redo、儲存重載及過期摘要請求取消。實際瀏覽器驗證 workflow 儲存與互動；無法完成時明確記錄，不以元件測試充當瀏覽器驗收。
- 執行受影響的後端與前端測試、TypeScript strict check 及 release build；最後做全套回歸。後續僅在新變更或失敗需要時重跑。

所有驗證報告記錄模型與 LoRA 指紋、採樣設定、模式、硬體及可重現入口。真實模型、影片與性能報告存於 ComfyUI 的 `output/easy_media/native-validation/segment-loras-*`，不提交模型權重或測試影片到 Git。

## 完成條件與既有品質限制

功能完成須同時具備：可用 UI、確定的規則解析、模型隔離證據、整輪預檢、版本失效與中段重跑、穩定的快取持有量、原生接續回歸，以及符合實測範圍的使用說明。記錄流程完成、記憶體成本與畫質觀察各自的結果；不把採樣完成視為畫質通過。

現有小尺寸 SelfLift 樣本已有明顯紋理／色彩問題，逐段 LoRA 功能不自動解決這些問題。驗收要與相同設定的現有基線比較，辨認新增的污染、接縫或音訊退化，並保留既有品質限制。相容的 native latent 也不保證大幅角色／風格切換一定自然。

目前基線的既有驗證紀錄為後端 1000 passed／1 skipped、前端 608 passed，詳見 [原生工作流收斂紀錄](h3-native-workflow-consolidation.md)。這些數字不是新功能的驗收結果。

實作參考：[現有 Project](../nodes/project.py)、[SelfLift sampling](../modules/selflift/sampling.py)、[原生狀態](../utils/h3_native_status.py)、[逐段記憶體回收](../utils/project_memory.py)。MODEL clone／patch 優先沿用已安裝版本的 ComfyUI；[官方 LoRA 實作](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/sd.py) 用於理解介面，最終相容性以使用者環境的 P1 實測為準。

## P1 實測紀錄（2026-10-06）

入口為 `tests/manual/h3_segment_loras_gpu.py`，報告位於 ComfyUI `output/easy_media/native-validation/segment-loras-isolation.json`。硬體 RTX 4090，PyTorch 2.9.1+cu130。

- H3 INT8 `10Eros_Max_h3_hybrid_beta5_int8`（SHA-256 `488e0d51…`），基底保留官方 8-step Turbo LoRA（`6a56f41a…`）。額外 A 為 `h3-realism-people-t2v-i2v-r2v`（`acc52960…`，125 MiB），B 為 `gemi_minimax_v1`（`de2e5f26…`，148 MiB）；完整指紋在報告。
- 兩輪 base → A → B → A → base，共 10 次固定 seed、零文字 conditioning、39 幀小 latent 的單步 Euler 運算。重複 A／B／base 的影音輸出最大差均為 0；A、B 相對基底均有非零效果。所有上游 patch 保持原樣。
- Dual 及 SelfLift 各測共享 Loader、具有獨立上游 patch 的第二 Loader 模型，共四條路徑皆有限且 patch 未受污染。第二 Loader 使用同 checkpoint 的另一 MODEL 設定，本輪不代表已測第二種 checkpoint。
- 第二輪 RSS 約 23.18–23.23 GiB，CUDA allocated 約 19.568 GiB；全測試 CUDA allocated 峰值約 19.825 GiB，reserved 約 20.924 GiB。重複少數組合沒有隨次數增加一份完整模型的現象。CPU RSS 包含完整模型及全域 Turbo，不能當作額外 LoRA 快取大小。
- CPU 權重快取初始上限定為 512 MiB，可容納上述 A+B 約 273 MiB。超出上限的大檔可作當段工作集，不留在可逐出快取；正式 adapter 的容量、釋放及重用另由 P2 測試驗證。

本輪未呼叫 VAE，僅驗證模型機制，不是畫質或完整 Editor 接續驗收。Context／Drift、真實節點接線、影片／音訊輸出、獨立第二 checkpoint、上採樣及長鏈記憶體仍由後續整合驗收覆蓋。

### P2 進度

純資料編譯、相對路徑與 schema 驗證、階段展開、內容別名重複檢查、穩定效果指紋、CPU byte LRU 與弱參照模型描述已完成；針對規則及 adapter 的 41 個測試通過。真實 GPU 腳本以 `--managed` 使用正式 adapter 重跑兩輪隔離及 Dual／SelfLift 四條路徑，結果通過，報告為 `segment-loras-adapter.json`。此時權重快取只保留 A、B 兩檔約 273 MiB，沒有為相同檔案的不同強度複製一份快取。

首版相容性明確限於 core key mapping 可完整對應的 H3 線性層標準 2D MODEL LoRA（含 alpha）。CLIP 權重、部分匹配、錯誤 shape、DoRA、mid／reshape 或其他未驗證 adapter 均在採樣前拒絕；不以成功讀取 safetensors 宣稱支援。

### P3／P4 進度

Project 已接入每段兩階段 MODEL、整輪 LoRA 預檢、持久化有效設定及 Context／previous-frame 依賴失效。受影響的後端測試 232 passed。LoRA UI 使用原有 graph history；相關前端測試 17 passed，TypeScript strict check 通過。

實際 API 已完成 Single 四段 A → B → A → 無額外 LoRA、Dual＋Drift＋learned latent upscale（共享 Loader），以及 SelfLift＋Drift（第二 checkpoint 為 `aiangelh3_v1Eros40Red60`）。缺檔、錯誤 shape、重複 LoRA、過期父段（包含允許 VAE 回退）皆在採樣前拒絕。中段重跑保留未選入的第 1 段版本。

隔離 Chromium 已驗證真實 ComfyUI 的搜尋選檔、範圍與階段編輯、多 Project、鍵盤 undo／redo、graph 儲存重載與窄介面；測試入口 `tests/manual/h3_segment_loras_browser.py`。不使用日常瀏覽器 profile。報告及截圖位於 `output/easy_media/native-validation/segment-loras-ui-*`。上述為階段驗證，最終回歸、其餘矩陣及影片觀察在 P5 彙整。
