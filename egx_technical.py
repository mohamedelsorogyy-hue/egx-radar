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
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
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


def fetch_ohlcv(symbol):
    """آخر ~6 شهور بأعلى وأقل وحجم. بترتيب زمني تصاعدي."""
    payload = http_get(f"{BASE}/quote/EGX/{symbol}/history/__data.json")
    node = payload["nodes"][2]
    rows = ((unflatten(node["data"]) or {}).get("data") or {}).get("data") or []
    out = []
    for r in rows:
        if r.get("c") is None:
            continue
        out.append({
            "date": r["t"],
            "open": r.get("o"), "high": r.get("h"),
            "low": r.get("l"), "close": r["c"], "volume": r.get("v"),
        })
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


def swing_levels(bars, lookback=120, window=5):
    """
    مستويات الدعم والمقاومة من القمم والقيعان المحلية.
    القمة = أعلى سعر في نافذة حواليها، والعكس للقاع.
    بنرجّع أقرب مقاومة فوق السعر وأقرب دعم تحته.
    """
    recent = [b for b in bars[-lookback:] if b["high"] is not None and b["low"] is not None]
    if len(recent) < window * 2 + 1:
        return None, None

    price = recent[-1]["close"]
    highs, lows = [], []
    for i in range(window, len(recent) - window):
        neighborhood = recent[i - window: i + window + 1]
        if recent[i]["high"] == max(b["high"] for b in neighborhood):
            highs.append(recent[i]["high"])
        if recent[i]["low"] == min(b["low"] for b in neighborhood):
            lows.append(recent[i]["low"])

    resistance = min((h for h in highs if h > price), default=None)
    support = max((l for l in lows if l < price), default=None)
    return support, resistance


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
    support, resistance = swing_levels(bars)

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
        "high52": round(high52, 2),
        "low52": round(low52, 2),
        "fromHigh52Pct": _pct(price, high52),
        "atr": _r(atr_value),
        "atrPct": round(atr_value / price * 100, 2) if atr_value and price else None,
        "stopLoss": _stop(price, support, atr_value),
        "volumeRatio": volume_ratio,
        "bars": bars,
        "closes": closes,
    }


def _r(v, digits=2):
    return round(v, digits) if v is not None else None


def _pct(price, reference):
    if not reference:
        return None
    return round((price - reference) / reference * 100, 1)


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
