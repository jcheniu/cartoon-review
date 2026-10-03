#!/usr/bin/env python3
"""Keep the receiver and temporary SSH tunnel alive; publish signed endpoint discovery to Cloudflare KV."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time
from receiver import ROOT, ROUND, ensure_local_runtime
from endpoint import publish

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True, help="Local filesystem for process locks")
    parser.add_argument("--port", type=int, default=7121)
    args = parser.parse_args()
    directory = args.data_dir.resolve()
    if directory.is_relative_to(ROOT):
        raise SystemExit("Private state must be outside the public repository")
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    runtime = args.runtime_dir.resolve()
    if runtime.is_relative_to(ROOT):
        raise SystemExit("Runtime locks must be outside the public repository")
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    ensure_local_runtime(runtime)
    supervisor_lock = (runtime / "supervisor.lock").open("a")
    fcntl.flock(supervisor_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    key_path = directory / "endpoint-signing.pem"
    if not key_path.is_file():
        raise SystemExit("Create the private endpoint signing key before starting the supervisor")
    receiver, tunnel = None, None
    receiver_log = (directory / "receiver.log").open("ab")
    tunnel_log = (directory / "tunnel.log").open("a")
    selector = selectors.DefaultSelector()
    endpoint, published, retry_at, tail = "", "", 0, ""
    active = True
    def stop(*_):
        nonlocal active
        active = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while active:
            if receiver is None or receiver.poll() is not None:
                receiver = subprocess.Popen([sys.executable, "-u", str(ROOT / "server/receiver.py"),
                    "--data-dir", str(directory), "--runtime-dir", str(runtime), "--port", str(args.port)],
                    stdout=receiver_log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                print("Receiver started", flush=True)
            if tunnel is None or tunnel.poll() is not None:
                if tunnel:
                    selector.unregister(tunnel.stdout)
                    tunnel.stdout.close()
                    time.sleep(5)
                tunnel = subprocess.Popen([
                    "ssh", "-T", "-p", "22", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                    "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=" + str(directory / "known_hosts"),
                    "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", "-o", "ExitOnForwardFailure=yes",
                    "-R", f"80:127.0.0.1:{args.port}", "nokey@localhost.run", "--", "--output", "json",
                ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                tail = ""
                os.set_blocking(tunnel.stdout.fileno(), False)
                selector.register(tunnel.stdout, selectors.EVENT_READ)
                print("Temporary HTTPS tunnel connecting", flush=True)
            for ready, _ in selector.select(timeout=2):
                chunk = os.read(ready.fileobj.fileno(), 65536).decode(errors="replace")
                if not chunk:
                    continue
                tunnel_log.write(chunk); tunnel_log.flush()
                combined = tail + chunk
                tail = combined[-512:]
                matches = re.findall(r"(?:https?://)?([a-zA-Z0-9-]+\.lhr\.life)", combined)
                if matches:
                    current = "https://" + matches[-1]
                    if current != endpoint:
                        endpoint, retry_at = current, 0
                        print("Received public endpoint " + endpoint, flush=True)
                        (directory / "endpoint.json").write_text(json.dumps({"endpoint": endpoint, "round_id": ROUND}))
            if endpoint and endpoint != published and time.time() >= retry_at:
                try:
                    publish(endpoint, key_path)
                    published = endpoint
                    print("Published endpoint discovery", flush=True)
                except Exception:
                    retry_at = time.time() + 60
                    print("Endpoint publication will retry", flush=True)
    finally:
        for process in (receiver, tunnel):
            if process and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill()
        receiver_log.close();tunnel_log.close()

if __name__ == "__main__":
    main()
