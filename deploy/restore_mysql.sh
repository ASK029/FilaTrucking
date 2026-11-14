#!/bin/sh
# Restore a backup made by backup_mysql.sh into a MySQL database.
#
#   deploy/restore_mysql.sh /backups/filatrucking-20260101-010000.sql.gz [target_database]
#
# The default target is "filatrucking_restore_test": a scratch database next to the real one, so a restore can be
# rehearsed (and its row counts compared) without touching production data. To really restore, pass the live
# database name AFTER stopping django/celery/sidecar.
# Needs: MYSQL_HOST, MYSQL_ROOT_PASSWORD (creating a database needs more than the application user may do).
set -eu
file="${1:?usage: restore_mysql.sh <backup.sql.gz> [target_database]}"
target="${2:-filatrucking_restore_test}"
host="${MYSQL_HOST:-mysql}"

gzip -t "$file"
MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -h "$host" -u root -e "DROP DATABASE IF EXISTS \`$target\`; CREATE DATABASE \`$target\` CHARACTER SET utf8mb4;"
gunzip -c "$file" | MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -h "$host" -u root "$target"
echo "restored $file into $target. Exact row counts:"
for table in $(MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -h "$host" -u root -N -e "SHOW TABLES FROM \`$target\`"); do
    count="$(MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -h "$host" -u root -N -e "SELECT COUNT(*) FROM \`$target\`.\`$table\`")"
    echo "$table: $count"
done
