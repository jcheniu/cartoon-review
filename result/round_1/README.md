# Round 1 annotations

完整 25 张标注保存为 JSONL 后，自动提交到本目录下的独立浏览器会话文件夹：

    <session-id>/000_024.jsonl
    <session-id>/450_474.jsonl

新提交的文件必须包含对应范围的完整 25 条记录。未完成组仅保存浏览器和服务端草稿。
实际修改后等待约 30 秒，合并连续修改再提交；重复内容不会创建提交。
旧实现留下的不完整文件保留，补齐后再更新。历史版本保留在 Git 中。
service.json 是旧地址发现文件，仅保留历史记录；当前服务使用 Cloudflare KV，不再更新或提交此文件。
文件名保持 xxx_xxx.jsonl。不同会话可能评价同一图，合并前应处理分歧。
annotation_session_id 是随机浏览器标识，不是经过验证的人员身份。

只有 accepted 为 a、b 或 both 且 split=train 的目标可以考虑用于后续训练；
neither、validation、test 不应进入训练。先核对 dataset_sha256、round_id 和图像哈希。
本目录不预置测试标签；实际结果由操作者提交。
