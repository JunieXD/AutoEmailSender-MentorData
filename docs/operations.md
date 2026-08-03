# 远程仓库启用与日常维护

## 一次性启用

1. 在个人账号下创建公开仓库 `AutoEmailSender-MentorData`，默认分支为 `main`。
2. 推送本仓库；在 Actions 设置中允许 GitHub Actions 读写仓库并创建 Pull Request。
3. 启用仓库 Auto-merge；Pages 的构建来源选择 GitHub Actions。
4. 运行 `Bootstrap repository labels` 工作流创建表单标签。
5. 给 `main` 设置分支保护：要求 `Validate repository` 和 `Check moderation proposals`，禁止强推和删除。
6. 启用私密漏洞报告，并确认 Issue Forms 可用。

`gh-pages` 是自动生成的不可变发布归档，不接受人工编辑，也不应套用要求普通 PR 的 `main` 分支保护。Pages 工作流会从它恢复旧版本、追加新版本，再发布完整静态站点。

## 试运行与自动合并

初始策略 `registry/policy.yml` 中 `auto_merge_enabled: false`。先用 20～50 次真实投稿验证机构名称、域名和纠错体验，期间所有贡献都生成 Draft PR。稳定后将它改为 `true`；此后仍需同时满足账号年龄、普通用户类型、已登记机构、批准来源域名、无身份冲突等条件，才会进入自动合并。

账号年龄阈值由 `minimum_auto_merge_account_age_days` 控制，初始值为 730。

## 新机构

新学校、学院、系所或官方域名始终由维护者确认。审核投稿 PR 时：

1. 在 `registry/organizations.yml` 增加稳定机构 ID、正式名称、别名、父级、官网和批准域名；
2. 把提案的 `accepted.organization_id` 改成该 ID；
3. 保留 `submitted_*` 原值用于审计；
4. 等待校验通过后再合并。

一个机构只需注册一次；以后正式名称或已登记别名会自动归一化。

## 封禁与撤销

运行 `Revoke contributor data` 工作流，先保持 `apply=false` 查看范围，再以数字 GitHub 用户 ID、原因代码和所需封禁范围重新运行并创建 Draft PR。确认独立来源仍然成立、唯一来源导师已撤下后合并。

内部封禁名单能阻止该数字 ID 的新投稿或反馈进入数据集。若还要阻止其继续在个人仓库创建 Issue，需要仓库所有者另外使用 GitHub 的账号屏蔽功能；该账户级操作不能由仓库 `GITHUB_TOKEN` 代替完成。
