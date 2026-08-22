# Cloudflare as the serving target (alternative)

The application code is deployment-agnostic; Cloudflare can serve the site and the snapshots
while the Python pipeline keeps running on AWS Lambda (or any host with Python and a
scheduler). This is the "hybrid" option from the plan. A Cloudflare-only deployment would need
the pipeline rewritten for Workers (Python Workers are in open beta and lack `urllib`), which is
not done here.

Verified against Cloudflare's documentation on 2026-08-21: R2 exposes an S3-compatible API
(`https://<ACCOUNT_ID>.r2.cloudflarestorage.com`, region `auto`) and accepts `Cache-Control` on
PutObject; a public bucket on a custom hostname sits behind Cloudflare's cache (`r2.dev` is
rate-limited and for development only); Cloudflare does not cache JSON by default, so a Cache
Rule marking the snapshot path eligible is required; Pages Direct Upload deploys a local
directory without a git integration; static requests are free and unlimited.

## Steps

1. Create an R2 bucket and an API token with object read/write on it. Point the pipeline at it:

        WX_STORAGE_BACKEND=s3
        WX_STORAGE_BUCKET=<bucket>
        WX_STORAGE_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
        WX_STORAGE_REGION=auto
        WX_STORAGE_PREFIX=data

   plus the R2 API token pair in the two standard AWS credential variables boto3 reads (the
   access key id and the secret). On Lambda these go in the function's environment (the
   template's `Environment` block); the secret belongs in Secrets Manager or the function
   configuration, never in the repository.

2. Connect the bucket to a custom hostname (for example `data.<domain>`) in the R2 dashboard.
   Add a Cache Rule for that hostname: path starts with `/data/snapshots/` → eligible for
   cache, edge TTL "use cache-control header if present", browser TTL respect origin. Do not
   expose `/data/archive/`; a second rule can block it, or keep the archive in a separate
   private bucket by giving the archive job its own prefix.

3. Set `data_base_url` in `config/site.json` to `https://data.<domain>/data` and build:

        python3 scripts/build.py --deploy

4. Deploy the static site with Pages Direct Upload (or Workers Static Assets):

        npx wrangler pages deploy dist/standalone --project-name <project>
        npx wrangler pages deploy dist/embed --project-name <project>-embed

   and attach the custom domain in the Pages project settings.

Nothing in `site/` changes between targets; only `config.js` (generated) differs.
