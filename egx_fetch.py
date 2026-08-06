#!/usr/bin/env python3
"""
EGX Fundamentals Fetcher
------------------------
بيسحب الأرقام الأساسية لكل أسهم البورصة المصرية من stockanalysis.com
(نفس الداتا اللي بتظهر على الموقع - مصدرها S&P Global Market Intelligence)

مجاني تماماً - مفيش API key ولا حدود استخدام.

الاستخدام:
    python3 egx_fetch.py                  # كل الأسهم -> egx_data.csv
    python3 egx_fetch.py --limit 30       # أكبر 30 سهم بس (للتجربة السريعة)
    python3 egx_fetch.py --out my.csv     # اسم ملف مخصص
"""

import argparse
import csv
import json
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

BASE = "https://stockanalysis.com"
LIST_URL = f"{BASE}/list/egyptian-stock-exchange/__data.json"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# عدد الطلبات المتوازية. متزوّدهاش عن 6 عشان الموقع ما يحجبكش.
WORKERS = 5
RETRIES = 3


# ---------------------------------------------------------------- fetching

def http_get(url, timeout=30):
    """طلب HTTP مع إعادة محاولة و backoff تصاعدي."""
    last_err = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json",
                    "Referer": f"{BASE}/list/egyptian-stock-exchange/",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
            last_err = err
            if attempt < RETRIES - 1:
                # backoff مع عشوائية بسيطة عشان الطلبات ما تتزامنش
                time.sleep((2 ** attempt) + random.random())
    raise RuntimeError(f"فشل تحميل {url}: {last_err}")


def unflatten(flat, index=0, depth=0):
    """
    فك ترميز SvelteKit devalue: الداتا بتيجي كمصفوفة مسطّحة
    والأرقام جواها بتشاور على مواضع تانية في نفس المصفوفة.
    """
    if depth > 40:
        return None
    if isinstance(index, int):
        if index < 0:
            return None
        value = flat[index]
    else:
        value = index
    if isinstance(value, list):
        return [unflatten(flat, i, depth + 1) for i in value]
    if isinstance(value, dict):
        return {k: unflatten(flat, i, depth + 1) for k, i in value.items()}
    return value


def load_node(payload, node_index):
    """يجيب عقدة بيانات محددة من رد __data.json ويفكّها."""
    nodes = payload.get("nodes", [])
    if node_index >= len(nodes):
        return None
    node = nodes[node_index]
    if not node or node.get("type") != "data":
        return None
    return unflatten(node["data"], 0)


# ---------------------------------------------------------------- parsing

_SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def to_number(raw):
    """
    يحوّل قيم زي "477.50B" / "4.28%" / "+64.98%" / "1,234" لأرقام.
    بيرجّع None لو مفيش قيمة - مش صفر، عشان ما نخلطش
    بين "مفيش داتا" و"القيمة صفر" وده بيبوّظ الترتيب.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "").replace("+", "").replace("E£", "")
    if not text or text in {"-", "n/a", "N/A", "null"}:
        return None
    text = text.rstrip("%")
    multiplier = 1.0
    if text and text[-1].upper() in _SUFFIX:
        multiplier = _SUFFIX[text[-1].upper()]
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def first_number(text):
    """يطلّع أول رقم من نص زي "180.35 (+28.61%)" أو "6.00 (4.28%)"."""
    if not text:
        return None
    return to_number(str(text).split("(")[0])


def get_symbols(limit=None):
    """قايمة كل الأسهم المصرية مرتبة بالقيمة السوقية تنازلياً."""
    data = load_node(http_get(LIST_URL), 2)
    rows = (data or {}).get("stockData") or []
    symbols = [
        {
            "symbol": r["s"].split("/")[-1],
            "name": r.get("n"),
            "marketCap": r.get("marketCap"),
            "price": r.get("price"),
        }
        for r in rows
        if r.get("s") and r.get("subtype") == "stock"
    ]
    return symbols[:limit] if limit else symbols


def fetch_ratios(symbol):
    """
    نسب التقييم والربحية (P/B, ROE, ROA, الدين/حقوق الملكية, PEG).
    القيم بتيجي كمصفوفات: العنصر [0] هو TTM وبعده السنين بالترتيب التنازلي.
    """
    try:
        payload = http_get(f"{BASE}/quote/EGX/{symbol}/financials/ratios/__data.json")
    except RuntimeError:
        return {}
    fd = (load_node(payload, 2) or {}).get("financialData") or {}

    def ttm(key, as_percent=False):
        values = fd.get(key)
        if not isinstance(values, list) or not values:
            return None
        value = values[0]
        if value is None:
            return None
        return round(value * 100, 2) if as_percent else value

    return {
        "pbRatio": ttm("pb"),
        "psRatio": ttm("ps"),
        "pegRatio": ttm("pegRatio"),
        "roe": ttm("roe", as_percent=True),
        "roa": ttm("roa", as_percent=True),
        "debtEquity": ttm("debtequity"),
        "netDebtEquity": ttm("netdebtequity"),
        "earningsYield": ttm("earningsyield", as_percent=True),
    }


def fetch_quote(symbol):
    """
    التسعيرة الحية: حجم التداول، مدى الـ52 أسبوع، وتاريخ آخر جلسة.
    تاريخ الجلسة مهم — بيكشف لو السهم موقوف أو الداتا بايتة.
    """
    try:
        payload = http_get(f"{BASE}/api/quotes/a/EGX-{symbol}")
    except RuntimeError:
        return {}
    q = payload.get("data") or {}
    return {
        "volume": q.get("v"),
        "week52High": q.get("h52"),
        "week52Low": q.get("l52"),
        "lastTradeDate": q.get("td"),
    }


def fetch_stock(entry):
    """بيانات سهم واحد. بيرجّع None لو السهم مالوش صفحة أو حصل خطأ."""
    symbol = entry["symbol"]
    try:
        payload = http_get(f"{BASE}/quote/EGX/{symbol}/__data.json")
    except RuntimeError as err:
        print(f"  ⚠️  {symbol}: {err}", file=sys.stderr)
        return None

    info = (load_node(payload, 1) or {}).get("info") or {}
    d = load_node(payload, 2) or {}
    if not d:
        return None

    analysts = d.get("analystChart") or {}
    changes = d.get("changes") or {}
    # السعر الحالي: آخر إغلاق في شارت الـ overview،
    # ولو الشارت فاضي بنرجع لسعر القايمة الرئيسية
    chart = (d.get("chart") or {}).get("data") or []
    price = chart[-1].get("c") if chart else None
    if price is None:
        price = entry.get("price")

    target = first_number(d.get("target"))
    upside = None
    if target and price:
        upside = round((target - price) / price * 100, 2)

    quote = fetch_quote(symbol)
    dollar_volume = None
    if quote.get("volume") and price:
        dollar_volume = round(quote["volume"] * price)

    return {
        "symbol": symbol,
        "name": info.get("nameFull") or entry.get("name"),
        "industry": next(
            (i.get("v") for i in (d.get("infoTable") or []) if i.get("t") == "Industry"),
            None,
        ),
        "price": price,
        "marketCap": to_number(d.get("marketCap")),
        "peRatio": to_number(d.get("peRatio")),
        "forwardPE": to_number(d.get("forwardPE")),
        "eps": to_number(d.get("eps")),
        "epsGrowth": to_number(d.get("epsGrowth")),
        "revenue": to_number(d.get("revenue")),
        "revenueGrowth": to_number(d.get("revenueGrowth")),
        "netIncome": to_number(d.get("netIncome")),
        "netIncomeGrowth": to_number(d.get("netIncomeGrowth")),
        "netMargin": _margin(d.get("netIncome"), d.get("revenue")),
        "dividendYield": to_number(d.get("dividendYield")),
        "payoutRatio": to_number(d.get("payoutRatio")),
        "beta": to_number(d.get("beta")),
        "analystRating": d.get("analysts"),
        "analystCount": sum(analysts.values()) if analysts else None,
        "priceTarget": target,
        "upsidePct": upside,
        "ch1y": to_number(d.get("ch1y")),
        "price1m": changes.get("price1m"),
        "price3m": changes.get("price3m"),
        "price6m": changes.get("price6m"),
        "priceYTD": changes.get("priceYTD"),
        "earningsDate": d.get("earningsDate"),
        "exDividendDate": d.get("exDividendDate"),
        "volume": quote.get("volume"),
        "dollarVolume": dollar_volume,
        "week52High": quote.get("week52High"),
        "week52Low": quote.get("week52Low"),
        "lastTradeDate": quote.get("lastTradeDate"),
        **fetch_ratios(symbol),
    }


def _margin(net_income, revenue):
    """هامش صافي الربح %."""
    ni, rev = to_number(net_income), to_number(revenue)
    if ni is None or not rev:
        return None
    return round(ni / rev * 100, 2)


# ---------------------------------------------------------------- output

COLUMNS = [
    "symbol", "name", "industry", "price", "marketCap",
    "volume", "dollarVolume", "lastTradeDate",
    "peRatio", "forwardPE", "pbRatio", "psRatio", "pegRatio", "earningsYield",
    "eps", "epsGrowth",
    "revenue", "revenueGrowth", "netIncome", "netIncomeGrowth", "netMargin",
    "roe", "roa", "debtEquity", "netDebtEquity",
    "dividendYield", "payoutRatio", "beta",
    "analystRating", "analystCount", "priceTarget", "upsidePct",
    "ch1y", "price1m", "price3m", "price6m", "priceYTD",
    "week52High", "week52Low",
    "earningsDate", "exDividendDate",
]


def main():
    parser = argparse.ArgumentParser(description="سحب الأرقام الأساسية لأسهم البورصة المصرية")
    parser.add_argument("--limit", type=int, help="أكبر N سهم بالقيمة السوقية")
    parser.add_argument("--out", default="egx_data.csv", help="ملف الإخراج")
    args = parser.parse_args()

    print("⏳ بجيب قايمة الأسهم...")
    symbols = get_symbols(args.limit)
    print(f"✅ لقيت {len(symbols)} سهم\n")

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_stock, s): s for s in symbols}
        for future in as_completed(futures):
            done += 1
            row = future.result()
            if row:
                results.append(row)
            print(f"\r  {done}/{len(symbols)} ({len(results)} نجحوا)", end="", flush=True)

    print()
    if not results:
        sys.exit("❌ مرجعش أي بيانات - يمكن الموقع اتغير أو النت واقف")

    results.sort(key=lambda r: r.get("marketCap") or 0, reverse=True)

    with open(args.out, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    failed = len(symbols) - len(results)
    print(f"\n✅ اتكتب {len(results)} سهم في {args.out}  ({stamp})")
    if failed:
        print(f"⚠️  {failed} سهم مرجعوش داتا (غالباً موقوفين عن التداول أو مالهمش صفحة)")


if __name__ == "__main__":
    main()
