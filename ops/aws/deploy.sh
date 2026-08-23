#!/usr/bin/env bash
# deploy.sh — the AWS steps from DEPLOY.md, one phase per command.
#
#   ops/aws/deploy.sh preflight   # credentials, region, tools
#   ops/aws/deploy.sh stack       # create or update the CloudFormation stack
#   ops/aws/deploy.sh code        # package the pipeline, upload it, smoke-test one job
#   ops/aws/deploy.sh seed        # import an existing archive and backfill observations into S3
#   ops/aws/deploy.sh site        # build both targets for deployment and upload them
#   ops/aws/deploy.sh check       # is it alive: manifest as-of, alarms, schedules
#   ops/aws/deploy.sh vendor      # the live-storm lane: stack deploy with the vendor base URL and key
#
# Settings come from ops/aws/deploy.env (copy deploy.env.example; gitignored)
# or from the environment. Nothing here stores a credential: the AWS CLI's own
# configuration is used. Every phase is safe to re-run.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ENV_FILE="${DEPLOY_ENV:-$HERE/deploy.env}"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a
fi

STACK="${STACK:-weather-tools-site}"
REGION="${REGION:-us-east-1}"
USER_AGENT="${USER_AGENT:-}"
DOMAIN="${DOMAIN:-}"
CERT_ARN="${CERT_ARN:-}"
ALARM_EMAIL="${ALARM_EMAIL:-}"
ARCHIVE_SRC="${ARCHIVE_SRC:-}"          # local directory holding {STATION}/hourly_*.json.gz ...
BACKFILL_FROM="${BACKFILL_FROM:-}"      # YYYY-MM-DD, at most 30 days ago
export AWS_DEFAULT_REGION="$REGION"

say()  { printf '\n== %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 is not installed ($2)"; }

output() {  # stack output by key
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

phase_preflight() {
  need aws "brew install awscli"
  need python3 "python 3.9+"
  say "AWS identity"
  aws sts get-caller-identity --output table || die "not signed in; run 'aws configure' or 'aws configure sso'"
  say "region: $REGION   stack: $STACK"
  [ -n "$USER_AGENT" ] || die "USER_AGENT is empty; set it in $ENV_FILE, e.g. weather-tools-site/0.1 (you@example.com)"
  if [ -n "$DOMAIN" ] && [ -z "$CERT_ARN" ]; then die "DOMAIN is set but CERT_ARN is empty (request the certificate in us-east-1 first)"; fi
  python3 -c "import boto3" 2>/dev/null || echo "note: boto3 is not installed; the seed phase needs it (python3 -m pip install boto3)"
  say "repository checks"
  (cd "$ROOT" && python3 scripts/scrub.py)
  (cd "$ROOT" && python3 -m unittest discover -s tests 2>&1 | tail -1)
  say "preflight ok"
}

phase_stack() {
  need aws "brew install awscli"
  [ -n "$USER_AGENT" ] || die "USER_AGENT is empty"
  # the vendor lane's two values ride along whenever they are set (deploy.env or the
  # environment), so a later stack deploy never resets them to empty; neither is echoed
  local vendor_params=()
  [ -n "${REASK_BASE_URL:-}" ] && vendor_params+=("ReaskBaseUrl=$REASK_BASE_URL")
  [ -n "${REASK_API_KEY:-}" ] && vendor_params+=("ReaskApiKey=$REASK_API_KEY")
  say "deploying stack $STACK in $REGION (this takes 5-15 minutes: CloudFront is slow to create)"
  [ ${#vendor_params[@]} -eq 2 ] && say "vendor lane parameters: set (values not shown)"
  aws cloudformation deploy \
    --stack-name "$STACK" \
    --template-file "$HERE/template.yaml" \
    --capabilities CAPABILITY_IAM \
    --no-fail-on-empty-changeset \
    --parameter-overrides \
      "SiteName=$STACK" \
      "UserAgent=$USER_AGENT" \
      "DomainName=$DOMAIN" \
      "CertificateArn=$CERT_ARN" \
      "AlarmEmail=$ALARM_EMAIL" \
      "${vendor_params[@]}"
  say "outputs"
  aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs" --output table
  [ -n "$ALARM_EMAIL" ] && echo "Confirm the SNS subscription email from AWS, or the alarms go nowhere."
  true
}

phase_vendor() {
  # the live-storm lane: both values must be present; they go to the function as
  # NoEcho parameters and nothing here prints them
  [ -n "${REASK_BASE_URL:-}" ] || die "REASK_BASE_URL is empty (deploy.env or the environment)"
  [ -n "${REASK_API_KEY:-}" ] || die "REASK_API_KEY is empty (deploy.env or the environment; see DEPLOY.md section 10)"
  case "$REASK_BASE_URL" in https://*) ;; *) die "REASK_BASE_URL must be https" ;; esac
  phase_stack
  say "the next market pass (every 10 minutes) polls the vendor; check with: ops/aws/deploy.sh check"
}

phase_code() {
  need aws "brew install awscli"
  local fn; fn="$(output FunctionName)"
  [ -n "$fn" ] || die "stack has no FunctionName output; run the stack phase first"
  say "packaging the pipeline"
  (cd "$ROOT" && python3 scripts/package_lambda.py --with-tzdata)
  say "uploading to $fn"
  aws lambda update-function-code --function-name "$fn" --zip-file "fileb://$ROOT/dist/lambda.zip" \
    --query "{Function:FunctionName,Size:CodeSize,Updated:LastModified}" --output table
  aws lambda wait function-updated --function-name "$fn"
  say "smoke test: one archive pass (takes 1-5 minutes; downloads the NBM bulletins)"
  aws lambda invoke --function-name "$fn" --cli-binary-format raw-in-base64-out \
    --payload '{"job":"archive"}' --cli-read-timeout 900 /dev/stdout
  echo
  echo "Expected: {\"job\": \"archive\", \"status\": 0, \"storage\": \"s3\"}. A traceback means the package or permissions are wrong; read the log:"
  echo "  aws logs tail /aws/lambda/$fn --since 15m"
}

phase_seed() {
  need aws "brew install awscli"
  python3 -c "import boto3" 2>/dev/null || die "boto3 is needed on this machine for the seed: python3 -m pip install boto3"
  local bucket; bucket="$(output BucketName)"
  [ -n "$bucket" ] || die "stack has no BucketName output; run the stack phase first"
  if [ -n "$ARCHIVE_SRC" ]; then
    [ -d "$ARCHIVE_SRC" ] || die "ARCHIVE_SRC=$ARCHIVE_SRC is not a directory"
    say "seeding the archive from $ARCHIVE_SRC into s3://$bucket/data/"
    (cd "$ROOT" && python3 scripts/seed_archive.py "$ARCHIVE_SRC" --backend s3 --bucket "$bucket" --prefix data)
  else
    echo "ARCHIVE_SRC not set; skipping the archive seed"
  fi
  if [ -n "$BACKFILL_FROM" ]; then
    say "backfilling observations from $BACKFILL_FROM (about 20 seconds per day)"
    (cd "$ROOT" && python3 scripts/backfill_obs.py --from "$BACKFILL_FROM" --backend s3 --bucket "$bucket" --prefix data)
  else
    echo "BACKFILL_FROM not set; skipping the observation backfill"
  fi
}

# Browser caching for the site files. The snapshots under data/ carry their own
# header from the pipeline (pipeline/snapshots.py) and are never touched here.
#
# Filenames are not content-hashed, so every max-age below is also the longest a
# browser can keep serving code from before a deploy reaches it. The entry points
# stay short so a deploy goes live promptly; code gets an hour, which covers a
# reading session without stranding anyone on old JavaScript for long; the
# projected geometry gets a day, because it only changes when the roster or the
# basemap does. stale-while-revalidate lets the browser paint from cache and
# refresh behind the render.
CC_ENTRY="public, max-age=60, stale-while-revalidate=300"
CC_CODE="public, max-age=3600, stale-while-revalidate=86400"
CC_ASSET="public, max-age=86400, stale-while-revalidate=604800"

# Stamp one target's files with those headers. `sync --delete` runs first, to
# place files and prune anything no longer built; the `cp` passes then rewrite
# every object with its header, because `sync` skips files whose bytes are
# unchanged and a header-only edit would never reach them.
upload_target() {
  local src="$1" dest="$2"; shift 2
  aws s3 sync "$src" "$dest" --delete "$@"
  # `if` blocks, not `&&`: the embed target has no assets/ directory and set -e
  # would abort on a false test
  if [ -d "$src/js" ]; then
    aws s3 cp "$src/js/" "$dest/js/" --recursive --only-show-errors --cache-control "$CC_CODE"
  fi
  if [ -d "$src/css" ]; then
    aws s3 cp "$src/css/" "$dest/css/" --recursive --only-show-errors --cache-control "$CC_CODE"
  fi
  if [ -d "$src/assets" ]; then
    aws s3 cp "$src/assets/" "$dest/assets/" --recursive --only-show-errors --cache-control "$CC_ASSET"
  fi
  aws s3 cp "$src" "$dest" --recursive --only-show-errors --cache-control "$CC_ENTRY" \
      --exclude "*" --include "*.html" --include "config.js"
}

phase_site() {
  need aws "brew install awscli"
  local bucket dist; bucket="$(output BucketName)"; dist="$(output DistributionId)"
  [ -n "$bucket" ] || die "stack has no BucketName output; run the stack phase first"
  say "building both targets for deployment (no sample data bundled)"
  (cd "$ROOT" && WX_DOMAIN="$DOMAIN" python3 scripts/build.py --deploy)
  say "uploading the standalone site to s3://$bucket/ (data/ and embed/ are never touched by this sync)"
  upload_target "$ROOT/dist/standalone" "s3://$bucket" --exclude "data/*" --exclude "embed/*"
  say "uploading the embed to s3://$bucket/embed/"
  upload_target "$ROOT/dist/embed" "s3://$bucket/embed"
  say "invalidating the CDN cache"
  aws cloudfront create-invalidation --distribution-id "$dist" --paths "/*" --query "Invalidation.{Id:Id,Status:Status}" --output table
  say "site: $(output SiteUrl)"
}

phase_check() {
  need aws "brew install awscli"
  local url fn; url="$(output SiteUrl)"; fn="$(output FunctionName)"
  say "manifest at ${url}data/snapshots/manifest.json"
  curl -sS --max-time 30 "${url}data/snapshots/manifest.json" | python3 -c '
import json,sys,datetime as dt
m=json.load(sys.stdin); now=dt.datetime.now(dt.timezone.utc)
for k,v in m["asof"].items():
    age = None if not v else round((now-dt.datetime.fromisoformat(v.replace("Z","+00:00"))).total_seconds()/60)
    print(f"  {k:10s} as of {v}  ({age} min ago)" if v else f"  {k:10s} none yet")
print("  alarms:", m.get("alarms"), " market lane alarms:", m.get("marketAlarms")); print("  archive days:", m.get("archiveDays")); print("  unhealed obs gaps:", m.get("obsUnhealed")); print("  vendor lane enabled:", m.get("reaskEnabled"))' \
    || echo "  manifest not served yet (run the site and code phases; the obs job writes it within 10 minutes)"
  say "schedules"
  aws scheduler list-schedules --name-prefix "$STACK" --query "Schedules[].{Name:Name,State:State}" --output table
  say "quote lane: ${url}data/snapshots/market/summary.json"
  curl -sS --max-time 30 "${url}data/snapshots/market/summary.json" | python3 -c '
import json, sys
m = json.load(sys.stdin)
print("  as of:", m.get("asof"), " quoted:", m.get("quoted"), " failed:", m.get("failed"), " listed stations:", sum(1 for c in m.get("cities", []) if c.get("listed")))
print("  roster stations without a listed symbol:", sorted((m.get("unmatched") or {}).keys()) or "none", " partial ladders kept:", m.get("partialKept"))' \
    || echo "  quote summary not served yet (the market job writes it within 10 minutes)"

  say "alarms"
  aws cloudwatch describe-alarms --alarm-name-prefix "$STACK" --query "MetricAlarms[].{Alarm:AlarmName,State:StateValue}" --output table
  say "last 20 log lines"
  aws logs tail "/aws/lambda/$fn" --since 1h --format short 2>/dev/null | tail -20 || echo "  no invocations yet"
}

case "${1:-}" in
  preflight) phase_preflight ;;
  stack)     phase_stack ;;
  code)      phase_code ;;
  seed)      phase_seed ;;
  site)      phase_site ;;
  check)     phase_check ;;
  vendor)    phase_vendor ;;
  all)       phase_preflight; phase_stack; phase_code; phase_seed; phase_site; phase_check ;;
  *) sed -n '2,14p' "$0"; exit 2 ;;
esac
