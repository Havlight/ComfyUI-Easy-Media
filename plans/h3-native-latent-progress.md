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

## 待完成

- 原生時間計畫、AV 時鐘與跨語言 fixtures。
- 完整 native artifacts、來源和階段 lineage、原子版本保存。
- 全部時間軸編輯入口與遷移。
- 原生 Context／Drift、Dual／SelfLift、嚴格政策與明確 fallback。
- UI、音訊組裝、Lock、last frame 和回歸驗證。
- 基礎 GPU 驗收通過後的 Masked 方法與預設方法比較。
- 最終 release、文件與適用 review。
