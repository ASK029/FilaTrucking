# Deploying the `qa-fixes` branch to the server (Phase 2)

Nothing in this document has been run against production. Every command is for **you** to run on the Oracle VPS
(`ubuntu@filatruckingco`, checkout in `~/FilaTrucking`). It is ordered so that you can stop after any step; steps 1 to 3
change nothing.

The branch contains one commit per task (`QA-001` ... `QA-049`). Production-affecting changes are in the compose file,
nginx, the Dockerfiles, `.env` variables and 10 database migrations.

> **Time estimate:** 30 to 45 minutes, including the WhatsApp re-pairing. Expect about 2 minutes of downtime while the
> containers are recreated. Do it when the dispatch group is quiet.

---

## 0. Decide first: the repository was public

You told me it has been shared publicly. Treat **everything that was ever committed as compromised**, including what is
no longer in the latest commit. That means:

| What leaked | Where it was | What to do (step 4 below) |
|---|---|---|
| WhatsApp shared secret (`fila_secret_2026`) | code, compose files, `.env.example` | new random `WHATSAPP_INGEST_SECRET` (the old one is now refused by the app) |
| MySQL **root** password and a default Django `SECRET_KEY` | `settings.py`, `.env.example` | change the DB passwords; change `SECRET_KEY` only if the server still uses the committed default (see 4b) |
| Business data: driver timesheet PDF, vehicle screenshots, 2025 statements, IFTA sheet, GoMotive vehicle dump | tracked files | cannot be un-published; purge from git history (step 9) and consider what is in them |
| Your admin password | not committed, but the login had no brute-force protection | change it after the deploy (`/auth/password_change/`) |

---

## 1. Read-only checks (nothing changes)

```bash
cd ~/FilaTrucking
git status --short                      # should be clean; stash or commit local edits first
docker compose -f docker-compose.prod.yml ps
df -h / ; free -m
```

## 2. Back everything up FIRST (the old stack has no backups)

```bash
STAMP=$(date +%F-%H%M)
mkdir -p ~/backup-$STAMP
# 2a. database (works with the CURRENT, old compose file)
docker exec fila-mysql sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --no-tablespaces --routines --triggers filatrucking' | gzip > ~/backup-$STAMP/db.sql.gz
gzip -t ~/backup-$STAMP/db.sql.gz && ls -lh ~/backup-$STAMP/db.sql.gz
# 2b. uploaded files, env file, WhatsApp session volume (it is empty today, but keep the habit)
cp -a FilaTrucking/media ~/backup-$STAMP/media
cp -a .env ~/backup-$STAMP/dot-env
docker run --rm -v filatrucking_auth_data:/d -v ~/backup-$STAMP:/b alpine tar czf /b/auth_data.tgz -C /d .
```

Copy `~/backup-$STAMP` somewhere that is **not** this server (your laptop is fine).

## 3. Get the code

```bash
git fetch origin
git checkout qa-fixes && git pull          # or: merge qa-fixes into main on GitHub, then 'git checkout main && git pull'
```

> **Warning:** this commit stops tracking `FilaTrucking/media/*` (QA-024). `git pull` therefore **deletes** the four
> tracked media files from the working tree (a driver timesheet and three vehicle screenshots). Restore them from the
> backup right away:
> ```bash
> cp -an ~/backup-$STAMP/media/. FilaTrucking/media/
> ```
> Files uploaded through the app later were never tracked and are not affected.

## 4. Edit `.env`

Back it up first (done in step 2), then change **only** what is listed. Never paste the values anywhere.

```bash
nano .env
```

| Variable | Action |
|---|---|
| `WHATSAPP_INGEST_SECRET` | **Add.** New random value: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Compose gives the same value to Django and to the sidecar. |
| `DJANGO_SECRET` | **Delete** (replaced by the line above). |
| `APP_UID`, `APP_GID` | **Add**: the output of `id -u` and `id -g` (the Django containers no longer run as root). |
| `SECURE_SSL_REDIRECT` | **Delete the `False` line** (or set `True`); nginx forwards `X-Forwarded-Proto`, so it is safe now. |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | leave `True`. |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | keep; remove hosts you do not use (e.g. the raw IP, `web`, `django:8000`). Docker healthchecks call `127.0.0.1`, so keep `127.0.0.1`. |
| `ENABLE_RECURRING_INVOICES` | do **not** set it yet (auto-created invoices are opt-in, QA-008). |
| `TIME_ZONE` | not needed (default `America/Chicago`). |

### 4b. `SECRET_KEY` and the database passwords

* `SECRET_KEY`: if the server's value is the placeholder from `.env.example` or the old committed default, replace it
  (`python3 -c "import secrets; print(secrets.token_urlsafe(64))"`). **Side effect:** the Motive API key and the SMTP
  password stored encrypted in *System Settings* can no longer be decrypted: re-enter both on `/settings/system/`
  afterwards, and everyone is logged out once. If you are not sure, rotate: it is only two values to re-type.
* MySQL passwords are only read by MySQL when the data directory is first created, so changing `.env` is **not**
  enough. After the new stack is up (step 6):
  ```bash
  docker exec -it fila-mysql mysql -uroot -p        # old root password
  ALTER USER 'root'@'localhost' IDENTIFIED BY 'NEW-ROOT-PASSWORD';
  ALTER USER 'root'@'%'         IDENTIFIED BY 'NEW-ROOT-PASSWORD';   -- error "does not exist" is fine
  ALTER USER 'filatrucking'@'%' IDENTIFIED BY 'NEW-APP-PASSWORD';
  ```
  then set `MYSQL_ROOT_PASSWORD` / `MYSQL_PASSWORD` in `.env` to the new values and run
  `docker compose -f docker-compose.prod.yml up -d` again.

## 5. Check, then apply

```bash
mkdir -p backups FilaTrucking/staticfiles
docker compose -f docker-compose.prod.yml config -q && echo "compose file OK"   # fails with a clear message if a variable is missing
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d --remove-orphans
```

The old `staticfiles` named volume stays on disk, unused (`docker volume rm filatrucking_staticfiles` later).
**Run the migrations** (10 new ones; verified on MySQL 8.0.45 with seeded data; the stack does not run them for you):

```bash
docker compose -f docker-compose.prod.yml exec django python manage.py migrate
docker compose -f docker-compose.prod.yml exec django python manage.py check --deploy    # expect: no issues
```

## 6. Verify (everything should be "yes")

```bash
docker compose -f docker-compose.prod.yml ps                      # every service Up; django, celery-worker, nginx, mysql, redis, sidecar "healthy"
ss -ltn | awk '{print $4}' | grep -E ':(22|80|443|3001|8888)$'     # only :22 :80 :443 (3001 and 8888 are closed now)
curl -sI https://filatruckingco.duckdns.org/auth/login/ | head -1  # HTTP/2 200
curl -s -o /dev/null -w '%{http_code}\n' https://filatruckingco.duckdns.org/sidecar/api/status     # 404 (nginx no longer proxies the sidecar)
curl -s -o /dev/null -w '%{http_code}\n' https://filatruckingco.duckdns.org/shipments/api/whatsapp/qr-code/   # 401 without login
docker logs --tail 30 fila-celery-beat                             # "beat: Starting..."
docker logs --tail 5 fila-backup                                   # first dump appears about a minute after start
```

In the browser: log in, open one **shipment** (no more 500), **download an invoice PDF** (WeasyPrint was upgraded
68.1 -> 70.0 and PDFs could not be tested on my machine), open `/invoices/create/` and click *Add Manual Item*,
check *Logout* works.

## 7. Bring WhatsApp back (QA-001)

1. Open `https://filatruckingco.duckdns.org/shipments/settings/whatsapp/` while logged in. A QR code should appear within
   about 10 seconds (status "authenticating"). If it shows a red 405 error instead, run
   `docker logs --tail 40 fila-whatsapp-sidecar` and send me the output.
2. On the dedicated phone: *WhatsApp > Linked devices > Link a device* and scan it. Status becomes **connected**.
3. Click **Sync Groups**. New groups are **disabled by default** (QA-020): tick only the dispatch group.
4. Send a test shipment from your test number to that group:
   ```
   TEST001
   CNTR12345
   SEAL99999
   ```
   The bot answers "Shipment recorded" and a `pending_review` shipment appears. Any other chat in the group gets no reply.
5. Leave it 24 hours and check `docker logs fila-whatsapp-sidecar | grep -c " 405 "` is `0`.

## 8. After the deploy

* Change the admin password; re-enter the Motive key and SMTP password if you rotated `SECRET_KEY`.
* **Firewall (QA-025):** Ubuntu `ufw` was inactive. In the OCI console check that the VCN security list allows ingress only for
  22, 80 and 443. If you also want the host firewall (allow SSH first or you lock yourself out!):
  `sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable`
  (Docker publishes ports through iptables and bypasses ufw, which is why the compose file no longer publishes 8888/3001.)
* **Off-server backups (QA-027):** copy `~/FilaTrucking/backups` and `FilaTrucking/media` to OCI Object Storage or another
  machine every night (see `deploy/README.md`) and rehearse one restore.
* Look at `/admin/shipments/financialstatement/` on the 2nd of next month: the monthly statement task should have stored last month.
* If you want recurring invoices: set `ENABLE_RECURRING_INVOICES=True` and `docker compose ... up -d` (it creates *draft* invoices
  and flips the shipments to "invoiced"; QA-042 explains what is still missing).
* Statements: historical imported "driver pay" rows moved from the *Truck* column to the driver-pay section (QA-037);
  totals are unchanged.

## 9. The public history (you decide; I did not touch it)

Removing the data from the latest commit does not remove it from history. To purge the committed secrets and business
data from **all** history you must rewrite it and force-push, which invalidates every clone and open PR, so I did not do it:

```bash
pip install git-filter-repo
git clone --mirror git@github.com:<you>/FilaTrucking.git fila-mirror.git && cd fila-mirror.git
git filter-repo --invert-paths \
  --path FilaTrucking/media --path "docs/2025 STATMENTS - 2025 ALL.csv" --path "docs/2025 STATMENTS - JAN.csv" \
  --path "docs/2025 STATMENTS.xlsx" --path "docs/IFTA2025 2ND.xlsx" --path gomotive_resp.json --path sidecar/node_modules
git push --force --mirror
```

Even then, assume copies exist (forks, caches): the rotation in steps 0 and 4 is what actually protects you.

## 10. Rolling back

```bash
docker compose -f docker-compose.prod.yml down
git checkout <previous commit>             # git log shows it (the old state was 58fb468)
gunzip -c ~/backup-$STAMP/db.sql.gz | docker exec -i fila-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" filatrucking'   # only needed if data changed
cp ~/backup-$STAMP/dot-env .env
docker compose -f docker-compose.prod.yml up -d
```

Migrations 0010-0015 (shipments), 0012-0014 (vehicles), 0005 (drivers) add/alter columns; the old code ignores the new columns, so
rolling back the code without rolling back the database also works.
