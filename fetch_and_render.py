#!/usr/bin/env python3
"""台股每日處置股網頁產生器。

直接呼叫 TWSE / TPEx 公開 API，過濾出「今天仍在處置期間內」的股票，
並用 templates/disposal.html.j2 產生 output/disposal.html。

用法：
    python fetch_and_render.py
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import markupsafe
import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

TWSE_URL = "https://openapi.twse.com.tw/v1/announcement/punish"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"

# 上市／上櫃公司每日重大訊息（新訂單、財報、營收公告等都屬於重大訊息的一種）
TWSE_MATERIAL_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
TPEX_MATERIAL_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"

# 「股票新聞查詢」分頁讀真實新聞標題用的 CORS 代理（見 cloudflare-worker/news-proxy.js）。
# 留空的話，網頁會改用相對路徑 /api/news（只有本機 nginx 有代理這個路徑，GitHub Pages
# 沒有伺服器、一定失敗，會自動退回顯示查詢連結）。部署好 Cloudflare Worker 後把網址填在這裡，
# 例如 "https://tw-stock-news-proxy.your-subdomain.workers.dev"。
NEWS_PROXY_BASE_URL = "https://tw-stock-news-proxy.po90050889.workers.dev"

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

ROOT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = ROOT_DIR / "templates"
OUTPUT_PATH = ROOT_DIR / "output" / "disposal.html"
INDEX_PATH = ROOT_DIR / "output" / "index.html"
SUMMARY_PATH = ROOT_DIR / "output" / "summary.txt"
MATERIAL_LOG_PATH = ROOT_DIR / "data" / "material_info.json"

REQUEST_TIMEOUT = 15

# 官方 API 每次只回傳「最新一個交易日」的重大訊息批次，無法查詢任意歷史日期。
# 「依日期查詢」分頁只能查到系統開始執行、累積 data/material_info.json 之後的日子，
# 且最多只保留最近這麼多天，避免持久化檔案／頁面內嵌資料無限成長。
MATERIAL_RETENTION_DAYS = 180


@dataclass
class DisposalRecord:
    market: str  # "上市" | "上櫃"
    code: str
    name: str
    announce_date_display: str  # 處置公告日，西元年顯示
    duration_days: int  # 處置期間天數（含起訖日）
    period_display: str  # 西元年顯示
    start_num: int
    end_num: int
    reason_short: str
    reason_full: str
    measures: str


def fetch_json(url: str) -> tuple[list[dict] | None, str | None]:
    """抓取 API JSON，失敗時回傳 (None, 錯誤訊息)。"""
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json(), None
    except Exception as exc:  # noqa: BLE001 - 任何錯誤都要讓網頁照樣產出
        return None, f"{url} 抓取失敗：{exc}"


def roc_to_ad(roc_str: str) -> str:
    """"115/08/07" -> "2026/08/07\""""
    year, month, day = roc_str.split("/")
    return f"{int(year) + 1911:04d}/{month}/{day}"


def parse_period(period: str) -> tuple[int, int, str]:
    """解析處置期間字串，回傳 (start_num, end_num, 西元年顯示字串)。

    TWSE: "115/08/07～115/08/20"（全形波浪號、含斜線）
    TPEx: "1150810~1150814"（半形波浪號、無斜線）
    """
    for tilde in ("～", "~"):
        if tilde in period:
            start_raw, end_raw = period.split(tilde, 1)
            break
    else:
        raise ValueError(f"無法解析處置期間：{period!r}")

    start_raw = start_raw.strip()
    end_raw = end_raw.strip()

    if "/" in start_raw:
        start_num = int(start_raw.replace("/", ""))
        end_num = int(end_raw.replace("/", ""))
        display = f"{roc_to_ad(start_raw)} ～ {roc_to_ad(end_raw)}"
    else:
        start_num = int(start_raw)
        end_num = int(end_raw)
        start_ad = f"{start_num // 10000 + 1911:04d}/{start_num // 100 % 100:02d}/{start_num % 100:02d}"
        end_ad = f"{end_num // 10000 + 1911:04d}/{end_num // 100 % 100:02d}/{end_num % 100:02d}"
        display = f"{start_ad} ～ {end_ad}"

    return start_num, end_num, display


def today_num(now: datetime) -> int:
    """民國年 8 碼數字，例如 2026-08-09 -> 1150809。"""
    roc_year = now.year - 1911
    return roc_year * 10000 + now.month * 100 + now.day


def roc_to_date(roc_str: str) -> date:
    """"1150808" -> date(2026, 8, 8)"""
    n = int(roc_str)
    return date(n // 10000 + 1911, n // 100 % 100, n % 100)


def period_duration_days(start_num: int, end_num: int) -> int:
    """處置期間天數（含起訖日），start_num/end_num 為 roc_to_date 可解析的 7 碼民國年數字。"""
    return (roc_to_date(str(end_num)) - roc_to_date(str(start_num))).days + 1


def format_announce_date(raw_date: str) -> str:
    """處置公告日「Date」欄位（民國年 8 碼數字字串，例如 "1150806"）轉西元年顯示；解析失敗回傳原始值。"""
    try:
        return roc_to_date(raw_date).strftime("%Y/%m/%d")
    except (ValueError, TypeError):
        return raw_date or "—"


def build_twse_records(raw: list[dict]) -> list[DisposalRecord]:
    records = []
    for item in raw:
        try:
            start_num, end_num, display = parse_period(item["DispositionPeriod"])
        except (KeyError, ValueError):
            continue
        records.append(
            DisposalRecord(
                market="上市",
                code=item.get("Code", ""),
                name=item.get("Name", ""),
                announce_date_display=format_announce_date(item.get("Date", "")),
                duration_days=period_duration_days(start_num, end_num),
                period_display=display,
                start_num=start_num,
                end_num=end_num,
                reason_short=item.get("ReasonsOfDisposition", ""),
                reason_full=item.get("Detail", "") or item.get("ReasonsOfDisposition", ""),
                measures=item.get("DispositionMeasures", ""),
            )
        )
    return records


def build_tpex_records(raw: list[dict]) -> list[DisposalRecord]:
    records = []
    for item in raw:
        try:
            start_num, end_num, display = parse_period(item["DispositionPeriod"])
        except (KeyError, ValueError):
            continue
        records.append(
            DisposalRecord(
                market="上櫃",
                code=item.get("SecuritiesCompanyCode", ""),
                name=item.get("CompanyName", ""),
                announce_date_display=format_announce_date(item.get("Date", "")),
                duration_days=period_duration_days(start_num, end_num),
                period_display=display,
                start_num=start_num,
                end_num=end_num,
                reason_short=item.get("DispositionReasons", ""),
                reason_full=item.get("DisposalCondition", "") or item.get("DispositionReasons", ""),
                measures="",
            )
        )
    return records


def build_material_entries(raw: list[dict], market: str) -> list[dict]:
    """把重大訊息原始資料轉成統一格式的 dict，供 JSON 持久化保存（不做日期篩選）。"""
    entries = []
    for item in raw:
        if market == "上市":
            code = item.get("公司代號", "")
            name = item.get("公司名稱", "")
            subject = (item.get("主旨 ") or item.get("主旨") or "").strip()
        else:
            code = item.get("SecuritiesCompanyCode", "")
            name = item.get("CompanyName", "")
            subject = (item.get("主旨") or "").strip()

        announce_date = item.get("發言日期", "")
        if not code or not subject or not announce_date:
            continue

        entries.append(
            {
                "market": market,
                "code": code,
                "name": name,
                "announce_date": announce_date,
                "announce_time": item.get("發言時間", ""),
                "clause": item.get("符合條款", ""),
                "subject": subject,
                "detail": (item.get("說明") or "").strip(),
            }
        )
    return entries


def load_material_log() -> list[dict]:
    if not MATERIAL_LOG_PATH.exists():
        return []
    try:
        return json.loads(MATERIAL_LOG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_material_log(entries: list[dict]) -> None:
    MATERIAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    MATERIAL_LOG_PATH.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _material_key(entry: dict) -> tuple:
    return (entry["market"], entry["code"], entry["announce_date"], entry["announce_time"], entry["subject"])


def merge_material_entries(existing: list[dict], new_entries: list[dict]) -> list[dict]:
    """用 (市場,代號,發言日期,發言時間,主旨) 當作去重 key，同一則多次抓取只留一筆。"""
    by_key = {_material_key(e): e for e in existing}
    for entry in new_entries:
        by_key[_material_key(entry)] = entry
    return list(by_key.values())


def prune_material_entries(entries: list[dict], today: date, retention_days: int) -> list[dict]:
    kept = []
    for entry in entries:
        try:
            announce_date = roc_to_date(entry["announce_date"])
        except (KeyError, ValueError):
            continue
        if announce_date <= today and (today - announce_date).days <= retention_days:
            kept.append(entry)
    return kept


def material_entry_to_display(entry: dict) -> dict:
    """把持久化紀錄轉成樣板／前端 JS 用的顯示格式。"""
    announce_date = roc_to_date(entry["announce_date"])
    yahoo_suffix = "TW" if entry["market"] == "上市" else "TWO"
    code = entry["code"]
    return {
        "market": entry["market"],
        "code": code,
        "name": entry["name"],
        "announce_date_display": announce_date.strftime("%Y/%m/%d"),
        "announce_date_iso": announce_date.isoformat(),
        "clause": entry["clause"],
        "subject": entry["subject"],
        "detail": entry["detail"],
        "yahoo_url": f"https://tw.stock.yahoo.com/quote/{code}.{yahoo_suffix}",
        "goodinfo_url": f"https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID={code}",
    }


def render(
    records: list[DisposalRecord],
    today_material_infos: list[dict],
    material_log_display: list[dict],
    today_iso: str,
    errors: list[str],
    generated_at: datetime,
) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )

    def nl2br(value: str) -> Markup:
        escaped = str(markupsafe.escape(value or ""))
        return Markup(escaped.replace("\n", "<br>\n"))

    env.filters["nl2br"] = nl2br

    template = env.get_template("disposal.html.j2")

    listed_count = sum(1 for r in records if r.market == "上市")
    otc_count = sum(1 for r in records if r.market == "上櫃")

    min_date_iso = min((m["announce_date_iso"] for m in material_log_display), default=today_iso)

    return template.render(
        records=sorted(records, key=lambda r: (r.market, r.code)),
        material_infos=sorted(today_material_infos, key=lambda m: (m["market"], m["code"])),
        material_log=sorted(material_log_display, key=lambda m: m["announce_date_iso"], reverse=True),
        today_iso=today_iso,
        min_date_iso=min_date_iso,
        total_count=len(records),
        listed_count=listed_count,
        otc_count=otc_count,
        material_count=len(today_material_infos),
        material_retention_days=MATERIAL_RETENTION_DAYS,
        news_proxy_base_url=NEWS_PROXY_BASE_URL,
        errors=errors,
        generated_at=generated_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
    )


def build_summary_text(
    active_records: list[DisposalRecord],
    today_material_infos: list[dict],
    errors: list[str],
    generated_at: datetime,
) -> str:
    """給 GitHub Actions 推播（例如 Telegram）用的純文字摘要。"""
    listed_count = sum(1 for r in active_records if r.market == "上市")
    otc_count = sum(1 for r in active_records if r.market == "上櫃")

    lines = [
        f"📊 台股每日處置股 {generated_at.strftime('%Y-%m-%d')}",
        f"今日處置股：{len(active_records)} 檔（上市 {listed_count}、上櫃 {otc_count}）",
        f"今日重大訊息：{len(today_material_infos)} 則",
    ]
    if errors:
        lines.append(f"⚠ {len(errors)} 個資料源抓取失敗，詳見網頁上方標註")

    repo = os.environ.get("GITHUB_REPOSITORY")  # GitHub Actions 內建環境變數，例如 "user/tw-stock-disposal"
    if repo and "/" in repo:
        owner, _, name = repo.partition("/")
        lines.append(f"完整清單：https://{owner}.github.io/{name}/disposal.html")

    return "\n".join(lines)


def main() -> int:
    now = datetime.now(TAIPEI_TZ)
    today = today_num(now)

    errors: list[str] = []
    all_records: list[DisposalRecord] = []

    twse_raw, twse_err = fetch_json(TWSE_URL)
    if twse_err:
        errors.append(f"上市（TWSE）資料源抓取失敗：{twse_err}")
    elif twse_raw:
        all_records.extend(build_twse_records(twse_raw))

    tpex_raw, tpex_err = fetch_json(TPEX_URL)
    if tpex_err:
        errors.append(f"上櫃（TPEx）資料源抓取失敗：{tpex_err}")
    elif tpex_raw:
        all_records.extend(build_tpex_records(tpex_raw))

    active_records = [r for r in all_records if r.start_num <= today <= r.end_num]

    # 重大訊息：全體上市／上櫃、不限處置股。官方 API 只給最新一個交易日的批次，抓到後
    # 併入本機保存的紀錄，並剔除超過 MATERIAL_RETENTION_DAYS 天的舊資料，藉多次（每日
    # 排程）執行累積出可查詢的歷史清單
    new_material: list[dict] = []

    twse_mat_raw, twse_mat_err = fetch_json(TWSE_MATERIAL_URL)
    if twse_mat_err:
        errors.append(f"上市重大訊息資料源抓取失敗：{twse_mat_err}")
    elif twse_mat_raw:
        new_material.extend(build_material_entries(twse_mat_raw, "上市"))

    tpex_mat_raw, tpex_mat_err = fetch_json(TPEX_MATERIAL_URL)
    if tpex_mat_err:
        errors.append(f"上櫃重大訊息資料源抓取失敗：{tpex_mat_err}")
    elif tpex_mat_raw:
        new_material.extend(build_material_entries(tpex_mat_raw, "上櫃"))

    material_log = merge_material_entries(load_material_log(), new_material)
    material_log = prune_material_entries(material_log, now.date(), MATERIAL_RETENTION_DAYS)
    save_material_log(material_log)

    material_log_display = [material_entry_to_display(e) for e in material_log]
    today_iso = now.date().isoformat()
    today_material_infos = [m for m in material_log_display if m["announce_date_iso"] == today_iso]

    html = render(active_records, today_material_infos, material_log_display, today_iso, errors, now)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    INDEX_PATH.write_text(
        '<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">'
        '<meta http-equiv="refresh" content="0; url=disposal.html">'
        '<title>台股每日處置股</title></head>'
        '<body>頁面已搬移，若沒有自動跳轉請點<a href="disposal.html">這裡</a>。</body></html>',
        encoding="utf-8",
    )
    SUMMARY_PATH.write_text(
        build_summary_text(active_records, today_material_infos, errors, now), encoding="utf-8"
    )

    print(
        f"已產出 {OUTPUT_PATH}（{len(active_records)} 檔今日處置中、"
        f"{len(today_material_infos)} 則今日重大訊息、累積 {len(material_log_display)} 則可查詢、"
        f"{len(errors)} 個錯誤）"
    )
    for err in errors:
        print(f"  - {err}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
