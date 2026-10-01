# Annotation receiver

The static frontend submits each saved group to this optional Python service. The service validates
the fixed dataset and generation round, persists the queue in SQLite, and writes only
result/round_1/<session UUID>/<start>_<end>.jsonl in jcheniu/cartoon-review on main.

## Run

Use Python 3.10+ and an already authenticated GitHub CLI with repository write access:

    python server/run_service.py --data-dir "<persistent-private-directory>" --port 7121

The private directory must be outside the public checkout. The service listens only on 127.0.0.1.
The supervisor starts an SSH HTTPS tunnel using localhost.run and restarts failed child processes.
It publishes changed endpoints in result/round_1/service.json. The Cloudflare Worker at api.asuperstrongfrog.com refreshes this discovery after connection failures.
The production frontend uses that fixed Worker URL.
For your own stable HTTPS proxy, run server/receiver.py directly instead. Set the HTTPS endpoint and
round_id in upload-config.json. The frontend accepts HTTPS endpoints only.

The production CORS origin is https://jcheniu.github.io. For local testing explicitly add, for example,
--origin http://127.0.0.1:8000. Never put a GitHub access token in the frontend or upload-config.json.

A free temporary tunnel can be used initially. Its URL may change when the tunnel restarts.
The bundled supervisor publishes its new endpoint automatically. If running a tunnel manually, update the discovery file when its URL changes. For a stable long-term service,
use a maintained domain and supervise both the receiver and tunnel. A lost connection leaves browser
records intact; uploads retry while the page is open and resume from browser storage on reopening.

## Storage and publishing

Keep uploads.sqlite3 and its WAL in persistent private storage. Use SQLite's backup API for snapshots.
The queue survives receiver restarts. Successful JSONL uploads are also backed by Git history.

Each browser owns an unpredictable 256-bit upload key, stored locally and hashed by the server.
It grants access only to that browser's session namespace. Keys never appear in JSONL or GitHub.
This is anonymous review collection; a session ID does not verify a person's identity.

The server limits request size, validates all row fields and hashes, ignores unsupported client fields,
and never uses client-supplied filesystem paths or repository names. Rejected records need a color/pose
reason. Updated files preserve omitted rows and reject older revisions from the same annotator.
GitHub writes are serialized and paced at 15 seconds, with automatic retries.

main contains results; gh-pages contains the published website snapshot.
Before pushing source/data changes to main, acquire the same exclusive flock on
<persistent-private-directory>/github-write.lock, pull main with --ff-only, commit, and push.
Update gh-pages when publishing UI or candidate changes. Annotation writes do not change gh-pages.

## Test

    python -m unittest discover -s tests -p "test_receiver.py" -v

Tests use temporary databases and a fake GitHub writer. They do not create training labels in the public repo.
