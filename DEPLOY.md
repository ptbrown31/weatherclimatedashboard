# Deploy runbook

Every step here needs the owner's own accounts and credentials, so none of it is automated from
this repository. The order matters: the archive job is the first thing that should be running,
because forecast cycles it misses cannot be recovered.

Local verification comes first and needs nothing below:

    python3 -m unittest discover -s tests
    python3 scripts/verify.py

`ops/aws/deploy.sh` runs the AWS steps below as phases (`preflight`, `stack`, `code`, `seed`,
`site`, `check`), reading its settings from `ops/aws/deploy.env` (copy `deploy.env.example`).
The sections that follow are what each phase does, so either path can be used.

## 0. What you need

- An AWS account on the pay-as-you-go plan. (A new account on the credit-based Free plan closes
  itself after six months; the pipeline's Lambda and CloudFront usage sits inside the
  always-free limits on either plan, S3 PUTs are the only billed line, about $1–2 a month.)
- The AWS CLI, signed in to that account, with permission to create the resources in
  `ops/aws/template.yaml` (S3, CloudFront, Lambda, IAM roles, EventBridge Scheduler,
  CloudWatch, SNS).
- A domain, if the site is to have one, and the ability to add DNS records for it.
- `~/.weather-tools-site.scrub` on the machine you publish from: the out-of-repo list of
  internal names the scrub refuses to publish. One substring per line.

### If the AWS account is operated by someone else

When the account is administered by another team and you have no console access, ask them for
one of these, in order of preference:

1. **An IAM role attached to an EC2 instance you can reach** (EC2 → instance → Actions →
   Security → Modify IAM role), carrying the policy in `ops/aws/deployer-policy.json`. Then clone
   the repository on that instance and run every `ops/aws/deploy.sh` phase there; no access
   key exists anywhere. The instance needs `aws` (v2), Python 3.9+ and `python3 -m pip install
   boto3` for the seed phase.
2. **An IAM user with an access key**, carrying the same policy; you run the phases from your
   machine after `aws configure`, and the key can be deactivated when the deployment is done.
3. **They run the stack themselves**: `ops/aws/template.yaml` with the parameters in section 3,
   then the code, seed and site phases need the bucket and function names from the outputs.

The policy is scoped to resources named `weather-tools-site-*` wherever the service allows it;
CloudFront, ACM and CloudFormation do not support name scoping and are granted on `*`.

## 1. Repository

    python3 scripts/scrub.py          # must print "scrub clean"
    git remote add origin <your GitHub repository URL>
    git push -u origin main

The scrub runs before every push. The repository is public from the first push, so the scrub's
external list must exist before this step.

## 2. Certificate (only with a custom domain)

CloudFront needs the certificate in us-east-1 regardless of any other region:

    aws acm request-certificate --region us-east-1 --domain-name weather.example.com \
        --validation-method DNS

Add the CNAME record ACM shows (`aws acm describe-certificate ... --region us-east-1`) at your
DNS host and wait for status ISSUED. Keep the certificate ARN.

## 3. The stack

    aws cloudformation deploy --region us-east-1 \
        --stack-name weather-tools-site \
        --template-file ops/aws/template.yaml \
        --capabilities CAPABILITY_IAM \
        --parameter-overrides \
            UserAgent="weather-tools-site/0.1 (you@example.com)" \
            DomainName=weather.example.com \
            CertificateArn=arn:aws:acm:us-east-1:...:certificate/... \
            AlarmEmail=you@example.com

Omit `DomainName` and `CertificateArn` to run on the CloudFront hostname only. The stack also
creates the `market` schedule (every 10 minutes, offset from the observation job) that quotes
the exchange's listed contracts; no credential is involved. `ReaskApiKey` is an optional
parameter for the vendor lane (section 10). Outputs:

    aws cloudformation describe-stacks --stack-name weather-tools-site \
        --query "Stacks[0].Outputs" --output table

Confirm the SNS subscription from the email AWS sends, or the alarms go nowhere.

## 4. The pipeline code

The stack creates the function with a placeholder body. Package and upload the real one:

    python3 scripts/package_lambda.py --with-tzdata
    aws lambda update-function-code --function-name weather-tools-site-pipeline \
        --zip-file fileb://dist/lambda.zip

Smoke test it before the schedules fire (each schedule is already created and active):

    aws lambda invoke --function-name weather-tools-site-pipeline \
        --cli-binary-format raw-in-base64-out --payload '{"job":"archive"}' /dev/stdout

A successful response is `{"job": "archive", "status": 0, "storage": "s3"}`. If it raises on
`ZoneInfo`, the package was built without `--with-tzdata`.

## 5. Seed the archive and backfill observations

From the machine that holds the existing archive copy:

    python3 scripts/seed_archive.py /path/to/archive --backend s3 \
        --bucket <BucketName output> --prefix data
    python3 scripts/backfill_obs.py --from 2026-08-13 --backend s3 \
        --bucket <BucketName output> --prefix data

Both are safe to repeat; neither overwrites. aviationweather.gov holds 30 days, so the backfill
cannot reach further back than that from the day it runs.

## 6. The site

Set `data_base_url` in `config/site.json` to `/data` (the default) and `domain` to the site's
domain, then build for deployment and upload both targets:

    python3 scripts/build.py --deploy
    aws s3 sync dist/standalone s3://<BucketName>/ --delete --exclude "data/*" --exclude "embed/*"
    aws s3 cp dist/standalone/js s3://<BucketName>/js/ --recursive \
        --cache-control "public, max-age=3600, stale-while-revalidate=86400"
    aws s3 cp dist/standalone/css s3://<BucketName>/css/ --recursive \
        --cache-control "public, max-age=3600, stale-while-revalidate=86400"
    aws s3 cp dist/standalone/assets s3://<BucketName>/assets/ --recursive \
        --cache-control "public, max-age=86400, stale-while-revalidate=604800"
    aws s3 cp dist/standalone s3://<BucketName>/ --recursive --exclude "*" --include "*.html" \
        --include "config.js" --cache-control "public, max-age=60, stale-while-revalidate=300"
    aws s3 sync dist/embed s3://<BucketName>/embed/ --delete        # then the same cp passes under embed/
    aws cloudfront create-invalidation --distribution-id <DistributionId> --paths "/*"

`ops/aws/deploy.sh site` does all of this; the sync places and prunes files, and the `cp` passes
set the browser cache headers, which a `sync` alone would skip on any file whose bytes did not
change. Filenames are not content-hashed, so each `max-age` is also the longest a browser can go
on the old file after a deploy: entry points a minute, code an hour, projected geometry a day.
Lengthen them only alongside hashed filenames. Snapshots under `data/` are not touched here; they
carry the header the pipeline writes (`pipeline/snapshots.py`).

Never sync with a bare `--delete` over the bucket root without the `data/` exclusion: the
pipeline's objects live under `data/`.

The embed is served at `https://<domain>/embed/?station=KLAX` with optional `theme=light|dark`
and `market=on|off`.

## 7. DNS

With a custom domain, add at your DNS host:

    weather.example.com   CNAME   <DistributionDomain output>

(or an ALIAS/A record to the distribution if the host supports it at the apex). Propagation
takes minutes to an hour.

## 8. Check that it is alive

- `https://<domain>/data/snapshots/manifest.json` shows `asof` values within the cadences and
  `alarms: []`.
- CloudWatch → Log groups → `/aws/lambda/weather-tools-site-pipeline` shows one JSON line per
  request; `archive/_runs/latest.json` in the bucket is the newest pass.
- The `weather-tools-site-pipeline-errors` alarm is OK; `pipeline-silent` is OK.

## 9. Operations

| Situation | Action |
| --- | --- |
| Error alarm | Read the last invocation's log. The handler raises when every request in a pass failed or a source has failed for six passes; `data/archive/_meta/health.json` names the source. |
| Observation gap longer than 72 h | `manifest.json` lists stations under `obsUnhealed`; run `scripts/backfill_obs.py` within 30 days. |
| Forecast bulletins missed (NOMADS outage) | The archive catches up from the bucket for about two days; nothing to do unless the outage was longer, in which case those NBM cycles are gone. |
| Change the roster or decode constants | Edit `pipeline/cities.py` / `pipeline/gov_weather.py`, run `scripts/build_assets.py`, repackage (step 4) and rebuild the site (step 6). |
| Rotate the User-Agent contact | `aws cloudformation deploy ... --parameter-overrides UserAgent=...` |
| Exchange endpoints unreachable from AWS (a CDN in front of them can block an address range) | The quote pass fails whole, snapshots stay as they were and the pages show their age; the `exchange` source reaches the streak alarm after six passes. Nothing to do but wait or move the quote job off AWS; there is no proxy in this stack. |
| Market overlay needs to go dark | `market_overlay.standalone` to `off` in `config/site.json`, rebuild and sync the site (step 6); the pipeline can keep quoting. |
| Traffic cost check | CloudFront's free tier is 1 TB out, 10M requests and 2M function invocations a month. A cold page view measures about 82 KB, 12 requests and 8.5 function invocations, so the function allowance binds first, at roughly 235k views a month; past that the cost is a few dollars per million views. Nothing in the request path can be overloaded: the pipeline runs on a schedule, not on visits. |
| Cost check | S3 PUTs are the only meaningful line: about 200k a month for the weather jobs plus about 200k for the quote job (45 objects a pass, 144 passes a day), roughly $2 a month in total; Lambda stays inside the always-free allowance (the quote pass is about 90 s at 512 MB). `aws ce` or the billing console. |

## 10. The vendor lane (live-storm wind probabilities)

`sources.reask` in `config/site.json` is `true`, so the lane runs as soon as the function carries
the vendor's API base URL and the credential; without them every pass writes a status snapshot
saying so and the hurricane page reports the lane off. `REASK_BASE_URL` lives in
`ops/aws/deploy.env`; the key is appended to that gitignored file by the owner (or exported in
the shell), never written by a script and never shown:

    chmod 600 ops/aws/deploy.env
    printf 'REASK_API_KEY=%s\n' "$(read -rs k; echo "$k")" >> ops/aws/deploy.env   # paste, Enter; nothing echoes
    ./ops/aws/deploy.sh vendor

Both values become `NoEcho` stack parameters and function environment variables
(`WX_REASK_BASE_URL`, `WX_REASK_API_KEY`), never template outputs. The base URL must be https; the
client refuses redirects so the key is never re-sent to another host. Every later `stack` deploy
reads the same two variables from `deploy.env`, so they persist; if they are missing from the
environment at that time, CloudFormation resets them to empty and the lane switches itself off.
With both in place the `market` schedule polls the vendor's listing every 10 minutes and fetches
each new forecast cycle, interim and final file, archiving every file under `data/archive/reask/`.
`snapshots/reask.json` reports the lane's state either way.

Every figure that shows the vendor's data carries its mark (the hurricane page does this
itself). The first real storm delivery should be checked against `data/archive/reask/` by hand:
the parser was built from the documented file shapes without a live storm to test on.

## 11. The energy series (needs a free EIA key)

The energy contracts settle on series the Energy Information Administration publishes, and its API
wants a key. It is free and per-person, from eia.gov/opendata. Without one the energy lane writes
nothing, says so in the daily pass, and the two energy pages fall back to listing strikes; nothing
else is affected.

Put it in `ops/aws/deploy.env` (gitignored, and the same file the vendor lane's key lives in):

    EIA_API_KEY=...

then deploy the stack and run the series job once so the pages have data before the next daily pass:

    ops/aws/deploy.sh stack
    aws lambda invoke --function-name <stack>-pipeline --cli-binary-format raw-in-base64-out \
      --payload '{"job":"series"}' --cli-read-timeout 900 /dev/stdout

The key reaches the pipeline as `WX_EIA_API_KEY`, a NoEcho stack parameter. It is never written to
the repository, and `scripts/scrub.py` refuses a commit that puts it in `config/site.json`.

Every mapping from a contract to a series was checked against the strikes the exchange has listed
rather than inferred from the contract's name. `pipeline/energy.py` records which readings are
easy to get wrong and why.

## 11. The accuracy curve (run from the machine that captures it)

The accuracy page draws forecast error against lead time for the National Weather Service
station forecast and the price-implied high. That comparison is captured hourly by a separate
system into a SQLite file on the owner's machine. The Lambda has no route to that file, and the
site's own archive only reaches back to the day it started, which is a fraction of the record,
so the curve is published from the machine that holds it:

    WX_STORAGE_BACKEND=s3 \
    WX_STORAGE_BUCKET=<bucket> WX_STORAGE_REGION=<region> WX_STORAGE_PREFIX=data \
    python3 scripts/export_accuracy.py

`--dry-run` prints the curve and writes nothing. `--db` points at another capture file.

It reads, reduces to a few hundred numbers, and writes one snapshot; it never uploads a row of
the underlying data. Re-run it whenever the curve should catch up — the page states the window
it covers, so a stale file is visible rather than silent. Nothing else depends on it, and the
page says the measurement has not been published yet if the file is missing.

## Cloudflare alternative

`ops/cloudflare/README.md` describes serving the site and snapshots from Cloudflare (R2 + Pages)
while the pipeline keeps running on Lambda. The application code does not change.

## 8. Traffic counts

The stack creates a second bucket, `<SiteName>-logs-<account>`, and turns on
CloudFront standard access logging into it under `cf/`. That bucket exists only
to receive logs: no CloudFront origin points at it, public access is blocked,
and a lifecycle rule expires objects after `AccessLogRetentionDays` (default
30). It allows bucket-owner-preferred object ownership because CloudFront's
standard logging writes with an ACL; the site bucket keeps ACLs disabled.

The `traffic` job runs inside the daily chain, reads the previous UTC day's log
files, and writes `data/archive/_meta/traffic/<day>.json` plus a rolling
`summary.json`. Those sit under the archive prefix, which the bucket policy
denies to CloudFront, so traffic figures are readable from the account and never
from the public site. The raw logs expire; the summaries do not.

Read them with:

    python3 scripts/traffic.py                    the last two weeks
    python3 scripts/traffic.py --day 2026-08-23   one day in full

A view is an HTML document request, not a request — a cold page view pulls about
a dozen objects, so counting requests overstates readership roughly tenfold. A
visitor is a distinct client address, which undercounts shared networks and
overcounts phones changing towers.

Set `WX_TRAFFIC_LOG_BUCKET` empty to turn the job off; it then reports that no
log bucket is configured and exits cleanly, which is the state of a clean
checkout.
