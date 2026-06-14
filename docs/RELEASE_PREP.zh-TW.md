# m_1 Notion 補課播放器發佈準備

## 發佈定位

目前先做一般 Windows 使用者版。使用者拿到 zip 後，在本機建立 Python venv，啟動 GUI，透過「API 設定精靈」填入自己的 Notion token 與課程安排 view。

這一版不是 SaaS，不讓別人走你的 API。token、播放進度、短效影片 URL cache、字幕 sidecar 都留在使用者本機。

## 發佈包形態

- `portable_source_windows`：目前的最小發佈形態。
- `bootstrap_windows.bat`：建立 `.venv` 並安裝一般依賴。
- `run_player.bat`：啟動 PySide6 GUI。
- `local_settings.example.json`：設定範本，不含真 token。
- `requirements-cuda.txt`：CUDA runtime 選配，不強迫一般使用者安裝。

不進發佈包的內容：

- `.venv/`
- `state/`
- `subtitles/`
- `tmp/`
- `dist/`
- 任何本機 token、Notion 短效 URL、播放 cache、字幕 cache

## 使用者啟動流程

1. 解壓縮發佈包。
2. 執行 `bootstrap_windows.bat`。
3. 執行 `run_player.bat`。
4. 在 GUI 左上按「API 設定精靈」。
5. 填入 Notion API token 與課程安排 view URL。
6. 點「同步課表」。

完成紀錄 data source 可以先不設定。未設定時，播放器仍可同步、播放、生成本地字幕與保存本地進度；只是不會把完成紀錄送回 Notion。

## CUDA 與 CPU

沒有 CUDA 也能跑播放器、Notion 同步、進度保存與 CPU 字幕生成。差別在於長課程字幕生成速度。

若使用者要啟用 CUDA，可以在 bootstrap 後執行：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-cuda.txt
```

程式會在目前 Python 行程內尋找 CUDA/cuBLAS/cuDNN runtime，不改全機 PATH。若 CUDA 不可用，`device=auto` 會降級到 CPU `int8`。

## Docker 位置

Docker 不適合作為目前 GUI 播放器的主要發佈方式，因為 PySide6 視窗、mpv 內嵌播放、Windows 音訊與使用者桌面互動都屬於本機 GUI 邊界。

未來適合 Docker 化的是背景 worker：

- Notion 同步 worker
- 字幕生成 worker
- 字幕 sidecar cache worker
- 完成紀錄回寫 worker

桌面版仍應以 Windows GUI 發佈為主。

## 發佈前檢查

```powershell
$env:PYTHONUTF8='1'
.venv\Scripts\python.exe scripts\check_release.py
.venv\Scripts\python.exe scripts\build_release_package.py
```

`check_release.py` 會執行編譯、smoke test、UI smoke test、`git diff --check`，並確認不該追蹤的本機資料夾沒有進 Git。

`build_release_package.py` 只打包 Git 追蹤檔，並額外寫入 `release_manifest.json`。這能避免把本機 secret 或字幕 cache 包進 zip。

## 後續發佈里程碑

1. `0.1.0-alpha.1`：portable source zip，可手動 bootstrap。
2. `0.1.0-alpha.2`：補 Windows 捷徑、mpv 檢查與使用者錯誤訊息整理。
3. `0.1.0-beta.1`：評估 PyInstaller 或 installer，但不承諾立刻做。
4. `0.2.x`：拆出可選 Docker worker，處理字幕與同步背景任務。
