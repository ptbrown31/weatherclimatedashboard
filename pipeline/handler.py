"""
handler.py — the only vendor-specific file in the pipeline.

AWS Lambda entrypoint. EventBridge Scheduler invokes it with a small JSON
event naming the job; the function body is one call into the same code the
command line runs. Configuration arrives as WX_* environment variables on
the function (set in ops/aws/template.yaml), never as code.

    event = {"job": "half-hourly"}      # archive, then forecast, then hurricane
    event = {"job": "obs"}              # every 10 minutes

Sizing, from measurements on 2026-08-21 and the review of them: a pass
streams two ~30 MB NBM bulletins plus LAMP and MAV; streamed, memory stays
near a megabyte per chunk, but set the function to 512 MB anyway (CPU scales
with memory on Lambda and the 128 MB default is too small for the JSON
work). Timeout 900 s (the maximum); the pass gives itself a deadline 30 s
short of what remains and records what it skipped. A function outside any
VPC has outbound internet access by default, so no NAT is needed. The
runtime needs tz data for the IANA zones: Amazon Linux 2023 images carry it;
if ZoneInfo fails at import, add the `tzdata` wheel to the deployment.

Alarms: the handler raises, which marks the invocation as an error for
CloudWatch, when every request in a pass failed OR when any enabled source
has failed for FAIL_STREAK_ALARM passes in a row (health.json). A source
that quietly fails every pass while the rest succeed is exactly the failure
that is otherwise invisible.
"""
from __future__ import annotations
import json
import os

from . import config, storage
from .run import JOBS, _register


def lambda_handler(event, context):
    _register()
    job = (event or {}).get("job", "half-hourly")
    if job not in JOBS:
        raise ValueError(f"unknown job {job!r}; known: {', '.join(sorted(JOBS))}")
    cfg = config.load()
    if context is not None and hasattr(context, "get_remaining_time_in_millis"):
        cfg["pass_budget_seconds"] = max(60, context.get_remaining_time_in_millis() / 1000 - 30)
    store = storage.from_config(cfg)
    status = JOBS[job](cfg, store)
    from . import archive
    alarms = (archive.LAST_STATUS or {}).get("alarms") or []
    if status:
        raise RuntimeError(f"{job}: every request in the pass failed")
    if alarms:
        raise RuntimeError(f"{job}: source(s) failing for {archive.FAIL_STREAK_ALARM}+ passes: {alarms}")
    return {"job": job, "status": status, "storage": store.kind()}


if __name__ == "__main__":  # local smoke test of the Lambda path, no AWS needed
    print(json.dumps(lambda_handler({"job": os.environ.get("WX_JOB", "archive")}, None)))
