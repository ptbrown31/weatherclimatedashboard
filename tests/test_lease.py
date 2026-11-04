"""The conditional writes in pipeline/storage.py and the lease built on them,
which keeps two passes of the analysis lane from ever overlapping.

Each case runs on the local backend (an exclusive create and an atomic rename
under a guard file) and on S3Storage over a stand-in for boto3's client that
answers the conditional headers the way S3 does: 412 PreconditionFailed when
If-None-Match: * meets an object or If-Match an older ETag, 404 NoSuchKey when
If-Match meets a key that is gone. The stand-in runs the client's
before-call hooks as botocore does, so the headers it judges are the ones the
backend added. Where botocore is installed (not the system Python 3.9), the
same cases run once more over a real boto3 client whose requests never leave
the process: a before-send hook answers them, so the headers checked are the
ones botocore would have put on the wire. No network."""
import datetime as dt
import io
import json
import os
import sys
import tempfile
import time
import types
import unittest
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import storage   # noqa: E402

KEY = "archive/analysis2/_meta/lease.json"
T0 = dt.datetime(2026, 11, 3, 14, 16, tzinfo=dt.timezone.utc)

try:
    import boto3                                   # noqa: F401
    from botocore.awsrequest import AWSResponse
    HAVE_BOTO = True
except ImportError:                                # the system Python 3.9 has no boto3
    HAVE_BOTO = False


class ClientError(Exception):
    """botocore's ClientError as far as S3Storage reads it."""
    def __init__(self, code, status):
        super().__init__(f"{status} {code}")
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class FakeS3:
    """put_object and get_object on a dict, the client's event hooks, and S3's
    answers to the conditional headers. `sent` keeps the headers each put
    carried after the hooks ran."""
    def __init__(self):
        self.objects, self.handlers, self.sent, self.n = {}, {}, [], 0
        self.fail_with = None                      # an error code the next put answers with
        self.meta = types.SimpleNamespace(events=types.SimpleNamespace(register=self._register,
                                                                       unregister=self._unregister))
        self.exceptions = types.SimpleNamespace(ClientError=ClientError)

    def _register(self, event, handler, *a, **kw):
        self.handlers.setdefault(event, []).append(handler)

    def _unregister(self, event, handler=None, *a, **kw):
        if handler in self.handlers.get(event, []):
            self.handlers[event].remove(handler)

    def put_object(self, Bucket, Key, Body, **kw):
        # the request dict botocore hands its before-call hooks
        params = {"headers": {}, "body": Body, "url_path": "/" + Key, "method": "PUT"}
        for handler in list(self.handlers.get("before-call.s3.PutObject", [])):
            handler(params=params, model=None, context={})
        h = params["headers"]
        self.sent.append(dict(h))
        if self.fail_with:
            code, self.fail_with = self.fail_with, None
            raise ClientError(code, {"ConditionalRequestConflict": 409, "SlowDown": 503}.get(code, 400))
        cur = self.objects.get(Key)
        if h.get("If-None-Match") == "*" and cur is not None:
            raise ClientError("PreconditionFailed", 412)
        if "If-Match" in h:
            if cur is None:
                raise ClientError("NoSuchKey", 404)
            if cur[1] != h["If-Match"]:
                raise ClientError("PreconditionFailed", 412)
        self.n += 1
        etag = '"%032x"' % self.n
        self.objects[Key] = (Body, etag)
        return {"ETag": etag}

    def get_object(self, Bucket, Key):
        cur = self.objects.get(Key)
        if cur is None:
            raise ClientError("NoSuchKey", 404)
        return {"Body": io.BytesIO(cur[0]), "ETag": cur[1]}


def wire(etag_of_put):
    """The before-send hook of a real boto3 client, giving S3's conditional
    answers from a dict for requests that never leave the process. Returns the
    hook and the list of the conditional headers each request carried."""
    objects, seen = {}, []

    class Raw:
        def __init__(self, body):
            self.body = body

        def stream(self, **kw):
            yield self.body

        def read(self, *a, **kw):
            b, self.body = self.body, b""
            return b

    def error(url, status, code):
        body = f'<?xml version="1.0" encoding="UTF-8"?><Error><Code>{code}</Code><Message>m</Message></Error>'.encode()
        return AWSResponse(url, status, {"content-type": "application/xml"}, Raw(body))

    def dechunk(raw):
        out, i = b"", 0
        while True:
            j = raw.index(b"\r\n", i)
            n = int(raw[i:j].split(b";")[0], 16)
            i = j + 2
            if n == 0:
                return out
            out, i = out + raw[i:i + n], i + n + 2

    def send(request, **kw):
        h = {k.lower(): (v.decode() if isinstance(v, bytes) else v) for k, v in request.headers.items()}
        seen.append({k: v for k, v in h.items() if k.startswith("if-")})
        key = urlsplit(request.url).path.lstrip("/")
        key = key[len("wx-test/"):] if key.startswith("wx-test/") else key
        cur = objects.get(key)
        if request.method == "GET":
            if cur is None:
                return error(request.url, 404, "NoSuchKey")
            return AWSResponse(request.url, 200, {"ETag": cur[1], "Content-Length": str(len(cur[0]))}, Raw(cur[0]))
        if h.get("if-none-match") == "*" and cur is not None:
            return error(request.url, 412, "PreconditionFailed")
        if "if-match" in h and (cur is None or cur[1] != h["if-match"]):
            return error(request.url, 404 if cur is None else 412, "NoSuchKey" if cur is None else "PreconditionFailed")
        b = request.body
        data = b.read() if hasattr(b, "read") else (b or b"")
        if "aws-chunked" in h.get("content-encoding", ""):
            data = dechunk(data)
        objects[key] = (data, etag_of_put(data))
        return AWSResponse(request.url, 200, {"ETag": objects[key][1]}, Raw(b""))

    return send, seen


class StaleOnce:
    """A store whose first read of the lease answers with an older version, as
    a pass sees it when another replaced the lease between its read and its
    write; every other call goes to the store itself."""
    def __init__(self, store, stale):
        self.store, self.stale = store, stale

    def get_tagged(self, key):
        stale, self.stale = self.stale, None
        return stale if stale is not None else self.store.get_tagged(key)

    def put_new(self, *a, **kw):
        return self.store.put_new(*a, **kw)

    def put_matching(self, *a, **kw):
        return self.store.put_matching(*a, **kw)


class LeaseCases:
    """The cases, on whichever backend make_store gives."""
    def make_store(self):
        raise NotImplementedError

    def setUp(self):
        self.st = self.make_store()

    def doc(self):
        data, _ = self.st.get_tagged(KEY)
        return json.loads(data)

    def test_create_takes_an_empty_store(self):
        a = storage.Lease(self.st, KEY)
        self.assertTrue(a.acquire(T0, 300, "window"))
        d = self.doc()
        self.assertEqual((d["holder"], d["mode"], d["acquired"], d["expires"], d["released"]),
                         (a.token, "window", "2026-11-03T14:16:00Z", "2026-11-03T14:21:00Z", None))
        self.assertEqual(self.st.get_tagged(KEY)[1], a.tag)

    def test_a_live_lease_is_held(self):
        a, b = storage.Lease(self.st, KEY), storage.Lease(self.st, KEY)
        self.assertTrue(a.acquire(T0, 300, "baseline"))
        before = self.st.get_tagged(KEY)
        self.assertFalse(b.acquire(T0 + dt.timedelta(minutes=2), 300, "window"))
        self.assertEqual((b.held["holder"], b.held["mode"]), (a.token, "baseline"))
        self.assertIsNone(b.tag)
        self.assertEqual(self.st.get_tagged(KEY), before)          # the holder's lease untouched
        self.assertFalse(b.release(T0))                            # nothing of its own to release
        self.assertTrue(a.release(T0 + dt.timedelta(minutes=3)))   # the holder still lets go

    def test_an_expired_lease_is_taken_over_and_its_old_holder_cannot_release_it(self):
        a, b = storage.Lease(self.st, KEY), storage.Lease(self.st, KEY)
        self.assertTrue(a.acquire(T0, 300, "baseline"))
        self.assertFalse(b.acquire(T0 + dt.timedelta(seconds=299), 300, "window"))
        self.assertTrue(b.acquire(T0 + dt.timedelta(seconds=300), 300, "window"))   # free from its expiry
        self.assertEqual(self.doc()["holder"], b.token)
        self.assertFalse(a.release(T0 + dt.timedelta(seconds=302)))
        self.assertEqual(self.doc()["holder"], b.token)            # the new holder's lease untouched

    def test_release_lets_the_next_pass_take_it_at_once(self):
        a, b = storage.Lease(self.st, KEY), storage.Lease(self.st, KEY)
        self.assertTrue(a.acquire(T0, 900, "baseline"))
        self.assertTrue(a.release(T0 + dt.timedelta(seconds=40)))
        d = self.doc()
        self.assertEqual((d["holder"], d["released"], d["acquired"]), (a.token, "2026-11-03T14:16:40Z", "2026-11-03T14:16:00Z"))
        self.assertIsNone(a.tag)
        self.assertFalse(a.release(T0 + dt.timedelta(seconds=41)))  # released once
        self.assertTrue(b.acquire(T0 + dt.timedelta(seconds=60), 900, "window"))
        self.assertEqual(self.doc()["holder"], b.token)

    def test_of_two_passes_taking_over_the_same_expired_lease_one_wins(self):
        a = storage.Lease(self.st, KEY)
        self.assertTrue(a.acquire(T0, 60, "baseline"))
        stale = self.st.get_tagged(KEY)
        later = T0 + dt.timedelta(minutes=5)
        b = storage.Lease(self.st, KEY)
        self.assertTrue(b.acquire(later, 300, "window"))
        # c read the expired version just before b replaced it, so its
        # takeover carries a tag that no longer matches and must fail
        c = storage.Lease(StaleOnce(self.st, stale), KEY)
        self.assertFalse(c.acquire(later, 300, "window"))
        self.assertEqual(self.doc()["holder"], b.token)
        self.assertEqual(c.held["holder"], b.token)
        self.assertIsNone(self.st.put_matching(KEY, b"{}", stale[1]))     # the old tag no longer matches

    def test_a_create_that_landed_though_its_answer_was_lost_is_kept(self):
        # a retried PutObject whose first try had landed answers 412; the pass
        # finds its own token and holds the lease rather than waiting it out
        a = storage.Lease(self.st, KEY)
        real = self.st.put_new

        def landed_then_refused(key, data, content_type="application/octet-stream"):
            real(key, data, content_type)
            return None
        self.st.put_new = landed_then_refused
        self.assertTrue(a.acquire(T0, 300, "window"))
        del self.st.put_new
        self.assertEqual(a.tag, self.st.get_tagged(KEY)[1])
        self.assertTrue(a.release(T0 + dt.timedelta(seconds=5)))

    def test_an_unreadable_lease_holds_nothing(self):
        self.assertIsNotNone(self.st.put_new(KEY, b"not json", "application/json"))
        a = storage.Lease(self.st, KEY)
        self.assertTrue(a.acquire(T0, 300, "window"))
        self.assertEqual(self.doc()["holder"], a.token)

    def test_the_conditional_writes_themselves(self):
        tag = self.st.put_new(KEY, b"one", "application/json")
        self.assertIsNotNone(tag)
        self.assertIsNone(self.st.put_new(KEY, b"two", "application/json"))
        self.assertEqual(self.st.get_tagged(KEY), (b"one", tag))
        tag2 = self.st.put_matching(KEY, b"three", tag, "application/json")
        self.assertIsNotNone(tag2)
        self.assertNotEqual(tag2, tag)
        self.assertIsNone(self.st.put_matching(KEY, b"four", tag, "application/json"))
        self.assertEqual(self.st.get_tagged(KEY), (b"three", tag2))
        self.assertIsNone(self.st.put_matching("archive/nothing/here.json", b"x", tag2, "application/json"))
        self.assertEqual(self.st.get_tagged("archive/nothing/here.json"), (None, None))


class LocalLease(LeaseCases, unittest.TestCase):
    def make_store(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        return storage.LocalStorage(self.tmp.name)

    def guard(self):
        return os.path.join(self.tmp.name, "archive", "analysis2", "_meta", ".tmp-guard-lease.json")

    def test_the_guard_is_never_listed_and_is_gone_after_each_write(self):
        self.st.put_new(KEY, b"one", "application/json")
        self.assertEqual(self.st.list("archive/analysis2/_meta/"), [KEY])
        self.assertFalse(os.path.exists(self.guard()))

    def test_a_live_guard_makes_a_conditional_write_give_up(self):
        tag = self.st.put_new(KEY, b"one", "application/json")
        open(self.guard(), "w").close()            # another writer inside its few milliseconds
        keep = storage.GUARD_WAIT_SECONDS
        storage.GUARD_WAIT_SECONDS = 0.05
        self.addCleanup(lambda: setattr(storage, "GUARD_WAIT_SECONDS", keep))
        self.assertIsNone(self.st.put_matching(KEY, b"two", tag, "application/json"))
        self.assertIsNone(self.st.put_new(KEY, b"two", "application/json"))
        self.assertEqual(self.st.get_tagged(KEY)[0], b"one")
        # the guard is the key's own, so another key is not held up by it
        self.assertIsNotNone(self.st.put_new("archive/analysis2/_meta/other.json", b"x", "application/json"))

    def test_a_dead_writers_guard_is_broken(self):
        tag = self.st.put_new(KEY, b"one", "application/json")
        open(self.guard(), "w").close()
        old = time.time() - storage.GUARD_STALE_SECONDS - 5
        os.utime(self.guard(), (old, old))
        self.assertIsNotNone(self.st.put_matching(KEY, b"two", tag, "application/json"))
        self.assertEqual(self.st.get_tagged(KEY)[0], b"two")
        self.assertFalse(os.path.exists(self.guard()))
        self.assertEqual([f for f in os.listdir(os.path.dirname(self.guard())) if f.startswith(".tmp-")], [])


class FakeS3Lease(LeaseCases, unittest.TestCase):
    def make_store(self):
        self.s3 = FakeS3()
        return storage.S3Storage("wx-test", prefix="data", client=self.s3)

    def test_the_headers_go_on_the_request_through_the_hook_and_the_hook_goes_away(self):
        a, b = storage.Lease(self.st, KEY), storage.Lease(self.st, KEY)
        self.assertTrue(a.acquire(T0, 60, "baseline"))
        first = self.s3.objects["data/" + KEY][1]
        self.assertTrue(b.acquire(T0 + dt.timedelta(minutes=2), 60, "window"))
        mine = b.tag
        self.assertTrue(b.release(T0 + dt.timedelta(minutes=3)))
        self.assertEqual(self.s3.sent[0], {"If-None-Match": "*"})                 # a's create
        self.assertEqual(self.s3.sent[1], {"If-None-Match": "*"})                 # b's create, refused
        self.assertEqual(self.s3.sent[2], {"If-Match": first})                    # b's takeover of a's version
        self.assertEqual(self.s3.sent[3], {"If-Match": mine})                     # b's release, on its own version
        self.assertNotEqual(mine, first)
        self.assertEqual(len(self.s3.sent), 4)
        self.assertEqual(self.s3.handlers.get("before-call.s3.PutObject"), [])     # no hook outlives its call
        # a plain write carries no condition
        self.st.put("archive/x.json", b"{}", "application/json")
        self.assertEqual(self.s3.sent[-1], {})

    def test_a_conflict_is_a_lost_race_and_any_other_error_raises(self):
        self.s3.fail_with = "ConditionalRequestConflict"
        self.assertIsNone(self.st.put_new(KEY, b"one", "application/json"))
        self.s3.fail_with = "SlowDown"
        with self.assertRaises(ClientError):
            self.st.put_new(KEY, b"one", "application/json")
        self.assertEqual(self.s3.handlers.get("before-call.s3.PutObject"), [])


@unittest.skipUnless(HAVE_BOTO, "botocore is not installed")
class Boto3Lease(LeaseCases, unittest.TestCase):
    def make_store(self):
        import hashlib
        import botocore
        from botocore.config import Config
        # unsigned, so no credential is needed or read; the hooks run as on any client
        client = boto3.client("s3", region_name="us-east-1", config=Config(signature_version=botocore.UNSIGNED))
        send, self.seen = wire(lambda data: '"' + hashlib.md5(data).hexdigest() + '"')
        client.meta.events.register("before-send.s3", send)
        return storage.S3Storage("wx-test", prefix="data", client=client)

    def test_botocore_puts_the_conditions_on_the_wire(self):
        a, b = storage.Lease(self.st, KEY), storage.Lease(self.st, KEY)
        self.assertTrue(a.acquire(T0, 60, "baseline"))
        self.assertFalse(b.acquire(T0 + dt.timedelta(seconds=30), 60, "window"))
        self.assertTrue(b.acquire(T0 + dt.timedelta(minutes=2), 60, "window"))
        puts = [s for s in self.seen if s]
        self.assertEqual(puts[0], {"if-none-match": "*"})
        self.assertTrue(all(set(s) <= {"if-none-match", "if-match"} for s in puts))
        self.assertIn("if-match", puts[-1])


if __name__ == "__main__":
    unittest.main()
