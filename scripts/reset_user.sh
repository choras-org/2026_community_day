#!/usr/bin/env bash
# Wipe participant instances back to a fresh start: stop their running
# simulations, delete their database and uploads, and start the stack again,
# which recreates the database and the example project. Login, password and
# secrets stay the same. Export first if the data matters (export_results.sh).
#
#   sudo ./reset_user.sh user16 user17     # asks for confirmation
#   sudo ./reset_user.sh --yes user16      # no question
set -euo pipefail

users_dir=${CHORAS_USERS_DIR:-/opt/choras/users}
yes=0
[ "${1:-}" = "--yes" ] && { yes=1; shift; }
[ $# -gt 0 ] || { echo "usage: $0 [--yes] userNN [userNN ...]" >&2; exit 2; }

for user in "$@"; do
  [ -f "$users_dir/$user/compose.yml" ] || { echo "$user: no instance in $users_dir/$user" >&2; exit 1; }
done

if [ $yes -eq 0 ]; then
  read -r -p "Delete all projects, models and results of: $*? [y/N] " answer
  [ "$answer" = "y" ] || { echo "nothing changed"; exit 1; }
fi

for user in "$@"; do
  dir="$users_dir/$user"
  # Simulation containers are named choras-<user>-<method>-simulation-<id> (backend #153).
  sims=$(docker ps -aq --filter "name=^choras-$user-")
  [ -z "$sims" ] || docker rm -f $sims > /dev/null
  docker compose -p "choras_$user" -f "$dir/compose.yml" down -v
  mkdir -p "$dir/uploads"
  find "$dir/uploads" -mindepth 1 -delete
  docker compose -p "choras_$user" -f "$dir/compose.yml" up -d
  echo "$user: reset; the backend recreates the database and the example project on startup"
done
