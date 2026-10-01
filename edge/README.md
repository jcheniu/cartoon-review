# Fixed upload domain

Production upload endpoint: https://api.asuperstrongfrog.com

Deployed manually from the Cloudflare dashboard. The public review website remains at
https://jcheniu.github.io/cartoon-review/ so existing browser-local annotations stay accessible.

This small Cloudflare Worker gives the existing private receiver a fixed HTTPS entry point.
It forwards only /health, /api/status and /api/reviews. GitHub credentials remain on the SSH receiver.
The Worker reads the current temporary transport endpoint from the fixed public service.json discovery file,
validates its hostname, and retries intermittent transport failures. If GitHub raw-file caching retains a retired
endpoint, it refreshes through the public GitHub Contents API. API refreshes have a one-minute per-isolate
cooldown, and successful refreshed discovery is retained for five minutes. Receiver redirects are explicitly
refused using manual redirect handling, which is supported by the Cloudflare runtime. The receiver still owns validation,
persistent upload queues, session namespaces and GitHub writes.

A custom domain stabilizes the browser-facing URL. It does not remove the SSH service or temporary
upstream transport dependency. The existing supervisor continues to reconnect and publish upstream changes.

## Deployment

1. Sign in to the Cloudflare account managing asuperstrongfrog.com.
2. Open the existing cartoon-review-upload Worker and replace worker.js with this directory’s worker.mjs.
   Deploy the code and verify the active version. Do not use GitHub automatic builds unless intentionally
   authorizing a separate Cloudflare build token.
3. Keep api.asuperstrongfrog.com in Domains. For a fresh installation, Add Domain → asuperstrongfrog.com → subdomain api.
4. Check HTTPS /health and cross-origin requests from https://jcheniu.github.io.
5. Only after validation, change upload-config.json to the fixed endpoint and remove discovery_url there.

Wrangler may also deploy using this directory's wrangler.jsonc after account authorization.
No GitHub token or other secret is needed by the Worker. Never copy the receiver's credentials into it.

Tests:

    node --test tests/edge.test.mjs

Do not change the GitHub Pages website's origin during annotation without first migrating browser-local data.

Health check: GET /health returns status=ok when the receiver is reachable. A failed health check includes
a connection reason; review upload errors do not include those diagnostics. Verify CORS preflight from
https://jcheniu.github.io and ensure invalid/empty labels are rejected without creating result files.
