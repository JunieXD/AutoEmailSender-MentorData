# AutoEmailSender MentorData

Auto Email Sender 的公共导师数据仓库。它使用 GitHub Issue 记录投稿者身份，使用可审计的贡献声明保存来源，使用 GitHub Actions 校验和审核数据，并通过 GitHub Pages 发布供软件直接读取的版本化 JSON 分片。

本仓库不把邮箱、姓名或学校名称当作永久主键：导师和机构均使用稳定 ID。邮箱、主页、任职关系可以有多个，也可以被标记为历史、共享、退休、离职或撤销。

## 设计目标

- 普通用户只需填写 GitHub 表单，不需要学习 Git 或 Pull Request。
- 每个公开字段都能追溯到 GitHub 数字用户 ID、Issue、官方来源和观察时间。
- 相同导师的重复投稿合并为一位导师和多条独立贡献声明。
- 未知机构、身份冲突和错误反馈必须人工处理。
- 满足账号年龄策略的无冲突投稿可以自动合并。
- 被封禁用户的贡献可批量撤销，并从其余独立声明重建公共数据。
- 软件按学校/学院下载小型 JSON 分片，不下载超大 XLSX。

## 仓库布局

```text
registry/organizations.yml        学校、学院、系所等稳定机构实体
records/mentors/*.json            当前规范导师实体
claims/<github-id>/*.json         已接受的原始贡献声明
reports/resolutions/*.json        已处理的纠错裁决
proposals/                        待维护者编辑和裁决的投稿提案
reviews/pending/                  按机构组合生成的批量审核清单
reviews/resolutions/              已应用的机构审核记录
schemas/*.schema.json             数据契约
mentor_data/                       校验、归一化、合并和发布工具
site/                              GitHub Pages 静态页面源文件
dist/                              本地生成的发布目录（不提交）
```

## 本地开发

需要 Python 3.12 和 `uv`：

```bash
uv sync --dev
uv run mentor-data validate
uv run mentor-data build --output dist
uv run pytest
uv run ruff check .
```

构建结果包括：

```text
dist/latest.json
dist/license.json
dist/datasets/<version>/catalog.json
dist/datasets/<version>/manifest.json
dist/datasets/<version>/data/<university-id>/<unit-id>.json
dist/datasets/<version>/revocations.json
```

## 当前阶段

GitHub 远程仓库、Actions、Pages 发布、批量机构审核助手和 Auto Email Sender 应用集成已经完成。生产目录当前以零记录空库安全上线，下一步是保持前 20～50 次真实投稿人工审核，根据试运行结果调整规则，再决定是否启用 730 天账号年龄自动合并。详细进度见 [docs/roadmap.md](docs/roadmap.md)，应用接口见 [docs/app-integration.md](docs/app-integration.md)，远程设置见 [docs/operations.md](docs/operations.md)。

## 许可证与数据边界

仓库程序代码、校验工具和工作流使用 [MIT License](LICENSE)。机构注册表、导师实体、贡献声明、审核与纠错记录、撤销记录及生成的公开数据集使用 [CC BY 4.0](LICENSE-DATA.md)。

社区数据的职业信息范围、投稿归因、第三方来源边界、更正和删除政策见 [DATA_POLICY.md](DATA_POLICY.md)。每次 Pages 构建还会在根目录发布机器可读的 `license.json`。
