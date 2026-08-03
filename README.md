# Auto Email Sender 社区导师库

这里汇集高校官网公开的导师职业信息，供 Auto Email Sender 用户直接浏览和导入。每条被接受的信息都会保留 GitHub 贡献者、投稿 Issue 和高校官方来源，便于核对、纠错和撤销。

[浏览社区导师库](https://juniexd.github.io/AutoEmailSender-MentorData/) · [批量贡献导师](https://github.com/JunieXD/AutoEmailSender-MentorData/issues/new?template=batch-contribution.yml) · [反馈错误或过时信息](https://github.com/JunieXD/AutoEmailSender-MentorData/issues/new?template=report-error.yml)

社区库不是一个需要完整下载的超大 Excel。正式数据按学校和学院发布为小型、版本化的 JSON 分片，Auto Email Sender 只下载用户选择的部分。投稿用的 XLSX 或 CSV 附件只会被安全读取，不会原样提交到 Git 仓库。

## 推荐：从 Auto Email Sender 批量贡献

大多数情况下，请一次贡献一个或多个学校、学院的导师，不必逐人填写表单：

1. 在 Auto Email Sender 的“导师管理”中筛选要分享的导师。
2. 选中这些导师，点击右侧的“贡献到社区”。
3. 保存软件生成的 `community-share.xlsx`。
4. 软件会打开 GitHub 的“批量贡献导师”页面。登录 GitHub 后，把刚保存的文件拖入“社区共享包”。
5. 等待 GitHub 显示附件链接，勾选投稿确认并提交 Issue。

一个共享包可以混合任意数量的学校和学院，无需拆成多次投稿。每个文件最大 5 MiB、最多 5,000 行。

投稿后：

- Bot 只在投稿 Issue 中保留一条状态评论；重跑时会更新同一条评论，不会逐步骤追加消息。
- 学校或学院简称、新机构和不同机构下的导师会由维护者统一映射；贡献者不需要预先写得与注册表完全一致。
- 维护者可以修正正式机构名、把个别导师移到其他学院、创建尚不存在的机构，或拒绝单独的数据行。
- 审核通过后会自动落库并发布；发布成功后 Issue 自动关闭。
- 同一导师被多人重复提交时，会优先合并为一个导师实体并保留多位贡献者的独立来源，不会仅因重复邮箱创建第二份导师。

## 只贡献一位导师

如果确实只想补充一位导师，也可以使用 [贡献一位导师表单](https://github.com/JunieXD/AutoEmailSender-MentorData/issues/new?template=contribute-mentor.yml)。请填写导师姓名、公开工作邮箱、任职机构和能支持这些信息的高校官方证据页面。

官方页面可以使用 `https://`，仅支持 HTTP 的高校网站也可以使用 `http://`。没有独立官网的系、办公室或实验室不需要为了投稿虚构一个网址；维护者会复用合适的上级机构官网和批准域名。

## 反馈错误、退休、离职或删除请求

发现数据错误或已经过时时，请提交 [信息反馈](https://github.com/JunieXD/AutoEmailSender-MentorData/issues/new?template=report-error.yml)，并提供新的高校官方证据。可反馈的情况包括：

- 姓名、邮箱、职称、学院或主页错误；
- 导师已经退休、离职、调动或去世；
- 两条记录实际是同一位导师；
- 邮箱被重新分配、变成多人共享邮箱；
- 导师本人或机构提出合理删除请求。

反馈不会立即修改数据。维护者会对照官方证据裁决；真实反馈会更正、标记历史状态或撤下记录，虚假反馈不会生效。软件也不会因为社区状态变化而静默删除用户已经导入的本地导师。

## 可以提交什么

只提交高校、学院、系所、研究院或实验室官网已经公开的职业信息：姓名、公开工作邮箱、职称、任职机构、研究方向、少量代表论文标题、官方个人主页和证据来源页。

不要提交私人邮箱、电话、家庭住址、身份证明、学生信息、完整简历或人物简介，也不要上传个人备注、标签、任务、匹配结果、邮件正文和发送记录。软件生成的社区共享包会排除这些本地数据。

详细边界见 [社区导师数据政策](DATA_POLICY.md)。恶意伪造者可以按 GitHub 数字用户 ID 被禁止继续投稿，其此前贡献也可以统一撤销。

## 归因与许可证

投稿会公开记录贡献者的 GitHub 数字用户 ID、提交时用户名、Issue 链接和官方来源，用于归因与审计。数据使用 [CC BY 4.0](LICENSE-DATA.md)，仓库代码和自动化工具使用 [MIT License](LICENSE)。高校官网及其原始内容仍受各自权利和使用条款约束。

## 在 Auto Email Sender 中使用

打开软件导航栏最右侧的“社区导师库”，选择学校或学院后即可预览和导入。社区数据默认只补全本地空字段；遇到姓名、邮箱或机构冲突时需要用户确认。个人备注、标签、任务和发送记录不会参与社区导入或贡献。

## 维护与开发

维护者操作见 [docs/operations.md](docs/operations.md)，应用集成契约见 [docs/app-integration.md](docs/app-integration.md)，数据结构和后续计划见 [docs/roadmap.md](docs/roadmap.md)。本地开发需要 Python 3.12 和 `uv`：

```bash
uv sync --dev
uv run mentor-data validate
uv run pytest
uv run ruff check .
uv run mentor-data build --output dist
```
