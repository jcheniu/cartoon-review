# Data licenses and attribution

The root MIT LICENSE covers software and original project documentation. It does **not** relicense the bundled images.

## Round 1 real animal photographs: 000–249

Source: [Oxford-IIIT Pet Dataset](https://www.robots.ox.ac.uk/~vgg/data/pets/).

Attribution: Omkar M. Parkhi, Andrea Vedaldi, Andrew Zisserman and C. V. Jawahar,
Oxford-IIIT Pet Dataset, “Cats and Dogs,” CVPR 2012.
Image copyright remains with the original image owners, as stated by the source.

The dataset publisher makes the dataset available under
[Creative Commons Attribution-ShareAlike 4.0 International](https://creativecommons.org/licenses/by-sa/4.0/).

Each bundled photograph is an unchanged copy selected from the source archive, with a new numeric filename.
The manifest preserves its original source ID, source URL, attribution and SHA-256.
Retain the attribution and license link when redistributing.
Generated cartoon candidates based on these photographs are identified as modifications; to the extent
they constitute copyrightable adaptations, those adaptations are distributed under CC BY-SA 4.0.
No endorsement by the original authors or image owners is implied.

## Round 1 archived synthetic anime faces: 250–499

Source: [alfredplpl/anime-with-caption-cc0](https://huggingface.co/datasets/alfredplpl/anime-with-caption-cc0),
revision 13c9c9a1df5cf927962b575e51b110c4fa113d5c.
Publisher: alfredplpl. The source identifies the images as generated using Emi 2.

The source declares [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
This project crops the generated source images to face-centered square regions.
Crop coordinates, source row, original image hash and publisher URL are recorded per item.
Any rights held by this project in its anime crops and anime-derived candidate images are dedicated under CC0 1.0.

## Generated candidates

Candidates are unreviewed SDXL/ControlNet outputs, not verified annotations.
Their source photograph or illustration is identified by its round and numeric ID in the corresponding manifest listed in data/rounds.json.
Generation changes appearance, outlines and shading, and may also introduce unwanted changes.
The exact model repositories, revisions and generation parameters are in the manifest.

Model repositories:
- [SDXL base 1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0)
- [Canny ControlNet for SDXL](https://huggingface.co/diffusers/controlnet-canny-sdxl-1.0)

Model weights are not redistributed here. The data license statements report the source publishers'
terms and this project's own contributions; they do not grant rights that third parties may hold.

## 新增宠物 round_2–4

新增来源来自 Wikimedia Commons，逐图保留作者、来源页和许可链接，包括 CC BY、CC BY-SA、CC0 与公有领域作品。网页 JPEG 是原图的等比缩小浏览副本，作者及许可列于对应 round 清单和标注页。原生文件 SHA-256 与浏览副本 preview_sha256 分开记录。不要将仓库代码的 MIT 许可套用于图片。
