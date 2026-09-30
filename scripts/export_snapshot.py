#!/usr/bin/env python3
"""Publish an allowlisted snapshot; never read private reviews or credentials."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
from datetime import datetime, timezone

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def within(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Path escapes source directory: " + relative)
    return path

def copy_verified(src, dest, expected):
    if digest(src) != expected:
        raise ValueError("Source hash mismatch: " + str(src))
    if dest.exists() and digest(dest) == expected:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    shutil.copyfile(src, tmp)
    if digest(tmp) != expected:
        raise ValueError("Copy hash mismatch: " + str(dest))
    tmp.replace(dest)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Private teacher-loop output directory")
    parser.add_argument("--round", required=True, help="Completed or partially completed generation round")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    source = args.source.resolve()
    numbered = (source / "dataset-numbered").resolve()
    rounds = (source / "rounds").resolve()
    round_dir = within(rounds, args.round)
    manifest_file = numbered / "manifest.json"
    numbered_manifest = json.loads(manifest_file.read_text())
    config_file = round_dir / "config.json"
    config = json.loads(config_file.read_text())
    if config["id"] != args.round or config["dataset_sha256"] != numbered_manifest["source_manifest_sha256"]:
        raise ValueError("Dataset/round mismatch")
    public_config = {k: config[k] for k in (
        "id", "created", "dataset_sha256", "style", "negative", "seed",
        "steps", "guidance", "size", "output_size", "output_transform",
        "control_scale", "control_end", "variants") if k in config}
    for key in ("model", "controlnet"):
        public_config[key] = {k: config[key][k] for k in ("repo", "revision")}
    public_config["uses_adapter"] = bool(config.get("adapter"))
    public_config["private_config_sha256"] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    fields = (
        "id", "image_number", "legacy_id", "sha256", "category", "split", "group_id",
        "caption", "width", "height", "license", "attribution", "source_url",
        "source_page", "source_id", "source_revision", "source_archive_sha256",
        "synthetic", "original_sha256", "original_size", "crop_box", "face_box")
    items = []
    for item in numbered_manifest["items"]:
        photo_id = str(item["image_number"]).zfill(3)
        if item["id"] != photo_id:
            raise ValueError("Numbering mismatch")
        public = {key: item[key] for key in fields if key in item}
        public["path"] = "data/images/" + photo_id + ".jpg"
        copy_verified(within(numbered, item["path"]), args.output / public["path"], item["sha256"])
        public["license_url"] = (
            "https://creativecommons.org/licenses/by-sa/4.0/"
            if item["license"] == "CC-BY-SA-4.0"
            else "https://creativecommons.org/publicdomain/zero/1.0/")
        public["candidates"] = None
        result_path = within(round_dir, item["legacy_id"] + "/result.json")
        if result_path.is_file():
            result = json.loads(result_path.read_text())
            if result["source_sha256"] != item["sha256"]:
                raise ValueError("Candidate/source mismatch: " + photo_id)
            if result["config_sha256"] != public_config["private_config_sha256"]:
                raise ValueError("Candidate/config mismatch: " + photo_id)
            candidates = {}
            for variant in ("a", "b"):
                candidate = result[variant]
                src = within(rounds, candidate["path"].removeprefix("rounds/"))
                if not src.is_relative_to(round_dir):
                    raise ValueError("Candidate belongs to another round")
                header = src.read_bytes()[:24]
                if header[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", header[16:24]) != (256, 256):
                    raise ValueError("Candidates must be 256 x 256 PNGs")
                out_path = "data/candidates/" + args.round + "/" + photo_id + "_" + variant + ".png"
                copy_verified(src, args.output / out_path, candidate["sha256"])
                candidates[variant] = {key: candidate[key] for key in ("sha256", "seed", "strength", "width", "height")}
                candidates[variant]["path"] = out_path
            public["master_hashes"] = {v: result[v].get("master_sha256") for v in ("a", "b")}
            public["candidates"] = candidates
            public["prompt"] = result["prompt"]
            public["input_transform"] = result["input_transform"]
        items.append(public)
    items.sort(key=lambda item: item["image_number"])
    if [item["id"] for item in items] != [str(i).zfill(3) for i in range(500)]:
        raise ValueError("Expected exactly 500 numbered inputs")
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_sha256": numbered_manifest["source_manifest_sha256"],
        "numbered_manifest_sha256": digest(manifest_file),
        "round": public_config,
        "total": len(items),
        "ready": sum(bool(item["candidates"]) for item in items),
        "items": items,
    }
    target = args.output / "data/manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(target)
    print(json.dumps({"inputs": manifest["total"], "ready_pairs": manifest["ready"], "round": args.round}))

if __name__ == "__main__":
    main()
