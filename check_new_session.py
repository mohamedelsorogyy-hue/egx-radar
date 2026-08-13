#!/usr/bin/env python3
"""
فحص سريع: فيه جلسة جديدة عند المصدر ولا لأ؟

بيعمل طلب واحد صغير بس (مش خط الأنابيب كله) ويقارن تاريخ آخر جلسة
عند المصدر بالتاريخ الموجود في الداشبورد.

بيخرج بـ:
    0  = فيه جلسة جديدة، شغّل التحديث
    1  = مفيش جديد، متعملش حاجة
    2  = مقدرش يوصل للمصدر

ليه ملف منفصل؟ عشان السكريبت ده بيتنادى كل نص ساعة، ولازم يبقى
رخيص جداً (طلب واحد) — مش يشغّل خط أنابيب كامل عشان يكتشف
إن مفيش جديد.
"""

import json
import os
import sys
import urllib.request

BASE = "https://stockanalysis.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# أسهم مرجعية: بنسأل عن أكتر من واحد لأن المصدر بيحدّث الأسهم
# على مراحل — لو سألنا عن سهم واحد بس ممكن يكون لسه ما اتحدّثش
# ونفوّت جلسة موجودة فعلاً.
PROBES = ["COMI", "TMGH", "ABUK", "SWDY"]

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "dashboard", "data.json")
LIVE = "https://egx-radar.pages.dev/data.json"


def source_latest():
    """أحدث تاريخ جلسة عند المصدر."""
    dates = []
    for symbol in PROBES:
        try:
            req = urllib.request.Request(
                f"{BASE}/api/quotes/a/EGX-{symbol}",
                headers={"User-Agent": UA},
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                payload = json.load(resp)
            td = (payload.get("data") or {}).get("td")
            if td:
                dates.append(td)
        except Exception:                      # noqa: BLE001
            continue
    return max(dates) if dates else None


def published_latest():
    """
    تاريخ الجلسة المنشورة على الموقع فعلاً.

    ⚠️ مهم: بنقارن باللي **منشور** مش بالملف المحلي.
    لو خط الأنابيب اشتغل وبنى الداتا بس النشر فشل، الملف المحلي
    بيبقى محدّث والموقع لأ — ولو قارنّا بالمحلي، النظام هيقول
    "مفيش جديد" ومش هيحاول ينشر تاني أبداً.
    """
    try:
        req = urllib.request.Request(LIVE, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.load(resp).get("tradeDate")
    except Exception:                          # noqa: BLE001
        # لو الموقع مش راد، بنرجع للملف المحلي كخطة بديلة
        try:
            with open(DATA, encoding="utf-8") as fh:
                return json.load(fh).get("tradeDate")
        except (FileNotFoundError, ValueError):
            return None


def main():
    remote = source_latest()
    if not remote:
        print("تعذّر الوصول للمصدر")
        sys.exit(2)

    # لو الملف المحلي ناقص أو باظ، لازم نعيد البناء حتى لو المنشور
    # محدّث — من غير الشرط ده السكريبت بيقول "مفيش جديد" ويسيب
    # المشروع من غير داتا محلية خالص.
    if not os.path.exists(DATA):
        print(f"الملف المحلي ناقص — إعادة بناء (المصدر {remote})")
        sys.exit(0)

    live = published_latest()
    if live and remote <= live:
        print(f"مفيش جديد (المصدر {remote} · المنشور {live})")
        sys.exit(1)

    print(f"جلسة جديدة: {remote}  (المنشور {live or 'لا شيء'})")
    sys.exit(0)


if __name__ == "__main__":
    main()
