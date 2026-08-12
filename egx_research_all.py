#!/usr/bin/env python3
"""
EGX Research — Batch
--------------------
بيعمل تقرير تحليل أساسي لكل الأسهم، وبيطلّع:

    dashboard/research/{رمز}.json   تقرير كامل لكل سهم
    dashboard/research.json          ملخص + ترتيب الفرص + إحصاءات القطاعات

الترتيب النهائي بيدمج 4 محاور، وكل واحد مبرر برقم:
    التقييم · الجودة · النمو · المتانة المالية

الاستخدام:
    python3 egx_research_all.py
    python3 egx_research_all.py --symbols TMGH,COMI
"""

import argparse
import csv
import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import egx_research as R  # noqa: E402

OUT_DIR = "dashboard/research"
OUT_SUMMARY = "dashboard/research.json"
WORKERS = 4


def safe_build(args):
    symbol, rows = args
    try:
        return symbol, R.build(symbol, rows)
    except SystemExit:
        return symbol, None
    except Exception as err:                      # noqa: BLE001
        print(f"\n  ⚠️  {symbol}: {type(err).__name__} {err}", file=sys.stderr)
        return symbol, None


# ---------------------------------------------------------------- الترتيب

def verdict(rep):
    """
    حكم مختصر على السهم من الأرقام.

    مش توصية شراء — ده تلخيص لموقع السهم من قطاعه ومن تاريخه،
    عشان تعرف تبدأ تبص فين. القرار بيفضل قرارك.
    """
    v, b, q, g = rep["valuation"], rep["balance"], rep["quality"], rep["growth"]
    points, notes = 0, []

    # التقييم مقابل القطاع
    vs = v.get("vsSector")
    if vs is not None:
        if vs < -25:
            points += 2; notes.append("أرخص من القطاع بوضوح")
        elif vs < -10:
            points += 1; notes.append("أرخص من القطاع")
        elif vs > 40:
            points -= 2; notes.append("أغلى من القطاع بكتير")
        elif vs > 15:
            points -= 1; notes.append("أغلى من القطاع")

    # النمو الحقيقي في آخر ربع
    qr = g.get("qRevenueYoY")
    if qr is not None:
        if qr > 25:
            points += 2; notes.append("إيرادات بتنمو بقوة")
        elif qr > 8:
            points += 1; notes.append("إيرادات بتنمو")
        elif qr < 0:
            points -= 2; notes.append("إيرادات بتتقلص")

    # الهوامش: عالية وثابتة = ميزة تنافسية
    nm, sd = q.get("netMargin"), q.get("grossMarginStdev")
    if nm is not None and nm > 20:
        points += 1; notes.append("هامش ربح عالي")
    if sd is not None and sd < 4:
        points += 1; notes.append("هوامش ثابتة")
    elif sd is not None and sd > 10:
        points -= 1; notes.append("هوامش متقلبة")

    # المتانة المالية
    de = b.get("debtToEquity")
    if de is not None:
        if de < 0.4:
            points += 1; notes.append("مديونية منخفضة")
        elif de > 1.5:
            points -= 2; notes.append("مديونية عالية")

    cc = b.get("cashConversion")
    if cc is not None:
        if cc > 1:
            points += 1; notes.append("أرباح بتتحوّل لكاش")
        elif cc < 0.5:
            points -= 2; notes.append("أرباح مش بتتحوّل لكاش")

    fcf = b.get("freeCashFlow")
    if fcf is not None and fcf < 0:
        points -= 1; notes.append("تدفق حر سالب")

    label = ("قوي" if points >= 5 else
             "جيد" if points >= 2 else
             "متوسط" if points >= 0 else
             "ضعيف" if points >= -3 else "خطر")

    return {"points": points, "label": label, "notes": notes}


def main():
    p = argparse.ArgumentParser(description="تقارير بحثية لكل الأسهم")
    p.add_argument("--symbols", help="رموز محددة")
    p.add_argument("--shortlist", default="egx_shortlist.csv")
    args = p.parse_args()

    rows = list(csv.DictReader(open("egx_data.csv", encoding="utf-8-sig")))
    rows_by = {r["symbol"]: r for r in rows}
    R.rows_g = rows_by

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        try:
            short = list(csv.DictReader(open(args.shortlist, encoding="utf-8-sig")))
            symbols = [r["symbol"] for r in short]
        except FileNotFoundError:
            symbols = list(rows_by)

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"⏳ ببني تقارير {len(symbols)} سهم...")

    reports, done = [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for symbol, rep in pool.map(safe_build,
                                    [(s, rows_by) for s in symbols]):
            done += 1
            print(f"\r  {done}/{len(symbols)}", end="", flush=True)
            if not rep:
                continue
            rep["verdict"] = verdict(rep)
            with open(os.path.join(OUT_DIR, f"{symbol}.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(rep, fh, ensure_ascii=False, separators=(",", ":"))
            reports.append(rep)

    print(f"\n✅ {len(reports)} تقرير")

    # ---- ملخص خفيف للداشبورد
    summary = []
    for r in reports:
        v, b, q, g, fv = (r["valuation"], r["balance"], r["quality"],
                          r["growth"], r["fairValue"])
        summary.append({
            "symbol": r["symbol"], "name": r["name"],
            "sector": r["sector"].get("name"),
            "price": r["price"],
            "verdict": r["verdict"]["label"],
            "points": r["verdict"]["points"],
            "notes": r["verdict"]["notes"],
            "pe": v.get("pe"), "peForward": v.get("peForward"),
            "vsSector": v.get("vsSector"), "vsOwnHistory": v.get("vsOwnHistory"),
            "sectorPE": v.get("sectorPE"),
            "netMargin": q.get("netMargin"),
            "marginStability": q.get("grossMarginStdev"),
            "qRevenueYoY": g.get("qRevenueYoY"),
            "qNetIncomeYoY": g.get("qNetIncomeYoY"),
            "debtToEquity": b.get("debtToEquity"),
            "currentRatio": b.get("currentRatio"),
            "cashConversion": b.get("cashConversion"),
            "freeCashFlow": b.get("freeCashFlow"),
            "fairValueMid": fv.get("mid") if fv else None,
            "fairValueLow": fv.get("low") if fv else None,
            "fairValueHigh": fv.get("high") if fv else None,
            "upside": fv.get("upside") if fv else None,
            "riskCount": sum(1 for k in r["risks"] if k["level"] == "عالية"),
            "topRisk": next((k["title"] for k in r["risks"]
                             if k["level"] == "عالية"), None),
        })

    summary.sort(key=lambda x: -(x["points"] or -99))

    # ---- إحصاءات القطاعات
    sectors = {}
    for s in summary:
        sectors.setdefault(s["sector"] or "غير مصنّف", []).append(s)

    sector_stats = []
    for name, items in sectors.items():
        pes = [i["pe"] for i in items if i["pe"] and i["pe"] > 0]
        gr = [i["qRevenueYoY"] for i in items if i["qRevenueYoY"] is not None]
        nm = [i["netMargin"] for i in items if i["netMargin"] is not None]
        best = max(items, key=lambda i: i["points"] or -99)
        sector_stats.append({
            "name": name, "count": len(items),
            "medianPE": round(statistics.median(pes), 2) if pes else None,
            "medianGrowth": round(statistics.median(gr), 1) if gr else None,
            "medianMargin": round(statistics.median(nm), 1) if nm else None,
            "best": best["symbol"], "bestPoints": best["points"],
        })
    sector_stats.sort(key=lambda s: -s["count"])

    from datetime import datetime, timezone
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(summary),
        "stocks": summary,
        "sectors": sector_stats,
    }
    with open(OUT_SUMMARY, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    size = os.path.getsize(OUT_SUMMARY) / 1024
    print(f"✅ الملخص في {OUT_SUMMARY} ({size:.0f} كيلوبايت) · "
          f"{len(sector_stats)} قطاع")

    print("\n  أعلى 10 حسب الأرقام:")
    for s in summary[:10]:
        print(f"   {s['symbol']:<8}{s['verdict']:<7}{s['points']:>3} نقطة  "
              f"{(s['sector'] or '')[:22]:<24}"
              f"{'  '+ ' · '.join(s['notes'][:2]) if s['notes'] else ''}")


if __name__ == "__main__":
    main()
