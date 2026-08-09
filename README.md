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

## 專案結構

```
├── fetch_and_render.py       # 抓 API + 過濾 + 產生 HTML
├── templates/
│   └── disposal.html.j2      # Jinja2 樣板（樣式與邏輯分離）
├── output/
│   └── disposal.html         # 產出結果（每次執行覆蓋）
├── requirements.txt
└── .github/workflows/daily-update.yml   # 排程：交易日 18:00 台灣時間自動更新
```

## 部署 / 排程

`.github/workflows/daily-update.yml` 會在每週一到五 UTC 10:00（台灣時間 18:00）自動執行
`fetch_and_render.py`，並把更新後的 `output/disposal.html` commit 回 repo。

若要用 GitHub Pages 對外發布：

1. 到 repo 設定 → Pages，Source 選擇 branch（例如 `main`）＋資料夾 `/output`。
2. 之後每次排程跑完、`output/disposal.html` 有變動，Pages 網址就會自動更新。

也可以手動觸發：到 GitHub Actions 頁面選擇 `Daily Update Disposal Page` → `Run workflow`。

## 核心邏輯備註

- 「今天仍在處置期間內」的判斷：把日期轉成 8 碼民國年數字（例如 2026-08-09 → `1150809`）
  後做區間比較，詳見 `fetch_and_render.py` 的 `parse_period` / `today_num`。
- TWSE 與 TPEx 的 `DispositionPeriod` 格式不同（斜線＋全形波浪號 vs. 無斜線＋半形波浪號），
  已分別處理並統一轉換成西元年顯示。
- 處置原因欄位用 `<details><summary>` 折疊，展開後顯示 API 回傳的完整全文
  （`Detail` / `DisposalCondition`），而非短摘要。
