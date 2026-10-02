# H3 原生接續操作指南

新版時間軸讓生成任務保持原生幾何，接續保存並使用完整 sampler AV latent。Editor 外觀、軌道、片段卡、Project 連線和版本選單保持原有結構。

## 開始使用

1. 更新後重啟 ComfyUI，再重新整理瀏覽器。使用 MiniMax、24 fps。
2. 新空時間軸第一次新增任務即採原生規則，預設不勾「允許 VAE 回退」。已有內容的 workflow 顯示「舊版時間規則」；按「升級原生時間」檢視變更，套用可一次 undo。
3. 第一段用 Shot。相鄰新增的接續段預設 Context，也可選 Drift Control Context 或 Context Masked。
4. 在原本時間欄輸入 `10s`、拖片段邊缘或按上下鍵，會換成合法時長。選取片段後，下方顯示 39 幀／1.625 秒 context 說明；重疊不增加成片長度。
5. 照原本方式 Queue。每段完成後保存影片、完整 raw AV、階段資訊及來源版本，再載入下一段所需 slice。Combine 繼續用原介面挑版本、剪輸出和組成影片。

## 開關的意思

**「允許 VAE 回退」關閉：** 在 Easy Media 管理的生成路徑中，已生成 video／audio 不得重編碼回生成鏈。找不到相容原生來源就明確失敗。外部素材首次 encode、採樣預覽及成片 decode 仍允許。

**開啟：** 優先使用原生來源，只有需要時才從已交付素材重建。時間軸仍保持原生規則，接續方法不被替換。版本選單會標示用過回退的結果，這個紀錄沿後代保留。它不等於回到舊版時間軸，也不能掩蓋 sidecar 損毀或破壞 Lock。

外部匯入的身份是工作流程邊界；無法判定使用者在本庫之外改名、轉檔的素材是不是另一個工具的生成結果。已知本專案生成的 MP4／WAV／末幀參考會做 provenance 檢查。

## 時間與編輯

| 操作／例子 | 原生行為 |
| --- | --- |
| Shot 輸入 `10s` | 243 幀，10.125 秒 |
| 接續輸入 `10s` | 新增 238 幀，約 9.917 秒；內部 raw 為 277 幀 |
| 上下鍵 | 下一個／上一個合法時長，相差 17 幀 |
| Split／Smart Split | 同一切點規則；Shot 分成 Shot + Context，兩側維持可生成 |
| 新增、刪除、移動、複製、marker、prompt override | 同一 planner 處理角色、長度和前後相依；影響多段時先顯示調整預覽 |
| 多段變更、升級 | 套用是一筆 history，可以整筆 undo／redo |
| 磁鐵關閉 | 不影響原生幾何限制 |
| 播放頭、素材軌、字幕、輸出剪輯 | 維持原有逐幀操作，不把來源影片伸縮成格點 |

39 幀是目前統一接續 context，對應 video 12 tokens、audio 65 ticks。Shot 最短 39 幀，接續最短新增 17 幀。接續前段必須相鄰；新鏈先用 Shot。Native passthrough 任務至少需要 39 個可交付來源幀，內部 marker 請改用明確 Split。API 的非法範圍會拒絕；prompt override 是顯式編輯 adapter，會重排生成任務。

音訊以有理數時鐘和累積 sample 邊界處理。24 fps 與 40 Hz latent 不總是完全同相位，來源 origin 的誤差界限是 ±1/120 秒，且不隨段數累積；這不等於每個 frame 邊界都剛好是一個音訊 token。

## 方法與模式

| 方法 | 用途與行為 |
| --- | --- |
| Shot | 獨立生成，沒有接續來源 |
| Context（預設） | 原生 AV guide + 短 video anchor；一般動作與場景延續 |
| Drift Control Context | 隨步數改變 video prefix mask，允許人物／外觀逐步變化；SelfLift 的兩個 MODEL 各有獨立 wrapper |
| Context Masked | 複製並固定完整原生 video prefix，音訊 prefix 尾端以 8 ticks 的半餘弦 mask 釋放；Dual 高解析階段固定第一階段音訊 |

Masked 僅在原生時間軸提供，也能使用經明確授權 fallback 建立的來源。沒有新增更多方法或 context 長度選單。實測不足以證明 Masked 普遍優於 Context，所以未更換預設。方法和 sigma／模型／upscaler 都會影響品質；原生路徑不保證任意 prompt 都無縫。

| 模式／設定 | 規則 |
| --- | --- |
| Single | 相容 final native source 可直接接續 |
| Dual、第二 MODEL + LoRA | 保留第二 MODEL patch；自訂 sampler／sigmas 成對連接，第二 schedule 不被 context preset 覆蓋 |
| Dual 放大 | 選 H3 latent upscaler 可保持原生；`None` 的像素放大需允許回退；`upscale_by=1` 仍執行第二採樣 |
| SelfLift | 一份完整 schedule 分低／高解析，`rho=0`；第二 MODEL 用於高解析，支援 learned 或 direct latent lift |
| SelfLift 的 `sigmas_2nd`、`upscale_by` | 沿用原模式語義，不假裝是另一個完整採樣 schedule；最終尺寸由 Editor 決定 |
| 更換模式／尺寸 | 相同尺寸 final stages 可互通；需要的 low stage 缺失或不同時，重算前段或允許重建 conditioning。不可把 high latent 縮小冒充 low 採樣歷史 |
| 僅第一採預覽 | 舊時間軸保留既有續跑；原生時間軸關閉預覽後重新生成兩階段，避免把 clean prediction 誤當 noisy resume |

## Lock、末幀和版本

- **Lock Video：** 使用 Reference／Edit 任務模式，外部影片須覆蓋完整 raw window，包括接續前方 39 幀。原生模式不藉 temporal stretch 或尾端 freeze 湊長度；覆蓋不足會指出原因。
- **Lock Audio：** 按原來源位置與 native audio clock 取樣；context 之後再套鎖定，Dual 第二階段也套用。成片使用保存的 float locked WAV（保存既有匯入／混音處理後的樣本），Lock 優先於生成 overlap。
- **一般軌道鎖：** 仍阻止內容修改；planner 不會藉整批變更移動被鎖素材。
- **Previous tail frame：** 保留原卡片與位置功能。它把已生成影像當 reference，必須允許回退；strict 會就地提示並在執行前阻擋。沒有偷偷改成另一種 latent 接續。
- **版本：** 原生專案的 `new` 和 `override` 都保存可回復的新 generation；不沿用舊版 `-1 + override` 清除後段的行為。切換父版本會讓不匹配的下游標成過期。被任何保留版本引用的來源不可刪除，需先處理依賴版本。
- **磁碟：** 每版保存完整 high AV，Dual／SelfLift 另存 low stage，因此比舊 context-only 檔案大。不自動逐出歷史；容量不足或寫入失敗保留舊版。

其他格式維持原規則；暫時切換其他格式時，H3 設定保留但不套用幾何限制，切回 H3 若有時間變更會先顯示調整預覽。Sampling preview 仍在 Project 節點；tiny VAE 不影響純 latent 政策。32×32 audio-only 的 Project Video Combine 仍維持原本限制。

## 遇到阻擋時

| 原因 | 處理 |
| --- | --- |
| 前段沒有原生來源、缺少 low stage、解析度不同 | 從必要的前段重算，或開啟回退 |
| 父版本過期／範圍變了 | 選回匹配版本，或按新時間軸重算前段及下游 |
| Previous-frame 參考／Dual 像素放大 | 移除影像回流、改用 latent upscaler，或允許回退 |
| checksum 不符／sidecar 消失 | 還原原檔或重算；回退不跳過損毀檢查 |
| 未驗證的模型 adapter | 使用實際 ComfyUI MiniMaxH3 / MiniMaxH3AV adapter；不以型別近似聲稱相容 |
| Lock Video 範圍不足 | 延長來源、縮短生成任務或從 Shot 重新起始；開啟回退不能解除 Lock |

Editor 提供時間合法性與已知末幀提示；模型、磁碟和實際來源相容性由後端在執行時核對。版本選單顯示過期及 fallback，避免在還沒拿到模型與 sidecar 前顯示「已驗證」。

詳細實作與驗證見[進度紀錄](h3-native-latent-progress.md)；原始設計與調整見[計畫](h3-native-latent-continuation.md)。
