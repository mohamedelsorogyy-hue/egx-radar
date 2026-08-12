#!/usr/bin/env python3
"""
EGX Signal Accuracy Test
------------------------
بيقيس حاجة واحدة بس:

    لما الإشارة الفنية تقول "اشتري"، السهم بيطلع فعلاً كام مرة من 100؟

الفكرة إن أي إشارة لازم تتقارن بـ"العشوائية". لو السهم بيطلع 55% من
الأيام عادي (سوق صاعد)، وإشارة معينة بتصيب 55%، يبقى الإشارة **مش
بتضيف أي حاجة** — إنت كنت هتوصل لنفس النتيجة لو اشتريت بالعشوائي.

الفرق بين نسبة نجاح الإشارة والنسبة الأساسية هو **الحافة** (edge).
حافة قريبة من صفر = الإشارة مالهاش قيمة تنبؤية.

الاستخدام:
    python3 egx_signal_test.py
    python3 egx_signal_test.py --horizon 20     # الأفق بالجلسات
"""

import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from egx_backtest import (  # noqa: E402
    build_indicators, load_prices, load_universe,
)

CACHE = "price_cache.json"


def evaluate(closes, ind, horizon):
    """
    لكل يوم، بنسجّل حالة كل إشارة والنتيجة اللي حصلت بعد `horizon` جلسة.
    بنرجّع: {اسم الإشارة: [قايمة العوائد اللي حصلت لما الإشارة كانت شغالة]}
    """
    n = len(closes)
    out = {name: [] for name in SIGNALS}
    base = []

    for i in range(n - horizon):
        future = closes[i + horizon] / closes[i] - 1
        base.append(future)
        for name, test in SIGNALS.items():
            if test(ind, i):
                out[name].append(future)
    return out, base


# كل إشارة: بتاخد المؤشرات ورقم اليوم، وترجّع True لو الإشارة شغالة اليوم ده.
# دي نفس الإشارات اللي بيعرضها الداشبورد بالظبط.
SIGNALS = {
    "RSI تحت 30 (تشبع بيعي)":
        lambda d, i: d["rsi"][i] is not None and d["rsi"][i] < 30,
    "RSI فوق 70 (تشبع شرائي)":
        lambda d, i: d["rsi"][i] is not None and d["rsi"][i] > 70,
    "MACD موجب":
        lambda d, i: d["macdHist"][i] is not None and d["macdHist"][i] > 0,
    "السعر فوق متوسط 200":
        lambda d, i: d["ma200"][i] is not None and d["close"][i] > d["ma200"][i],
    "تقاطع ذهبي (م50 فوق م200)":
        lambda d, i: (d["ma50"][i] is not None and d["ma200"][i] is not None
                      and d["ma50"][i] > d["ma200"][i]),
    "السعر تحت متوسط 200":
        lambda d, i: d["ma200"][i] is not None and d["close"][i] < d["ma200"][i],
}


def main():
    p = argparse.ArgumentParser(description="اختبار دقة الإشارات الفنية")
    p.add_argument("--horizon", type=int, default=20,
                   help="بعد كام جلسة نقيس النتيجة (20 ≈ شهر)")
    p.add_argument("--limit", type=int)
    args = p.parse_args()

    symbols = load_universe(args.limit)
    prices = load_prices(symbols)

    totals = {name: [] for name in SIGNALS}
    base_all = []
    used = 0

    for sym in symbols:
        series = prices.get(sym) or []
        closes = [c for _, c in series if c and c > 0]
        if len(closes) < 400:
            continue
        used += 1
        ind = build_indicators(closes)
        res, base = evaluate(closes, ind, args.horizon)
        for name, vals in res.items():
            totals[name].extend(vals)
        base_all.extend(base)

    if not base_all:
        sys.exit("❌ مفيش داتا كفاية")

    base_win = sum(1 for r in base_all if r > 0) / len(base_all) * 100
    base_avg = statistics.mean(base_all) * 100

    print(f"\n🔬 اختبار دقة الإشارات · {used} سهم · الأفق {args.horizon} جلسة"
          f" (~{args.horizon//20 or 1} شهر)")
    print(f"   إجمالي الحالات المقيسة: {len(base_all):,}\n")

    print(f"{'الخط الأساسي (أي يوم عشوائي)':<32}{base_win:>9.1f}%"
          f"{base_avg:>11.2f}%{'—':>10}")
    print("─" * 64)

    rows = []
    for name, vals in totals.items():
        if len(vals) < 500:
            continue
        win = sum(1 for r in vals if r > 0) / len(vals) * 100
        avg = statistics.mean(vals) * 100
        rows.append((name, win, avg, win - base_win, len(vals)))

    rows.sort(key=lambda r: -r[3])
    for name, win, avg, edge, n in rows:
        mark = "✅" if edge > 5 else "⚠️ " if edge > 0 else "❌"
        print(f"{name:<32}{win:>9.1f}%{avg:>11.2f}%{edge:>+9.1f} {mark}")

    print(f"\n{'':32}{'نسبة الصعود':>9} {'متوسط العائد':>10} {'الحافة':>9}")
    print("""
📌 الحافة = الفرق بين نسبة نجاح الإشارة وبين شراء أي يوم عشوائي.
   حافة قريبة من صفر معناها إن الإشارة مابتضيفش أي معلومة —
   نفس النتيجة كنت هتوصلها لو اشتريت من غير ما تبص على أي مؤشر.""")


if __name__ == "__main__":
    main()
