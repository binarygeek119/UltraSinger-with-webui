#!/bin/sh
set -e
# Fix bind-mount ownership, then drop from root to uid 1000.
uid="${PUID:-1000}"
gid="${PGID:-1000}"

for d in /app/UltraSinger/data /app/UltraSinger/.cache /export/yarg /export/ultrastar; do
  mkdir -p "$d"
  chown -R "$uid:$gid" "$d" 2>/dev/null || true
done

if [ "$(id -u)" = "0" ] && command -v setpriv >/dev/null 2>&1; then
  exec setpriv --reuid="$uid" --regid="$gid" --clear-groups -- "$@"
fi

exec "$@"
