# AI Stock Research Agent

一个面向个人投资者的、可验证和可持续更新的港股研究 Agent。

## 当前状态

目前完成了第一版产品和技术设计，并实现了不依赖外部服务的研究任务领域骨架：

- 研究项目和研究运行；
- 研究步骤状态机；
- 追加式运行事件；
- 失败记录；
- 可替换的行情数据源接口，以及 Yahoo Finance 日线原型适配器；
- 保留文档行号和内容哈希的证据切片器；
- 受限域名和大小的 HTTPS 文档下载器；
- HTML/纯文本可见内容解析；
- 基于系统 `pdftotext` 的 PDF 文本和页码解析器；
- 保守的中英文财务指标事实抽取器（收入、净利润、经营现金流、现金、负债、资本开支）；
- 可复算的 CAGR、利润率、自由现金流、净现金和三情景 DCF 计算；
- 可运行的本地单公司研究流水线和 Markdown/JSON 报告输出；
- 可替换的 LLM Adapter：OpenAI-compatible Provider（本地规则 Provider 仅用于自动化测试）；
- SQLite 持久化研究项目、运行、步骤、事件、原文、证据、事实、计算、模型结论和报告版本；
- Web MVP 的历史研究 API，可在服务重启后恢复运行详情和报告；
- 按公司聚合的研究会话、会话事件和聊天消息；
- 基础任务流程测试。
- 可选的 Redis/RQ 后台研究任务队列；任务状态和失败信息持久化在 SQLite，聊天页面会自动轮询并恢复未完成任务。
- 可配置的公开资料 URL 采集：HKEX/公司 IR HTML、文本和 PDF，保留来源 URL、内容哈希和 PDF 页边界。

设计文档：

- [产品定位](./PRODUCT_POSITIONING.md)
- [第一版可验收需求](./MVP_REQUIREMENTS.md)
- [第一版 PRD](./PRD_V0.1.md)
- [技术设计](./TECH_DESIGN_V0.1.md)
- [数据模型与工作流](./DATA_MODEL_AND_WORKFLOW_V0.1.md)

## 运行测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
DEEPSEEK_API_KEY=... PYTHONPATH=src python3 -m stock_research --demo --llm
AI_STOCK_DB=./research.sqlite3 PYTHONPATH=src python3 -m stock_research --web --port 8000
# optional background worker (另一个终端；先安装 pip install -e '.[worker]' 并启动 Redis)
AI_STOCK_DB=./research.sqlite3 REDIS_URL=redis://127.0.0.1:6379/0 \
  PYTHONPATH=src python3 -m stock_research --worker
# optional React frontend (in another terminal)
cd frontend && npm run dev
```

Yahoo Finance 适配器只用于原型验证，不能作为生产环境的唯一数据源；生产接入前需要替换为已授权的港股数据供应商，并保留来源、时间和授权元数据。

文档下载器默认只允许 HKEX 域名，并且限制响应大小。PDF 解析器通过 `pdftotext` 保留页码边界；扫描版或无文本 PDF 会明确失败，后续再增加 OCR worker。

事实抽取器只接受金额、指标标签和期间足够明确的文本；多年度表格行会跳过，避免在没有列头映射时猜错年份。抽取结果始终关联证据片段和置信度。

启动 Web MVP 后访问 `http://127.0.0.1:8000`。页面以聊天为主入口，支持按公司查看会话、继续追问和打开历史报告；研究设置可在聊天区展开编辑。可在研究设置中粘贴 HKEX/公司 IR 的公开 HTTPS URL（每行一个），系统会下载并解析 HTML、文本或 PDF；也可粘贴财报/公告文本。未提供任何资料时，研究会明确报错并要求提供资料，不再静默使用合成样例。数据库路径由 `AI_STOCK_DB` 指定，默认是当前目录的 `research.sqlite3`。该页面是本地演示，不连接真实用户账户或交易系统。

联网搜索：追问消息中包含 `搜`/`联网`/`网络`/`最新`/`最近`/`新闻`/`资讯`/`消息`/`公告`/`股价`/`行情`/`价格` 等词时，系统会先查 DuckDuckGo（经系统 `curl` 请求，因其对 Python TLS 指纹反爬），无结果时降级到 Google News RSS（官方免费源，优先使用用户原话作为查询词）；两者都无结果时回退到基于历史报告的回答。搜索命中时回答末尾附 Markdown 来源链接，前端在新标签页打开。整条链路免费且无需额外 API Key，搜索失败不影响正常问答。

公司档案面板：顶栏「公司档案」按钮可展开右侧面板，默认收起；新报告生成时按钮显示红点提示而不自动弹开。面板含四个 Tab——「概览」展示 Yahoo 延迟行情快照（现价、涨跌、52 周区间，原型数据仅供参考）与公司资料/报告计数；「资料」是参考资料管理：登记的资料源 URL 持久化在数据库中并绑定公司（`company_sources` 表），每次研究自动抓取全部已登记 URL，抓取成功的文档进入「已归档文档」列表，登记/移除即时生效；「报告」列出历史报告，点开在弹层中查看全文；「动态」按需加载 Google News 相关新闻。研究截止日期不再由前端传入，默认取当天。配套 API：`GET /api/company-panel?symbol=&market=`、`POST /api/company-panel/sources`、`DELETE /api/company-panel/sources/{id}`、`GET /api/quote/{symbol}?market=`、`GET /api/news?name=&symbol=`。

Web API：

- `POST /api/research`：创建一次研究运行并返回报告（含 `run_id`、`report_id`）；
- `GET /api/projects`：列出研究项目；
- `GET /api/companies`：按公司聚合，并按最新会话事件排序；
- `GET /api/projects/{project_id}/sessions`：列出公司下的会话；
- `POST /api/projects/{project_id}/sessions`：创建新的主题会话；
- `GET /api/sessions/{session_id}`：读取会话消息、事件和关联运行；
- `POST /api/sessions/{session_id}/messages`：在当前会话中继续聊天；
- `POST /api/sessions/{session_id}/archive` / `activate`：归档或激活会话；
- `POST /api/chat`：以聊天消息作为统一入口，自动创建或复用公司会话；
- `GET /api/jobs/{job_id}`：读取后台研究任务状态；
- `GET /api/runs`：列出运行，可用 `?project_id=...` 过滤；
- `GET /api/runs/{run_id}`：返回状态、步骤、事件及所有中间产物；
- `GET /api/reports/{report_id}`：读取完整报告 JSON/Markdown。
- `GET/POST /api/settings`：读取或配置当前本地服务的模型 Provider（不会返回 API Key）。

`POST /api/chat` 和 `POST /api/research` 支持 `document_urls`（URL 数组或换行分隔字符串），也支持 `document_sources`（包含 `url`、可选 `title`、`published_at` 和 `language` 的对象数组）。默认只允许 HKEX 的 `www1.hkexnews.hk`、`www.hkexnews.hk` 和 `hkexnews.hk`；如需公司 IR 域名，可通过 `AI_STOCK_DOCUMENT_HOSTS=ir.example.com,www1.hkexnews.hk` 显式加入白名单。

前端源码位于 [`frontend/`](./frontend/)，开发模式访问 `http://127.0.0.1:5173`；Python Web 服务检测到 `frontend/dist` 后会自动托管构建后的页面。

LLM 接口使用 OpenAI-compatible wire shape。配置 `DEEPSEEK_API_KEY` 后运行 `--llm` 即可使用 DeepSeek `deepseek-chat`；也可以通过 `AI_STOCK_LLM_BASE_URL` 和 `AI_STOCK_LLM_MODEL` 接入其他兼容网关。API Key 只从环境变量读取，不写入代码或仓库。CLI 和 Web 研究请求都要求真实模型，未配置时会直接提示“未配置真实模型”，不会降级到本地规则模式。

Web 页面左下角“设置”也可以配置 DeepSeek 或其他 OpenAI 兼容模型（接口地址、模型名和 API Key）。保存后新的研究请求会直接使用该模型；API Key 不会通过状态接口返回。生产环境建议继续使用环境变量注入密钥，并保护本地数据库与队列存储。

设置 `AI_STOCK_QUEUE=rq` 后，研究类消息会写入 Redis/RQ 队列并立即返回 `job_id`；不设置时默认使用 inline 模式，适合本地演示和测试。RQ 依赖是可选的，不会影响默认安装。

## 下一阶段

1. 将事件日志和研究运行持久化到 PostgreSQL；
2. 接入经核验的 HKEX/公司 IR 文档；
3. 增加真实财报的表格解析和 OCR 降级；
4. 接入可替换的 LLM Adapter，生成定性分析；
5. 增加 Web 研究工作区。
