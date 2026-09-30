# Cartoon Review · 卡通图像标注

公开标注网页：https://jcheniu.github.io/cartoon-review/

独立的静态标注工具，包含 500 张编号原图（000–499），以及已生成的 256×256 卡通候选 A/B。无需登录、GPU、数据库或安装依赖。未完成的候选在页面中显示“待生成”。

## 操作方法

1. 打开网页，在图片序号中输入连续 25 张的范围，例如 0-24 或 450-474，点击“应用”。
2. 对照原图查看 A/B。选择**卡通风格，同时主体不失真**的结果；保留主体轮廓、五官、姿态和主要颜色。
3. 选择“仅 A 合格”“仅 B 合格”“两张都合格”或“两张都不合格”。两张合格时选更偏好的结果；都不合格时至少勾选“颜色”或“姿态”，可同时勾选。
4. 修正英语目标描述，必要时填写备注，点击“保存并下一张”。在输入框外可用 1–4 选择，Enter 保存。
5. **当前范围内 25 张全部标完后自动下载 JSONL**，文件名如 000_024.jsonl、450_474.jsonl。浏览器可能要求允许多文件下载；没有收到文件时点击“导出本组 JSONL”。尚未标满也可手动导出，文件只含已保存的记录。
6. 修改已标注结果：将“标注状态”切换到“已标注”或“全部”，重新保存并导出。已完成的组修改后会再次下载；浏览器可能给同名文件添加 (1) 等后缀。

**标注仅保存在当前浏览器的本地存储，不自动上传、不在多人之间共享。** 清除浏览器数据、无痕模式结束或换设备都可能丢失本地标注。保留 JSONL；“备份全部已标注”下载 000_499.jsonl。用“导入 JSONL”在同一数据集与轮次下恢复；文件内图片会覆盖同编号的本地记录。不要同时在多个标签页编辑。

如果要分配多人工作，可分别发送带范围的链接，例如：
https://jcheniu.github.io/cartoon-review/?range=450-474

## 数据内容

- 000–249：Oxford-IIIT Pet 猫狗照片，共 250 张，37 个品种。
- 250–499：CC0 合成动漫脸，共 250 张；由原始合成图裁切得到。
- 固定划分：训练 400、验证 50、测试 50；各类别分别为 200/25/25。
- 原图字节、来源、许可、划分及候选哈希均记录在 data/manifest.json。
- 候选由 SDXL + Canny ControlNet 生成，等比缩放并补白至 256×256。生成参数、模型仓库和版本记录在清单中。
- 实际就绪数量以页面和 data/manifest.json 的 ready 字段为准。仓库是发布快照；刷新页面不会触发生成任务。

代码采用 MIT；**数据不统一适用 MIT**。见 [数据许可](DATA_LICENSE.md) 与 [数据说明](DATASET.md)。

## 本地运行

克隆仓库后在仓库根目录执行：

    python -m http.server 8000

打开 http://localhost:8000/ 。不要直接双击 index.html，浏览器会阻止本地文件的 fetch。
服务器监听范围由运行环境决定；仅本机使用可加 --bind 127.0.0.1。

前端没有第三方运行时依赖；GitHub Pages 从 main 分支根目录发布，.nojekyll 已包含。

## 维护与更新候选

在可以访问教师流水线输出的机器上执行（替换尖括号占位内容）：

    python scripts/export_snapshot.py --source "<teacher-loop-output>" --round "<round-id>"
    python scripts/verify_snapshot.py
    git add data
    git commit -m "Update generated candidate snapshot"
    git push

导出器校验原图、候选和配置哈希，只读取完成的 result.json，将可公开字段写入清单。
它不读取人工标注库，不复制训练底稿、权重、密钥或机器路径。更新同一轮候选不会重置浏览器标注；
发布不同轮次时，浏览器使用新的独立标注存储。旧轮次可在 Git 历史中恢复。

## 收回标注与后续训练

操作者将下载的 JSONL 交给数据负责人；网页没有集中收集接口。
核心字段与教师标注流程一致：accepted 为 a/b/both/neither，preferred 为 a/b/tie/neither，
rejection_reasons 为 color/pose 数组。另含原图 ID、编号、轮次、划分、SHA-256、标注版本与时间。
完整格式见 [JSONL 字段说明](docs/jsonl.md)。

合并时用 dataset_sha256 + round_id + item_id 识别样本，用 annotation_session_id 区分浏览器来源。
不同操作者对同一图片的标注须人工解决分歧，不应直接以“最后一条”为准。
候选路径为本仓库路径，不能直接当作私有流水线中的绝对路径使用；应按 item_id、轮次及哈希关联。
只有训练集中人工通过的目标可进入训练；验证、测试及两张都不合格的记录不得混入。
公开包不含 1024px 训练底稿或训练接口；master_hashes 可用于回连原流水线底稿。
导出的 JSONL 本身不会启动训练，也不包含 LoRA 权重。

## 验证

    npm install
    npm test
    python scripts/verify_snapshot.py
    npx playwright install chromium
    python -m http.server 8000 --bind 127.0.0.1

在另一个终端设置 REVIEW_URL（可选，默认 http://127.0.0.1:8000/）后执行：

    npm run test:browser

维护者：Jing <jcheniu@connect.ust.hk>。界面样式与范围选择逻辑提取自
[Teddytututu/ai-bead-pattern](https://github.com/Teddytututu/ai-bead-pattern)，保留原 MIT 版权声明。
