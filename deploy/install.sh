#!/usr/bin/env bash
#
# نصب ایجنت سئو روی سرور اوبونتو/دبیان.
#
#   sudo bash deploy/install.sh
#
# کاری که می‌کند:
#   - یک کاربر سیستمی بدون شل (seoagent) می‌سازد
#   - پروژه را در /opt/seoagent نصب می‌کند و محیط مجازی می‌سازد
#   - یک رمز تصادفی برای داشبورد تولید می‌کند
#   - سرویس systemd را با gunicorn راه می‌اندازد (بایند روی 127.0.0.1)
#   - در صورت دادن دامنه، nginx و گواهی TLS را هم تنظیم می‌کند
#
set -euo pipefail

APP_USER="seoagent"
APP_DIR="/opt/seoagent"
STATE_DIR="/var/lib/seoagent"
ENV_FILE="/etc/seoagent.env"
PORT="${PORT:-5000}"
DOMAIN="${DOMAIN:-}"
REPO="${REPO:-https://github.com/ferya3/seo.git}"
BRANCH="${BRANCH:-claude/personal-seo-agent-3n2suc}"

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
die() { printf '\n\033[1;31mخطا:\033[0m %s\n' "$1" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "این اسکریپت باید با sudo اجرا شود."

log "نصب پیش‌نیازها"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip ca-certificates >/dev/null

log "ساخت کاربر سیستمی «$APP_USER»"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir "/home/$APP_USER" --shell /usr/sbin/nologin "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" -m 750 "$STATE_DIR"

log "دریافت کد در $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" reset --quiet --hard "origin/$BRANCH"
else
  rm -rf "$APP_DIR"
  git clone --quiet --branch "$BRANCH" "$REPO" "$APP_DIR"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

log "ساخت محیط مجازی و نصب وابستگی‌ها"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/services/engine/requirements.txt"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet gunicorn

log "تنظیم فایل محیطی"
if [ ! -f "$ENV_FILE" ]; then
  PASSWORD="$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 20)"
  cat > "$ENV_FILE" <<EOF
# تنظیمات ایجنت سئو — بعد از تغییر: systemctl restart seoagent

SEO_AGENT_USERNAME=admin
SEO_AGENT_PASSWORD=$PASSWORD

# محل نگهداری گزارش‌ها
SEO_AGENT_DATA_DIR=$STATE_DIR

# برای فعال کردن پیشنهادهای هوش مصنوعی، کلید را اینجا بگذار:
# ANTHROPIC_API_KEY=sk-ant-...

# برای سقف بالاتر PageSpeed Insights:
# PAGESPEED_API_KEY=...

# فقط اگر می‌خواهی سایت‌های روی شبکه‌ی داخلی را هم بررسی کنی (ناامن روی سرور عمومی):
# SEO_AGENT_ALLOW_PRIVATE=1
EOF
  chmod 640 "$ENV_FILE"
  chown root:"$APP_USER" "$ENV_FILE"
  GENERATED_PASSWORD="$PASSWORD"
else
  log "فایل $ENV_FILE از قبل وجود دارد — دست نخورد"
  GENERATED_PASSWORD=""
fi

log "نصب سرویس systemd"
sed -e "s|@APP_DIR@|$APP_DIR|g" \
    -e "s|@APP_USER@|$APP_USER|g" \
    -e "s|@ENV_FILE@|$ENV_FILE|g" \
    -e "s|@PORT@|$PORT|g" \
    "$APP_DIR/deploy/seoagent.service" > /etc/systemd/system/seoagent.service

systemctl daemon-reload
systemctl enable --now seoagent >/dev/null

log "بررسی سلامت سرویس"
for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    HEALTHY=1; break
  fi
  sleep 0.5
done
[ "${HEALTHY:-}" = "1" ] || die "سرویس بالا نیامد. لاگ: journalctl -u seoagent -n 50 --no-pager"

if [ -n "$DOMAIN" ]; then
  log "تنظیم nginx برای $DOMAIN"
  apt-get install -y -qq nginx >/dev/null
  sed -e "s|@DOMAIN@|$DOMAIN|g" -e "s|@PORT@|$PORT|g" \
      "$APP_DIR/deploy/nginx.conf" > "/etc/nginx/sites-available/seoagent"
  ln -sf /etc/nginx/sites-available/seoagent /etc/nginx/sites-enabled/seoagent
  rm -f /etc/nginx/sites-enabled/default
  nginx -t >/dev/null && systemctl reload nginx

  log "گرفتن گواهی TLS از Let's Encrypt"
  apt-get install -y -qq certbot python3-certbot-nginx >/dev/null
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
          --register-unsafely-without-email --redirect || \
    printf '\033[1;33mهشدار:\033[0m گرفتن گواهی ناموفق بود. DNS دامنه را بررسی کن و دوباره اجرا کن: certbot --nginx -d %s\n' "$DOMAIN"
fi

cat <<EOF

────────────────────────────────────────────────────────────
  نصب تمام شد.

  وضعیت سرویس :  systemctl status seoagent
  لاگ زنده     :  journalctl -u seoagent -f
  تنظیمات      :  $ENV_FILE
  گزارش‌ها      :  $STATE_DIR
EOF

if [ -n "$DOMAIN" ]; then
  echo "  آدرس        :  https://$DOMAIN"
else
  cat <<EOF
  آدرس        :  http://127.0.0.1:$PORT  (فقط روی خود سرور)

  برای دسترسی از کامپیوتر خودت، تونل SSH بزن:
      ssh -N -L $PORT:127.0.0.1:$PORT $(logname 2>/dev/null || echo user)@<آی‌پی-سرور>
  بعد در مرورگر باز کن: http://127.0.0.1:$PORT
EOF
fi

echo "  کاربر       :  admin"
if [ -n "$GENERATED_PASSWORD" ]; then
  echo "  رمز         :  $GENERATED_PASSWORD"
  echo ""
  echo "  ⚠ این رمز را همین حالا جایی ذخیره کن — دوباره نمایش داده نمی‌شود."
else
  echo "  رمز         :  همان رمز قبلی در $ENV_FILE"
fi
echo "────────────────────────────────────────────────────────────"
