"""
Configuration: config/site.json, overridden by environment variables.

One place reads the config so that nothing vendor- or environment-specific is
written into application code. The storage backend, bucket, endpoint, domain
and User-Agent are all values here, never constants elsewhere.

Environment overrides (highest precedence):

    WX_CONFIG              path to an alternative site.json
    WX_STORAGE_BACKEND     local | s3
    WX_STORAGE_ROOT        local backend: directory that holds the data tree
    WX_STORAGE_BUCKET      s3 backend: bucket name
    WX_STORAGE_ENDPOINT    s3 backend: endpoint URL (set for R2, empty for S3)
    WX_STORAGE_PREFIX      s3 backend: key prefix inside the bucket
    WX_STORAGE_REGION      s3 backend: region (R2 uses "auto")
    WX_USER_AGENT          the User-Agent sent to every government endpoint
    WX_DOMAIN              the site's domain
"""
from __future__ import annotations
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, "config", "site.json")

_ENV = {
    ("storage", "backend"): "WX_STORAGE_BACKEND",
    ("storage", "root"): "WX_STORAGE_ROOT",
    ("storage", "bucket"): "WX_STORAGE_BUCKET",
    ("storage", "endpoint"): "WX_STORAGE_ENDPOINT",
    ("storage", "prefix"): "WX_STORAGE_PREFIX",
    ("storage", "region"): "WX_STORAGE_REGION",
    ("user_agent",): "WX_USER_AGENT",
    ("domain",): "WX_DOMAIN",
}


def load(path: str | None = None) -> dict:
    path = path or os.environ.get("WX_CONFIG") or DEFAULT_PATH
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    for keys, var in _ENV.items():
        val = os.environ.get(var)
        if val is None:
            continue
        node = cfg
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = val
    # A relative local root is relative to the repo, not the working directory,
    # so `python -m pipeline.run` behaves the same from any cwd.
    st = cfg.setdefault("storage", {})
    if st.get("backend", "local") == "local":
        root = st.get("root") or "./data"
        if not os.path.isabs(root):
            root = os.path.normpath(os.path.join(ROOT, root))
        st["root"] = root
    return cfg
