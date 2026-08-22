# Deploy runbook

Every step here needs the owner's own accounts and credentials, so none of it is automated from
this repository. The order matters: the archive job is the first thing that should be running,
because forecast cycles it misses cannot be recovered.

Local verification comes first and needs nothing below:

    python3 -m unittest discover -s tests
    python3 scripts/verify.py

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

Omit `DomainName` and `CertificateArn` to run on the CloudFront hostname only. Outputs:

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
| Cost check | S3 PUT count (about 200k a month) is the only meaningful line; `aws ce` or the billing console. |

## Cloudflare alternative

`ops/cloudflare/README.md` describes serving the site and snapshots from Cloudflare (R2 + Pages)
while the pipeline keeps running on Lambda. The application code does not change.
