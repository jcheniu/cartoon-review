# Annotation receiver

The service validates each registered dataset and generation round, durably saves private JSONL, and automatically
commits only complete changed groups to result/round_N/<session UUID>/<start>_<end>.jsonl on main.
data/rounds.json registers rounds 1–4. The original round retains its existing private directory and keys;
new rounds use isolated rounds/round_N subdirectories. All publishers share the same local Git lock.
Requests carry the generation round_id; old clients can omit it for the legacy round. Round-specific
source and candidate hashes are validated before any write.

## Run

Use Python 3.10+, OpenSSL, and an authenticated GitHub CLI with repository write access:

    python server/run_service.py --data-dir "<persistent-private-directory>" --runtime-dir "<local-runtime-directory>" --port 7121

Both directories must be outside the public checkout. The runtime directory must be on a local
filesystem; network filesystems are rejected. It contains rebuildable process and Git writer locks.
The data directory is persistent and private:

- jsonl/<session UUID>/<start>_<end>.jsonl: authoritative annotations, including incomplete drafts.
- sessions/<session UUID>.json: hashed browser keys needed to restore access.
- state/<session UUID>/<start>_<end>.json: queue, publication and retry metadata.
- format.json: storage format version 2.
- endpoint-signing.pem, endpoint.json and logs: signed tunnel discovery and diagnostics.

The query index is SQLite entirely in memory. No running receiver opens a database, WAL, SHM or
SQLite file lock on the network filesystem. Startup rebuilds the index from the persistent files.
Temporary storage is never the sole copy of an acknowledged annotation or a session key.

The receiver binds to 127.0.0.1. The supervisor keeps a localhost.run SSH HTTPS tunnel alive and
publishes its address to Cloudflare KV using a signature. ENDPOINTS is bound to the dedicated
cartoon-review-endpoints namespace. No address changes are committed to GitHub.

Generate a signing key only for a fresh deployment, preserving existing keys:

    test -f "<persistent-private-directory>/endpoint-signing.pem" || (umask 077; openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "<persistent-private-directory>/endpoint-signing.pem")
    openssl pkey -in "<persistent-private-directory>/endpoint-signing.pem" -pubout -outform DER | base64 -w0

The second command prints the public verification key for edge/worker.mjs. Keep the private key
outside Git and backed up privately. See [edge deployment](../edge/README.md).

## Persistence and automatic commits

Changed annotations are validated and atomically written to private JSONL, fsynced, then accompanied
by durable queue metadata before the in-memory index exposes the new snapshot. Acknowledgement and
publication follow durable storage. If a crash occurs between these writes, startup reconciles the
newer JSONL with the older metadata and restores a pending group.

Each group has its own nonblocking save lock. A stalled filesystem operation holds only that group;
duplicate in-flight writes return a retryable response. Already saved identical requests and status
checks read the in-memory index without filesystem I/O. The browser processes up to four independent
groups concurrently, with separate retry delays. One complete group cannot starve the next.

Exactly 25 consecutive saved image numbers are required for publication. The first complete group
enters the publisher immediately. Edits to a previously published group use a 30-second quiet period.
Successful writes are paced at 15 seconds. Duplicate content, timestamp-only changes and reverting to
the already published content do not create commits. GitHub writes independently compare content.

The publisher reads the immutable index snapshot installed after JSONL persistence. No network
filesystem operation runs under the shared index lock or in the publication path. Publication
metadata is flushed separately; if interrupted, GitHub content comparison avoids a duplicate commit
during recovery. GitHub failures retry after at least 60 seconds while the process is running;
successfully flushed retry metadata also survives restart.

Health includes active/slow saves and pending groups. Saves or metadata flushes blocked for over
20 seconds report degraded health instead of only reporting that the HTTP listener is alive.

Each browser owns a 256-bit upload key. Only its hash is stored server-side. Keys never enter public
JSONL or GitHub. CORS accepts https://jcheniu.github.io. For local tests, receiver.py accepts an explicit
--origin http://127.0.0.1:8000. Never put a GitHub token in frontend configuration.

## Migration and recovery

Stop the old receiver before migration. Make a consistent backup of the legacy SQLite database,
copy that backup to local storage, and keep the old database, WAL and existing JSONL as rollback evidence.
Run the conversion against the local backup:

    python server/migrate_storage.py --database "<local-consistent-backup.sqlite3>" --data-dir "<persistent-private-directory>"

The converter preserves existing JSONL, session key hashes and publication metadata. It neither
invokes the publisher nor writes to GitHub. It refuses to replace an existing version 2 store.
A receiver encountering an unmigrated legacy database refuses to start.

Back up the entire persistent directory privately, including sessions and state. Metadata writes
may lag JSONL during a live copy; recovery deliberately accepts the newer durable JSONL. Restoring
only GitHub results does not recover private browser keys or unfinished drafts.

main contains source/data/results; gh-pages contains the website snapshot. Before a source push,
acquire the flock on <local-runtime-directory>/github-write.lock, pull --ff-only, commit selected
source changes and push. Update gh-pages only for website releases. User annotations are published
only by the receiver's automatic worker.

## Test

    python -m unittest discover -s tests -p "test_*.py" -v
    npm test
    npm run test:browser

Tests use temporary durable directories, fake publishers and intercepted browser uploads. They
cover blocked storage, per-group isolation, recovery after interrupted writes and unchanged saves.
They never publish synthetic labels to the production repository.
