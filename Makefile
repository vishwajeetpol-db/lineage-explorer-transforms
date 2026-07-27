# BrickTrace — deploy helper
# ---------------------------------------------------------------------------
# Encodes the workspace-specific --var overrides so a compute/config change or
# a plain redeploy never drops DATABRICKS_WAREHOUSE_ID / the app-owned schema.
#
# Defaults target Pritam's FEVM workspace. Override any on the command line:
#   make deploy PROFILE=my-profile WAREHOUSE_ID=abc123 LINEAGE_CATALOG=my_cat
#
# Common flows:
#   make redeploy      # build frontend + deploy bundle + run (the full, safe path)
#   make run           # re-apply env + start the app (fixes "warehouse not set")
#   make deploy        # upload bundle only
#   make status        # app + compute status
#   make diagnostics   # hit /api/diagnostics
#   make logs          # tail app logs
# ---------------------------------------------------------------------------

PROFILE          ?= fevm-pritam-demo-workspace
TARGET           ?= dev
APP              ?= bricktrace
APP_NAME         ?= bricktrace-dev
WAREHOUSE_ID     ?= cd54b9f16b2bf7ed
LINEAGE_CATALOG  ?= pritam_demo_workspace_catalog
LINEAGE_SCHEMA   ?= bricktrace_lineage
# Captured plans/CDC are written by the lineage_capture project into the POC's
# lineage_explorer schema — point the reader there (app-owned schema has none).
CAPTURED_PLANS_TABLE ?= pritam_demo_workspace_catalog.lineage_explorer.captured_plans
CAPTURED_CDC_TABLE   ?= pritam_demo_workspace_catalog.lineage_explorer.captured_cdc_specs
APP_URL          ?= https://bricktrace-dev-7474659775414644.aws.databricksapps.com

# The --var overrides that MUST accompany every deploy/run for this workspace.
VARS = --var="warehouse_id=$(WAREHOUSE_ID)" \
       --var="lineage_catalog=$(LINEAGE_CATALOG)" \
       --var="lineage_schema=$(LINEAGE_SCHEMA)" \
       --var="captured_plans_table=$(CAPTURED_PLANS_TABLE)" \
       --var="captured_cdc_table=$(CAPTURED_CDC_TABLE)"

DBX  = databricks
BUNDLE_FLAGS = -t $(TARGET) --profile $(PROFILE) $(VARS)

.PHONY: help build deploy run redeploy status diagnostics logs login token open

help:
	@grep -E '^[a-zA-Z_-]+:.*?#' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?#"}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

build: # Build the frontend (Databricks Apps serves prebuilt frontend/dist)
	cd frontend && npm run build

deploy: # Upload the bundle (source + resources) — does NOT restart with env
	$(DBX) bundle deploy $(BUNDLE_FLAGS)

run: # Re-apply env config + (re)start the app — fixes "warehouse not set"
	$(DBX) bundle run $(APP) $(BUNDLE_FLAGS)

redeploy: build deploy run # Full safe path: build → deploy → run
	@echo "Redeployed. Verify with: make diagnostics"

status: # Show app + compute status
	@$(DBX) apps get $(APP_NAME) --profile $(PROFILE) -o json | \
	  python3 -c "import sys,json; d=json.load(sys.stdin); \
	  print('app:', d.get('app_status',{}).get('state'), '-', d.get('app_status',{}).get('message')); \
	  print('compute:', d.get('compute_status',{}).get('state')); print('url:', d.get('url'))"

diagnostics: # Call /api/diagnostics (confirms warehouse + system tables reachable)
	@TOKEN=$$($(DBX) auth token --profile $(PROFILE) | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])"); \
	curl -s --max-time 60 -H "Authorization: Bearer $$TOKEN" "$(APP_URL)/api/diagnostics" | python3 -m json.tool

logs: # Tail the app logs
	$(DBX) apps logs $(APP_NAME) --profile $(PROFILE)

login: # Re-authenticate the CLI profile (interactive; opens a browser)
	$(DBX) auth login --profile $(PROFILE)

open: # Print the app URL
	@echo "$(APP_URL)"
