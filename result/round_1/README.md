# Round 1 annotations

保存后的标注自动上传到本目录下的独立浏览器会话文件夹：

    <session-id>/000_024.jsonl
    <session-id>/450_474.jsonl

每个文件含对应范围已保存的记录，最多 25 条；后续保存更新同一文件，历史版本保留在 Git 中。
文件名保持 xxx_xxx.jsonl。不同会话可能评价同一图，合并前应处理分歧。
annotation_session_id 是随机浏览器标识，不是经过验证的人员身份。

只有 accepted 为 a、b 或 both 且 split=train 的目标可以考虑用于后续训练；
neither、validation、test 不应进入训练。先核对 dataset_sha256、round_id 和图像哈希。
本目录不预置测试标签；实际结果由操作者提交。
