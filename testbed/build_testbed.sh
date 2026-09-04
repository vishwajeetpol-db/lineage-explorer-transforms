#!/usr/bin/env bash
# =============================================================================
# build_testbed.sh — create the BrickTrace test bed in a Databricks workspace.
#
# Builds one throwaway schema of fixtures that between them exercise every
# producer type and capability panel the app supports. See testbed/README.md for
# the fixture -> capability -> expected-result matrix.
#
# RUN THIS AS YOURSELF (a U2M profile), not as the deploy service principal:
# it needs CREATE SCHEMA on the catalog and CREATE TABLE/JOB/PIPELINE. The
# deploy SP has none of those.
#
#   databricks auth login --host <workspace-url> --profile vish-user
#   ./testbed/build_testbed.sh --profile vish-user --warehouse <id> [--tier 1]
#
# Tiers (cumulative):
#   1  producer-type coverage — SQL fixtures, 6 jobs, 2 DLT pipelines   (~10 serverless runs)
#   2  + ops/quality fixtures — schema evolution, 3 run-health jobs     (~6 more short runs)
#   3  + costly extras, OFF unless asked (ML model + serving endpoint)
#
# Everything is idempotent: re-running updates in place. --cleanup removes it all.
# =============================================================================
set -euo pipefail

PROFILE=""
CATALOG="ws_us_e2_vish_aws_databricks_1_catalog"
SCHEMA="bt_testbed"
WAREHOUSE_ID="11c2f090646163af"
APP_NAME="bricktrace-dev"
TIER=1
DRY_RUN=0
CLEANUP=0
SKIP_RUNS=0
SLEEP_SECONDS=240

# A value-taking flag passed as the LAST argument (`--tier` with nothing after
# it) would otherwise expand unset $2 and die with bash's own "unbound variable"
# under `set -u`, never reaching the curated validation below.
need_val() {  # need_val <flag> [remaining args...]
  [[ $# -ge 2 && -n "${2:-}" ]] || { echo "ERROR: $1 requires a value." >&2; exit 2; }
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)   need_val "$@"; PROFILE="$2"; shift 2 ;;
    --catalog)   need_val "$@"; CATALOG="$2"; shift 2 ;;
    --schema)    need_val "$@"; SCHEMA="$2"; shift 2 ;;
    --warehouse) need_val "$@"; WAREHOUSE_ID="$2"; shift 2 ;;
    --app)       need_val "$@"; APP_NAME="$2"; shift 2 ;;
    --tier)      need_val "$@"; TIER="$2"; shift 2 ;;
    --sleep)     need_val "$@"; SLEEP_SECONDS="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=1; shift ;;
    --cleanup)   CLEANUP=1; shift ;;
    --skip-runs) SKIP_RUNS=1; shift ;;   # create everything, trigger nothing
    # Range ends at the header's closing rule, so the help text cannot drift into
    # the source below it as the header grows.
    -h|--help)   sed -n '2,/^# =\{10,\}$/p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -z "$PROFILE" ]] && { echo "ERROR: --profile is required." >&2; exit 2; }
[[ "$TIER" == "all" ]] && TIER=3

# Validate BEFORE anything is created. An unchecked value reaches `(( TIER >= 2 ))`
# far below, where bash arithmetic treats a non-numeric token as a variable name and
# aborts under `set -u` — but only after the tier-1 fixtures, ACL grants and workspace
# uploads have already been applied, leaving a half-built test bed and an error that
# names nothing the user typed. An out-of-range number (`--tier 9`) would pass every
# `(( TIER >= 2 ))` and silently mean tier 3.
[[ "$TIER" =~ ^[1-3]$ ]] || {
  echo "ERROR: --tier must be 1, 2, 3 or all (got '$TIER')." >&2; exit 2; }
[[ "$SLEEP_SECONDS" =~ ^[0-9]+$ ]] || {
  echo "ERROR: --sleep must be a whole number of seconds (got '$SLEEP_SECONDS')." >&2; exit 2; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

DBX=(databricks --profile "$PROFILE")
ME="$("${DBX[@]}" current-user me -o json | python3 -c 'import sys,json; print(json.load(sys.stdin)["userName"])')"
WS_DIR="/Users/${ME}/bricktrace_testbed"
FQ="${CATALOG}.${SCHEMA}"

echo "─────────────────────────────────────────────────────────────"
echo " identity   : $ME"
echo " target     : $FQ"
echo " workspace  : $WS_DIR"
echo " warehouse  : $WAREHOUSE_ID"
echo " tier       : $TIER   dry-run: $DRY_RUN   cleanup: $CLEANUP"
echo "─────────────────────────────────────────────────────────────"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
run_sql() {  # run_sql "<statement>" [label]
  local sql="$1" label="${2:-}"
  if [[ "$DRY_RUN" == "1" ]]; then echo "  [dry-run] SQL ${label:-${sql:0:70}}"; return 0; fi
  local payload out
  payload="$(python3 -c '
import json, sys
print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.argv[2], "wait_timeout": "50s"}))' \
    "$WAREHOUSE_ID" "$sql")"
  out="$("${DBX[@]}" api post /api/2.0/sql/statements --json "$payload" 2>&1 | python3 -c '
import sys, json
try: d = json.load(sys.stdin)
except Exception: print("PARSE_ERROR " + sys.stdin.read()[:300]); sys.exit()
st = d.get("status", {})
state = st.get("state", "?")
print(state if state == "SUCCEEDED" else "FAILED " + (st.get("error") or {}).get("message", "")[:300])
')"
  if [[ "$out" == SUCCEEDED ]]; then
    echo "  OK   ${label:-${sql:0:70}}"
  else
    echo "  FAIL ${label:-${sql:0:70}}" >&2
    echo "       $out" >&2
    return 1
  fi
}

run_sql_file() {  # split a .sql file into statements and run them in order
  local f="$1"
  echo "SQL file: $(basename "$f")"
  local n=0
  while IFS= read -r -d $'\0' stmt; do
    [[ -z "${stmt//[[:space:]]/}" ]] && continue
    n=$((n+1))
    run_sql "$stmt" "$(printf '%s' "$stmt" | tr '\n' ' ' | cut -c1-72)"
  done < <(python3 "$HERE/split_sql.py" "$f")
  echo "  ($n statements)"
}

substitute() {  # substitute --> stage dir, tokens replaced
  local src="$1" dst="$STAGE/$(basename "$1")"
  sed -e "s|__CATALOG__|$CATALOG|g" -e "s|__SCHEMA__|$SCHEMA|g" "$src" > "$dst"
  echo "$dst"
}

ws_import() {  # ws_import <local> <ws-path> <RAW|SOURCE> [language]
  local local_f="$1" ws_path="$2" fmt="$3" lang="${4:-}"
  if [[ "$DRY_RUN" == "1" ]]; then echo "  [dry-run] import $(basename "$local_f") -> $ws_path ($fmt)"; return 0; fi
  local args=(workspace import "$ws_path" --file "$local_f" --format "$fmt" --overwrite)
  [[ -n "$lang" ]] && args+=(--language "$lang")
  "${DBX[@]}" "${args[@]}" >/dev/null
  echo "  imported $ws_path ($fmt)"
}

api_post() {  # api_post <path> <json>
  if [[ "$DRY_RUN" == "1" ]]; then echo "  [dry-run] POST $1" >&2; echo '{}'; return 0; fi
  "${DBX[@]}" api post "$1" --json "$2"
}

# UC grants on the schema are not enough. The transformation-lineage build runs
# as the APP's service principal, and to resolve a producer it must (a) read the
# producing job/pipeline via jobs.get_run, and (b) export the task's source file.
# Objects created here are owned by the human running this script, so the app SP
# has neither by default — and the failure is silent and misleading: the app SP's
# PERMISSION_DENIED on jobs.get_run is swallowed and reported as the skip reason
# "no_resolvable_tasks", which reads as "unsupported task type" instead of
# "cannot see the job". A PATCH, so the owner's and admins' entries survive.
acl_grant() {  # acl_grant <object-type> <object-id> <permission-level> <label>
  local otype="$1" oid="$2" level="$3" label="$4"
  # DRY_RUN first: on a dry run the object does not exist yet, so oid is empty —
  # the grant should still be listed rather than silently skipped.
  if [[ "$DRY_RUN" == "1" ]]; then echo "  [dry-run] grant $level on $otype ($label)"; return 0; fi
  [[ -z "${APP_SP:-}" || -z "$oid" ]] && return 0
  if "${DBX[@]}" permissions update "$otype" "$oid" \
       --json "{\"access_control_list\":[{\"service_principal_name\":\"${APP_SP}\",\"permission_level\":\"${level}\"}]}" \
       >/dev/null 2>&1; then
    echo "  granted $level on $label"
  else
    echo "  WARNING: could not grant $level on $label — the app will report no" >&2
    echo "           transformation lineage for anything it produces." >&2
  fi
}

ws_object_id() {  # ws_object_id <workspace-path>
  "${DBX[@]}" workspace get-status "$1" -o json 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("object_id",""))' 2>/dev/null || echo ""
}

job_id_by_name() {
  "${DBX[@]}" api get "/api/2.2/jobs/list?name=$1" 2>/dev/null | python3 -c '
import sys, json
try: d = json.load(sys.stdin)
except Exception: print(""); sys.exit()
jobs = d.get("jobs") or []
print(jobs[0]["job_id"] if jobs else "")
' 2>/dev/null || echo ""
}

pipeline_id_by_name() {
  "${DBX[@]}" api get "/api/2.0/pipelines?filter=name%20LIKE%20'$1'" 2>/dev/null | python3 -c '
import sys, json
try: d = json.load(sys.stdin)
except Exception: print(""); sys.exit()
st = d.get("statuses") or []
print(st[0]["pipeline_id"] if st else "")
' 2>/dev/null || echo ""
}

upsert_job() {  # upsert_job <name> <settings-json>
  local name="$1" settings="$2" existing
  existing="$(job_id_by_name "$name")"
  if [[ -n "$existing" ]]; then
    api_post /api/2.2/jobs/reset "$(python3 -c '
import json, sys
print(json.dumps({"job_id": int(sys.argv[1]), "new_settings": json.loads(sys.argv[2])}))' "$existing" "$settings")" >/dev/null
    echo "  updated job $name ($existing)" >&2
    echo "$existing"
  else
    local created
    created="$(api_post /api/2.2/jobs/create "$settings" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("job_id",""))')"
    echo "  created job $name ($created)" >&2
    echo "$created"
  fi
}

run_job() {  # run_job <job_id> <name> [notebook-params-json]
  local jid="$1" name="$2" params="${3:-}"
  # The caller guards SKIP_RUNS/DRY_RUN at the block level; here we only need to
  # tolerate a job that was not created (empty id).
  [[ -z "$jid" ]] && { echo "  (skipped run: $name — no job id)"; return 0; }
  local body rid
  body="$(python3 -c '
import json, sys
body = {"job_id": int(sys.argv[1])}
raw = sys.argv[2] if len(sys.argv) > 2 else ""
if raw.strip():
    body["notebook_params"] = json.loads(raw)
print(json.dumps(body))' "$jid" "$params")"
  rid="$(api_post /api/2.2/jobs/run-now "$body" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("run_id",""))')"
  echo "  started $name run_id=$rid"
}

# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------
if [[ "$CLEANUP" == "1" ]]; then
  echo "Removing the test bed…"
  for name in bt-sqltask-order-agg bt-pyfile-customer-norm bt-multi-producer-a \
              bt-multi-producer-b bt-framework-driver bt-xcat-trips \
              bt-health-flaky bt-health-failing bt-health-slow; do
    jid="$(job_id_by_name "$name")"
    if [[ -n "$jid" ]]; then
      api_post /api/2.2/jobs/delete "{\"job_id\": $jid}" >/dev/null && echo "  deleted job $name"
    fi
  done
  # These two are direct CLI calls, not run_sql/api_post, so they do NOT inherit
  # those helpers' dry-run guard — without an explicit check here a
  # `--cleanup --dry-run` really destroyed the pipelines and the workspace folder
  # while printing "[dry-run]" for everything around them.
  for name in bt-sql-dlt-channel bt-cdc-scd2-customers; do
    pid="$(pipeline_id_by_name "$name")"
    if [[ -n "$pid" ]]; then
      if [[ "$DRY_RUN" == "1" ]]; then
        echo "  [dry-run] DELETE pipeline $name ($pid)"
      elif "${DBX[@]}" api delete "/api/2.0/pipelines/$pid" >/dev/null 2>&1; then
        echo "  deleted pipeline $name"
      fi
    fi
  done
  run_sql "DROP SCHEMA IF EXISTS ${FQ} CASCADE" "DROP SCHEMA ${FQ} CASCADE"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "  [dry-run] workspace delete $WS_DIR --recursive"
  else
    "${DBX[@]}" workspace delete "$WS_DIR" --recursive >/dev/null 2>&1 && echo "  deleted $WS_DIR" || true
  fi
  echo "Done."
  exit 0
fi

# ---------------------------------------------------------------------------
# 1. SQL fixtures
# ---------------------------------------------------------------------------
echo ""
echo "[1/5] SQL fixtures"
run_sql_file "$(substitute "$HERE/sql/10_fixtures.sql")"
if (( TIER >= 2 )); then
  run_sql_file "$(substitute "$HERE/sql/20_schema_evolution.sql")"
fi

# ---------------------------------------------------------------------------
# 2. Grant the app's service principal read on the new schema.
#    Without this the app cannot see any of these fixtures — a new schema
#    inherits nothing, and BROWSE on the catalog is not enough for
#    SHOW CREATE TABLE (which the view / MV / streaming-table resolution needs).
# ---------------------------------------------------------------------------
echo ""
echo "[2/5] Grants for the app service principal"
APP_SP="$("${DBX[@]}" apps get "$APP_NAME" -o json 2>/dev/null \
  | python3 -c 'import sys,json; print(json.load(sys.stdin).get("service_principal_client_id",""))' 2>/dev/null || true)"
if [[ -z "$APP_SP" ]]; then
  echo "  WARNING: could not resolve the SP for app '$APP_NAME' — grant it by hand:" >&2
  echo "    GRANT USE SCHEMA, SELECT ON SCHEMA ${FQ} TO \`<app-sp>\`;" >&2
else
  echo "  app SP: $APP_SP"
  run_sql "GRANT USE SCHEMA ON SCHEMA ${FQ} TO \`${APP_SP}\`" "GRANT USE SCHEMA ON ${FQ}"
  run_sql "GRANT SELECT ON SCHEMA ${FQ} TO \`${APP_SP}\`" "GRANT SELECT ON ${FQ}"
fi

# ---------------------------------------------------------------------------
# 3. Upload producer sources
#    Notebooks  -> SOURCE + language (become NOTEBOOK objects)
#    .py / .sql -> RAW (must stay FILE objects: spark_python_task and sql_task
#                  take workspace FILES, and RAW is what avoids notebook-ifying them)
# ---------------------------------------------------------------------------
echo ""
echo "[3/5] Uploading producer sources to $WS_DIR"
[[ "$DRY_RUN" == "0" ]] && "${DBX[@]}" workspace mkdirs "$WS_DIR" >/dev/null

for nb in nb_multi_producer_a nb_multi_producer_b nb_framework_driver nb_xcat_trips; do
  ws_import "$(substitute "$HERE/notebooks/$nb.py")" "$WS_DIR/$nb" SOURCE PYTHON
done
if (( TIER >= 2 )); then
  for nb in nb_health_flaky nb_health_failing nb_health_slow; do
    ws_import "$(substitute "$HERE/notebooks/$nb.py")" "$WS_DIR/$nb" SOURCE PYTHON
  done
fi
ws_import "$(substitute "$HERE/notebooks/py_file_customer_norm.py")" "$WS_DIR/py_file_customer_norm.py" RAW
ws_import "$(substitute "$HERE/sql/sqltask_order_agg.sql")"          "$WS_DIR/sqltask_order_agg.sql" RAW
ws_import "$(substitute "$HERE/dlt/sql_dlt_channel.sql")"            "$WS_DIR/sql_dlt_channel.sql" RAW
ws_import "$(substitute "$HERE/dlt/dlt_cdc_customers.py")"           "$WS_DIR/dlt_cdc_customers" SOURCE PYTHON

# The build exports these sources as the app SP to parse them. CAN_READ is
# enough here — unlike the app's own deployed build notebook, which the job has
# to *execute* and therefore needs CAN_RUN (grant_build_source_access.sh).
# Children inherit, so one grant on the directory covers every file above.
acl_grant directories "$(ws_object_id "$WS_DIR")" CAN_READ "$WS_DIR (producer sources)"

# ---------------------------------------------------------------------------
# 4. Jobs and pipelines
# ---------------------------------------------------------------------------
echo ""
echo "[4/5] Jobs and pipelines"

nb_job() {  # nb_job <name> <notebook> — serverless notebook task with CATALOG/SCHEMA params
  python3 -c '
import json, sys
name, path, cat, sch = sys.argv[1:5]
print(json.dumps({
  "name": name,
  "tasks": [{
    "task_key": "main",
    "notebook_task": {
      "notebook_path": path,
      "source": "WORKSPACE",
      "base_parameters": {"CATALOG": cat, "SCHEMA": sch},
    },
  }],
  "max_concurrent_runs": 1,
  # Without this, back-to-back run-now calls are SKIPPED rather than queued,
  # which would silently collapse the 3-run flaky mix into one run.
  "queue": {"enabled": True},
  "tags": {"bricktrace_testbed": "true"},
}))' "$1" "$2" "$CATALOG" "$SCHEMA"
}

JOB_A="$(upsert_job bt-multi-producer-a "$(nb_job bt-multi-producer-a "$WS_DIR/nb_multi_producer_a")")"
JOB_B="$(upsert_job bt-multi-producer-b "$(nb_job bt-multi-producer-b "$WS_DIR/nb_multi_producer_b")")"
JOB_FW="$(upsert_job bt-framework-driver "$(nb_job bt-framework-driver "$WS_DIR/nb_framework_driver")")"
JOB_XC="$(upsert_job bt-xcat-trips "$(nb_job bt-xcat-trips "$WS_DIR/nb_xcat_trips")")"

JOB_SQL="$(upsert_job bt-sqltask-order-agg "$(python3 -c '
import json, sys
path, wh = sys.argv[1:3]
print(json.dumps({
  "name": "bt-sqltask-order-agg",
  "tasks": [{
    "task_key": "sql_agg",
    "sql_task": {"file": {"path": path, "source": "WORKSPACE"}, "warehouse_id": wh},
  }],
  "queue": {"enabled": True},
  "tags": {"bricktrace_testbed": "true"},
}))' "$WS_DIR/sqltask_order_agg.sql" "$WAREHOUSE_ID")")"

JOB_PY="$(upsert_job bt-pyfile-customer-norm "$(python3 -c '
import json, sys
path, cat, sch = sys.argv[1:4]
print(json.dumps({
  "name": "bt-pyfile-customer-norm",
  "tasks": [{
    "task_key": "py_norm",
    # A plain .py FILE, not a notebook — this is the spark_python_task branch.
    "spark_python_task": {"python_file": path, "source": "WORKSPACE", "parameters": [cat, sch]},
    "environment_key": "Default",
  }],
  "environments": [{"environment_key": "Default", "spec": {"client": "2"}}],
  "queue": {"enabled": True},
  "tags": {"bricktrace_testbed": "true"},
}))' "$WS_DIR/py_file_customer_norm.py" "$CATALOG" "$SCHEMA")")"

if (( TIER >= 2 )); then
  JOB_FLAKY="$(upsert_job bt-health-flaky "$(nb_job bt-health-flaky "$WS_DIR/nb_health_flaky")")"
  JOB_FAIL="$(upsert_job bt-health-failing "$(nb_job bt-health-failing "$WS_DIR/nb_health_failing")")"
  JOB_SLOW="$(upsert_job bt-health-slow "$(python3 -c '
import json, sys
path, secs = sys.argv[1:3]
print(json.dumps({
  "name": "bt-health-slow",
  "tasks": [{
    "task_key": "main",
    "notebook_task": {"notebook_path": path, "source": "WORKSPACE",
                      "base_parameters": {"SLEEP_SECONDS": secs}},
  }],
  "queue": {"enabled": True},
  "tags": {"bricktrace_testbed": "true"},
}))' "$WS_DIR/nb_health_slow" "$SLEEP_SECONDS")")"
fi

upsert_pipeline() {  # upsert_pipeline <name> <spec-json>
  local name="$1" spec="$2" existing
  existing="$(pipeline_id_by_name "$name")"
  if [[ -n "$existing" ]]; then
    if [[ "$DRY_RUN" == "0" ]]; then
      "${DBX[@]}" api put "/api/2.0/pipelines/$existing" --json "$spec" >/dev/null
    fi
    echo "  updated pipeline $name ($existing)" >&2
    echo "$existing"
  else
    local created
    created="$(api_post /api/2.0/pipelines "$spec" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("pipeline_id",""))')"
    echo "  created pipeline $name ($created)" >&2
    echo "$created"
  fi
}

PIPE_SQL="$(upsert_pipeline bt-sql-dlt-channel "$(python3 -c '
import json, sys
path, cat, sch = sys.argv[1:4]
print(json.dumps({
  "name": "bt-sql-dlt-channel",
  "serverless": True,
  "development": True,
  "continuous": False,
  "catalog": cat,
  "schema": sch,
  # A .sql FILE library — the SQL-defined DLT path (the existing workspace
  # fixture is Python-defined, so this branch was never exercised).
  "libraries": [{"file": {"path": path}}],
}))' "$WS_DIR/sql_dlt_channel.sql" "$CATALOG" "$SCHEMA")")"

PIPE_CDC="$(upsert_pipeline bt-cdc-scd2-customers "$(python3 -c '
import json, sys
path, cat, sch = sys.argv[1:4]
print(json.dumps({
  "name": "bt-cdc-scd2-customers",
  "serverless": True,
  "development": True,
  "continuous": False,
  "catalog": cat,
  "schema": sch,
  "libraries": [{"notebook": {"path": path}}],
  "configuration": {"bt.catalog": cat, "bt.schema": sch},
}))' "$WS_DIR/dlt_cdc_customers" "$CATALOG" "$SCHEMA")")"

# Let the app SP read the producers it will be asked to resolve. CAN_VIEW is the
# least that lets jobs.get_run / pipelines.get succeed; the build never triggers
# them, it only reads their task definitions.
for jid_name in \
  "${JOB_A}:bt-multi-producer-a" "${JOB_B}:bt-multi-producer-b" \
  "${JOB_FW}:bt-framework-driver" "${JOB_XC}:bt-xcat-trips" \
  "${JOB_SQL}:bt-sqltask-order-agg" "${JOB_PY}:bt-pyfile-customer-norm" \
  "${JOB_FLAKY:-}:bt-health-flaky" "${JOB_FAIL:-}:bt-health-failing" \
  "${JOB_SLOW:-}:bt-health-slow"; do
  acl_grant jobs "${jid_name%%:*}" CAN_VIEW "job ${jid_name##*:}"
done
for pid_name in "${PIPE_SQL}:bt-sql-dlt-channel" "${PIPE_CDC}:bt-cdc-scd2-customers"; do
  acl_grant pipelines "${pid_name%%:*}" CAN_VIEW "pipeline ${pid_name##*:}"
done

# ---------------------------------------------------------------------------
# 5. Trigger everything once so Unity Catalog actually records lineage.
#    Nothing shows up in the app until each producer has run at least once —
#    system.access.column_lineage is populated by RUNS, not by definitions.
# ---------------------------------------------------------------------------
echo ""
echo "[5/5] Triggering runs (lineage only exists once a producer has run)"
if [[ "$SKIP_RUNS" == "1" || "$DRY_RUN" == "1" ]]; then
  echo "  (skipped — nothing triggered)"
else
  run_job "$JOB_SQL" bt-sqltask-order-agg
  run_job "$JOB_PY"  bt-pyfile-customer-norm
  run_job "$JOB_XC"  bt-xcat-trips
  # A first, then B, so B's drifted logic is the most recent writer of
  # multi_metrics — both still appear as producers in column_lineage.
  run_job "$JOB_A"   bt-multi-producer-a
  run_job "$JOB_B"   bt-multi-producer-b
  run_job "$JOB_FW"  bt-framework-driver

  for pid_name in "$PIPE_SQL:bt-sql-dlt-channel" "$PIPE_CDC:bt-cdc-scd2-customers"; do
    pid="${pid_name%%:*}"; pname="${pid_name##*:}"
    [[ -z "$pid" ]] && continue
    api_post "/api/2.0/pipelines/$pid/updates" '{"full_refresh": false}' >/dev/null \
      && echo "  started pipeline update: $pname"
  done

  if (( TIER >= 2 )); then
    # Deterministic 2-pass / 1-fail mix via the FAIL widget, so "Degraded"
    # is reproducible instead of depending on when the script happened to run.
    run_job "${JOB_FLAKY:-}" "bt-health-flaky (pass 1)" '{"FAIL": "false"}'
    run_job "${JOB_FLAKY:-}" "bt-health-flaky (fail)"   '{"FAIL": "true"}'
    run_job "${JOB_FLAKY:-}" "bt-health-flaky (pass 2)" '{"FAIL": "false"}'
    run_job "${JOB_FAIL:-}"  "bt-health-failing (run 1)"
    run_job "${JOB_FAIL:-}"  "bt-health-failing (run 2)"
    run_job "${JOB_SLOW:-}"  bt-health-slow "{\"SLEEP_SECONDS\": \"$SLEEP_SECONDS\"}"
  fi
fi

cat <<EOM

─────────────────────────────────────────────────────────────
Test bed created: ${FQ}
Producer sources: ${WS_DIR}

Runs are asynchronous. Unity Catalog surfaces lineage a few minutes after each
producer finishes (column_lineage lags the run — usually 5 to 60 minutes), so
give it time before judging an empty graph.

Next:
  1. Watch the runs finish (Workflows UI, or 'databricks api get /api/2.2/jobs/runs/list?job_id=<id>').
  2. Open the app and browse to ${SCHEMA}.
  3. Work through testbed/README.md — it lists each fixture, the capability it
     exercises, and the result you should see.

Remove everything: $0 --profile ${PROFILE} --cleanup
─────────────────────────────────────────────────────────────
EOM
