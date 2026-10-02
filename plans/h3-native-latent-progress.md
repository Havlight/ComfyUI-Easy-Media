# H3 原生接續實作紀錄

功能分支：`feat/h3-native-latent`，基於含完整計畫的 `main`（`3ebfb77`）。使用者已授權實作整份計畫並分段 commit。本文件記錄實際結果，不替代[驗收計畫](h3-native-latent-continuation.md)。

## 測試基線

- 修正兩個 MultiImagesLoader 測試漏傳 `max_limit` 的呼叫，未改產品 API。
- 修正 release build 失敗卻回傳成功狀態的問題；watch 模式仍可繼續監看。
- 8 個 Python 測試檔通過 **514 項**；2 項因 Linux 測試環境無 FFmpeg 略過。
- release build 在不含 `#` 的相同 source 暫存副本成功。原始路徑會觸發 Tailwind／Vite 的路徑解析問題，因此前端驗證使用暫存副本，產物另行提交。
- Windows 既有 Python 的 PyTorch `2.9.1+cu130` 可使用 RTX 4090；H3 模型、文字模型、video／audio VAE 和 latent upscaler 已存在。尚未執行本功能 GPU 推論。

Python 基線指令，於倉庫根目錄執行：

```bash
PYTHONPATH=tests:../.. python -m pytest -q --tb=short -c /dev/null \
  --confcutdir=tests --rootdir=tests --import-mode=importlib \
  tests/test_h3_motion_context.py tests/test_h3_audio_lock.py \
  tests/test_h3_latent_upscale.py tests/test_selflift_sampling.py \
  tests/test_sampling_preview.py tests/test_multitrack_info_output.py \
  tests/test_minimax_utils.py tests/test_minimax_node.py
```

`--confcutdir` 隔離 ComfyUI 插件啟動與上層 pytest 設定，不修改或停用倉庫的 `__init__.py`。

## 原生時間契約

- 新增不依賴 ComfyUI 的 Python planner 與 TypeScript 編輯交易 planner；legacy schema 維持原路徑。
- 共同 fixtures 覆蓋 Shot／continuation 長度、同距離向下取整與 split。
- 1,000 段有理數時鐘模擬確認 audio origin 誤差不累積、來源 slice 覆蓋接縫，輸出 sample 數由累積 frame 邊界換算。
- 新增 Python **16 項**、前端 **16 項**測試；連同前端既有測試共 **179 項通過**。release build 成功。
- planner 尚未接入 UI 或 sampler；後續提交會沿用此契約，避免只在 UI 表面吸附。

## Raw latent 與版本交易

- 新增完整 AV safetensors 保存，內嵌時間／階段／provenance；載入核對整檔 SHA-256 與 manifest，一律保留原始 dtype。
- 接續工作 slice 複製至 CPU，使用 delivered seam 的 video phase 與獨立 audio clock，不修改父 tensor。
- 版本寫入先完成 sidecars／media，再原子切換 manifest；失敗回復、跨 writer 鎖、磁碟容量檢查、stable ID 重排與父版本失效傳遞均有測試。
- 新增 **13 項**實際 torch／safetensors 測試；連同時間測試共 **29 項通過**。
- 本提交是共用底層，後續才接入 Project 圖與既有版本 UI。

## Editor 原生時間整合

- 新 H3 空時間軸的首次編輯採 strict；有內容的舊 workflow 需先檢視升級預覽，可整筆 undo。
- 原生交易在進入 history 之前套用；resize preview、duration、split／cut／smart split 與 undo／redo 均接入，素材本身維持逐幀時間。
- 加入共用回退 checkbox、39 幀 context 說明、last frame 相容性提示，保留原版面與接續方法。
- 時間輸入支援 `10s`、上下鍵合法間距、Esc 取消；API 送入不合法 native 範圍會在媒體讀取前拒絕。
- 前端 **237 項通過**，TypeScript 檢查與 release build 通過；Editor 序列化後端 **178 項通過、2 項略過**。
- sampler 圖仍待下一階段串接，本階段未宣稱可執行完整 native pipeline。

## Sampler 與原生來源串接

- 新版 Single／Dual／SelfLift 圖改為儲存完整 sampler AV，再 decode delivered view；不再建立 legacy context rebuild／anchor re-encode 節點。
- 每段完成保存後，下一段由版本 sidecar 載入所需 stage 並複製合法 context；沿用既有 segment cache 回收邊界，避免整條鏈保留完整 GPU tensors。
- Native SelfLift 明示 `rho=0`；Dual 像素 upscale 與 previous-frame 影像回流在 strict preflight 阻擋。
- 新增 **8 項**圖／runtime 測試，含實際 safetensors 保存後重新載入、audio canvas 多一 tick 的覆蓋與 39 幀 slice。
- 11 個後端測試檔合計 **552 項通過、2 項因 FFmpeg 略過**；release build 成功。修正測試間 Comfy mock 污染造成的套件順序相依。
- 外部 passthrough／舊版本 fallback adapter、Lock 覆蓋優先序、音訊 overlap 組裝與 GPU 品質驗收仍待完成；目前 native passthrough 會明確拒絕。

## 實際 GPU 結構驗證

- 新增可手動執行的 `tests/manual/h3_native_gpu_smoke.py`，使用既有 ComfyUI Python 和真實 H3 diffusion checkpoint。
- RTX 4090／PyTorch 2.9.1+cu130 上完成兩段 raw 56 幀採樣：video 17 tokens、audio 94 ticks；第二段採 39 幀原生 context。
- 完整流程約 **26.2 秒**，峰值 CUDA allocated **21,337,064,448 bytes**，全部 tensor 有限，接續 encoder 呼叫 **0**。
- 使用零文字 embedding，屬結構驗證，不代表畫質 A/B 已完成。尚未因此新增 Masked 或切換預設。
- JSON 結果保存在 ComfyUI `output/easy_media/native-validation/structural.json`。

## 外部來源、回退与版本保護

- Native passthrough 以實際 delivered 尾端 39 幀首次編碼；缺少 low stage、解析度或階段不相容時，只有已授權 fallback 才重建生成素材。損毀／checksum 不符不會被回退掩蓋。
- 相同解析度的三種 final stage 可直接互通；低解析 clean prediction 仍獨立驗證，不當作 final 或 noisy resume。
- 版本刪除保護涵蓋歷史與 detached 任務；選擇版本會更新後代過期狀態，與生成共用交易鎖。已移除任務復原時取回 stable ID 的版本。
- Native Lock Audio 使用繼承的 audio clock，在 context 複製後套用來源區間；保存 lossless locked WAV 供成片使用。
- 真實 VAE 驗證通過：raw 56 幀解碼、17 幀 delivered view、外部 39 幀 seed 對應 video 12 tokens／audio 65 ticks；接續 encode 次數 0。
- 三個後端測試檔 **287 項通過**；release build 通過。擴充 GPU 測試的 allocator peak 包含 Windows shared-memory 行為，不能解讀為實體 VRAM 需求。

## 編輯入口、Lock 與成片

- marker 依同一 split 契約吸附，後端展開為 stable ID 的 virtual tasks；prompt override 先對齊生成任務，素材時間不跟著位移。
- 多段受連帶調整時使用既有升級預覽 dialog 檢視，再一次 undo；已鎖定軌道不能藉整筆交易繞過鎖定。
- Lock Video 取得 raw 視窗的外部影片，包括 context 前綴；要求來源完整覆蓋且實際幀數相符，不作 uniform stretch。
- 成片以 saved raw WAV 組裝，incoming 版本擁有正確接縫的 generated audio overlap；重排／trim／錯誤父版本不套用重疊。Lock 來源優先。
- 20 段 sample-valued ramp 驗證精確累積樣本數、無重複或跳過；版本選單顯示過期及曾使用 fallback。
- SelfLift low guide 改用保存的 low-stage tokens；第二 MODEL 的 Drift wrapper 獨立建立，保留 LoRA 及完整 sigma schedule。
- 後端主回歸 **559 通過、2 略過**；新增 Lock graph 測試另外通過。前端相關 **282 項**中未變更六檔先前通過，修正 locked-track normalization 後 native timing/editing **22 項通過**。TypeScript 與 release build 通過。


## Masked、UI 與品質決策

- 完成共同底層、Context／Drift 和 GPU 結構驗收後，新增唯一方法 Context Masked。固定 video prefix、音訊最後 8 ticks 半餘弦釋放；Dual 第二階段固定既有 audio，SelfLift 仍走原生 stage lineage。
- 新／已升級 timeline 選單共 Shot、Context、Drift、Masked 四項；legacy 不出現 Masked。保留 Context 預設。
- RTX 4090 真實 32B CLIP、H3 int8、Turbo LoRA、不同第二 MODEL LoRA 強度與自訂 sigma：Single／Dual／SelfLift 共 12 組採樣與解碼通過，生成內容 encoder 呼叫 0。另有 learned upscaler 的 Dual／SelfLift 8 組通過。
- 這些小尺寸比較不是品質認證：極短二採 sigma 的 Dual 圖有明顯紋理／重影，direct lift 也不等同 learned upscaler。沒有因此宣稱 Masked 普遍優於 Context。
- 實際 React Widget 在獨立 Chromium 完成四種選單、秒數輸入／方向鍵、fallback 不改幾何及 1200／480 px 版面操作，沒有瀏覽器例外。Chrome 連線工具受 Windows／WSL 路徑問題阻擋，因此使用獨立 headless 瀏覽器；沒有假稱使用者整個 ComfyUI 工作流已由 GUI 操作驗收。

## 實際 ComfyUI 排程與 review 修正

- 啟動只載入本插件的獨立 ComfyUI，九個 native runtime nodes 與 Project 均成功註冊。使用真正 Editor → Project → Combine → SaveVideo 的 API 工作流。
- 單次三段完整生成、sidecar 保存、下一段載入及成片成功。Single Context 快速動作 10 段與 SelfLift Drift 人物／場景轉換 10 段均完整保存並合成，後者包含第二 MODEL LoRA 及 learned upscaler。
- 實際 Windows 執行找出並修正只讀 handle 的 `fsync` 問題；改為可寫 handle，失敗版本未覆蓋已存版本。Windows torch／safetensors／版本與末幀測試 70 項通過，1 項因 Windows 未授權 symlink 略過（Linux 另有覆蓋）。
- 實際 Dual 自訂二採 schedule 找出既有 linked static prepare 對 Context 輸出 `None` 的錯誤，修為保留指定第二 schedule，並加入 runtime regression。不是偷偷套內建 sigma。
- 補上來源重建時 low／high audio clock 對齊；只需重建 video stage 時沿用 high 原生 audio。外部已驗證 seed 可重新做所需尺寸的 ingress，不標成 generated fallback。
- Lock Video 在不使用影片的 task mode 直接指出需 Reference／Edit；短於 39 幀的 native source 任務在 Editor 即拒絕，避免執行時才發現幾何不成立。
- 明示原生第一採預覽後會重算兩階段；不把 clean prediction 冒充 noisy resume。修正 Windows 輸出路徑比較和 legacy 版本操作不必要載入 native module。
- 最終 React／TypeScript review：互動控制沿用 shadcn，無新增 raw color、素材時間不被 planner 移動、錯誤可選取對應片段。中英文 key 一致，其他語系沿用既有 fallback；sampling preview DOM 與 uint8 防白屏測試保留。


## 最終驗收與交付

- 自訂第二 sigma 修正後，真實 Dual + Masked + 第二 MODEL LoRA + learned upscaler 三段完整生成／成片成功；重啟 ComfyUI 後單獨重算第三段也成功，既有前三段版本均保留。
- Lock Audio 與沒有音軌的 Lock Video 各三段真實 API 流程通過。無音軌影片不虛構音訊鎖定；完成後移除本次 staging MP4，失敗時保留排錯資料。
- Lock Audio 的逐樣本檢查發現原本每段獨立 round 在 44.1 kHz 下會少一個 sample，已改為累積 sample endpoints。重跑 Dual + Masked + Lock Audio 後，三段 8 秒共 **352,800 samples**，與既有匯入／混音階段的整段來源相比最大差異 **0.0**。Native raw／locked WAV 改存 FLOAT，legacy PCM24 保存不變。
- H3 政策在其他模型格式的 Editor 中暫停，Split／duration 也不會沿用 H3 格點；切回 H3 需要調整時先檢視預覽。新增回歸確認設定保留且不偷偷改其他格式幾何。
- 最終測試數量見本節下方；主回歸之外的 Windows 測試有重疊，不把兩者相加宣稱不同案例。實際 API 測試及圖片／WAV 留在本機 ComfyUI `output/easy_media/native-validation/`，無模型或測試影片加入 Git。

### 驗證範圍與限制

此次涵蓋多個 seed、同場景、快速動作與人物／場景變化，包含兩條 10 段鏈、三段 Dual、重啟接續與外部 Lock，並完成中小尺寸 GPU、實際保存／合成與 encoder spy 驗證。沒有完成高解析度／大量 prompt 的全面感知品質評測，也沒有以音訊數值一致推論對嘴或語音品質。因此保留 Context 預設；方法選擇仍需依使用者模型、prompt 和 sigma 試片。

CPU 音訊時鐘測試另涵蓋 1,000 段模擬與 20 段精確 waveform 組裝，這是時間正確性證據，不等同 1,000 段真實 GPU 渲染。Native 首採預覽續跑採重算兩階段；任意第三方模型 adapter、任意輸出切點轉回接續與 5／22 幀 context 選單沒有加入。


### 最終檢查結果

| 檢查 | 結果 |
| --- | --- |
| Python 主回歸（11 檔） | 571 通過，2 因 Linux 無 FFmpeg 略過 |
| Windows 版本／末幀／native artifacts | 73 通過，1 因帳戶未授權 symlink 略過 |
| Frontend Vitest | 52 檔、600 項通過 |
| TypeScript strict | 通過 |
| Release build | 通過；暫存副本產物逐檔核對後同步到 `dist/release`，獨立 commit |
| Browser | 實際 Widget 選單／秒數／方向鍵／fallback／寬窄版操作通過，無 pageerror |
| GPU／ComfyUI API | 三種 sampling mode、兩個 MODEL 的 LoRA、自訂 sigma、learned upscale、3／10 段、restart、Lock 均通過上述矩陣 |
| Review | Python／ComfyUI、React／TypeScript、i18n、完整 branch diff 與 `git diff --check` 完成 |

手動重現入口：`tests/manual/h3_native_gpu_smoke.py`（結構與 VAE 邊界）、`h3_native_gpu_matrix.py`（三模式比較）、`h3_native_api_smoke.py`（真實排程、跨執行與 Lock）。使用 ComfyUI 的 Python 執行 `--help` 查看參數；Windows 建議 `python -X utf8`，避免既有中文 log 在重導向輸出時遇到系統編碼錯誤。
