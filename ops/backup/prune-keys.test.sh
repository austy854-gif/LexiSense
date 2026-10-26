#!/usr/bin/env bash
# Offline tests for prune-keys.sh (fake key lists; no network).
#   run: ops/backup/prune-keys.test.sh
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
prune="${here}/prune-keys.sh"
fail=0

check() {
  local name="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    echo "ok   - ${name}"
  else
    echo "FAIL - ${name}"
    diff <(printf '%s\n' "$expected") <(printf '%s\n' "$actual") || true
    fail=1
  fi
}

days() { # days <prefix> <first-date> <count>  -> archive + manifest keys, shuffled
  local p="$1" start="$2" n="$3" i d
  for ((i = 0; i < n; i++)); do
    d="$(date -u -d "${start} +${i} days" +%F)"
    echo "${p}/${d}.archive.gz.age"
    echo "${p}/${d}.manifest.txt.age"
  done | shuf
}

# 1. 10 daily backups -> delete the 3 oldest dates (both files each).
out="$(days daily 2026-10-01 10 | "$prune" daily 7)"
check "daily keeps newest 7 of 10" "$(printf '%s\n' \
  daily/2026-10-01.archive.gz.age daily/2026-10-01.manifest.txt.age \
  daily/2026-10-02.archive.gz.age daily/2026-10-02.manifest.txt.age \
  daily/2026-10-03.archive.gz.age daily/2026-10-03.manifest.txt.age)" "$out"

# 2. Fewer than keep -> nothing.
out="$(days daily 2026-10-01 5 | "$prune" daily 7)"
check "daily with 5 objects deletes nothing" "" "$out"

# 3. Exactly keep -> nothing.
out="$(days daily 2026-10-01 7 | "$prune" daily 7)"
check "daily with exactly 7 deletes nothing" "" "$out"

# 4. Weekly: 6 Sundays -> delete the 2 oldest.
weekly="$(for d in 2026-09-06 2026-09-13 2026-09-20 2026-09-27 2026-10-04 2026-10-11; do
  echo "weekly/${d}.archive.gz.age"; echo "weekly/${d}.manifest.txt.age"; done | shuf)"
out="$(printf '%s\n' "$weekly" | "$prune" weekly 4)"
check "weekly keeps newest 4 of 6" "$(printf '%s\n' \
  weekly/2026-09-06.archive.gz.age weekly/2026-09-06.manifest.txt.age \
  weekly/2026-09-13.archive.gz.age weekly/2026-09-13.manifest.txt.age)" "$out"

# 5. Foreign / malformed keys are never deleted; other prefixes ignored.
out="$( { days daily 2026-10-01 9
  printf '%s\n' daily/README.txt daily/2020-01-01.archive.gz daily/2020-01-01.archive.gz.age.bak \
    daily/sub/2020-01-01.archive.gz.age weekly/2020-01-01.archive.gz.age \
    "daily/2020-01-01.archive.gz.age " other/2020-01-01.archive.gz.age; } | "$prune" daily 7)"
check "foreign keys untouched" "$(printf '%s\n' \
  daily/2026-10-01.archive.gz.age daily/2026-10-01.manifest.txt.age \
  daily/2026-10-02.archive.gz.age daily/2026-10-02.manifest.txt.age)" "$out"

# 6. A date with only an archive (manifest missing) still counts as a date.
out="$(printf '%s\n' daily/2026-10-0{1..9}.archive.gz.age | "$prune" daily 7)"
check "archive-only dates" "$(printf '%s\n' daily/2026-10-01.archive.gz.age daily/2026-10-02.archive.gz.age)" "$out"

# 7. Empty input, and the aws "None" sentinel after the workflow's grep filter.
out="$(printf '' | "$prune" daily 7)"
check "empty input" "" "$out"
out="$(printf 'None\n' | grep -v -x None | "$prune" daily 7 || true)"
check "None sentinel" "" "$out"

# 8. CRLF and no trailing newline.
out="$(printf 'daily/2026-10-01.archive.gz.age\r\ndaily/2026-10-02.archive.gz.age' | "$prune" daily 1)"
check "CRLF / no trailing newline" "daily/2026-10-01.archive.gz.age" "$out"

# 9. Year boundary sorts correctly.
out="$(printf '%s\n' daily/2026-12-30.archive.gz.age daily/2026-12-31.archive.gz.age daily/2027-01-01.archive.gz.age | "$prune" daily 2)"
check "year boundary" "daily/2026-12-30.archive.gz.age" "$out"

# 10. Bad arguments are rejected.
for args in "daily 0" "daily -1" "daily x" "monthly 3" "../daily 3"; do
  # shellcheck disable=SC2086
  if "$prune" $args </dev/null >/dev/null 2>&1; then
    echo "FAIL - rejects args '${args}'"; fail=1
  else
    echo "ok   - rejects args '${args}'"
  fi
done

# 11. Simulate 60 days of the daily+weekly schedule end to end.
store=""
for ((i = 0; i < 60; i++)); do
  d="$(date -u -d "2026-08-01 +${i} days" +%F)"
  store+="daily/${d}.archive.gz.age"$'\n'"daily/${d}.manifest.txt.age"$'\n'
  if [[ "$(date -u -d "$d" +%u)" == 7 ]]; then
    store+="weekly/${d}.archive.gz.age"$'\n'"weekly/${d}.manifest.txt.age"$'\n'
  fi
  for spec in daily:7 weekly:4; do
    p="${spec%%:*}"; k="${spec##*:}"
    doomed="$(printf '%s' "$store" | grep "^${p}/" | "$prune" "$p" "$k" || true)"
    if [[ -n "$doomed" ]]; then
      store="$(printf '%s' "$store" | grep -v -x -F -f <(printf '%s\n' "$doomed"))"$'\n'
    fi
  done
done
nd="$(printf '%s' "$store" | grep -c '^daily/.*archive' || true)"
nw="$(printf '%s' "$store" | grep -c '^weekly/.*archive' || true)"
newest_daily="$(printf '%s' "$store" | grep '^daily/.*archive' | sort | tail -n 1)"
oldest_weekly="$(printf '%s' "$store" | grep '^weekly/.*archive' | sort | head -n 1)"
check "60-day sim: 7 daily remain" 7 "$nd"
check "60-day sim: 4 weekly remain" 4 "$nw"
check "60-day sim: newest daily" "daily/2026-09-29.archive.gz.age" "$newest_daily"
check "60-day sim: oldest weekly" "weekly/2026-09-06.archive.gz.age" "$oldest_weekly"

if ((fail)); then
  echo "prune-keys tests FAILED"
  exit 1
fi
echo "prune-keys tests passed"
