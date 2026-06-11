# Monitoring

## Health check

`healthcheck.py` pings the app every few minutes and alerts Slack when it's unhealthy. It's a standalone script with no dependencies — uses stdlib only.

### Run manually

```bash
python monitoring/healthcheck.py \
  --url https://lineage-explorer-direct-7474657661772683.aws.databricksapps.com \
  --slack-webhook "$SLACK_WEBHOOK_URL" \
  --env prod
```

### Schedule as a Databricks Job (recommended)

1. Workspace settings → Notification destinations → add a Slack destination. Copy the destination ID.
2. Create a job with one task:
   - Type: Python script
   - Source: Workspace file `/Workspace/.../monitoring/healthcheck.py`
   - Parameters: `["--url", "https://...", "--slack-webhook", "${secrets/lineage-explorer/slack-webhook}", "--env", "prod"]`
3. Schedule: every 5 minutes (`0 */5 * * * ?`)
4. Notification: on failure → Slack destination from step 1

### Schedule via Azure DevOps (alternative)

Add a scheduled pipeline that runs `python monitoring/healthcheck.py` every 5 minutes. Azure DevOps can post to Slack directly on pipeline failure without needing the `--slack-webhook` flag.

## What this catches

| Failure mode | Detection |
|---|---|
| App crashed / warehouse offline | `/health` returns 5xx or times out |
| Frontend bundle missing | `/` returns HTML but not the Lineage Explorer page |
| OAuth config broken | `/` returns login redirect instead of app |
| Whole workspace down | Both checks fail |

## What this does NOT catch

- Slow queries (no latency threshold here — use admin dashboard)
- Cache misconfigured (caught by smoke tests, not this)
- Specific lineage queries failing (would need deeper probes)

Add more probes to `healthcheck.py` only when a specific failure pattern has bitten you. No preemptive coverage — same philosophy as the smoke tests.
