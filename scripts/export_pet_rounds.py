"""Export three pet batches without changing the legacy round or publishing private state."""
import argparse
import hashlib
import io
import json
from pathlib import Path
from datetime import datetime, timezone
from PIL import Image, ImageOps
from export_snapshot import digest, within, copy_verified

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n")
    temp.replace(path)

def export(source_root, output, require_complete=False):
    source_root, output = source_root.resolve(), output.resolve()
    overrides = json.loads(Path(__file__).with_name("attribution_overrides.json").read_text())
    launch = json.loads((source_root/"launch.json").read_text())
    legacy = json.loads((output/"data/manifest.json").read_text())
    catalog = [dict(id="round_1", label="Round 1 · 原有宠物", count=250,
                    manifest="data/manifest.json", round_id=legacy["round"]["id"],
                    ready=sum(bool(i["candidates"]) for i in legacy["items"][:250]))]
    for batch in launch["batches"]:
        name = batch["name"]
        if name not in ("round_2","round_3","round_4"):
            raise ValueError("Invalid batch")
        source = Path(batch["data"]).resolve()
        numbered = json.loads((source/"dataset-numbered/manifest.json").read_text())
        config = json.loads((source/"rounds"/batch["round_id"]/"config.json").read_text())
        config_sha = hashlib.sha256(json.dumps(config, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if config["dataset_sha256"] != numbered["source_manifest_sha256"] or not config.get("adapter"):
            raise ValueError("Expected the pinned pet LoRA round")
        public_config = {k: config[k] for k in ("id","created","dataset_sha256","style","negative","seed","steps",
                         "guidance","size","output_size","output_transform","control_scale","control_end","variants")}
        public_config.update(uses_adapter=True, adapter={k:config["adapter"][k] for k in ("id","sha256")},
                             private_config_sha256=config_sha)
        for key in ("model","controlnet"):
            public_config[key]={k:config[key][k] for k in ("repo","revision")}
        records=[]
        for original in numbered["items"]:
            item = {k:original[k] for k in ("id","image_number","legacy_id","sha256","category","split","group_id",
                    "caption","width","height","license","license_url","attribution","source_url","source_page",
                    "source_id","media_type","source_batch","source_manifest_sha256") if k in original}
            if original["path"] != f"images/{item['id']}.jpg":raise ValueError("Unexpected numbered source path")
            raw = within((source/"dataset-numbered/images").resolve(), item["id"]+".jpg")
            if digest(raw) != item["sha256"]:raise ValueError("Source mismatch")
            item["path"] = f"data/{name}/images/{item['id']}.jpg"
            with Image.open(raw) as im:
                preview=ImageOps.contain(ImageOps.exif_transpose(im).convert("RGB"),(1024,1024),Image.Resampling.LANCZOS)
                encoded=io.BytesIO();preview.save(encoded,format="JPEG",quality=90,optimize=True)
                item["preview_size"]=list(preview.size)
            item["preview_sha256"]=hashlib.sha256(encoded.getvalue()).hexdigest()
            destination=output/item["path"];destination.parent.mkdir(parents=True,exist_ok=True)
            if destination.exists():
                if digest(destination)!=item["preview_sha256"]:raise ValueError("Established source preview changed")
            else:destination.write_bytes(encoded.getvalue())
            item["original_sha256"]=item["sha256"]
            if item["legacy_id"] in overrides:
                item["source_attribution"] = item.get("attribution","")
                item["attribution"] = overrides[item["legacy_id"]]["attribution"]
                item["attribution_note"] = overrides[item["legacy_id"]]["note"]
            item["license_url"]=item.get("license_url") or ("https://creativecommons.org/publicdomain/mark/1.0/" if item["license"]=="Public domain" else "https://creativecommons.org/publicdomain/zero/1.0/")
            item["candidates"]=None
            result_path=source/"rounds"/config["id"]/original["legacy_id"]/"result.json"
            if result_path.exists():
                result=json.loads(result_path.read_text())
                if result["source_sha256"]!=item["sha256"] or result["config_sha256"]!=config_sha:
                    raise ValueError("Source or generation identity mismatch")
                item["candidates"]={}
                for variant in ("a","b"):
                    candidate=result[variant]
                    candidate_path=within(source/"rounds",candidate["path"].removeprefix("rounds/"))
                    if not candidate_path.is_relative_to((source/"rounds"/config["id"]).resolve()):
                        raise ValueError("Candidate belongs to another round")
                    with Image.open(candidate_path) as image:
                        if image.size!=(256,256) or image.format!="PNG":raise ValueError("Invalid candidate")
                    relative=f"data/{name}/candidates/{item['id']}_{variant}.png"
                    dest=output/relative
                    if dest.exists() and digest(dest)!=candidate["sha256"]:raise ValueError("Established candidate changed")
                    copy_verified(candidate_path,dest,candidate["sha256"])
                    master=within(source/"rounds",candidate["master_path"].removeprefix("rounds/"))
                    if digest(master)!=candidate["master_sha256"]:raise ValueError("Master hash mismatch")
                    item["candidates"][variant]={k:candidate[k] for k in ("sha256","seed","strength","width","height")}
                    item["candidates"][variant]["path"]=relative
                item["master_hashes"]={v:result[v]["master_sha256"] for v in ("a","b")}
                item["prompt"]=result["prompt"];item["input_transform"]=result["input_transform"]
            records.append(item)
        if [r["id"] for r in records]!=[f"{i:03d}" for i in range(250)]:raise ValueError("Invalid numbering")
        ready=sum(bool(r["candidates"]) for r in records)
        if require_complete and ready!=250:raise ValueError(f"{name}: only {ready}/250 pairs ready")
        public=dict(schema_version=2,collection_id=name,generated_at=datetime.now(timezone.utc).isoformat(),
                    dataset_sha256=numbered["source_manifest_sha256"],
                    numbered_manifest_sha256=digest(source/"dataset-numbered/manifest.json"),
                    round=public_config,total=250,ready=ready,items=records)
        relative=f"data/{name}/manifest.json"
        existing=output/relative
        if existing.exists():
            prior=json.loads(existing.read_text())
            if prior["dataset_sha256"]!=public["dataset_sha256"] or prior["round"]!=public["round"]:
                raise ValueError("Cannot replace an established round")
        write_json(existing,public)
        catalog.append(dict(id=name,label=name.replace("round_","Round ")+" · 新增宠物",
                            count=250,manifest=relative,round_id=config["id"],ready=ready))
        print(json.dumps(dict(round=name,ready_pairs=ready,total=250)),flush=True)
    if len(catalog)!=4:raise ValueError("Expected four collections")
    write_json(output/"data/rounds.json",dict(schema_version=1,rounds=catalog))
    upload=json.loads((output/"upload-config.json").read_text())
    upload["round_ids"]=[r["round_id"] for r in catalog]
    write_json(output/"upload-config.json",upload)

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root",type=Path,required=True)
    p.add_argument("--output",type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument("--require-complete",action="store_true")
    args=p.parse_args();export(args.source_root,args.output,args.require_complete)
