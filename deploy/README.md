# Operations runbook

All commands run on the server in the checkout directory (`~/FilaTrucking`).

## Backups (QA-027)

The `backup` service in `docker-compose.prod.yml` dumps the database one minute after it starts and then every 24 h
into `./backups/filatrucking-<UTC timestamp>.sql.gz`. A dump is only kept when it is complete (gzip test + the
`Dump completed` trailer); dumps older than `BACKUP_KEEP_DAYS` (default 14) are deleted.

```bash
# one-off backup right now
docker compose -f docker-compose.prod.yml run --rm backup once
ls -lh backups/

# watch it work
docker logs --tail 20 fila-backup
```

**A backup that only lives on the same server is not a backup.** Copy `./backups` somewhere else, e.g. OCI Object
Storage (`oci os object bulk-upload`) or `rclone sync backups remote:filatrucking-backups` from a nightly cron job.
Also back up `./FilaTrucking/media` (uploaded documents) and the `.env` file (without it the encrypted Motive/SMTP
values cannot be read again because `SECRET_KEY` is part of the key).

### Rehearse a restore (do this once, and after every major change)

This restores into a scratch database next to the real one and prints exact row counts; production is untouched.

```bash
docker compose -f docker-compose.prod.yml run --rm --no-deps \
  -e MYSQL_HOST=mysql -e MYSQL_ROOT_PASSWORD="$(grep ^MYSQL_ROOT_PASSWORD .env | cut -d= -f2-)" \
  --entrypoint /bin/sh backup /deploy/restore_mysql.sh /backups/<file>.sql.gz
# compare with the live counts, then drop the scratch database:
docker compose -f docker-compose.prod.yml exec mysql sh -c \
  'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "DROP DATABASE filatrucking_restore_test"'
```

### Real restore after a disaster

1. `docker compose -f docker-compose.prod.yml stop django celery-worker celery-beat sidecar`
2. `... run --rm --no-deps -e MYSQL_HOST=mysql -e MYSQL_ROOT_PASSWORD=... --entrypoint /bin/sh backup /deploy/restore_mysql.sh /backups/<file>.sql.gz filatrucking`
3. `docker compose -f docker-compose.prod.yml up -d`

## Upgrading MySQL 8.0 to 8.4 LTS (QA-028, not done automatically)

MySQL 8.0 reached end of life in April 2026. 8.4 can upgrade an 8.0 data directory in place, but **cannot go back**,
so rehearse it first:

1. `docker compose -f docker-compose.prod.yml run --rm backup once` and copy the dump off the server.
2. On a scratch machine (or a second compose project): start `mysql:8.4`, restore the dump into it with
   `restore_mysql.sh`, point a copy of the app at it and click through the main pages / run `python manage.py test`.
3. Only then change `image: mysql:8.0` to `mysql:8.4` (both the `mysql` and `backup` services) in a maintenance
   window, with the dump from step 1 as the way back.

## Certificates (QA-026)

`certbot` renews automatically; `nginx` reloads every 6 h to pick the new files up. Check the served certificate:

```bash
echo | openssl s_client -connect filatruckingco.duckdns.org:443 -servername filatruckingco.duckdns.org 2>/dev/null | openssl x509 -noout -dates
```
