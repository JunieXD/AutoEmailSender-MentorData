# Auto Email Sender 集成契约

## 当前实现

生产数据入口为 <https://juniexd.github.io/AutoEmailSender-MentorData/>，应用页面为 `/community`。Auto Email Sender 的本地 FastAPI 后端负责网络访问、严格校验、缓存、三方比较和数据库写入；React 前端不直接下载 Pages 数据，也不持有 GitHub Token。

默认数据基地址内置在应用中。开发、镜像或灾备场景可通过 `AUTO_EMAIL_SENDER_COMMUNITY_DATA_BASE_URLS` 配置逗号分隔的多个 HTTPS 基地址；只允许无用户名、密码、查询参数和片段的标准 HTTPS URL。

## 下载

软件本地后端下载 GitHub Pages 静态数据，不把用户引导到浏览器。推荐流程：

1. 获取 `latest.json`；
2. 获取对应版本 `manifest.json` 和 `catalog.json`；
3. 校验 Schema 版本、最低应用版本、字节数和 SHA-256；
4. 按用户选择下载一个或多个学院分片；
5. 缓存在用户本地数据目录；
6. 网络失败时继续使用最后一次验证成功的缓存。

软件应支持多个只读镜像基地址，但所有镜像必须提供相同的 Manifest 与哈希。

缓存位于应用用户数据目录的 `community-mentor-cache`。只有 `latest.json`、Manifest、Catalog 和撤销列表全部验证成功后才原子切换缓存索引；学院分片按需下载并逐个验证。网络失败时只能回退到最后一次完整验证成功的缓存，页面会显示 `stale` 和警告信息。

## 已实现接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/community-mentors/catalog?refresh=false` | 返回目录、缓存来源、版本和已关联导师的生命周期提醒；`refresh=true` 强制联网刷新 |
| `POST` | `/api/community-mentors/records` | 按数据版本和学院分片加载导师，并返回与本地数据的比较结果 |
| `POST` | `/api/community-mentors/preview` | 对选中的稳定导师 ID 重新生成导入预览 |
| `POST` | `/api/community-mentors/import` | 根据逐字段选择新增、更新或仅建立稳定关联 |
| `GET` | `/api/community-mentors/share-package?professor_ids=1,2` | 导出最多 500 位本地导师的安全 XLSX 投稿包 |

`records` 请求体：

```json
{
  "dataset_version": "v2-0752d0c095f7d084c8758f96f4b1a2c3",
  "unit_paths": ["objects/sha256/0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef.json"]
}
```

`preview` 在上述字段外增加最多 2,000 个 `record_ids`。`import` 则增加最多 2,000 个 `items`；每项包含 `community_record_id`、预览时返回的 `comparison_token`、可选的 `field_choices`（值为 `community` 或 `local`）以及身份冲突时必须显式设置的 `confirm_identity_match`。

所有写入都在本地数据库事务中完成。客户端必须使用预览时的数据版本；版本变化、分片越界、生命周期禁止导入、邮箱多重匹配或稳定关联冲突都会拒绝写入。

## 浏览页面

社区导师库页面提供：

- 学校、学院、系所、姓名、邮箱和研究方向搜索；
- 当前状态、最后核验时间、官方来源和贡献者；
- 多邮箱和多任职提示；
- 新增、已存在、可补全、冲突、过时和已撤销分类；
- 单选、批量选择和导入预览。

默认隐藏 `retired`、`departed`、`deceased`、`removed`、`disputed`，允许用户显式查看历史状态。

## 本地关联

应用已经建立 `professor_community_links` 一对一关联表：

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

同一社区 ID 只能关联一位本地导师，一位本地导师也只能关联一个社区 ID。邮箱相同但姓名或学校冲突、同一邮箱匹配多条本地记录、社区 ID 已关联其他本地记录，均进入人工确认或直接阻止导入。

## 导入策略

默认：

- 新导师直接新增；
- 本地空字段可由社区值补全；
- 本地非空字段不覆盖；
- 标签、个人备注、任务、发送记录和匹配结果永远不导入；
- 已归档本地导师不自动恢复；
- 冲突字段逐项显示，由用户决定；
- 远端退休、离职或撤销只提示，不静默删除本地记录。

社区记录完整保留多个联系方式和多个当前任职。软件默认写入唯一主要当前邮箱与唯一主要当前任职；其他联系方式和任职在预览页展示，不丢失社区侧结构。`retired`、`departed`、`deceased`、`disputed`、`removed` 等状态通过撤销列表同步到关联表，只生成提醒，不删除、归档或恢复本地导师。

## 贡献和反馈

“贡献到社区”和“反馈错误”使用系统浏览器打开预填 GitHub Issue Form，以 GitHub Issue 作者作为身份来源。

URL 仅预填较短字段。长研究方向、论文和批量数据通过安全共享包或剪贴板提供，因为浏览器 URL 存在长度限制，浏览器也不允许应用自动附加本地文件。

安全共享包只包含姓名、主邮箱、职称、学校、学院、系所、研究方向、近期论文、官方主页和证据来源十类公开职业字段。导出会拒绝缺少邮箱或官方来源的导师，并拒绝以 `= + - @` 开头的表格公式内容；个人备注、标签、任务、通信数据和本地社区关联不会进入文件。

## 客户端校验边界

- Schema 使用严格未知字段拒绝策略，并核对数据版本、生成时间、记录数、稳定 ID 和主字段投影一致性；
- Manifest 同时校验声明字节数和 SHA-256，且对核心文件、单分片与单次总下载量设置上限；
- 禁止绝对路径、反斜杠、`.`、`..`、跨源 URL、重定向和不属于 Catalog/Manifest 的分片；
- 社区文字只作为 React 文本渲染，不执行 HTML、脚本或 Shell；
- 投稿自动化只解析 GitHub 事件的结构化字段，账号身份来自事件作者，不信任 Issue 正文自报身份。
