# Dataset card

## Purpose

A small, fixed pool for human comparison of photo/illustration-to-cartoon candidates.
The target is a simple cartoon that preserves subject identity, major colors, facial features and pose.
This is a review dataset, not a benchmark with established ground truth.

## Composition

| Number range | Count | Content | Source license |
| --- | ---: | --- | --- |
| 000–249 | 250 | Real cat and dog photos, 37 breeds | CC BY-SA 4.0 |
| 250–499 | 250 | Face-centered crops of AI-generated anime images | CC0 1.0 |

There are 400 training, 50 validation and 50 test inputs, balanced by category.
Splits are fixed in the manifest. Similar-image grouping was used before assigning splits;
this does not establish complete subject or identity independence.

## Processing and provenance

Animal originals retain their source bytes. Anime inputs are crops of 1024px generated images,
typically 512–1024px square, and retain the crop coordinates and source hashes in metadata.
The initial numbered-source manifest hash is
1e46f404f902720171176ca29f794725cfa9774589e384b3786933a0b790f970.
The underlying dataset manifest hash is
c2c61f0a4bc7ec18cd8f99a86c8469541c4f57ed19916a7b451e00c0ca489db6.

Teacher inputs use proportional resizing with white padding. Published A/B candidates are
256×256 PNGs, proportionally downsampled from teacher outputs.
They are **not** hand-drawn or quality-approved references.
Incomplete pairs are represented by a null candidates field; no placeholder is counted as a generated output.

## Limitations

- Real-animal coverage is cats and dogs only, not a broad wildlife dataset.
- The synthetic anime subset largely depicts female characters and has style and demographic imbalances.
- Some animal photos include cluttered backgrounds, human hands or watermarks.
- Automatic face cropping and captions may be imperfect; reviewers should correct target descriptions.
- Strong cartoonization may change colors, pose or identity. Review both candidates against the input.
- This dataset is small and does not establish generalization to arbitrary mobile camera inputs.
- Browser annotations are individual judgments; disagreement resolution and training quality checks remain necessary.

## Public snapshot boundaries

The package includes 500 selected original inputs, available 256px A/B pairs and public provenance.
It excludes private human labels, review history, training masters, adapters, model caches and connection metadata.
Do not commit operator JSONL files without their explicit authorization.
