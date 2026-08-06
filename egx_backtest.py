#!/usr/bin/env python3
"""
EGX Backtest
------------
بيختبر الإشارات الفنية على تاريخ أسعار البورصة المصرية الحقيقي،
ويقارنها بالشراء والاحتفاظ (buy & hold).

السؤال الوحيد اللي بيجاوب عليه:
    هل الإشارات دي كانت هتكسب فعلاً، ولا الشراء والاحتفاظ أحسن؟

⚠️  قيود لازم تتقري قبل أي استنتاج — مشروحة في آخر الملف وفي التقرير.

الاستخدام:
    python3 egx_backtest.py                    # كل الاستراتيجيات، 5 سنين
    python3 egx_backtest.py --years 10
    python3 egx_backtest.py --cost 0.3         # تكلفة أعلى للصفقة
    python3 egx_backtest.py --symbols COMI,ABUK
"""

import argparse
import csv
import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from egx_technical import fetch_closes  # noqa: E402

CACHE = "price_cache.json"
OUT_FILE = "egx_backtest.csv"

# تكلفة الصفقة الواحدة (شراء أو بيع) بالنسبة المئوية.
# عمولة السمسرة في مصر + الفروق السعرية. 0.2% لكل جهة تقدير معقول
# ومحافظ — الرقم الحقيقي بيختلف حسب الشركة وحجم الصفقة.
DEFAULT_COST_PCT = 0.2

# أقل عدد جلسات عشان السهم يدخل الاختبار.
# 300 جلسة (~14 شهر) عشان متوسط 200 يوم يكون له معنى.
MIN_SESSIONS = 300


# ---------------------------------------------------------------- المؤشرات

def sma_series(values, period):
    out, total = [], 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        out.append(total / period if i >= period - 1 else None)
    return out


def ema_series_aligned(values, period):
    """EMA بنفس طول السلسلة الأصلية، مع None في البداية."""
    out = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi_series(values, period=14):
    """RSI بطريقة Wilder، بنفس طول السلسلة."""
    out = [None] * len(values)
    if len(values) < period + 1:
        return out
    gains = [max(values[i] - values[i - 1], 0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0) for i in range(1, len(values))]

    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    out[period] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)

    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        out[i + 1] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def macd_hist_series(values, fast=12, slow=26, signal=9):
    """هيستوجرام MACD بنفس طول السلسلة."""
    ef = ema_series_aligned(values, fast)
    es = ema_series_aligned(values, slow)
    macd = [
        (ef[i] - es[i]) if (ef[i] is not None and es[i] is not None) else None
        for i in range(len(values))
    ]
    valid = [(i, v) for i, v in enumerate(macd) if v is not None]
    out = [None] * len(values)
    if len(valid) < signal:
        return out
    idxs = [i for i, _ in valid]
    vals = [v for _, v in valid]
    sig = ema_series_aligned(vals, signal)
    for j, i in enumerate(idxs):
        if sig[j] is not None:
            out[i] = vals[j] - sig[j]
    return out


# ---------------------------------------------------------------- الاستراتيجيات
#
# كل استراتيجية بتاخد المؤشرات وبترجّع قايمة True/False:
# True يعني "لازم أكون شايل السهم في نهاية اليوم ده".
#
# ⚠️ حرج: الإشارة بتتحسب من إغلاق يوم t، والتنفيذ بيتم بإغلاق يوم t+1.
#    التأخير ده مقصود — من غيره بنكون بنشتري بسعر إحنا شفناه بعد
#    ما قررنا نشتري، وده غش بيخلي أي استراتيجية تبان ناجحة.


def strat_buy_hold(ind):
    return [True] * len(ind["close"])


def strat_above_ma200(ind):
    """شايل السهم طول ما السعر فوق متوسط 200 يوم."""
    return [
        ma is not None and c > ma
        for c, ma in zip(ind["close"], ind["ma200"])
    ]


def strat_golden_cross(ind):
    """شايل طول ما متوسط 50 فوق متوسط 200."""
    return [
        a is not None and b is not None and a > b
        for a, b in zip(ind["ma50"], ind["ma200"])
    ]


def strat_macd(ind):
    """شايل طول ما هيستوجرام MACD موجب."""
    return [h is not None and h > 0 for h in ind["macdHist"]]


def strat_rsi_meanrev(ind):
    """
    ارتداد: يشتري لما RSI ينزل تحت 30، ويبيع لما يعدي 55.
    محتاج حالة مستمرة مش شرط لحظي، فبنبنيه بالتسلسل.
    """
    held, out = False, []
    for r in ind["rsi"]:
        if r is None:
            out.append(False)
            continue
        if not held and r < 30:
            held = True
        elif held and r > 55:
            held = False
        out.append(held)
    return out


def strat_ma200_macd(ind):
    """المركّب: فلتر اتجاه (فوق متوسط 200) + زخم موجب (MACD)."""
    trend = strat_above_ma200(ind)
    momentum = strat_macd(ind)
    return [t and m for t, m in zip(trend, momentum)]


def strat_ma200_not_overbought(ind):
    """فوق متوسط 200 بس بره منطقة التشبع الشرائي (RSI < 70)."""
    trend = strat_above_ma200(ind)
    return [
        t and (r is not None and r < 70)
        for t, r in zip(trend, ind["rsi"])
    ]


STRATEGIES = {
    "شراء واحتفاظ": strat_buy_hold,
    "فوق متوسط 200": strat_above_ma200,
    "تقاطع ذهبي 50/200": strat_golden_cross,
    "MACD موجب": strat_macd,
    "ارتداد RSI (30/55)": strat_rsi_meanrev,
    "متوسط 200 + MACD": strat_ma200_macd,
    "متوسط 200 + RSI<70": strat_ma200_not_overbought,
}


# ---------------------------------------------------------------- المحرّك

def build_indicators(closes):
    return {
        "close": closes,
        "ma50": sma_series(closes, 50),
        "ma200": sma_series(closes, 200),
        "rsi": rsi_series(closes),
        "macdHist": macd_hist_series(closes),
    }


def run_strategy(closes, signals, cost_pct):
    """
    بيشغّل الاستراتيجية ويرجّع منحنى رأس المال ومقاييس الأداء.

    القاعدة: إشارة يوم t تتنفّذ بإغلاق يوم t+1.
    """
    cost = cost_pct / 100
    equity = 1.0
    holding = False
    curve = []
    trades = []
    entry_price = None
    entry_index = None

    for i in range(1, len(closes)):
        # عايد اليوم بيتحقق بس لو كنا شايلين السهم من قبل بداية اليوم
        if holding:
            equity *= closes[i] / closes[i - 1]

        # قرار بناءً على إشارة أمس (بدون استباق)
        want = signals[i - 1]

        if want and not holding:
            equity *= (1 - cost)
            holding = True
            entry_price, entry_index = closes[i], i
        elif not want and holding:
            equity *= (1 - cost)
            holding = False
            trades.append({
                "ret": closes[i] / entry_price - 1,
                "days": i - entry_index,
            })

        curve.append(equity)

    # لو لسه شايلين في الآخر، نقفل الصفقة للإحصاء
    if holding and entry_price:
        trades.append({
            "ret": closes[-1] / entry_price - 1,
            "days": len(closes) - 1 - entry_index,
        })

    return curve, trades


def max_drawdown(curve):
    """أكبر هبوط من قمة لقاع — بيقيس أسوأ ألم كان هيحصل."""
    peak, worst = curve[0] if curve else 1, 0.0
    for v in curve:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst * 100


def summarize(curve, trades, years):
    if not curve:
        return None
    total = (curve[-1] - 1) * 100
    cagr = ((curve[-1] ** (1 / years)) - 1) * 100 if years > 0 and curve[-1] > 0 else None
    wins = [t for t in trades if t["ret"] > 0]
    return {
        "totalReturn": total,
        "cagr": cagr,
        "maxDrawdown": max_drawdown(curve),
        "trades": len(trades),
        "winRate": (len(wins) / len(trades) * 100) if trades else None,
        "avgHoldDays": (statistics.mean(t["days"] for t in trades)) if trades else None,
        "timeInMarket": None,  # بتتحسب بره
    }


# ---------------------------------------------------------------- الداتا

def load_universe(limit=None):
    """رموز كل الأسهم من egx_data.csv."""
    path = "egx_data.csv"
    if not os.path.exists(path):
        sys.exit("❌ egx_data.csv مش موجود — شغّل egx_fetch.py الأول")
    symbols = [r["symbol"] for r in csv.DictReader(open(path, encoding="utf-8-sig"))]
    return symbols[:limit] if limit else symbols


def load_prices(symbols, refresh=False):
    """
    بيجيب تاريخ الأسعار مع تخزين محلي.
    الكاش مهم — من غيره كل تجربة بتعمل 224 طلب.
    """
    cache = {}
    if os.path.exists(CACHE) and not refresh:
        with open(CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)

    missing = [s for s in symbols if s not in cache]
    if missing:
        print(f"⏳ بجيب تاريخ أسعار {len(missing)} سهم...")

        def grab(sym):
            try:
                return sym, [[d.isoformat(), c] for d, c in fetch_closes(sym)]
            except Exception:
                return sym, []

        with ThreadPoolExecutor(max_workers=5) as pool:
            for sym, series in pool.map(grab, missing):
                cache[sym] = series

        with open(CACHE, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, separators=(",", ":"))
        print(f"✅ اتخزن الكاش في {CACHE}")

    return cache


# ---------------------------------------------------------------- التشغيل

def backtest(symbols, prices, years, cost_pct):
    """
    بيشغّل كل استراتيجية على كل سهم، وبيجمّع النتايج.
    كل سهم بياخد وزن متساوي — يعني النتيجة بتمثّل
    "لو وزّعت فلوسك بالتساوي على كل الأسهم دي".
    """
    cutoff = (date.today() - timedelta(days=int(years * 365.25))).isoformat()
    per_strategy = {name: [] for name in STRATEGIES}
    used = 0

    for sym in symbols:
        series = prices.get(sym) or []
        window = [(d, c) for d, c in series if d >= cutoff and c and c > 0]
        if len(window) < MIN_SESSIONS:
            continue

        # المؤشرات لازم تتحسب من تاريخ أطول من نافذة الاختبار،
        # وإلا أول 200 يوم في النافذة هيبقى متوسط 200 بتاعهم فاضي.
        warmup_start = max(0, len(series) - len(window) - 250)
        full = [c for _, c in series[warmup_start:] if c and c > 0]
        offset = len(full) - len(window)
        if offset < 0:
            continue

        ind_full = build_indicators(full)
        ind = {k: v[offset:] for k, v in ind_full.items()}
        closes = ind["close"]
        used += 1

        for name, fn in STRATEGIES.items():
            signals = fn(ind)
            curve, trades = run_strategy(closes, signals, cost_pct)
            stats = summarize(curve, trades, years)
            if stats:
                stats["timeInMarket"] = sum(1 for s in signals if s) / len(signals) * 100
                stats["symbol"] = sym
                per_strategy[name].append(stats)

    return per_strategy, used


def aggregate(results, years):
    """متوسط النتايج عبر كل الأسهم."""
    rows = []
    for name, entries in results.items():
        if not entries:
            continue
        finals = [1 + e["totalReturn"] / 100 for e in entries]
        median_total = (statistics.median(finals) - 1) * 100
        mean_total = (statistics.mean(finals) - 1) * 100
        rows.append({
            "strategy": name,
            "stocks": len(entries),
            "medianReturn": median_total,
            "meanReturn": mean_total,
            "medianCagr": statistics.median(
                [e["cagr"] for e in entries if e["cagr"] is not None] or [0]),
            "avgMaxDrawdown": statistics.mean([e["maxDrawdown"] for e in entries]),
            "avgTrades": statistics.mean([e["trades"] for e in entries]),
            "avgWinRate": statistics.mean(
                [e["winRate"] for e in entries if e["winRate"] is not None] or [0]),
            "avgTimeInMarket": statistics.mean([e["timeInMarket"] for e in entries]),
            "beatBuyHold": None,
        })
    return rows


def main():
    p = argparse.ArgumentParser(description="باكتيست للإشارات الفنية")
    p.add_argument("--years", type=float, default=5)
    p.add_argument("--cost", type=float, default=DEFAULT_COST_PCT,
                   help="تكلفة الصفقة الواحدة %%")
    p.add_argument("--symbols", help="رموز محددة مفصولة بفاصلة")
    p.add_argument("--limit", type=int, help="عدد الأسهم للاختبار")
    p.add_argument("--refresh", action="store_true", help="تجاهل الكاش")
    p.add_argument("--out", default=OUT_FILE)
    args = p.parse_args()

    symbols = ([s.strip().upper() for s in args.symbols.split(",")]
               if args.symbols else load_universe(args.limit))

    prices = load_prices(symbols, args.refresh)
    print(f"\n🔬 باكتيست {args.years:g} سنين · تكلفة {args.cost}% للصفقة")

    results, used = backtest(symbols, prices, args.years, args.cost)
    if not used:
        sys.exit("❌ مفيش أسهم بتاريخ كافٍ")

    rows = aggregate(results, args.years)
    rows.sort(key=lambda r: r["medianReturn"], reverse=True)

    # المقارنة بالشراء والاحتفاظ
    baseline = next((r for r in rows if r["strategy"] == "شراء واحتفاظ"), None)
    for r in rows:
        if baseline:
            r["beatBuyHold"] = r["medianReturn"] - baseline["medianReturn"]

    print(f"   {used} سهم بتاريخ كافٍ (من {len(symbols)})\n")

    head = (f"{'الاستراتيجية':<22}{'عائد وسيط':>11}{'سنوي':>9}"
            f"{'أسوأ هبوط':>11}{'صفقات':>8}{'نجاح':>8}{'في السوق':>10}{'مقابل ش.و':>11}")
    print(head)
    print("─" * (len(head) + 20))
    for r in rows:
        mark = "★" if r["strategy"] == "شراء واحتفاظ" else " "
        print(
            f"{mark}{r['strategy']:<21}"
            f"{r['medianReturn']:>10.1f}%{r['medianCagr']:>8.1f}%"
            f"{r['avgMaxDrawdown']:>10.1f}%{r['avgTrades']:>8.1f}"
            f"{r['avgWinRate']:>7.0f}%{r['avgTimeInMarket']:>9.0f}%"
            f"{(r['beatBuyHold'] or 0):>+10.1f}%"
        )

    with open(args.out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n✅ اتكتب في {args.out}")

    print("""
⚠️  قيود النتايج دي:
   • انحياز البقاء — الأسهم دي هي اللي عايشة ومدرجة النهاردة.
     الشركات اللي أفلست أو اتشالت مش موجودة، فالنتايج متفائلة بطبيعتها.
   • التحليل الأساسي مش متقاس — مفيش عندي P/E و ROE تاريخيين،
     فالفلترة الأساسية في egx_score.py لسه غير مثبتة.
   • أسعار الإغلاق بس — مفيش انزلاق سعري ولا فروق عرض/طلب حقيقية،
     والحدود السعرية اليومية في مصر ممكن تمنع التنفيذ أصلاً.
   • الأداء الماضي مش دليل على المستقبل.""")


if __name__ == "__main__":
    main()
