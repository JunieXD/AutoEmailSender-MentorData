# 数据模型

## 导师 Mentor

导师实体代表自然人。核心字段：

- `id`：不透明稳定 ID，不从邮箱、姓名或机构名称派生。
- `names`：一个或多个姓名表示，包含当前主要显示名和别名。
- `contacts`：邮箱及其状态、来源、有效时间和主次关系。
- `affiliations`：一个或多个任职关系，支持双聘、离职和历史任职。
- `profiles`：一个或多个高校官网导师详情页。
- `status`：导师整体生命周期状态。
- `title`、`research_directions`、`recent_papers`：当前规范投影字段。
- `claim_ids`：支持当前实体的有效贡献声明。
- `field_provenance`：当前每个字段由哪些 Claim 或已审核 Resolution 支持。

导师整体退休时使用 `status=retired`；只离开某学院但仍在其他学校任职时，应结束对应 affiliation，而不是把整个导师标记为离职。

## 联系方式 Contact

```json
{
  "type": "email",
  "value": "mentor@example.edu",
  "normalized_value": "mentor@example.edu",
  "status": "current",
  "is_primary": true,
  "affiliation_id": "aff_...",
  "source_url": "https://example.edu/faculty/mentor",
  "observed_at": "2026-08-03T00:00:00Z"
}
```

`shared`、`generic` 和 `reassigned` 邮箱不得作为默认导入邮箱或自动实体匹配键。

## 任职关系 Affiliation

```json
{
  "id": "aff_...",
  "organization_id": "org_...",
  "status": "current",
  "is_primary": true,
  "title": "教授",
  "started_at": null,
  "ended_at": null,
  "source_url": "https://example.edu/faculty/mentor",
  "observed_at": "2026-08-03T00:00:00Z"
}
```

一位导师可以有多个 `current` 任职，但只能有一个主要任职。如果只有一个当前任职，构建器会要求它为主要任职。

## 机构 Organization

机构采用树形结构：

```text
university
  -> school / institute
    -> department / center / laboratory
```

核心字段：

- 稳定 `id`
- `type`
- `canonical_name`
- `parent_id`
- `aliases`
- `official_urls`
- `approved_domains`
- `status`
- `successor_id`

同名学院只有在 `parent_id` 相同的情况下才能比较别名。显示名修改不改变机构 ID。

## 贡献声明 Claim

Claim 记录“谁在什么时候依据什么页面提交了什么”，并把原始值和最终采用值分离。封禁用户时以数字 `github_user_id` 查找并撤销全部 Claim。

同一用户重复提交相同导师不会产生额外独立支持；不同用户的相同 Claim 可以共同支持同一字段。

## 纠错裁决 Resolution

错误反馈 Issue 只有在维护者裁决后才生成 Resolution。裁决可以是：

- `accepted`
- `partially_accepted`
- `rejected`
- `needs_evidence`
- `duplicate`

Resolution 保存修改前快照、建议值、实际采用值、裁决者数字 ID、证据和原因。结构化补丁可以修改姓名、多邮箱、多任职、主页、学术字段和生命周期；新值以 Resolution 作为独立字段来源。普通信息更新不把原投稿者视为恶意；确认伪造时才撤销其 Claim。

## 发布投影

供软件使用的导师记录是规范实体的受限投影：

- 主要当前邮箱
- 主要当前任职
- 安全的公开学术字段
- 社区记录 ID、状态、贡献者和来源

原始 Claim、审核备注、历史无效邮箱和未解决冲突不进入默认学院分片。
