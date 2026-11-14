#!/usr/bin/env bash
# =============================================================================
# FilaTrucking - READ-ONLY production diagnostics
#
# Run ON THE SERVER from the repo directory (where docker-compose.prod.yml lives):
#       bash qa/prod_diagnostics.sh 2>&1 | tee ~/fila_diag_$(date +%F_%H%M).txt
#
# SAFETY: this script only reads (it creates no files; pipe to tee yourself if you want a copy). It does not restart, stop, build, delete, write
# or change anything: no docker restart/exec of mutating commands, no HTTP
# DELETE/POST, no SQL other than SELECT/SHOW. It never prints secret values:
#   * env vars are printed as NAMES only (a few non-secret settings as values)
#   * the WhatsApp session folder is listed by file NAME/size/mtime, never content
#   * log lines are filtered; long digit strings (phone numbers) are masked
# Then send me the output file; read the "SUMMARY OF FLAGS" block at the bottom first.
# =============================================================================
set -u
export LC_ALL=C
FLAGS=()
flag() { FLAGS+=("$1"); echo "  >>> [FLAG] $1"; }
hr()   { echo; echo "=================================================================="; echo "== $*"; echo "=================================================================="; }
run()  { echo "\$ $*"; timeout "${T:-20}" "$@" 2>&1 | head -n "${N:-40}"; }
mask() { sed -E 's/[0-9]{9,}/<num>/g; s/(Bearer )[A-Za-z0-9._-]+/\1<hidden>/g'; }

DJ=${DJ:-$(docker ps -a --format '{{.Names}}' | grep -E 'django' | head -1)}
SC=${SC:-$(docker ps -a --format '{{.Names}}' | grep -E 'sidecar' | head -1)}
DB=${DB:-$(docker ps -a --format '{{.Names}}' | grep -E 'mysql' | head -1)}
NG=${NG:-$(docker ps -a --format '{{.Names}}' | grep -E 'nginx' | head -1)}
CB=${CB:-$(docker ps -a --format '{{.Names}}' | grep -E 'certbot' | head -1)}
DOMAIN=${DOMAIN:-filatruckingco.duckdns.org}
# sha256 of the secret constant that is committed in the repo (so we can test "still using the default?" without printing anything)
DEFAULT_SECRET_SHA256=3994e333d2307844dee26291b06947873de6b9c3faa2655cbb6c4e14127fc0d1

hr "0. Context"
echo "run at (UTC): $(date -u +%F\ %T)   host: $(hostname)   user: $(id -un)"
echo "containers detected -> django=$DJ sidecar=$SC mysql=$DB nginx=$NG certbot=$CB"
[ -z "$SC" ] && flag "no sidecar container found"

hr "1. Host resources"
N=6 run uptime
N=6 run free -m
N=15 run df -h /
run docker system df
echo "swap:"; N=3 run swapon --show
echo "oracle/arm?  arch=$(uname -m)  cpus=$(nproc)"
N=8 run timedatectl

hr "2. All containers (state, restarts, published ports)"
run docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
for c in $DJ $SC $DB $NG $CB; do
  [ -z "$c" ] && continue
  echo; echo "--- $c"
  docker inspect "$c" --format 'image={{.Config.Image}}  created={{.Created}}  startedAt={{.State.StartedAt}}
status={{.State.Status}} exitCode={{.State.ExitCode}} restarts={{.RestartCount}} OOMKilled={{.State.OOMKilled}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none-defined{{end}}
restartPolicy={{.HostConfig.RestartPolicy.Name}} user={{if .Config.User}}{{.Config.User}}{{else}}root(default){{end}} memLimit={{.HostConfig.Memory}} shm={{.HostConfig.ShmSize}}' 2>&1
  r=$(docker inspect "$c" --format '{{.RestartCount}}'); o=$(docker inspect "$c" --format '{{.State.OOMKilled}}')
  [ "${r:-0}" -gt 0 ] 2>/dev/null && flag "$c restarted $r time(s) (see section 4 for why)"
  [ "$o" = "true" ] && flag "$c was OOM-killed"
  h=$(docker inspect "$c" --format '{{if .State.Health}}yes{{else}}no{{end}}'); [ "$h" = "no" ] && echo "  (no healthcheck defined)"
  echo "mounts (type:name-or-source -> destination):"
  docker inspect "$c" --format '{{range .Mounts}}  {{.Type}}:{{if .Name}}{{.Name}}{{else}}{{.Source}}{{end}} -> {{.Destination}} rw={{.RW}}{{"\n"}}{{end}}'
  echo "published ports (host side):"
  docker port "$c" 2>&1 | sed 's/^/  /'
  docker port "$c" 2>/dev/null | grep -q '^\(3001\|8000\|3306\)/tcp -> 0.0.0.0' && flag "$c publishes an internal port on 0.0.0.0 ($(docker port "$c" | grep -E '^(3001|8000|3306)' | tr '\n' ' '))"
  echo "env var NAMES only:"; docker inspect "$c" --format '{{range .Config.Env}}{{println .}}{{end}}' | cut -d= -f1 | sort | tr '\n' ' '; echo
done
N=15 run docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.PIDs}}'
docker ps --format '{{.Names}}' | grep -qiE 'celery|worker|beat|redis' || flag "no redis/celery worker/beat container is running -> scheduled tasks (statements, recurring invoices, GoMotive sync) never run"

hr "3. WhatsApp sidecar internals"
if [ -n "$SC" ]; then
  echo "--- runtime"
  N=3 run docker exec "$SC" node -v
  echo "user id inside container: $(docker exec "$SC" id -u 2>&1)  (0 = root)"
  echo -n "baileys version: "; docker exec "$SC" node -p "require('/app/sidecar/node_modules/@whiskeysockets/baileys/package.json').version" 2>&1
  echo -n "express installed: "; docker exec "$SC" sh -c 'test -d /app/sidecar/node_modules/express && echo yes || echo NO' 2>&1
  echo -n "local patch applied (MACOS marker count in validate-connection.js): "; docker exec "$SC" sh -c "grep -c MACOS /app/sidecar/node_modules/@whiskeysockets/baileys/lib/Utils/validate-connection.js" 2>&1
  echo -n "pinned WA Web version in index.js: "; docker exec "$SC" sh -c "grep -n 'version:' /app/sidecar/index.js | head -2" 2>&1
  echo "--- session (auth) folder: NAMES, sizes, mtimes only"
  docker exec "$SC" sh -c 'ls -la --time-style=long-iso /app/sidecar/auth_data 2>&1 | head -12; echo "file count: $(ls /app/sidecar/auth_data 2>/dev/null | wc -l)"; test -f /app/sidecar/auth_data/creds.json && echo "creds.json: present, $(stat -c "%y %s bytes" /app/sidecar/auth_data/creds.json)" || echo "creds.json: MISSING"; grep -o "\"registered\":[a-z]*" /app/sidecar/auth_data/creds.json 2>/dev/null' 2>&1
  docker exec "$SC" sh -c 'test -f /app/sidecar/auth_data/creds.json' 2>/dev/null || flag "sidecar has NO creds.json: it is not logged in to WhatsApp (needs QR scan). If this was working before: the session was cleared (DELETE /api/auth, logout from phone, or volume recreated)"
  echo "--- is the session folder on a persistent volume?"
  docker inspect "$SC" --format '{{range .Mounts}}{{if eq .Destination "/app/sidecar/auth_data"}}volume={{.Name}} type={{.Type}}{{end}}{{end}}' | grep -q volume= || flag "nothing is mounted at /app/sidecar/auth_data: session would be lost on every container re-create"
  echo "--- own REST API (GET only, from inside the container)"
  docker exec "$SC" node -e "fetch('http://127.0.0.1:3001/api/status').then(r=>r.text()).then(t=>console.log(t.slice(0,400))).catch(e=>console.log('ERR',e.message))" 2>&1 | mask
  docker exec "$SC" node -e "fetch('http://127.0.0.1:3001/health').then(r=>console.log('health http',r.status)).catch(e=>console.log('ERR',e.message))" 2>&1
  echo "--- log analysis (counts over ALL retained log lines; pino 'devicePairingData' blobs are never printed)"
  LOGF=$(docker logs "$SC" 2>&1)
  echo "total lines: $(wc -l <<<"$LOGF")"
  echo "first log line time: $(grep -m1 -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z' <<<"$LOGF")   last: $(grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z' <<<"$LOGF" | tail -1)"
  c405=$(grep -c '"reason":"405"' <<<"$LOGF"); echo "405 'Connection Failure' disconnects : $c405"
  [ "$c405" -gt 0 ] && flag "sidecar logged $c405 x HTTP-405 'Connection Failure' from WhatsApp: the pinned WhatsApp Web version / Baileys build is rejected (see qa/WHATSAPP_INVESTIGATION.md, WA-001)"
  echo "401 loggedOut                         : $(grep -c -E 'statusCode":401|loggedOut|Logged out' <<<"$LOGF")"
  echo "440 connectionReplaced (two sessions) : $(grep -c -E 'statusCode":440|connectionReplaced|Stream Errored \(conflict\)' <<<"$LOGF")"
  echo "515 restartRequired                   : $(grep -c -E 'statusCode":515|restart required' <<<"$LOGF")"
  echo "428 connectionClosed                  : $(grep -c -E 'statusCode":428' <<<"$LOGF")"
  echo "408 timeout / 503 unavailable         : $(grep -c -E 'statusCode":(408|503)' <<<"$LOGF")"
  echo "'connected to WA' (reconnect attempts): $(grep -c 'connected to WA' <<<"$LOGF")"
  echo "'Opened connection' (successful opens): $(grep -c 'Opened connection' <<<"$LOGF")"
  echo "ECONNREFUSED / ENOTFOUND to django     : $(grep -c -E 'ECONNREFUSED|ENOTFOUND|getaddrinfo' <<<"$LOGF")"
  echo "Django API errors                     : $(grep -c 'Django API error' <<<"$LOGF")"
  echo "unhandled rejection / fatal           : $(grep -c -E 'UnhandledPromiseRejection|Unhandled|Fatal error|uncaughtException|ERR_UNHANDLED' <<<"$LOGF")"
  echo "last 12 non-pino lines (what the app itself printed):"; grep -vE '^\{"level"' <<<"$LOGF" | grep -E '^\[|✅|❌|🚪|📱|🚀|Opened|Error' | tail -12 | cut -c1-220 | mask
  echo "last exit reason of the previous process (from docker):"; docker inspect "$SC" --format 'exitCode={{.State.ExitCode}} error={{.State.Error}} finishedAt={{.State.FinishedAt}}'
fi

hr "4. Connectivity (all read-only)"
if [ -n "$DJ" ] && [ -n "$SC" ]; then
  echo "--- django -> sidecar (service-name DNS and /health)"
  for h in sidecar whatsapp-sidecar; do
    echo -n "  $h : "; docker exec "$DJ" python -c "import socket,sys;print(socket.gethostbyname('$h'))" 2>&1 | tail -1
  done
  echo -n "  GET http://sidecar:3001/health from django -> "
  docker exec "$DJ" python -c "import urllib.request as u;print(u.urlopen('http://sidecar:3001/health',timeout=5).status)" 2>&1 | tail -1
  echo -n "  env WHATSAPP_SIDECAR_URL in django = "; docker exec "$DJ" printenv WHATSAPP_SIDECAR_URL 2>&1
  echo "--- sidecar -> django"
  docker exec "$SC" node -e "fetch('http://django:8000/shipments/api/whatsapp/status/').then(async r=>console.log('django reachable, http',r.status)).catch(e=>console.log('ERR',e.message))" 2>&1
  echo -n "  does sidecar's DJANGO_SECRET equal the default committed in the repo?  "
  h=$(docker exec "$SC" sh -c 'printf %s "$DJANGO_SECRET" | sha256sum | cut -d" " -f1' 2>/dev/null); [ "$h" = "$DEFAULT_SECRET_SHA256" ] && { echo "YES"; flag "sidecar still uses the repo-default shared secret (public constant)"; } || echo "no (or not set)"
  echo -n "  env var DJANGO_SECRET present in sidecar? "; docker exec "$SC" sh -c '[ -n "$DJANGO_SECRET" ] && echo yes || echo NO (falls back to the default in index.js)'
  echo "--- sidecar -> WhatsApp / GitHub (needed for login and for fetchLatestBaileysVersion)"
  docker exec "$SC" node -e "
const net=require('net');const t=(h,p)=>new Promise(r=>{const s=net.connect({host:h,port:p,timeout:5000},()=>{console.log(h+':'+p,'TCP OK');s.destroy();r()});s.on('error',e=>{console.log(h+':'+p,'FAIL',e.code);r()});s.on('timeout',()=>{console.log(h+':'+p,'TIMEOUT');s.destroy();r()})});
(async()=>{await t('web.whatsapp.com',443);await t('g.whatsapp.net',443);await t('raw.githubusercontent.com',443);})()" 2>&1
fi
echo "--- host: clock + outbound"
N=2 run date -u
N=2 run curl -sS -o /dev/null -m 6 -w 'web.whatsapp.com https -> %{http_code}\n' https://web.whatsapp.com/

hr "5. Django container"
if [ -n "$DJ" ]; then
  echo "non-secret settings as the container sees them:"
  for v in DEBUG ALLOWED_HOSTS CSRF_TRUSTED_ORIGINS SECURE_SSL_REDIRECT SESSION_COOKIE_SECURE CSRF_COOKIE_SECURE WHATSAPP_SIDECAR_URL TZ; do
    printf '  %-26s = %s\n' "$v" "$(docker exec "$DJ" printenv $v 2>/dev/null || echo '<unset in container env>')"
  done
  echo "  CELERY_BROKER_URL host only = $(docker exec "$DJ" printenv CELERY_BROKER_URL 2>/dev/null | sed -E 's#^([a-z]+://)([^@/]*@)?([^/:]+).*#\1\3#' || echo '<unset>')"
  echo "  .env key NAMES visible to django: $(docker exec "$DJ" sh -c 'cut -d= -f1 /app/.env 2>/dev/null | grep -v "^#" | grep . | sort | tr "\n" " "')"
  docker exec "$DJ" sh -c 'grep -E "^(DEBUG|SECURE_SSL_REDIRECT|SESSION_COOKIE_SECURE|CSRF_COOKIE_SECURE)=" /app/.env 2>/dev/null' | sed 's/^/  .env: /'
  docker exec "$DJ" sh -c 'grep -qE "^(SECURE_SSL_REDIRECT|SESSION_COOKIE_SECURE|CSRF_COOKIE_SECURE)=False" /app/.env' 2>/dev/null && flag "secure-cookie / SSL-redirect flags are set to False in .env"
  echo "django, last 7 days of logs:"
  LOGD=$(docker logs --since 168h "$DJ" 2>&1)
  echo "  total lines: $(wc -l <<<"$LOGD")"
  echo "  gunicorn WORKER TIMEOUT : $(grep -c 'WORKER TIMEOUT' <<<"$LOGD")"
  echo "  worker killed / SIGKILL / OOM : $(grep -c -E 'SIGKILL|Out of memory|killed' <<<"$LOGD")"
  echo "  Booting worker (restarts)    : $(grep -c 'Booting worker' <<<"$LOGD")"
  echo "  Internal Server Error / Traceback: $(grep -c -E 'Internal Server Error|Traceback' <<<"$LOGD")"
  echo "  mentions of whatsapp/sidecar/ingest: $(grep -ciE 'whatsapp|sidecar|ingest' <<<"$LOGD")"
  echo "  top 10 404 paths (internet scanners show up here):"; grep 'Not Found:' <<<"$LOGD" | sed 's/.*Not Found: //' | sort | uniq -c | sort -rn | head -10 | sed 's/^/    /'
  [ "$(grep -c 'WORKER TIMEOUT' <<<"$LOGD")" -gt 0 ] && flag "gunicorn worker timeouts in the last 7 days"
  [ "$(grep -c -E 'Internal Server Error|Traceback' <<<"$LOGD")" -gt 0 ] && { echo "  distinct exception lines:"; grep -E '^[A-Za-z_.]+(Error|Exception):' <<<"$LOGD" | sort | uniq -c | sort -rn | head -10 | sed 's/^/    /'; }
  echo "--- user inside django container: $(docker exec "$DJ" id -un 2>&1) (root = no privilege drop)"
fi

hr "6. Database (SELECT/SHOW only)"
if [ -n "$DB" ]; then
  Q() { docker exec "$DB" sh -c "mysql -uroot -p\"\$MYSQL_ROOT_PASSWORD\" -N -B filatrucking -e \"$1\" 2>/dev/null"; }
  echo -n "server version: "; Q "SELECT VERSION()"
  echo "rows: whatsapp_message=$(Q 'SELECT COUNT(*) FROM shipments_whatsappmessage') whatsapp_log=$(Q 'SELECT COUNT(*) FROM shipments_whatsapplog') groups=$(Q 'SELECT COUNT(*) FROM shipments_whatsappgroup') shipments=$(Q 'SELECT COUNT(*) FROM shipments_shipment')"
  echo "whatsapp config row (NO qr column):"; Q "SELECT sidecar_status, auth_status, last_connection_time, last_error_time, LEFT(last_error,120) FROM shipments_whatsappconfig"
  echo "flagged messages in last 7 days: $(Q 'SELECT COUNT(*) FROM shipments_whatsappmessage WHERE is_flagged=1 AND received_at > NOW() - INTERVAL 7 DAY')   processed: $(Q 'SELECT COUNT(*) FROM shipments_whatsappmessage WHERE is_processed=1 AND received_at > NOW() - INTERVAL 7 DAY')"
  echo "last message received at: $(Q 'SELECT MAX(received_at) FROM shipments_whatsappmessage')   last shipment created: $(Q 'SELECT MAX(created_at) FROM shipments_shipment')"
  echo "whatsapp_log rows per day (last 5 days; huge numbers = reconnect-loop spam):"; Q "SELECT DATE(created_at), COUNT(*) FROM shipments_whatsapplog GROUP BY 1 ORDER BY 1 DESC LIMIT 5"
  echo "groups (name, active):"; Q "SELECT group_name, is_active FROM shipments_whatsappgroup LIMIT 10"
  echo "shipments by status:"; Q "SELECT status, COUNT(*) FROM shipments_shipment GROUP BY status"
  echo "DB size MB:"; Q "SELECT ROUND(SUM(data_length+index_length)/1024/1024,1) FROM information_schema.tables WHERE table_schema='filatrucking'"
  echo "mysql 8.0 end-of-life check: $(Q 'SELECT VERSION()')  (8.0 reached vendor EOL in April 2026)"
fi
echo "--- backups (names/dates only)"
{ crontab -l 2>/dev/null; ls /etc/cron.d 2>/dev/null; } | grep -iE 'dump|backup' || echo "no cron entry mentioning dump/backup"
find / -xdev -maxdepth 5 \( -name '*.sql' -o -name '*.sql.gz' -o -name '*backup*' \) -mtime -45 -not -path '*/proc/*' -not -path '*/node_modules/*' -not -path '/var/lib/docker/*' 2>/dev/null | head -5 | sed 's/^/  found: /'
find / -xdev -maxdepth 5 \( -name '*.sql' -o -name '*.sql.gz' \) -mtime -45 -not -path '/var/lib/docker/*' 2>/dev/null | grep -q . || flag "no recent MySQL dump found on the host (last 45 days): data only lives in one docker volume"

hr "7. Network exposure (what the internet can reach)"
echo "listening sockets of interest:"
ss -ltnH 2>/dev/null | awk '{print $4}' | grep -E ':(80|443|8888|3001|3306|6379|22)$' | sort | sed 's/^/  /'
ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE '^(0\.0\.0\.0|\[::\]|\*):(8888|3001)$' && flag "ports 8888 (django) and/or 3001 (sidecar) listen on all interfaces: reachable from the internet unless the OCI security list / firewall blocks them"
echo "host firewall (needs sudo; skipped if not available):"
sudo -n ufw status 2>/dev/null | head -12 || echo "  (no passwordless sudo: please run 'sudo ufw status; sudo iptables -S | head -30' yourself)"
echo "--- does nginx expose the sidecar API? (GET /sidecar/api/status via localhost; read-only)"
code=$(curl -sk -m 8 -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/sidecar/api/status"); echo "  https://$DOMAIN/sidecar/api/status -> HTTP $code"
[ "$code" = "200" ] && flag "nginx /sidecar/ proxies the UNAUTHENTICATED sidecar API to the internet (qr-code, logs, restart, DELETE auth)"
code=$(curl -s -m 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:3001/api/status); echo "  http://127.0.0.1:3001/api/status -> HTTP $code (host port 3001)"
code=$(curl -sk -m 8 -o /dev/null -w '%{http_code}' --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/shipments/api/whatsapp/qr-code/"); echo "  https://$DOMAIN/shipments/api/whatsapp/qr-code/ (no login) -> HTTP $code"
[ "$code" = "200" ] && flag "Django serves the WhatsApp pairing-QR endpoint without login"

hr "8. TLS certificate / nginx reload"
echo "certificate currently SERVED by nginx:"
echo | timeout 10 openssl s_client -connect 127.0.0.1:443 -servername "$DOMAIN" 2>/dev/null | openssl x509 -noout -subject -issuer -startdate -enddate 2>&1 | head -5
[ -n "$CB" ] && { echo "certificates on DISK (certbot):"; docker exec "$CB" certbot certificates 2>&1 | grep -E 'Certificate Name|Expiry|Domains' | head -6
  echo "renewal hooks configured:"; docker exec "$CB" sh -c 'ls /etc/letsencrypt/renewal-hooks/*/ 2>/dev/null; grep -H -E "deploy_hook|post_hook" /etc/letsencrypt/renewal/*.conf 2>/dev/null' | head -5
  docker exec "$CB" sh -c 'grep -q -E "deploy_hook|post_hook" /etc/letsencrypt/renewal/*.conf 2>/dev/null || ls /etc/letsencrypt/renewal-hooks/deploy/* >/dev/null 2>&1' || flag "no certbot deploy/post hook found: nginx will keep serving the OLD certificate after a renewal until it is reloaded (outage when the old one expires)"; }
echo "nginx config test (no reload): "; [ -n "$NG" ] && docker exec "$NG" nginx -t 2>&1 | head -3

hr "9. Image / base-image age"
for c in $DJ $SC $DB $NG; do [ -n "$c" ] && docker inspect "$c" --format '{{.Name}}: image {{.Config.Image}} created {{.Created}}'; done
echo "sidecar base: node:20-bullseye-slim (Node 20 EOL 2026-04-30, Debian 11 EOL 2026-08) | mysql:8.0 (EOL 2026-04) -> upgrade planned in TASKS.md"

hr "SUMMARY OF FLAGS"
if [ ${#FLAGS[@]} -eq 0 ]; then echo "(none raised)"; else for f in "${FLAGS[@]}"; do echo " * $f"; done; fi
echo
echo "Done. Nothing was changed. Please send me this whole output."
