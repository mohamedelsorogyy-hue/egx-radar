#!/usr/bin/env python3
"""
EGX Scoring & Shortlist
-----------------------
بياخد egx_data.csv وبيطلّع منه شورت-ليست مرتبة.

الفكرة على مرحلتين:
  1. فلاتر إقصاء (hard filters) — بتشيل الأسهم اللي أصلاً مينفعش نلعب فيها
  2. تقييم نسبي (percentile scoring) — كل سهم بياخد درجة مقارنةً بباقي السوق

ليه ترتيب نسبي مش أرقام مطلقة؟ لأن "P/E كويس" في البنوك غير الأسمنت
غير العقارات. الترتيب النسبي بيقارن السهم بالسوق اللي هو فيه فعلاً.

الاستخدام:
    python3 egx_score.py                      # شورت-ليست 15 سهم
    python3 egx_score.py --top 25
    python3 egx_score.py --min-volume 10      # حد أدنى 10 مليون جنيه تداول يومي
    python3 egx_score.py --explain COMI       # تفصيل درجات سهم معين
"""

import argparse
import csv
import sys
from collections import defaultdict

IN_FILE = "egx_data.csv"
OUT_FILE = "egx_shortlist.csv"

# الحد الأدنى لقيمة التداول اليومية بالجنيه.
# أقل من كده يبقى الخروج من السهم نفسه مشكلة، مهما كانت أرقامه حلوة.
MIN_DOLLAR_VOLUME = 5_000_000

# الأوزان. المجموع 100.
WEIGHTS = {
    "value": 30,     # التقييم — بشتري رخيص ولا غالي؟
    "quality": 30,   # الجودة — الشركة بتحقق عائد كويس على فلوسها؟
    "growth": 25,    # النمو — بتكبر ولا بتتقلّص؟
    "safety": 15,    # الأمان — مديونة أوي؟
}

# أقل عدد فئات لازم يكون عند السهم داتا فيها عشان يتحسبله درجة.
# السهم اللي أقل من كده بيتشال — مش لأنه وحش، لكن لأن مفيش أساس نحكم بيه.
MIN_CATEGORIES = 3

# مقاييس إضافية بتدي نقط زيادة بس عمرها ما بتخصم،
# لأن تغطيتها ناقصة (التوزيعات 44%، توصيات المحللين 15%)
# ولو خصمنا بيها هنعاقب أسهم كويسة لمجرد إن مفيش عنها داتا.
BONUS_MAX = 10


# ---------------------------------------------------------------- helpers

def num(row, key):
    """قراءة رقم من الصف، None لو فاضي أو مش رقم."""
    raw = (row.get(key) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def percentile_ranks(values_by_symbol, higher_is_better=True):
    """
    بيحوّل قيم خام لدرجات من 0 لـ100 حسب ترتيب السهم بين اللي عندهم داتا.
    الأسهم اللي مالهاش قيمة بتتشال خالص من الحسبة (مش بتاخد صفر).
    """
    present = {s: v for s, v in values_by_symbol.items() if v is not None}
    if len(present) < 2:
        return {}
    ordered = sorted(present.items(), key=lambda kv: kv[1], reverse=not higher_is_better)
    last = len(ordered) - 1
    return {symbol: round(i / last * 100, 1) for i, (symbol, _) in enumerate(ordered)}


def average(scores):
    """متوسط الدرجات المتاحة. None لو مفيش ولا واحدة."""
    available = [s for s in scores if s is not None]
    return sum(available) / len(available) if available else None


# ---------------------------------------------------------------- filters

# فوق كده النمو بيكون جاي من قاعدة قريبة من الصفر أو من إعادة هيكلة،
# مش أداء حقيقي — ورقم زي 23,768% بيكسّر أي ترتيب نسبي لوحده.
MAX_CREDIBLE_GROWTH = 500


def sanity_problems(row):
    """
    بيمسك الصفوف اللي أرقامها بتناقض بعضها.
    السبب: سهم بأرقام غلط بيتصدّر الترتيب — مش بيقع لتحت —
    لأن الغلط بيخليه يبان رخيص ومربح في نفس الوقت.
    """
    problems = []
    market_cap = num(row, "marketCap")
    net_income = num(row, "netIncome")
    dollar_volume = num(row, "dollarVolume")
    pe = num(row, "peRatio")
    pb = num(row, "pbRatio")

    # شركة أرباحها أكبر من قيمتها السوقية كلها
    if market_cap and net_income and net_income > market_cap:
        problems.append("أرباح أكبر من القيمة السوقية")

    # بيتداول عليها في يوم أكتر من قيمة الشركة كلها
    if market_cap and dollar_volume and dollar_volume > market_cap:
        problems.append("تداول يومي أكبر من القيمة السوقية")

    # P/E أقل من 1 يعني السهم بيرجّع تمنه في سنة — مش واقعي
    if pe is not None and 0 < pe < 1:
        problems.append(f"P/E غير واقعي ({pe})")

    # P/B شبه صفر يعني السعر والدفاتر على أساس مختلف
    if pb is not None and 0 < pb < 0.05:
        problems.append(f"P/B غير واقعي ({pb})")

    return problems


def drop_absurd_growth(rows):
    """
    بيفضّي قيم النمو الخيالية بدل ما يشيل السهم كله.
    باقي أرقام السهم ممكن تكون سليمة، فبنحكم عليه بيها.
    """
    dropped = 0
    for row in rows:
        for column in ("revenueGrowth", "netIncomeGrowth", "epsGrowth"):
            value = num(row, column)
            if value is not None and abs(value) > MAX_CREDIBLE_GROWTH:
                row[column] = ""
                dropped += 1
    return dropped


def hard_filters(rows, min_volume, latest_date):
    """
    بيقسم الأسهم لمقبولة ومستبعدة، مع سبب الاستبعاد.
    كل شرط هنا سببه مخاطرة حقيقية مش مجرد رقم وحش.
    """
    kept, rejected = [], []
    for row in rows:
        symbol = row["symbol"]
        reasons = []

        # سهم مش بيتداول = فخ. الأرقام بتاعته ميتة والخروج منه مستحيل.
        if row.get("lastTradeDate") != latest_date:
            reasons.append(f"آخر تداول {row.get('lastTradeDate') or 'غير معروف'}")

        dollar_volume = num(row, "dollarVolume")
        if dollar_volume is None:
            reasons.append("مفيش حجم تداول")
        elif dollar_volume < min_volume:
            reasons.append(f"سيولة ضعيفة ({dollar_volume/1e6:.1f}م جنيه/يوم)")

        # شركة بتخسر: التقييم بالـ P/E ملوش معنى أصلاً
        eps = num(row, "eps")
        if eps is not None and eps <= 0:
            reasons.append("بتخسر (EPS سالب)")

        # حقوق ملكية سالبة = الالتزامات أكبر من الأصول
        pb = num(row, "pbRatio")
        if pb is not None and pb < 0:
            reasons.append("حقوق ملكية سالبة")

        # مديونية فوق 3 أضعاف حقوق الملكية — خطر مالي عالي
        de = num(row, "debtEquity")
        if de is not None and de > 3:
            reasons.append(f"مديونية عالية (D/E {de:.1f})")

        reasons += sanity_problems(row)

        if reasons:
            rejected.append((symbol, row.get("name"), reasons))
        else:
            kept.append(row)
    return kept, rejected


# ---------------------------------------------------------------- scoring

# كل مقياس: (اسم العمود, هل الأعلى أحسن؟)
METRICS = {
    "value": [
        ("earningsYield", True),    # عائد الأرباح — مقلوب الـ P/E، بيتعامل مع الأرقام الشاذة أحسن
        ("pbRatio", False),
        ("psRatio", False),
        ("pegRatio", False),        # النمو مقابل السعر
    ],
    "quality": [
        ("roe", True),
        ("roa", True),
        ("netMargin", True),
    ],
    "growth": [
        ("revenueGrowth", True),
        ("netIncomeGrowth", True),
        ("epsGrowth", True),
    ],
    "safety": [
        ("debtEquity", False),
        ("beta", False),            # تذبذب أقل من السوق
    ],
}

BONUS_METRICS = [
    ("dividendYield", True),
    ("upsidePct", True),
]


def score_all(rows):
    """بيحسب درجة كل سهم في كل فئة، وبعدين الدرجة النهائية."""
    # درجات كل مقياس على حدة
    metric_scores = {}
    for metrics in list(METRICS.values()) + [BONUS_METRICS]:
        for column, higher_better in metrics:
            values = {r["symbol"]: num(r, column) for r in rows}
            metric_scores[column] = percentile_ranks(values, higher_better)

    results = []
    for row in rows:
        symbol = row["symbol"]
        categories = {}
        for category, metrics in METRICS.items():
            categories[category] = average(
                [metric_scores[col].get(symbol) for col, _ in metrics]
            )

        # عدد الفئات اللي عندها داتا
        coverage = sum(1 for s in categories.values() if s is not None)

        # لازم 3 فئات على الأقل. من غير الشرط ده، سهم عنده فئة واحدة
        # بياخد درجتها كاملة ويتصدّر القايمة — يعني نقص الداتا يبان تفوق.
        if coverage < MIN_CATEGORIES:
            continue

        # الدرجة الأساسية: متوسط مرجّح للفئات المتاحة فقط.
        # لو فئة ناقصة، بنعيد توزيع وزنها على الباقي بدل ما نحسبها صفر.
        total_weight = sum(WEIGHTS[c] for c, s in categories.items() if s is not None)
        if not total_weight:
            continue
        base = sum(
            s * WEIGHTS[c] for c, s in categories.items() if s is not None
        ) / total_weight

        # النقط الإضافية
        bonus_scores = [metric_scores[col].get(symbol) for col, _ in BONUS_METRICS]
        bonus_avg = average(bonus_scores)
        bonus = (bonus_avg / 100 * BONUS_MAX) if bonus_avg is not None else 0

        results.append({
            **row,
            "score": round(min(base + bonus, 100), 1),
            "scoreValue": _fmt(categories["value"]),
            "scoreQuality": _fmt(categories["quality"]),
            "scoreGrowth": _fmt(categories["growth"]),
            "scoreSafety": _fmt(categories["safety"]),
            "scoreBonus": round(bonus, 1),
            "dataCoverage": f"{coverage}/4",
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def _fmt(value):
    return round(value, 1) if value is not None else None


# ---------------------------------------------------------------- output

SHORTLIST_COLUMNS = [
    "symbol", "name", "industry", "score",
    "scoreValue", "scoreQuality", "scoreGrowth", "scoreSafety", "scoreBonus",
    "dataCoverage", "price", "peRatio", "pbRatio", "roe", "netMargin",
    "revenueGrowth", "netIncomeGrowth", "debtEquity", "dividendYield",
    "dollarVolume", "analystRating", "priceTarget", "upsidePct", "lastTradeDate",
]


def print_table(results, top):
    head = f"{'#':<3}{'الرمز':<8}{'الدرجة':>7}{'تقييم':>7}{'جودة':>7}{'نمو':>7}{'أمان':>7}{'P/E':>8}{'ROE%':>8}{'سيولة/م':>9}"
    print("\n" + head)
    print("-" * len(head))
    for i, r in enumerate(results[:top], 1):
        dv = num(r, "dollarVolume")
        print(
            f"{i:<3}{r['symbol']:<8}{r['score']:>7}"
            f"{_c(r['scoreValue']):>7}{_c(r['scoreQuality']):>7}"
            f"{_c(r['scoreGrowth']):>7}{_c(r['scoreSafety']):>7}"
            f"{_c(num(r,'peRatio')):>8}{_c(num(r,'roe')):>8}"
            f"{(f'{dv/1e6:.0f}' if dv else '-'):>9}"
        )


def _c(v):
    return "-" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v))


def explain(results, symbol):
    """تفصيل درجات سهم واحد."""
    row = next((r for r in results if r["symbol"].upper() == symbol.upper()), None)
    if not row:
        sys.exit(f"❌ {symbol} مش موجود في القايمة المقبولة (يمكن اتستبعد في الفلاتر)")

    print(f"\n{row['symbol']} — {row['name']}")
    print(f"القطاع: {row.get('industry') or '-'}")
    print(f"الدرجة النهائية: {row['score']}/100   (تغطية الداتا {row['dataCoverage']})\n")
    for label, key, weight in [
        ("التقييم", "scoreValue", WEIGHTS["value"]),
        ("الجودة", "scoreQuality", WEIGHTS["quality"]),
        ("النمو", "scoreGrowth", WEIGHTS["growth"]),
        ("الأمان", "scoreSafety", WEIGHTS["safety"]),
    ]:
        print(f"  {label:<8} {_c(row[key]):>6}/100   (وزن {weight}%)")
    print(f"  {'إضافي':<8} {row['scoreBonus']:>6}/{BONUS_MAX}\n")

    print("الأرقام الخام:")
    for label, key, suffix in [
        ("السعر", "price", ""), ("P/E", "peRatio", ""), ("P/B", "pbRatio", ""),
        ("ROE", "roe", "%"), ("هامش الربح", "netMargin", "%"),
        ("نمو الإيرادات", "revenueGrowth", "%"), ("نمو الأرباح", "netIncomeGrowth", "%"),
        ("الدين/الملكية", "debtEquity", ""), ("العائد التوزيعي", "dividendYield", "%"),
    ]:
        v = num(row, key)
        print(f"  {label:<16} {_c(v)}{suffix if v is not None else ''}")


def main():
    p = argparse.ArgumentParser(description="ترتيب أسهم البورصة المصرية")
    p.add_argument("--top", type=int, default=15, help="عدد أسهم الشورت-ليست")
    p.add_argument("--min-volume", type=float, default=MIN_DOLLAR_VOLUME / 1e6,
                   help="أقل قيمة تداول يومية بالمليون جنيه")
    p.add_argument("--explain", help="تفصيل درجات سهم معين")
    p.add_argument("--in", dest="infile", default=IN_FILE)
    p.add_argument("--out", default=OUT_FILE)
    args = p.parse_args()

    try:
        rows = list(csv.DictReader(open(args.infile, encoding="utf-8-sig")))
    except FileNotFoundError:
        sys.exit(f"❌ {args.infile} مش موجود — شغّل egx_fetch.py الأول")

    # أحدث جلسة موجودة في الداتا هي المرجع لكشف الأسهم الواقفة
    dates = [r.get("lastTradeDate") for r in rows if r.get("lastTradeDate")]
    latest = max(dates) if dates else None

    absurd = drop_absurd_growth(rows)
    kept, rejected = hard_filters(rows, args.min_volume * 1e6, latest)
    print(f"📊 {len(rows)} سهم  →  {len(kept)} عدّوا الفلاتر  ({len(rejected)} اتستبعدوا)")
    print(f"   أحدث جلسة في الداتا: {latest}")

    by_reason = defaultdict(int)
    for _, _, reasons in rejected:
        by_reason[reasons[0].split("(")[0].strip()] += 1
    print("\n   أسباب الاستبعاد:")
    for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        print(f"     {count:>3} سهم — {reason}")

    if not kept:
        sys.exit("\n❌ مفيش سهم عدّى الفلاتر — جرّب تقلل --min-volume")

    results = score_all(kept)
    thin = len(kept) - len(results)
    if thin:
        print(f"\n   ⚠️  {thin} سهم اتشالوا كمان: داتا أقل من {MIN_CATEGORIES} فئات")
    if absurd:
        print(f"   ⚠️  {absurd} قيمة نمو اتفضّت: أكبر من {MAX_CREDIBLE_GROWTH}%")

    if args.explain:
        explain(results, args.explain)
        return

    print_table(results, args.top)

    with open(args.out, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=SHORTLIST_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"\n✅ اتكتب الترتيب الكامل ({len(results)} سهم) في {args.out}")
    print("   ⚠️  ده ترتيب تحليلي للفرز مش توصية شراء — الأرقام متأخرة جلسة،")
    print("      وأي قرار تنفيذ لازم يكون بسعر Thndr اللحظي.")


if __name__ == "__main__":
    main()
