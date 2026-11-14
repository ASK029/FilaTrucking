#!/bin/sh
# Start as root only long enough to fix ownership of the persistent session volume (volumes created by older,
# root-running versions of this image are root-owned), then drop to the unprivileged "node" user (QA-025).
set -e
mkdir -p /app/sidecar/auth_data
if [ "$(id -u)" = "0" ]; then
    chown -R node:node /app/sidecar/auth_data
    exec setpriv --reuid=node --regid=node --init-groups "$@"
fi
exec "$@"
