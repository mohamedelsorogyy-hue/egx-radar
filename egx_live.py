#!/usr/bin/env python3
"""
EGX Live Prices — TradingView
-----------------------------
أسعار متأخرة 15 دقيقة بس (بدل جلسة كاملة)، مع **سعر افتتاح حقيقي**.

ليه مصدر تاني؟
  • stockanalysis بينزّل إغلاق الجلسة بعد ساعات — يعني طول اليوم
    إنت شايف أرقام امبارح
  • وحقل "الافتتاح" عنده هو إغلاق اليوم السابق، فـ22% من الشموع
    كان الأعلى فيها أقل من الافتتاح (مستحيل فيزيائياً)

TradingView بيحل الاتنين: تأخير 15 دقيقة، وافتتاح حقيقي بصفر تناقض.
بنسيب القوائم المالية والتاريخ الطويل على stockanalysis لأن
TradingView مابيديهمش مجاناً.

⚠️ التأخير 15 دقيقة مش صفر. للتنفيذ استخدم سعر شركة السمسرة.

الاستخدام:
    python3 egx_live.py                 # يحدّث dashboard/live.json
    python3 egx_live.py --show COMI     # سعر سهم على الشاشة
"""

import argparse
import csv
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

SCANNER = "https://scanner.tradingview.com/egypt/scan"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

OUT = "dashboard/live.json"
RETRIES = 4

# الأعمدة اللي بنطلبها من الماسح. الترتيب مهم — الرد بيرجع
# مصفوفة قيم بنفس ترتيب الأعمدة دي.
COLUMNS = [
    "close", "change", "change_abs", "open", "high", "low",
    "volume", "update_mode", "market_cap_basic",
]

# البورصة المصرية: الأحد–الخميس، 10:00 لـ 14:30 بتوقيت القاهرة
MARKET_OPEN_H, MARKET_CLOSE_H, MARKET_CLOSE_M = 10, 14, 30


def http_post(url, payload):
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers={"User-Agent": UA, "Content-Type": "application/json",
                         "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=40) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except OSError as err:
            last = err
            if attempt < RETRIES - 1:
                time.sleep((2 ** attempt) + random.random())
    raise RuntimeError(str(last))


def market_state():
    """
    حالة السوق بتوقيت القاهرة (UTC+3 صيفاً).
    بترجع (مفتوح؟, نص الحالة).
    """
    cairo = datetime.now(timezone.utc) + timedelta(hours=3)
    # الجمعة=4، السبت=5 في تقويم بايثون
    if cairo.weekday() in (4, 5):
        return False, {"ar": "السوق مقفول — إجازة أسبوعية",
                       "en": "Market closed — weekend"}
    minutes = cairo.hour * 60 + cairo.minute
    if minutes < MARKET_OPEN_H * 60:
        return False, {"ar": "السوق لسه ما فتحش (بيفتح 10 صباحاً)",
                       "en": "Market not open yet (opens 10:00)"}
    if minutes > MARKET_CLOSE_H * 60 + MARKET_CLOSE_M:
        return False, {"ar": "السوق قفل (2:30 ظهراً)",
                       "en": "Market closed (14:30)"}
    return True, {"ar": "السوق شغال دلوقتي", "en": "Market is open"}


def fetch(symbols):
    """
    بيجيب أسعار كل الأسهم في طلب واحد.
    الماسح بياخد 200+ رمز مرة واحدة، فمفيش داعي نقسّمهم.
    """
    payload = {
        "symbols": {"tickers": [f"EGX:{s}" for s in symbols],
                    "query": {"types": []}},
        "columns": COLUMNS,
    }
    data = http_post(SCANNER, payload)
    out = {}
    for row in data.get("data", []):
        symbol = row["s"].split(":")[-1]
        values = dict(zip(COLUMNS, row["d"]))
        if values.get("close") is None:
            continue
        out[symbol] = values
    return out


def sanity(quotes, reference):
    """
    فحص اتساق قبل ما نثق في الأرقام.

    مصدر تاني معناه احتمال اختلاف في التعديلات (تجزئة، توزيعات).
    لو سعر TradingView بعيد جداً عن آخر إغلاق معروف، يبقى فيه
    حاجة غلط — وساعتها بنستبعد السهم بدل ما نعرض رقم مشكوك فيه.
    """
    ok, suspicious = {}, []
    for symbol, q in quotes.items():
        ref = reference.get(symbol)
        close = q["close"]

        # الشمعة نفسها لازم تكون متسقة
        o, h, l = q.get("open"), q.get("high"), q.get("low")
        if None not in (o, h, l):
            if h < max(o, close) or l > min(o, close):
                suspicious.append((symbol, "شمعة متناقضة"))
                continue

        # مقارنة بآخر إغلاق معروف — فرق فوق 25% في يوم واحد
        # مش مستحيل بس نادر جداً، والأرجح إنه اختلاف تعديلات
        if ref and ref > 0:
            drift = abs(close - ref) / ref * 100
            if drift > 25:
                suspicious.append((symbol, f"فرق {drift:.0f}% عن {ref}"))
                continue

        ok[symbol] = q
    return ok, suspicious


def main():
    p = argparse.ArgumentParser(description="أسعار لحظية من TradingView")
    p.add_argument("--show", help="اعرض سهم واحد")
    p.add_argument("--out", default=OUT)
    args = p.parse_args()

    try:
        rows = list(csv.DictReader(open("egx_data.csv", encoding="utf-8-sig")))
    except FileNotFoundError:
        sys.exit("❌ egx_data.csv مش موجود — شغّل egx_fetch.py الأول")

    symbols = [r["symbol"] for r in rows]
    reference = {}
    for r in rows:
        try:
            reference[r["symbol"]] = float(r.get("price") or 0)
        except ValueError:
            pass

    quotes = fetch(symbols)
    quotes, suspicious = sanity(quotes, reference)

    if args.show:
        sym = args.show.upper()
        q = quotes.get(sym)
        if not q:
            sys.exit(f"❌ {sym} مش متاح على TradingView")
        ref = reference.get(sym)
        print(f"\n{sym}")
        print(f"  السعر    {q['close']}   ({q.get('change', 0):+.2f}%)")
        print(f"  افتتاح   {q.get('open')}")
        print(f"  أعلى/أدنى {q.get('high')} / {q.get('low')}")
        print(f"  الحجم    {q.get('volume'):,}" if q.get('volume') else "")
        print(f"  آخر إغلاق مسجّل عندنا: {ref}")
        print(f"  نمط التحديث: {q.get('update_mode')}")
        return

    is_open, state = market_state()
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "TradingView",
        "delayMinutes": 15,
        "marketOpen": is_open,
        "marketState": state,
        "count": len(quotes),
        "quotes": {
            s: {
                "price": q["close"],
                "changePct": round(q["change"], 2) if q.get("change") is not None else None,
                "changeAbs": round(q["change_abs"], 3) if q.get("change_abs") is not None else None,
                "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
                "volume": q.get("volume"),
            } for s, q in quotes.items()
        },
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    print(f"✅ {len(quotes)} سعر في {args.out} · {state['ar']}")
    if suspicious:
        print(f"⚠️  {len(suspicious)} سهم اتستبعد لفحص الاتساق:")
        for s, why in suspicious[:6]:
            print(f"     {s}: {why}")


if __name__ == "__main__":
    main()
