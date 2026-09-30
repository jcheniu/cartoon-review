#!/usr/bin/env python3
"""Verify all published inputs, complete candidate pairs and split counts."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import struct

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "data/manifest.json").read_text())
assert manifest["total"] == 500
assert [x["id"] for x in manifest["items"]] == [str(i).zfill(3) for i in range(500)]
assert Counter(x["category"] for x in manifest["items"]) == {"animal": 250, "anime": 250}
assert Counter(x["split"] for x in manifest["items"]) == {"train": 400, "validation": 50, "test": 50}
for category in ("animal", "anime"):
    assert Counter(x["split"] for x in manifest["items"] if x["category"] == category) == {"train": 200, "validation": 25, "test": 25}
ready = 0
def verify(relative, expected):
    path = (root / relative).resolve()
    assert path.is_relative_to(root), relative
    payload = path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == expected, relative
    return payload
for item in manifest["items"]:
    assert item["license"] in ("CC-BY-SA-4.0", "CC0-1.0")
    assert item["attribution"] and item["source_url"].startswith("https://")
    verify(item["path"], item["sha256"])
    if item["candidates"]:
        ready += 1
        assert set(item["candidates"]) == {"a", "b"}
        for candidate in item["candidates"].values():
            data = verify(candidate["path"], candidate["sha256"])
            assert data[:8] == b"\x89PNG\r\n\x1a\n"
            assert struct.unpack(">II", data[16:24]) == (256, 256)
            assert candidate["width"] == candidate["height"] == 256
assert ready == manifest["ready"]
print(f"Verified 500 source images, {ready} candidate pairs, hashes, licenses and fixed splits.")
