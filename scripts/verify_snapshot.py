#!/usr/bin/env python3
"""Verify immutable legacy sources and all round-specific previews/candidate hashes."""
from collections import Counter
from pathlib import Path
import hashlib,json,struct
root=Path(__file__).resolve().parents[1]
def verify(relative,expected):
    path=(root/relative).resolve()
    assert path.is_relative_to(root),relative
    raw=path.read_bytes();assert hashlib.sha256(raw).hexdigest()==expected,relative
    return raw
legacy=json.loads((root/"data/manifest.json").read_text())
assert legacy["total"]==500
assert Counter(x["category"] for x in legacy["items"])=={"animal":250,"anime":250}
assert Counter(x["split"] for x in legacy["items"])=={"train":400,"validation":50,"test":50}
catalog=json.loads((root/"data/rounds.json").read_text()) if (root/"data/rounds.json").exists() else {"rounds":[dict(id="round_1",manifest="data/manifest.json",round_id=legacy["round"]["id"])]}
if len(catalog["rounds"])>1:assert [r["id"] for r in catalog["rounds"]]==["round_1","round_2","round_3","round_4"]
active_ready=0
new_ids=set();new_hashes=set()
for entry in catalog["rounds"]:
    manifest=json.loads((root/entry["manifest"]).read_text())
    total=500 if entry["id"]=="round_1" else 250
    assert manifest["total"]==total and manifest["round"]["id"]==entry["round_id"]
    assert [i["id"] for i in manifest["items"]]==[f"{i:03d}" for i in range(total)]
    ready=0
    for item in manifest["items"]:
        assert item["attribution"] and item["source_url"].startswith("https://")
        assert item["license_url"].startswith(("https://","http://"))
        verify(item["path"],item.get("preview_sha256",item["sha256"]))
        if entry["id"]!="round_1":
            assert item["legacy_id"] not in new_ids and item["sha256"] not in new_hashes
            new_ids.add(item["legacy_id"]);new_hashes.add(item["sha256"])
            assert item["category"]=="animal" and item["split"]=="train"
            assert manifest["round"]["uses_adapter"] and manifest["round"]["adapter"]["sha256"]
        if item["candidates"]:
            ready+=1
            assert set(item["candidates"])=={"a","b"}
            for candidate in item["candidates"].values():
                raw=verify(candidate["path"],candidate["sha256"])
                assert raw[:8]==b"\x89PNG\r\n\x1a\n" and struct.unpack(">II",raw[16:24])==(256,256)
            if item["image_number"]<250:active_ready+=1
    assert ready==manifest["ready"]
    print(f"Verified {entry['id']}: {total} inputs, {ready} complete A/B pairs.")
if len(catalog["rounds"])==4:assert len(new_ids)==750
print(f"Active pet pairs ready: {active_ready}/1000; legacy image bytes and hashes preserved.")
