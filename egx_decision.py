#!/usr/bin/env python3
"""
EGX Trading Decision System
---------------------------
بياخد كل التحاليل (فني + أساسي + تقييم + أخبار) ويطلّع منها
قرار واضح بسبب مكتوب، مع خطط دخول وأهداف ووقف خسارة وحجم مركز.

⚠️ فلسفة النظام: ده **نظام انضباط** مش نظام تنبؤ.
   مابيقولش "السهم هيطلع" — بيقول "دي فرصة بمخاطرة محسوبة ولا لأ".
   الباكتيست بتاعنا أثبت إن توقيت السوق بيخسر، فالقيمة هنا في
   إدارة المخاطر: تعرف تخسر كام قبل ما تدخل، وتتجنب مطاردة السعر.

بيطلّع:
    dashboard/decisions.json      ملخص القرارات لكل الأسهم
    dashboard/decision/{رمز}.json  التقرير الكامل

الاستخدام:
    python3 egx_decision.py QNBE          # تقرير على الشاشة
    python3 egx_decision.py --all         # كل الأسهم
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OUT_DIR = "dashboard/decision"
OUT_SUMMARY = "dashboard/decisions.json"


# ---------------------------------------------------------------- أدوات

def L(ar, en):
    """نص بلغتين — الداشبورد بيختار حسب اللغة المعروضة."""
    return {"ar": ar, "en": en}


def pct_between(a, b):
    """نسبة الفرق من b لـ a."""
    if a is None or b in (None, 0):
        return None
    return (a - b) / b * 100


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- الاتجاه

def trend_block(s, candles):
    """
    درجة الاتجاه من 10، مبنية على شروط قابلة للفحص —
    مش رأي. كل شرط إما متحقق أو لأ.
    """
    price = s.get("price")
    ma20, ma50, ma200 = s.get("ma20"), s.get("ma50"), s.get("ma200")
    daily = (s.get("candles") or {}).get("daily") if s.get("candles") else None
    bars = candles.get("daily") if candles else (daily or [])

    checks = []

    def check(ok, ar, en):
        checks.append({"ok": bool(ok), "label": L(ar, en)})

    check(price and ma20 and price > ma20,
          "السعر فوق متوسط 20 يوم", "Price > MA20")
    check(ma20 and ma50 and ma20 > ma50,
          "متوسط 20 فوق متوسط 50", "MA20 > MA50")
    check(ma50 and ma200 and ma50 > ma200,
          "متوسط 50 فوق متوسط 200 (تقاطع ذهبي)", "MA50 > MA200 (Golden Cross)")
    check(price and ma200 and price > ma200,
          "السعر فوق متوسط 200 يوم", "Price > MA200")

    # قمم وقيعان صاعدة — من آخر 60 شمعة، على نصفين
    hh = ll = None
    if bars and len(bars) >= 40:
        half = len(bars) // 2
        first, second = bars[-40:-half or None], bars[-half:]
        if first and second:
            hh = max(b["h"] for b in second if b.get("h")) > \
                 max(b["h"] for b in first if b.get("h"))
            ll = min(b["l"] for b in second if b.get("l")) > \
                 min(b["l"] for b in first if b.get("l"))
    check(hh, "قمم أعلى من السابقة", "Higher Highs")
    check(ll, "قيعان أعلى من السابقة", "Higher Lows")

    frames = s.get("frames") or {}
    for key, ar, en in [("weekly", "الاتجاه الأسبوعي صاعد", "Weekly uptrend"),
                        ("monthly", "الاتجاه الشهري صاعد", "Monthly uptrend")]:
        f = frames.get(key)
        check(f and "صاعد" in (f.get("trend") or ""), ar, en)

    passed = sum(1 for c in checks if c["ok"])
    score10 = round(passed / len(checks) * 10, 1)

    label = (L("صاعد قوي", "Strong Uptrend") if score10 >= 7.5 else
             L("صاعد", "Uptrend") if score10 >= 5.5 else
             L("متذبذب", "Sideways") if score10 >= 3.5 else
             L("هابط", "Downtrend"))

    return {"score10": score10, "passed": passed, "total": len(checks),
            "checks": checks, "label": label,
            "frames": {k: (frames.get(k) or {}).get("trend") for k in
                       ("daily", "weekly", "monthly")}}


# ---------------------------------------------------------------- المؤشرات

def indicators_block(s):
    """كل مؤشر مع تفسير — الرقم لوحده مايفيدش."""
    out = []
    rsi = s.get("rsi")
    if rsi is not None:
        if rsi >= 70:
            zone, tone = L("تشبع شرائي", "Overbought"), "warn"
            note = L("الزخم قوي، بس السعر بقى معرّض لتصحيح قصير.",
                     "Momentum is strong but a short pullback is more likely here.")
        elif rsi <= 30:
            zone, tone = L("تشبع بيعي", "Oversold"), "warn"
            note = L("ضغط بيع قوي. تاريخياً الارتداد من هنا مش مضمون — "
                     "الباكتيست بتاعنا وجد إن الشراء عند RSI<30 أسوأ من العشوائي.",
                     "Heavy selling. Our backtest found buying RSI<30 performed "
                     "worse than random.")
        elif rsi >= 55:
            zone, tone = L("زخم صاعد", "Bullish momentum"), "good"
            note = L("زخم صحي من غير تشبع.", "Healthy momentum, not stretched.")
        else:
            zone, tone = L("محايد", "Neutral"), "flat"
            note = L("مفيش زخم واضح في أي اتجاه.", "No clear momentum.")
        out.append({"key": "RSI", "value": rsi, "zone": zone,
                    "tone": tone, "note": note})

    macd, hist = s.get("macd"), s.get("macdHist")
    if macd:
        up = macd == "صاعد"
        out.append({
            "key": "MACD", "value": hist,
            "zone": L("صاعد", "Bullish") if up else L("هابط", "Bearish"),
            "tone": "good" if up else "bad",
            "note": (L("MACD فوق خط الإشارة → الزخم الصاعد قائم.",
                       "MACD above signal line → upward momentum intact.")
                     if up else
                     L("MACD تحت خط الإشارة → الزخم ضعيف.",
                       "MACD below signal line → momentum weakening.")),
        })

    atr = s.get("atrPct")
    if atr is not None:
        high = atr > 3
        out.append({
            "key": L("التذبذب اليومي", "Daily Volatility"), "value": atr,
            "zone": L("عالي", "High") if high else L("طبيعي", "Normal"),
            "tone": "warn" if high else "flat",
            "note": (L(f"السهم بيتحرك {atr:.1f}% في اليوم — وقف الخسارة "
                       f"لازم يكون بعيد، وحجم المركز أصغر.",
                       f"Moves {atr:.1f}% daily — needs a wider stop and "
                       f"a smaller position.")),
        })
    return out


# ---------------------------------------------------------------- المستويات

def levels_block(s):
    """الدعم والمقاومة مع المسافة — المسافة هي اللي بتحدد القرار."""
    price = s.get("price")
    res, sup, stop = s.get("resistance"), s.get("support"), s.get("stopLoss")

    return {
        "price": price,
        "resistance": res,
        "support": sup,
        "stopLoss": stop,
        "toResistance": pct_between(res, price) if res else None,
        "toSupport": pct_between(sup, price) if sup else None,
        "high52": s.get("high52"), "low52": s.get("low52"),
        "all": s.get("levels") or [],
    }


def volume_block(s, candles):
    """
    حجم التداول مقابل متوسطه — ده اللي بيفرّق بين اختراق حقيقي
    واختراق كاذب.
    """
    ratio = s.get("volumeRatio")
    if ratio is None:
        return None

    if ratio >= 1.5:
        zone, tone = L("حجم مرتفع", "High Volume"), "good"
    elif ratio >= 0.8:
        zone, tone = L("حجم طبيعي", "Normal Volume"), "flat"
    else:
        zone, tone = L("حجم ضعيف", "Low Volume"), "warn"

    # تأكيد الاختراق: السعر فوق المقاومة + حجم قوي
    res = s.get("resistance")
    price = s.get("price")
    breakout = None
    if res and price:
        above = price > res
        if above and ratio >= 1.3:
            breakout = {"state": "confirmed", "tone": "good",
                        "label": L("اختراق مؤكد", "Confirmed Breakout"),
                        "note": L("السعر فوق المقاومة والحجم أعلى من متوسطه "
                                  "— الاختراق مدعوم.",
                                  "Price above resistance on above-average "
                                  "volume — the move is supported.")}
        elif above:
            breakout = {"state": "weak", "tone": "warn",
                        "label": L("اختراق غير مؤكد", "Unconfirmed Breakout"),
                        "note": L("السعر فوق المقاومة بس الحجم ضعيف — "
                                  "احتمال يكون اختراق كاذب.",
                                  "Above resistance but volume is weak — "
                                  "could be a fake breakout.")}

    return {"ratio": ratio, "zone": zone, "tone": tone, "breakout": breakout}


# ---------------------------------------------------------------- الدرجات

def scores_block(s, research, trend, levels, volume):
    """
    5 درجات مستقلة. كل واحدة بتجاوب على سؤال مختلف —
    وده بيخلّي "السهم كويس" و"التوقيت كويس" حاجتين منفصلتين.
    """
    # فني: الاتجاه + الزخم
    tech = trend["score10"] * 8
    rsi = s.get("rsi")
    if rsi is not None:
        if 45 <= rsi <= 65:
            tech += 12
        elif rsi > 75 or rsi < 25:
            tech -= 8
        else:
            tech += 5
    if s.get("macd") == "صاعد":
        tech += 8
    tech = clamp(tech)

    # أساسي: من تقرير البحث
    fund = None
    if research:
        pts = research.get("points")
        if pts is not None:
            # النطاق العملي للنقط من -8 لـ +10
            fund = clamp((pts + 8) / 18 * 100)

    # تقييم: مقابل القطاع + الفرق عن السعر العادل
    val = None
    if research:
        val = 50
        vs = research.get("vsSector")
        if vs is not None:
            val += clamp(-vs * 1.2, -35, 35)
        up = research.get("upside")
        if up is not None:
            val += clamp(up * 0.6, -25, 25)
        val = clamp(val)

    # المخاطرة: كل ما الوقف أقرب والتذبذب أقل، الدرجة أعلى
    risk = 60
    rp = s.get("riskPct")
    if rp is not None:
        risk = clamp(100 - rp * 5)
    atr = s.get("atrPct")
    if atr is not None:
        risk -= clamp(atr * 4, 0, 25)
    if research and research.get("debtToEquity") is not None:
        de = research["debtToEquity"]
        risk -= clamp((de - 0.5) * 20, 0, 25)
    risk = clamp(risk)

    # توقيت الدخول: أهم درجة — السهم ممكن يكون ممتاز والتوقيت وحش
    timing = 50
    tr = levels.get("toResistance")
    ts = levels.get("toSupport")
    if tr is not None:
        # قريب من المقاومة = مطاردة سعر
        timing += clamp((tr - 3) * 4, -30, 25)
    if ts is not None:
        # قريب من الدعم = دخول أحسن (المخاطرة محدودة)
        timing += clamp((-ts - 8) * -2.5, -20, 20)
    if rsi is not None:
        if rsi > 70:
            timing -= 20
        elif rsi < 40:
            timing += 10
    if volume and volume.get("breakout"):
        timing += 15 if volume["breakout"]["state"] == "confirmed" else -10
    timing = clamp(timing)

    parts = {"technical": round(tech), "fundamental": round(fund) if fund else None,
             "valuation": round(val) if val else None,
             "risk": round(risk), "timing": round(timing)}

    # الإجمالي: متوسط مرجّح للمتاح
    weights = {"technical": 0.22, "fundamental": 0.28, "valuation": 0.2,
               "risk": 0.15, "timing": 0.15}
    total_w = sum(w for k, w in weights.items() if parts[k] is not None)
    overall = round(sum(parts[k] * w for k, w in weights.items()
                        if parts[k] is not None) / total_w) if total_w else None

    parts["overall"] = overall
    return parts


# ---------------------------------------------------------------- الدخول

def entry_block(s, levels, volume, trend):
    """
    خطط الدخول: كل خطة بشروطها، والشروط بتتفحص واحد واحد.
    كده تعرف بالظبط إيه اللي ناقص عشان الصفقة تبقى جاهزة.
    """
    price = levels["price"]
    sup, res, stop = levels["support"], levels["resistance"], levels["stopLoss"]
    rsi = s.get("rsi")
    plans = []

    # خطة 1: الشراء عند الارتداد من الدعم
    if sup and price:
        zone_lo, zone_hi = round(sup, 2), round(sup * 1.03, 2)
        conds = [
            {"ok": price <= sup * 1.04,
             "label": L(f"السعر داخل منطقة الدعم ({zone_lo}–{zone_hi})",
                        f"Price in support zone ({zone_lo}–{zone_hi})")},
            {"ok": rsi is not None and rsi < 60,
             "label": L("RSI مش في منطقة تشبع", "RSI not overbought")},
            {"ok": trend["score10"] >= 5.5,
             "label": L("الاتجاه العام لسه صاعد", "Overall trend still up")},
            {"ok": bool(volume and volume["ratio"] and volume["ratio"] >= 0.8),
             "label": L("حجم التداول طبيعي أو أعلى", "Volume normal or better")},
        ]
        plans.append({
            "name": L("الدخول عند الارتداد", "Pullback Entry"),
            "type": "pullback",
            "zone": [zone_lo, zone_hi],
            "conditions": conds,
            "ready": all(c["ok"] for c in conds),
        })

    # خطة 2: الدخول بعد اختراق المقاومة
    if res:
        conds = [
            {"ok": bool(price and price > res),
             "label": L(f"إغلاق فوق {round(res,2)}", f"Close above {round(res,2)}")},
            {"ok": bool(volume and volume["ratio"] and volume["ratio"] >= 1.3),
             "label": L("حجم أعلى من المتوسط بـ30%", "Volume 30% above average")},
            {"ok": s.get("macd") == "صاعد",
             "label": L("MACD صاعد", "MACD bullish")},
            {"ok": trend["score10"] >= 6,
             "label": L("الاتجاه صاعد", "Trend is up")},
        ]
        plans.append({
            "name": L("الدخول بعد الاختراق", "Breakout Entry"),
            "type": "breakout",
            "zone": [round(res, 2), round(res * 1.02, 2)],
            "conditions": conds,
            "ready": all(c["ok"] for c in conds),
        })

    # خطة 3: تحذير الكسر لأسفل
    if sup:
        conds = [
            {"ok": bool(price and price < sup),
             "label": L(f"إغلاق تحت {round(sup,2)}", f"Close below {round(sup,2)}")},
            {"ok": bool(volume and volume["ratio"] and volume["ratio"] >= 1.3),
             "label": L("حجم بيع مرتفع", "High selling volume")},
        ]
        plans.append({
            "name": L("كسر الدعم — تحذير", "Breakdown — Warning"),
            "type": "breakdown",
            "zone": None,
            "conditions": conds,
            "ready": all(c["ok"] for c in conds),
            "warning": True,
        })

    return plans


def targets_block(s, levels, entry_price):
    """
    الأهداف من المستويات الحقيقية مش أرقام مخترعة.
    TP1 = أقرب مقاومة · TP2 = المستوى اللي بعده · TP3 = قمة 52 أسبوع
    """
    price = entry_price or levels["price"]
    if not price:
        return []

    above = {round(l["price"], 2) for l in (levels.get("all") or [])
             if l["price"] > price * 1.01}
    if levels.get("high52") and levels["high52"] > price * 1.01:
        above.add(round(levels["high52"], 2))

    # لو المستويات الحقيقية أقل من 3، بنكمّل بمضاعفات المخاطرة.
    # هدف عند 2× و3× المخاطرة مش رقم مخترع — ده المعيار اللي بيخلي
    # الصفقة تستاهل أصلاً، وبيدي المستخدم سقف واقعي يقيس عليه.
    stop = levels.get("stopLoss")
    if stop and price > stop and len(above) < 3:
        risk = price - stop
        for mult in (2, 3):
            target = round(price + risk * mult, 2)
            if all(abs(target - a) / a > 0.015 for a in above):
                above.add(target)

    above = sorted(above)[:3]

    out = []
    for i, lvl in enumerate(above, 1):
        out.append({
            "name": f"TP{i}",
            "price": lvl,
            "gain": round((lvl - price) / price * 100, 1),
        })
    return out


def risk_reward(entry, stop, targets):
    """نسبة العائد للمخاطرة على أول هدف — ودي اللي بتحدد لو الصفقة تستاهل."""
    if not (entry and stop and targets) or entry <= stop:
        return None
    risk = entry - stop
    reward = targets[0]["price"] - entry
    if risk <= 0:
        return None
    ratio = reward / risk
    return {
        "risk": round(risk, 2),
        "reward": round(reward, 2),
        "ratio": round(ratio, 2),
        "verdict": (L("ممتازة", "Excellent") if ratio >= 3 else
                    L("جيدة", "Good") if ratio >= 2 else
                    L("مقبولة", "Acceptable") if ratio >= 1.5 else
                    L("غير جذابة", "Unattractive")),
        "tone": "good" if ratio >= 2 else "warn" if ratio >= 1.5 else "bad",
    }


# ---------------------------------------------------------------- القرار

def decide(s, scores, levels, trend, volume, plans, rr):
    """
    القرار النهائي — بقواعد صريحة، وكل قرار معاه سببه.

    ⚠️ ده مش تنبؤ بالسعر. ده حكم على **جودة الفرصة دلوقتي**:
       سهم ممتاز عند مقاومة بمخاطرة عالية = انتظار، مش رفض للسهم.
    """
    reasons_ar, reasons_en = [], []
    action = "WAIT"

    timing = scores["timing"]
    overall = scores["overall"] or 0
    tr = levels.get("toResistance")
    rsi = s.get("rsi")
    rp = s.get("riskPct")

    ready_plan = next((p for p in plans
                       if p["ready"] and not p.get("warning")), None)
    breakdown = next((p for p in plans
                      if p.get("warning") and p["ready"]), None)

    if breakdown:
        action = "AVOID"
        reasons_ar.append("السعر كسر الدعم بحجم مرتفع — الاتجاه بيضعف")
        reasons_en.append("Support broken on high volume — trend weakening")
    elif overall < 45:
        action = "AVOID"
        reasons_ar.append("الأرقام الأساسية والفنية ضعيفة")
        reasons_en.append("Weak fundamentals and technicals")
    elif ready_plan and timing >= 55 and (not rr or rr["ratio"] >= 1.5):
        action = "BUY"
        reasons_ar.append(f"شروط {ready_plan['name']['ar']} متحققة")
        reasons_en.append(f"{ready_plan['name']['en']} conditions met")
    else:
        action = "WAIT"

    # الأسباب التفصيلية — دي اللي بتخلي القرار مفهوم
    if trend["score10"] >= 7.5:
        reasons_ar.append("الاتجاه صاعد بقوة على المدد الثلاثة")
        reasons_en.append("Strong uptrend across all timeframes")
    elif trend["score10"] < 4:
        reasons_ar.append("الاتجاه ضعيف أو هابط")
        reasons_en.append("Trend is weak or down")

    if tr is not None and tr < 3:
        reasons_ar.append(f"السعر على بعد {tr:.1f}% من المقاومة — "
                          f"مطاردة السعر هنا مخاطرتها أعلى")
        reasons_en.append(f"Only {tr:.1f}% below resistance — chasing here "
                          f"carries higher risk")
    if rsi is not None and rsi > 70:
        reasons_ar.append(f"RSI عند {rsi:.0f} (تشبع شرائي)")
        reasons_en.append(f"RSI at {rsi:.0f} (overbought)")
    if rp is not None and rp > 12:
        reasons_ar.append(f"وقف الخسارة بعيد ({rp:.1f}%) — مخاطرة كبيرة للسهم")
        reasons_en.append(f"Stop is {rp:.1f}% away — large risk per share")
    if rr and rr["ratio"] < 1.5:
        reasons_ar.append(f"العائد مقابل المخاطرة {rr['ratio']}:1 — مش مجزي")
        reasons_en.append(f"Risk/reward {rr['ratio']}:1 — not compelling")
    if volume and volume.get("breakout"):
        b = volume["breakout"]
        reasons_ar.append(b["label"]["ar"])
        reasons_en.append(b["label"]["en"])

    label = {
        "BUY": L("فرصة دخول", "Entry Opportunity"),
        "WAIT": L("انتظار فرصة أفضل", "Wait for Better Entry"),
        "AVOID": L("تجنّب حالياً", "Avoid For Now"),
    }[action]

    return {
        "action": action,
        "label": label,
        "tone": {"BUY": "good", "WAIT": "warn", "AVOID": "bad"}[action],
        "reasons": L(reasons_ar, reasons_en),
        "readyPlan": ready_plan["type"] if ready_plan else None,
    }


def summary_text(s, trend, levels, scores, decision, rr):
    """ملخص بلغة طبيعية — بيربط الأرقام ببعضها في جملة مفهومة."""
    name = s.get("symbol")
    tr, ts = levels.get("toResistance"), levels.get("toSupport")
    rsi = s.get("rsi")

    ar = [f"الاتجاه {trend['label']['ar']} "
          f"({trend['passed']} من {trend['total']} شروط متحققة)."]
    en = [f"Trend is {trend['label']['en'].lower()} "
          f"({trend['passed']}/{trend['total']} criteria met)."]

    if rsi is not None:
        ar.append(f"RSI عند {rsi:.0f}.")
        en.append(f"RSI at {rsi:.0f}.")
    if tr is not None:
        ar.append(f"السعر يبعد {abs(tr):.1f}% عن المقاومة")
        en.append(f"Price is {abs(tr):.1f}% from resistance")
    if ts is not None:
        ar.append(f"و{abs(ts):.1f}% فوق الدعم.")
        en.append(f"and {abs(ts):.1f}% above support.")
    if rr:
        ar.append(f"العائد مقابل المخاطرة {rr['ratio']}:1 ({rr['verdict']['ar']}).")
        en.append(f"Risk/reward is {rr['ratio']}:1 ({rr['verdict']['en'].lower()}).")

    if decision["action"] == "WAIT":
        ar.append("السيناريو الأفضل: استنى ارتداد للدعم أو اختراق مؤكد بحجم مرتفع.")
        en.append("Best scenario: wait for a pullback to support or a "
                  "volume-confirmed breakout.")
    elif decision["action"] == "BUY":
        ar.append("الشروط متحققة — لو دخلت، حط وقف الخسارة فوراً.")
        en.append("Conditions are met — if you enter, set the stop immediately.")
    else:
        ar.append("الأرقام مش داعمة للدخول دلوقتي.")
        en.append("The numbers don't support entry right now.")

    return L(" ".join(ar), " ".join(en))


# ---------------------------------------------------------------- التجميع

def build(symbol, stocks, research_map, candles_dir):
    s = stocks.get(symbol)
    if not s:
        return None

    candles = {}
    path = os.path.join(candles_dir, f"{symbol}.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                candles = json.load(fh)
        except ValueError:
            candles = {}

    research = research_map.get(symbol)
    trend = trend_block(s, candles)
    indicators = indicators_block(s)
    levels = levels_block(s)
    volume = volume_block(s, candles)
    scores = scores_block(s, research, trend, levels, volume)
    plans = entry_block(s, levels, volume, trend)

    # سعر الدخول المرجعي: منتصف أول خطة جاهزة، وإلا السعر الحالي
    ready = next((p for p in plans if p["ready"] and not p.get("warning")), None)
    entry_price = (sum(ready["zone"]) / 2) if (ready and ready["zone"]) else s.get("price")
    # التقريب هنا مش في العرض بس — عشان لما المستخدم يحسب
    # (الهدف − الدخول) ÷ (الدخول − الوقف) بإيده، يطلع نفس الرقم
    # اللي إحنا معروضينه بالظبط. الفرق كان بيوصل 7%.
    if entry_price is not None:
        entry_price = round(entry_price, 2)

    targets = targets_block(s, levels, entry_price)
    rr = risk_reward(entry_price, s.get("stopLoss"), targets)
    decision = decide(s, scores, levels, trend, volume, plans, rr)

    return {
        "symbol": symbol,
        "name": s.get("name"),
        "sector": (research or {}).get("sector"),
        "price": s.get("price"),
        "tradeDate": s.get("sessionDate"),
        "decision": decision,
        "summary": summary_text(s, trend, levels, scores, decision, rr),
        "scores": scores,
        "trend": trend,
        "indicators": indicators,
        "levels": levels,
        "volume": volume,
        "plans": plans,
        "entryPrice": round(entry_price, 2) if entry_price else None,
        "targets": targets,
        "riskReward": rr,
        "fundamental": {
            "pe": (research or {}).get("pe"),
            "peForward": (research or {}).get("peForward"),
            "roe": s.get("roe"),
            "netMargin": s.get("netMargin"),
            "revenueGrowth": (research or {}).get("qRevenueYoY"),
            "netIncomeGrowth": (research or {}).get("qNetIncomeYoY"),
            "debtToEquity": (research or {}).get("debtToEquity"),
            "fairValue": (research or {}).get("fairValueMid"),
            "upside": (research or {}).get("upside"),
            "vsSector": (research or {}).get("vsSector"),
            "sectorVerdict": sector_verdict((research or {}).get("vsSector")),
            "verdict": (research or {}).get("verdict"),
        },
    }


def sector_verdict(vs):
    if vs is None:
        return None
    if vs < -20:
        return {"tone": "good", **L("أرخص من القطاع", "Attractive vs sector")}
    if vs > 25:
        return {"tone": "bad", **L("أغلى من القطاع", "Expensive vs sector")}
    return {"tone": "flat", **L("في نطاق القطاع", "Fairly valued vs sector")}


# ---------------------------------------------------------------- العرض

def show(r):
    d, sc, t, lv = r["decision"], r["scores"], r["trend"], r["levels"]
    icon = {"BUY": "🟢", "WAIT": "🟡", "AVOID": "🔴"}[d["action"]]
    print(f"\n{'='*60}")
    print(f"  {r['symbol']} — {r['name']}")
    print(f"  السعر {r['price']}  ·  جلسة {r['tradeDate']}")
    print(f"{'='*60}")
    print(f"\n{icon}  {d['action']} — {d['label']['ar']}")
    for x in d["reasons"]["ar"]:
        print(f"    • {x}")

    print(f"\n▌ الدرجات")
    for k, ar in [("overall", "الإجمالي"), ("technical", "فني"),
                  ("fundamental", "أساسي"), ("valuation", "تقييم"),
                  ("risk", "مخاطرة"), ("timing", "توقيت الدخول")]:
        v = sc.get(k)
        if v is not None:
            bar = "█" * int(v / 5) + "░" * (20 - int(v / 5))
            print(f"   {ar:<14} {v:>3}/100  {bar}")

    print(f"\n▌ الاتجاه — {t['label']['ar']}  ({t['score10']}/10)")
    for c in t["checks"]:
        print(f"   {'☑' if c['ok'] else '☐'} {c['label']['ar']}")

    print(f"\n▌ المؤشرات")
    for i in r["indicators"]:
        key = i["key"] if isinstance(i["key"], str) else i["key"]["ar"]
        print(f"   {key}: {i['value']}  — {i['zone']['ar']}")
        print(f"      {i['note']['ar']}")

    print(f"\n▌ المستويات")
    print(f"   مقاومة  {lv['resistance']}   ({lv['toResistance']:+.1f}%)"
          if lv["toResistance"] is not None else "   مقاومة  —")
    print(f"   السعر   {lv['price']}")
    print(f"   دعم     {lv['support']}   ({lv['toSupport']:+.1f}%)"
          if lv["toSupport"] is not None else "   دعم     —")
    print(f"   وقف     {lv['stopLoss']}")

    if r["volume"]:
        v = r["volume"]
        print(f"\n▌ الحجم: {v['ratio']}× المتوسط — {v['zone']['ar']}")
        if v.get("breakout"):
            print(f"   {v['breakout']['label']['ar']}: {v['breakout']['note']['ar']}")

    print(f"\n▌ خطط الدخول")
    for p in r["plans"]:
        mark = "🟢 جاهزة" if p["ready"] else "⏳ مش مكتملة"
        zone = f"  [{p['zone'][0]}–{p['zone'][1]}]" if p.get("zone") else ""
        print(f"   {p['name']['ar']}{zone}  {mark}")
        for c in p["conditions"]:
            print(f"      {'☑' if c['ok'] else '☐'} {c['label']['ar']}")

    if r["targets"]:
        print(f"\n▌ الأهداف (من سعر {r['entryPrice']})")
        for tp in r["targets"]:
            print(f"   {tp['name']}  {tp['price']}   ({tp['gain']:+.1f}%)")

    if r["riskReward"]:
        rr = r["riskReward"]
        print(f"\n▌ العائد مقابل المخاطرة")
        print(f"   مخاطرة {rr['risk']} ج · عائد {rr['reward']} ج · "
              f"النسبة 1:{rr['ratio']}  ({rr['verdict']['ar']})")

    print(f"\n▌ الملخص\n   {r['summary']['ar']}")
    print(f"\n{'-'*60}")
    print("⚠️  ده نظام انضباط مش تنبؤ. بيحكم على جودة الفرصة")
    print("    ومخاطرتها — مش على اتجاه السعر. القرار قرارك.")
    print("-" * 60)


def main():
    p = argparse.ArgumentParser(description="نظام قرار التداول")
    p.add_argument("symbol", nargs="?")
    p.add_argument("--all", action="store_true")
    args = p.parse_args()

    with open("dashboard/data.json", encoding="utf-8") as fh:
        data = json.load(fh)
    stocks = {s["symbol"]: s for s in data["stocks"]}

    research_map = {}
    try:
        with open("dashboard/research.json", encoding="utf-8") as fh:
            for r in json.load(fh)["stocks"]:
                research_map[r["symbol"]] = r
    except (FileNotFoundError, ValueError):
        pass

    candles_dir = "dashboard/candles"

    if args.symbol and not args.all:
        r = build(args.symbol.upper(), stocks, research_map, candles_dir)
        if not r:
            sys.exit(f"❌ {args.symbol} مش موجود")
        show(r)
        return

    # الأسهم المستبعدة من الفلترة — بتدخل القايمة بقرار "تجنّب"
    # وسبب الاستبعاد. من غير كده كان الداشبورد بيعرض 139 من 224
    # والباقي بيختفي، والمستخدم يحس إن فيه أسهم ناقصة.
    excluded = {}
    try:
        with open("dashboard/excluded.json", encoding="utf-8") as fh:
            for it in json.load(fh)["items"]:
                excluded[it["symbol"]] = it
    except (FileNotFoundError, ValueError):
        pass

    raw_rows = {}
    try:
        with open("egx_data.csv", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                raw_rows[r["symbol"]] = r
    except FileNotFoundError:
        pass

    os.makedirs(OUT_DIR, exist_ok=True)
    reports, summary = [], []
    for symbol in stocks:
        r = build(symbol, stocks, research_map, candles_dir)
        if not r:
            continue
        with open(os.path.join(OUT_DIR, f"{symbol}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(r, fh, ensure_ascii=False, separators=(",", ":"))
        reports.append(r)
        summary.append({
            "symbol": symbol, "name": r["name"], "sector": r["sector"],
            "price": r["price"],
            "action": r["decision"]["action"],
            "label": r["decision"]["label"],
            "tone": r["decision"]["tone"],
            "reason": {"ar": (r["decision"]["reasons"]["ar"] or [""])[0],
                       "en": (r["decision"]["reasons"]["en"] or [""])[0]},
            "scores": r["scores"],
            "trendScore": r["trend"]["score10"],
            "rsi": stocks[symbol].get("rsi"),
            "toResistance": r["levels"]["toResistance"],
            "toSupport": r["levels"]["toSupport"],
            "riskReward": r["riskReward"]["ratio"] if r["riskReward"] else None,
            "upside": r["fundamental"]["upside"],
        })

    # المستبعدين: قرار "تجنّب" بسبب الفلترة، من غير تحليل فني
    # (مالهومش داتا كفاية أصلاً — وده هو سبب الاستبعاد).
    for symbol, info in excluded.items():
        if symbol in stocks:
            continue
        row = raw_rows.get(symbol, {})
        reasons = info.get("reasons") or []
        price = None
        try:
            price = float(row.get("price") or 0) or None
        except ValueError:
            pass
        summary.append({
            "symbol": symbol,
            "name": info.get("name") or row.get("name") or symbol,
            "sector": None,
            "price": price,
            "action": "AVOID",
            "label": L("مستبعد من الفلترة", "Filtered Out"),
            "tone": "bad",
            "reason": L(reasons[0] if reasons else "بيانات غير كافية",
                        reasons[0] if reasons else "Insufficient data"),
            "filteredOut": True,
            "allReasons": reasons,
            "scores": {"overall": None, "technical": None, "fundamental": None,
                       "valuation": None, "risk": None, "timing": None},
            "trendScore": None, "rsi": None,
            "toResistance": None, "toSupport": None,
            "riskReward": None, "upside": None,
        })

    order = {"BUY": 0, "WAIT": 1, "AVOID": 2}
    summary.sort(key=lambda x: (order[x["action"]],
                                1 if x.get("filteredOut") else 0,
                                -(x["scores"]["overall"] or 0)))

    with open(OUT_SUMMARY, "w", encoding="utf-8") as fh:
        json.dump({
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tradeDate": data.get("tradeDate"),
            "count": len(summary),
            "stocks": summary,
        }, fh, ensure_ascii=False, separators=(",", ":"))

    counts = {a: sum(1 for x in summary if x["action"] == a)
              for a in ("BUY", "WAIT", "AVOID")}
    filtered = sum(1 for x in summary if x.get("filteredOut"))
    print(f"✅ {len(summary)} سهم · "
          f"🟢 {counts['BUY']} دخول · 🟡 {counts['WAIT']} انتظار · "
          f"🔴 {counts['AVOID']} تجنّب "
          f"(منهم {filtered} مستبعد من الفلترة)")


if __name__ == "__main__":
    main()
