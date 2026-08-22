"""
Storage adapter: the one seam between the pipeline and the hosting target.

Two backends with the same four operations. `local` writes a directory tree
(the local mode every reviewer can run). `s3` writes any S3-compatible bucket
through boto3: Amazon S3 itself, or Cloudflare R2 by setting an endpoint.
Nothing above this module knows which one is in use.

Keys are forward-slash paths relative to the data root, e.g.
"archive/KLAX/hourly_20260821T195218Z.json.gz" or "snapshots/summary.json".

`put_if_absent` is what the append-only archive relies on: a cycle already
stored is never written twice, and a retried scheduled run cannot duplicate
anything. On S3 this uses a conditional write where the service supports it
(`If-None-Match: *`) and falls back to an existence check where it does not.
"""
from __future__ import annotations
import os
import tempfile
from typing import Optional


class Storage:
    def get(self, key: str) -> Optional[bytes]:
        raise NotImplementedError

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream",
            cache_control: Optional[str] = None) -> None:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def list(self, prefix: str) -> list:
        raise NotImplementedError

    def put_if_absent(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
        """Write only if nothing is stored under key. Returns True if written."""
        if self.exists(key):
            return False
        self.put(key, data, content_type)
        return True

    def describe(self) -> str:
        raise NotImplementedError


class LocalStorage(Storage):
    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    def _path(self, key: str) -> str:
        p = os.path.normpath(os.path.join(self.root, key))
        if not p.startswith(self.root):
            raise ValueError(f"key escapes the data root: {key}")
        return p

    def get(self, key: str) -> Optional[bytes]:
        try:
            with open(self._path(key), "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream",
            cache_control: Optional[str] = None) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Write to a sibling temp file and rename, so a reader (the local static
        # server, or a browser in local mode) never sees a half-written object.
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def exists(self, key: str) -> bool:
        return os.path.isfile(self._path(key))

    def list(self, prefix: str) -> list:
        base = self._path(prefix) if prefix else self.root
        out = []
        if os.path.isdir(base):
            for dirpath, _dirs, files in os.walk(base):
                for fn in files:
                    if fn.startswith(".tmp-"):
                        continue
                    full = os.path.join(dirpath, fn)
                    out.append(os.path.relpath(full, self.root).replace(os.sep, "/"))
        elif os.path.isfile(base):
            out.append(prefix)
        else:
            # prefix may be a partial filename, e.g. "archive/KLAX/hourly_"
            d, stem = os.path.split(base)
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    if fn.startswith(stem) and not fn.startswith(".tmp-"):
                        out.append(os.path.relpath(os.path.join(d, fn), self.root).replace(os.sep, "/"))
        return sorted(out)

    def describe(self) -> str:
        return f"local:{self.root}"


class S3Storage(Storage):
    def __init__(self, bucket: str, prefix: str = "", endpoint: str = "", region: str = ""):
        import boto3  # only imported when this backend is configured
        from botocore.config import Config
        if not bucket:
            raise ValueError("s3 backend needs a bucket name (WX_STORAGE_BUCKET)")
        kwargs = {}
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        if region:
            kwargs["region_name"] = region
        self.client = boto3.client("s3", config=Config(retries={"max_attempts": 4, "mode": "standard"}), **kwargs)
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._conditional_ok = True

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def get(self, key: str) -> Optional[bytes]:
        try:
            r = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
            return r["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None
        except self.client.exceptions.ClientError as e:  # pragma: no cover
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream",
            cache_control: Optional[str] = None) -> None:
        extra = {"ContentType": content_type}
        if cache_control:
            extra["CacheControl"] = cache_control
        if key.endswith(".gz"):
            extra["ContentEncoding"] = "identity"   # stored compressed, served as-is
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data, **extra)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except self.client.exceptions.ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def put_if_absent(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
        if self._conditional_ok:
            try:
                self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data,
                                       ContentType=content_type, IfNoneMatch="*")
                return True
            except self.client.exceptions.ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code in ("PreconditionFailed", "412"):
                    return False
                if code in ("NotImplemented", "InvalidArgument", "InvalidRequest"):
                    self._conditional_ok = False   # backend lacks conditional writes
                else:
                    raise
            except (TypeError, ValueError):
                self._conditional_ok = False        # older boto3 without the parameter
        return Storage.put_if_absent(self, key, data, content_type)

    def list(self, prefix: str) -> list:
        out = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self._key(prefix)):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                if self.prefix:
                    k = k[len(self.prefix) + 1:]
                out.append(k)
        return sorted(out)

    def describe(self) -> str:
        return f"s3:{self.bucket}/{self.prefix}" + (f" @ {self.client.meta.endpoint_url}" if self.client.meta.endpoint_url else "")


def from_config(cfg: dict) -> Storage:
    st = cfg.get("storage", {})
    backend = st.get("backend", "local")
    if backend == "local":
        return LocalStorage(st.get("root", "./data"))
    if backend == "s3":
        return S3Storage(st.get("bucket", ""), st.get("prefix", ""), st.get("endpoint", ""), st.get("region", ""))
    raise ValueError(f"unknown storage backend {backend!r} (expected local or s3)")
