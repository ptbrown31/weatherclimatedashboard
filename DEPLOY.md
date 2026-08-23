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
    aws s3 sync dist/embed s3://<BucketName>/embed/ --delete
    aws cloudfront create-invalidation --distribution-id <DistributionId> --paths "/*"

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
| Exchange endpoints unreachable from AWS (a CDN block; it happened to the owner's other system once) | The quote pass fails whole, snapshots stay as they were and the pages show their age; the `exchange` source reaches the streak alarm after six passes. Nothing to do but wait or move the quote job off AWS; there is no proxy in this stack. |
| Market overlay needs to go dark | `market_overlay.standalone` to `off` in `config/site.json`, rebuild and sync the site (step 6); the pipeline can keep quoting. |
| Cost check | S3 PUT count (about 200k a month) is the only meaningful line; `aws ce` or the billing console. |

## 10. The vendor lane (live-storm wind probabilities), optional

Off by default, and doubly gated: `sources.reask` in `config/site.json` must be `true` (then
repackage and upload the code, step 4) AND the function must carry the credential:

    aws cloudformation deploy ... --parameter-overrides ... ReaskApiKey=<the key>

The parameter is `NoEcho`: it is stored as a function environment variable (`WX_REASK_API_KEY`),
never in the repository or the template outputs. Paste it yourself; do not put it in a file the
repo can see. With both gates open the `market` schedule polls the vendor's listing every 10
minutes and fetches each new forecast cycle, interim and final file, archiving every file under
`data/archive/reask/`. `snapshots/reask.json` reports the lane's state either way, and the
hurricane page shows it.

Before enabling: the vendor's terms require its mark on every figure that shows the data and a
credential of the site's own, separate from any other system's pull. Confirm both with the vendor
in writing and keep the confirmation with the deployment records.

## Cloudflare alternative

`ops/cloudflare/README.md` describes serving the site and snapshots from Cloudflare (R2 + Pages)
while the pipeline keeps running on Lambda. The application code does not change.
