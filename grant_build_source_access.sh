#!/usr/bin/env bash
# =============================================================================
# grant_build_source_access.sh — post-deploy helper (workspace ACLs only).
#
# WHY THIS EXISTS
#   `bundle deploy` uploads the source into the DEPLOYER's home directory
#   (/Workspace/Users/<deployer>/.bundle/<bundle>/<target>/files). That folder
#   is readable only by the deployer and workspace admins.
#
#   The app's "Generate transformation lineage" button submits a serverless job
#   that runs <source>/notebooks/run_pipeline and imports <source>/
#   transformation_lineage — AS THE APP's service principal, not as the
#   deployer. With no grant on that folder every build dies with:
#
#     Task build_lineage failed with message: Unable to access the notebook
#     ".../files/notebooks/run_pipeline" in the workspace. Either it does not
#     exist, or the identity used to run this job, app-xxxxxx <app> (<sp-id>),
#     lacks the required permissions.
#
#   This script resolves the deployed app's service principal and its source
#   path, then grants that SP CAN_RUN on the source folder. CAN_READ is NOT
#   enough: a notebook_task has to execute the notebook, which requires CAN_RUN.
#   Children (notebooks/, transformation_lineage/) inherit it.
#
#   Idempotent, and safe to re-run: the permissions API is called with PATCH, so
#   existing ACL entries (the deployer's CAN_MANAGE, admins) are left untouched.
#
# WHO RUNS IT
#   The identity that deployed the bundle — it already has CAN_MANAGE on the
#   folder. No metastore admin, no SQL warehouse, no account admin needed.
#   (Contrast grant_app_access.sh, which needs a metastore admin + a warehouse
#   for the UC/system-table GRANTs.)
#
# WHEN TO RUN IT
#   After every `bundle deploy` that (re)creates the bundle root — i.e. a first
#   deploy, a deploy by a different identity, a renamed bundle/target, or after
#   `bundle destroy`. `make deploy` runs it automatically.
#
# Usage:
#   ./grant_build_source_access.sh --profile <cli-profile> [--app <app-name>] [--dry-run]
#
# Env-var equivalents: PROFILE, APP_NAME
# =============================================================================
set -euo pipefail

APP_NAME="${APP_NAME:-bricktrace-dev}"
PROFILE="${PROFILE:-DEFAULT}"
LEVEL="CAN_RUN"
DRY_RUN=0

# A value-taking flag passed as the LAST argument (`--app` with nothing after it)
# would otherwise expand unset $2 and die with bash's own "unbound variable" under
# `set -u`, never reaching the curated guidance below.
need_val() {  # need_val <flag> [remaining args...]
  [[ $# -ge 2 && -n "${2:-}" ]] || { echo "ERROR: $1 requires a value." >&2; exit 2; }
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)     need_val "$@"; APP_NAME="$2"; shift 2 ;;
    --profile) need_val "$@"; PROFILE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    # Range ends at the header's closing rule, so the help text cannot drift into
    # the source below it as the header grows.
    -h|--help) sed -n '2,/^# =\{10,\}$/p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

echo "Resolving app '$APP_NAME' (profile: $PROFILE)…"
# `|| true`: under `set -e` a failed lookup would abort the script here, silently
# skipping every curated error message below. Callers detect failure by the empty
# value instead.
APP_JSON="$(databricks apps get "$APP_NAME" --profile "$PROFILE" -o json 2>/dev/null || true)"
if [[ -z "$APP_JSON" ]]; then
  echo "ERROR: could not read app '$APP_NAME' as profile '$PROFILE'." >&2
  echo "       Is the app deployed? Try: databricks apps get $APP_NAME --profile $PROFILE" >&2
  exit 1
fi

# Read the two fields separately — a workspace path may contain spaces, which a
# single word-splitting `read` would mangle.
SPN="$(printf '%s' "$APP_JSON" | python3 -c '
import sys, json
try:
    print(json.load(sys.stdin).get("service_principal_client_id") or "-")
except Exception:
    print("-")
')"
SRC_PATH="$(printf '%s' "$APP_JSON" | python3 -c '
import sys, json
# Precedence matches backend/build_service.py::_discover_from_app_source, which is
# what the app itself resolves PIPELINE_NOTEBOOK_PATH from — the two must agree on
# which folder matters, or this grants one folder while the app opens another.
#
# app-level default FIRST, deployments only as a fallback: `bundle deploy` updates
# the app spec, and `make deploy` runs this script BETWEEN `bundle deploy` and
# `bundle run`, so active_deployment still describes the PREVIOUS deployment. Any
# change to workspace.root_path (a different deploying identity, a renamed
# bundle/target, a redeploy after `bundle destroy`) makes it point at the old path
# — exactly the cases this script exists to be re-run for.
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
for key in ("default_source_code_path", "source_code_path"):
    if d.get(key):
        print(d[key]); break
else:
    for key in ("active_deployment", "pending_deployment"):
        dep = d.get(key) or {}
        if dep.get("source_code_path"):
            print(dep["source_code_path"]); break
    else:
        print("-")
')"

if [[ "$SPN" == "-" ]]; then
  echo "ERROR: could not resolve service_principal_client_id for app '$APP_NAME'." >&2
  echo "       Is the app deployed? Try: databricks apps get $APP_NAME --profile $PROFILE" >&2
  exit 1
fi
if [[ "$SRC_PATH" == "-" ]]; then
  echo "ERROR: app '$APP_NAME' reports no source_code_path — deploy it first:" >&2
  echo "       databricks bundle deploy -t <target> --profile $PROFILE …" >&2
  exit 1
fi

echo "  service principal : $SPN"
echo "  source folder     : $SRC_PATH"

# The permissions API addresses objects by numeric id, not path.
#
# `|| true` is load-bearing: without it a failed stat propagates non-zero through
# `pipefail` and `set -e` kills the script at the ASSIGNMENT below, so none of the
# fallbacks or curated errors that check for an empty result ever run.
_stat_object_id() {
  databricks workspace get-status "$1" --profile "$PROFILE" -o json 2>/dev/null \
    | python3 -c '
import sys, json
try:
    print(json.load(sys.stdin).get("object_id", "") or "")
except Exception:
    pass
' 2>/dev/null || true
}

# get-status accepts both the /Workspace-prefixed and the bare spelling, but which
# one the app reports varies — so try the reported spelling, then the other.
#
# The retry lives INSIDE the helper so EVERY lookup gets it. With it at the folder
# call site only, the run_pipeline verification below saw a single spelling: for a
# source path whose resolvable spelling differed from the reported one (a /Repos
# root, say) it reported "did the bundle upload notebooks/?" about a notebook that
# was there all along, right after successfully granting on its parent folder.
get_object_id() {
  local path="$1" alt out
  out="$(_stat_object_id "$path")"
  if [[ -z "$out" ]]; then
    alt="${path#/Workspace}"
    [[ "$alt" == "$path" ]] && alt="/Workspace${path}"
    out="$(_stat_object_id "$alt")"
  fi
  printf '%s' "$out"
}

OBJECT_ID="$(get_object_id "$SRC_PATH")"
if [[ -z "$OBJECT_ID" ]]; then
  echo "ERROR: could not stat the source folder '$SRC_PATH' as profile '$PROFILE'." >&2
  echo "       Run this as the identity that deployed the bundle (it owns that folder)." >&2
  exit 1
fi
echo "  folder object id  : $OBJECT_ID"

if [[ "$DRY_RUN" == "1" ]]; then
  echo ""
  echo "DRY RUN — would grant $LEVEL on directory $OBJECT_ID to $SPN"
  exit 0
fi

echo "Granting $LEVEL on the source folder to the app service principal…"
databricks permissions update directories "$OBJECT_ID" --profile "$PROFILE" --json "$(
  printf '{"access_control_list":[{"service_principal_name":"%s","permission_level":"%s"}]}' "$SPN" "$LEVEL"
)" >/dev/null

# Verify against the notebook the build job actually opens, since that is the
# object whose ACL decides whether a build succeeds.
NB_PATH="${SRC_PATH%/}/notebooks/run_pipeline"
NB_ID="$(get_object_id "$NB_PATH")"
if [[ -z "$NB_ID" ]]; then
  echo "WARNING: granted, but '$NB_PATH' was not found — did the bundle upload notebooks/?" >&2
  exit 0
fi

# Same `|| true` reasoning as get_object_id: a failed read must fall through to the
# ERROR below, not abort the script one line short of it.
VERIFIED="$(databricks permissions get notebooks "$NB_ID" --profile "$PROFILE" -o json 2>/dev/null \
  | SPN="$SPN" python3 -c '
import os, sys, json
spn = os.environ["SPN"]
try:
    acl = json.load(sys.stdin).get("access_control_list", [])
except Exception:
    acl = []
for entry in acl:
    if entry.get("service_principal_name") == spn:
        levels = [p.get("permission_level") for p in entry.get("all_permissions", [])]
        if {"CAN_RUN", "CAN_EDIT", "CAN_MANAGE"} & set(levels):
            print(",".join(l for l in levels if l))
            break
' 2>/dev/null || true)"

if [[ -z "$VERIFIED" ]]; then
  echo "ERROR: grant applied but $SPN still lacks CAN_RUN on $NB_PATH." >&2
  echo "       Check the ACL: databricks permissions get notebooks $NB_ID --profile $PROFILE" >&2
  exit 1
fi

echo "  verified: run_pipeline notebook -> $SPN has $VERIFIED"
echo ""
echo "Done. Transformation-lineage builds can now read the deployed source."
echo "Verify in the app: click a column -> Generate transformation lineage."
