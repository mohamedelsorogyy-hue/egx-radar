#!/usr/bin/env python3
"""
EGX Technical Analysis
----------------------
بياخد أسهم الشورت-ليست وبيحسب عليها المؤشرات الفنية ومستويات
الدعم والمقاومة ووقف الخسارة.

مصدرين:
  • تاريخ إغلاقات طويل (من 1995) — للمتوسطات المتحركة و RSI و MACD
  • آخر ~6 شهور OHLCV كاملة — للتذبذب (ATR) ومستويات القمم والقيعان

⚠️  الأسعار متأخرة جلسة واحدة. المستويات دي للتخطيط،
    والتنفيذ لازم يكون بسعر Thndr اللحظي.

الاستخدام:
    python3 egx_technical.py                    # أعلى 15 من الشورت-ليست
    python3 egx_technical.py --top 10
    python3 egx_technical.py --symbols COMI,ABUK,QNBE
    python3 egx_technical.py --detail MBSC      # تقرير مفصّل لسهم
"""

import argparse
import csv
import json
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

BASE = "https://stockanalysis.com"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
WORKERS = 5
RETRIES = 3

SHORTLIST = "egx_shortlist.csv"
OUT_FILE = "egx_technical.csv"


# ---------------------------------------------------------------- fetching

def http_get(url):
    last_err = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json",
                    "Referer": f"{BASE}/quote/EGX/COMI/history/",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        # OSError بتغطي انقطاع الشبكة و socket.timeout و URLError و HTTPError.
        # في بايثون 3.9 الـ socket.timeout مش نوع من TimeoutError، فكان
        # بيعدّي من غير ما يتمسك ويوقّع الرن كله بسبب سهم واحد.
        except OSError as err:
            last_err = err
            if attempt < RETRIES - 1:
                time.sleep((2 ** attempt) + random.random())
    raise RuntimeError(str(last_err))


def unflatten(flat, index=0, depth=0):
    if depth > 40:
        return None
    value = flat[index] if isinstance(index, int) and index >= 0 else (
        None if isinstance(index, int) else index
    )
    if isinstance(value, list):
        return [unflatten(flat, i, depth + 1) for i in value]
    if isinstance(value, dict):
        return {k: unflatten(flat, i, depth + 1) for k, i in value.items()}
    return value


def fetch_closes(symbol):
    """تاريخ الإغلاقات المعدّلة الطويل. بيرجّع [(تاريخ, إغلاق), ...] بترتيب زمني."""
    payload = http_get(f"{BASE}/api/symbol/a/EGX-{symbol}/history?type=chart")
    rows = payload.get("data") or []
    return [
        (datetime.fromtimestamp(ts / 1000, timezone.utc).date(), close)
        for ts, close in rows
        if close is not None
    ]


def fetch_ohlcv(symbol, years="10Y"):
    """
    شموع كاملة (افتتاح/أعلى/أدنى/إغلاق/حجم) بترتيب زمني تصاعدي.

    نقطة النهاية دي بتقبل range=5Y/10Y/Max وبترجّع لحد 7400 شمعة
    من 1995. الافتراضي 10 سنين — كفاية للاتجاهات الشهرية
    من غير ما الملف يكبر أوي.
    """
    payload = http_get(f"{BASE}/api/symbol/a/EGX-{symbol}/history?range={years}")
    rows = payload.get("data") or []
    out = []
    for r in rows:
        if r.get("c") is None:
            continue
        out.append({
            "date": r["t"],
            "open": r.get("o"), "high": r.get("h"),
            "low": r.get("l"), "close": r["c"],
            "adj": r.get("a"), "volume": r.get("v"),
        })
    # المصدر بيرجّعها من الأحدث للأقدم
    return list(reversed(out))


# ---------------------------------------------------------------- indicators

def sma(values, period):
    """المتوسط المتحرك البسيط لآخر فترة."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema_series(values, period):
    """المتوسط المتحرك الأسي — كل القيم، عشان الـ MACD محتاج السلسلة كاملة."""
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(values, period=14):
    """
    مؤشر القوة النسبية بطريقة Wilder (متوسط أسي مش بسيط).
    فوق 70 = تشبع شرائي، تحت 30 = تشبع بيعي.
    """
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def macd(values, fast=12, slow=26, signal=9):
    """
    MACD: الفرق بين متوسطين أسيين، وخط إشارة عليه.
    الهيستوجرام موجب = زخم صاعد، سالب = زخم هابط.
    """
    if len(values) < slow + signal:
        return None, None, None
    ema_fast = ema_series(values, fast)
    ema_slow = ema_series(values, slow)
    # نوفّق بداية السلسلتين عشان نطرح نقط متقابلة زمنياً
    offset = len(ema_fast) - len(ema_slow)
    macd_line = [f - s for f, s in zip(ema_fast[offset:], ema_slow)]
    signal_line = ema_series(macd_line, signal)
    if not signal_line:
        return None, None, None
    return (
        round(macd_line[-1], 3),
        round(signal_line[-1], 3),
        round(macd_line[-1] - signal_line[-1], 3),
    )


def atr(bars, period=14):
    """
    المدى الحقيقي المتوسط — مقياس التذبذب اليومي.
    بيستخدم في تحديد وقف الخسارة: وقف قريب أوي بيتضرب من الضوضاء العادية.
    """
    if len(bars) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(bars)):
        high, low = bars[i]["high"], bars[i]["low"]
        prev_close = bars[i - 1]["close"]
        if high is None or low is None or prev_close is None:
            continue
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if len(true_ranges) < period:
        return None
    return sum(true_ranges[-period:]) / period


def find_pivots(closes, window=8):
    """
    نقاط الانعكاس: سعر أعلى (أو أقل) من كل الجلسات في نافذة على جنبيه.
    بنستخدم الإغلاقات لأن التاريخ الطويل متاح بيها بس — والإغلاق
    أهم من الظل اللحظي في تحديد المستويات اللي السوق بيحترمها فعلاً.
    """
    highs, lows = [], []
    for i in range(window, len(closes) - window):
        neighborhood = closes[i - window: i + window + 1]
        if closes[i] == max(neighborhood):
            highs.append((i, closes[i]))
        elif closes[i] == min(neighborhood):
            lows.append((i, closes[i]))
    return highs, lows


def cluster_levels(pivots, tolerance=0.02, total_sessions=1):
    """
    بيجمّع نقاط الانعكاس القريبة من بعضها في مستوى واحد.

    ليه التجميع؟ لأن السوق نادراً ما بيرتد من سعر واحد بالظبط —
    بيرتد من *منطقة*. قمتين عند 100.2 و101.5 مش مستويين، دول واحد.

    كل مستوى بياخد وزن = عدد اللمسات + حداثة آخر لمسة.
    المستوى اللي اتلمس 5 مرات أقوى من اللي اتلمس مرة.
    """
    if not pivots:
        return []

    ordered = sorted(pivots, key=lambda p: p[1])
    clusters = []
    current = [ordered[0]]

    for idx, price in ordered[1:]:
        if abs(price - current[-1][1]) / current[-1][1] <= tolerance:
            current.append((idx, price))
        else:
            clusters.append(current)
            current = [(idx, price)]
    clusters.append(current)

    levels = []
    for group in clusters:
        prices = [p for _, p in group]
        last_touch = max(i for i, _ in group)
        recency = last_touch / total_sessions if total_sessions else 0
        levels.append({
            "price": round(sum(prices) / len(prices), 2),
            "touches": len(group),
            # الوزن: اللمسات أهم، والحداثة بتضيف ترجيح خفيف
            "weight": round(len(group) * (0.6 + 0.4 * recency), 2),
            "lastTouch": last_touch,
        })
    return levels


def swing_levels(closes, window=8, tolerance=0.02, min_touches=1):
    """
    بيرجّع (دعم, مقاومة, كل المستويات).

    الدعم = أقوى مستوى تحت السعر، والمقاومة = أقوى مستوى فوقه.
    "أقوى" مش "أقرب" — مستوى اتلمس 4 مرات وبعيد 6% أهم من
    مستوى اتلمس مرة وبعيد 1%.

    بس بنقيّد البحث في نطاق ±25% حوالين السعر، لأن مستوى بعيد 60%
    ملوش قيمة عملية في قرار دخول أو خروج.
    """
    if len(closes) < window * 2 + 20:
        return None, None, []

    price = closes[-1]
    highs, lows = find_pivots(closes, window)
    total = len(closes)

    # قمم وقيعان في سلة واحدة عن قصد: المقاومة المكسورة بتشتغل دعم
    # والدعم المكسور بيشتغل مقاومة. اللي بيحدد دور المستوى هو موقعه
    # من السعر النهاردة، مش نوع الانعكاس اللي كوّنه.
    levels = cluster_levels(highs + lows, tolerance, total)

    near_lo, near_hi = price * 0.75, price * 1.25

    def pick(above):
        """
        بيختار المستوى العملي مش الأقوى.

        ليه؟ لأن الدعم بيتحوّل لوقف خسارة. مستوى قوي بعيد 25% تحت السعر
        معناه مخاطرة 25% — رقم مالوش قيمة في قرار. الأقرب المعتبر أنفع.

        الترتيب: أقرب مستوى اتلمس مرتين أو أكتر، وإلا أقرب مستوى أياً كان.
        """
        candidates = [
            l for l in levels
            if l["touches"] >= min_touches
            and near_lo <= l["price"] <= near_hi
            and (l["price"] > price * 1.005 if above else l["price"] < price * 0.995)
        ]
        if not candidates:
            return None
        by_distance = sorted(candidates, key=lambda l: abs(l["price"] - price))
        confirmed = [l for l in by_distance if l["touches"] >= 2]
        return confirmed[0] if confirmed else by_distance[0]

    resistance = pick(True)
    support = pick(False)

    # كل المستويات القريبة — بتترسم على الشارت
    all_levels = sorted(
        [l for l in levels if near_lo <= l["price"] <= near_hi],
        key=lambda l: -l["weight"]
    )[:6]

    return (
        support["price"] if support else None,
        resistance["price"] if resistance else None,
        all_levels,
    )


# ---------------------------------------------------------------- analysis

def analyse(symbol):
    """بيحسب كل المؤشرات لسهم واحد."""
    try:
        history = fetch_closes(symbol)
        bars = fetch_ohlcv(symbol)
    except RuntimeError as err:
        print(f"  ⚠️  {symbol}: {err}", file=sys.stderr)
        return None
    if len(history) < 60:
        print(f"  ⚠️  {symbol}: تاريخ قصير ({len(history)} جلسة)", file=sys.stderr)
        return None

    closes = [c for _, c in history]
    price = closes[-1]

    ma20, ma50, ma200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    macd_line, signal_line, histogram = macd(closes)
    atr_value = atr(bars)
    # المستويات بتتحسب من آخر 3 سنين إغلاقات — مش من 6 شهور OHLCV.
    # 6 شهور كانت بتفوّت مستويات مهمة السوق بيحترمها من سنين.
    support, resistance, levels = swing_levels(closes[-750:])

    year = closes[-252:] if len(closes) >= 252 else closes
    high52, low52 = max(year), min(year)

    # حجم التداول: آخر 5 جلسات مقابل متوسط 60 جلسة
    volumes = [b["volume"] for b in bars if b["volume"]]
    volume_ratio = None
    if len(volumes) >= 20:
        recent_avg = sum(volumes[-5:]) / 5
        base_avg = sum(volumes[-60:]) / len(volumes[-60:])
        if base_avg:
            volume_ratio = round(recent_avg / base_avg, 2)

    return {
        "symbol": symbol,
        "date": history[-1][0].isoformat(),
        "price": round(price, 2),
        "ma20": _r(ma20), "ma50": _r(ma50), "ma200": _r(ma200),
        "vsMa50Pct": _pct(price, ma50),
        "vsMa200Pct": _pct(price, ma200),
        "trend": _trend(price, ma50, ma200),
        "rsi": rsi(closes),
        "macdHist": histogram,
        "macdSignal": "صاعد" if histogram and histogram > 0 else ("هابط" if histogram is not None else None),
        "support": _r(support),
        "resistance": _r(resistance),
        "levels": levels,
        "high52": round(high52, 2),
        "low52": round(low52, 2),
        "fromHigh52Pct": _pct(price, high52),
        "atr": _r(atr_value),
        "atrPct": round(atr_value / price * 100, 2) if atr_value and price else None,
        "stopLoss": _stop(price, support, atr_value),
        "volumeRatio": volume_ratio,
        # قراءة الاتجاه على 3 أطر زمنية.
        # الإطار اليومي بيمسك الحركة القريبة، والأسبوعي بيصفّي الضوضاء،
        # والشهري بيدّي الصورة الكبيرة. اتفاقهم إشارة أقوى من أي واحد لوحده.
        "frames": {
            "daily":   timeframe_view(bars, 20, 50),
            "weekly":  timeframe_view(resample(bars, "W"), 10, 30),
            "monthly": timeframe_view(resample(bars, "M"), 6, 12),
        },
        "bars": bars,
        "closes": closes,
    }


def _r(v, digits=2):
    return round(v, digits) if v is not None else None


def _pct(price, reference):
    if not reference:
        return None
    return round((price - reference) / reference * 100, 1)


def resample(bars, period="W"):
    """
    بيحوّل الشموع اليومية لأسبوعية أو شهرية.

    الشمعة الأسبوعية = افتتاح أول يوم، أعلى قمة، أدنى قاع، إغلاق آخر يوم.
    ده اللي بيخلّي "الاتجاه الأسبوعي" معناه حقيقي: بنقيس حركة
    أسابيع كاملة مش أيام.
    """
    groups = {}
    for b in bars:
        y, m, d = (int(x) for x in b["date"].split("-"))
        if period == "W":
            # رقم الأسبوع حسب ISO — الأسبوع بيبدأ الاثنين
            key = datetime(y, m, d).isocalendar()[:2]
        else:
            key = (y, m)
        groups.setdefault(key, []).append(b)

    out = []
    for key in sorted(groups):
        chunk = groups[key]
        highs = [c["high"] for c in chunk if c["high"] is not None]
        lows = [c["low"] for c in chunk if c["low"] is not None]
        vols = [c["volume"] for c in chunk if c["volume"] is not None]
        out.append({
            "date": chunk[-1]["date"],
            "open": chunk[0]["open"],
            "high": max(highs) if highs else None,
            "low": min(lows) if lows else None,
            "close": chunk[-1]["close"],
            "volume": sum(vols) if vols else None,
            "bars": len(chunk),
        })
    return out


def timeframe_view(bars, fast, slow, rsi_period=14):
    """
    قراءة الاتجاه على إطار زمني واحد.

    بنستخدم متوسطات أقصر على الأطر الأطول: 10 و30 شمعة أسبوعية
    = ~شهرين ونص و~7 شهور، وده المعتاد في التحليل الأسبوعي.
    """
    closes = [b["close"] for b in bars if b["close"] is not None]
    if len(closes) < slow + 2:
        return None

    price = closes[-1]
    ma_fast, ma_slow = sma(closes, fast), sma(closes, slow)
    prev = closes[-2] if len(closes) > 1 else None

    return {
        "price": round(price, 2),
        "change": round((price - prev) / prev * 100, 2) if prev else None,
        "maFast": _r(ma_fast),
        "maSlow": _r(ma_slow),
        "trend": _trend(price, ma_fast, ma_slow),
        "rsi": rsi(closes, rsi_period),
        "candles": len(closes),
    }


def _trend(price, ma50, ma200):
    """وصف الاتجاه من موقع السعر بالنسبة للمتوسطين."""
    if ma50 is None or ma200 is None:
        return None
    if price > ma50 > ma200:
        return "صاعد قوي"
    if price > ma50 and price > ma200:
        return "صاعد"
    if price < ma50 < ma200:
        return "هابط قوي"
    if price < ma50 and price < ma200:
        return "هابط"
    return "متذبذب"


def _stop(price, support, atr_value):
    """
    وقف الخسارة المقترح: الأبعد بين (الدعم ناقص هامش) و(السعر ناقص 2×ATR).
    بناخد الأبعد عشان الوقف ما يتضربش من تذبذب يوم عادي.
    """
    candidates = []
    if support:
        candidates.append(support * 0.98)
    if atr_value:
        candidates.append(price - 2 * atr_value)
    if not candidates:
        return None
    stop = min(candidates)
    return round(stop, 2) if stop < price else None


# ---------------------------------------------------------------- output

COLUMNS = [
    "symbol", "date", "price", "trend", "rsi", "macdSignal",
    "ma20", "ma50", "ma200", "vsMa50Pct", "vsMa200Pct",
    "support", "resistance", "stopLoss", "riskPct",
    "high52", "low52", "fromHigh52Pct", "atr", "atrPct", "volumeRatio",
]


def print_table(results):
    head = (f"{'الرمز':<8}{'السعر':>9}{'الاتجاه':>12}{'RSI':>6}{'MACD':>7}"
            f"{'مقابل م50':>10}{'دعم':>9}{'مقاومة':>9}{'وقف':>9}{'مخاطرة':>8}")
    print("\n" + head)
    print("-" * (len(head) + 12))
    for r in results:
        risk = r.get("riskPct")
        print(
            f"{r['symbol']:<8}{r['price']:>9}{(r['trend'] or '-'):>12}"
            f"{_s(r['rsi']):>6}{(r['macdSignal'] or '-'):>7}"
            f"{_s(r['vsMa50Pct'],'%'):>10}{_s(r['support']):>9}"
            f"{_s(r['resistance']):>9}{_s(r['stopLoss']):>9}"
            f"{_s(risk,'%'):>8}"
        )


def _s(v, suffix=""):
    return "-" if v is None else f"{v}{suffix}"


def detail(r):
    """تقرير مفصّل لسهم واحد."""
    print(f"\n{'='*54}\n{r['symbol']}  —  إغلاق {r['date']}\n{'='*54}")
    print(f"السعر: {r['price']}")
    print(f"الاتجاه: {r['trend']}\n")

    print("المتوسطات المتحركة:")
    for label, key, ref in [("20 يوم", "ma20", None), ("50 يوم", "ma50", "vsMa50Pct"),
                            ("200 يوم", "ma200", "vsMa200Pct")]:
        v = r[key]
        extra = f"   (السعر {_s(r[ref],'%')} منه)" if ref and r.get(ref) is not None else ""
        print(f"  {label:<9} {_s(v):>10}{extra}")

    print(f"\nالزخم:")
    rsi_note = ""
    if r["rsi"] is not None:
        if r["rsi"] > 70:
            rsi_note = "  ← تشبع شرائي"
        elif r["rsi"] < 30:
            rsi_note = "  ← تشبع بيعي"
    print(f"  RSI(14)   {_s(r['rsi']):>10}{rsi_note}")
    print(f"  MACD      {_s(r['macdSignal']):>10}  (هيستوجرام {_s(r['macdHist'])})")
    print(f"  الحجم     {_s(r['volumeRatio'],'×'):>10}  (آخر 5 جلسات مقابل متوسط 60)")

    print(f"\nالمستويات:")
    print(f"  مقاومة     {_s(r['resistance']):>10}")
    print(f"  السعر      {r['price']:>10}")
    print(f"  دعم        {_s(r['support']):>10}")
    print(f"  وقف خسارة  {_s(r['stopLoss']):>10}  (مخاطرة {_s(r.get('riskPct'),'%')})")

    print(f"\nالمدى السنوي:")
    print(f"  أعلى 52 أسبوع  {r['high52']:>8}   (السعر {_s(r['fromHigh52Pct'],'%')} منه)")
    print(f"  أدنى 52 أسبوع  {r['low52']:>8}")
    print(f"  تذبذب يومي     {_s(r['atrPct'],'%'):>8}   (ATR {_s(r['atr'])})")


def main():
    p = argparse.ArgumentParser(description="تحليل فني لأسهم البورصة المصرية")
    p.add_argument("--top", type=int, default=15, help="عدد الأسهم من الشورت-ليست")
    p.add_argument("--symbols", help="رموز محددة مفصولة بفاصلة")
    p.add_argument("--detail", help="تقرير مفصّل لسهم واحد")
    p.add_argument("--shortlist", default=SHORTLIST)
    p.add_argument("--out", default=OUT_FILE)
    args = p.parse_args()

    if args.detail:
        symbols = [args.detail.upper()]
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        try:
            rows = list(csv.DictReader(open(args.shortlist, encoding="utf-8-sig")))
        except FileNotFoundError:
            sys.exit(f"❌ {args.shortlist} مش موجود — شغّل egx_score.py الأول")
        symbols = [r["symbol"] for r in rows[:args.top]]

    print(f"⏳ بحلل {len(symbols)} سهم...")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = [r for r in pool.map(analyse, symbols) if r]

    if not results:
        sys.exit("❌ مفيش نتائج")

    # نسبة المخاطرة: بعد وقف الخسارة عن السعر الحالي
    for r in results:
        r["riskPct"] = (
            round((r["price"] - r["stopLoss"]) / r["price"] * 100, 1)
            if r.get("stopLoss") else None
        )

    if args.detail:
        detail(results[0])
        return

    order = {s: i for i, s in enumerate(symbols)}
    results.sort(key=lambda r: order.get(r["symbol"], 999))
    print_table(results)

    with open(args.out, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    print(f"\n✅ اتكتب {len(results)} سهم في {args.out}")
    print("   ⚠️  الأسعار إغلاق الجلسة السابقة — نفّذ بسعر Thndr اللحظي.")


if __name__ == "__main__":
    main()
