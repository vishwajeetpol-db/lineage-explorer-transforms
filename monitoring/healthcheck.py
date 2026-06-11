"""
Standalone health-check script. Run on a cron (Databricks Job, GitHub Actions,
or plain crontab) to ping the app and alert on Slack when it's unhealthy.

Usage:
  python monitoring/healthcheck.py \
    --url https://lineage-explorer-direct-7474657661772683.aws.databricksapps.com \
    --slack-webhook $SLACK_WEBHOOK_URL

Exits 0 when healthy, 1 when any check fails. The exit code lets any orchestrator
(Databricks Jobs, Azure Pipelines, etc.) trigger its own native failure alerts
in addition to the Slack notification this script posts.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def check(url: str, path: str, timeout: int = 15) -> tuple[bool, str]:
    """Return (healthy, detail_message)."""
    full = url.rstrip("/") + path
    start = time.time()
    try:
        with urllib.request.urlopen(full, timeout=timeout) as resp:
            elapsed_ms = int((time.time() - start) * 1000)
            if resp.status != 200:
                return False, f"HTTP {resp.status} from {path}"
            body = resp.read()
            if path == "/health":
                data = json.loads(body)
                if data.get("status") != "ok":
                    return False, f"/health returned: {data}"
            return True, f"{path} OK ({elapsed_ms}ms)"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} from {path}: {e.reason}"
    except Exception as e:
        return False, f"Error hitting {path}: {type(e).__name__}: {e}"


def post_slack(webhook_url: str, message: str) -> None:
    """Fire-and-forget Slack post. Errors are swallowed (we don't want
    alerting failures to mask the underlying app failure)."""
    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps({"text": message}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"[slack] post failed: {e}", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True, help="Base URL of the app to check")
    p.add_argument("--slack-webhook", default="", help="Slack incoming-webhook URL")
    p.add_argument("--env", default="prod", help="Label used in alert message")
    args = p.parse_args()

    results: list[tuple[bool, str]] = []
    results.append(check(args.url, "/health"))
    # Static asset check — catches broken bundle deploys where frontend is missing
    results.append(check(args.url, "/"))

    all_healthy = all(ok for ok, _ in results)
    summary_lines = [f"  {'✅' if ok else '❌'} {msg}" for ok, msg in results]

    if all_healthy:
        print(f"[{args.env}] healthy:\n" + "\n".join(summary_lines))
        return 0

    message = (
        f":rotating_light: *Lineage Explorer [{args.env}] unhealthy*\n"
        f"URL: {args.url}\n" + "\n".join(summary_lines)
    )
    print(message, file=sys.stderr)
    if args.slack_webhook:
        post_slack(args.slack_webhook, message)
    return 1


if __name__ == "__main__":
    sys.exit(main())
