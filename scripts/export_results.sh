#!/usr/bin/env bash
# Bundle each participant's results: one <user>-<date>.tar.gz with the uploads
# directory (models, results JSON, .wav, .xlsx) and a pg_dump of the instance's
# database (projects, simulations), so the projects can be restored elsewhere.
#
#   sudo ./export_results.sh                  # all instances in /opt/choras/users
#   sudo ./export_results.sh user01 user02    # only these
#
# Archives go to $CHORAS_EXPORT_DIR (default /opt/choras/exports) and are owned
# by the user who ran sudo, so they can be copied off with scp.
set -euo pipefail

users_dir=${CHORAS_USERS_DIR:-/opt/choras/users}
out_dir=${CHORAS_EXPORT_DIR:-/opt/choras/exports}
stamp=$(date +%Y%m%d-%H%M)

if [ $# -gt 0 ]; then
  users=("$@")
else
  users=()
  for d in "$users_dir"/*/; do
    [ -f "$d/compose.yml" ] && users+=("$(basename "$d")")
  done
fi
[ ${#users[@]} -gt 0 ] || { echo "no instances found in $users_dir" >&2; exit 1; }

mkdir -p "$out_dir"
failed=0
for user in "${users[@]}"; do
  dir="$users_dir/$user"
  tmp=$(mktemp -d)
  # Credentials come from the container's own environment (secrets.env).
  if docker compose -p "choras_$user" -f "$dir/compose.yml" exec -T db_service \
      sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$tmp/database.sql" \
    && tar -czf "$out_dir/$user-$stamp.tar.gz" -C "$tmp" database.sql -C "$dir" uploads; then
    echo "$user: $out_dir/$user-$stamp.tar.gz ($(du -h "$out_dir/$user-$stamp.tar.gz" | cut -f1))"
  else
    echo "$user: export failed" >&2
    rm -f "$out_dir/$user-$stamp.tar.gz"
    failed=1
  fi
  rm -rf "$tmp"
done

[ -n "${SUDO_USER:-}" ] && chown -R "$SUDO_USER" "$out_dir"
exit $failed
