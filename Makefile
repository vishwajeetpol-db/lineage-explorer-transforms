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
#   make grant-source  # let the app SP read its deployed source (transform builds)
#   make run           # re-apply env + start the app (fixes "warehouse not set")
#   make deploy        # upload bundle only
#   make status        # app + compute status
#   make diagnostics   # hit /api/diagnostics
#   make logs          # tail app logs
# ---------------------------------------------------------------------------

PROFILE          ?= fevm-pritam-demo-workspace
TARGET           ?= dev
APP              ?= bricktrace
# The deployed app's NAME (the bundle resource is always `bricktrace`; its name is
# ${var.app_name}, which each target sets differently — see databricks.yml). Keep
# these in sync with that file: a pinned name would make `TARGET=prod` deploy prod
# and then grant/inspect the DEV app.
APP_NAME_dev     ?= bricktrace-dev
APP_NAME_prod    ?= bricktrace
APP_NAME         ?= $(APP_NAME_$(TARGET))
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

.PHONY: help build deploy grant-source run redeploy status diagnostics logs login token open

help:
	@grep -E '^[a-zA-Z_-]+:.*?#' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?#"}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

build: # Build the frontend (Databricks Apps serves prebuilt frontend/dist)
	cd frontend && npm run build

deploy: # Upload the bundle (source + resources) — does NOT restart with env
	@$(if $(APP_NAME),,$(error TARGET=$(TARGET) has no APP_NAME_$(TARGET); set APP_NAME=<app> explicitly))
	$(DBX) bundle deploy $(BUNDLE_FLAGS)
	@# Non-fatal on purpose. `redeploy` is `build deploy run`, and make stops at the
	@# first failing prerequisite — so letting a grant failure fail `deploy` would
	@# skip `run`, which is what re-applies the app's env (warehouse id, catalog).
	@# A missing ACL only breaks transformation-lineage BUILDS; a skipped `run`
	@# breaks the whole app. Warn loudly and carry on; `make grant-source` on its
	@# own still exits non-zero.
	@$(MAKE) --no-print-directory grant-source || { \
	  echo ""; \
	  echo "WARNING: grant-source failed — the deploy itself succeeded."; \
	  echo "         Transformation-lineage builds will fail until you re-run:"; \
	  echo "           make grant-source PROFILE=$(PROFILE) TARGET=$(TARGET)"; \
	  echo ""; \
	} >&2

grant-source: # Grant the app SP CAN_RUN on the deployed source (transform builds need it)
	@$(if $(APP_NAME),,$(error TARGET=$(TARGET) has no APP_NAME_$(TARGET); set APP_NAME=<app> explicitly))
	@./grant_build_source_access.sh --profile $(PROFILE) --app $(APP_NAME)

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
