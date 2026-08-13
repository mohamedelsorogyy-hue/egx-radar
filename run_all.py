#!/usr/bin/env python3
"""
EGX Pipeline Runner
-------------------
بيشغّل خط الأنابيب كامل بالترتيب:
    جلب الداتا  →  التقييم والفلترة  →  التحليل الفني  →  داتا الداشبورد

بيوقف عند أول خطوة تفشل، وبيتأكد إن الداتا فريش قبل ما ينشرها.

الاستخدام:
    python3 run_all.py                 # 24 سهم في الداشبورد
    python3 run_all.py --top 40
    python3 run_all.py --skip-fetch     # يعيد الحساب من CSV موجود
"""

import argparse
import csv
import os
import subprocess
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))

# أقصى تأخير مقبول في تاريخ آخر جلسة قبل ما نعتبر الداتا بايتة.
# البورصة المصرية بتشتغل الأحد للخميس، فالجمعة والسبت إجازة —
# 4 أيام بتغطي عطلة أسبوع عادية + يوم إجازة رسمية.
MAX_STALE_DAYS = 4


def step(title, command):
    print(f"\n{'─'*58}\n▶  {title}\n{'─'*58}")
    result = subprocess.run([sys.executable] + command, cwd=HERE)
    if result.returncode != 0:
        sys.exit(f"\n❌ فشلت الخطوة: {title}")


def check_freshness():
    """
    بيتأكد إن الداتا مش قديمة قبل ما تتنشر.
    من غير الفحص ده، لو المصدر وقف، الداشبورد هيفضل يعرض
    أرقام قديمة من غير ما حد ياخد باله — وده أخطر من إنه يقع.
    """
    path = os.path.join(HERE, "egx_data.csv")
    dates = []
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if row.get("lastTradeDate"):
                dates.append(row["lastTradeDate"])
    if not dates:
        sys.exit("❌ مفيش تواريخ جلسات في الداتا")

    latest = max(dates)
    age = (date.today() - datetime.strptime(latest, "%Y-%m-%d").date()).days
    print(f"\n📅 أحدث جلسة: {latest}  (عمرها {age} يوم)")

    if age > MAX_STALE_DAYS:
        sys.exit(
            f"❌ الداتا قديمة: آخر جلسة من {age} يوم (الحد {MAX_STALE_DAYS}).\n"
            f"   يمكن المصدر وقف أو غيّر الروابط. الداشبورد مش هيتحدّث."
        )
    return latest


def main():
    p = argparse.ArgumentParser(description="تشغيل خط أنابيب البورصة كامل")
    p.add_argument("--top", type=int, default=500,
                   help="عدد أسهم الداشبورد (الافتراضي: كل اللي عدّى الفلترة)")
    p.add_argument("--skip-fetch", action="store_true", help="من غير جلب داتا جديدة")
    p.add_argument("--skip-news", action="store_true", help="من غير أخبار")
    p.add_argument("--skip-research", action="store_true",
                   help="من غير تحليل أساسي")
    args = p.parse_args()

    started = datetime.now()

    if not args.skip_fetch:
        step("1/7  جلب بيانات الشركات", ["egx_fetch.py"])
    else:
        print("⏭  تخطّي الجلب — بستخدم egx_data.csv الموجود")

    latest = check_freshness()

    step("2/7  التقييم والفلترة", ["egx_score.py", "--top", "15"])
    step("3/7  التحليل الفني", ["egx_technical.py", "--top", str(args.top)])
    step("4/7  بناء داتا الداشبورد",
         ["egx_dashboard_data.py", "--top", str(args.top)])

    # التحليل الأساسي: بيجيب القوائم المالية الكاملة لكل سهم.
    # أبطأ خطوة (6 طلبات لكل سهم) فبتيجي بعد ما الداشبورد الأساسي جاهز.
    if not args.skip_research:
        try:
            step("5/7  التحليل الأساسي", ["egx_research_all.py"])
        except SystemExit:
            print("⚠️  التحليل الأساسي فشل — باقي الداشبورد شغال")

    # القرارات: بتبني على مخرجات كل اللي قبلها، فلازم تيجي بعدهم
    try:
        step("6/7  قرارات التداول", ["egx_decision.py", "--all"])
    except SystemExit:
        print("⚠️  القرارات فشلت — باقي الداشبورد شغال")

    # الأخبار آخر خطوة عن قصد: لو Google رفض الطلبات، الداشبورد
    # يفضل يتحدّث بالأسعار والتحليل — الأخبار إضافة مش أساس.
    if not args.skip_news:
        try:
            step("7/7  الأخبار", ["egx_news.py"])
        except SystemExit:
            print("⚠️  الأخبار فشلت — الداشبورد هيتحدّث من غيرها")

    elapsed = (datetime.now() - started).total_seconds()
    print(f"\n{'═'*58}")
    print(f"✅ خلص في {elapsed:.0f} ثانية · جلسة {latest}")
    print(f"{'═'*58}")


if __name__ == "__main__":
    main()
