#!/usr/bin/env python3
"""
EGX Dashboard Data Builder
--------------------------
بيجمع مخرجات egx_score.py و egx_technical.py مع سلسلة أسعار مختصرة
ويطلّع ملف JSON واحد الداشبورد بيقراه.

الاستخدام:
    python3 egx_dashboard_data.py                 # أعلى 24 سهم
    python3 egx_dashboard_data.py --top 40
"""

import argparse
import csv
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from egx_technical import analyse, fetch_closes  # noqa: E402

SHORTLIST = "egx_shortlist.csv"
OUT_FILE = "dashboard/data.json"

# عدد الجلسات اللي بتتحفظ للرسم البياني.
# 420 جلسة (~20 شهر) عشان الشارت يقدر يرسم متوسط 200 يوم
# على مدى معقول — أقل من كده المتوسط هيبان ناقص.
SPARK_SESSIONS = 420


def num(value):
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(f, 4)


def count_market():
    """
    إجمالي أسهم السوق قبل أي فلترة.
    بيتقري من ملف الداتا الخام مش من الشورت-ليست، عشان العداد
    يقول "102 من 224" مش "102 من 102".
    """
    try:
        with open("egx_data.csv", encoding="utf-8-sig") as fh:
            return sum(1 for _ in csv.DictReader(fh))
    except FileNotFoundError:
        return None


def build_one(row):
    """بيدمج أرقام السهم الأساسية مع الفنية مع سلسلة السعر."""
    symbol = row["symbol"]
    tech = analyse(symbol)
    if not tech:
        return None

    history = fetch_closes(symbol)[-SPARK_SESSIONS:]
    series = [{"d": d.isoformat(), "c": round(c, 3)} for d, c in history]

    # التغير على آخر ~9 شهور — بيستخدم في لون الرسم المصغّر على الكارت
    tail = series[-180:]
    change_pct = None
    if len(tail) > 1 and tail[0]["c"]:
        change_pct = round((tail[-1]["c"] - tail[0]["c"]) / tail[0]["c"] * 100, 1)

    return {
        "symbol": symbol,
        "name": row.get("name") or symbol,
        "industry": row.get("industry") or "",
        # التقييم الأساسي
        "score": num(row.get("score")),
        "scoreValue": num(row.get("scoreValue")),
        "scoreQuality": num(row.get("scoreQuality")),
        "scoreGrowth": num(row.get("scoreGrowth")),
        "scoreSafety": num(row.get("scoreSafety")),
        "coverage": row.get("dataCoverage"),
        # الأرقام المالية
        "price": tech["price"],
        "pe": num(row.get("peRatio")),
        "pb": num(row.get("pbRatio")),
        "roe": num(row.get("roe")),
        "netMargin": num(row.get("netMargin")),
        "revenueGrowth": num(row.get("revenueGrowth")),
        "netIncomeGrowth": num(row.get("netIncomeGrowth")),
        "debtEquity": num(row.get("debtEquity")),
        "dividendYield": num(row.get("dividendYield")),
        "dollarVolume": num(row.get("dollarVolume")),
        "priceTarget": num(row.get("priceTarget")),
        "upside": num(row.get("upsidePct")),
        "analystRating": row.get("analystRating") or "",
        # الفني
        "trend": tech["trend"],
        "rsi": tech["rsi"],
        "macd": tech["macdSignal"],
        "macdHist": tech["macdHist"],
        "ma20": tech["ma20"],
        "ma50": tech["ma50"],
        "ma200": tech["ma200"],
        "vsMa50": tech["vsMa50Pct"],
        "vsMa200": tech["vsMa200Pct"],
        "support": tech["support"],
        "resistance": tech["resistance"],
        "levels": tech.get("levels") or [],
        "stopLoss": tech["stopLoss"],
        "riskPct": (
            round((tech["price"] - tech["stopLoss"]) / tech["price"] * 100, 1)
            if tech["stopLoss"] else None
        ),
        "high52": tech["high52"],
        "low52": tech["low52"],
        "fromHigh52": tech["fromHigh52Pct"],
        "atrPct": tech["atrPct"],
        "volumeRatio": tech["volumeRatio"],
        # السلسلة
        "series": series,
        "seriesChange": change_pct,
    }


def main():
    p = argparse.ArgumentParser(description="بناء ملف داتا الداشبورد")
    # الافتراضي: كل الأسهم اللي عدّت الفلترة.
    # الملف بيطلع ~1.2 ميجا خام لكن Cloudflare بيضغطه لـ~220 كيلوبايت.
    p.add_argument("--top", type=int, default=500)
    p.add_argument("--shortlist", default=SHORTLIST)
    p.add_argument("--out", default=OUT_FILE)
    args = p.parse_args()

    try:
        rows = list(csv.DictReader(open(args.shortlist, encoding="utf-8-sig")))
    except FileNotFoundError:
        sys.exit(f"❌ {args.shortlist} مش موجود — شغّل egx_score.py الأول")

    selected = rows[:args.top]
    print(f"⏳ ببني داتا {len(selected)} سهم...")

    with ThreadPoolExecutor(max_workers=5) as pool:
        stocks = [s for s in pool.map(build_one, selected) if s]

    if not stocks:
        sys.exit("❌ مفيش داتا")

    trade_date = max(
        (s["series"][-1]["d"] for s in stocks if s["series"]), default=None
    )

    # آخر يوم تداول مفروض يكون فيه جلسة (البورصة المصرية: الأحد–الخميس).
    # المقارنة بيه بتقول للمستخدم صراحةً: الأرقام دي جلسة النهاردة ولا أقدم؟
    today = datetime.now(timezone.utc).date()
    probe = today
    while probe.weekday() in (4, 5):      # الجمعة والسبت إجازة
        probe -= timedelta(days=1)
    expected = probe.isoformat()

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tradeDate": trade_date,
        "expectedTradeDate": expected,
        "isLatestSession": trade_date == expected,
        "passedFilters": len(rows),
        "universe": count_market(),
        "stocks": stocks,
        "summary": {
            "uptrend": sum(1 for s in stocks if s["trend"] in ("صاعد قوي", "صاعد")),
            "downtrend": sum(1 for s in stocks if s["trend"] in ("هابط قوي", "هابط")),
            "overbought": sum(1 for s in stocks if (s["rsi"] or 0) > 70),
            "oversold": sum(1 for s in stocks if s["rsi"] is not None and s["rsi"] < 30),
            "avgScore": round(
                sum(s["score"] for s in stocks if s["score"]) /
                max(sum(1 for s in stocks if s["score"]), 1), 1
            ),
        },
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(args.out) / 1024
    print(f"✅ {len(stocks)} سهم في {args.out}  ({size_kb:.0f} كيلوبايت)")
    print(f"   تاريخ آخر جلسة: {trade_date}")


if __name__ == "__main__":
    main()
