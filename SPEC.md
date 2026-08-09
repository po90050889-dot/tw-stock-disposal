# 台股每日處置股網頁 — 專案規格 (SPEC)

給 Claude Code 或其他實作者使用的規格文件。目標是把這個功能落地成一個真正的專案（repo），
可本機執行、可部署、可用 cron / GitHub Actions 排程每日更新。

## 1. 背景 / 目標

台灣證交所（TWSE，上市）與證券櫃檯買賣中心（TPEx，上櫃）每個交易日會公布「處置股」
（因異常交易被限制撮合頻率、需收足價金等處置措施的股票）。目標是做一個網頁，每日自動
彙整「今天仍在處置期間內」的上市＋上櫃股票清單，供使用者查看，並可展開查看完整處置原因。

## 2. 資料來源

| 市場 | API | 回傳格式 | 備註 |
|---|---|---|---|
| 上市 (TWSE) | `https://openapi.twse.com.tw/v1/announcement/punish` | JSON array | 免金鑰、公開資料 |
| 上櫃 (TPEx) | `https://www.tpex.org.tw/openapi/v1/tpex_disposal_information` | JSON array | 免金鑰、公開資料 |

### 2.1 TWSE 回傳欄位（重點）

```json
{
  "Number": "1",
  "Date": "1150806",              // 公告日 (民國年yyy MM dd)
  "Code": "039038",               // 股票代號
  "Name": "富鼎統一6A購01",         // 股票名稱
  "ReasonsOfDisposition": "連續三次", // 處置原因（短）
  "DispositionPeriod": "115/08/07～115/08/20", // 處置期間 (民國年/月/日～民國年/月/日，全形波浪號)
  "DispositionMeasures": "第一次處置",
  "Detail": "完整處置原因/期間/措施說明全文（含換行 \\n）"
}
```

### 2.2 TPEx 回傳欄位（重點）

```json
{
  "Date": "1150807",
  "SecuritiesCompanyCode": "6274",    // 股票代號
  "CompanyName": "台燿",               // 股票名稱
  "DispositionPeriod": "1150810~1150814", // 處置期間 (民國年月日~民國年月日，半形波浪號，無斜線)
  "DispositionReasons": "因連續3個營業日達本中心作業要點第四條第一項第一款", // 處置原因（短）
  "DisposalCondition": "完整處置原因/期間/措施說明全文"
}
```

**重要差異：** 兩邊 `DispositionPeriod` 的日期格式不同（TWSE 用 `115/08/07` 有斜線＋全形
波浪號 `～`；TPEx 用 `1150810` 無斜線＋半形波浪號 `~`），解析時要分別處理。

## 3. 核心邏輯：只顯示「今天仍在處置期間內」的股票

兩個 API 回傳的都是「近期曾發布」的處置公告，包含已過期、未開始、進行中的項目，**不能整包
直接顯示**，必須用今天的民國日期做區間過濾：

```
today_num = (今年民國年) * 10000 + 月 * 100 + 日   # e.g. 2026-08-09 -> 1150809
start_num <= today_num <= end_num  → 保留（今日仍在處置中）
```

- 若 `end_num < today_num`：處置已結束，排除。
- 若 `start_num > today_num`：處置尚未開始，排除。
- 兩邊日期都要先轉成單純的 8 碼數字（去掉斜線／全半形波浪號）再比較。

## 4. 網頁需求

單一靜態 HTML 檔（`disposal.html`），深色主題，內容包含：

1. 標題 + 產出時間（台灣時間 `Asia/Taipei`）+ 資料來源說明。
2. 統計卡片：今日處置股總數／上市檔數／上櫃檔數。
3. 表格欄位：市場（上市/上櫃 badge）、代號、名稱、處置期間（轉換成西元年顯示）、
   **處置原因（可展開）**、處置措施。
4. **處置原因欄位使用 `<details><summary>` 折疊元件**：`summary` 顯示簡短原因
   （`ReasonsOfDisposition` / `DispositionReasons`），展開後顯示完整說明全文
   （`Detail` / `DisposalCondition`，換行以 `<br>` 呈現）— 這是使用者明確要求的重點，
   不可只顯示短摘要。
5. 若當日無資料，表格顯示「今日無處置股資料」而不是空白。
6. RWD：手機版表格改為 block 排列的卡片式列表（用 `::before` 加欄位標籤）。
7. Footer 附上兩個原始 API 連結。

## 5. 排程需求

- 頻率：**每週一至五（交易日）18:00**（台灣時間）執行一次，盤後讓 TWSE / TPEx
  資料公布完整後再抓取。
- 每次執行：重新抓取兩個 API → 重新產生 `disposal.html`（覆蓋舊檔）。
- 若其中一個 API 抓取失敗：仍用另一個來源的資料產出網頁，並在頁面或執行紀錄註明
  「某資料源抓取失敗」。

## 6. 已知環境限制（重要，交接時務必告知）

目前的實作環境（Cowork sandbox）對外網路走白名單 proxy，**沙盒內的 Python
`urllib` / `requests` 直接呼叫 `openapi.twse.com.tw`、`www.tpex.org.tw` 會被
`403 blocked-by-allowlist` 擋掉**。因此目前作法是：

1. 用 Claude 的 `web_fetch` 工具（可以打通這兩個網域）先把 JSON 抓下來，存成本機檔案
   `twse_punish.json`、`tpex_disposal.json`。
2. Python 腳本 `gen_disposal_page.py` 只讀這兩個本機 JSON 檔案，**不自己發網路請求**，
   純粹做過濾＋渲染 HTML。

**如果改在一般開發環境 / CI（例如 GitHub Actions、自己的伺服器）實作，這個限制不存在**，
可以讓腳本直接用 `requests.get(url)` 一次抓資料＋產生頁面，不需要分兩步。這點請 Claude
Code 依實際部署環境決定要不要合併這兩步。

## 7. 建議的專案結構（若要重新用 Claude Code 落地成正式 repo）

```
tw-stock-disposal/
├── README.md
├── requirements.txt          # requests
├── fetch_and_render.py       # 直接抓 API + 過濾 + 產生 HTML（合併第6節的兩步）
├── templates/
│   └── disposal.html.j2      # 可選：改用 Jinja2 模板，邏輯與樣式分離
├── output/
│   └── disposal.html         # 產出結果
└── .github/
    └── workflows/
        └── daily-update.yml  # cron: "0 10 * * 1-5" (UTC，對應台灣 18:00) 執行後 commit/deploy output/
```

### 7.1 GitHub Actions 排程建議

```yaml
on:
  schedule:
    - cron: "0 10 * * 1-5"   # UTC 10:00 = 台灣 18:00，交易日
  workflow_dispatch: {}
```

部署方式可用 GitHub Pages（把 `output/` 設成 Pages 來源），每次跑完 commit 新的
`disposal.html` 即可自動更新公開網址。

## 8. 驗收標準 (Acceptance Criteria)

- [ ] 執行後 `disposal.html` 存在且可用瀏覽器開啟。
- [ ] 表格只列出「今天日期落在 DispositionPeriod 區間內」的股票（用當天日期手動抽查
      2–3 檔驗證起訖日）。
- [ ] 上市／上櫃兩個來源的資料都有出現在同一張表格，並用 badge 區分市場別。
- [ ] 每一列的處置原因可點擊展開，展開後文字為 API 的完整 `Detail` / `DisposalCondition`
      全文，而非短摘要。
- [ ] 手機寬度（< 640px）表格可正常閱讀，不需要橫向捲動。
- [ ] 任一資料源抓取失敗時，程式不會整個崩潰，仍會產出網頁並標註缺漏來源。
- [ ] 排程可自動重跑並覆蓋舊檔，時間為交易日 18:00（台灣時間）。

## 9. 目前已有的實作（可直接參考／複製邏輯）

Cowork session 中已完成一版可運作的實作，邏輯與本規格一致，檔案：
- `gen_disposal_page.py`：過濾＋渲染邏輯（第 3、4 節的完整程式碼）
- `twse_punish.json` / `tpex_disposal.json`：範例輸入資料格式
- `disposal.html`：目前產出的成品，可作為視覺驗收基準

Claude Code 可以直接讀這幾個檔案作為起點，依第 6、7 節的建議改成不依賴 `web_fetch`
工具、可獨立在一般環境跑的版本。
