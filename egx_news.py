#!/usr/bin/env python3
"""
EGX News
--------
بيجيب أخبار السوق وأخبار كل سهم بالعربي من Google News RSS.

مجاني تماماً — مفيش مفتاح API ولا حدود معلنة.

بيطلّع:
    dashboard/news.json          أخبار السوق العامة + نبض إخباري
    dashboard/news/{رمز}.json    أخبار كل سهم (بتتحمّل عند فتح السهم)

الاستخدام:
    python3 egx_news.py                  # كل الأسهم اللي في الشورت-ليست
    python3 egx_news.py --symbols COMI,ABUK
    python3 egx_news.py --market-only    # أخبار السوق بس
"""

import argparse
import csv
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

RSS = "https://news.google.com/rss/search"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
WORKERS = 4
RETRIES = 3

OUT_MARKET = "dashboard/news.json"
OUT_DIR = "dashboard/news"

# أخبار أقدم من كده مالهاش قيمة في قرار تداول
MAX_AGE_DAYS = 21
PER_STOCK = 8
PER_MARKET = 25

# استعلامات السوق العامة. كل واحد بيغطي زاوية مختلفة —
# السوق نفسه، والاقتصاد الكلي اللي بيحرّكه، والقرارات التنظيمية.
MARKET_QUERIES = [
    ("السوق", "البورصة المصرية"),
    ("السوق", "مؤشر EGX30"),
    ("الاقتصاد", "البنك المركزي المصري سعر الفائدة"),
    ("الاقتصاد", "التضخم في مصر"),
    ("العملة", "سعر الدولار في مصر"),
    ("تنظيمي", "الرقابة المالية البورصة"),
]


def http_get(url):
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except OSError as err:
            last = err
            if attempt < RETRIES - 1:
                time.sleep((2 ** attempt) + random.random())
    raise RuntimeError(str(last))


def feed_url(query):
    q = urllib.parse.quote(query)
    return f"{RSS}?q={q}&hl=ar&gl=EG&ceid=EG:ar"


def parse_date(raw):
    """RFC-822 من RSS. بيرجّع None لو الصيغة غريبة."""
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            continue
    return None


def clean(text):
    """بيشيل وسوم HTML وبيفك الترميز."""
    return re.sub(r"<[^>]+>", "", html.unescape(text or "")).strip()


def fetch_feed(query, limit, max_age=MAX_AGE_DAYS):
    """بيجيب ويحلّل خلاصة RSS واحدة."""
    try:
        xml = http_get(feed_url(query))
    except RuntimeError:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age)
    out = []
    for block in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(pattern):
            m = re.search(pattern, block, re.S)
            return m.group(1) if m else ""

        title = clean(tag(r"<title>(.*?)</title>"))
        if not title:
            continue

        link = clean(tag(r"<link>(.*?)</link>"))
        raw_date = tag(r"<pubDate>(.*?)</pubDate>")
        source = clean(tag(r"<source[^>]*>(.*?)</source>"))

        published = parse_date(raw_date)
        if published and published < cutoff:
            continue

        # Google بيحط " - المصدر" في آخر العنوان، بنشيله لأنه مكرر
        if source and title.endswith(f"- {source}"):
            title = title[: -len(source) - 2].strip()

        out.append({
            "title": title,
            "url": link,
            "source": source,
            "date": published.isoformat(timespec="seconds") if published else None,
        })
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- التصنيف

# كلمات بتدل على اتجاه الخبر. مش تحليل مشاعر حقيقي — دي إشارة
# تقريبية بتساعد العين تفرز بسرعة، ومش أساس لقرار.
POSITIVE = [
    "ارتفاع", "ارتفع", "صعود", "تصعد", "مكاسب", "ربح", "أرباح", "نمو",
    "تحسن", "قفزة", "يقفز", "استثمار جديد", "توسع", "توزيعات", "كوبون",
    "زيادة الأرباح", "تعاقد", "صفقة", "اتفاقية", "ترقية",
]
NEGATIVE = [
    "هبوط", "هبط", "تراجع", "خسائر", "خسارة", "انخفاض", "انخفض",
    "أزمة", "تحذير", "غرامة", "مخالفة", "وقف التداول", "شطب",
    "تخفيض", "استقالة", "تعثر", "ديون", "احتجاجات", "توتر",
]


def sentiment(title):
    """اتجاه تقريبي من كلمات العنوان."""
    pos = sum(1 for w in POSITIVE if w in title)
    neg = sum(1 for w in NEGATIVE if w in title)
    if pos > neg:
        return "إيجابي"
    if neg > pos:
        return "سلبي"
    return "محايد"


def enrich(items):
    for it in items:
        it["tone"] = sentiment(it["title"])
    return items


# لواحق قانونية مالهاش قيمة في البحث — بتضيّق النتيجة لصفر.
# "السويدي اليكتريك ش م م" مابيرجّعش حاجة، "السويدي اليكتريك" بيرجّع أخبار.
LEGAL_SUFFIXES = re.compile(
    r"\s*[-–—]?\s*\(?\s*(ش\s*\.?\s*م\s*\.?\s*م|ش\s*\.?\s*م\s*\.?\s*ع|"
    r"ش\.?م\.?م|SAE|S\.A\.E|شركة مساهمة مصرية)\s*\)?\s*$",
    re.I,
)


def tidy_name(name):
    """
    بينضّف الاسم للبحث: بيشيل اللواحق القانونية والأقواس التوضيحية.
    "البنك التجاري الدولي - مصر ( سي أي بي)" → "البنك التجاري الدولي"
    """
    if not name:
        return None
    name = LEGAL_SUFFIXES.sub("", name.strip())
    # أقواس توضيحية زي "( سي أي بي)" — مش جزء من الاسم المتداول
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    # جزء توضيحي بعد شرطة زي "- مصر"
    name = re.sub(r"\s*[-–—]\s*مصر\s*$", "", name).strip()
    return name.strip(" -–—:،") or None


def arabic_name(items, symbol):
    """
    بيستخرج الاسم العربي للشركة من عناوين الأخبار.
    مباشر بتكتب العنوان بصيغة "اسم الشركة (RMZ)" فبناخد اللي قبل القوس.
    ده بيدينا أسماء عربية مجاناً من غير ما نكتب 130 اسم بإيدنا.
    """
    for it in items:
        m = re.search(rf"^(.{{3,60}}?)\s*\(\s*{symbol}\s*\)", it["title"])
        if m:
            name = m.group(1).strip(" -–—:")
            if len(name) > 3:
                return name
    return None


# ---------------------------------------------------------------- التشغيل

NAMES_FILE = "arabic_names.json"


def load_names():
    try:
        with open(NAMES_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}


def discover_name(symbol):
    """
    بيطلّع الاسم العربي للشركة.

    البحث بالرمز بيرجّع صفحات تعريفية من "معلومات مباشر" عنوانها
    "اسم الشركة (RMZ)" — قديمة كأخبار، بس مثالية كمصدر للاسم.
    عشان كده بنسمح بأي عمر هنا، بعكس البحث عن الأخبار نفسها.
    """
    items = fetch_feed(f'"{symbol}" بورصة', 10, max_age=3650)
    return tidy_name(arabic_name(items, symbol))


def fetch_stock_news(args):
    """
    بيجيب أخبار سهم بالاسم العربي.
    البحث بالرمز لوحده مابيجيبش أخبار حديثة — الصحافة المصرية
    بتكتب اسم الشركة مش رمزها.
    """
    symbol, name = args
    items = []
    if name:
        items = enrich(fetch_feed(f'"{name}"', PER_STOCK))
        # لو الاسم الكامل ضيّق أوي، نجرب أول 3 كلمات منه
        if len(items) < 2:
            short = " ".join(name.split()[:3])
            if short and short != name:
                items = enrich(fetch_feed(f'"{short}" بورصة', PER_STOCK))

    return symbol, {
        "symbol": symbol,
        "nameAr": name,
        "items": items,
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main():
    p = argparse.ArgumentParser(description="أخبار البورصة المصرية")
    p.add_argument("--symbols", help="رموز محددة مفصولة بفاصلة")
    p.add_argument("--shortlist", default="egx_shortlist.csv")
    p.add_argument("--market-only", action="store_true")
    args = p.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(OUT_MARKET) or ".", exist_ok=True)

    # ---- أخبار السوق العامة
    print("⏳ بجيب أخبار السوق...")
    market = []
    seen = set()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = pool.map(
            lambda qq: (qq[0], fetch_feed(qq[1], PER_MARKET, max_age=7)),
            MARKET_QUERIES,
        )
        for topic, items in results:
            for it in items:
                # نفس الخبر بيتكرر بين الاستعلامات
                key = it["title"][:70]
                if key in seen:
                    continue
                seen.add(key)
                it["topic"] = topic
                market.append(it)

    enrich(market)
    market.sort(key=lambda x: x["date"] or "", reverse=True)
    market = market[:40]

    tones = [m["tone"] for m in market]
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": market,
        "pulse": {
            "positive": tones.count("إيجابي"),
            "negative": tones.count("سلبي"),
            "neutral": tones.count("محايد"),
        },
    }
    with open(OUT_MARKET, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"✅ {len(market)} خبر سوق في {OUT_MARKET}")

    if args.market_only:
        return

    # ---- أخبار كل سهم
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        try:
            rows = list(csv.DictReader(open(args.shortlist, encoding="utf-8-sig")))
        except FileNotFoundError:
            sys.exit(f"❌ {args.shortlist} مش موجود")
        symbols = [r["symbol"] for r in rows]

    # الأسماء العربية بتتخزّن على القرص: بتتكتشف مرة واحدة بس
    # وبعدها بنستخدمها في كل تشغيل من غير طلبات زيادة.
    names = load_names()
    missing = [s for s in symbols if s not in names]
    if missing:
        print(f"⏳ بكتشف الأسماء العربية لـ {len(missing)} سهم...")
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for symbol, name in zip(missing, pool.map(discover_name, missing)):
                if name:
                    names[symbol] = name
        with open(NAMES_FILE, "w", encoding="utf-8") as fh:
            json.dump(names, fh, ensure_ascii=False, indent=1, sort_keys=True)
        print(f"✅ {len(names)} اسم عربي محفوظ في {NAMES_FILE}")

    print(f"⏳ بجيب أخبار {len(symbols)} سهم...")
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        jobs = [(s, names.get(s)) for s in symbols]
        for symbol, data in pool.map(fetch_stock_news, jobs):
            with open(os.path.join(OUT_DIR, f"{symbol}.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
            done += 1

            print(f"\r  {done}/{len(symbols)}", end="", flush=True)

    # نسخة للداشبورد عشان يعرض الأسماء العربية ويدوّر بيها
    with open("dashboard/names_ar.json", "w", encoding="utf-8") as fh:
        json.dump(names, fh, ensure_ascii=False, separators=(",", ":"))

    have = sum(1 for s in symbols if names.get(s))
    print(f"\n✅ أخبار {done} سهم · {have} منهم ليهم اسم عربي")


if __name__ == "__main__":
    main()
