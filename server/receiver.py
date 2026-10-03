#!/usr/bin/env python3
"""Anonymous review receiver. Only validated annotations can reach the fixed GitHub directory."""
import argparse
import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

REPO = "jcheniu/cartoon-review"
BRANCH = "main"
ROOT = Path(__file__).resolve().parents[1]
ROUND = "round-20260930T231132-d69c0f"
DESTINATION = "result/round_1"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
MAX_BODY = 256 * 1024

class ReviewError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()

def annotation_digest(content):
    # Revision/timestamp changes alone are not a new human annotation.
    rows = [json.loads(line) for line in content.splitlines() if line.strip()]
    return sha(canonical([{k: v for k, v in row.items()
                           if k not in ("review_version", "reviewed_at")} for row in rows]))

def complete(content, start, end):
    rows = [json.loads(line) for line in content.splitlines() if line.strip()]
    return [row.get("image_number") for row in rows] == list(range(start, end + 1))

def bounds(value):
    if not isinstance(value, dict):
        raise ReviewError("Invalid group")
    start, end = value.get("start"), value.get("end")
    if type(start) is not int or type(end) is not int or not 0 <= start <= 475 or end != start + 24:
        raise ReviewError("A group must contain 25 consecutive indices in 0–499")
    return start, end

def validate_rows(manifest, records, start, end):
    if not isinstance(records, list) or not 1 <= len(records) <= 25:
        raise ReviewError("Upload 1–25 saved records")
    cleaned, seen = [], set()
    by_id = {item["id"]: item for item in manifest["items"]}
    for row in records:
        if not isinstance(row, dict):
            raise ReviewError("Invalid review")
        item = by_id.get(row.get("photo_id"))
        if not item or not item.get("candidates"):
            raise ReviewError("Candidate is not available")
        if not start <= item["image_number"] <= end or item["id"] in seen:
            raise ReviewError("Duplicate or out-of-range review")
        seen.add(item["id"])
        if (row.get("round_id") != ROUND or row.get("dataset_sha256") != manifest["dataset_sha256"]
                or row.get("source_sha256") != item["sha256"]
                or row.get("candidate_hashes") != {v: item["candidates"][v]["sha256"] for v in ("a", "b")}):
            raise ReviewError("Dataset, round or image hashes do not match")
        accepted, preferred = row.get("accepted"), row.get("preferred")
        if accepted not in ("a", "b", "both", "neither"):
            raise ReviewError("Invalid acceptance")
        if (accepted == "both" and preferred not in ("a", "b", "tie")) or (accepted != "both" and preferred != accepted):
            raise ReviewError("Invalid preference")
        reasons = row.get("rejection_reasons", [])
        if not isinstance(reasons, list) or any(r not in ("color", "pose") for r in reasons) or len(set(reasons)) != len(reasons):
            raise ReviewError("Invalid rejection reasons")
        if (accepted == "neither" and not reasons) or (accepted != "neither" and reasons):
            raise ReviewError("Rejected candidates need color and/or pose reasons")
        caption = row.get("caption", "")
        if not isinstance(caption, str) or len(caption) > 2000 or (accepted != "neither" and len(caption.strip()) < 5):
            raise ReviewError("Invalid target description")
        version, reviewed = row.get("review_version"), row.get("reviewed_at")
        if type(version) is not int or not 1 <= version <= 100000 or not isinstance(reviewed, str) or len(reviewed) > 50:
            raise ReviewError("Invalid review version or timestamp")
        try:
            if datetime.fromisoformat(reviewed.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError()
        except ValueError:
            raise ReviewError("Timestamp must include a timezone")
        session = row.get("annotation_session_id")
        if not isinstance(session, str) or not UUID.fullmatch(session):
            raise ReviewError("Invalid annotation session")
        # Copy only public schema fields. Never accept client paths or arbitrary GitHub filenames.
        cleaned.append(dict(
            schema_version=1, dataset_sha256=manifest["dataset_sha256"],
            numbered_manifest_sha256=manifest["numbered_manifest_sha256"],
            round_id=ROUND, annotation_session_id=session,
            item_id=item["legacy_id"], photo_id=item["id"], image_number=item["image_number"],
            category=item["category"], split=item["split"], group_id=item["group_id"],
            accepted=accepted, preferred=preferred, rejection_reasons=reasons,
            caption=caption.strip(), review_version=version, reviewed_at=reviewed,
            source_sha256=item["sha256"],
            source=dict(path=item["path"], sha256=item["sha256"], license=item["license"], source_url=item["source_url"]),
            candidates=item["candidates"], candidate_hashes={v: item["candidates"][v]["sha256"] for v in ("a", "b")},
            master_hashes=item.get("master_hashes", {}),
        ))
    return sorted(cleaned, key=lambda row: row["image_number"])

def github_json(method, endpoint, body=None):
    args = ["gh", "api", "--method", method, endpoint]
    if body is not None:
        args += ["--input", "-"]
    result = subprocess.run(args, input=canonical(body) if body is not None else None, text=True,
                            capture_output=True, timeout=60)
    if result.returncode:
        if "HTTP 404" in result.stderr:
            return None
        raise RuntimeError("GitHub API temporarily unavailable")
    return json.loads(result.stdout) if result.stdout.strip() else {}

def github_write(path, content):
    match = re.fullmatch(re.escape(DESTINATION) + r"/(" + UUID.pattern[1:-1] + r")/(\d{3})_(\d{3})\.jsonl", path)
    if not match:
        raise ValueError("Only review JSONL files may be committed")
    start, end = map(int, match.groups()[1:])
    bounds(dict(start=start, end=end))
    if not complete(content, start, end):
        raise ValueError("Only complete 25-row groups may be committed")
    for attempt in range(3):
        old = github_json("GET", f"repos/{REPO}/contents/{path}?ref={BRANCH}")
        encoded = base64.b64encode(content.encode()).decode()
        if old:
            previous = base64.b64decode(old.get("content", "")).decode()
            if previous == content or annotation_digest(previous) == annotation_digest(content):
                return old["html_url"]
        body = dict(message="Save completed round 1 review group " + path.rsplit("/", 1)[-1], content=encoded, branch=BRANCH,
                    author=dict(name="Jing", email="jcheniu@connect.ust.hk"))
        if old:
            body["sha"] = old["sha"]
        try:
            value = github_json("PUT", f"repos/{REPO}/contents/{path}", body)
            if not value:
                raise RuntimeError("GitHub write unavailable")
            return value["content"]["html_url"]
        except RuntimeError:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))

class Receiver:
    def __init__(self, data_dir, manifest_path=ROOT / "data/manifest.json", writer=github_write,
                 clock=time.time, quiet_seconds=30, retry_seconds=60):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data_dir, 0o700)
        self.db_path = self.data_dir / "uploads.sqlite3"
        self.manifest_path, self.writer = Path(manifest_path), writer
        self.clock, self.quiet_seconds, self.retry_seconds = clock, quiet_seconds, retry_seconds
        self.wake = threading.Event()
        self.lock = threading.RLock()
        self.rate = {}
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, key_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS uploads(
                    session TEXT, start INTEGER, end INTEGER, content TEXT, digest TEXT,
                    state TEXT, url TEXT, error TEXT, updated REAL,
                    published_digest TEXT NOT NULL DEFAULT '', retry_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(session,start,end));
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(uploads)")}
            migrate = "published_digest" not in columns
            if migrate:
                db.execute("ALTER TABLE uploads ADD COLUMN published_digest TEXT NOT NULL DEFAULT ''")
            if "retry_at" not in columns:
                db.execute("ALTER TABLE uploads ADD COLUMN retry_at REAL NOT NULL DEFAULT 0")
            for row in db.execute("SELECT * FROM uploads").fetchall():
                digest = annotation_digest(row["content"])
                state = row["state"] if complete(row["content"], row["start"], row["end"]) else "draft"
                published = digest if migrate and row["state"] == "uploaded" else row["published_digest"]
                db.execute("UPDATE uploads SET digest=?,state=?,published_digest=? WHERE session=? AND start=? AND end=?",
                           (digest, state, published, row["session"], row["start"], row["end"]))
                self.save_snapshot(row["session"], row["start"], row["end"], row["content"])
        os.chmod(self.db_path, 0o600)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def snapshot_path(self, session, start, end):
        return self.data_dir / "jsonl" / session / f"{start:03d}_{end:03d}.jsonl"

    def save_snapshot(self, session, start, end, content):
        path = self.snapshot_path(session, start, end)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text() == content:
            return
        temporary = path.with_suffix(".jsonl.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def authenticate(self, db, session, key, create=False):
        if not isinstance(session, str) or not UUID.fullmatch(session) or not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ReviewError("Invalid upload session", 403)
        record = db.execute("SELECT key_hash FROM sessions WHERE id=?", (session,)).fetchone()
        digest = sha(key)
        if record:
            if not hmac.compare_digest(record["key_hash"], digest):
                raise ReviewError("Upload session key does not match", 403)
        elif create:
            if db.execute("SELECT count(*) FROM sessions").fetchone()[0] >= 1000:
                raise ReviewError("Receiver capacity reached", 429)
            db.execute("INSERT INTO sessions VALUES(?,?)", (session, digest))
        else:
            raise ReviewError("Unknown upload session", 404)

    def submit(self, body, key, ip="local"):
        if not isinstance(body, dict):
            raise ReviewError("Invalid request")
        session = body.get("session_id")
        start, end = bounds(body.get("range"))
        manifest = json.loads(self.manifest_path.read_text())
        records = validate_rows(manifest, body.get("records"), start, end)
        with self.lock, self.db() as db:
            now = self.clock()
            recent = [stamp for stamp in self.rate.get(ip, []) if now - stamp < 60]
            if len(recent) >= 120:
                raise ReviewError("Please retry shortly", 429)
            self.rate[ip] = recent + [now]
            if len(self.rate) > 1000:
                self.rate = {address: stamps for address, stamps in self.rate.items() if now - stamps[-1] < 60}
            self.authenticate(db, session, key, create=True)
            previous = db.execute("SELECT * FROM uploads WHERE session=? AND start=? AND end=?", (session, start, end)).fetchone()
            merged = {row["photo_id"]: row for row in map(json.loads, previous["content"].splitlines())} if previous else {}
            for row in records:
                old = merged.get(row["photo_id"])
                if old and old["annotation_session_id"] == row["annotation_session_id"] and old["review_version"] > row["review_version"]:
                    raise ReviewError("A newer review is already uploaded; import the latest JSONL", 409)
                merged[row["photo_id"]] = row
            content = "".join(canonical(row) + "\n" for row in sorted(merged.values(), key=lambda r: r["image_number"]))
            digest = annotation_digest(content)
            changed = not previous or previous["digest"] != digest
            # Durable JSONL must exist before the queue can make this snapshot publishable.
            self.save_snapshot(session, start, end, content)
            if changed:
                published = previous["published_digest"] if previous else ""
                state = ("uploaded" if digest == published else "pending") if complete(content, start, end) else "draft"
                db.execute("""INSERT INTO uploads
                    (session,start,end,content,digest,state,url,error,updated,published_digest,retry_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,0)
                    ON CONFLICT(session,start,end) DO UPDATE SET
                    content=excluded.content,digest=excluded.digest,state=excluded.state,
                    error='',updated=excluded.updated,retry_at=0""",
                    (session, start, end, content, digest, state, previous["url"] if previous else "", "", now, published))
            elif previous["content"] != content:
                # Record the newest revision locally without resetting the quiet period or committing metadata.
                db.execute("UPDATE uploads SET content=? WHERE session=? AND start=? AND end=?",
                           (content, session, start, end))
        self.wake.set()
        return self.status(session, start, end, key)

    def status(self, session, start, end, key):
        bounds(dict(start=start, end=end))
        with self.db() as db:
            self.authenticate(db, session, key)
            row = db.execute("SELECT * FROM uploads WHERE session=? AND start=? AND end=?", (session, start, end)).fetchone()
        if not row:
            raise ReviewError("Group not found", 404)
        return dict(state=row["state"], digest=row["digest"], url=row["url"],
                    count=len(row["content"].splitlines()), error=row["error"],
                    ready_at=max(row["updated"] + self.quiet_seconds, row["retry_at"]),
                    path=f"{DESTINATION}/{session}/{start:03d}_{end:03d}.jsonl")

    def process_one(self):
        # Re-read after obtaining the publisher lock so a queued old snapshot cannot bypass debounce.
        with (self.data_dir / "github-write.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.lock, self.db() as db:
                now = self.clock()
                row = db.execute("""SELECT * FROM uploads WHERE state='pending' AND
                    updated<=? AND retry_at<=? ORDER BY updated LIMIT 1""",
                    (now - self.quiet_seconds, now)).fetchone()
                if not row:
                    return False
                if not complete(row["content"], row["start"], row["end"]):
                    db.execute("UPDATE uploads SET state='draft' WHERE session=? AND start=? AND end=?",
                               (row["session"], row["start"], row["end"]))
                    return False
                if row["digest"] == row["published_digest"]:
                    db.execute("UPDATE uploads SET state='uploaded' WHERE session=? AND start=? AND end=?",
                               (row["session"], row["start"], row["end"]))
                    return False
                self.save_snapshot(row["session"], row["start"], row["end"], row["content"])
                content = self.snapshot_path(row["session"], row["start"], row["end"]).read_text()
                if content != row["content"]:
                    raise RuntimeError("JSONL snapshot mismatch")
            path = f"{DESTINATION}/{row['session']}/{row['start']:03d}_{row['end']:03d}.jsonl"
            try:
                url = self.writer(path, content)
                with self.lock, self.db() as db:
                    db.execute("""UPDATE uploads SET published_digest=?,url=?,
                        state=CASE WHEN digest=? THEN 'uploaded' ELSE state END,
                        error=CASE WHEN digest=? THEN '' ELSE error END
                        WHERE session=? AND start=? AND end=?""",
                        (row["digest"], url, row["digest"], row["digest"], row["session"], row["start"], row["end"]))
            except Exception:
                with self.lock, self.db() as db:
                    db.execute("""UPDATE uploads SET error='GitHub 暂时不可用，服务会自动重试',retry_at=?
                        WHERE session=? AND start=? AND end=? AND digest=?""",
                        (self.clock() + self.retry_seconds, row["session"], row["start"], row["end"], row["digest"]))
        return True

    def worker(self):
        while True:
            if self.process_one():
                time.sleep(15)
            else:
                self.wake.wait(5)
                self.wake.clear()

def handler(receiver, origins):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Do not log browser keys or annotation bodies.

        def reply(self, code, body):
            encoded = canonical(body).encode()
            self.send_response(code)
            if self.headers.get("Origin") in origins:
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
                self.send_header("Vary", "Origin")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def origin(self):
            if self.headers.get("Origin") not in origins:
                raise ReviewError("Origin not permitted", 403)

        def do_OPTIONS(self):
            if self.headers.get("Origin") not in origins:
                return self.reply(403, {"error": "Origin not permitted"})
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Upload-Key")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            try:
                url = urlparse(self.path)
                if url.path == "/health":
                    return self.reply(200, {"status": "ok", "destination": DESTINATION, "round_id": ROUND})
                self.origin()
                if url.path != "/api/status":
                    raise ReviewError("Not found", 404)
                q = parse_qs(url.query)
                result = receiver.status(q["session"][0], int(q["start"][0]), int(q["end"][0]), self.headers.get("X-Upload-Key", ""))
                self.reply(200, result)
            except (KeyError, ValueError):
                self.reply(400, {"error": "Invalid request"})
            except ReviewError as error:
                self.reply(error.status, {"error": str(error)})
            except Exception:
                self.reply(503, {"error": "Receiver temporarily unavailable"})

        def do_POST(self):
            try:
                self.origin()
                if self.path != "/api/reviews":
                    raise ReviewError("Not found", 404)
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise ReviewError("Invalid upload size", 413)
                self.connection.settimeout(15)
                body = json.loads(self.rfile.read(length))
                result = receiver.submit(body, self.headers.get("X-Upload-Key", ""),
                                         self.headers.get("CF-Connecting-IP", self.client_address[0]))
                self.reply(202 if result["state"] == "pending" else 200, result)
            except (ValueError, TimeoutError):
                self.reply(400, {"error": "Invalid request body"})
            except ReviewError as error:
                self.reply(error.status, {"error": str(error)})
            except Exception:
                self.reply(503, {"error": "Receiver temporarily unavailable"})
    return Handler

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="Persistent private storage OUTSIDE the public checkout")
    parser.add_argument("--port", type=int, default=7121)
    parser.add_argument("--origin", action="append", default=["https://jcheniu.github.io"])
    args = parser.parse_args()
    if args.data_dir.resolve().is_relative_to(ROOT):
        raise SystemExit("Private upload storage must be outside the public repository")
    receiver = Receiver(args.data_dir)
    threading.Thread(target=receiver.worker, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(receiver, set(args.origin)))
    print("Review receiver listening on localhost; destination " + DESTINATION, flush=True)
    server.serve_forever()

if __name__ == "__main__":
    main()
