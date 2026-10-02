# H3 純 latent 接續與時間軸升級計畫

日期：2026 年 10 月 3 日。適用倉庫基線：`22c9161`，包含本 fork 的 previous frame 功能。狀態：**功能已實作，最終驗證記錄見下列連結**。程式碼證據、外部參考的固定版本與本輪測試結果見[可行性紀錄](h3-native-latent-feasibility.md)。

目前操作方式見[使用指南](h3-native-latent-guide.md)，測試與實作取捨見[進度紀錄](h3-native-latent-progress.md)。本文保留設計時的需求與候選方案；以下完成決策優先於後文的預定文字。

- 保留 Context 預設。Masked 已加入且通過原生結構測試，但樣本未證明普遍品質優勢，不執行原第 9 階段的預設切換。
- 設定定稿為 `h3_native: {version: 1, allow_vae_fallback: false}`；不新增更多開關或 context 長度選單。
- Editor 顯示時間已對齊及末幀相容性；模型、sidecar 與來源在執行時驗證。版本 UI 顯示過期與回退，未新增不可靠的提前「已驗證」標章。編輯錯誤可以選取對應片段；重算與回退沿用原操作。
- Native 第一採預覽後重新執行兩阶段；目前不從 clean prediction 推斷 noisy resume。舊版 resume 保留。
- API／GPU／瀏覽器及回歸證據、品質限制均記錄在進度文件，不以結構測試代替感知品質認證。

本次升級的目標是：使用者照原本的方式編輯 H3 任務時間軸，系統就產生合法的原生生成計畫；接續直接使用上一段的 sampler latent，避免生成內容經過 VAE decode→encode 回流。保留原有 Editor、軌道、片段卡、接續選單、Project 節點與版本操作，新增一個回退設定和少量就地說明。

## 核心決策

1. **原生時間規則永遠生效於新版 H3 生成任務。** 新增、修改、刪除、搬移、分割、貼上、匯入和 undo／redo 都走同一套規則。回退開關不切換這套規則。
2. **唯一開關是「允許 VAE 回退」。** 新 H3 專案預設關閉。開啟只授權有明確原因的重編碼路徑；仍優先使用 native source。
3. **完整 raw sampler latent 是接續來源。** 預覽影片和輸出 trim 只是一個 view，不能覆蓋原始 latent；接縫依實際可見終點映射到來源，不能盲取 raw 最後一格。
4. **接續方法先保留 Context、Context Drift，後增加一個 Context Masked。** 加上 Shot，選單共四項；不再拆 Hard AV／Soft AV，不加入 Zero-Cut。完成共同底層與驗收後才做新方法。
5. **最終新接續預設候選為 Context Masked。** 理由是保留原生 video prefix 並釋放音訊出口；必須先通過本 fork 的 A/B 驗證。現有專案不被自動換方法。
6. **純 latent 保證涵蓋已生成 video 和 audio 回流。** 第一次匯入外部素材的 encode、預覽 decode、最終輸出 decode 仍可使用；禁止的是生成結果經像素／waveform 重建後再次餵回生成鏈。

「合法 timeline」不表示任何外部模型、遺失檔案或來源配置都能執行。編輯器保證任務幾何與依賴規則；後端在實際模型與檔案可得時證明執行路徑。**strict 執行成功，才代表該次受管理的生成路徑沒有 VAE round-trip。** 未驗證的第三方節點不能被標成已通過。

## 完成後的介面

### 保留的版面與新增位置

Editor 頂端既有 H3 設定區增加一個小型 checkbox／switch；不在每段各放一個，也不在 Project 節點再放可互相衝突的同名設定。沿用現有 shadcn 元件、字級、間距、主題顏色與 Lucide 圖示。

選取片段時，在現有 duration／接續方法附近增加一行狀態文字。長說明放 tooltip；阻擋原因才顯示可展開的具體資訊。窄版允許換行，不壓縮預覽或時間輸入。示意如下，並非新增一個大型面板：

```text
Editor 原有設定 …                  [ ] 允許 VAE 回退

原有預覽、素材與 prompt 區

片段 2      時間 [原有時間輸入]      接續 [Context Masked ▾]
原生接續 · 使用前段 39 幀／1.625 秒 · 不增加成片長度

原有時間軸：  [ Shot 1 ][ Segment 2 ][ Segment 3 ]
```

設定 tooltip：

> 關閉時，生成內容不得經 VAE 重編碼後回流。外部素材首次編碼、預覽和輸出解碼仍允許。開啟只允許必要的回退，時間軸仍保持原生對齊。

狀態文字使用不同文案，避免把「尚未生成」說成「資料遺失」：

| 狀態 | 畫面文字示例 |
| --- | --- |
| 前段將在同次執行產生 | `待前段生成 · 已規劃原生接續` |
| 來源、模型與計畫已驗證 | `原生接續 · 重疊 39 幀` |
| 尚未拿到外部模型的能力資訊 | `時間已對齊 · 模型相容性待檢查` |
| 回退已允許且確實需要 | `需 VAE 回退 · 前段版本缺少相容 latent` |
| strict 遇到 previous frame 影像回流 | `無法原生執行 · 前段末幀參考需要 VAE 編碼` |
| 任務受修改而需要重算 | `需重新生成 · 前段版本已變更` |
| 首段／獨立 Shot | `原生生成 · 無接續重疊` |

點擊原因可定位到該片段或對應 Project 設定，提供「重算必要前段」「改用已驗證版本」「啟用回退」等與原因相符的動作。切換普通選項不彈確認視窗；會重新安排多段時間或升級舊專案時，才顯示一次影響預覽，套用後可整筆 undo。

### 時間輸入與拖曳

- 保留原有時間輸入方式，另接受明確秒數，例如 `10s`。Enter／失焦時換成最近合法值；tooltip 顯示實際秒數與整數幀數。相同距離選較短者；上下鍵選前／後一個合法值。Esc 取消尚未提交的輸入。
- 拖任務邊緣時，游標可連續移動，片段邊界預覽停在合法位置；放開後只新增一筆 history。到最短／最長界線不跳回或生成零長片段。
- 磁鐵仍控制對其他物件的吸附。關閉磁鐵不會關閉 H3 的基本合法格點。
- 播放頭、逐幀檢視、素材軌、字幕與純輸出剪輯保留逐幀操作。只有會改變 H3 生成視窗或原生接縫的操作需要套用 H3 規則。
- 任務顯示的長度是**新增成片長度**；重疊由系統管理。首版不加手拉 overlap handle、陰影重疊軌或新的接縫卡片。

例如在 24 fps、沒有 Lock 衝突且來源足夠時：

| 使用者輸入 | 最終片段 | 後端生成 |
| --- | --- | --- |
| Shot 輸入 `10s` | 243 幀，10.125 秒 | raw 243 幀 |
| Continuation 輸入 `10s` | 新增 238 幀，約 9.917 秒 | 使用 39 幀 context 時 raw 277 幀 |

這是預定 video 幾何行為；AV phase 驗證是發布前的必要條件。時間小數只用於顯示，儲存與運算以幀、有理數時鐘與 audio samples 為準。

### 日常操作完成後會怎樣

1. 新建 H3 專案：預設不允許回退；第一段是 Shot。拖曳長度或輸入秒數，直接看到合法的實際時間。
2. 在同一鏈後方新增一段：自動建立接續依賴。Masked 通過品質門檻後作為新段預設；來源尚未生成仍可以先編輯和排程。
3. 點選該段：沿用原選單改 Context／Drift／Masked，下面顯示實際使用的重疊。一般切換方法不改可見長度。
4. 右鍵 Split：分割點落在最近能使兩側都成立的位置；左側保留原先的首段／接續角色，右側接續左側。不能成立時保留原段，顯示原因。
5. 刪除或搬移片段：依新順序更新依賴、時間與過期標記；下游需要重算，但已存版本不被清掉。影響多段的角色／時間調整可先預覽，套用後一次 undo。
6. 切換 Single／Dual／SelfLift 或模型、upscale：沿用既有控制和連線；檢查新的階段與尺寸是否有可用來源，必要時標示重算範圍。
7. 執行：先檢查整條計畫。strict 無合法路徑就停止且指出原因；允許回退時只有指定的邊界／階段走回退，結果記錄使用原因。

## 時間模型與編輯不變條件

### 幀數的來源

完整 H3 window 為 `F = 17k + 5`，合法 context 為 `C = 17m + 5`。Shot 的可見長度等於 `F`；一般 continuation 的新增長度為 `D = F - C = 17(k-m)`。最小／最大值還受模型 raw 上限、方法需要的 context、source 可用範圍與 Lock 約束限制，不能把公式中的所有數值直接開放給 UI。

新版接續的標準 context 先設 **39 幀**，讓方法切換盡量共享相同幾何。這同時對應 12 個 video latent steps 和 65 個 audio ticks，但來源 raw 長度仍可能有 audio 殘差，必須另外處理。Context 保留 guide 加短 seam anchor 的方法差異，不因改成 39 幀就變成 Masked。

首版不新增 context 長度選單。契約可以保存合法 `context_frames`，為未來開放 5／22／39 等 video-only 或 guide 用途留空間；精確 AV copy 只接受已驗證的共同時鐘長度。舊 22 幀配置由版本化 adapter 處理，不能以補零、複製 token 或 round 偷渡成 exact AV。

所有區間採半開區間 `[start, end)`，禁止前端用 inclusive end、後端用 exclusive end。每段保存以下關係：

```text
來源 artifact 的 raw window
  └─ source slice 和 phase
      └─ 本段 context prefix C + 新生成 D = raw F
          ├─ 完整 raw AV latent，留作後續來源
          └─ delivered view，供預覽與成片組裝
```

接縫只讀取截止於已交付端點的來源；若輸出曾裁去尾部，不能接到未交付的 raw 尾端。對任意內部切割，要根據 raw origin、delivered offset 與 token phase 求合法切點。

### 每個編輯入口的規則

前端以單一 planner／reducer 處理編輯交易；後端以相同規格和共享 fixtures 重驗。保留現有外部 API 的相容 adapter，逐一清除繞過規則的寫入入口。

| 操作 | 新規則 |
| --- | --- |
| 新增、複製、貼上、批次插入 | 在目標位置重新求角色、長度、source ID；不帶入其他位置的舊 dependency 或假 artifact |
| duration、左右 resize、鍵盤 nudge | 先按方法、raw 上限、Lock 與可用來源求合法集合，再提交；相鄰任務的位移同屬一次交易 |
| move／reorder | 任務跟隨內容，重算接續關係；已完成影像可留作舊版本，但不作新依賴的有效 cache |
| Split、Smart Split、Cut／Trim | 共用 cut planner。split 兩側都要可生成且有足夠 context；Shot 分為 Shot 加 continuation，continuation 分為兩個 continuation |
| 兩個獨立 Shot 的分割 | 不能假設仍維持原總長；使用者明確改為 Shot 時另求長度，不把一般 Split 默默改成兩個 Shot |
| 刪除鏈首或中段 | 中段下游重新連接；鏈首的下一段需轉成新 Shot 或有明確外部起始來源。會改長度時顯示影響；Lock 衝突不得默默移動素材 |
| 多選、快捷鍵、marker／批次自動分段 | 走相同交易；marker 保留編輯座標，拿來切任務時才求合法點 |
| 清空、undo／redo | 空鏈合法；恢復整筆版本化計畫和 dependencies，不只恢復視覺矩形 |
| 存檔、載入、preset、API／prompt override | 序列化保留 schema 和 policy；新 schema 的不合法輸入回傳具體修正建議，API 不偷偷改時間 |

游離的 continuation 不能無前段執行。插入 gap／跨 Shot 邊界時先把鏈分清楚；既有 gap 行為若不支持純接續，顯示可用操作，不跨空白隨意取另一段尾部。任務重排後不以舊數字 index 當永久身份。

## 純 latent 路徑與回退契約

### 共用來源解析

建立一個 context source provider，輸出「來源種類、完整 artifact、合法 slice、各 stream 時鐘、相容階段」；Context、Drift、Masked 都透過它取得資料。

```text
編輯意圖 → 合法時間計畫 → 來源與階段相容性檢查
                              ↓
                   native source 可用：直接使用
                   缺少來源且可回退：重建 source
                   其他情況：具體錯誤
                              ↓
                   已選定的 Context／Drift／Masked
                              ↓
                   sampler → 儲存 raw → 輸出 decode
```

回退只改來源取得方式或明示的既有像素階段，不偷偷改接續方法。每個決策都有原因碼與人類可讀說明；不是用 `except Exception` 把任何錯誤都轉成 encode。

| 情境 | 不允許回退 | 允許回退 |
| --- | --- | --- |
| 相容的已生成 native artifact | 直接使用 | 一樣直接使用 |
| 同次執行的前段尚未生成 | 先排程產生，再驗證 handoff | 一樣排程 |
| 只有舊交付影片、無可證明的 native source | 指出需重算前段或開啟回退 | 由實際 delivered 範圍重建，保持所選接續方法 |
| 第一次匯入外部 video／audio／image | 允許外部來源 encode，標示 imported seed | 相同 |
| 已生成的 previous frame 影像參考 | 保留設定並指出需要回退；使用者可取消該參考或改接續方式 | 按原功能 encode，記錄為 generated-image 回流 |
| Dual 像素放大、已選 SelfLift pixel correction | 拒絕，提出相容 latent 路徑 | 允許明確選定的既有 round-trip 階段 |
| 檔案毀損、錯誤 checksum、格式／模型不相容 | 停止並指出問題 | 同樣停止；不能假裝只是缺少 native source |
| 缺少 SelfLift noisy resume state | 重算必要階段 | 一般 VAE encode 不能還原 resume state，仍須重算 |

strict 的檢查有兩層：執行圖建立前檢查已知 encode／decode 回路；runtime 在每個來源與 encoder 邊界驗證用途與 provenance。未知第三方輸出必須經已驗證 adapter 才能給純 latent 通過標記。輸出或預覽 decoder 不因 strict 被全部禁用。

回退後新產生的 sampler latent 仍可供後段原生接續，但必須保留「祖先曾經 round-trip」記錄。UI 可說當前邊界是原生，不能把整條歷史重新標成從未回退。外部首次匯入與已生成資料重建使用不同 origin 類型。

## 原生來源與持久化

擴充 `utils/h3_project.py` 的 safetensors／manifest 工具，不把大型 tensor 放入 workflow JSON，不每一段都常駐完整影片和 latent。

每個 artifact 必須能回答：

| 欄位群 | 必要內容 |
| --- | --- |
| 身份 | schema version、segment ID、generation ID、artifact ID、parent IDs、原始／重建／外部來源 |
| 時間 | raw frames、delivered range、context source range、video token offset／phase、audio tick offset／時鐘偏移／取整殘差、sample 範圍 |
| 張量 | stream shape、dtype、video／audio format、尺寸、保存精度、實際 tensor checksum |
| 階段 | single final、dual stage、SelfLift low clean prediction、noisy transition、high final；是否含殘留噪音及對應 sigma |
| 配置 | 模型與 latent format、LoRA patch、VAE／audio VAE、upscaler、sigma schedule、seed、方法版本及會影響生成的來源設定 fingerprint |
| 組裝 | 視訊與音訊的 overlap ownership、輸出 view、來源音軌 Lock、回退原因與祖先記錄 |

以穩定 ID 關聯版本；index 只作 UI 排序。Fingerprint 用來判斷需要重算，不能把「同模型檔名」當成相容證明，也不因相容模型的權重不同就一律禁止新生成。跨模型允許的 latent format 必須由 adapter 明確驗證。

完整 raw tensor 保存一次、CPU offload、不降精度；工作用 slice 複製避免誤改父來源。接續時按需要載入對應 stage，bounded cache；不得為每個 MODEL patch 複製一份模型權重。

preflight 依實際 shape、dtype、階段和待保留版本估算新增磁碟需求。記錄 sidecar 大小與載入時間，確認長鏈不隨片段數持續占用 GPU 記憶體；磁碟不足時保留舊版本並回報，不用降精度或清掉仍被引用的來源來掩蓋。

寫入採原子交易：先檢查計畫，再寫新 artifacts 和 manifest，成功後切 active generation。生成失敗保留舊可用版本。既有版本上限／刪除工具要先檢查引用；刪除被下游引用的版本須拒絕或明確使依賴失效，不能讓自動清理破壞可接續性。修改時間軸只標記 affected descendants 過期，不先刪掉整個專案的檔案。

resume 必須驗證 stage、shape、schedule、parent、recipe 與必要 noisy state。SelfLift 的低解析 clean `x0` 可是原生預測資料，但不等於終端低解析成品；是否可作跨段 low context 要按專屬 recipe 驗證，不能冒充任意低解析 cache。

## 接續方法

| 選單 | 保留的特性 | 原生實作 | 預設策略 |
| --- | --- | --- | --- |
| Shot | 新鏡頭，不繼承前段 | 無 handoff | 新鏈首段預設 |
| Context | 較長 guide 加短 seam anchor，保留既有引導方式 | guide 與 anchor 都從來源 native latent 取得 | 共同底層完成時的基線；保留舊選擇 |
| Context Drift | sigma 驅動的 prefix 釋放，讓模型有較多變化空間 | 工作副本上修改 mask，seam 端保留較強；不破壞父 latent | 可選；不把它描述成只限換人，也不宣稱一定更銳利 |
| Context Masked | 整段 video prefix 受保護，generated audio 在出口柔化 | hard video prefix，加末端 8 audio ticks 的 half-cosine release；鎖定音訊例外 | 通過 A/B 後作新接續預設 |

規劃中的三種接續方法都提供 native source 路徑和可用時的共用 fallback source，故不因回退開關而整項隱藏。只有當前配置已知不相容時才禁用選項並顯示理由；載入既存不相容選擇時保留它，讓使用者知道需修正哪裡。

首版不暴露 mask 曲線、hard／soft 比例、taper steps 或多種音訊模式。Context 與 Masked 的價值差異要用實測判斷；若 Context 無獨立用途，可在後續版本從「新建」選項隱藏，仍保留舊方法 ID／recipe，不能把舊 workflow 的同一 ID 改成另一算法。

新 Masked 的整合放在基礎路徑完成之後。默認方法的提升也是獨立 commit：測試未通過就保留 Context 預設，文件記錄原因，不為填滿四項而發布未驗證方法。

## 音訊與 overlap 組裝

視訊沿用前段可見畫面，移除下一段重複 prefix。這是內部 context 重疊，不會在 UI 增加 1.625 秒，也不要求使用者手動疊兩個片段。

generated audio 的 Soft AV 釋放發生在 incoming prefix 末端；如果直接把全部 prefix 丟掉，soft mask 的結果也會一起消失。因此保留完整解碼 overlap 作輸出用途，由下一段擁有對應的音訊重疊區，按經過驗證的 sample 映射替換前段尾音。相同 overlap 只能被組裝一次；單段預覽與合併成片的差異要清楚，成片預覽以 assembly 計畫為準。

原始音訊被 Lock 時，它在所鎖定區間擁有最高優先權。carry audio、Drift、soft release 與成片 crossfade 都不得覆寫它。鎖定來源需涵蓋實際 raw／prefix 時間；不能只把 delivered 音軌向右搬就當對齊。

成片 audio sample 數從**累積 video frame 邊界**換算，不能逐段各自 round 再相加。需處理 raw audio 的 overhang 與非零來源 offset，並以 waveform impulse 驗證長鏈無累積偏移。必要的輸出端 sample trim／對齊只作用於輸出，不把對齊後的 waveform 重新 encode 回 context。對無法證明的相位組合先拒絕／縮小支援集合。

## Single Dual SelfLift 與第二模型

沿用目前的 mode selector、`model_loader_2nd`、sampler／sigma 連線和 upscale 設定。切換模式保存暫時無效的欄位與連線，標示適用範圍，切回時可恢復；不增加另一套平行控制。

| 模式 | 採樣與尺寸 | native 接續所需資料 | 重要限制 |
| --- | --- | --- | --- |
| Single | 使用第一模型和 schedule，Editor 尺寸為輸出尺寸 | 相容 final native source | 模型切換要檢查格式與 mask 能力 |
| Dual | 原有兩 pass；第二模型可帶 LoRA；`upscale_by` 影響第二 pass 尺寸 | 各 pass 相容來源、階段狀態與尺寸映射 | custom sampler／sigmas 成對；顯式 sigma 不被 context preset 覆蓋 |
| SelfLift | 一份完整 schedule，以 transition 分低／高解析；目前 Euler；Editor 是最終尺寸，`lowres_scale` 決定低解析 | low clean prediction、必要 transition state 與 high final 的獨立 lineage | `sampler_2nd`／`sigmas_2nd`／`upscale_by` 不假裝生效；第二 MODEL 用於高解析階段 |
| Passthrough | 不進 sampler，保持既有素材輸出 | 下游若接續，需辨認是外部 seed、已生成素材或有相容 sidecar | 不能因沒有 sampler 就免除 generated media 回流檢查 |

Dual 的兩個獨立完整 schedule 和一份 schedule 的上下段是不同情境。前者分別按各 pass 的 schedule 計算 mask；後者需保存原始完整 schedule、split index、resume sigma，兩個 MODEL 各自套正確的 mask wrapper。不能把外接 `sigmas_2nd` 擅自串成不一致的 schedule。

第二 loader 的 MODEL LoRA 必須完整保留。既有設計仍使用第一 loader 的 CLIP／VAE／audio VAE，不會因第二 loader 附帶不同 CLIP 就換掉文字編碼。第二模型有動態 mask patch 衝突時，preflight 指出是哪個 patch，不重複疊加。

### SelfLift 的純 latent recipe

新版 SelfLift 預設使用明示的 latent-only recipe：`rho=0`、不執行 pixel correction，沿用可相容的 latent upscaler 或已實作並驗證的 direct latent lift。這是新 recipe 的設計，不是執行時偷偷覆蓋使用者指定的非零參數。舊 recipe 保留版本資訊；使用者明確選了 pixel correction 時，strict 拒絕，允許回退才可執行。

開啟「允許 VAE 回退」不會自動重新打開 SelfLift 的 pixel correction。learned upscaler 和 direct interpolation 也不是畫質等價物；缺少已選模型就明確指出問題，不能悄悄換成 nearest。

Dual 放大使用合適的 latent upscaler 可保持純 latent；選像素 resize＋encode 時需要回退。`upscale_by=1` 的第二次採樣仍有效，不應當作沒有 second pass。

### 切換與重算

模式、尺寸、LoRA、upscaler、sigma、seed 或來源版本改變，重新計算 affected artifacts。若缺少新的低解析 lineage，提出重算最早必要前段；禁止直接把高解析 latent 縮小後冒充原來的低解析採樣歷史。只有已登記並通過測試的 latent bridge 可以轉換資料，且必須標記 derived artifact。

允許回退能重建可作 conditioning 的 source，不能重建消失的 noisy sampler state，也不能修復不相容模型。不能保證「任何設定切換都免重算」，但必須保證不誤用舊 cache、不丟連線、不默默覆蓋使用者的採樣計畫。

## Lock last frame 與既有功能

### Lock 的優先順序

素材的 video／audio Lock 保持原有語義；一般軌道編輯 lock 也繼續阻擋使用者對該軌的編輯。來源素材在真實時間軸的位置不因 generation snapping 自動伸縮、改速或平移。

外部 locked media 第一次 encode 可以在 strict 中執行，但來源取樣、generation window 和 delivered view 必須各自記錄。不能為湊格點在來源尾端偷偷 freeze，也不能改變原始音訊速度。來源覆蓋不足時使用明確的現有補齊政策或要求調整生成範圍，不虛構資料。

若保留精確 source 終點會落在非原生 seam，這個終點可以是純輸出裁切；要從該點繼續生成，仍須找到合法來源 slice。沒有合法解時顯示最近可用接點，或讓使用者明確改為新 Shot／允許重建。**VAE 回退不等於允許破壞 Lock。**

### Last frame

保留本 fork 的 last frame 素材位置、版本選取與行為。由已生成片段取實際最後一張圖，再作 image reference，仍屬生成畫面重編碼；strict 下不能把它誤標為純 latent。

不把 last frame 悄悄替換成 5 幀 native tail，兩者條件語義不同。第一階段以就地相容性提示處理；使用者可取消 last frame、改用 native continuation，或開啟回退。若未來證明有相同語義的原生 reference adapter，再獨立加入，不作首版完成條件。

### 其餘功能與範圍

非 MiniMax 格式、素材管理、prompt A／B、參考圖、shared references、panorama、字幕／語音功能、音量、mute／solo、既有輸出合成和版本 UI 不做重設計；需以代表性回歸測試確認沒有被新 reducer 或序列化丟掉欄位。已生成 MP4 的任意剪輯不自動變成可再次原生接續的來源。

sampling preview 仍屬於 Project 節點，不搬進 TRACK_DATA／Editor。輸出 decode 與 tiny VAE preview 不被 strict 禁止，保留 `uint8` 插值尺度及白屏回歸測試。

32×32 audio-only 沿用明確的既有功能界線：不憑這次升級新增 Project Video Combine 支援，也不宣稱已解決 audio-only 組裝；需要單獨規格的能力保持禁用並說明原因。

## 舊專案與版本升級

沒有新 schema 的 workflow 按舊語義載入，不在開檔時改時間、覆蓋方法或偽造 native provenance。顯示一次「舊版時間規則」提示及升級入口；保留舊執行是版本相容性，不是把回退開關當 legacy 開關。

升級預覽列出會改變的幀數、Shot／continuation 角色、Lock 衝突與需重算區段。使用者套用後，所有編輯都使用新版原生規則，policy 序列化到 `TRACKS_INFO`。舊版本仍可還原；無法證明來源的舊 sidecar 標為未驗證或重建來源，不只看檔名就當 native。

新政策建議用 `timing_schema` 與 `continuation_policy` 表達，確切型別在實作第一階段固定。Editor 是唯一編輯來源，Project 是驗證與執行者；API 同樣帶版本化政策。沒有 Editor 的舊節點輸入走 legacy adapter，不暗自套新預設。

## 程式碼落點

| 區域 | 工作 |
| --- | --- |
| `utils/` 的新 H3 plan／timing 工具 | 純函式解析時間、切點、來源範圍、階段相容性與原因碼，先重用現有 minimax／h3_project 工具 |
| `frontend/src/lib/` 的 H3 planner | 編輯交易與 native duration／split／resize 計算；Python／TS 共用 JSON fixtures |
| `frontend/src/hooks/` | 共用校驗狀態、節流 preview 與跨元件編輯行為；元件不各自做另一套 snap |
| `types/multitrack.ts` 與序列化路徑 | schema、policy、方法 ID、stable dependencies；舊資料 adapter |
| `MultiTrackWidget`、`MultiTrackToolbar`、`TaskSegmentEditor` | 導入 planner；一個 setting、既有選單增項、狀態文字、輸入和 tooltip |
| `nodes/project.py` | 在副作用前編譯完整 execution plan；分開 raw／delivery／來源；mode 和 fallback 路由 |
| `modules/motion_context/` | 原生 guide／anchor、mask、Drift stage wrapper、後期的 Masked adapter |
| `modules/selflift/` | 明確 native recipe、stage artifact 與 resume；保留已選 MODEL patch |
| `utils/h3_project.py`、routes、輸出合成 | provenance manifest、原子版本切換、依賴失效、audio overlap ownership |
| `locales/`、README、測試 | 同步文案與 API 行為，解釋 strict、fallback、Lock 和 migration |

不修改外部 ComfyUI 核心、不把所有第三方 node 直接複製進本庫。需要相容處理時以局部 adapter 加能力測試實作，註明來源與授權。

## 分階段實作與 commit

本輪先提交可行性紀錄，再提交完整計畫。正式功能開始時從最新 `main` 建立或沿用相符 feature branch；有使用者工作時先保護，不直接 reset。每個可驗證的小階段完成就 commit，不等整個功能做完才保存。

| 階段 | 建議 commit 主題 | 完成門檻 |
| --- | --- | --- |
| 0 | `docs: record native latent feasibility findings`；`docs: plan native latent continuation upgrade` | 本輪證據、UI、決策、限制與驗收可審閱 |
| 1 | `test: establish h3 continuation validation baseline` | 正式 test harness 可用；釐清現有兩個失敗；固定 AV／phase／stage fixtures。必要基線修復另 commit |
| 2 | `feat: define h3 native timing and execution plans` | raw／delivered／context、原因碼、schema、fallback 契約與跨語言 fixtures 完成 |
| 3 | `feat: persist native h3 artifacts and stage provenance` | full raw 儲存、完整 lineage、checksum、原子寫入、resume 和版本刪除保護通過 |
| 4 | `feat: keep h3 timeline edits on native boundaries` | 所有任務編輯入口接 planner；undo、batch、Split、Lock、migration 不變條件通過 |
| 5 | `refactor: source context and drift from native latents` | 現有 Context／Drift 移除主 context 及短 anchor round-trip；AV slice／assembly 通過 |
| 6 | `feat: validate native dual and selflift execution` | single／dual／selflift、模型 LoRA、sigma、upscale、native recipe 和缺失 stage 決策通過 |
| 7 | `feat: enforce h3 continuation policy and report status` | strict runtime guard、明確 fallback、last frame／passthrough／Lock、UI 狀態與 migration 端到端一致 |
| 基礎驗收 | `test: verify native continuation integration` | **先完成 1–7 與 GPU 基礎驗收，才能進入新方法**；此時既有 Context／Drift 已可在支持配置下純 latent 執行 |
| 8 | `feat: add masked h3 continuation` | 只新增 Masked；hard video、Soft AV、source audio ownership、各 stage mask 通過 |
| 9 | `feat: default new continuations to validated masked context` | A/B 品質門檻通過才做；未通過就記錄結果並保留原預設 |
| 10 | `docs: document native continuation workflows` | 使用說明、遷移、例子、已知限制、最終驗證報告與翻譯一致 |

每個 frontend 階段在提交前執行 `bun run build:release`，generated `dist/` 另用 `chore: build release assets for ...` commit；source 不與產物混為一個 commit。提交前檢查 diff、測試及相對 main 的狀態；避免 pre-commit hook 自動把 dist 混入 source，stage 後再次檢查範圍。基線／文件階段也遵守倉庫的提交前 build 規則。

每階段必要的測試和修復與該責任一起提交，不把所有測試延到最後。前端、Python／ComfyUI、i18n 各按實際變更 review；重大修正後重跑受影響驗證。正式發布前確認 `dist/release` 對應 source。最初規劃輪未授權實作；使用者隨後已明確授權完整實作及分段 commit。實作於 feature branch，不直接推送 main。

階段 2–6 的新 schema 在功能分支內整合，尚未連通時不作正式新建專案預設；舊 schema 仍走相容路徑。階段 7 和基礎驗收通過後才開放完整原生工作流程，避免中間 commit 只開 UI 開關卻給出尚未成立的保證。

## 驗收矩陣與完成標準

### 自動化

| 驗收 | 必須證明 |
| --- | --- |
| 時間 property tests | 隨機連續新增／改長度／刪除／移動／split／貼上／undo 後，每個 raw、source slice、角色與 dependency 都合法；TS／Python 結果相同 |
| Video／AV phase | 非零 raw offset、來源內部切點、39 幀與不同 raw 長度；錯相位／資料短缺必須明確拒絕 |
| strict encoder spy | 先完成外部素材 ingress，再把「生成資料回流」的 encoder 設為拋錯；支持路徑仍可執行。preview／output decode 可用 |
| Graph inspection | single／dual／selflift 的實際展開圖不含未授權 re-encode 路徑；短 anchor、low context、pixel lift 都在範圍內 |
| Mask | 受保護 video 符合預期、audio release 正確；dynamic mask 的模型端與 sampler 端量化／velocity 一致；Lock 最優先 |
| Source integrity | 工作 slice 和 method patch 不修改父 tensor；原始 latent 儲存未因 trim、preview、audio mix 改變 |
| 階段／resume | low clean prediction 不被當 noisy state；分段 sigma、模型切換、跨執行重啟結果可追溯；錯 cache 被拒絕 |
| Fallback | 只有可回退原因才重建；方法不變；strict 拒絕、allow 明確記錄；祖先回退不被洗掉 |
| 音訊 assembly | waveform impulse 跨至少 20 段不累積位移；overlap 只保留一次；locked source PCM 在覆蓋範圍保持不變 |
| 編輯／持久化 | 舊 workflow 不被默改；升級可 undo；刪除／改 active version／失敗重跑不破壞其他已存版本 |
| 回歸 | last frame、Lock、reference、模式切換、非 MiniMax、subtitle、passthrough、preview 白屏與 hit-testing 等代表案例 |

### GPU 與 UI 操作驗收

GPU 比較至少涵蓋 Single、Dual（含第二模型＋LoRA＋自訂 sigma）與 native SelfLift；使用相同 seed／prompt／尺寸／模型配對，測同場景、快速動作、人物或場景轉換，以及 generated／locked audio。短 3 段與至少 10 段鏈各取多個 seed，記錄失敗，不只展示最佳片。

衡量接縫亮度／色彩突變、人物與動作延續、細節、末端 freeze、音訊 click／重複字／對嘴與累積時間偏差，並記錄時間、VRAM、磁碟需求。幾何與禁止 encoder 呼叫是硬門檻；Masked 作預設還需接縫和整段品質沒有一致性退步，不能只因耗時較短就取代 Context。

瀏覽器驗收使用現有版面：滑鼠拖曳、秒數輸入、鍵盤微調、右鍵 Split／Smart Split、刪鏈首、多選、undo、切換 mode／方法／版本、關閉磁鐵、Lock 衝突、存檔重開、舊專案升級、缺 sidecar，以及 strict 失敗後開啟回退。確認錯誤定位、鍵盤焦點、窄版佈局、翻譯與實際計畫一致。

完成的定義是：使用者在支援配置內編輯得到合法 timeline，strict 成功執行可由資料流與測試證明沒有生成內容 VAE round-trip，舊功能相容且失敗不丟紀錄。只完成一個 checkbox、只讓固定 22 幀案例能跑，或只有畫面看起來連續，都不算完成。

## 本版不加入的功能

不做 Zero-Cut、額外的 tone／color 修正方法、通用跨模型 latent 轉換、任意位置無條件原生接續、手拉 overlap 編輯器、全片 joint refinement，或新的多模型 schedule 語言。5／22／39 context 自選留給後續有測試支持的進階功能；首版集中在合法編輯、可靠來源、現有模式與三種接續方法。
