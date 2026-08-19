# 台股每日處置股網頁

每個交易日彙整台灣證交所（TWSE，上市）與證券櫃檯買賣中心（TPEx，上櫃）目前「仍在處置期間內」
的股票清單，產生單一靜態頁面 [`output/disposal.html`](output/disposal.html)。規格詳見
[`SPEC.md`](SPEC.md)。

## 本機執行

```bash
pip install -r requirements.txt
python fetch_and_render.py
```

執行後會直接呼叫 TWSE / TPEx 的公開 API、依今天日期過濾出仍在處置中的股票，並覆蓋產生
`output/disposal.html`。用瀏覽器打開該檔案即可查看。

若任一資料源抓取失敗，腳本不會中止，仍會用另一來源的資料產出網頁，並在頁面上方標註哪個
資料源抓取失敗。

## 用 Docker Desktop 執行

不想在本機裝 Python，也可以用 Docker 執行（需要先安裝並啟動 Docker Desktop）：

```bash
# 產生（或更新）output/disposal.html，執行完容器就結束
docker compose run --rm stock-disposal

# 啟動本機網頁伺服器查看結果：http://localhost:8080/
docker compose --profile web up -d webserver
```

- `stock-disposal` 服務：用 `Dockerfile`（python:3.12-alpine 多階段建置、非 root 使用者）
  執行 `fetch_and_render.py`，把 `./output`、`./data` 掛載進容器，執行完寫回本機的
  `output/disposal.html`、`data/material_info.json` 後容器即結束（一次性任務，非常駐）。
- `webserver` 服務：`nginx:alpine` 掛載 `./output`（唯讀）與 `nginx.conf`（讓
  `disposal.html` 可當首頁），監聽本機 `8080` port。放在 `web` profile，預設不會隨
  `docker compose up` 一起啟動，要用 `--profile web` 明確帶出來；常駐執行，重新整理瀏覽器
  即可看到 `stock-disposal` 每次重跑後的最新結果。
- 要停止／清除容器：`docker compose --profile web down`。

## 專案結構

```
├── fetch_and_render.py       # 抓 API + 過濾 + 產生 HTML
├── templates/
│   └── disposal.html.j2      # Jinja2 樣板（樣式與邏輯分離）
├── output/
│   └── disposal.html         # 產出結果（每次執行覆蓋）
├── data/
│   └── material_info.json    # 重大訊息持久化紀錄，供「依日期查詢」分頁使用（見下方說明）
├── requirements.txt
├── Dockerfile                 # stock-disposal 服務的映像檔（多階段建置、非 root）
├── docker-compose.yml         # stock-disposal（一次性）＋ webserver（nginx，web profile）
├── nginx.conf                 # webserver 用，讓 disposal.html 可當首頁
└── .github/workflows/daily-update.yml   # 排程：每天 18:00 台灣時間自動更新（含週末）
```

## 部署 / 排程

`.github/workflows/daily-update.yml` 會**每天**（含週末）UTC 10:00（台灣時間 18:00）自動執行
（處置股本身是交易日概念，週末不會有新資料；但重大訊息公司偶爾會在假日發布，每天執行
是為了不漏接這類假日公告）
`fetch_and_render.py`，並把更新後的 `output/disposal.html` commit 回 repo。

若要用 GitHub Pages 對外發布：

1. 到 repo **Settings → Pages**，**Build and deployment → Source** 選 **GitHub Actions**
   （不要選 "Deploy from a branch"——那個模式的資料夾只能選 `/` 或 `/docs`，沒有
   `/output` 可選；workflow 已經用 `actions/upload-pages-artifact` /
   `actions/deploy-pages` 直接把 `output/` 部署出去，不受這個限制）。
2. **GitHub Free 帳號的 Pages 只能用在 Public repo**（Private repo 需要 GitHub Pro），
   若 repo 是 Private，需要先到 Settings 最下面 Danger Zone 改成 Public 才能啟用。
3. 之後每次排程跑完，`output/` 有變動就會自動重新部署到 Pages 網址
   （`https://<帳號>.github.io/<repo名稱>/disposal.html`）。

也可以手動觸發：到 GitHub Actions 頁面選擇 `Daily Update Disposal Page` → `Run workflow`。

### Telegram 推播（選用）

排程每次跑完會嘗試用 Telegram Bot 把當日摘要（處置股/重大訊息則數 + 網頁連結）推播到手機，
沒設定的話會自動略過，不影響主流程。設定步驟：

1. Telegram 搜尋 **@BotFather**，傳送 `/newbot`，依提示建立一個 bot，拿到一組
   `Bot Token`（格式類似 `123456789:ABCdefGhIJKlmNoPQRstuVWXyz`）。
2. 用自己的帳號搜尋剛建立的 bot、傳一句話給它（先啟動對話）。
3. 瀏覽器打開 `https://api.telegram.org/bot<你的TOKEN>/getUpdates`，在回傳的 JSON 裡找
   `"chat":{"id": 數字, ...}`，這組數字就是 `Chat ID`。
4. 到這個 repo 的 **Settings → Secrets and variables → Actions**，新增兩個 repository
   secret：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`，值分別填入上面拿到的 Token 與 Chat ID。

之後排程每次執行完（不論是否有處置股資料變化）都會推播一則摘要；若想要「有變化才通知」
或加上更多內容，可以修改 `fetch_and_render.py` 的 `build_summary_text()`。推播訊息內容範例：

```
📊 台股每日處置股 2026-08-19
今日處置股：23 檔（上市 10、上櫃 13）
今日重大訊息：0 則
完整清單：https://<你的帳號>.github.io/<repo名稱>/disposal.html
```

（最後一行連結只有在 GitHub Pages 設定完成後才是真的可以打開的網址，否則可以先忽略。）

## 核心邏輯備註

- 「今天仍在處置期間內」的判斷：把日期轉成 8 碼民國年數字（例如 2026-08-09 → `1150809`）
  後做區間比較，詳見 `fetch_and_render.py` 的 `parse_period` / `today_num`。
- TWSE 與 TPEx 的 `DispositionPeriod` 格式不同（斜線＋全形波浪號 vs. 無斜線＋半形波浪號），
  已分別處理並統一轉換成西元年顯示。
- 處置原因欄位用 `<details><summary>` 折疊，展開後顯示 API 回傳的完整全文
  （`Detail` / `DisposalCondition`），而非短摘要。

## 個股資料 ／ 依日期查詢重大訊息

頁面分成三個分頁籤（純前端 JS 切換，無需重新整理），彼此完全獨立、不限於處置股：

- **處置股清單**：原本的處置股表格（市場／代號／名稱／處置期間／處置原因／處置措施）。
- **個股資料**：**全體**上市／上櫃公司**今日**公告的重大訊息（新訂單、財報、營收公告…等
  公司自行申報的重大訊息皆屬此類），每則顯示市場／代號／名稱／發言日期，以及可展開的完整
  說明全文；每列同樣附 Yahoo 奇摩股市、Goodinfo 連結（依代號組成 URL，不自行爬取新聞或
  財報內容）。
- **依日期查詢**：多一個日期選擇器，可任意切換查看**系統已經累積到的某一天**公告的重大
  訊息，切換日期時純前端 JS 從頁面內嵌的資料裡篩選、即時重繪表格，不需要重新整理或重新
  產生頁面。

資料源：TWSE `t187ap04_L`／TPEx `mopsfin_t187ap04_O`（上市／上櫃公司每日重大訊息，免金鑰）。

**重要限制**：這兩個 API 每次呼叫只回傳「最新一個交易日」的批次，無法查詢任意歷史日期。
因此腳本會把每次抓到的資料併入 `data/material_info.json`（去重後），並剔除超過
`MATERIAL_RETENTION_DAYS`（預設 180 天）的舊資料，讓「依日期查詢」能查到的範圍隨著
**每日排程重複執行**逐漸累積、往前滾動。也就是說：
- 剛把 repo 部署起來、`data/material_info.json` 還是空的時候，只能查到系統開始執行之後
  累積到的日子；「個股資料」分頁的「今日」永遠是即時抓取，不受累積進度影響。
- 日期選擇器的可選範圍（`min`／`max`）會依實際累積到的最早／最新日期自動調整。
- 若要在本機一次補齊更多天的資料，可以手動連續執行 `python fetch_and_render.py` 幾次
  （但同一天內重跑仍只會抓到當天最新批次，無法回溯更早之前遺漏的日子）。
- 此資料源抓取失敗時比照 TWSE／TPEx 處置股 API，會在頁面上方標註但不中止整頁產出。

## 股票新聞查詢

第四個分頁籤：輸入任意股票代號，顯示近期相關新聞標題（來源：Google 新聞 RSS，`news.google.com/rss/search`），
點擊標題在新分頁開啟原文；下方另外附幾個外部網站的查詢連結（Google 新聞搜尋、Yahoo 奇摩
股市上市／上櫃新聞頁、Goodinfo 個股頁）當備援。由於不知道輸入的代號是上市或上櫃，Yahoo
奇摩股市固定同時給 `.TW`／`.TWO` 兩個連結。

**技術限制與取捨**：這是純靜態頁面，沒有自己的後端伺服器；而 Google 新聞 RSS 不允許瀏覽器
直接跨網域讀取內容（沒有 CORS header）。有兩層代理可以解決這個問題，`fetch_and_render.py`
的 `NEWS_PROXY_BASE_URL` 決定要用哪一個：

- **本機 nginx**（`nginx.conf` 的 `/api/news`）：只有透過
  `docker compose --profile web up webserver` 開啟的網頁伺服器瀏覽時才會顯示；直接雙擊開啟
  `output/disposal.html` 檔案，或透過 GitHub Pages 瀏覽，`fetch()` 都會失敗。
- **Cloudflare Worker**（`cloudflare-worker/news-proxy.js`，部署步驟見檔案內註解）：把
  `NEWS_PROXY_BASE_URL` 設成部署好的 Worker 網址（例如
  `https://tw-stock-news-proxy.<你的帳號>.workers.dev`）之後，本機、GitHub Pages 都能用。

不論用哪種代理，**新聞標題功能都有已知的不穩定性，接受度因人而異，先說明清楚**：

- Google 會把來自雲端代管服務（例如 Cloudflare Workers 的共用 IP）的請求判定為「自動化
  查詢」而不定期擋掉（回傳 503），擋多久、多頻繁不受我們控制，實測時發現同一個代號有時候
  成功、幾分鐘內重複查詢又會被擋。本機 nginx 因為用的是你自己的網路 IP，被擋的機率通常
  低很多，但也不是完全不會發生。
- 頁面已經做了防呆：查不到標題時（不管是代理沒設定、fetch 失敗、還是被 Google 擋）都會
  自動退回顯示下方的查詢連結卡片（Google 新聞搜尋、Yahoo 奇摩股市、Goodinfo），使用者
  還是找得到新聞，只是要多點一次連結，不會整頁掛掉或顯示錯誤畫面。
- Worker 程式碼刻意**不快取任何回應**（`Cache-Control: no-store`）：早期版本用了
  `cf.cacheEverything`，若剛好某次被 Google 擋下，那個錯誤回應會被 Cloudflare 邊緣快取
  住，接下來幾分鐘同樣的查詢都會拿到快取的錯誤，看起來像「一直壞掉」，因此拿掉快取、
  改成失敗時在 Worker 內重試一次。
- Google 新聞 RSS 的版權聲明註明「僅供個人非商業用途的 feed reader 使用」；這裡是把它用在
  個人本機專案的網頁分頁上，非商業、非大量重新散布，但嚴格來說不完全等同「個人 feed
  reader」，使用前請自行評估是否符合你的使用情境。
