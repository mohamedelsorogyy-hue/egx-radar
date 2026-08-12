#!/usr/bin/env python3
"""
EGX Support/Resistance Hit-Rate Test
------------------------------------
بيجاوب على سؤال واحد:

    لما السعر يوصل لمستوى مقاومة، بيقف عنده فعلاً كام مرة من 100؟
    ولما يوصل لدعم، بيرتد كام مرة؟

ده بالظبط اللي السمسار بيقوله لما يقول "متوقع يوصل 150 وبعدين ينزل" —
هو بيقرا المقاومة. السكريبت ده بيقيس هل القراءة دي بتصح ولا لأ.

⚠️ منع الاستباق (lookahead): المستويات بتتحسب من **الماضي فقط** عند كل
   نقطة زمنية. لو حسبناها من الداتا كلها هنكون بنغش — هنعرف القمم
   اللي لسه ما حصلتش.

الاستخدام:
    python3 egx_levels_test.py
    python3 egx_levels_test.py --horizon 15
"""

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from egx_backtest import load_prices, load_universe  # noqa: E402
from egx_technical import swing_levels  # noqa: E402

# نافذة التاريخ اللي بنحسب منها المستويات عند كل نقطة
HISTORY = 750
# كل كام جلسة نعيد الحساب. إعادة الحساب كل يوم بطيئة جداً،
# والمستويات مابتتغيرش يومياً أصلاً.
STEP = 5
# السعر يعتبر "وصل" للمستوى لو بقى في حدود النسبة دي منه
TOUCH_BAND = 0.015
# كسر حقيقي = تجاوز المستوى بالنسبة دي (أقل من كده ضوضاء)
BREAK_MARGIN = 0.02


def test_symbol(closes, horizon):
    """
    بيرجّع (نتائج المقاومة, نتائج الدعم).
    كل نتيجة: True = المستوى صمد، False = اتكسر.
    """
    res_out, sup_out = [], []

    for i in range(HISTORY, len(closes) - horizon, STEP):
        past = closes[i - HISTORY:i + 1]          # الماضي فقط — بدون استباق
        price = past[-1]
        support, resistance, _ = swing_levels(past)
        future = closes[i + 1:i + 1 + horizon]
        if not future:
            continue

        # مقاومة: السعر قريب منها من تحت — هيقف ولا هيكسر؟
        if resistance and abs(price - resistance) / resistance <= TOUCH_BAND:
            broke = max(future) > resistance * (1 + BREAK_MARGIN)
            res_out.append(not broke)

        # دعم: السعر قريب منه من فوق — هيرتد ولا هيكسر؟
        if support and abs(price - support) / support <= TOUCH_BAND:
            broke = min(future) < support * (1 - BREAK_MARGIN)
            sup_out.append(not broke)

    return res_out, sup_out


def main():
    p = argparse.ArgumentParser(description="اختبار صمود الدعم والمقاومة")
    p.add_argument("--horizon", type=int, default=15,
                   help="بنتابع النتيجة كام جلسة بعد اللمسة")
    p.add_argument("--limit", type=int, default=80,
                   help="عدد الأسهم (الحساب تقيل)")
    args = p.parse_args()

    symbols = load_universe(args.limit)
    prices = load_prices(symbols)

    res_all, sup_all = [], []
    used = 0
    for idx, sym in enumerate(symbols, 1):
        series = prices.get(sym) or []
        closes = [c for _, c in series if c and c > 0]
        if len(closes) < HISTORY + args.horizon + 50:
            continue
        r, s = test_symbol(closes, args.horizon)
        res_all += r
        sup_all += s
        used += 1
        print(f"\r  {idx}/{len(symbols)}", end="", flush=True)

    print(f"\n\n🔬 اختبار المستويات · {used} سهم · متابعة {args.horizon} جلسة بعد اللمسة")
    print(f"   ⚠️  المستويات محسوبة من الماضي فقط عند كل نقطة (بدون استباق)\n")

    def report(name, vals, meaning):
        if len(vals) < 100:
            print(f"  {name}: عينة صغيرة ({len(vals)})")
            return
        hold = sum(vals) / len(vals) * 100
        print(f"  {name}")
        print(f"     صمد: {hold:.1f}%   ·   اتكسر: {100-hold:.1f}%"
              f"   ·   عدد الحالات: {len(vals):,}")
        print(f"     {meaning}\n")

    report("🔴 المقاومة (السعر جاي من تحت)", res_all,
           "يعني: لما السمسار يقول «هيوصل هنا وبعدين ينزل»")
    report("🟢 الدعم (السعر جاي من فوق)", sup_all,
           "يعني: لما السمسار يقول «هيرتد من هنا»")

    print("📌 إزاي تقرا الرقم:")
    print("   لو الصمود قريب من 50% → المستوى مابيقولش حاجة، زي رمي عملة.")
    print("   لو أعلى من 60% → المستوى فيه معلومة حقيقية تستاهل تتبني عليها.")


if __name__ == "__main__":
    main()
