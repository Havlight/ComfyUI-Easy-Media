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

## 待完成

- 將 native artifacts、階段 lineage、原子保存接入執行圖與版本操作。
- marker／prompt override 的原生 adapter、Lock 與完整 UI 回歸。
- 原生 Context／Drift、Dual／SelfLift、嚴格政策與明確 fallback。
- UI、音訊組裝、Lock、last frame 和回歸驗證。
- 基礎 GPU 驗收通過後的 Masked 方法與預設方法比較。
- 最終 release、文件與適用 review。
