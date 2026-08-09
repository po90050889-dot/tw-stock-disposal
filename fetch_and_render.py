#!/usr/bin/env python3
"""台股每日處置股網頁產生器。

直接呼叫 TWSE / TPEx 公開 API，過濾出「今天仍在處置期間內」的股票，
並用 templates/disposal.html.j2 產生 output/disposal.html。

用法：
    python fetch_and_render.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

TWSE_URL = "https://openapi.twse.com.tw/v1/announcement/punish"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

ROOT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = ROOT_DIR / "templates"
OUTPUT_PATH = ROOT_DIR / "output" / "disposal.html"

REQUEST_TIMEOUT = 15


@dataclass
class DisposalRecord:
    market: str  # "上市" | "上櫃"
    code: str
    name: str
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
                period_display=display,
                start_num=start_num,
                end_num=end_num,
                reason_short=item.get("DispositionReasons", ""),
                reason_full=item.get("DisposalCondition", "") or item.get("DispositionReasons", ""),
                measures="",
            )
        )
    return records


def render(records: list[DisposalRecord], errors: list[str], generated_at: datetime) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )

    def nl2br(value: str) -> str:
        escaped = env.filters["e"](value or "")
        return escaped.replace("\n", "<br>\n")

    env.filters["nl2br"] = nl2br

    template = env.get_template("disposal.html.j2")

    listed_count = sum(1 for r in records if r.market == "上市")
    otc_count = sum(1 for r in records if r.market == "上櫃")

    return template.render(
        records=sorted(records, key=lambda r: (r.market, r.code)),
        total_count=len(records),
        listed_count=listed_count,
        otc_count=otc_count,
        errors=errors,
        generated_at=generated_at.strftime("%Y-%m-%d %H:%M:%S %Z"),
    )


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

    html = render(active_records, errors, now)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")

    print(f"已產出 {OUTPUT_PATH}（{len(active_records)} 檔今日處置中，{len(errors)} 個錯誤）")
    for err in errors:
        print(f"  - {err}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
