"""
Storage adapter: the one seam between the pipeline and the hosting target.

Two backends with the same operations. `local` writes a directory tree
(the local mode every reviewer can run). `s3` writes any S3-compatible bucket
through boto3: Amazon S3 itself, or Cloudflare R2 by setting an endpoint.
Nothing above this module knows which one is in use.

Keys are forward-slash paths relative to the data root, e.g.
"archive/KLAX/hourly_20260821T195218Z.json.gz" or "snapshots/summary.json".
list(prefix) is a plain key-prefix scan on both backends, so code tested
locally behaves the same against a bucket.

`put_if_absent` is what the append-only archive relies on: a cycle already
stored is never written twice, and a retried scheduled run cannot duplicate
anything. On S3 this uses a conditional write (`If-None-Match: *`) where the
service and the installed boto3 support it, and an existence check otherwise.

Archive objects ending in .gz are stored as opaque gzip files (Content-Type
application/gzip, no Content-Encoding), because the archive is read by the
pipeline, not by browsers. The browser's surface is snapshots/, which is
plain JSON. Do not fetch archive objects from a page.
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

    def delete(self, key: str) -> None:
        """Remove one key; nothing happens when it is already absent."""
        raise NotImplementedError

    def list(self, prefix: str, start_after: Optional[str] = None) -> list:
        """Keys under prefix in sorted order; with start_after, only keys
        that sort after it (S3's StartAfter), which bounds a listing of
        stamped archive keys to a time window."""
        raise NotImplementedError

    def put_if_absent(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
        """Write only if nothing is stored under key. Returns True if written."""
        if self.exists(key):
            return False
        self.put(key, data, content_type)
        return True

    def kind(self) -> str:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError


class LocalStorage(Storage):
    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    def _path(self, key: str) -> str:
        if not key or any(part in ("..", "") for part in key.split("/")):
            raise ValueError(f"bad key: {key!r}")
        p = os.path.normpath(os.path.join(self.root, key))
        if p == self.root or not p.startswith(self.root + os.sep):
            raise ValueError(f"key escapes the data root: {key!r}")
        return p

    def get(self, key: str) -> Optional[bytes]:
        try:
            with open(self._path(key), "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def delete(self, key: str) -> None:
        try:
            os.remove(self._path(key))
        except FileNotFoundError:
            pass

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream",
            cache_control: Optional[str] = None) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Write to a sibling temp file, flush it to disk, then rename, so a
        # reader never sees a half-written object and a crash after the
        # rename cannot leave an empty one.
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def exists(self, key: str) -> bool:
        p = self._path(key)
        # a zero-length object is treated as absent so a damaged write-once
        # object can be fetched again while the source still has it
        return os.path.isfile(p) and os.path.getsize(p) > 0

    def list(self, prefix: str, start_after: Optional[str] = None) -> list:
        # walk only the directory the prefix names, so a bounded listing of
        # one station does not scan the whole data root
        top = os.path.join(self.root, os.path.dirname(prefix)) if prefix else self.root
        if not os.path.isdir(top):
            return []
        out = []
        for dirpath, _dirs, files in os.walk(top):
            for fn in files:
                if fn.startswith(".tmp-"):
                    continue
                key = os.path.relpath(os.path.join(dirpath, fn), self.root).replace(os.sep, "/")
                if key.startswith(prefix) and (start_after is None or key > start_after):
                    out.append(key)
        return sorted(out)

    def kind(self) -> str:
        return "local"

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
        # decide once whether this boto3 knows conditional writes
        try:
            members = self.client.meta.service_model.operation_model("PutObject").input_shape.members
            self._conditional_ok = "IfNoneMatch" in members
        except Exception:
            self._conditional_ok = False

    def _key(self, key: str) -> str:
        if not key or any(part in ("..", "") for part in key.split("/")):
            raise ValueError(f"bad key: {key!r}")
        return f"{self.prefix}/{key}" if self.prefix else key

    @staticmethod
    def _code(e) -> str:
        return str(e.response.get("Error", {}).get("Code", "")) if hasattr(e, "response") else ""

    def get(self, key: str) -> Optional[bytes]:
        try:
            r = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
            return r["Body"].read()
        except self.client.exceptions.ClientError as e:
            if self._code(e) in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream",
            cache_control: Optional[str] = None) -> None:
        extra = {"ContentType": content_type}
        if cache_control:
            extra["CacheControl"] = cache_control
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data, **extra)

    def exists(self, key: str) -> bool:
        try:
            r = self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return r.get("ContentLength", 1) > 0
        except self.client.exceptions.ClientError as e:
            if self._code(e) in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def put_if_absent(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
        if self._conditional_ok:
            for attempt in range(2):
                try:
                    self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data,
                                           ContentType=content_type, IfNoneMatch="*")
                    return True
                except self.client.exceptions.ClientError as e:
                    code = self._code(e)
                    if code in ("PreconditionFailed", "412"):
                        return False
                    if code == "ConditionalRequestConflict" and attempt == 0:
                        # a concurrent write to the same key: S3 asks for a retry
                        if self.exists(key):
                            return False
                        continue
                    if code in ("NotImplemented", "InvalidArgument", "InvalidRequest"):
                        self._conditional_ok = False   # backend lacks conditional writes
                        break
                    raise
        return Storage.put_if_absent(self, key, data, content_type)

    def list(self, prefix: str, start_after: Optional[str] = None) -> list:
        out = []
        paginator = self.client.get_paginator("list_objects_v2")
        full = f"{self.prefix}/{prefix}" if self.prefix else prefix
        kwargs = {"Bucket": self.bucket, "Prefix": full}
        if start_after:
            kwargs["StartAfter"] = f"{self.prefix}/{start_after}" if self.prefix else start_after
        for page in paginator.paginate(**kwargs):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                if self.prefix:
                    k = k[len(self.prefix) + 1:]
                out.append(k)
        return sorted(out)

    def kind(self) -> str:
        return "s3"

    def describe(self) -> str:
        ep = self.client.meta.endpoint_url
        return f"s3:{self.bucket}/{self.prefix}" + (f" @ {ep}" if ep else "")


def from_config(cfg: dict) -> Storage:
    st = cfg.get("storage", {})
    backend = st.get("backend", "local")
    if backend == "local":
        return LocalStorage(st.get("root", "./data"))
    if backend == "s3":
        return S3Storage(st.get("bucket", ""), st.get("prefix", ""), st.get("endpoint", ""), st.get("region", ""))
    raise ValueError(f"unknown storage backend {backend!r} (expected local or s3)")
