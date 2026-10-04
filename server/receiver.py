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
import tempfile
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

def bounds(value, total=500):
    if not isinstance(value, dict):
        raise ReviewError("Invalid group")
    start, end = value.get("start"), value.get("end")
    if type(start) is not int or type(end) is not int or not 0 <= start <= total - 25 or end != start + 24:
        raise ReviewError(f"A group must contain 25 consecutive indices in 0–{total-1}")
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
        if (row.get("round_id") != manifest["round"]["id"] or row.get("dataset_sha256") != manifest["dataset_sha256"]
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
            round_id=manifest["round"]["id"], annotation_session_id=session,
            item_id=item["legacy_id"], photo_id=item["id"], image_number=item["image_number"],
            category=item["category"], split=item["split"], group_id=item["group_id"],
            accepted=accepted, preferred=preferred, rejection_reasons=reasons,
            caption=caption.strip(), review_version=version, reviewed_at=reviewed,
            source_sha256=item["sha256"],
            source=dict(path=item["path"], sha256=item.get("preview_sha256",item["sha256"]), license=item["license"], source_url=item["source_url"], **({"original_sha256":item["sha256"]} if item.get("preview_sha256") else {})),
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
    match = re.fullmatch(r"result/round_([1-4])/(" + UUID.pattern[1:-1] + r")/(\d{3})_(\d{3})\.jsonl", path)
    if not match:
        raise ValueError("Only review JSONL files may be committed")
    round_number = int(match.group(1))
    start, end = map(int, match.groups()[2:])
    bounds(dict(start=start, end=end), 500 if round_number == 1 else 250)
    if not complete(content, start, end):
        raise ValueError("Only complete 25-row groups may be committed")
    for attempt in range(3):
        old = github_json("GET", f"repos/{REPO}/contents/{path}?ref={BRANCH}")
        encoded = base64.b64encode(content.encode()).decode()
        if old:
            previous = base64.b64decode(old.get("content", "")).decode()
            if previous == content or annotation_digest(previous) == annotation_digest(content):
                return old["html_url"]
        body = dict(message=f"Save completed round {round_number} review group " + path.rsplit("/", 1)[-1], content=encoded, branch=BRANCH,
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

def ensure_local_runtime(path):
    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.exists():
        return
    resolved = Path(path).resolve()
    matches = []
    for line in mountinfo.read_text().splitlines():
        fields = line.split()
        mount = Path(fields[4].replace("\\040", " "))
        if resolved == mount or mount in resolved.parents:
            matches.append((len(str(mount)), fields[fields.index("-")+1]))
    filesystem = max(matches)[1] if matches else ""
    if filesystem in ("nfs", "nfs4", "cifs", "smb3", "fuse.sshfs", "9p"):
        raise RuntimeError("Runtime locks require a local filesystem; use --runtime-dir")

def atomic_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + str(threading.get_ident()) + ".tmp")
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

def persist_record(directory, row):
    metadata = {k: v for k, v in row.items() if k != "content"}
    atomic_text(Path(directory) / "state" / row["session"] /
                f"{row['start']:03d}_{row['end']:03d}.json", canonical(metadata))

def migrate_database(database, directory):
    """Explicit offline migration from a consistent SQLite snapshot; never publishes labels."""
    directory = Path(directory)
    ensure_local_runtime(Path(database).parent)
    if (directory / "format.json").exists():
        raise RuntimeError("Durable JSON storage already exists; preserve it")
    with sqlite3.connect("file:" + str(Path(database).resolve()) + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        sessions = db.execute("SELECT id,key_hash FROM sessions").fetchall()
        rows = db.execute("SELECT * FROM uploads").fetchall()
    for session in sessions:
        if not UUID.fullmatch(session["id"]) or not re.fullmatch(r"[a-f0-9]{64}", session["key_hash"]):
            raise RuntimeError("Invalid legacy session")
        atomic_text(directory / "sessions" / (session["id"] + ".json"), canonical(dict(session)))
    for legacy in rows:
        row = dict(legacy)
        bounds(dict(start=row["start"], end=row["end"]))
        if row["session"] not in {s["id"] for s in sessions}:
            raise RuntimeError("Missing legacy session")
        digest = annotation_digest(row["content"])
        row.setdefault("published_digest", digest if row["state"] == "uploaded" else "")
        row.setdefault("retry_at", 0)
        row["digest"] = digest
        path = directory / "jsonl" / row["session"] / f"{row['start']:03d}_{row['end']:03d}.jsonl"
        # Existing durable JSONL can be newer than the database snapshot.
        if not path.exists():
            atomic_text(path, row["content"])
        persist_record(directory, row)
    known = {s["id"] for s in sessions}
    if any(p.parent.name not in known for p in (directory / "jsonl").glob("*/*.jsonl")):
        raise RuntimeError("A durable group has no session key in the migration snapshot")
    atomic_text(directory / "format.json", canonical(dict(version=2, storage="durable-jsonl")))
    return dict(sessions=len(sessions), groups=len(rows))

class Receiver:
    def __init__(self, data_dir, manifest_path=ROOT / "data/manifest.json", writer=github_write,
                 clock=time.time, quiet_seconds=30, retry_seconds=60, runtime_dir=None, destination=DESTINATION):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data_dir, 0o700)
        self.runtime_temp = tempfile.TemporaryDirectory(prefix="cartoon-review-runtime-") if runtime_dir is None else None
        self.runtime_dir = Path(runtime_dir) if runtime_dir else Path(self.runtime_temp.name)
        self.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        ensure_local_runtime(self.runtime_dir)
        self.manifest = json.loads(Path(manifest_path).read_text())
        if not re.fullmatch(r"result/round_[1-4]", destination):
            raise ValueError("Invalid server-configured destination")
        self.destination = destination
        self.round_id = self.manifest["round"]["id"]
        self.total = len(self.manifest["items"])
        self.writer = writer
        self.clock, self.quiet_seconds, self.retry_seconds = clock, quiet_seconds, retry_seconds
        self.wake, self.metadata_wake = threading.Event(), threading.Event()
        self.lock = threading.RLock()
        self.rate, self.group_locks, self.session_locks = {}, {}, {}
        self.active_saves, self.dirty = {}, set()
        self.flushing_since = 0
        # SQLite is only an in-memory index. No database, WAL, SHM or SQLite locks touch NFS.
        self.db_uri = "file:cartoon-review-" + str(id(self)) + "?mode=memory&cache=shared"
        self.anchor = sqlite3.connect(self.db_uri, uri=True, check_same_thread=False)
        marker = self.data_dir / "format.json"
        if not marker.exists() and (self.data_dir / "uploads.sqlite3").exists():
            raise RuntimeError("Migrate the legacy database before starting durable JSON storage")
        if marker.exists() and json.loads(marker.read_text()).get("version") != 2:
            raise RuntimeError("Unsupported durable storage format")
        with self.db() as db:
            db.executescript("""
                CREATE TABLE sessions(id TEXT PRIMARY KEY, key_hash TEXT NOT NULL);
                CREATE TABLE uploads(
                    session TEXT,start INTEGER,end INTEGER,content TEXT,digest TEXT,
                    state TEXT,url TEXT,error TEXT,updated REAL,
                    published_digest TEXT NOT NULL DEFAULT '',retry_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(session,start,end));
            """)
            for path in (self.data_dir / "sessions").glob("*.json"):
                value = json.loads(path.read_text())
                if path.stem != value["id"] or not UUID.fullmatch(value["id"]) or not re.fullmatch(r"[a-f0-9]{64}", value["key_hash"]):
                    raise RuntimeError("Invalid durable session")
                db.execute("INSERT INTO sessions VALUES(?,?)", (value["id"], value["key_hash"]))
            for path in (self.data_dir / "jsonl").glob("*/*.jsonl"):
                session = path.parent.name
                match = re.fullmatch(r"(\d{3})_(\d{3})\.jsonl", path.name)
                if not UUID.fullmatch(session) or not match:
                    raise RuntimeError("Invalid durable group path")
                if not db.execute("SELECT 1 FROM sessions WHERE id=?", (session,)).fetchone():
                    raise RuntimeError("Durable group is missing its session key")
                start, end = map(int, match.groups())
                bounds(dict(start=start, end=end), self.total)
                content = path.read_text()
                parsed = [json.loads(line) for line in content.splitlines() if line.strip()]
                cleaned = validate_rows(self.manifest, parsed, start, end)
                content = "".join(canonical(row) + "\n" for row in cleaned)
                digest = annotation_digest(content)
                meta_path = self.data_dir / "state" / session / path.with_suffix(".json").name
                old = json.loads(meta_path.read_text()) if meta_path.exists() else {}
                published = old.get("published_digest", "")
                same = old.get("digest") == digest
                row = dict(session=session, start=start, end=end, content=content, digest=digest,
                    state=("uploaded" if digest == published else "pending") if complete(content,start,end) else "draft",
                    url=old.get("url", ""), error=old.get("error", "") if same else "",
                    updated=old.get("updated", path.stat().st_mtime) if same else path.stat().st_mtime,
                    published_digest=published, retry_at=old.get("retry_at", 0) if same else 0)
                self.put(db, row)
        if not marker.exists():
            atomic_text(marker, canonical(dict(version=2, storage="durable-jsonl")))

    def close(self):
        self.anchor.close()
        if self.runtime_temp:
            self.runtime_temp.cleanup()

    @contextlib.contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.db_uri, uri=True)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()

    def put(self, db, row):
        columns = ("session","start","end","content","digest","state","url","error","updated","published_digest","retry_at")
        db.execute("INSERT OR REPLACE INTO uploads VALUES(?,?,?,?,?,?,?,?,?,?,?)", tuple(row[k] for k in columns))

    def snapshot_path(self, session, start, end):
        return self.data_dir / "jsonl" / session / f"{start:03d}_{end:03d}.jsonl"

    def save_snapshot(self, session, start, end, content):
        atomic_text(self.snapshot_path(session, start, end), content)

    def authenticate(self, db, session, key):
        if not isinstance(session, str) or not UUID.fullmatch(session) or not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ReviewError("Invalid upload session", 403)
        row = db.execute("SELECT key_hash FROM sessions WHERE id=?", (session,)).fetchone()
        if not row:
            raise ReviewError("Unknown upload session", 404)
        if not hmac.compare_digest(row["key_hash"], sha(key)):
            raise ReviewError("Upload session key does not match", 403)

    def ensure_session(self, session, key):
        with self.db() as db:
            try:
                self.authenticate(db, session, key)
                return
            except ReviewError as error:
                if error.status != 404:
                    raise
            if db.execute("SELECT count(*) FROM sessions").fetchone()[0] >= 1000:
                raise ReviewError("Receiver capacity reached", 429)
            lock = self.session_locks.setdefault(session, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ReviewError("Session is being saved; retry shortly", 503)
        try:
            with self.db() as db:
                if db.execute("SELECT 1 FROM sessions WHERE id=?", (session,)).fetchone():
                    return self.authenticate(db, session, key)
            atomic_text(self.data_dir / "sessions" / (session + ".json"),
                        canonical(dict(id=session, key_hash=sha(key))))
            with self.db() as db:
                db.execute("INSERT INTO sessions VALUES(?,?)", (session, sha(key)))
        finally:
            lock.release()

    def submit(self, body, key, ip="local"):
        if not isinstance(body, dict):
            raise ReviewError("Invalid request")
        if body.get("round_id", self.round_id) != self.round_id:
            raise ReviewError("Wrong round")
        session = body.get("session_id")
        start, end = bounds(body.get("range"), self.total)
        records = validate_rows(self.manifest, body.get("records"), start, end)
        self.ensure_session(session, key)
        identity = (session, start, end)
        with self.db() as db:
            now = self.clock()
            recent = [stamp for stamp in self.rate.get(ip, []) if now - stamp < 60]
            if len(recent) >= 120:
                raise ReviewError("Please retry shortly", 429)
            self.rate[ip] = recent + [now]
            if len(self.rate) > 1000:
                self.rate = {address: stamps for address, stamps in self.rate.items() if now - stamps[-1] < 60}
            previous = db.execute("SELECT content FROM uploads WHERE session=? AND start=? AND end=?", identity).fetchone()
            if previous:
                saved = {r["photo_id"]: r for r in map(json.loads, previous["content"].splitlines())}
                if all(saved.get(r["photo_id"]) == r for r in records):
                    return self.status(session, start, end, key)
            group_lock = self.group_locks.setdefault(identity, threading.Lock())
        if not group_lock.acquire(blocking=False):
            raise ReviewError("本组正在持久保存，稍后自动重试；其他组可继续上传", 503)
        try:
            with self.db() as db:
                previous = db.execute("SELECT * FROM uploads WHERE session=? AND start=? AND end=?", identity).fetchone()
                previous = dict(previous) if previous else None
                merged = {r["photo_id"]: r for r in map(json.loads, previous["content"].splitlines())} if previous else {}
                for row in records:
                    old = merged.get(row["photo_id"])
                    if old and old["annotation_session_id"] == row["annotation_session_id"] and old["review_version"] > row["review_version"]:
                        raise ReviewError("A newer review is already uploaded; import the latest JSONL", 409)
                    merged[row["photo_id"]] = row
                content = "".join(canonical(row) + "\n" for row in sorted(merged.values(), key=lambda r: r["image_number"]))
                if previous and previous["content"] == content:
                    return self.status(session, start, end, key)
                digest = annotation_digest(content)
                changed = not previous or previous["digest"] != digest
                published = previous["published_digest"] if previous else ""
                record = dict(session=session, start=start, end=end, content=content, digest=digest,
                    state=("uploaded" if digest == published else "pending") if complete(content,start,end) else "draft",
                    url=previous["url"] if previous else "", error="" if changed else previous["error"],
                    updated=now if changed else previous["updated"], published_digest=published,
                    retry_at=0 if changed else previous["retry_at"])
                self.active_saves[identity] = self.clock()
            # Network filesystem I/O is outside the shared index lock and isolated to this group.
            self.save_snapshot(session, start, end, content)
            persist_record(self.data_dir, record)
            with self.db() as db:
                current = db.execute("SELECT * FROM uploads WHERE session=? AND start=? AND end=?", identity).fetchone()
                if current and (current["published_digest"], current["url"]) != (record["published_digest"], record["url"]):
                    record.update(published_digest=current["published_digest"], url=current["url"])
                    record["state"] = ("uploaded" if digest == current["published_digest"] else "pending") if complete(content,start,end) else "draft"
                    self.dirty.add(identity)
                    self.metadata_wake.set()
                self.put(db, record)
            self.wake.set()
            return self.status(session, start, end, key)
        finally:
            with self.lock:
                self.active_saves.pop(identity, None)
            group_lock.release()

    def status(self, session, start, end, key, round_id=None):
        if round_id is not None and round_id != self.round_id:
            raise ReviewError("Wrong round")
        bounds(dict(start=start, end=end), self.total)
        with self.db() as db:
            self.authenticate(db, session, key)
            row = db.execute("SELECT * FROM uploads WHERE session=? AND start=? AND end=?", (session,start,end)).fetchone()
            if not row:
                raise ReviewError("Group not found", 404)
            return dict(state=row["state"], digest=row["digest"], url=row["url"],
                        count=len(row["content"].splitlines()), error=row["error"],
                        ready_at=max(row["updated"] + (self.quiet_seconds if row["published_digest"] else 0), row["retry_at"]),
                        path=f"{self.destination}/{session}/{start:03d}_{end:03d}.jsonl")

    def process_one(self):
        with (self.runtime_dir / "github-write.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            with self.db() as db:
                now = self.clock()
                candidates = db.execute("""SELECT * FROM uploads WHERE state='pending' AND
                    (published_digest='' OR updated<=?) AND retry_at<=? ORDER BY updated""",
                    (now-self.quiet_seconds, now)).fetchall()
                row = next((dict(r) for r in candidates
                    if (r["session"],r["start"],r["end"]) not in self.active_saves), None)
                if not row:
                    return False
                identity = (row["session"], row["start"], row["end"])
                if not complete(row["content"], row["start"], row["end"]):
                    db.execute("UPDATE uploads SET state='draft' WHERE session=? AND start=? AND end=?", identity)
                    self.dirty.add(identity)
                    self.metadata_wake.set()
                    return False
            path = f"{self.destination}/{row['session']}/{row['start']:03d}_{row['end']:03d}.jsonl"
            try:
                # The immutable index row is installed only after its JSONL has been fsynced.
                url = self.writer(path, row["content"])
                with self.db() as db:
                    db.execute("""UPDATE uploads SET published_digest=?,url=?,
                        state=CASE WHEN digest=? THEN 'uploaded' ELSE state END,
                        error=CASE WHEN digest=? THEN '' ELSE error END
                        WHERE session=? AND start=? AND end=?""",
                        (row["digest"],url,row["digest"],row["digest"],*identity))
                    self.dirty.add(identity)
            except Exception:
                with self.db() as db:
                    db.execute("""UPDATE uploads SET error='GitHub 暂时不可用，服务会自动重试',retry_at=?
                        WHERE session=? AND start=? AND end=? AND digest=?""",
                        (self.clock()+self.retry_seconds,*identity,row["digest"]))
                    self.dirty.add(identity)
            self.metadata_wake.set()
        return True

    def flush_metadata_once(self):
        with self.lock:
            identities = list(self.dirty)
        for identity in identities:
            with self.lock:
                group_lock = self.group_locks.setdefault(identity, threading.Lock())
            if not group_lock.acquire(blocking=False):
                continue
            try:
                with self.db() as db:
                    row = db.execute("SELECT * FROM uploads WHERE session=? AND start=? AND end=?", identity).fetchone()
                    self.dirty.discard(identity)
                    self.flushing_since = self.clock()
                try:
                    persist_record(self.data_dir, dict(row))
                except Exception:
                    with self.lock:
                        self.dirty.add(identity)
                    return False
                return True
            finally:
                with self.lock:
                    self.flushing_since = 0
                group_lock.release()
        return False

    def metadata_worker(self):
        while True:
            if not self.flush_metadata_once():
                self.metadata_wake.wait(5)
                self.metadata_wake.clear()

    def health(self):
        with self.db() as db:
            now = self.clock()
            slow = sum(now-started > 20 for started in self.active_saves.values())
            pending = db.execute("SELECT count(*) FROM uploads WHERE state='pending'").fetchone()[0]
            metadata_slow = bool(self.flushing_since and now-self.flushing_since > 20)
            return dict(status="degraded" if slow or metadata_slow else "ok", storage="durable-jsonl-v2",
                        active_saves=len(self.active_saves), slow_saves=slow, pending_groups=pending,
                        metadata_pending=len(self.dirty), metadata_slow=metadata_slow,
                        destination=self.destination, round_id=self.round_id)

    def worker(self):
        while True:
            if self.process_one():
                time.sleep(15)
            else:
                self.wake.wait(2)
                self.wake.clear()


class CollectionReceiver:
    """Route each known round to its own durable store; legacy storage stays in place."""
    def __init__(self, receivers):
        self.receivers = {receiver.round_id: receiver for receiver in receivers}
        if len(self.receivers) != len(receivers):
            raise ValueError("Duplicate generation round")
    def select(self, round_id):
        selected = self.receivers.get(round_id)
        if selected is None:
            raise ReviewError("Unknown round", 400)
        return selected
    def submit(self, body, key, ip="local"):
        if not isinstance(body, dict):
            raise ReviewError("Invalid request")
        records = body.get("records")
        inferred = records[0].get("round_id", ROUND) if isinstance(records, list) and records and isinstance(records[0], dict) else ROUND
        return self.select(body.get("round_id", inferred)).submit(body, key, ip)
    def status(self, session, start, end, key, round_id=ROUND):
        return self.select(round_id).status(session, start, end, key, round_id)
    def health(self):
        rounds = [receiver.health() for receiver in self.receivers.values()]
        return dict(status="ok" if all(r["status"] == "ok" for r in rounds) else "degraded",
                    storage="durable-jsonl-v2", round_id=ROUND, rounds=rounds)


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
                    health = receiver.health()
                    return self.reply(200 if health["status"] == "ok" else 503, health)
                self.origin()
                if url.path != "/api/status":
                    raise ReviewError("Not found", 404)
                q = parse_qs(url.query)
                result = receiver.status(q["session"][0], int(q["start"][0]), int(q["end"][0]), self.headers.get("X-Upload-Key", ""), round_id=q.get("round_id", [ROUND])[0])
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
    parser.add_argument("--runtime-dir", type=Path, help="Local filesystem directory for rebuildable locks")
    parser.add_argument("--port", type=int, default=7121)
    parser.add_argument("--origin", action="append", default=["https://jcheniu.github.io"])
    args = parser.parse_args()
    if args.data_dir.resolve().is_relative_to(ROOT):
        raise SystemExit("Private upload storage must be outside the public repository")
    if args.runtime_dir and args.runtime_dir.resolve().is_relative_to(ROOT):
        raise SystemExit("Runtime locks must be outside the public repository")
    receivers = [Receiver(args.data_dir, runtime_dir=args.runtime_dir)]
    catalog_path = ROOT / "data/rounds.json"
    if catalog_path.exists():
        for entry in json.loads(catalog_path.read_text())["rounds"]:
            if entry["id"] == "round_1":
                continue
            if not re.fullmatch(r"round_[2-4]", entry["id"]):
                raise ValueError("Unexpected public collection")
            manifest_path = (ROOT / entry["manifest"]).resolve()
            if not manifest_path.is_relative_to(ROOT / "data"):
                raise ValueError("Manifest escapes public data")
            child = Receiver(args.data_dir / "rounds" / entry["id"], manifest_path=manifest_path,
                             runtime_dir=args.runtime_dir, destination="result/" + entry["id"])
            if child.round_id != entry["round_id"]:
                raise ValueError("Collection/generation round mismatch")
            receivers.append(child)
    for child in receivers:
        threading.Thread(target=child.worker, daemon=True).start()
        threading.Thread(target=child.metadata_worker, daemon=True).start()
    receiver = CollectionReceiver(receivers)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(receiver, set(args.origin)))
    print("Review receiver listening on localhost; destination " + DESTINATION, flush=True)
    server.serve_forever()

if __name__ == "__main__":
    main()
