"""Signed receiver discovery updates. This module never writes GitHub files."""
import base64
import json
from pathlib import Path
import subprocess
import time
from urllib.request import Request, HTTPRedirectHandler, build_opener
from receiver import ROUND

EDGE = "https://api.asuperstrongfrog.com/api/endpoint"

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def publish(endpoint, key_path, opener=None):
    payload = json.dumps(dict(endpoint=endpoint, round_id=ROUND, updated_at=int(time.time())),
                         separators=(",", ":")).encode()
    signed = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key_path)],
                            input=payload, capture_output=True, check=True, timeout=10).stdout
    request = Request(EDGE, data=payload, headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "cartoon-review-address-publisher/1.0 (+https://github.com/jcheniu/cartoon-review)",
        "X-Endpoint-Signature": base64.b64encode(signed).decode(),
    }, method="POST")
    with (opener or build_opener(NoRedirect())).open(request, timeout=15) as response:
        result = json.loads(response.read(4096))
        if response.status != 200 or result.get("status") != "ok" or result.get("round_id") != ROUND:
            raise RuntimeError("Endpoint publication unavailable")
