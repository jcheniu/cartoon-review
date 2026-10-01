# Fixed upload domain

Target: https://api.asuperstrongfrog.com

This small Cloudflare Worker gives the existing private receiver a fixed HTTPS entry point.
It forwards only /health, /api/status and /api/reviews. GitHub credentials remain on the SSH receiver.
The Worker reads the current temporary transport endpoint from the fixed public service.json discovery file,
validates its hostname, and retries intermittent transport failures. The receiver still owns validation,
persistent upload queues, session namespaces and GitHub writes.

A custom domain stabilizes the browser-facing URL. It does not remove the SSH service or temporary
upstream transport dependency. The existing supervisor continues to reconnect and publish upstream changes.

## Deployment

1. Sign in to the Cloudflare account managing asuperstrongfrog.com.
2. Deploy worker.mjs as the ES module Worker cartoon-review-upload.
3. Add api.asuperstrongfrog.com in Settings → Domains & Routes → Custom Domain.
4. Check HTTPS /health and cross-origin requests from https://jcheniu.github.io.
5. Only after validation, change upload-config.json to the fixed endpoint and remove discovery_url there.

Wrangler may also deploy using this directory's wrangler.jsonc after account authorization.
No GitHub token or other secret is needed by the Worker. Never copy the receiver's credentials into it.

Tests:

    node --test tests/edge.test.mjs

Do not change the GitHub Pages website's origin during annotation without first migrating browser-local data.
