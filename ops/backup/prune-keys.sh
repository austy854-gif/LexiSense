#!/usr/bin/env bash
# Retention helper for the daily MongoDB backup.
#
# Reads R2 object keys (one per line) on stdin and prints the keys that should be
# DELETED so that only the newest <keep> distinct dates under <prefix>/ remain.
#
#   usage: prune-keys.sh <daily|weekly> <keep>
#
# Only keys that exactly match the backup layout are considered:
#   <prefix>/YYYY-MM-DD.archive.gz.age
#   <prefix>/YYYY-MM-DD.manifest.txt.age
# Anything else in the bucket is ignored and never printed (never deleted).
# Pure function: no network access. Tested by prune-keys.test.sh.
set -euo pipefail

prefix="${1:?usage: prune-keys.sh <daily|weekly> <keep>}"
keep="${2:?usage: prune-keys.sh <daily|weekly> <keep>}"

if [[ ! "$prefix" =~ ^(daily|weekly)$ ]]; then
  echo "prune-keys: prefix must be daily or weekly" >&2
  exit 2
fi
if [[ ! "$keep" =~ ^[1-9][0-9]*$ ]]; then
  echo "prune-keys: keep must be an integer >= 1" >&2
  exit 2
fi

re="^${prefix}/([0-9]{4}-[0-9]{2}-[0-9]{2})\.(archive\.gz\.age|manifest\.txt\.age)$"

keys=()
while IFS= read -r key || [[ -n "$key" ]]; do
  key="${key%$'\r'}"
  if [[ "$key" =~ $re ]]; then
    keys+=("$key")
  fi
done

if [[ ${#keys[@]} -eq 0 ]]; then
  exit 0
fi

# Newest <keep> distinct dates (ISO dates sort lexically).
mapfile -t keep_dates < <(
  printf '%s\n' "${keys[@]}" |
    sed -E "s#^${prefix}/([0-9]{4}-[0-9]{2}-[0-9]{2})\..*#\1#" |
    sort -u -r |
    head -n "$keep"
)

for key in "${keys[@]}"; do
  [[ "$key" =~ $re ]]
  d="${BASH_REMATCH[1]}"
  retain=false
  for k in "${keep_dates[@]}"; do
    if [[ "$d" == "$k" ]]; then
      retain=true
      break
    fi
  done
  if [[ "$retain" == false ]]; then
    printf '%s\n' "$key"
  fi
done | sort
