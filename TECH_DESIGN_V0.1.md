# 股票研究 Agent 平台：第一版技术设计

**版本：** v0.1

**对应需求：** [PRD_V0.1.md](./PRD_V0.1.md)

**设计目标：** 用最小的系统实现一条完整、可恢复、可验证的港股研究路径。

## 1. 第一版垂直切片

第一版只保证一条端到端流程：

```text
用户输入港股公司和研究问题
  → 创建研究项目
  → 生成研究计划
  → 获取有限的公开资料
  → 抽取事实和证据
  → 计算基础指标和估值
  → 审计结果
  → 生成研究报告
  → 支持报告追问和用户反馈
```

建议先使用 1～3 家港股公司验证完整链路，再扩展到 10～20 家。

## 2. 系统边界

### 2.1 系统内

- 研究项目和任务管理；
- 用户聊天和任务启动；
- 研究计划；
- 财报/公告文档获取；
- 文档解析和证据切片；
- 财务事实抽取；
- 指标和估值计算；
- 报告生成；
- 证据查看；
- 任务重试和恢复；
- 用户偏好记录。

### 2.2 系统外

- 交易执行；
- 实时行情交易终端；
- 个性化买卖建议；
- 全市场数据授权；
- 自动修改代码或金融公式。

## 3. 逻辑架构

```text
Web UI
  ↓
Application API
  ├── Project Service
  ├── Research Task Service
  ├── Report Service
  └── Feedback Service
  ↓
Research Orchestrator
  ├── Request Router
  ├── Context Builder
  ├── Planner
  ├── Research Workers
  ├── Reviewer
  └── Report Compiler
  ↓
Domain Services
  ├── Document Service
  ├── Evidence Service
  ├── Financial Calculator
  ├── Data Source Connectors
  └── User Preference Service
  ↓
Persistence
  ├── Relational Database
  ├── Raw Document/Object Storage
  ├── Evidence/Full-text Index
  └── Task Event Log
```

## 4. Agent 边界

第一版不做大量自由协作的 Agent，而采用固定流程和少量职责明确的模块。

### 4.1 Request Router

判断输入属于：

- 快速事实问答；
- 当前报告追问；
- 深度研究任务；
- 报告更新；
- 情景分析。

### 4.2 Context Builder

在每次模型请求前构建上下文：

```text
基础研究规则
+ 当前任务
+ 用户已确认偏好
+ 公司研究历史
+ 当前截止日期
+ 已验证事实和证据
```

上下文必须记录版本，便于重放。

### 4.3 Planner

将研究问题转化为有限的结构化子任务。Planner 只能从允许的任务类型中选择，不直接创建未知工具或未知流程。

### 4.4 Research Workers

执行有限职责：

- `business_analysis`：业务和收入结构；
- `financial_analysis`：财务趋势和现金流；
- `industry_analysis`：行业和竞争；
- `risk_analysis`：风险和反方证据；
- `valuation_analysis`：解释计算结果和估值假设。

Worker 输出结构化事实、观点、证据和不确定性，不直接写最终报告。

### 4.5 Reviewer

检查：

- 关键数字是否有来源；
- 计算是否可复算；
- 日期是否符合截止日期；
- 章节之间是否存在数字冲突；
- 结论是否有反方证据；
- 是否存在无证据的确定性表述。

### 4.6 Report Compiler

将审核通过的结构化产物渲染为报告。报告生成失败不能丢失前面的事实、证据和计算结果。

## 5. 任务状态机

```text
created
  → planned
  → collecting_data
  → extracting_facts
  → analyzing
  → calculating
  → reviewing
  → completed
```

任意运行状态允许转为：

```text
paused / canceled / failed
```

恢复时从最后一个成功 checkpoint 继续。

每个步骤需要记录：

- 输入；
- 输出；
- 使用的模型和 Prompt 版本；
- 数据源和参数；
- 开始/结束时间；
- 错误信息；
- 重试次数；
- 产物 ID。

## 6. 核心数据对象

### 6.1 ResearchProject

长期研究容器，对应用户正在跟踪的一家公司。

```text
id
user_id
company_id
symbol
name
market
status
created_at
updated_at
```

### 6.2 ResearchRun

一次具体研究或更新任务。

```text
id
project_id
question
as_of_date
research_type
plan_version
status
model_version
started_at
completed_at
```

### 6.3 ResearchStep

任务中的一个可恢复步骤。

```text
id
run_id
step_type
status
attempt
input_artifact_id
output_artifact_id
checkpoint_at
error
```

### 6.4 Document

原始财报、公告或其他资料。

```text
id
company_id
title
document_type
source_url
published_at
period_start
period_end
language
content_hash
storage_uri
```

### 6.5 Evidence

可被报告引用的原始证据片段。

```text
id
document_id
text
page
section
published_at
content_hash
```

### 6.6 Fact

从证据中抽取的结构化事实。

```text
id
company_id
metric
value
currency
unit
period_start
period_end
published_at
accounting_basis
evidence_id
confidence
```

### 6.7 Calculation

由代码产生的可复算计算结果。

```text
id
run_id
calculation_type
inputs
formula_version
outputs
created_at
```

### 6.8 Claim

报告中的一个可审计判断。

```text
id
report_id
text
claim_type
evidence_ids
calculation_ids
counter_evidence_ids
confidence
```

### 6.9 UserPreference

用户明确确认或待确认的研究偏好。

```text
id
user_id
key
value
source
confidence
status
version
```

## 7. 数据和证据原则

数据源优先级：

```text
HKEXnews / 公司 IR
  > 授权结构化数据源
  > 可靠财经新闻
  > 其他公开网页
```

关键数字不能只保存最终值，必须保留：

- 数据期间；
- 发布时间；
- 币种和单位；
- 会计口径；
- 原始文件和位置；
- 抽取置信度。

所有研究请求都必须携带 `as_of_date`。检索层和报告层都要拒绝使用发布时间晚于截止日期的资料。

## 8. 交互和 API 草案

### 创建研究项目

```http
POST /api/projects
```

```json
{
  "company": "腾讯",
  "question": "截至 2025 年底，是否适合长期持有？",
  "as_of_date": "2025-12-31"
}
```

### 查询任务状态

```http
GET /api/research-runs/{run_id}
```

### 发送追问

```http
POST /api/research-runs/{run_id}/messages
```

```json
{
  "content": "如果云业务增速下降到 15%，估值会怎样？"
}
```

### 更新报告

```http
POST /api/research-runs/{run_id}/refresh
```

### 提交反馈

```http
POST /api/feedback
```

```json
{
  "run_id": "run_001",
  "type": "preference",
  "content": "以后多分析现金流，少讲市场空间"
}
```

## 9. 长任务执行策略

第一版可以使用一个持久化任务队列和 Worker，不需要完整引入通用 Harness。

要求：

- 每个步骤独立提交和确认；
- 结果写入数据库后才标记完成；
- 重试使用幂等键；
- 原始文档按内容哈希去重；
- 已完成步骤不重复调用模型；
- 用户取消任务时停止未开始步骤；
- 服务重启后扫描未完成任务并恢复。

## 10. 自适应和长期记忆

第一版只做上下文级的个性化：

```text
用户明确反馈
  → 提取偏好
  → 保存偏好版本
  → 下次 Context Builder 注入
  → Planner 调整研究计划
```

不允许用户反馈直接修改：

- 财务公式；
- 数据校验规则；
- 系统安全策略；
- Agent 代码。

## 11. 评测和可观测性

每次运行需要记录：

- 任务状态变化；
- 模型请求和版本；
- 工具调用和耗时；
- 数据源返回状态；
- 事实和证据数量；
- 审计发现的问题；
- Token 和成本（如果供应商提供）。

第一版最低评测集：

- 10 个财报事实问题；
- 10 个财务计算问题；
- 5 个引用支持问题；
- 5 个截止日期/时间泄漏问题；
- 3 个任务中断恢复测试。

## 12. 实现建议

技术栈先保持可替换：

- 前端：普通 Web 应用；
- API：TypeScript 或 Python；
- 编排：轻量状态机 + 持久化队列；
- 数据库：PostgreSQL；
- 文档存储：对象存储或本地文件系统；
- 检索：先使用全文检索，向量检索作为补充；
- 模型：通过统一 LLM Adapter 接入不同模型；
- 计算：Python/TypeScript 的纯函数财务计算模块。

不在第一版绑定某一个模型、某一个数据供应商或某一个 Agent 框架。

## 13. 技术验收标准

完成一家港股公司的研究任务后，系统必须能够：

1. 从自然语言创建任务；
2. 生成并保存研究计划；
3. 读取至少一份官方披露文件；
4. 产生带原文位置的结构化事实；
5. 完成至少三项财务指标计算；
6. 完成至少一种估值计算；
7. 生成带引用和反方证据的报告；
8. 对报告进行追问；
9. 模拟失败后从 checkpoint 恢复；
10. 保存用户反馈并在下一次研究中生效。

