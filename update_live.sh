#!/bin/bash
# تحديث الأسعار اللحظية بس (من TradingView) ونشرها.
#
# منفصل عن update.sh عن قصد: ده خفيف جداً (طلب واحد + رفع ملف صغير)
# فينفع يشتغل كل ربع ساعة أثناء التداول، بينما خط الأنابيب الكامل
# بياخد دقايق وبيتشغّل مرة واحدة بعد الإقفال.

set -uo pipefail
cd "$(dirname "$0")" || exit 1

LOG="logs/live.log"
mkdir -p logs
say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# اللوج بيتقص عشان ما يكبرش — بيتكتب فيه كل ربع ساعة
[ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 1500 ] && \
  { tail -n 500 "$LOG" > "$LOG.tmp"; mv "$LOG.tmp" "$LOG"; }

# البورصة المصرية: الأحد–الخميس 10:00–14:30 بتوقيت القاهرة.
# بنكمّل لحد 15:00 عشان نمسك آخر تحديث بعد الإقفال.
DOW=$(TZ=Africa/Cairo date +%u)      # 5=الجمعة 6=السبت
HOUR=$(TZ=Africa/Cairo date +%-H)
if [ "$DOW" = "5" ] || [ "$DOW" = "6" ]; then exit 0; fi
if [ "$HOUR" -lt 9 ] || [ "$HOUR" -gt 15 ]; then exit 0; fi

export PATH="/opt/anaconda3/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
PY=""
for c in /opt/anaconda3/bin/python3 /opt/homebrew/bin/python3 \
         /usr/local/bin/python3 /usr/bin/python3; do
  [ -x "$c" ] || continue
  "$c" -c "import ssl,urllib.request
urllib.request.urlopen('https://www.tradingview.com/robots.txt',timeout=10)" \
    >/dev/null 2>&1 && { PY="$c"; break; }
done
[ -z "$PY" ] && { say "❌ مفيش بايثون شهاداته سليمة"; exit 1; }

if ! "$PY" egx_live.py >> "$LOG" 2>&1; then
  say "❌ فشل جلب الأسعار"
  exit 1
fi

# النشر: بنرفع ملف الأسعار بس مش الداشبورد كله.
# wrangler بيرفع المجلد كامل لكنه بيتخطى الملفات اللي ما اتغيرتش،
# فالرفع بيبقى ثواني.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.npm-global/bin:$PATH"
WRANGLER=$(command -v wrangler)
[ -z "$WRANGLER" ] && { say "⚠️ wrangler مش موجود"; exit 1; }

if "$WRANGLER" pages deploy dashboard --project-name=egx-radar \
     --branch=main --commit-dirty=true >> "$LOG" 2>&1; then
  PRICE=$("$PY" -c "import json;d=json.load(open('dashboard/live.json'));print(d['count'])" 2>/dev/null)
  say "✅ اتنشر $PRICE سعر"
else
  say "❌ النشر فشل"
  exit 1
fi
