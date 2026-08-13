#!/usr/bin/env python3
"""
EGX Equity Research
-------------------
تقرير تحليل أساسي كامل لسهم: تقييم، مقارنة قطاعية، جودة الأعمال،
الميزانية والتدفق النقدي، تقدير السعر العادل، والمخاطر.

بيجاوب على الأسئلة اللي محلل حقيقي بيجاوبها:
  • السعر الحالي عادل ولا مبالغ فيه؟ (مقارنةً بالقطاع والتاريخ)
  • الشركة بتكسب إزاي، مش بتكسب كام؟ (الهوامش وثباتها)
  • النمو حقيقي ولا محاسبي؟ (الإيرادات مقابل التدفق النقدي)
  • تصمد قدام أزمة ولا تختنق بالديون؟
  • بتستثمر فلوسها في إيه؟
  • أكبر المخاطر إيه؟

⚠️ التقييم تقدير مبني على مضاعفات، مش سعر مضمون.
   والقطاع بيتقارن بالوسيط عشان شركة شاذة ماتحرّفش المقارنة.

الاستخدام:
    python3 egx_research.py TMGH
    python3 egx_research.py TMGH --json      # مخرج للداشبورد
    python3 egx_research.py --sector "Land Subdividers"   # مقارنة قطاع
"""

import argparse
import csv
import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from egx_fetch import DUAL_LISTED, http_get, load_node  # noqa: E402

BASE = "https://stockanalysis.com"
CACHE_DIR = ".cache/financials"


# ---------------------------------------------------------------- الجلب

def statement(symbol, path="", quarterly=False):
    """
    بيجيب قائمة مالية. بيرجّع dict من {بند: [قيم بترتيب زمني تنازلي]}
    مع مفتاح datekey للفترات.
    """
    # الأسهم المزدوجة الإدراج قوائمها تحت بورصة تانية — التفاصيل
    # في DUAL_LISTED داخل egx_fetch.py
    exchange = DUAL_LISTED.get(symbol, "EGX")
    url = f"{BASE}/quote/{exchange}/{symbol}/financials/{path}__data.json"
    if quarterly:
        url += "?p=quarterly"
    try:
        payload = http_get(url)
    except RuntimeError:
        return {}
    return (load_node(payload, 2) or {}).get("financialData") or {}


def load_all(symbol):
    """كل القوائم المالية لسهم واحد، بالتوازي."""
    jobs = {
        "income": ("income-statement/", False),
        "income_q": ("income-statement/", True),
        "balance": ("balance-sheet/", False),
        "cash": ("cash-flow-statement/", False),
        "ratios": ("ratios/", False),
        "ratios_q": ("ratios/", True),
    }
    out = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {k: pool.submit(statement, symbol, p, q)
                   for k, (p, q) in jobs.items()}
        for k, f in futures.items():
            out[k] = f.result()
    return out


# ---------------------------------------------------------------- أدوات

def at(data, key, i=0):
    """قيمة بند عند فهرس زمني. 0 = الأحدث."""
    vals = data.get(key)
    if not isinstance(vals, list) or i >= len(vals):
        return None
    return vals[i]


def pct(new, old):
    """نسبة التغير. None لو القاعدة صفر أو سالبة (النسبة بتبقى بلا معنى)."""
    if new is None or old is None or old <= 0:
        return None
    return (new - old) / old * 100


def fmt(v, digits=2, suffix=""):
    if v is None:
        return "—"
    if abs(v) >= 1e9:
        return f"{v/1e9:,.2f} مليار"
    if abs(v) >= 1e6:
        return f"{v/1e6:,.1f} مليون"
    return f"{v:,.{digits}f}{suffix}"


def pctf(v, digits=1):
    return "—" if v is None else f"{v:+.{digits}f}%"


def median_of(values):
    clean = [v for v in values if v is not None]
    return statistics.median(clean) if clean else None


# ---------------------------------------------------------------- التحليل

def growth_block(fin):
    """
    النمو: سنوي وربع سنوي (مقارنة بنفس الربع السنة اللي فاتت).

    ليه المقارنة بنفس الربع مش بالربع اللي قبله؟ لأن معظم الشركات
    عندها موسمية — مقارنة الشتا بالصيف بتدي رقم مضلل.
    """
    inc, incq = fin["income"], fin["income_q"]

    rev_ttm = at(inc, "revenue", 0)
    rev_prev = at(inc, "revenue", 2)      # آخر سنة كاملة قبل السابقة
    ni_ttm = at(inc, "netIncomeCommon", 0) or at(inc, "netinccmn", 0)

    # آخر ربع مقابل نفس الربع السنة اللي فاتت (فهرس 4 = 4 أرباع قبله)
    q_rev, q_rev_yoy = at(incq, "revenue", 0), at(incq, "revenue", 4)
    q_gp, q_gp_yoy = at(incq, "gp", 0), at(incq, "gp", 4)
    q_ni = at(incq, "netIncomeCommon", 0) or at(incq, "netinccmn", 0)
    q_ni_yoy = at(incq, "netIncomeCommon", 4) or at(incq, "netinccmn", 4)

    # اتجاه هامش الربح الإجمالي على 4 أرباع — بيكشف ضغط التسعير
    margins = []
    for i in range(min(8, len(incq.get("revenue") or []))):
        r, g = at(incq, "revenue", i), at(incq, "gp", i)
        if r and g is not None and r > 0:
            margins.append(round(g / r * 100, 1))

    return {
        "period": at(incq, "datekey", 0),
        "revenueTTM": rev_ttm,
        "revenueGrowth2y": pct(rev_ttm, rev_prev),
        "netIncomeTTM": ni_ttm,
        "qRevenue": q_rev,
        "qRevenueYoY": pct(q_rev, q_rev_yoy),
        "qGrossProfit": q_gp,
        "qGrossProfitYoY": pct(q_gp, q_gp_yoy),
        "qNetIncome": q_ni,
        "qNetIncomeYoY": pct(q_ni, q_ni_yoy),
        "grossMarginTrend": margins,      # الأحدث أولاً
    }


def quality_block(fin):
    """
    جودة الأعمال: الهوامش وثباتها.
    الهامش الثابت العالي = ميزة تنافسية. الهامش المتقلب = سلعة.
    """
    inc = fin["income"]
    rev = at(inc, "revenue", 0)
    out = {}
    for label, key in [("gross", "gp"), ("operating", "opinc"),
                       ("net", "netIncomeCommon")]:
        v = at(inc, key, 0)
        if v is None and key == "netIncomeCommon":
            v = at(inc, "netinccmn", 0)
        out[label + "Margin"] = (v / rev * 100) if (rev and v is not None) else None

    # ثبات الهامش الإجمالي على 5 سنين — الانحراف المعياري
    hist = []
    for i in range(min(6, len(inc.get("revenue") or []))):
        r, g = at(inc, "revenue", i), at(inc, "gp", i)
        if r and g is not None and r > 0:
            hist.append(g / r * 100)
    out["grossMarginHistory"] = [round(h, 1) for h in hist]
    out["grossMarginStdev"] = round(statistics.stdev(hist), 2) if len(hist) > 2 else None
    return out


def balance_block(fin):
    """
    الميزانية: هل تصمد قدام أزمة؟
    النسبة الجارية بتقيس السيولة قصيرة الأجل، وصافي الدين للـEBITDA
    بيقيس كام سنة أرباح محتاجة عشان تسدد ديونها.
    """
    bs, cf, inc = fin["balance"], fin["cash"], fin["income"]

    cash = at(bs, "totalcash", 0)
    # المصدر بيدي حقل `debt` جاهز = إجمالي الدين. لو ناقص بنجمّعه
    # من الجزء الجاري وغير الجاري.
    total_debt = at(bs, "debt", 0)
    if total_debt is None:
        total_debt = (at(bs, "debtc", 0) or 0) + (at(bs, "debtnc", 0) or 0)
    assets_c = at(bs, "assetsc", 0)
    liab_c = at(bs, "currentLiabilities", 0)
    equity = at(bs, "equity", 0) or at(bs, "totalCommonEquity", 0)

    ebitda = at(inc, "ebitda", 0)
    ncfo = at(cf, "ncfo", 0)
    capex = at(cf, "capex", 0)
    fcf = (ncfo + capex) if (ncfo is not None and capex is not None) else None

    net_debt = (total_debt - cash) if cash is not None else None

    return {
        "cash": cash,
        "totalDebt": total_debt or None,
        "netDebt": net_debt,
        "currentRatio": (assets_c / liab_c) if (assets_c and liab_c) else None,
        "debtToEquity": (total_debt / equity) if (equity and equity > 0) else None,
        "netDebtToEbitda": (net_debt / ebitda)
                           if (net_debt is not None and ebitda and ebitda > 0) else None,
        "operatingCashFlow": ncfo,
        "capex": capex,
        "freeCashFlow": fcf,
        # جودة الأرباح: التدفق التشغيلي مقابل صافي الربح.
        # أقل من 1 معناها أرباح دفترية مش بتتحوّل لكاش.
        "cashConversion": (ncfo / at(inc, "netIncomeCommon", 0))
                          if (ncfo and at(inc, "netIncomeCommon", 0)) else None,
    }


def capital_block(fin):
    """
    الشركة بتحط فلوسها فين؟ استثمار في التوسع ولا بتوزع ولا بتسدد ديون؟
    """
    cf = fin["cash"]
    ncfo = at(cf, "ncfo", 0)
    capex = abs(at(cf, "capex", 0) or 0)
    div = abs(at(cf, "dividends", 0) or at(cf, "commonDividends", 0) or 0)
    buyback = abs(at(cf, "repurchaseCommon", 0) or 0)
    debt_net = at(cf, "netDebtIssued", 0)

    share = lambda v: (v / ncfo * 100) if (ncfo and ncfo > 0) else None
    return {
        "capex": capex or None,
        "capexShare": share(capex),
        "dividends": div or None,
        "dividendShare": share(div),
        "buyback": buyback or None,
        "netDebtIssued": debt_net,
    }


# ---------------------------------------------------------------- التقييم

def valuation_block(fin, sector_stats):
    """
    التقييم: المضاعفات الحالية مقابل تاريخ الشركة ومقابل القطاع.

    ثلاث مقارنات لأن كل واحدة بتجاوب على سؤال مختلف:
      • مقابل التاريخ  → السهم غالي بالنسبة لنفسه؟
      • مقابل القطاع   → غالي بالنسبة لمنافسينه؟
      • المستقبلي      → السوق متوقع الأرباح تكبر؟
    """
    r = fin["ratios"]
    pe = at(r, "pe", 0)
    pe_fwd = at(r, "peForward", 0)
    pb = at(r, "pb", 0)
    ps = at(r, "ps", 0)
    ev_ebitda = at(r, "evebitda", 0)
    peg = at(r, "pegRatio", 0)

    # متوسط مكرر الربحية للشركة على 5 سنين
    pe_hist = [v for v in (r.get("pe") or [])[1:6] if v and 0 < v < 100]
    pe_avg = statistics.median(pe_hist) if pe_hist else None

    return {
        "pe": pe, "peForward": pe_fwd, "pb": pb, "ps": ps,
        "evEbitda": ev_ebitda, "peg": peg,
        "peOwnHistory": pe_avg,
        "vsOwnHistory": pct(pe, pe_avg),
        "sectorPE": sector_stats.get("pe"),
        "vsSector": pct(pe, sector_stats.get("pe")),
        "sectorPB": sector_stats.get("pb"),
        "sectorCount": sector_stats.get("count"),
    }


def fair_value(fin, valuation, price):
    """
    تقدير السعر العادل بثلاث طرق مستقلة، وبنعرضهم كنطاق مش رقم واحد.

    ⚠️ ده تقدير مبني على مضاعفات — يعني بيفترض إن السوق هيسعّر
       الشركة زي ما سعّرها تاريخياً أو زي ما بيسعّر منافسينها.
       مش نبوءة، وممكن يكون غلط لو أساسيات الشركة اتغيرت.
    """
    inc = fin["income"]
    eps = at(inc, "epsdil", 0) or at(inc, "eps", 0)
    estimates = []

    if eps and valuation.get("peOwnHistory"):
        estimates.append(("مضاعف الشركة التاريخي",
                          eps * valuation["peOwnHistory"]))
    if eps and valuation.get("sectorPE"):
        estimates.append(("مضاعف القطاع", eps * valuation["sectorPE"]))

    # المستقبلي: لو الأرباح المتوقعة أعلى، السعر العادل أعلى
    if eps and valuation.get("peForward") and valuation.get("pe"):
        implied_eps = eps * (valuation["pe"] / valuation["peForward"])
        if valuation.get("peOwnHistory"):
            estimates.append(("الأرباح المتوقعة × المضاعف التاريخي",
                              implied_eps * valuation["peOwnHistory"]))

    vals = [v for _, v in estimates if v and v > 0]
    if not vals:
        return None

    return {
        "methods": [{"name": n, "value": round(v, 2)} for n, v in estimates if v > 0],
        "low": round(min(vals), 2),
        "high": round(max(vals), 2),
        "mid": round(statistics.median(vals), 2),
        "upside": round((statistics.median(vals) - price) / price * 100, 1)
                  if price else None,
        "eps": eps,
    }


# ---------------------------------------------------------------- القطاع

# المصدر بيقسّم السوق لـ85 تصنيف دقيق أوي — "Real Estate" و
# "Land Subdividers" قطاعين منفصلين مع إنهم نفس النشاط، وطلعت مصطفى
# مصنّفة "Investors" فبتتقارن بشركات استثمار مش بمطوّرين عقاريين.
# التجميع ده بيخلّي المقارنة عادلة.
SECTOR_MAP = [
    ("البنوك", r"commercial bank|state commercial bank|national commercial bank"),
    ("التمويل والوساطة", r"security.*broker|investors, not|investment offices|"
                          r"finance service|personal credit|mortgage banker|"
                          r"management investment|blank check"),
    ("التأمين", r"insurance|fire, marine"),
    ("العقارات والتطوير", r"real estate|land subdivider|operative builder|"
                           r"cemetery|title abstract"),
    ("المقاولات والإنشاءات", r"construction|contractor|engineering service|"
                              r"heavy construction"),
    ("مواد البناء", r"cement|concrete|gypsum|ceramic|glass|brick|"
                     r"structural clay|abrasive"),
    ("الحديد والمعادن", r"steel|iron|metal|aluminum|copper|smelting|foundr|"
                         r"rolling mill|fabricated"),
    ("الأسمدة والكيماويات", r"agricultural chemical|chemical|industrial organic|"
                             r"plastics|paint|adhesive|petroleum refining"),
    ("الأدوية والرعاية الصحية", r"pharmaceutical|medicinal|biological|hospital|"
                                 r"health service|medical|surgical|diagnostic"),
    ("الأغذية والمشروبات", r"food|bakery|dairy|sugar|beverage|grain mill|"
                            r"canned|poultry|meat|agricultur(?!al chemical)|"
                            r"fats and oils|confection"),
    ("الغزل والنسيج والملابس", r"textile|apparel|garment|cotton|knitting|"
                                r"broadwoven|carpet|leather|footwear"),
    ("السياحة والفنادق", r"hotel|motel|resort|amusement|recreation|"
                          r"eating|drinking place|travel"),
    ("النقل والشحن", r"transportation|water transport|trucking|air transport|"
                      r"marine cargo|freight|courier|warehous"),
    ("الاتصالات والتكنولوجيا", r"telephone|communication|computer|data "
                                r"preparation|software|prepackaged|"
                                r"radiotelephone|information retrieval|"
                                r"electronic|semiconductor"),
    ("الطاقة والمرافق", r"electric service|gas |utilit|crude petroleum|"
                         r"oil and gas|natural gas|drilling|energy"),
    ("التعليم", r"educational"),
    ("الورق والتغليف", r"paper|packaging|container|printing|publish|"
                        r"converted paper"),
    ("التجزئة والتوزيع", r"retail|wholesale|department store|grocer|"
                          r"catalog|variety store"),
    ("الصناعات المتنوعة", r"manufactur|machinery|equipment|motor vehicle|"
                           r"household appliance|furniture|rubber|"
                           r"miscellaneous"),
]


# تصحيحات يدوية لشركات المصدر مصنّفها غلط.
# طلعت مصطفى مسجّلة "Investors, not elsewhere classified" لأنها
# شركة قابضة قانونياً، لكن نشاطها الفعلي تطوير عقاري — ولو سبناها
# كده هتتقارن ببنوك استثمار بدل ما تتقارن بمدينة نصر وبالم هيلز.
SECTOR_OVERRIDE = {
    "TMGH": "العقارات والتطوير",
    "ORAS": "المقاولات والإنشاءات",
    "SWDY": "الصناعات المتنوعة",
    "EFIH": "التمويل والوساطة",
    "HRHO": "التمويل والوساطة",
}


def sector_of(industry, symbol=None):
    """
    بيحوّل التصنيف الدقيق لقطاع عريض قابل للمقارنة.
    اللي مالوش مقابل بيرجع باسمه الأصلي عشان ما نضيّعش معلومة.
    """
    import re as _re
    if symbol and symbol in SECTOR_OVERRIDE:
        return SECTOR_OVERRIDE[symbol]
    text = (industry or "").lower()
    for name, pattern in SECTOR_MAP:
        if _re.search(pattern, text):
            return name
    return industry or "غير مصنّف"


def sector_peers(symbol, industry, rows):
    """
    شركات نفس القطاع. بنستخدم الوسيط مش المتوسط عشان شركة
    بمضاعف 300 ماتحرّفش المقارنة كلها.
    """
    mine = sector_of(industry, symbol)
    peers = [r for r in rows
             if sector_of(r.get("industry"), r["symbol"]) == mine
             and r["symbol"] != symbol]
    if not peers:
        return {"count": 0, "name": mine}, []

    def col(key):
        out = []
        for r in peers:
            try:
                v = float(r.get(key) or "")
                if v > 0:
                    out.append(v)
            except ValueError:
                pass
        return out

    return {
        "name": mine,
        "count": len(peers),
        "pe": median_of(col("peRatio")),
        "pb": median_of(col("pbRatio")),
        "roe": median_of(col("roe")),
        "netMargin": median_of(col("netMargin")),
        "revenueGrowth": median_of(col("revenueGrowth")),
    }, peers


# ---------------------------------------------------------------- المخاطر

def risks(fin, val, bal, qual, grow, sector):
    """
    المخاطر بتتولّد من الأرقام نفسها، مش من رأي.
    كل مخاطرة معاها الرقم اللي سببها.
    """
    out = []

    de = bal.get("debtToEquity")
    if de and de > 1.5:
        out.append(("مديونية عالية",
                    f"الدين {de:.1f}× حقوق الملكية — أي ارتفاع في الفائدة "
                    f"بيضغط على الأرباح مباشرة", "عالية"))

    cr = bal.get("currentRatio")
    if cr and cr < 1:
        out.append(("سيولة قصيرة الأجل ضعيفة",
                    f"النسبة الجارية {cr:.2f} — الالتزامات الجارية أكبر من "
                    f"الأصول الجارية", "عالية"))

    nde = bal.get("netDebtToEbitda")
    if nde and nde > 4:
        out.append(("عبء دين ثقيل مقابل الأرباح",
                    f"صافي الدين {nde:.1f}× الأرباح التشغيلية السنوية", "عالية"))

    cc = bal.get("cashConversion")
    if cc is not None and cc < 0.6:
        out.append(("الأرباح مش بتتحوّل لكاش",
                    f"التدفق التشغيلي {cc:.0%} من صافي الربح — أرباح دفترية "
                    f"أكتر من نقدية", "عالية"))

    fcf = bal.get("freeCashFlow")
    if fcf is not None and fcf < 0:
        out.append(("تدفق نقدي حر سالب",
                    f"{fmt(fcf)} — بتصرف أكتر مما بتولّد، فمحتاجة تمويل خارجي",
                    "متوسطة"))

    sd = qual.get("grossMarginStdev")
    if sd and sd > 8:
        out.append(("هوامش متقلبة",
                    f"تذبذب الهامش الإجمالي {sd:.1f} نقطة — أرباحها مش "
                    f"متوقعة وحساسة لأسعار المدخلات", "متوسطة"))

    vs = val.get("vsSector")
    if vs and vs > 40:
        out.append(("تقييم أعلى من القطاع",
                    f"مكرر الربحية أعلى من وسيط القطاع بـ{vs:.0f}% — "
                    f"السعر متسعّر فيه نمو لازم يتحقق", "متوسطة"))

    vh = val.get("vsOwnHistory")
    if vh and vh > 50:
        out.append(("تقييم أعلى من تاريخ الشركة",
                    f"المضاعف أعلى من وسيطه على 5 سنين بـ{vh:.0f}%", "متوسطة"))

    qr = grow.get("qRevenueYoY")
    if qr is not None and qr < 0:
        out.append(("إيرادات الربع الأخير بتتقلص",
                    f"{qr:.1f}% مقارنةً بنفس الربع السنة اللي فاتت", "عالية"))

    qg = grow.get("qGrossProfitYoY")
    if qg is not None and qr is not None and qg < qr - 10:
        out.append(("ضغط على الهوامش",
                    f"الربح الإجمالي بينمو {qg:.1f}% مقابل {qr:.1f}% للإيرادات — "
                    f"التكاليف بتزيد أسرع من المبيعات", "متوسطة"))

    if not out:
        out.append(("مفيش مخاطرة صارخة في الأرقام",
                    "الميزانية والهوامش والتقييم كلهم في نطاق معقول — "
                    "ده مابيلغيش مخاطر السوق والقطاع", "منخفضة"))
    return out


# ---------------------------------------------------------------- العرض

def build(symbol, rows_by_symbol):
    row = rows_by_symbol.get(symbol)
    if not row:
        sys.exit(f"❌ {symbol} مش موجود في egx_data.csv")

    fin = load_all(symbol)
    if not fin.get("income"):
        sys.exit(f"❌ مفيش قوائم مالية لـ {symbol}")

    price = float(row.get("price") or 0)
    industry = row.get("industry") or ""
    sector, peers = sector_peers(symbol, industry,
                                 list(rows_by_symbol.values()))

    grow = growth_block(fin)
    qual = quality_block(fin)
    bal = balance_block(fin)
    cap = capital_block(fin)
    val = valuation_block(fin, sector)
    fv = fair_value(fin, val, price)
    rk = risks(fin, val, bal, qual, grow, sector)

    return {
        "symbol": symbol,
        "name": row.get("name"),
        "industry": industry,
        "price": price,
        "growth": grow, "quality": qual, "balance": bal,
        "capital": cap, "valuation": val, "fairValue": fv,
        "risks": [{"title": t, "detail": d, "level": lv} for t, d, lv in rk],
        "sector": sector,
        "peers": [
            {"symbol": p["symbol"], "name": p.get("name"),
             "pe": p.get("peRatio"), "pb": p.get("pbRatio"),
             "roe": p.get("roe"), "netMargin": p.get("netMargin"),
             "revenueGrowth": p.get("revenueGrowth"),
             "marketCap": p.get("marketCap")}
            for p in sorted(peers,
                            key=lambda x: -(float(x.get("marketCap") or 0)))[:8]
        ],
    }


def show(r):
    W = 66
    print("\n" + "═" * W)
    print(f"  {r['symbol']} — {r['name']}")
    print(f"  {r['industry']}   ·   السعر {r['price']:,.2f} ج.م")
    print("═" * W)

    g, q, b, c, v = r["growth"], r["quality"], r["balance"], r["capital"], r["valuation"]

    print(f"\n▌ النمو  (آخر ربع: {g['period']})")
    print(f"   الإيرادات {pctf(g['qRevenueYoY']):>9}  سنوياً   ({fmt(g['qRevenue'])})")
    print(f"   الربح الإجمالي {pctf(g['qGrossProfitYoY']):>9}       ({fmt(g['qGrossProfit'])})")
    print(f"   صافي الربح {pctf(g['qNetIncomeYoY']):>9}           ({fmt(g['qNetIncome'])})")
    if g["grossMarginTrend"]:
        print(f"   اتجاه الهامش الإجمالي (أحدث ← أقدم): "
              f"{' · '.join(str(m) for m in g['grossMarginTrend'][:6])}")

    print(f"\n▌ جودة الأعمال — بتكسب إزاي")
    print(f"   الهامش الإجمالي   {fmt(q['grossMargin'],1,'%'):>10}")
    print(f"   هامش التشغيل      {fmt(q['operatingMargin'],1,'%'):>10}")
    print(f"   هامش صافي الربح   {fmt(q['netMargin'],1,'%'):>10}")
    if q["grossMarginStdev"] is not None:
        stability = ("ثابت — ميزة تنافسية" if q["grossMarginStdev"] < 4
                     else "متوسط" if q["grossMarginStdev"] < 8
                     else "متقلب — حساس للتكاليف")
        print(f"   ثبات الهامش       {q['grossMarginStdev']:>10.1f}  ({stability})")

    print(f"\n▌ الميزانية والتدفق النقدي — بتصمد قدام أزمة؟")
    print(f"   النقد              {fmt(b['cash']):>16}")
    print(f"   إجمالي الدين       {fmt(b['totalDebt']):>16}")
    print(f"   صافي الدين         {fmt(b['netDebt']):>16}")
    print(f"   الدين/حقوق الملكية {fmt(b['debtToEquity']):>16}")
    print(f"   النسبة الجارية     {fmt(b['currentRatio']):>16}")
    print(f"   التدفق التشغيلي    {fmt(b['operatingCashFlow']):>16}")
    print(f"   التدفق الحر        {fmt(b['freeCashFlow']):>16}")
    if b["cashConversion"] is not None:
        note = "ممتاز" if b["cashConversion"] > 1 else \
               "معقول" if b["cashConversion"] > 0.6 else "⚠️ ضعيف"
        print(f"   تحويل الأرباح لكاش {b['cashConversion']:>15.0%}  ({note})")

    print(f"\n▌ توزيع رأس المال — بتحط فلوسها فين")
    if c["capexShare"] is not None:
        print(f"   استثمار في التوسع  {c['capexShare']:>15.0f}% من التدفق التشغيلي")
    if c["dividendShare"] is not None:
        print(f"   توزيعات            {c['dividendShare']:>15.0f}%")
    if c["netDebtIssued"] is not None:
        act = "اقتراض" if c["netDebtIssued"] > 0 else "سداد ديون"
        print(f"   صافي الدين         {fmt(abs(c['netDebtIssued'])):>16}  ({act})")

    print(f"\n▌ التقييم — السعر عادل ولا مبالغ فيه؟")
    print(f"   مكرر الربحية الحالي      {fmt(v['pe']):>9}")
    print(f"   المستقبلي                {fmt(v['peForward']):>9}")
    print(f"   وسيط الشركة (5 سنين)     {fmt(v['peOwnHistory']):>9}   "
          f"→ الحالي {pctf(v['vsOwnHistory'])}")
    if v["sectorPE"]:
        print(f"   وسيط القطاع ({v['sectorCount']} شركة)      {fmt(v['sectorPE']):>9}   "
              f"→ الحالي {pctf(v['vsSector'])}")
    print(f"   السعر/القيمة الدفترية    {fmt(v['pb']):>9}"
          + (f"   (القطاع {fmt(v['sectorPB'])})" if v["sectorPB"] else ""))
    print(f"   PEG                      {fmt(v['peg']):>9}")

    fv = r["fairValue"]
    if fv:
        print(f"\n▌ تقدير السعر العادل   (ربحية السهم {fmt(fv['eps'])})")
        for m in fv["methods"]:
            print(f"   {m['name']:<34} {m['value']:>9,.2f}")
        print(f"   {'─'*44}")
        print(f"   النطاق  {fv['low']:,.2f} — {fv['high']:,.2f}"
              f"   ·   الوسيط {fv['mid']:,.2f}"
              f"   ·   مقابل السعر {pctf(fv['upside'],0)}")

    if r["peers"]:
        print(f"\n▌ مقارنة القطاع")
        print(f"   {'الرمز':<8}{'مكرر':>8}{'س/د':>8}{'ROE':>8}"
              f"{'هامش':>8}{'نمو':>9}")
        me = r
        print(f"   {'★'+me['symbol']:<8}{fmt(v['pe'],1):>8}{fmt(v['pb'],1):>8}"
              f"{fmt(float(rows_g[me['symbol']].get('roe') or 0),0):>8}"
              f"{fmt(q['netMargin'],0):>8}{pctf(g['qRevenueYoY'],0):>9}")
        for p in r["peers"]:
            f2 = lambda k, d=1: fmt(float(p[k]), d) if p.get(k) else "—"
            print(f"   {p['symbol']:<8}{f2('pe'):>8}{f2('pb'):>8}"
                  f"{f2('roe',0):>8}{f2('netMargin',0):>8}{f2('revenueGrowth',0):>9}")

    print(f"\n▌ المخاطر الأساسية")
    icon = {"عالية": "🔴", "متوسطة": "🟡", "منخفضة": "🟢"}
    for k in r["risks"]:
        print(f"   {icon.get(k['level'],'•')} {k['title']}")
        print(f"      {k['detail']}")

    print(f"\n{'─'*W}")
    print("⚠️  التقييم تقدير مبني على مضاعفات السوق الحالية والتاريخية،")
    print("    مش سعر مضمون. المضاعفات بتتغير مع تغيّر مزاج السوق")
    print("    وأسعار الفائدة، ومش بتتوقع أحداث مفاجئة.")
    print("    التقرير ده تحليل — القرار وحجم المركز قرارك إنت.")
    print("─" * W)


rows_g = {}


def main():
    global rows_g
    p = argparse.ArgumentParser(description="تقرير تحليل أساسي")
    p.add_argument("symbol", nargs="?", help="رمز السهم")
    p.add_argument("--json", action="store_true")
    p.add_argument("--out")
    args = p.parse_args()

    if not args.symbol:
        sys.exit("❌ اكتب رمز السهم: python3 egx_research.py TMGH")

    try:
        rows = list(csv.DictReader(open("egx_data.csv", encoding="utf-8-sig")))
    except FileNotFoundError:
        sys.exit("❌ egx_data.csv مش موجود — شغّل egx_fetch.py الأول")
    rows_g = {r["symbol"]: r for r in rows}

    report = build(args.symbol.upper(), rows_g)

    if args.json or args.out:
        text = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"✅ اتكتب في {args.out}")
        else:
            print(text)
    else:
        show(report)


if __name__ == "__main__":
    main()
