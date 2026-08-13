#!/bin/bash
# تحديث مرصد البورصة المصرية ونشره على Cloudflare Pages.
# بيتنادى من launchd يومياً، أو يدوياً: ./update.sh

set -uo pipefail
cd "$(dirname "$0")" || exit 1

LOG="logs/update.log"
mkdir -p logs

# نحتفظ بآخر 2000 سطر بس عشان اللوج ما يكبرش مع الوقت
trim_log() {
  if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 2000 ]; then
    tail -n 1000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
  fi
}

say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

trim_log
say "──────── بداية التحديث ────────"

# قفل يمنع تشغيلين في نفس الوقت.
# من غيره: التشغيل الساعة 1 لسه بيبني التقارير، والساعة 2 بيبدأ
# ويكتب فوقه — فالاتنين بيفشلوا والموقع مايتحدّثش. حصل فعلاً.
LOCK="logs/.update.lock"
if [ -e "$LOCK" ]; then
  OWNER=$(cat "$LOCK" 2>/dev/null)
  if [ -n "$OWNER" ] && kill -0 "$OWNER" 2>/dev/null; then
    say "تشغيل تاني شغال دلوقتي (PID $OWNER) — بخرج"
    exit 0
  fi
  say "قفل قديم من تشغيل مات — بشيله"
  rm -f "$LOCK"
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# مفيش تخطّي لأيام الأسبوع عن قصد: إغلاق الخميس ساعات بينزل الجمعة
# بالليل، فلو عدّينا الجمعة كنا هنفوّته لحد الأحد. فحص "فيه جلسة
# جديدة؟" اللي تحت بيتكفّل بالإجازات لوحده وبتكلفة طلب واحد.

# اختيار بايثون: launchd بيشتغل ببيئة محدودة مش شايفة PATH العادي.
# مش كفاية نلاقي بايثون — لازم نلاقي واحد شهادات SSL بتاعته متثبّتة.
# (نسخ python.org بتيجي من غير شهادات لحد ما تشغّل Install Certificates)
export PATH="/opt/anaconda3/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

PY=""
for candidate in /opt/anaconda3/bin/python3 /opt/homebrew/bin/python3 \
                 /usr/local/bin/python3 /usr/bin/python3; do
  [ -x "$candidate" ] || continue
  if "$candidate" -c "
import ssl, urllib.request, sys
try:
    ctx = ssl.create_default_context()
    urllib.request.urlopen('https://stockanalysis.com/robots.txt', timeout=15, context=ctx)
except urllib.error.HTTPError:
    pass                       # وصلنا للسيرفر — يبقى SSL تمام
except Exception:
    sys.exit(1)
" >/dev/null 2>&1; then
    PY="$candidate"; break
  fi
done

if [ -z "$PY" ]; then
  say "❌ مفيش نسخة بايثون شهاداتها سليمة — مفيش تحديث"
  exit 1
fi
say "بايثون: $PY ($("$PY" -c 'import sys;print(sys.version.split()[0])'))"

# فحص رخيص قبل الشغل الكبير: بيسأل 4 أسهم عن آخر جلسة، ويقارنها
# باللي **منشور على الموقع** مش بالملف المحلي.
#
# ليه 4 أسهم؟ المصدر بيحدّث الأسهم على مراحل — لو سألنا سهم واحد
# وهو اتأخر، هنقول "مفيش جديد" ونفوّت الجلسة.
#
# ليه نقارن بالمنشور؟ لأن لو الداتا اتبنت محلياً والنشر فشل، الملف
# المحلي بيبقى محدّث والموقع لأ — والنظام هيقول "مفيش جديد" ومش
# هيحاول ينشر تاني أبداً. حصل فعلاً.
"$PY" check_new_session.py >> "$LOG" 2>&1
RC=$?
if [ $RC -eq 1 ]; then exit 0; fi
if [ $RC -eq 2 ]; then
  say "⚠️  مقدرتش أوصل للمصدر — هجرّب في الميعاد الجاي"
  exit 1
fi

say "بشغّل خط الأنابيب..."
if ! "$PY" run_all.py >> "$LOG" 2>&1; then
  say "❌ خط الأنابيب فشل — مفيش نشر (الموقع هيفضل على آخر داتا سليمة)"
  exit 1
fi

TRADE_DATE=$("$PY" -c "import json;print(json.load(open('dashboard/data.json'))['tradeDate'])" 2>/dev/null)
say "الداتا جاهزة — جلسة $TRADE_DATE"

# wrangler متثبّت عن طريق npm، فمحتاجين مساره
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.npm-global/bin:$PATH"
WRANGLER=$(command -v wrangler)

if [ -z "$WRANGLER" ]; then
  say "⚠️  wrangler مش موجود في المسار — الداتا اتحدّثت محلياً بس من غير نشر"
  exit 1
fi

say "بنشر على Cloudflare..."
if "$WRANGLER" pages deploy dashboard \
     --project-name=egx-radar --branch=main --commit-dirty=true >> "$LOG" 2>&1; then
  say "✅ اتنشر — https://egx-radar.pages.dev (جلسة $TRADE_DATE)"
else
  say "❌ النشر فشل — الداتا محدّثة محلياً، جرّب تنشر يدوياً"
  exit 1
fi
