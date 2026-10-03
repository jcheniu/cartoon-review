# Annotation receiver

The service validates the fixed dataset and round, durably saves drafts and JSONL, and automatically
commits only complete changed groups to result/round_1/<session UUID>/<start>_<end>.jsonl on main.

The production receiver and signed KV supervisor are active. ENDPOINTS is bound to the dedicated
cartoon-review-endpoints namespace; no process publishes service.json to GitHub.

## Run

Use Python 3.10+, OpenSSL, and an already authenticated GitHub CLI with repository write access:

    python server/run_service.py --data-dir "<persistent-private-directory>" --port 7121

The directory must be outside the public checkout. It contains the SQLite database, private JSONL
snapshots under jsonl/<session UUID>/, endpoint-signing.pem, endpoint.json, locks and service logs.
The receiver listens only on 127.0.0.1. The supervisor starts a localhost.run SSH HTTPS tunnel
and restarts failed children. Tunnel addresses are published to Cloudflare KV by signed POST requests,
never by Git commits. See [edge deployment](../edge/README.md) before starting a fresh supervisor.

Generate a signing key once in private storage, preserving an existing key:

    test -f "<persistent-private-directory>/endpoint-signing.pem" || (umask 077; openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "<persistent-private-directory>/endpoint-signing.pem")
    openssl pkey -in "<persistent-private-directory>/endpoint-signing.pem" -pubout -outform DER | base64 -w0

The second command prints only the public verification key. For a fresh deployment, use that public key
as PUBLIC_KEY in edge/worker.mjs. Keep the private key outside Git and back it up privately; never copy
it to the browser or Cloudflare. Rotating it requires deploying the matching public key.

The frontend uses https://api.asuperstrongfrog.com. GitHub credentials remain only on the SSH receiver.
For your own stable proxy, run receiver.py directly and configure upload-config.json with that HTTPS URL.
The production CORS origin is https://jcheniu.github.io. Local tests may explicitly add
--origin http://127.0.0.1:8000. Never put a GitHub access token in frontend configuration.

## Persistence and automatic commits

- Every changed save is validated, atomically written to a private JSONL snapshot, fsynced, and recorded
  in SQLite with FULL synchronous writes. Partial groups are drafts and never enter the Git publisher.
- A group must contain exactly the 25 consecutive image numbers named by its range.
- A first complete group enters the publisher immediately after its durable JSONL save. Edits to an
  already published group wait 30 seconds after their last actual change; continuous edits are combined.
  Successful writes remain paced at 15 seconds. Network failures can still delay completion.
- The browser prioritizes complete groups, cancels obsolete snapshot requests and retry backoffs, and
  sends new snapshots before polling publication. Publication polling never holds later groups in a loop.
- Identical requests, timestamp/revision-only differences and returning to the previously published
  content create no commits. The GitHub writer independently rejects partial files and service.json.
- The writer uses the saved JSONL snapshot. Changes received during a write remain pending.
  Failures retry after at least 60 seconds; retry state survives restart.
- Startup preserves existing groups and keys, migrates old partial uploads to drafts, and does not
  recommit already uploaded complete groups. Old public partial files are not deleted.
- The browser retains offline annotations and retries when open or reopened. The browser's download
  request cannot prove that its OS saved a file; the publication gate uses the fsynced server JSONL.

Keep uploads.sqlite3, its WAL, private JSONL and the signing key in persistent storage. Use SQLite's
backup API for consistent snapshots. Successful complete groups are additionally backed by Git history.

Each browser owns a 256-bit upload key, stored locally and hashed by the server. It grants access only
to that session's namespace. Keys never appear in JSONL or GitHub. Session IDs do not verify identities.
Inputs are size-limited; paths, hashes and row fields are validated. Rejected rows require color/pose
reasons. Omitted rows are preserved, and older revisions from the same annotator are rejected.

main contains source/data/results; gh-pages contains the website snapshot. Before pushing to main,
acquire the exclusive flock on <persistent-private-directory>/github-write.lock, pull --ff-only,
commit selected changes and push. Update gh-pages only for website releases.

## Test

    python -m unittest discover -s tests -p "test_receiver.py" -v
    npm test
    npm run test:browser

Tests use temporary databases, fake writers and intercepted browser uploads. They never publish
synthetic training labels in the public results directory.
