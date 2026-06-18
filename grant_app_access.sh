#!/usr/bin/env bash
# =============================================================================
# grant_app_access.sh — post-deploy helper.
#
# Resolves the deployed app's service principal (created by `bundle deploy`) and
# applies the grants from setup.sql for it: read on the system schemas + USE
# CATALOG/BROWSE on each catalog you want explorable. Run once, as a metastore
# admin, after deploying.
#
# Prereqs the app SPN still needs that this script does NOT do (account-admin):
#   * Enable system.access / system.billing (Account console → System tables).
#   * Enable App on-behalf-of OAuth + scopes if you want admin "live mode".
#
# Usage:
#   ./grant_app_access.sh --profile <cli-profile> --warehouse <warehouse-id> \
#       [--app <app-name>] [--catalogs "cat1 cat2 ..."]
#
# Env-var equivalents: PROFILE, WAREHOUSE_ID, APP_NAME, CATALOGS
# =============================================================================
set -euo pipefail

APP_NAME="${APP_NAME:-lineage-explorer-direct}"
PROFILE="${PROFILE:-DEFAULT}"
WAREHOUSE_ID="${WAREHOUSE_ID:-}"
CATALOGS="${CATALOGS:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)       APP_NAME="$2"; shift 2 ;;
    --profile)   PROFILE="$2"; shift 2 ;;
    --warehouse) WAREHOUSE_ID="$2"; shift 2 ;;
    --catalogs)  CATALOGS="$2"; shift 2 ;;
    -h|--help)   sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$WAREHOUSE_ID" ]]; then
  echo "ERROR: --warehouse <warehouse-id> is required (SQL warehouse to run the GRANTs)." >&2
  exit 2
fi

echo "Resolving service principal for app '$APP_NAME' (profile: $PROFILE)…"
SPN="$(databricks apps get "$APP_NAME" --profile "$PROFILE" -o json \
  | python3 -c 'import sys,json; print(json.load(sys.stdin).get("service_principal_client_id",""))')"

if [[ -z "$SPN" ]]; then
  echo "ERROR: could not resolve service_principal_client_id for app '$APP_NAME'." >&2
  echo "       Is the app deployed? Try: databricks apps get $APP_NAME --profile $PROFILE" >&2
  exit 1
fi
echo "App service principal: $SPN"

run_sql() {
  local sql="$1"
  local state
  state="$(databricks api post /api/2.0/sql/statements --profile "$PROFILE" \
    --json "{\"warehouse_id\":\"$WAREHOUSE_ID\",\"statement\":\"$sql\",\"wait_timeout\":\"50s\"}" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); s=d.get("status",{}); print(s.get("state","?"), (s.get("error") or {}).get("message",""))')"
  if [[ "$state" == SUCCEEDED* ]]; then
    echo "  OK   $sql"
  else
    echo "  FAIL $sql  -> $state"
  fi
}

echo "Granting read on system schemas…"
run_sql "GRANT USE CATALOG ON CATALOG system TO \`$SPN\`"
run_sql "GRANT USE SCHEMA, SELECT ON SCHEMA system.access TO \`$SPN\`"
run_sql "GRANT USE SCHEMA, SELECT ON SCHEMA system.billing TO \`$SPN\`"
run_sql "GRANT USE SCHEMA, SELECT ON SCHEMA system.information_schema TO \`$SPN\`"

if [[ -n "$CATALOGS" ]]; then
  echo "Granting USE CATALOG + BROWSE on catalogs: $CATALOGS"
  for c in $CATALOGS; do
    run_sql "GRANT USE CATALOG ON CATALOG \`$c\` TO \`$SPN\`"
    run_sql "GRANT BROWSE ON CATALOG \`$c\` TO \`$SPN\`"
  done
else
  echo "No --catalogs given; skipping per-catalog BROWSE grants."
  echo "  (Lineage spans catalogs — grant BROWSE on every catalog you want visible.)"
fi

echo "Done. Verify with: curl -s <app-url>/api/diagnostics | python3 -m json.tool"
