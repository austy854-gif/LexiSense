#!/usr/bin/env bash
# Temporary Atlas IP access-list entry for the backup runner.
#
#   usage: atlas-access-list.sh exists|add|wait|remove <ipv4>
#
#   exists  exit 0 if <ipv4>/32 is already on the project access list,
#           1 if not (404), 2 on any other error.
#   add     POST <ipv4>/32 with a comment and a deleteAfterDate backstop.
#   wait    poll .../accessList/<ipv4>%2F32/status until ACTIVE.
#   remove  DELETE .../accessList/<ipv4>%2F32 (404 counts as success).
#
# Required env: ATLAS_PUBLIC_KEY, ATLAS_PRIVATE_KEY, ATLAS_PROJECT_ID
# Optional env:
#   ATLAS_COMMENT        default gha-backup-${GITHUB_RUN_ID:-local}
#   ATLAS_TTL_MINUTES    default 60 (deleteAfterDate = now + TTL)
#   ATLAS_WAIT_SECONDS   default 300
#   ATLAS_DRY_RUN=1      print the request instead of sending it (no network)
#   ATLAS_BASE_URL       override the API base (local mock tests only)
#
# Uses Atlas Admin API v2 with HTTP digest auth. The API key is handed to
# curl through a config on a file descriptor, so it never appears in argv or
# in logs. Response bodies are reduced to errorCode/detail before printing.
set -euo pipefail

ATLAS_BASE_URL="${ATLAS_BASE_URL:-https://cloud.mongodb.com/api/atlas/v2}"  # override only for local tests
ATLAS_ACCEPT="application/vnd.atlas.2023-01-01+json"

cmd="${1:?usage: atlas-access-list.sh exists|add|wait|remove <ipv4>}"
ip="${2:?usage: atlas-access-list.sh exists|add|wait|remove <ipv4>}"

if [[ ! "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "atlas: refusing a value that is not an IPv4 address" >&2
  exit 2
fi
IFS=. read -r o1 o2 o3 o4 <<<"$ip"
for o in "$o1" "$o2" "$o3" "$o4"; do
  if ((10#$o > 255)); then
    echo "atlas: invalid IPv4 octet" >&2
    exit 2
  fi
done

: "${ATLAS_PROJECT_ID:?ATLAS_PROJECT_ID is required}"
if [[ ! "$ATLAS_PROJECT_ID" =~ ^[a-f0-9]{24}$ ]]; then
  echo "atlas: ATLAS_PROJECT_ID must be a 24-hex project id" >&2
  exit 2
fi
if [[ "${ATLAS_DRY_RUN:-0}" != 1 ]]; then
  : "${ATLAS_PUBLIC_KEY:?ATLAS_PUBLIC_KEY is required}"
  : "${ATLAS_PRIVATE_KEY:?ATLAS_PRIVATE_KEY is required}"
fi

entry="${ip}%2F32"
body_file="$(mktemp)"
trap 'rm -f "$body_file"' EXIT

# api METHOD PATH [JSON]  -> prints HTTP status code; body goes to $body_file
api() {
  local method="$1" path="$2" data="${3:-}"
  local url="${ATLAS_BASE_URL}/groups/${ATLAS_PROJECT_ID}${path}"
  if [[ "${ATLAS_DRY_RUN:-0}" == 1 ]]; then
    echo "DRY-RUN ${method} ${url} ${data}" >&2
    case "$method" in
      GET) printf '{"STATUS":"ACTIVE"}' >"$body_file"; echo "${ATLAS_DRY_RUN_GET_CODE:-200}" ;;
      POST) printf '{}' >"$body_file"; echo 201 ;;
      DELETE) printf '' >"$body_file"; echo "${ATLAS_DRY_RUN_DELETE_CODE:-204}" ;;
    esac
    return 0
  fi
  local args=(
    --silent --show-error
    --max-time 30
    --digest
    --header "Accept: ${ATLAS_ACCEPT}"
    --output "$body_file"
    --write-out '%{http_code}'
    --request "$method"
  )
  if [[ -n "$data" ]]; then
    args+=(--header "Content-Type: application/json" --data "$data")
  fi
  # The process substitution must sit on the curl command itself so its fd is
  # still open when curl reads it. printf is a builtin: no argv exposure.
  local code
  code="$(curl "${args[@]}" \
    --config <(printf 'user = "%s:%s"\n' "$ATLAS_PUBLIC_KEY" "$ATLAS_PRIVATE_KEY") \
    "$url")" || true
  echo "${code:-000}"
}

explain() {
  # Print only the error code / detail from an Atlas error body.
  jq -r '"errorCode=\(.errorCode // "n/a") detail=\(.detail // "n/a")"' "$body_file" 2>/dev/null ||
    echo "(no parsable error body)"
}

case "$cmd" in
  exists)
    code="$(api GET "/accessList/${entry}")"
    case "$code" in
      200) exit 0 ;;
      404) exit 1 ;;
      *) echo "atlas exists: HTTP ${code} $(explain)" >&2; exit 2 ;;
    esac
    ;;

  add)
    ttl="${ATLAS_TTL_MINUTES:-60}"
    comment="${ATLAS_COMMENT:-gha-backup-${GITHUB_RUN_ID:-local}}"
    delete_after="$(date -u -d "+${ttl} minutes" +%Y-%m-%dT%H:%M:%SZ)"
    payload="$(jq -cn --arg cidr "${ip}/32" --arg c "${comment:0:80}" --arg d "$delete_after" \
      '[{cidrBlock: $cidr, comment: $c, deleteAfterDate: $d}]')"
    code="$(api POST "/accessList" "$payload")"
    case "$code" in
      200 | 201) echo "atlas: temporary access-list entry created (expires ${delete_after})" ;;
      *) echo "atlas add: HTTP ${code} $(explain)" >&2; exit 1 ;;
    esac
    ;;

  wait)
    deadline=$((SECONDS + ${ATLAS_WAIT_SECONDS:-300}))
    while :; do
      code="$(api GET "/accessList/${entry}/status")"
      status=""
      if [[ "$code" == 200 ]]; then
        status="$(jq -r '.STATUS // empty' "$body_file" 2>/dev/null || true)"
      fi
      case "$status" in
        ACTIVE) echo "atlas: access-list entry is ACTIVE"; exit 0 ;;
        FAILED) echo "atlas: access-list entry FAILED" >&2; exit 1 ;;
      esac
      if ((SECONDS >= deadline)); then
        echo "atlas wait: timed out (last HTTP ${code}, status '${status:-none}')" >&2
        exit 1
      fi
      sleep 5
    done
    ;;

  remove)
    for attempt in 1 2 3; do
      code="$(api DELETE "/accessList/${entry}")"
      case "$code" in
        200 | 202 | 204) echo "atlas: temporary access-list entry removed"; exit 0 ;;
        404) echo "atlas: access-list entry already gone (404)"; exit 0 ;;
        *) echo "atlas remove: attempt ${attempt} HTTP ${code} $(explain)" >&2 ;;
      esac
      sleep $((attempt * 5))
    done
    echo "atlas remove: giving up; deleteAfterDate will expire the entry" >&2
    exit 1
    ;;

  *)
    echo "usage: atlas-access-list.sh exists|add|wait|remove <ipv4>" >&2
    exit 2
    ;;
esac
