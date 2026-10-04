# Cartoon Review · 卡通图像标注

[打开公开标注网页](https://jcheniu.github.io/cartoon-review/) ·
[查看已上传结果](https://github.com/jcheniu/cartoon-review/tree/main/result)

四个宠物 round 各 250 张，编号在每轮内为 000–249。每张输入对应 256×256 的 A/B 两张卡通候选。无需登录。round_1 的原有标注、数据身份和图片保持不变；历史漫头数据保留在原清单，通过 ?round=round_1&archive=1 可回看，当前不再生成或训练。

## 操作方法

1. 先选择 round，再输入连续 25 张的序号范围，例如 0-24 或 225-249，点击“应用”。
2. 图片从左到右为 **候选 A → 候选 B → 输入原图**。选择卡通风格，同时保持主体轮廓、五官、姿态和主要颜色不失真。
3. 选择仅 A、仅 B、都合格或都不合格。都合格时选择偏好；都不合格时至少勾选“颜色”或“姿态”。
4. 修正英语目标描述，点击“保存并下一张”。输入框外可用 1–4 选择，Enter 保存。
5. 每次保存先保留浏览器副本并同步服务端草稿。本组不足 25 张时不会创建 Git 提交。
6. 满 25 张后，服务端先持久保存 JSONL；首次完整组立即进入 GitHub 提交队列；已提交组的后续修改合并约 30 秒后再提交。成功后显示“已上传”及文件链接；连接失败自动重试。
7. 本组 25 张全部标完仍会自动下载 JSONL，如 000_024.jsonl。也可随时点“导出本组 JSONL”，下载已保存的部分。
8. 修改结果时切换到“已标注”或“全部”，重新保存即可更新 GitHub 文件。换设备可逐组导入 JSONL。

标注会公开上传到 GitHub。已有的浏览器标注在连接成功后也会自动同步。
不要在描述中填写个人联系方式等无关内容。只在看到“已上传”后，才能确认该版本已写入 GitHub。
清理浏览器前请完成上传或保留逐组 JSONL；离线标注等待重新联网后同步。

多人分工可发送带范围的链接，例如：
https://jcheniu.github.io/cartoon-review/?round=round_2&range=0-24

## GitHub 结果目录

    result/round_1/<浏览器会话 ID>/000_024.jsonl
    result/round_2/<浏览器会话 ID>/000_024.jsonl
    result/round_3/<浏览器会话 ID>/000_024.jsonl
    result/round_4/<浏览器会话 ID>/000_024.jsonl

不同 round 分别保存草稿、上传状态与结果；不同浏览器使用不同会话目录，避免互相覆盖。新提交的每个文件必须包含范围内完整的 25 条记录。
完整组后续修改会更新同一文件，Git 历史保留已提交的版本。连续修改合并后提交一次；
重复上传、仅版本时间变化，以及没有改内容的“保存”不会创建提交。
旧实现已上传的不完整文件保留原样，补齐 25 张后再更新。隧道重连和地址更新只写 Cloudflare KV，不再创建 Git 提交。
浏览器只持有本会话的接收服务密钥；GitHub 凭据仅存在于 SSH 服务端。

JSONL 保留 a/b/both/neither、偏好、颜色／姿态原因、描述、编号、数据划分及图像哈希。
不同操作者的分歧需人工合并。后续训练只可使用训练集中通过的目标。
说明见 [JSONL 字段](docs/jsonl.md)。

## 数据内容

- round_1：250 张 Oxford-IIIT Pet 猫狗照片，保留原固定划分和既有标注。
- round_2–4：新增 750 张宠物来源，猫 200、狗 200、兔 150、宠物鸟 150、其他小宠 50；含实拍与插画，每轮均混排。
- 原清单中的 250–499 为历史 CC0 合成动漫脸；当前暂停，仅作历史回放。
- 原固定划分不变；新增来源作为训练候选，须人工批准生成目标后才能用于 LoRA。
- data/rounds.json 为四轮目录；原轮保留 data/manifest.json，新轮位于 data/round_2、round_3、round_4/manifest.json。
- 新来源保留原始 SHA-256、来源、作者和许可，网页 JPEG 浏览图另有 preview_sha256；高清原文件及生成高清底稿保存在教师归档，不将浏览图误当训练原文件。
- SDXL + Canny ControlNet 生成 A/B，等比缩放并补白至 256×256。
- 未完成候选显示“待生成”；就绪数量以页面的数据快照为准。

代码 MIT；图片适用各自来源许可，见 [数据许可](DATA_LICENSE.md) 和 [数据说明](DATASET.md)。

## 本地运行与维护

静态页面无第三方运行时依赖。在仓库根目录运行：

    python -m http.server 8000 --bind 127.0.0.1

打开 http://localhost:8000/ 。直接双击 index.html 无法读取数据清单。
本地副本仍可保存和导出；自动上传默认仅接受生产站点来源。
固定上传入口为 https://api.asuperstrongfrog.com；网页地址继续使用 GitHub Pages。
各组独立重试；上一组等待保存或提交时，其他组仍可上传。
接收服务及部署说明见 [server/README.md](server/README.md)，固定域名维护见 [edge/README.md](edge/README.md)。

导出新增宠物轮次（保留原轮快照）：

    python scripts/export_pet_rounds.py --source-root "<pet-review-output>" --require-complete
    python scripts/verify_snapshot.py

导出器仅复制完成且哈希一致的候选，不读取私有人工标注。不要修改现有轮次的图片字节；
同一轮新增候选不会重置已有浏览器标注。

main 保存源码、数据和上传结果；GitHub Pages 从 gh-pages 分支发布。
更新页面或候选时，先在服务本机运行目录的 GitHub 写入锁内拉取 main，再提交并将同一提交推送到 main 和 gh-pages。
只有结果上传时更新 main，避免每条标注触发网页重建。

## 验证

    npm install
    npm test
    python scripts/verify_snapshot.py
    python -m unittest discover -s tests -p "test_*.py" -v
    npx playwright install chromium

运行本地 HTTP 服务后执行 npm run test:browser，以及 node tests/rounds-browser.mjs（默认端口 8015，可用 REVIEW_URL 指定）。
浏览器测试模拟上传接收端，不向正式结果目录写入测试标签。

维护者：Jing <jcheniu@connect.ust.hk>。
界面样式与范围逻辑提取自 [Teddytututu/ai-bead-pattern](https://github.com/Teddytututu/ai-bead-pattern)，保留原 MIT 版权声明。
