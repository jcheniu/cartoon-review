# Fixed upload domain and address discovery

Production API: https://api.asuperstrongfrog.com
Review website: https://jcheniu.github.io/cartoon-review/

The Worker forwards /health, /api/status and /api/reviews to the private SSH receiver. Browser keys
are forwarded only to allowlisted root HTTPS tunnel hosts; redirects are refused. GitHub credentials
and validation remain on SSH. Existing browser annotations stay accessible on the unchanged website.

Production uses the ENDPOINTS binding to cartoon-review-endpoints. The signed KV Worker was
deployed manually and the SSH supervisor switched on 2026-10-03. A real tunnel address change and
duplicate signed publication were verified to leave GitHub's commit unchanged.

## Address updates without Git commits

The ENDPOINTS binding uses the dedicated cartoon-review-endpoints KV namespace. It stores one public
receiver address for the fixed round. The supervisor POSTs /api/endpoint with an RSA/SHA-256 signature;
the update client uses an explicit application User-Agent; the Worker verifies the public key in worker.mjs, validates the hostname/round and a five-minute
timestamp window, and awaits KV persistence. Identical addresses cause no extra KV writes.
The endpoint signing private key remains in private SSH storage. No Cloudflare API token or GitHub
credential is required by the Worker or the signed update client.

Address discovery reads only KV; it never reads or updates GitHub service.json. The old file remains
a historical snapshot. KV can take up to about 60 seconds to propagate across locations; failed
uploads keep browser data and retry. See [Cloudflare consistency](https://developers.cloudflare.com/kv/api/write-key-value-pairs/).

## Manual deployment

1. In the account managing asuperstrongfrog.com, create the dedicated cartoon-review-endpoints KV
   namespace if it does not already exist.
2. On the existing cartoon-review-upload Worker, Settings > Bindings > Add binding > KV namespace:
   variable ENDPOINTS, select that dedicated namespace, and save/deploy the binding.
   This grants the Worker access only to this address store.
3. Confirm PUBLIC_KEY in worker.mjs matches the private receiver's public verification key.
   Replace worker.js with the tested worker.mjs and deploy manually. The wrangler.jsonc namespace
   ID records the production binding; namespace IDs and the verification key are public.
4. Keep api.asuperstrongfrog.com attached. Keep workers.dev and preview URLs disabled.
5. Restart the supervisor. Verify signed address publication, GET /health, CORS preflight,
   invalid/empty review rejection, and that address refresh creates no Git commits.

Do not use automatic GitHub builds unless separately authorizing a build token.
No receiver signing private key or GitHub token may be copied into Cloudflare.

Tests:

    node --test tests/edge.test.mjs

The Worker code uses [Cloudflare Web Crypto](https://developers.cloudflare.com/workers/runtime-apis/web-crypto/).
Health failures include a connection reason; review errors keep internal diagnostics private.
Do not change the website origin during annotation without migrating browser-local storage.
