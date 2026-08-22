"""
handler.py — the only vendor-specific file in the pipeline.

AWS Lambda entrypoint. EventBridge Scheduler invokes it with a small JSON
event naming the job; the function body is one call into the same code the
command line runs. Configuration arrives as WX_* environment variables on
the function (set in ops/aws/template.yaml), never as code.

    event = {"job": "archive"}

Timing facts this relies on (checked 2026-08-21): Lambda's maximum timeout is
15 minutes; a full archive pass downloads two ~30 MB NBM bulletins plus LAMP
and MAV and takes well under that. A function outside any VPC has outbound
internet access by default, so no NAT is needed.
"""
from __future__ import annotations
import json

from . import config, storage
from .run import JOBS, _register


def lambda_handler(event, context):
    _register()
    job = (event or {}).get("job", "archive")
    if job not in JOBS:
        raise ValueError(f"unknown job {job!r}; known: {', '.join(sorted(JOBS))}")
    cfg = config.load()
    store = storage.from_config(cfg)
    status = JOBS[job](cfg, store)
    # Raise on total failure so the invocation is recorded as an error and
    # the scheduler's retry and alarm paths see it; partial failures are
    # already in the run record and the logs.
    if status:
        raise RuntimeError(f"{job}: every request in the pass failed")
    return {"job": job, "status": status, "storage": store.describe()}


if __name__ == "__main__":  # local smoke test of the Lambda path, no AWS needed
    print(json.dumps(lambda_handler({"job": "archive"}, None)))
