# JSONL export format

UTF-8; one JSON object per line, with a trailing newline. Rows are sorted by image_number.
The group filename uses three-digit inclusive bounds, e.g. 000_024.jsonl or 450_474.jsonl.
Manual exports can contain fewer than 25 rows. New automatic GitHub commits require all 25 rows, a durable server JSONL snapshot, and changed annotation content. The first complete group is queued immediately; edits to an already published group use a 30-second quiet period. Files use result/round_N/<upload-session>/<start>_<end>.jsonl on main, where N is the selected round (1–4). Earlier partial files remain as historical annotations.

| Field | Meaning |
| --- | --- |
| schema_version | 1; extra public-review metadata supplements the teacher review schema |
| dataset_sha256 | Original fixed dataset manifest SHA-256 |
| numbered_manifest_sha256 | Numbered input manifest SHA-256 |
| round_id | Generation round |
| annotation_session_id | Random browser-local identifier; not an authenticated user identity |
| item_id | Legacy source identifier used by the original teacher pipeline |
| photo_id / image_number | Three-digit string / integer 0–499 |
| category / split / group_id | Source metadata; fixed across annotations |
| accepted | a, b, both, neither |
| preferred | a, b, tie, neither; must agree with accepted |
| rejection_reasons | color and/or pose when accepted=neither; otherwise empty |
| caption | Operator-edited target description |
| review_version / reviewed_at | Per-browser revision counter and ISO timestamp |
| source_sha256 / source | Exact input identity, public relative path and license |
| candidate_hashes | SHA-256 of the reviewed A and B PNG files |
| master_hashes | SHA-256 of the private high-resolution training masters; no master images included |
| candidates | Public relative image paths, SHA-256, seed, strength and output dimensions |

Imports validate dataset, round and image hashes before changing local storage.
All rows are validated first; one invalid row rejects the whole import.
Imports intentionally replace local records for the images present in the file and retain others.
JSONL imports are accepted from this public tool; older private exports without an annotation_session_id
need a deliberate conversion before import.

Review versions are not globally ordered between annotators. Resolve competing annotations explicitly.
Hash metadata allows the data owner to link public judgments to private masters; it is not a digital signature
and does not authenticate a reviewer. Nothing in this repository runs training automatically.

The removed notes field is not included in new exports or server uploads. Fixed split metadata is retained even though the UI no longer filters by split. The upload-session directory isolates browser owners; imported records may retain their original annotation_session_id.\n
新增 round_2–4 的网页原图为缩小浏览副本：顶层 source_sha256 始终指向高清原文件；source.sha256 校验 source.path 对应的浏览副本，source.original_sha256 再次记录高清原文件哈希。旧轮次 source 字段保持原格式不变。必须通过 round_id 与 dataset_sha256 共同定位数据，不能只按 000 等编号合并不同轮次。
