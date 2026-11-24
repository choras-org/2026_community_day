#!/usr/bin/env bash
# Snapshot of the workshop server: load, memory, disk, instances that aren't
# running, and the simulations running right now.
#
#   sudo ./status.sh                 # once
#   watch -n 10 sudo ./status.sh     # refresh every 10 s
set -uo pipefail

users_dir=${CHORAS_USERS_DIR:-/opt/choras/users}
cores=$(nproc)
read -r l1 l5 l15 _ < /proc/loadavg
warn=$(awk -v l="$l1" -v c="$cores" 'BEGIN { if (l > c) print "  << more load than cores" }')
echo "load      $l1 / $l5 / $l15 (1/5/15 min) on $cores cores$warn"
awk '/^MemTotal/ {t=$2} /^MemAvailable/ {a=$2} END {printf "memory    %.0f of %.0f GB used\n", (t-a)/1048576, t/1048576}' /proc/meminfo
df -h / | awk 'NR==2 {print "disk /    " $3 " of " $2 " used (" $5 ")"}'

total=0
down=()
for dir in "$users_dir"/*/; do
  [ -f "$dir/compose.yml" ] || continue
  user=$(basename "$dir")
  total=$((total + 1))
  for service in backend db_service; do
    state=$(docker inspect -f '{{.State.Status}}' "choras_$user-$service-1" 2>/dev/null) || state=missing
    [ "$state" = running ] || down+=("$user/$service:$state")
  done
done
echo "instances $total, not running: ${down[*]:-none}"

# Simulation containers are named choras-<login>-<method>-simulation-<id> (backend #153).
sims=$(docker ps -q --filter name=-simulation-)
echo "simulations running: $(printf '%s' "$sims" | grep -c .)"
if [ -n "$sims" ]; then
  # shellcheck disable=SC2086
  docker stats --no-stream --format '  {{.Name}}  CPU {{.CPUPerc}}  mem {{.MemUsage}}' $sims
fi
