# Auto Email Sender 集成契约

## 下载

软件本地后端下载 GitHub Pages 静态数据，不把用户引导到浏览器。推荐流程：

1. 获取 `latest.json`；
2. 获取对应版本 `manifest.json` 和 `catalog.json`；
3. 校验 Schema 版本、最低应用版本、字节数和 SHA-256；
4. 按用户选择下载一个或多个学院分片；
5. 缓存在用户本地数据目录；
6. 网络失败时继续使用最后一次验证成功的缓存。

软件应支持多个只读镜像基地址，但所有镜像必须提供相同的 Manifest 与哈希。

## 浏览页面

社区导师库页面提供：

- 学校、学院、系所、姓名、邮箱和研究方向搜索；
- 当前状态、最后核验时间、官方来源和贡献者；
- 多邮箱和多任职提示；
- 新增、已存在、可补全、冲突、过时和已撤销分类；
- 单选、批量选择和导入预览。

默认隐藏 `retired`、`departed`、`deceased`、`removed`、`disputed`，允许用户显式查看历史状态。

## 本地关联

推荐新建 `professor_community_links` 表：

```text
professor_id
community_record_id
dataset_version
imported_snapshot_json
imported_at
last_checked_at
remote_status
remote_revoked_at
```

已有本地导师第一次匹配时使用规范化邮箱和冲突保护；建立关联后始终以稳定 `community_record_id` 跟踪，即使邮箱变化也不会创建新导师。

## 导入策略

默认：

- 新导师直接新增；
- 本地空字段可由社区值补全；
- 本地非空字段不覆盖；
- 标签、个人备注、任务、发送记录和匹配结果永远不导入；
- 已归档本地导师不自动恢复；
- 冲突字段逐项显示，由用户决定；
- 远端退休、离职或撤销只提示，不静默删除本地记录。

## 贡献和反馈

“贡献到社区”和“反馈错误”使用系统浏览器打开预填 GitHub Issue Form，以 GitHub Issue 作者作为身份来源。

URL 仅预填较短字段。长研究方向、论文和批量数据通过安全共享包或剪贴板提供，因为浏览器 URL 存在长度限制，浏览器也不允许应用自动附加本地文件。

