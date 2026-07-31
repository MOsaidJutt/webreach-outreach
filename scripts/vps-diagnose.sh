#!/usr/bin/env bash
#
# Run this ON THE VPS to find out why an inbound reply did not get answered.
#
#   bash /var/www/webreach/scripts/vps-diagnose.sh
#
# It answers, in order, the only four questions that matter:
#   1. Is the deployed code current?
#   2. Is the service actually running?
#   3. Did GHL ever call the webhook?   <-- this is usually the answer
#   4. If it did, what did the app do with it?
#
# Everything is read-only. Nothing is changed and no SMS is sent.

APP_DIR=${APP_DIR:-/var/www/webreach}
SERVICE=${SERVICE:-webreach}
PORT=${PORT:-5001}
NGINX_ACCESS=${NGINX_ACCESS:-/var/log/nginx/access.log}

bar() { printf '\n\033[1;36m===== %s =====\033[0m\n' "$1"; }

bar "1. DEPLOYED CODE"
cd "$APP_DIR" 2>/dev/null || { echo "!! $APP_DIR not found"; exit 1; }
git log --oneline -3 2>/dev/null || echo "(not a git checkout)"
echo "--- build marker the app reports ---"
curl -s --max-time 10 "http://127.0.0.1:$PORT/api/webhooks/ghl" || echo "(no response from the app)"
echo
echo ">> If that printed 'Method Not Allowed' or nothing, the new code is NOT running."

bar "2. SERVICE STATE"
systemctl status "$SERVICE" --no-pager -l 2>/dev/null | head -15
echo "--- listening on $PORT? ---"
(ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null) | grep ":$PORT" || echo "!! nothing is listening on $PORT"

bar "3. DID GHL EVER CALL THE WEBHOOK?"
if [ -r "$NGINX_ACCESS" ]; then
  hits=$(grep -c "webhooks/ghl" "$NGINX_ACCESS" 2>/dev/null || echo 0)
  echo "nginx has logged $hits request(s) to /api/webhooks/ghl in the current log"
  echo "--- the last 20 ---"
  grep "webhooks/ghl" "$NGINX_ACCESS" 2>/dev/null | tail -20 || true
  echo
  echo "--- also checking rotated logs ---"
  zgrep -h "webhooks/ghl" "${NGINX_ACCESS}".*.gz 2>/dev/null | tail -10 || echo "(none in rotated logs)"
else
  echo "cannot read $NGINX_ACCESS -- try: sudo bash $0"
fi
echo
echo ">> POST lines here = GHL is calling us; the problem is in the app."
echo ">> No lines at all  = GHL never called; the problem is the GHL workflow."

bar "4. WHAT THE APP DID WITH THEM"
echo "--- webhook events recorded by the app ---"
curl -s --max-time 10 "http://127.0.0.1:$PORT/api/admin/webhook-events?limit=15" \
  | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('(endpoint not available -- old code still deployed)'); raise SystemExit
ev=d.get('events',[])
if not ev: print('NO WEBHOOK EVENTS RECORDED -- the app has never been called.')
for e in ev:
    print(f\"{(e.get('created_at') or '')[:19]}  {e.get('outcome','?'):12} from={e.get('phone') or e.get('contact_id') or '-'}\")
    print(f\"    they said: {(e.get('message') or '')[:90]}\")
    print(f\"    we replied: {(e.get('reply') or '(nothing)')[:90]}\")
    print(f\"    detail: {(e.get('detail') or '')[:110]}\")
" 2>/dev/null || echo "(could not parse -- is python3 present?)"

echo
echo "--- health summary ---"
curl -s --max-time 10 "http://127.0.0.1:$PORT/api/admin/webhook-health" \
  | python3 -m json.tool 2>/dev/null | head -40 || echo "(not available)"

bar "5. APPLICATION LOG"
LOG="$APP_DIR/instance/app.log"
if [ -f "$LOG" ]; then
  echo "--- every webhook the app received ---"
  grep -a "GHL webhook received" "$LOG" | tail -10 || echo "(none logged)"
  echo
  echo "--- errors and warnings ---"
  grep -aE "ERROR|WARNING|Traceback|Failed|failed" "$LOG" | tail -25 || echo "(none)"
  echo
  echo "--- last 30 lines ---"
  tail -30 "$LOG"
else
  echo "$LOG does not exist yet"
fi

bar "6. SERVICE OUTPUT (last 80 lines)"
journalctl -u "$SERVICE" -n 80 --no-pager 2>/dev/null || echo "(journalctl unavailable)"

bar "DONE"
cat <<'EOS'
Read section 3 first -- it is almost always the answer.

  No POST lines to /api/webhooks/ghl
      GHL is not calling the server. Fix the GHL workflow:
      trigger "Customer Replied" -> action "Webhook" -> POST to
      https://outreach.sms2cartdemo.com/api/webhooks/ghl
      and make sure the workflow is PUBLISHED, not just saved.

  POST lines returning 200, but section 4 shows outcome "no_lead"
      The number that texted is not matched to a lead.

  POST lines returning 401
      WEBHOOK_SECRET is set in .env -- clear it and restart.

  POST lines returning 500, or section 5 shows a Traceback
      Send me that traceback.
EOS
