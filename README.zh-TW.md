# m_1 Notion 補課播放器

私人補課平台的本地播放器原型。Notion 是課程清單來源，本地程式負責播放、字幕提示、進度暫存與回寫事件排隊。

## 原則

- Notion 是課程與影片清單的來源。
- 啟動時自動同步 Notion 課程安排，不把影片列表寫死在本地。
- 播放影片時優先使用 Notion 影片來源進行串流，不下載整支影片到本機。
- 本地只保存字幕、小型同步 cache、播放進度與回寫 outbox。
- 不使用 WebView 播放 Notion 頁面。
- UI 只是外殼，不直接理解 Notion MCP 細節。

## 模組邊界

```text
m1_player/config.py
  路徑、Notion view URL、cache 位置與完成門檻。

m1_player/notion_mcp.py
  Notion MCP 子程序與工具呼叫邊界。包含 request timeout 與 Windows 子程序樹清理，避免外部握手卡死後留下 node 殘骸。

m1_player/notion_api.py
  官方 Notion REST API 同步路徑。有 token 時用 data source query 與 block children 直接同步課程頁與影片 block。

m1_player/attachment_resolver.py
  可插拔的 Notion 附件 URL 解析器。若有 `M1_NOTION_TOKEN` 或 `NOTION_TOKEN`，會嘗試用官方 API 解析附件 URL。

m1_player/local_settings.py
  本地 secret 設定讀取。預設讀 `state/local_settings.json`，此目錄不應進 repo。

m1_player/runtime_config.py
  將預設值、環境變數與本地設定合成啟動用 AppConfig。UI 與 CLI 不直接判斷設定優先序。

m1_player/settings_status.py
  彙整 token、預計同步後端、上次實際同步 metadata、附件解析、回寫模式、cache 與下一步提示。CLI、UI 或守護進程應共用這個模型，不各自重寫設定判斷。

m1_player/settings_actions.py
  保存 Notion token、補課完成紀錄 data source 與課程安排 view URL。CLI 與 UI 共用這層，避免 secret 與設定保存規則散在不同入口。

m1_player/mvp_readiness.py
  將 mpv、課程 cache、來源形狀、字幕、Notion token、完成回寫 data source 與 outbox 收束成 MVP readiness gates。它是總儀表，不取代各子模組的細部檢查。

m1_player/readiness_summary.py
  將 readiness gates 轉成 UI 可顯示的摘要文字。UI 不直接拼接 gate 規則，避免設定狀態判斷散落在視窗程式裡。

m1_player/setup_guide.py
  將目前設定狀態轉成可複製的外部設定命令清單。它只產生命令與說明，不寫 token、不呼叫 Notion、不啟動播放。

m1_player/resolved_url_cache.py
  保存短效可播放 URL 與到期時間。只保存 metadata，不保存影片本體。

m1_player/notion_parser.py
  將 Notion fetch 文字轉成 CoursePageRef 與 VideoSegment。

m1_player/playback.py
  mpv JSON IPC 播放核心。若找不到 `mpv.exe`，會回報缺依賴而不是假裝播放。

m1_player/playability.py
  將影片來源、Notion 附件解析結果與播放核心狀態合成可播放性判斷。UI 只消費結果，不在選片流程內散落來源判斷。這裡也是 mpv 前最後一道串流 gate，非 http/https URL 不會送進播放核心。

m1_player/video_detail_summary.py
  將選中影片的來源解析、播放可行性、字幕、完成狀態與進度轉成 UI 可讀摘要。它只負責狀態呈現，不決定播放或同步行為。

m1_player/subtitle_readiness.py
  靜態檢查每段影片是否能找到本地字幕檔與 cues。字幕缺失會列為 readiness warning，不阻擋播放核心。

m1_player/subtitle_manifest.py
  依本地 progress cache 產生字幕 sidecar manifest，列出每段影片建議使用的 `.md`、`.srt`、`.vtt` 路徑；必要時可產生 timestamped Markdown 佔位檔。

m1_player/preflight.py
  啟動前本地診斷，檢查 mpv、Notion token、cache、影片來源、字幕與 outbox。

m1_player/sync_service.py
  啟動同步服務，查 Notion database view，更新本地 ProgressStore。

m1_player/progress.py
  本地播放狀態 cache。Notion 重新同步時只刷新影片 metadata，不覆蓋播放秒數、完成時間、完成狀態或字幕路徑；同步完成後會記錄 backend、時間、頁數與影片數，供啟動診斷使用。

m1_player/progress_overview.py
  將所有 PlaybackRecord 收束成補課總覽，包含總影片數、各狀態數量、平均進度、完成率與待回寫完成紀錄數。UI 與 CLI 共用這個模型，不各自計算。

m1_player/subtitle.py
  SRT、VTT、timestamped Markdown parser 與目前字幕 cue 判定。

m1_player/subtitle_lint.py
  檢查本地字幕 sidecar 的時間軸、空白內容、重疊 cue 與過長段落。它是字幕來源進入播放器前的格式守門，不負責語音轉文字。

m1_player/subtitle_resolver.py
  依影片 stable_key 或檔名尋找本地字幕。

m1_player/writeback.py
  將完成事件寫入 outbox。正式 Notion 補課紀錄資料庫尚未建立前，不直接寫 Notion。

m1_player/writeback_summary.py
  將 outbox 轉成 UI/CLI 可讀摘要，讓完成回寫狀態不只是一個數字。

m1_player/completion.py
  管理完成狀態與 outbox 排隊規則，避免已完成影片重複產生完成回寫事件。

m1_player/writeback_schema.py
  將 PlaybackRecord 轉成未來 Notion 補課紀錄資料庫的 property map。

m1_player/writeback_sink.py
  將完成 outbox 寫入 Notion 補課紀錄 data source。預設由 CLI dry-run，只有明確 `--apply` 才送出。

m1_player/writeback_schema_check.py
  檢查補課紀錄 data source 欄位是否能承接完成事件，並提供可輸出的 data source 欄位模板。它只檢查 schema，不建立頁面。

m1_player/video_source.py
  判斷影片來源是否為可播放 URL。Notion 內部附件標記會被明確標成需要 resolver。

m1_player/streaming_policy.py
  靜態檢查影片來源與短效 URL cache 是否仍符合串流優先邊界。允許 http/https 與 Notion attachment resolver，不允許本地影片路徑進入播放來源或短效 URL cache。

m1_player/source_readiness.py
  靜態檢查目前 cache 內的影片來源是否具備未來 token 解析所需形狀。它不呼叫 Notion、不下載、不播放，只確認來源是否可直接播放或具備 permission block id。

m1_player/app_qt.py
  PySide6 UI。它只控制同步、字幕、進度、回寫調度與 mpv 播放核心，不直接理解 Notion MCP 或 Notion API 細節。右側上方黑色區塊是嵌入式 mpv 播放器宿主；字幕區分為當前提詞大字區與可雙擊跳轉的字幕列表；選中影片會顯示來源解析、播放可行性、字幕與進度摘要。
```

這個拆法的目標是避免大泥球。新增功能時，先判斷它屬於同步、播放、字幕、進度、回寫，還是 UI 外殼，不要把跨層邏輯塞進 UI。

## 資料模型

Notion 課程安排 database：

```text
名稱 title
日期 date
標籤 multi_select
```

每個課程頁可能包含多個 video block。播放器將一個 video block 視為一段補課影片。

未來補課紀錄 database 建議欄位：

```text
影片名稱 title
課程頁 URL url
課程日期 date
段落序號 number
影片 block id rich_text
影片來源 rich_text
最後播放秒數 number
影片總長秒數 number
進度百分比 number
補課狀態 select
完整補課時間 date
最後更新時間 date
字幕路徑 rich_text
```

目前不直接建立這個 database。播放器先把完成事件排入 `state/notion_writeback_outbox.jsonl`；若設定 token 與補課紀錄 data source id，可用 UI 的「送出完成紀錄」或 `flush_writeback.py --apply` 透過官方 Notion API 建立完成紀錄。

## 啟動

```powershell
$env:PYTHONUTF8='1'
D:\RRKAL_tools\m1-makeup-player\.venv\Scripts\python.exe D:\RRKAL_tools\m1-makeup-player\scripts\settings_status.py
D:\RRKAL_tools\m1-makeup-player\.venv\Scripts\python.exe D:\RRKAL_tools\m1-makeup-player\scripts\setup_guide.py
D:\RRKAL_tools\m1-makeup-player\.venv\Scripts\python.exe D:\RRKAL_tools\m1-makeup-player\scripts\preflight.py
D:\RRKAL_tools\m1-makeup-player\.venv\Scripts\python.exe D:\RRKAL_tools\m1-makeup-player\scripts\run_ui.py
```

設定 Notion API token：

```powershell
$env:PYTHONUTF8='1'
D:\RRKAL_tools\m1-makeup-player\.venv\Scripts\python.exe D:\RRKAL_tools\m1-makeup-player\scripts\set_token.py
```

token 會寫入 `state/local_settings.json`。也可以改用環境變數 `M1_NOTION_TOKEN` 或 `NOTION_TOKEN`。

有 token 時，啟動同步會優先走官方 Notion API。沒有 token 時才使用 Notion MCP fallback；MCP 可能需要瀏覽器登入狀態，也可能因外部握手卡住而 timeout。

一般使用者版可直接在主畫面按「API 設定精靈」。精靈會顯示目前 token、課程安排 view、補課完成紀錄 data source、同步路徑與回寫模式，並提供三個欄位一次保存。最低可用設定是 Notion token 加課程安排 view；完成紀錄庫可以稍後再補。token 輸入框使用密碼模式，保存後只記錄設定檔位置，不會把 token 印到事件紀錄。

同步成功時，UI 事件欄與 `scan_schedule.py` 會標出 `sync_backend`，用來區分 `official_notion_api` 與 `notion_mcp_fallback`。本地 cache 也會保存上次同步的 backend、時間、課程頁數與影片段數；`settings_status.py` 與 readiness 會讀取這份 metadata，不再只憑目前 token 狀態推測。

設定課程安排 database view：

```powershell
D:\RRKAL_tools\m1-makeup-player\.venv\Scripts\python.exe D:\RRKAL_tools\m1-makeup-player\scripts\set_schedule_url.py "<notion_schedule_database_view_url>"
```

也可以用環境變數 `M1_SCHEDULE_VIEW_URL` 暫時覆蓋。優先序是：環境變數、本地 `state/local_settings.json`、程式預設 view URL。

設定補課紀錄 data source：

```powershell
D:\RRKAL_tools\m1-makeup-player\.venv\Scripts\python.exe D:\RRKAL_tools\m1-makeup-player\scripts\bootstrap_completion_database.py --parent-from-schedule --apply --save
D:\RRKAL_tools\m1-makeup-player\.venv\Scripts\python.exe D:\RRKAL_tools\m1-makeup-player\scripts\set_completion_database.py "<data_source_id_or_notion_url>"
```

第一條命令會在課程安排資料庫的同一個父頁下建立 `m_1 補課完成紀錄`，並把建立出的 data source id 寫入 `state/local_settings.json`。它需要 Notion token 具備 insert content 權限，且 integration 已被分享進該父頁。若只想檢查將送出的 payload，先拿掉 `--apply --save`。

這裡可填 Notion data source id、database id 或 Notion URL。工具會先抽取 32 字元 Notion id 後保存；若輸入的是 database id，送出完成紀錄時會嘗試讀取第一個 child data source，但多 data source 的資料庫仍建議明確指定。

送出完成紀錄前，先檢查補課紀錄 data source schema：

```powershell
D:\RRKAL_tools\m1-makeup-player\.venv\Scripts\python.exe D:\RRKAL_tools\m1-makeup-player\scripts\check_writeback_schema.py
D:\RRKAL_tools\m1-makeup-player\.venv\Scripts\python.exe D:\RRKAL_tools\m1-makeup-player\scripts\writeback_apply_smoke.py --json
D:\RRKAL_tools\m1-makeup-player\.venv\Scripts\python.exe D:\RRKAL_tools\m1-makeup-player\scripts\writeback_apply_smoke.py --apply --json
```

這個檢查會讀取補課紀錄 data source 的 properties，確認 `影片名稱`、`課程頁 URL`、`段落序號`、`最後播放秒數`、`進度百分比`、`補課狀態`、`最後更新時間` 等必要欄位型別正確。缺 token 或缺 data source 時只回報 `not_applicable`，不會寫入 Notion。

`writeback_apply_smoke.py --apply` 會建立一筆 synthetic 完成紀錄，成功後預設立刻用 Notion `in_trash` 收進垃圾桶，避免測試列留在補課完成紀錄庫。若要保留測試列，可加 `--keep`。

啟動後會自動同步 Notion 課程安排。左側是補課總覽、課程影片列表、重新同步、重新檢查與 API 設定入口，右側上方是嵌入式播放器、下方是控制列與字幕提詞。控制列包含「標記完成」與「送出完成紀錄」；前者只排入本地 outbox，後者才嘗試把 outbox 送到 Notion。「待送出完成紀錄」顯示的是本地 outbox 筆數，不代表已寫入 Notion。內部事件紀錄仍保留給診斷與測試，但一般版主畫面預設不顯示。

「重新檢查」會同時寫入 preflight 與 MVP readiness gates 到事件紀錄。若 Notion token 或完成紀錄 data source 尚未設定，UI 會顯示外部設定未完成，而不是把它當成本地播放核心錯誤。若本地字幕尚未準備，readiness 會列為字幕 warning，影片播放仍可繼續。

左側的「API 設定精靈」是一般版入口；「設定 token」、「設定完成庫」與「設定課表」保留為快速單項設定。這些設定都會寫入本地 `state/local_settings.json`。token 輸入框使用密碼模式，事件紀錄只顯示保存位置，不會列印 token 內容。設定保存後會立刻更新本次 UI session 的 resolver、readiness、writeback sink 與課表同步 URL，不需要重開播放器才生效。

`setup_guide.py` 與 UI readiness 區會列出可複製命令，用來完成 token、補課紀錄 data source、schema 檢查、同步試跑、影片來源解析與 UI 啟動。它只是一張操作清單，不會自動送出 secret。

「建立字幕佔位」是進階維護功能，主畫面預設隱藏。它會替目前選取的影片建立一個 timestamped Markdown sidecar，預設內容只有第一條 `待補字幕` cue。若同一段影片已經有 `.md`、`.srt` 或 `.vtt`，UI 不會覆寫既有字幕檔；日常流程應直接播放，讓播放時間軸自動觸發字幕生成。

## 播放進度

- 選取影片後，若本地 cache 有上次播放秒數，播放器會在 mpv 載入後自動跳回該位置。
- 播放中每秒更新本地進度 cache，字幕提詞會依目前秒數高亮。
- 左側補課總覽會顯示總影片數、完成數、補課中數、平均進度與待回寫完成紀錄數。
- 影片接近完成門檻時會自動排入完成 outbox。
- 也可以按「標記完成」手動排入完成 outbox，用於補登或播放器未能取得完整 duration 的情況。

倍速播放只應改變時間軸，不應改變人聲音高。播放器啟動 mpv 時會明確帶入 `--audio-pitch-correction=yes`，避免高倍速時出現娃娃音。

## 字幕

本地字幕放在 `D:\RRKAL_tools\m1-makeup-player\subtitles\`，目前支援 `.srt`、`.vtt` 與 `.md`。播放器會依序嘗試：

```text
<stable_key with colon replaced by underscore>.srt
<stable_key with colon replaced by underscore>.vtt
<stable_key with colon replaced by underscore>.md
<video filename stem>.srt
<video filename stem>.vtt
<video filename stem>.md
```

Markdown 逐字稿支援以下時間戳格式：

```markdown
[00:00:00] 開場說明
[00:00:03 --> 00:00:05] 第二段說明

00:00:05
第三段第一行
第三段第二行
```

若 Markdown 逐字稿沒有結束時間，播放器會用下一個時間戳作為結束；最後一條會使用短暫預設長度，讓字幕提詞 BOX 能跟著播放進度高亮。

可用檢查工具列出每段影片目前是否找到字幕，以及候選檔名：

```powershell
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\check_subtitles.py --show-candidates
```

可用 lint 工具檢查已存在的 sidecar 字幕是否有時間重疊、空白字幕、非法時間範圍，或過長段落。這個檢查只處理本地字幕檔，不會讀取或修改 Notion：

```powershell
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\lint_subtitles.py
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\lint_subtitles.py --json
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\lint_subtitles.py --strict
```

字幕仍維持本地 sidecar，不直接上傳 Notion；語音轉文字也先落成 `.md`、`.srt` 或 `.vtt` 後再讓 lint 檢查。

### 自動生成字幕

播放器支援對沒有字幕的 Notion 串流影片生成本地 sidecar 字幕。影片本體仍透過 Notion 短效 URL 串流，不把整支影片長期下載到本機。生成流程會把可播放 URL 交給 `faster-whisper`，輸出預設是 `.srt`，之後播放器會自動依既有 sidecar 規則載入。

預設語言是中文 `zh`，模型是 `medium`，並帶有計算機科學技術詞提示，避免 K8、資料庫、網路、設計模式等課程中的專有名詞被隨機音譯或誤翻。預設運行策略會先檢查 CUDA runtime；若找到 `cublas64_12.dll`，才使用 GPU `cuda/float16`，否則直接使用 CPU `int8`，避免每次都浪費時間嘗試不可用的 GPU 後端。批次大小預設為 8，用來避免一小時影片也要等一小時才得到字幕。

檢查字幕生成依賴：

```powershell
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\generate_subtitles.py --check-deps
```

替目前 cache 內某支影片生成字幕：

```powershell
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\generate_subtitles.py --key "<stable_key>"
```

只做短段煙霧測試時，可限制只解析前 30 秒：

```powershell
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\generate_subtitles.py --key "<stable_key>" --model tiny --max-seconds 30 --subtitle-dir D:\RRKAL_tools\m1-makeup-player\tmp\subtitle_smoke --overwrite
```

短段測試只能確認管線可用，不應拿來估算 rolling-ahead 的 worker 數。5 到 15 秒窗格會把連線握手、遠端首包延遲、demux 初始化放大。要找甜蜜點，使用 profiling sweep：

```powershell
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\profile_subtitle_windows.py --key "<stable_key>" --windows 15,30,60,120 --model tiny
```

這個工具不寫 sidecar、不更新播放進度，只量測不同音訊窗格長度的 decode/inference ratio，並輸出建議 decode worker 數。rolling-ahead 的性能判準應以 60 到 180 秒級別窗格為主，5 秒只保留為 runtime smoke。

替所有缺字幕影片批次生成字幕：

```powershell
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\generate_subtitles.py --all
```

可用環境變數調整轉錄設定：

```powershell
$env:M1_WHISPER_MODEL='medium'
$env:M1_WHISPER_LANGUAGE='zh'
$env:M1_WHISPER_DEVICE='cuda'
$env:M1_WHISPER_COMPUTE_TYPE='float16'
$env:M1_WHISPER_BATCH_SIZE='8'
$env:M1_WHISPER_HOTWORDS='Kubernetes, K8S, SQL, MySQL, TCP, HTTP, Docker, Linux, design pattern'
```

### CUDA 修復檢查點

這個播放器的字幕生成以 GPU 優先設計。若 `generate_subtitles.py --check-deps` 顯示 `cuda_runtime_available: false`，代表 NVIDIA 驅動能看到顯卡，但 Python 推理後端缺少 CUDA/cuBLAS/cuDNN runtime。此時 `auto` 會降級到 CPU，避免每次嘗試 CUDA 都浪費時間；但 CPU 只能作短片段煙測、補破洞或失敗 fallback，不能當作長課程 rolling subtitle 的主解析頭。

官方 `faster-whisper` 1.2.1 的 GPU 路線需要：

- CUDA 12 系列的 cuBLAS。
- CUDA 12 系列的 cuDNN 9。
- 包含 `cublas64_12.dll` 的資料夾必須在 PATH 中，或由啟動腳本注入 PATH。

`requirements.txt` 會安裝 `nvidia-cublas-cu12` 與 `nvidia-cudnn-cu12`。程式啟動時會自動尋找 `.venv\Lib\site-packages\nvidia\*\bin`，並只在目前 Python 行程內注入 DLL 搜尋路徑。這能避免修改全機 PATH，也能避免其他 AI/遊戲/開發工具被不同 CUDA/cuDNN 版本影響。

若另有獨立 CUDA runtime 位置，可用 `M1_CUDA_RUNTIME_DIRS` 指向含有 `cublas64_12.dll` 的資料夾；多個資料夾用 Windows 的分號分隔。程式啟動時會同樣把它注入目前 Python 行程。

```powershell
$env:M1_CUDA_RUNTIME_DIRS='D:\path\to\cuda-runtime-bin'
```

修復後用下列命令驗證：

```powershell
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\generate_subtitles.py --check-deps
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\generate_subtitles.py --key "<stable_key>" --model tiny --max-seconds 5 --device cuda --compute-type float16 --subtitle-dir D:\RRKAL_tools\m1-makeup-player\tmp\subtitle_cuda_probe --overwrite --json
```

第一條應顯示 `cuda_runtime_available: true`，第二條應回報 `device: cuda` 與 `compute_type: float16`。若仍出現 `cublas64_12.dll is not found or cannot be loaded`，代表 PATH 或 CUDA/cuDNN 版本仍未對齊。

GUI 主流程不顯示「生成字幕」按鈕；字幕生成由播放時間軸觸發。手動生成仍可透過 CLI 或隱藏的進階維護入口呼叫，完成後會自動重載本地 sidecar。後續可以把同一個 generator 升級為 rolling-ahead 模式：播放到時間點 T 時，GPU 先吃 T 後方數分鐘音訊窗，持續把 partial cues merge 回 sidecar；目前版本先採完整 sidecar 快取作為穩定基線。

播放與字幕生成共用同一條時間軸入口。按下播放時，若目前影片沒有可用字幕，或只有 `待補字幕` 佔位 cue，播放器會自動以 `playback_timeline` 觸發背景字幕生成；已有可用字幕時則只做播放切換。這讓字幕生成依附於使用者真正要看的影片，不會在瀏覽課表或選片時提前大量消耗 Notion 串流與 GPU 資源。

rolling-ahead 模式的分工應維持 GPU 主解析、CPU 降級與合併。CPU 適合做遠端音訊解碼、窗格排程、overlap 去重、sidecar 合併與失敗 fallback；GPU 才適合作為追播放時間線的主轉錄頭。若只用 CPU 追即時播放，長課程很容易被播放速度超車。NPU 暫不放進主線，因為目前這條 `faster-whisper` 管線直接支援的是 CPU/CUDA，去重這類結構化任務也不值得搬到 NPU。

rolling-ahead 的 decode worker 數應是動態值，不是固定值。每個音訊窗格完成後，程式會依下列量測估算 worker 數：

```text
decode_realtime_ratio = decode_elapsed_sec / audio_window_sec
inference_realtime_ratio = inference_elapsed_sec / audio_window_sec
recommended_decode_workers = ceil(decode_realtime_ratio * playback_rate * safety_factor)
```

worker 數需設上下限與冷卻時間。當遠端解碼低於播放速度時降線，當遠端解碼追不上播放頭時升線；但不能無限制增加，避免 Notion 短效 URL 或遠端儲存端被並發請求打爆。GPU worker 預設維持 1 條，避免多個 Whisper 實例重複佔用 VRAM；CPU merge worker 也維持 1 條，負責 overlap 去重、時間戳排序與 sidecar 寫入。

窗格任務數與 worker 並發數要分開理解。播放時間點 T 後方可以預排很多窗格任務，例如 10 到 15 個待解析窗格；但同時啟動的遠端 decode worker 應由 profiling ratio 動態控制，通常限制在 1 到 4 條。這樣可以保留足夠的預抓 horizon，又不會真的開 15 個無頭播放器、15 個遠端連線或 15 個 Whisper 模型實例。

播放倍速會直接提高容量門檻。若主播放視窗是 8x，背後 decode pipeline 至少要提供 8x 以上的音訊窗格吞吐；若 safety factor 是 1.35，目標容量就是 10.8x。可用併發壓力測試工具同時計算 1x、2x、4x、8x 的建議值：

```powershell
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\profile_decode_concurrency.py --key "<stable_key>" --windows 30,60,120 --concurrency 1,2,3,4 --playback-rates 1,2,4,8
```

互動模式預設會把 overall 建議限制在 60 秒窗格內。120 秒窗格可能在吞吐上漂亮，但對字幕追頭來說太粗；若要做批次預先計算，可用 `--max-overall-window-sec 0` 關閉這個上限。

這個工具只測遠端音訊 decode，不跑 Whisper。若某個 concurrency 的 P95 decode 時間上升、錯誤率上升，或 capacity 不再增加，就代表 Notion/CDN 或本機網路已經接近應激點。

Profiler 會拆出 `handshake` 與 `loop`。`handshake` 大致代表遠端 URL 開啟、協議握手、seek、stream 就緒等常數項；這部分應該在 UI 顯示成「串流預熱中」讀條，不應當作 ASR 或演算法慢。`loop` 才是實際取音與 resample 的可優化段。

這套排程不宣稱改變 ASR 演算法 Big-O。體感速度提升主要來自使用者習慣假設：播放頭附近最可能被立刻需要，所以資源先押在 T 附近；T 前缺洞與遠端區間可等 worker 空閒後補齊。若 Notion/CDN 抖動導致 `handshake` 變長，UI 應維持讀條或預熱提示，而不是讓使用者誤以為字幕引擎已經就緒。

Notion/CDN 要視為不受控外部服務，不可把單次成功吞吐當合約。後續 worker 需要內建 concurrency 上限、cooldown、失敗重排、backoff 與讀條；若 profile 顯示錯誤率上升或 capacity 不再增加，就要降 worker 或拉長 horizon。

字幕 sidecar 是昂貴計算結果的 cache，不是 UI 附屬物。完整課程只要成功生成一次 `.srt`、`.vtt` 或 `.md`，後續播放應優先讀 sidecar，避免重複觸發 Notion 遠端取音與 GPU ASR。只有來源影片、字幕模型設定或人工校訂版本變更時，才應重建字幕。

後續 rolling scheduler 的合理分工是：播放時間點 T 之前的缺口由 CPU 或低優先權 worker 補洞；T 之後的未來窗格由 GPU 優先追頭。多個無頭 worker 應負責遠端取音與切窗，不應等同於同時開很多 Whisper 模型實例。

2026-06-14 的實測口徑顯示，整節課一次生成字幕會讓使用者只看到最後一次交貨，和串流字幕體感衝突。rolling sidecar 應改用短窗格逐段落地。以 `medium / cuda / float16` 測同一支 Notion 影片，30 秒窗格常駐 worker 口徑約 6.08x，不足以穩追 8x；60 秒窗格在 T=480 秒約 11.94x、T=540 秒約 9.23x，可以追上 8x。`batch=16 / beam=5` 在另一個 60 秒窗格約 10.85x，可列為優先候選；`beam=1` 約 10.07x，但應視為追頭降級模式，不直接當品質預設。模型載入約 2 秒，正式架構應讓 Whisper worker 常駐，避免每個窗格都重新載入模型。握手時間約 12 到 16 秒，屬於串流預熱，不應混入 ASR 演算法速度判斷。

可先用排程檢視工具模擬 T 點附近的工作矩陣：

```powershell
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\plan_rolling_subtitles.py --position-sec 480 --duration-sec 900 --playback-rate 8 --headless-workers 7 --future-horizon-sec 180
```

若要讓 T 後方越靠近播放頭越細，可用 Fibonacci 切窗；T 前 backfill 仍維持等差切。以目前每窗格重新開遠端串流的實作，Fibonacci base 不應低於 60 秒。15 秒 base 只適合未來已能共享串流握手或長連線 demux 的版本：

```powershell
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\plan_rolling_subtitles.py --position-sec 480 --duration-sec 900 --playback-rate 8 --headless-workers 7 --future-horizon-sec 180 --future-window-strategy fibonacci --future-base-window-sec 60
```

若已有播放 cache 與字幕 sidecar，可用 `--key "<stable_key>"` 讓工具讀取目前播放位置與已覆蓋字幕區間。這一步只產生 future/backfill job plan，不會啟動 Whisper，也不會寫入 Notion。

## 目前已知播放邊界

Notion MCP 目前回傳的影片來源是 `file://{source: attachment...}` 內部附件標記，不是可直接交給播放器的 `https` 簽名 URL。程式已把這種來源分類為 `notion_attachment_marker`，UI 會顯示需要 resolver，不會假裝能播放。

真正串流 Notion 影片時，附件 URL resolver 會取得可播放 URL。現在已保留 `NotionAttachmentResolver`，若提供 `M1_NOTION_TOKEN` 或 `NOTION_TOKEN`，它會嘗試用官方 API 解析 block file URL。若沒有 token，會回報 `missing_token`。Notion file URL 是短效簽名 URL，不應長期寫死進 cache，播放前需要按需刷新。

UI 透過 `m1_player/playability.py` 顯示可播放性狀態。它會區分缺 Notion token、resolver 失敗、mpv 不可用、非串流 URL 與來源格式不支援，不把這些狀態混成同一個播放失敗訊息。即使 resolver 錯誤回傳本地檔案路徑，playability 也會擋下，不會交給 mpv。

`state/resolved_url_cache.json` 只保存短效 URL、到期時間與來源 hash，不保存影片本體。URL 快過期時會被視為不可用，避免 mpv 拿到快失效的串流來源。

Notion 附件實際落在 S3 短效 URL 上，HTTP `HEAD` 可能回 403，但 `Range` 串流可正常讀取。mpv 首次載入大型 Notion MP4 時可能需要約 10 秒完成 demux，UI 在這段時間可能尚未取得 duration 或 position；這不是下載整支影片，也不代表播放失敗。

接 token 前可以先跑來源形狀檢查：

```powershell
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\audit_sources.py --strict
```

`ready_for_token_resolution` 代表目前 cache 內的 Notion attachment marker 已有 permission block id，等 token 設好後才進一步嘗試解析短效 URL。

## 檢查

```powershell
$env:PYTHONUTF8='1'
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\smoke_test.py
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\ui_smoke_test.py
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\readiness.py
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\readiness.py --json
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\setup_guide.py
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\setup_guide.py --json
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\progress_overview.py
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\progress_overview.py --json
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\settings_status.py
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\settings_status.py --json
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\preflight.py
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\audit_sources.py --strict
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\check_streaming_policy.py --strict
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\set_schedule_url.py "<notion_schedule_database_view_url>"
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\set_token.py
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\scan_schedule.py --max-pages 5 --timeout-sec 45 --json
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\check_sources.py
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\check_subtitles.py --show-candidates
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\subtitle_manifest.py
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\subtitle_manifest.py --write-missing-md
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\lint_subtitles.py --json
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\generate_subtitles.py --check-deps
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\check_playback.py --ipc-smoke
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\resolve_sources.py --show-reason
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\preview_writeback.py
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\writeback_schema_template.py --markdown --check
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\bootstrap_completion_database.py --parent-from-schedule --json
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\bootstrap_completion_database.py --parent-from-schedule --apply --save
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\writeback_schema_template.py --fixture-only --output D:\RRKAL_tools\m1-makeup-player\tmp\completion_data_source.template.json
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\check_writeback_schema.py --fixture D:\RRKAL_tools\m1-makeup-player\tmp\completion_data_source.template.json
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\check_writeback_schema.py
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\writeback_apply_smoke.py --json
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\flush_writeback.py
```

含中文欄位名稱的 JSON 請用腳本的 `--output` 寫檔，不要用 PowerShell `>` 或 pipe 串接，避免中文 key 在 shell 管線中被轉碼破壞。

若已安裝 `mpv.exe`，可用任意可播放 URL 或本地影片測試播放核心：

```powershell
$env:M1_MPV_PATH='C:\path\to\mpv.exe'
py -3 D:\RRKAL_tools\m1-makeup-player\scripts\play_url.py "https://example.com/video.mp4" --seconds 15
```

## 邊界

- 不批次下載 Notion 影片本體。
- 不把影片列表寫進本地固定配置。
- 不在 UI 內直接查 Notion 或直接組 Notion API payload。
- 不在 token 與補課紀錄 data source 未設定時假裝已回寫 Notion。
- 不把字幕塞回 Notion，字幕先維持本地檔案。
