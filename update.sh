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

# البورصة المصرية بتشتغل الأحد للخميس. الجمعة والسبت مفيش داعي.
DOW=$(date +%u)          # 1=الاثنين … 5=الجمعة 6=السبت 7=الأحد
if [ "$DOW" = "5" ] || [ "$DOW" = "6" ]; then
  say "إجازة أسبوعية — مفيش تحديث"
  exit 0
fi

# مسار بايثون: launchd بيشتغل ببيئة محدودة مش شايفة PATH العادي
PY=$(command -v python3 || echo /usr/bin/python3)

say "بشغّل خط الأنابيب..."
if ! "$PY" run_all.py --top 24 >> "$LOG" 2>&1; then
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
