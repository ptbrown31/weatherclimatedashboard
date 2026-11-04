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

`get_tagged`, `put_new` and `put_matching` are the conditional writes a lease
needs (`Lease`, below), a write that lands only when nothing is stored under
the key (S3's `If-None-Match: *`), and one that lands only while the object
is still the version the writer read (S3's `If-Match` on its ETag). Of two
writers racing, exactly one lands. Amazon S3 enforces both on the server, and
they reach it on any botocore; the local backend gets the same from an
exclusive create and an atomic rename. A store that ignores the headers gives
no such guarantee.

Archive objects ending in .gz are stored as opaque gzip files (Content-Type
application/gzip, no Content-Encoding), because the archive is read by the
pipeline, not by browsers. The browser's surface is snapshots/, which is
plain JSON. Do not fetch archive objects from a page.
"""
from __future__ import annotations
import contextlib
import datetime as dt
import hashlib
import json
import os
import tempfile
import time
import uuid
from typing import Optional, Tuple


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

    def get_tagged(self, key: str) -> Tuple[Optional[bytes], Optional[str]]:
        """The object and its entity tag (S3's ETag), or (None, None) when
        nothing is stored under key."""
        raise NotImplementedError

    def put_new(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> Optional[str]:
        """Write only when nothing is stored under key, atomically, so of two
        writers one lands. Returns the new tag, or None when something is there."""
        raise NotImplementedError

    def put_matching(self, key: str, data: bytes, tag: str,
                     content_type: str = "application/octet-stream") -> Optional[str]:
        """Replace the object only while its tag is still `tag`, atomically.
        Returns the new tag, or None when the object changed or is gone."""
        raise NotImplementedError

    def kind(self) -> str:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError


# the local backend serializes its conditional writes on a guard file, held for
# the few milliseconds of a read, a compare and a rename; one left behind by a
# process that died inside them is broken this long after it was made, and a
# writer gives up on a live one after the wait
GUARD_STALE_SECONDS = 30
GUARD_WAIT_SECONDS = 2.0


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

    def _spool(self, path: str, data: bytes) -> str:
        """The data in a sibling temp file, flushed to disk, ready to be renamed
        or linked into place."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            os.unlink(tmp)
            raise
        return tmp

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream",
            cache_control: Optional[str] = None) -> None:
        path = self._path(key)
        # Write to a sibling temp file, flush it to disk, then rename, so a
        # reader never sees a half-written object and a crash after the
        # rename cannot leave an empty one.
        tmp = self._spool(path, data)
        try:
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @staticmethod
    def _tag(data: bytes) -> str:
        # a digest of the content stands in for S3's ETag; a lease write is
        # unique by its holder and its times, so no two versions share one
        return '"' + hashlib.sha256(data).hexdigest() + '"'

    def get_tagged(self, key: str) -> Tuple[Optional[bytes], Optional[str]]:
        data = self.get(key)
        return (None, None) if data is None else (data, self._tag(data))

    @contextlib.contextmanager
    def _guard(self, path: str):
        """Hold the key's guard while a conditional write reads, compares and
        renames, so no other conditional write on the key interleaves. The
        guard is an exclusive create; it is named like a temp file, so list()
        never reports it. Yields False when another writer kept it past
        GUARD_WAIT_SECONDS, which the caller treats as a lost race."""
        guard = os.path.join(os.path.dirname(path), ".tmp-guard-" + os.path.basename(path))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        waited = 0.0
        while True:
            try:
                os.close(os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
                break
            except FileExistsError:
                try:
                    stale = time.time() - os.path.getmtime(guard) > GUARD_STALE_SECONDS
                except FileNotFoundError:
                    continue                      # let go between the two calls
                if stale:
                    # a rename takes a dead writer's guard away for exactly one breaker
                    try:
                        moved = f"{guard}.{os.getpid()}.{uuid.uuid4().hex}"
                        os.rename(guard, moved)
                        os.remove(moved)
                    except FileNotFoundError:
                        pass
                    continue
                if waited >= GUARD_WAIT_SECONDS:
                    yield False
                    return
                time.sleep(0.01)
                waited += 0.01
        try:
            yield True
        finally:
            try:
                os.remove(guard)
            except FileNotFoundError:
                pass

    def put_new(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> Optional[str]:
        path = self._path(key)
        with self._guard(path) as ok:
            if not ok or os.path.exists(path):
                return None
            tmp = self._spool(path, data)
            try:
                # a hard link to the whole written file is an exclusive create,
                # which fails when the name exists and shows no reader a partial object
                os.link(tmp, path)
            except FileExistsError:
                return None
            finally:
                os.unlink(tmp)
        return self._tag(data)

    def put_matching(self, key: str, data: bytes, tag: str,
                     content_type: str = "application/octet-stream") -> Optional[str]:
        path = self._path(key)
        with self._guard(path) as ok:
            if not ok:
                return None
            cur = self.get(key)
            if cur is None or self._tag(cur) != tag:
                return None
            tmp = self._spool(path, data)
            try:
                os.replace(tmp, path)             # the atomic rename
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return self._tag(data)

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
    def __init__(self, bucket: str, prefix: str = "", endpoint: str = "", region: str = "", client=None):
        if not bucket:
            raise ValueError("s3 backend needs a bucket name (WX_STORAGE_BUCKET)")
        if client is None:
            import boto3  # only imported when this backend is configured
            from botocore.config import Config
            kwargs = {}
            if endpoint:
                kwargs["endpoint_url"] = endpoint
            if region:
                kwargs["region_name"] = region
            client = boto3.client("s3", config=Config(retries={"max_attempts": 4, "mode": "standard"}), **kwargs)
        # a client may be handed in (the tests' stand-in for S3); it is used as boto3's would be
        self.client = client
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

    def get_tagged(self, key: str) -> Tuple[Optional[bytes], Optional[str]]:
        try:
            r = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
            return r["Body"].read(), r.get("ETag")
        except self.client.exceptions.ClientError as e:
            if self._code(e) in ("404", "NoSuchKey", "NotFound"):
                return None, None
            raise

    def _put_conditional(self, key: str, data: bytes, content_type: str, headers: dict) -> Optional[str]:
        """PutObject with conditional headers, which S3 enforces on the server.
        They are added to the request through the client's event hook rather
        than passed as parameters, because the Lambda runtime's botocore is not
        pinned and one that predates the IfNoneMatch and IfMatch parameters
        would refuse them as unknown, while the hook and the headers work on any
        botocore. Returns the new ETag, or None when the condition failed."""
        def add(params, **_):
            params["headers"].update(headers)
        event = "before-call.s3.PutObject"
        self.client.meta.events.register(event, add)
        try:
            r = self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data, ContentType=content_type)
        except self.client.exceptions.ClientError as e:
            # a 412 is a condition that did not hold, a 409 is S3 seeing a
            # conditional write race another to the key, which counts as a lost
            # race, and a 404 is If-Match on a key that is gone
            if self._code(e) in ("PreconditionFailed", "412", "ConditionalRequestConflict", "409", "NoSuchKey", "404"):
                return None
            raise
        finally:
            self.client.meta.events.unregister(event, add)
        return r.get("ETag")

    def put_new(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> Optional[str]:
        return self._put_conditional(key, data, content_type, {"If-None-Match": "*"})

    def put_matching(self, key: str, data: bytes, tag: str,
                     content_type: str = "application/octet-stream") -> Optional[str]:
        return self._put_conditional(key, data, content_type, {"If-Match": tag})

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


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Lease:
    """One pass at a time for a lane, on the store's conditional writes.

    The lease is a small JSON object under one key: who holds it (a token made
    for each pass), when it was taken, until when, and when it was released.
    A pass creates it when nothing is stored (put_new), takes over one that has
    expired or been released while it is still the version the pass read
    (put_matching), and releases its own by the same rule, so of two passes
    racing for it exactly one holds it. The holder never renews it, so
    `expires` has to lie past the longest the holder can run."""

    def __init__(self, store: Storage, key: str):
        self.store, self.key = store, key
        self.token = uuid.uuid4().hex
        self.tag: Optional[str] = None      # the version this pass wrote, while it holds the lease
        self.held: Optional[dict] = None    # the lease another pass holds, when acquire found one
        self._took: dict = {}

    @staticmethod
    def _doc(data: Optional[bytes]) -> Optional[dict]:
        if not data:
            return None
        try:
            doc = json.loads(data)
        except ValueError:
            return None
        return doc if isinstance(doc, dict) else None

    def _body(self, **fields) -> bytes:
        return json.dumps(dict({"holder": self.token}, **fields), separators=(",", ":")).encode()

    def _mine(self, data: Optional[bytes]) -> bool:
        doc = self._doc(data)
        return bool(doc) and doc.get("holder") == self.token

    @staticmethod
    def live(doc: Optional[dict], now: dt.datetime) -> bool:
        """Whether a lease document still holds the lane at `now`, being
        neither released nor expired. An unreadable one holds nothing."""
        if not doc or doc.get("released"):
            return False
        try:
            return dt.datetime.fromisoformat(str(doc.get("expires")).replace("Z", "+00:00")) > now
        except ValueError:
            return False

    def acquire(self, now: dt.datetime, seconds: float, mode: str = "") -> bool:
        """Take the lease until `seconds` after `now`. False when another pass
        holds it, whose document is then in `held`."""
        self._took = {"mode": mode, "acquired": _iso(now)}
        data = self._body(expires=_iso(now + dt.timedelta(seconds=seconds)), released=None, **self._took)
        for _ in range(2):
            tag = self.store.put_new(self.key, data, "application/json")
            if tag is None:
                cur, cur_tag = self.store.get_tagged(self.key)
                if cur is None:
                    continue                      # removed between the two calls, so create it again
                if self._mine(cur):
                    tag = cur_tag                 # a create retried after it had landed
                elif self.live(self._doc(cur), now):
                    self.held = self._doc(cur)
                    return False
                else:
                    # an expired, released or unreadable lease is taken over only
                    # while it is still the version this pass read
                    tag = self.store.put_matching(self.key, data, cur_tag, "application/json")
                    if tag is None:
                        cur, cur_tag = self.store.get_tagged(self.key)
                        if not self._mine(cur):
                            self.held = self._doc(cur) or {}
                            return False
                        tag = cur_tag
            self.tag = tag
            return True
        self.held = {}
        return False

    def release(self, now: dt.datetime) -> bool:
        """Mark the lease released, while it is still the version this pass
        wrote. False when it is not, because it expired and another pass
        took it over, which the caller reports."""
        if self.tag is None:
            return False
        data = self._body(expires=_iso(now), released=_iso(now), **self._took)
        if self.store.put_matching(self.key, data, self.tag, "application/json") is None:
            cur, _ = self.store.get_tagged(self.key)
            doc = self._doc(cur)
            if not (doc and doc.get("holder") == self.token and doc.get("released")):
                return False
        self.tag = None
        return True


def from_config(cfg: dict) -> Storage:
    st = cfg.get("storage", {})
    backend = st.get("backend", "local")
    if backend == "local":
        return LocalStorage(st.get("root", "./data"))
    if backend == "s3":
        return S3Storage(st.get("bucket", ""), st.get("prefix", ""), st.get("endpoint", ""), st.get("region", ""))
    raise ValueError(f"unknown storage backend {backend!r} (expected local or s3)")
