/**
 * Cloudflare Worker：代理 Google 新聞 RSS，補上 CORS header。
 *
 * 為什麼需要這個：
 * Google 新聞 RSS（news.google.com/rss/search）不允許瀏覽器直接跨網域讀取內容
 * （沒有 Access-Control-Allow-Origin），本機用 Docker 跑的話可以靠 nginx 反向代理
 * 解決，但 GitHub Pages 是純靜態網頁主機、沒有伺服器可以代為轉發，所以需要另外一個
 * 免費雲端服務來扮演一樣的角色。
 *
 * 部署方式（Cloudflare Dashboard，不需要安裝任何工具）：
 *   1. https://dash.cloudflare.com/ 註冊一個免費帳號（可用 GitHub 帳號登入）。
 *   2. 左側選單 Workers & Pages → Create → Create Worker，取個名字（例如
 *      tw-stock-news-proxy），先按 Deploy 產生一個預設的 Worker。
 *   3. 進去該 Worker，點 Edit code，把預設程式碼整個換成這個檔案的內容，
 *      按 Save and Deploy。
 *   4. Worker 頁面上會顯示一個網址，格式類似
 *      https://tw-stock-news-proxy.<你的帳號>.workers.dev
 *      把這個網址填進 fetch_and_render.py 的 NEWS_PROXY_BASE_URL。
 *
 * 用法：GET <worker網址>?q=<股票代號>
 * 回傳：Google 新聞 RSS 的原始 XML，並附上 Access-Control-Allow-Origin: *
 */

const GOOGLE_NEWS_RSS = "https://news.google.com/rss/search";
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export default {
  async fetch(request) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS_HEADERS });
    }

    const url = new URL(request.url);
    const q = url.searchParams.get("q");
    if (!q) {
      return new Response("Missing required query parameter: q", {
        status: 400,
        headers: CORS_HEADERS,
      });
    }

    const upstream = new URL(GOOGLE_NEWS_RSS);
    upstream.searchParams.set("q", q);
    upstream.searchParams.set("hl", "zh-TW");
    upstream.searchParams.set("gl", "TW");
    upstream.searchParams.set("ceid", "TW:zh-Hant");

    // 不對「Google 的回應」做 Cloudflare 邊緣快取（cf.cacheEverything）：如果 Google
    // 偶爾把某次請求判定為自動化查詢而擋掉，快取會把那個錯誤頁面也存起來，之後幾分鐘
    // 內同樣的查詢都會拿到快取的錯誤，看起來像「一直壞掉」。改成失敗時重試一次即可，
    // 不快取任何結果（Cache-Control: no-store）。
    let resp;
    let body = "";
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        resp = await fetch(upstream.toString(), {
          headers: { "User-Agent": USER_AGENT },
        });
      } catch (err) {
        return new Response("Upstream fetch failed: " + err, {
          status: 502,
          headers: CORS_HEADERS,
        });
      }
      body = await resp.text();
      if (resp.ok) break;
    }

    return new Response(body, {
      status: resp.status,
      headers: {
        ...CORS_HEADERS,
        "Content-Type": "application/xml; charset=utf-8",
        "Cache-Control": "no-store",
      },
    });
  },
};
