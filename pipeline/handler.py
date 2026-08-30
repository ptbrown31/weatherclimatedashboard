"""
handler.py — the only vendor-specific file in the pipeline.

AWS Lambda entrypoint. EventBridge Scheduler invokes it with a small JSON
event naming the job; the function body is one call into the same code the
command line runs. Configuration arrives as WX_* environment variables on
the function (set in ops/aws/template.yaml), never as code.

    event = {"job": "half-hourly"}      # archive, then forecast, then hurricane
    event = {"job": "obs"}              # every 10 minutes

A pass may also compose a report (see report.py). The generic code writes it
to the archive and leaves it in archive.LAST_REPORT; this file delivers it,
which is the only place the pipeline knows how mail goes out here. Delivery
is SMTP when the WX_SMTP_* variables are set (see mailer.py for why that is
the carrier), and the SNS topic named by WX_REPORT_TOPIC_ARN otherwise, so a
deployment without mail credentials behaves as it did before.

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

The same function is also subscribed to the alarm topic, so a CloudWatch
alarm arrives here as an SNS event and goes out as mail. That covers the one
failure the pipeline cannot report on its own: the schedules stopping, where
nothing runs to notice that nothing ran. An SNS invocation is a different
path from the schedules, so it still fires when they do not.
"""
from __future__ import annotations
import json
import os

from . import config, storage
from .run import JOBS, _register


ALARM_MIN_GAP_H = 6            # a failing source must not mail on every pass
ALARM_STATE_KEY = "archive/_meta/alarm/last_mail.json"


def _deliver(subject: str, body: str, kind: str) -> bool:
    """One message out, by whichever carrier is configured.

    SMTP first, because it reaches an inbox; the SNS topic second, so a
    deployment with no mail credentials keeps whatever delivery it had. A
    failure is logged and swallowed by the caller: mail is never worth
    failing a pass that already did its work, and the message is on the
    archive either way.
    """
    from . import mailer
    if mailer.configured():
        mailer.send(subject, body)
        print(json.dumps({"kind": kind, "sent": True, "via": "smtp", "subject": subject}))
        return True
    arn = os.environ.get("WX_REPORT_TOPIC_ARN", "")
    if not arn:
        return False
    import boto3
    boto3.client("sns").publish(TopicArn=arn, Subject=subject[:100], Message=body)
    print(json.dumps({"kind": kind, "sent": True, "via": "sns", "subject": subject}))
    return True


def _send_report(archive) -> None:
    """Send a report the pass composed, where the site is deployed."""
    msg = getattr(archive, "LAST_REPORT", None)
    archive.LAST_REPORT = {}
    if not msg:
        return
    try:
        _deliver(msg["subject"], msg["body"], "report")
    except Exception as e:  # noqa: BLE001 - the pass itself already succeeded
        print(json.dumps({"kind": "report", "sent": False, "error": f"{type(e).__name__}: {e}"}))


def _send_alarm(store, job: str, alarms: list, archive) -> None:
    """Mail a health alarm, at most once every ALARM_MIN_GAP_H per alarm set.

    The obs pass runs every ten minutes, so a source that fails all day would
    otherwise send a hundred and forty messages and teach the reader to
    ignore the channel. The gap is keyed on the set of failing sources, so a
    NEW source failing mails immediately rather than waiting out the gap left
    by an existing one. This is a supplement, not a replacement: the handler
    still raises, so the CloudWatch alarm on the error metric fires as it
    always did.
    """
    import datetime as dt
    from . import mailer
    if not alarms or not mailer.configured():
        return
    key = ",".join(sorted(alarms))
    now = dt.datetime.now(dt.timezone.utc)
    try:
        raw = store.get(ALARM_STATE_KEY)
        state = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        state = {}
    last = state.get(key)
    if last:
        try:
            age_h = (now - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() / 3600
            if age_h < ALARM_MIN_GAP_H:
                return
        except (TypeError, ValueError):
            pass
    body = (f"{job}: source(s) failing for {archive.FAIL_STREAK_ALARM}+ passes in a row.\n\n"
            + "".join(f"  {a}\n" for a in sorted(alarms))
            + f"\nSeen {now.isoformat(timespec='seconds').replace('+00:00', 'Z')}.\n"
            "The pass itself is logged in CloudWatch under the pipeline function.\n")
    try:
        _deliver(f"Weather tools alarm: {key}", body, "alarm")
        state[key] = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        store.put(ALARM_STATE_KEY, json.dumps(state, separators=(",", ":")).encode(), "application/json")
    except Exception as e:  # noqa: BLE001 - the raise below still reaches CloudWatch
        print(json.dumps({"kind": "alarm", "sent": False, "error": f"{type(e).__name__}: {e}"}))


def _relay_alarm(event) -> dict:
    """Mail a CloudWatch alarm that arrived over SNS.

    Nothing in here may raise. A raise would mark this invocation as an
    error, which is the metric the error alarm watches, so a failing relay
    would keep re-alarming itself; CloudWatch would hold it to one message
    per state change, but the loop is pointless and this path is not worth
    failing for.
    """
    sent, seen = 0, 0
    for rec in (event.get("Records") or []):
        sns = rec.get("Sns") or {}
        seen += 1
        try:
            body = json.loads(sns.get("Message") or "{}")
        except ValueError:
            body = {}
        name = body.get("AlarmName") or sns.get("Subject") or "pipeline alarm"
        state = body.get("NewStateValue") or "ALARM"
        reason = body.get("NewStateReason") or (sns.get("Message") or "")
        when = body.get("StateChangeTime") or sns.get("Timestamp") or ""
        text = (f"{name} is {state}.\n\n{reason}\n\n"
                f"{body.get('AlarmDescription') or ''}\n\n"
                f"State changed {when}.\n"
                "The pipeline's own log is in CloudWatch under the pipeline function.\n")
        try:
            if _deliver(f"Weather tools alarm: {name} {state}", text, "alarm-relay"):
                sent += 1
        except Exception as e:  # noqa: BLE001 - never raise out of the relay
            print(json.dumps({"kind": "alarm-relay", "sent": False,
                              "error": f"{type(e).__name__}: {e}"}))
    return {"relayed": sent, "records": seen}


def lambda_handler(event, context):
    # a CloudWatch alarm reaches this function as an SNS event, not a job
    if isinstance(event, dict) and event.get("Records"):
        return _relay_alarm(event)
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
    _send_report(archive)
    alarms = (archive.LAST_STATUS or {}).get("alarms") or []
    _send_alarm(store, job, alarms, archive)
    if status:
        raise RuntimeError(f"{job}: every request in the pass failed")
    if alarms:
        raise RuntimeError(f"{job}: source(s) failing for {archive.FAIL_STREAK_ALARM}+ passes: {alarms}")
    return {"job": job, "status": status, "storage": store.kind()}


if __name__ == "__main__":  # local smoke test of the Lambda path, no AWS needed
    print(json.dumps(lambda_handler({"job": os.environ.get("WX_JOB", "archive")}, None)))
