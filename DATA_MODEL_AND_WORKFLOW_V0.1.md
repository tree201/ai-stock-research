# 股票研究 Agent：数据模型与第一条执行流程

**版本：** v0.1

**对应文档：** [PRD_V0.1.md](./PRD_V0.1.md)、[TECH_DESIGN_V0.1.md](./TECH_DESIGN_V0.1.md)

## 1. 设计目标

第一版只解决一件事：

> 对一家港股公司执行一次可恢复、带证据、可复算的研究任务。

暂时不加入组合管理、交易执行、实时行情和复杂协作。

## 2. 持久化分层

```text
研究业务数据：项目、运行、步骤、报告、偏好
证据数据：文档、文档片段、事实、结论
计算数据：指标、估值输入、计算结果
运行数据：事件、模型调用、工具调用、错误
```

原则：

- 原始文档不可变；
- 抽取结果可重新生成；
- 计算结果可根据公式版本重算；
- 报告是某次运行的产物，不覆盖历史版本；
- 运行事件可以重放整个任务。

## 3. 第一版数据库表

推荐使用 PostgreSQL。文本和结构化数据先放在同一数据库中，向量检索暂时不是必需依赖。

### 3.1 `users`

第一版可以使用固定开发用户，但保留用户边界。

```text
id              uuid primary key
created_at      timestamptz
```

### 3.2 `companies`

```text
id              uuid primary key
symbol          text not null
market          text not null       -- HK
name_zh         text
name_en         text
status          text                -- active/suspended/delisted
metadata        jsonb
created_at      timestamptz
updated_at      timestamptz
unique(market, symbol)
```

### 3.3 `research_projects`

一个项目代表用户长期跟踪的一家公司。

```text
id              uuid primary key
user_id         uuid references users(id)
company_id      uuid references companies(id)
title           text
status          text                -- active/archived
created_at      timestamptz
updated_at      timestamptz
```

### 3.4 `research_runs`

一次具体的首次研究、追问或增量更新。

```text
id              uuid primary key
project_id      uuid references research_projects(id)
question        text not null
run_type        text not null        -- initial/followup/refresh/scenario
as_of_date      date not null
status          text not null
plan_version    integer not null
model_version   text
created_at      timestamptz
started_at      timestamptz
completed_at    timestamptz
```

### 3.5 `research_steps`

任务可恢复的最小单位。

```text
id              uuid primary key
run_id          uuid references research_runs(id)
step_key        text not null       -- resolve_company, collect_docs...
step_order      integer not null
status          text not null
attempt          integer not null default 0
input_json      jsonb
output_json     jsonb
error_json      jsonb
started_at      timestamptz
completed_at    timestamptz
unique(run_id, step_key)
```

### 3.6 `documents`

原始文件按内容哈希去重。

```text
id              uuid primary key
company_id      uuid references companies(id)
source_type     text not null       -- hkexnews/company_ir/news
source_url      text not null
title           text
document_type   text                -- annual_report/results/announcement
published_at    timestamptz
period_start    date
period_end      date
language        text
content_hash    text not null
storage_uri     text not null
parse_status    text
metadata        jsonb
created_at      timestamptz
unique(content_hash)
```

### 3.7 `evidence_chunks`

可以被引用的原文片段。

```text
id              uuid primary key
document_id     uuid references documents(id)
chunk_index     integer not null
text            text not null
page            integer
section         text
published_at    timestamptz
metadata        jsonb
unique(document_id, chunk_index)
```

### 3.8 `facts`

从证据抽取的结构化事实。

```text
id              uuid primary key
company_id      uuid references companies(id)
metric          text not null
value_numeric   numeric
value_text      text
currency        text
unit            text
period_start    date
period_end      date
published_at    timestamptz
accounting_basis text
evidence_id     uuid references evidence_chunks(id)
confidence      numeric
status          text                -- candidate/verified/rejected
created_at      timestamptz
```

### 3.9 `calculations`

```text
id              uuid primary key
run_id          uuid references research_runs(id)
calculation_type text not null       -- cagr/dcf/ratio
formula_version text not null
inputs_json     jsonb not null
outputs_json    jsonb not null
created_at      timestamptz
```

### 3.10 `claims`

报告中的可审计结论。

```text
id              uuid primary key
run_id          uuid references research_runs(id)
claim_type      text not null        -- fact/inference/risk/conclusion
text            text not null
evidence_ids    jsonb not null
calculation_ids jsonb
counter_evidence_ids jsonb
confidence      numeric
review_status   text                 -- pending/passed/needs_review/failed
```

### 3.11 `reports`

```text
id              uuid primary key
project_id      uuid references research_projects(id)
run_id          uuid references research_runs(id)
version         integer not null
status          text not null        -- draft/final/superseded
content_json    jsonb not null
rendered_uri    text
created_at      timestamptz
unique(project_id, version)
```

### 3.12 `user_preferences`

```text
id              uuid primary key
user_id         uuid references users(id)
key             text not null
value_json      jsonb not null
source          text not null        -- explicit/inferred
confidence      numeric
status          text not null        -- candidate/active/revoked
version         integer not null
created_at      timestamptz
updated_at      timestamptz
```

### 3.13 `run_events`

研究运行的追加式事件记录。

```text
seq             bigserial primary key
run_id          uuid references research_runs(id)
event_type      text not null
payload_json    jsonb not null
created_at      timestamptz
```

事件类型先控制在：

```text
run/created
run/planned
step/started
step/completed
step/failed
document/fetched
fact/extracted
calculation/completed
claim/reviewed
report/published
feedback/received
preference/updated
```

## 4. 第一条研究任务流程

### Step 0：创建项目和运行

用户输入自然语言：

> 截至 2025 年底，研究腾讯是否适合长期持有，重点关注云业务、现金流和估值。

系统：

1. 解析公司和证券；
2. 创建或复用 `research_project`；
3. 创建 `research_run`；
4. 写入 `run/created` 事件。

### Step 1：生成研究计划

Context Builder 加载：

- 基础研究模板；
- 用户已生效偏好；
- 公司历史报告和未完成假设；
- 本次截止日期和重点。

Planner 输出有限的步骤：

```json
[
  {"key": "collect_filings", "purpose": "获取年报和业绩公告"},
  {"key": "extract_financials", "purpose": "抽取收入、利润、现金流"},
  {"key": "analyze_business", "purpose": "分析云业务和收入结构"},
  {"key": "analyze_risks", "purpose": "寻找风险和反方证据"},
  {"key": "calculate_valuation", "purpose": "计算三情景估值"},
  {"key": "review", "purpose": "检查数字、引用和截止日期"},
  {"key": "compile_report", "purpose": "生成最终报告"}
]
```

计划保存为运行的 `plan_version`。

### Step 2：获取文档

数据连接器按来源优先级获取：

```text
HKEXnews / 公司 IR
  > 授权结构化数据
  > 可靠新闻
```

每个文档先按 URL 和内容哈希去重，再保存原始文件。

只接受 `published_at <= as_of_date` 的文档进入本次研究。

### Step 3：解析和切片

文档服务完成：

- PDF/HTML 解析；
- 表格识别；
- 文本切片；
- 页码和章节保留；
- 中英文元数据关联。

解析产物写入 `evidence_chunks`，原始文档不修改。

### Step 4：抽取事实

Financial Worker 从证据中抽取指定指标，写入 `facts`。

低置信度或来源冲突的事实状态为 `candidate`，不得直接用于最终结论；通过规则或 Reviewer 后才改为 `verified`。

### Step 5：执行计算

代码计算：

- 收入 CAGR；
- 毛利率和净利率；
- 经营现金流率和自由现金流率；
- 净现金/净负债；
- 至少一种估值模型。

所有输入、公式版本和输出写入 `calculations`。

### Step 6：生成结构化结论

各分析 Worker 输出 `claims`，每条 Claim 必须至少关联一个证据或计算结果。

结论分为：

- `fact`：直接事实；
- `inference`：基于事实的推断；
- `risk`：风险判断；
- `conclusion`：综合结论。

### Step 7：审计

Reviewer 检查：

- 是否存在截止日期之后的证据；
- 关键数字是否来自 `verified` Fact；
- 计算结果是否能复算；
- 报告数字是否前后一致；
- 关键结论是否有证据；
- 是否包含反方证据；
- 是否需要人工复核。

审计不通过时，只重跑失败的步骤，不重做整个任务。

### Step 8：生成报告

Report Compiler 根据通过审计的 Facts、Calculations 和 Claims 生成报告 JSON，再渲染为页面。

报告保留 `run_id`、`plan_version`、数据截止日期和模型版本。

## 5. 快速问答和深度研究的分流

```text
“腾讯 2025 年收入是多少？”
  → 直接查询 verified Fact

“为什么腾讯利润增长比收入快？”
  → 基于已有事实和证据做短推理

“研究腾讯是否适合长期持有？”
  → 创建 Research Run

“更新上次腾讯研究。”
  → 创建 refresh Run，复用历史产物并只获取新增资料

“如果云业务增速降到 15%？”
  → 读取已有 Calculation，创建 scenario Run
```

## 6. 第一版的错误恢复

### 数据源失败

- 重试有限次数；
- 切换备用源；
- 记录失败原因；
- 如果无法取得关键资料，任务进入 `needs_review`，不生成确定性结论。

### 模型输出格式错误

- 使用 Schema 校验；
- 让同一步模型修正一次；
- 必要时切换备用模型；
- 仍失败则标记步骤失败。

### 服务重启

- 从数据库读取未完成的 `research_steps`；
- 已完成步骤不重复执行；
- 从最近 checkpoint 继续。

### 报告生成失败

- 保留 Facts、Evidence、Calculations 和 Claims；
- 只重试 Report Compiler；
- 用户可以查看中间结果。

## 7. 第一版实现顺序

1. 建立 `companies`、`research_projects`、`research_runs`、`research_steps` 表；
2. 实现任务状态和事件记录；
3. 接入一个公开结构化行情/财务源；
4. 接入一份 HKEX/company IR 文档；
5. 实现 PDF/HTML 解析和证据片段；
6. 实现 3～5 个财务指标计算；
7. 实现一个带引用的基础报告；
8. 增加 Reviewer 和失败恢复；
9. 增加用户偏好反馈和报告追问。

