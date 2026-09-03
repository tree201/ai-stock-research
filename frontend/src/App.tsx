import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Button, Select } from "antd";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  BarChart3,
  ChevronDown,
  ChevronRight,
  FileText,
  ListTodo,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Settings,
  Sparkles,
} from "lucide-react";
import {
  api,
  Company,
  CompanyCatalogEntry,
  Job,
  Message,
  ModelSettings,
  Report,
  Run,
  SearchResult,
  Session,
} from "./api";

function MessageBubble({ message }: { message: Message }) {
  if (message.message_type === "report_card") return null;
  const text =
    message.content?.text || message.content?.summary?.join("\n") || "";
  return (
    <div className={`message-row ${message.role === "user" ? "is-user" : ""}`}>
      <div className="message-bubble">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: (props) => <a {...props} target="_blank" rel="noreferrer" />,
          }}
        >
          {text}
        </ReactMarkdown>
      </div>
    </div>
  );
}

function ReportCard({ report }: { report: Report }) {
  const facts = useMemo(() => report.facts?.slice(0, 8) || [], [report]);
  const dcf =
    report.calculations?.filter((item) => item.calculation_type === "dcf") ||
    [];
  return (
    <div className="report-card">
      <div className="report-heading">
        <FileText size={16} />
        <span>研究报告</span>
        <span className="muted">{report.company.symbol}</span>
      </div>
      <div className="summary-list">
        {report.summary?.map((item) => (
          <div key={item}>{item}</div>
        ))}
      </div>
      {facts.length > 0 && (
        <details>
          <summary>财务事实</summary>
          <div className="fact-table">
            <div className="fact-row fact-head">
              <span>指标</span>
              <span>数值</span>
              <span>期间</span>
              <span>置信度</span>
              <span>来源</span>
            </div>
            {facts.map((fact) => (
              <div
                className="fact-row"
                key={`${fact.metric}-${fact.period_end}`}
              >
                <span>{fact.metric}</span>
                <span>
                  {fact.value.toLocaleString()} {fact.currency || ""}
                </span>
                <span>{fact.period_end || "-"}</span>
                <span>{fact.confidence.toFixed(2)}</span>
                <span className="fact-source">
                  {fact.citation.source_url ? (
                    <a
                      href={fact.citation.source_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {fact.citation.page
                        ? `第 ${fact.citation.page} 页`
                        : `第 ${fact.citation.source_line} 行`}
                    </a>
                  ) : (
                    `第 ${fact.citation.source_line} 行`
                  )}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}
      {dcf.length > 0 && (
        <details>
          <summary>
            <BarChart3 size={14} /> DCF 情景
          </summary>
          <div className="scenario-grid">
            {dcf.map((item) => (
              <div className="scenario" key={String(item.inputs.scenario)}>
                <span>{String(item.inputs.scenario || "scenario")}</span>
                <strong>
                  {Object.values(item.outputs)[0]?.toLocaleString?.() || "-"}
                </strong>
              </div>
            ))}
          </div>
        </details>
      )}
      <details>
        <summary>打开完整 Markdown 报告</summary>
        <div className="markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {report.markdown}
          </ReactMarkdown>
        </div>
      </details>
    </div>
  );
}

const STEP_LABELS: Record<string, string> = {
  collect_filings: "获取资料",
  extract_financials: "抽取财务事实",
  analyze_business: "分析业务",
  analyze_risks: "分析风险",
  calculate_valuation: "计算估值",
  review: "校验证据",
  compile_report: "编写报告",
};
const activeRun = (runs?: Run[]) =>
  runs?.find(
    (run) => !["completed", "failed", "canceled"].includes(run.status),
  ) || undefined;
const sessionTitle = (title?: string) => {
  const value = title?.trim();
  return !value ||
    ["你好", "您好", "嗨", "hello", "hi", "hey", "在吗", "测试"].includes(
      value.toLowerCase(),
    )
    ? "长期研究"
    : value;
};

function cleanMessages(messages: Message[]) {
  const seen = new Set<string>();
  return messages.filter((message) => {
    if (message.message_type === "report_card") return false;
    const text =
      message.content?.text || message.content?.summary?.join("\n") || "";
    const key = `${message.role}:${text.trim()}`;
    if (!text.trim() || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function ProgressCard({ job, run }: { job: Job; run?: Run }) {
  const steps = run?.steps || [];
  const completed = steps.filter((step) => step.status === "completed").length;
  const running = steps.find((step) => step.status === "running");
  const current = running
    ? STEP_LABELS[running.step_key] || running.step_key
    : job.status === "queued"
      ? "等待 Worker 接单"
      : "准备研究计划";
  const percent = steps.length
    ? Math.round((completed / steps.length) * 100)
    : job.status === "queued"
      ? 5
      : 10;
  return (
    <div className="progress-card">
      <div className="progress-heading">
        <Sparkles size={15} />
        <strong>后台研究进度</strong>
        <span>{job.status === "queued" ? "排队中" : "执行中"}</span>
      </div>
      <div className="progress-track">
        <div
          className="progress-fill"
          style={{ width: `${Math.max(5, percent)}%` }}
        />
      </div>
      <div className="progress-meta">
        <span>{current}</span>
        <span>
          {steps.length ? `${completed}/${steps.length} 步` : "正在初始化"}
        </span>
      </div>
      {steps.length > 0 && (
        <div className="step-list">
          {steps.map((step) => (
            <div className={`step-item ${step.status}`} key={step.step_key}>
              <span className="step-mark">
                {step.status === "completed"
                  ? "✓"
                  : step.status === "running"
                    ? "•"
                    : ""}
              </span>
              <span>{STEP_LABELS[step.step_key] || step.step_key}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

type CompanyTreeProps = {
  companies: Company[];
  expandedCompanies: Record<string, boolean>;
  companySessions: Record<string, Session[]>;
  companyName: string;
  sessionId: string | null;
  selectedCompanyKey: string | null;
  onToggle: (company: Company) => void;
  onOpenCompany: (company: Company) => void;
  onOpenSession: (
    session: Session,
    companyName: string,
    companyKey: string,
  ) => void;
};

function CompanyTree({
  companies,
  expandedCompanies,
  companySessions,
  companyName,
  sessionId,
  selectedCompanyKey,
  onToggle,
  onOpenCompany,
  onOpenSession,
}: CompanyTreeProps) {
  return (
    <div className="company-list">
      {companies.map((company) => {
        const key = `${company.market}:${company.symbol}`;
        const expanded = !!expandedCompanies[key];
        const sessions = companySessions[key] || [];
        return (
          <div className="company-tree" key={key}>
            <div className="company-node">
              <button
                className="tree-toggle"
                aria-label={expanded ? "收起公司" : "展开公司"}
                onClick={() => onToggle(company)}
              >
                {expanded ? (
                  <ChevronDown size={14} />
                ) : (
                  <ChevronRight size={14} />
                )}
              </button>
              <button
                className="company-main"
                title={company.name}
                onClick={() => onOpenCompany(company)}
              >
                <span className="company-title">
                  <strong>{company.name}</strong>
                  <span className="company-symbol">{company.symbol}</span>
                  <span className="company-market">{company.market}</span>
                  <em>{company.session_count}</em>
                </span>
              </button>
            </div>
            {expanded && (
              <div className="session-tree">
                {sessions.length ? (
                  sessions.map((session) => (
                    <button
                      className={`session-node ${sessionId === session.id ? "selected" : ""}`}
                      key={session.id}
                      onClick={() => onOpenSession(session, company.name, key)}
                    >
                      <MessageSquare size={12} className="session-icon" />
                      <span className="session-copy">
                        <strong>{sessionTitle(session.title)}</strong>
                        <small>
                          {session.last_event_preview || "暂无活动"}
                        </small>
                      </span>
                    </button>
                  ))
                ) : (
                  <div className="session-empty">暂无会话</div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function App() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [expandedCompanies, setExpandedCompanies] = useState<
    Record<string, boolean>
  >({});
  const [companySessions, setCompanySessions] = useState<
    Record<string, Session[]>
  >({});
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [selectedCompanyKey, setSelectedCompanyKey] = useState<string | null>(
    null,
  );
  const [companyName, setCompanyName] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [pendingReports, setPendingReports] = useState<Report[]>([]);
  const [reportPanelOpen, setReportPanelOpen] = useState(true);
  const [pendingJob, setPendingJob] = useState<Job | null>(null);
  const [progressRun, setProgressRun] = useState<Run | undefined>();
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [settings, setSettings] = useState({
    name: "腾讯（示例）",
    symbol: "00700",
    asOfDate: "2025-12-31",
    documentUrls: "",
  });
  const [modelSettings, setModelSettings] = useState<ModelSettings>({
    provider: "deepseek",
    base_url: "https://api.deepseek.com/v1",
    model: "deepseek-chat",
    api_key: "",
  });
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
  const [modelSaving, setModelSaving] = useState(false);
  const submittingRef = useRef(false);
  const [newResearchOpen, setNewResearchOpen] = useState(false);
  const [newResearchCompanyKey, setNewResearchCompanyKey] = useState("");
  const [companyCatalog, setCompanyCatalog] = useState<CompanyCatalogEntry[]>([]);
  const [companyCatalogLoading, setCompanyCatalogLoading] = useState(false);
  const [companyCatalogLoaded, setCompanyCatalogLoaded] = useState(false);
  const [newResearchBusy, setNewResearchBusy] = useState(false);

  const openSettings = () => {
    setModelSettingsOpen(true);
  };

  const openNewResearch = () => {
    setNewResearchCompanyKey("");
    setNewResearchOpen(true);
  };

  useEffect(() => {
    if (!newResearchOpen || companyCatalogLoaded || companyCatalogLoading) return;
    setCompanyCatalogLoading(true);
    api.companyCatalog()
      .then(setCompanyCatalog)
      .catch((err) => setError(err instanceof Error ? err.message : "港股公司列表加载失败"))
      .finally(() => { setCompanyCatalogLoading(false); setCompanyCatalogLoaded(true); });
  }, [newResearchOpen, companyCatalogLoaded, companyCatalogLoading]);

  const researchCompanyOptions = useMemo(() => {
    const options = new Map<string, { value: string; label: string }>();
    [...companyCatalog, ...companies].forEach((company) => {
      const key = `${company.market}:${company.symbol}`;
      options.set(key, {
        value: key,
        label: `${company.name}（${company.symbol} · ${company.market}）`,
      });
    });
    return [...options.values()];
  }, [companies, companyCatalog]);

  async function createNewResearch() {
    if (newResearchBusy) return;
    const existingCompany = companies.find(
      (item) => `${item.market}:${item.symbol}` === newResearchCompanyKey,
    );
    const catalogCompany = companyCatalog.find(
      (item) => `${item.market}:${item.symbol}` === newResearchCompanyKey,
    );
    const company = existingCompany || catalogCompany;
    if (!company) { setError("请选择要研究的公司"); return; }
    setNewResearchBusy(true); setError("");
    try {
      let session: Session;
      let projectId: string;
      if (existingCompany?.project_ids?.length) {
        const result = await api.createSession(existingCompany.project_ids[0]);
        session = result.session;
        projectId = existingCompany.project_ids[0];
      } else {
        const result = await api.createProject(company.name, company.symbol, company.market);
        session = result.session;
        projectId = result.project.id;
      }
      await refreshCompanies();
      setNewResearchOpen(false);
      const key = `${company.market}:${company.symbol}`;
      const refreshedCompany = existingCompany || {
        ...company,
        id: projectId,
        project_ids: [projectId],
        session_count: 1,
      };
      setCompanySessions(prev => ({ ...prev, [key]: [] }));
      await fetchCompanySessions(refreshedCompany);
      await openSession(session, company.name, key);
    } catch (err) { setError(err instanceof Error ? err.message : "创建研究失败"); }
    finally { setNewResearchBusy(false); }
  }

  const refreshCompanies = () =>
    api
      .companies()
      .then(setCompanies)
      .catch((err) => setError(err.message));
  useEffect(() => {
    refreshCompanies();
    api
      .settings()
      .then((status) =>
        setModelSettings((current) => ({
          ...current,
          api_key_configured: status.api_key_configured,
          ...(status.llm_enabled
            ? {
                provider:
                  status.provider === "deepseek"
                    ? "deepseek"
                    : "openai_compatible",
                base_url: status.base_url || current.base_url,
                model: status.model || current.model,
              }
            : {}),
        })),
      )
      .catch(() => undefined);
  }, []);
  const visibleCompanies = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return companies;
    return companies.filter((company) => {
      const key = `${company.market}:${company.symbol}`;
      const sessions = companySessions[key] || [];
      return (
        [company.name, company.symbol, company.market].some((value) =>
          value.toLowerCase().includes(query),
        ) ||
        sessions.some((session) =>
          `${session.title} ${session.last_event_preview || ""}`
            .toLowerCase()
            .includes(query),
        )
      );
    });
  }, [companies, companySessions, searchQuery]);

  useEffect(() => {
    if (!searchOpen) { setSearchResults([]); return; }
    const query = searchQuery.trim();
    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (query) {
        api.search(query).then(results => { if (!cancelled) setSearchResults(results); }).catch(() => { if (!cancelled) setSearchResults([]); });
      } else {
        Promise.all(companies.flatMap(company => (company.project_ids?.length ? company.project_ids : [company.id]).map(projectId => api.sessions(projectId).then(sessions => sessions.map(session => ({ session, company: { id: company.id, name: company.name, symbol: company.symbol, market: company.market } }))))))
          .then(groups => { if (!cancelled) setSearchResults(groups.flat().sort((a, b) => new Date(b.session.latest_event_at).getTime() - new Date(a.session.latest_event_at).getTime()).slice(0, 20)); })
          .catch(() => { if (!cancelled) setSearchResults([]); });
      }
    }, 180);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [searchOpen, searchQuery, companies]);

  async function fetchCompanySessions(company: Company) {
    const projectIds = company.project_ids?.length
      ? company.project_ids
      : [company.id];
    const sessionGroups = await Promise.all(
      projectIds.map((projectId) => api.sessions(projectId)),
    );
    const sessions = sessionGroups
      .flat()
      .sort(
        (a, b) =>
          new Date(b.latest_event_at).getTime() -
          new Date(a.latest_event_at).getTime(),
      );
    const key = `${company.market}:${company.symbol}`;
    setCompanySessions((prev) => ({ ...prev, [key]: sessions }));
    return sessions;
  }

  async function openSession(
    session: Session,
    companyNameValue: string,
    companyKey?: string,
  ) {
    setError("");
    setCompanyName(companyNameValue);
    if (companyKey) setSelectedCompanyKey(companyKey);
    const detail = await api.session(session.id);
    setSessionId(detail.session.id);
    setMessages(cleanMessages(detail.messages));
    setPendingReports([]);
    setReportPanelOpen(false);
    const reportIds = detail.messages
      .filter(
        (message) =>
          message.message_type === "report_card" && message.content.report_id,
      )
      .map((message) => message.content.report_id as string);
    const latestReportId = reportIds.at(-1);
    if (latestReportId) {
      try {
        setPendingReports([await api.report(latestReportId)]);
        setReportPanelOpen(true);
      } catch {
        /* stale report references are ignored */
      }
    }
    const activeJob = (detail.jobs || []).find(
      (job) => job.status === "queued" || job.status === "running",
    );
    setPendingJob(activeJob || null);
    setProgressRun(activeRun(detail.runs));
  }

  async function openCompany(company: Company) {
    setError("");
    setCompanyName(company.name);
    const key = `${company.market}:${company.symbol}`;
    setSelectedCompanyKey(key);
    setExpandedCompanies((prev) => ({ ...prev, [key]: true }));
    const sessions =
      companySessions[key] || (await fetchCompanySessions(company));
    if (!sessions.length) return;
    const active =
      sessions.find((session) => session.status === "active") || sessions[0];
    await openSession(active, company.name);
  }

  async function toggleCompany(company: Company) {
    const key = `${company.market}:${company.symbol}`;
    if (expandedCompanies[key]) {
      setExpandedCompanies((prev) => ({ ...prev, [key]: false }));
      return;
    }
    setExpandedCompanies((prev) => ({ ...prev, [key]: true }));
    if (!companySessions[key]) await fetchCompanySessions(company);
  }

  useEffect(() => {
    if (!pendingJob) return;
    let stopped = false;
    const poll = async () => {
      try {
        const job = await api.job(pendingJob.id);
        const detail = await api.session(pendingJob.session_id);
        if (stopped) return;
        setProgressRun(activeRun(detail.runs));
        if (job.status === "completed" && job.run_id) {
          const run = await api.run(job.run_id);
          const reportRef = run.artifacts?.reports?.at(-1);
          if (reportRef?.id) {
            const report = await api.report(reportRef.id);
            if (!stopped)
              setPendingReports((prev) =>
                prev.some((item) => item.report_id === report.report_id)
                  ? prev
                  : [...prev, report],
              );
          }
          if (!stopped) {
            setPendingJob(null);
            setBusy(false);
            setError("");
          }
        } else if (job.status === "completed") {
          if (!stopped) {
            setError("后台任务已完成，但没有关联研究报告");
            setPendingJob(null);
            setBusy(false);
          }
        } else if (job.status === "failed" || job.status === "canceled") {
          if (!stopped) {
            setError(job.error?.message || "后台研究任务失败");
            setPendingJob(null);
            setBusy(false);
          }
        } else if (!stopped) {
          setPendingJob(job);
        }
      } catch (err) {
        if (!stopped)
          setError(err instanceof Error ? err.message : "任务状态查询失败");
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1500);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [pendingJob?.id]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || busy || submittingRef.current) return;
    if (!modelSettings.api_key_configured && !modelSettings.api_key?.trim()) {
      setError("尚未配置真实模型，请打开‘设置 → 模型接入’完成配置。");
      return;
    }
    submittingRef.current = true;
    setInput("");
    setError("");
    setBusy(true);
    const optimistic: Message = {
      id: crypto.randomUUID(),
      role: "user",
      message_type: "text",
      content: { text },
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic]);
    try {
      const llm = modelSettings.api_key?.trim()
        ? {
            provider: modelSettings.provider,
            base_url: modelSettings.base_url,
            model: modelSettings.model,
            api_key: modelSettings.api_key,
          }
        : undefined;
      const result = sessionId
        ? await api.message(
            sessionId,
            text,
            settings.asOfDate,
            llm,
            settings.documentUrls,
          )
        : await api.chat({
            name: settings.name,
            symbol: settings.symbol,
            as_of_date: settings.asOfDate,
            content: text,
            document_urls: settings.documentUrls,
            llm,
          });
      if (result.session_id) {
        setSessionId(result.session_id);
        if (!sessionId) setSelectedCompanyKey(`HK:${settings.symbol}`);
      }
      if (result.message)
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            message_type: "text",
            content: { text: result.message },
            created_at: new Date().toISOString(),
          },
        ]);
      if (result.report) {
        setPendingReports((prev) => [...prev, result.report!]);
        setReportPanelOpen(true);
      }
      if (result.type === "research_queued" && result.job_id) {
        setPendingJob({
          id: result.job_id,
          session_id: result.session_id,
          status: "queued",
          attempts: 0,
        });
        setProgressRun(undefined);
        setError("");
      }
      await refreshCompanies();
    } catch (err) {
      setError(err instanceof Error ? err.message : "处理失败");
    } finally {
      setInput((current) => (current.trim() === text ? "" : current));
      submittingRef.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarCollapsed ? "is-collapsed" : ""}`}>
        <div className="workspace-switcher">
          <div className="workspace-copy">
            <strong>ASR</strong>
          </div>
          <button
            className="sidebar-collapse"
            aria-label={sidebarCollapsed ? "展开侧栏" : "收起侧栏"}
            onClick={() => setSidebarCollapsed((value) => !value)}
          >
            {sidebarCollapsed ? (
              <PanelLeftOpen size={14} />
            ) : (
              <PanelLeftClose size={14} />
            )}
          </button>
        </div>
        {!sidebarCollapsed && (
          <>
            <div className="sidebar-actions">
              <button
                className="sidebar-action"
                onClick={() => setSearchOpen((value) => !value)}
              >
                <Search size={14} /> 搜索
              </button>
              <button
                className="sidebar-action"
                onClick={openNewResearch}
              >
                <Plus size={14} /> 新研究
              </button>
              <button
                className="sidebar-action"
                onClick={() =>
                  setError(
                    pendingJob
                      ? "当前有研究任务正在后台执行"
                      : "暂无后台研究任务",
                  )
                }
              >
                <ListTodo size={14} /> 后台任务
              </button>
            </div>
            <div className="section-label">
              公司研究 <span>{visibleCompanies.length}</span>
            </div>
            <div className="company-scroll">
              <CompanyTree
                companies={visibleCompanies}
                expandedCompanies={expandedCompanies}
                companySessions={companySessions}
                companyName={companyName}
                sessionId={sessionId}
                selectedCompanyKey={selectedCompanyKey}
                onToggle={(company) => void toggleCompany(company)}
                onOpenCompany={(company) => void openCompany(company)}
                onOpenSession={(session, name, key) =>
                  void openSession(session, name, key)
                }
              />
            </div>
          </>
        )}
        <div className="sidebar-footer">
          <button
            className="sidebar-footer-button"
            title="设置"
            aria-label="设置"
            aria-haspopup="dialog"
            aria-expanded={modelSettingsOpen}
            onClick={() => openSettings()}
          >
            <Settings size={14} />
          </button>
        </div>
      </aside>
      <main className="main-shell">
        <header className="topbar">
          <h1>{companyName || "开始一段研究"}</h1>
          <div className="topbar-actions">
            {pendingReports.length > 0 && (
              <button
                className="topbar-button report-toggle"
                onClick={() => setReportPanelOpen((value) => !value)}
              >
                <FileText size={14} />{" "}
                {reportPanelOpen ? "收起报告" : "查看报告"}
              </button>
            )}
          </div>
        </header>
        <div className="thread-scroll">
          <section className="thread">
            {!messages.length && (
              <div className="empty-state">
                <div className="empty-icon">
                  <Sparkles />
                </div>
                <h2>今天想研究哪家公司？</h2>
                <p>
                  直接输入研究问题。研究结果、证据和报告会沉淀在公司的会话中。
                </p>
                <div className="suggestions">
                  <button
                    onClick={() =>
                      setInput("研究腾讯是否适合长期持有，重点看现金流和估值")
                    }
                  >
                    研究长期持有价值
                  </button>
                  <button
                    onClick={() => setInput("分析公司的主要风险和反方证据")}
                  >
                    寻找风险和反方
                  </button>
                </div>
              </div>
            )}
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} />
            ))}
            {pendingJob && (
              <div className="message-row">
                <div className="message-bubble report-bubble">
                  <ProgressCard job={pendingJob} run={progressRun} />
                </div>
              </div>
            )}
            {busy && !pendingJob && (
              <div className="message-row">
                <div className="message-bubble typing">
                  <span />
                  <span />
                  <span />
                </div>
              </div>
            )}
          </section>
        </div>
        <div
          className="composer-wrap"
          style={{
            left: sidebarCollapsed ? 62 : undefined,
            right:
              reportPanelOpen && pendingReports.length > 0 ? 380 : undefined,
          }}
        >
          {error && (
            <div className="error-bar">
              {error}
              <button type="button" onClick={openSettings}>
                打开模型设置
              </button>
            </div>
          )}
          <form className="composer" onSubmit={submit}>
            <textarea
              value={input}
              disabled={busy || !!pendingJob}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit(e);
                }
              }}
              placeholder="输入研究问题或追问……"
              rows={1}
            />
          </form>
        </div>
      </main>
      {reportPanelOpen && pendingReports.length > 0 && (
        <aside className="report-panel">
          <div className="report-panel-heading">
            <div>
              <strong>研究报告</strong>
              <small>{pendingReports.at(-1)?.company.name} · 最新版本</small>
            </div>
            <button
              onClick={() => setReportPanelOpen(false)}
              aria-label="收起报告"
            >
              <ChevronRight size={16} />
            </button>
          </div>
          <div className="report-panel-scroll">
            <div className="report-panel-content">
              {pendingReports.slice(-1).map((report) => (
                <ReportCard report={report} key={report.report_id} />
              ))}
            </div>
          </div>
        </aside>
      )}
      {searchOpen && (
        <div className="search-overlay" role="dialog" aria-modal="true" aria-label="搜索会话" onMouseDown={() => setSearchOpen(false)}>
          <div className="search-dialog" onMouseDown={event => event.stopPropagation()}>
            <div className="search-dialog-input"><Search size={20} /><input autoFocus value={searchQuery} onChange={event => setSearchQuery(event.target.value)} onKeyDown={event => { if (event.key === "Escape") setSearchOpen(false); if (event.key === "Enter" && searchResults[0]) { const result = searchResults[0]; const company = companies.find(item => item.symbol === result.company.symbol && item.market === result.company.market); setSearchOpen(false); if (company) void openSession(result.session, result.company.name, `${result.company.market}:${result.company.symbol}`); } }} placeholder="搜索会话内容、标题…" /></div>
            <div className="search-dialog-body">
              <div className="search-dialog-label">{searchQuery.trim() ? "搜索结果" : "最近会话"}</div>
              {searchResults.length ? searchResults.map(result => {
                const company = companies.find(item => item.symbol === result.company.symbol && item.market === result.company.market);
                return <button className="search-dialog-result" key={result.session.id} onClick={() => { setSearchOpen(false); if (company) void openSession(result.session, result.company.name, `${result.company.market}:${result.company.symbol}`); }}><MessageSquare size={16} /><span className="search-dialog-result-copy"><strong>{result.session.title || "新研究"}</strong><small>{result.company.name} · {result.company.symbol}{result.session.last_event_preview ? ` · ${result.session.last_event_preview}` : ""}</small></span><span className="search-dialog-result-key">↵</span></button>;
              }) : <div className="search-dialog-empty">{searchQuery.trim() ? "未找到匹配的会话" : "正在加载最近会话…"}</div>}
            </div>
            <div className="search-dialog-footer"><span><kbd>↑</kbd><kbd>↓</kbd> 移动</span><span><kbd>Enter</kbd> 打开</span><span><kbd>Esc</kbd> 关闭</span></div>
          </div>
        </div>
      )}
      {newResearchOpen && (
        <div className="settings-overlay" role="dialog" aria-modal="true" aria-labelledby="new-research-title">
          <div className="settings-dialog new-research-dialog">
            <div className="model-settings-heading">
              <div><h2 id="new-research-title">新研究</h2></div>
              <button className="modal-close" onClick={() => setNewResearchOpen(false)} aria-label="关闭">×</button>
            </div>
            <label className="new-research-label">
              <span>公司</span>
              <Select
                className="new-research-select"
                showSearch
                autoFocus
                value={newResearchCompanyKey || undefined}
                placeholder="输入公司名称或代码"
                loading={companyCatalogLoading}
                optionFilterProp="label"
                filterOption={(input, option) =>
                  String(option?.label || "")
                    .toLowerCase()
                    .includes(input.toLowerCase())
                }
                options={researchCompanyOptions}
                onChange={(value) => setNewResearchCompanyKey(value)}
              />
            </label>
            <div className="new-research-actions"><Button onClick={() => setNewResearchOpen(false)}>取消</Button><Button type="primary" loading={newResearchBusy} onClick={() => void createNewResearch()}>确认创建</Button></div>
          </div>
        </div>
      )}
      {modelSettingsOpen && (
        <div className="settings-overlay" role="dialog" aria-modal="true">
          <div className="settings-dialog">
            <div className="model-settings-heading">
              <div>
                <h2>设置</h2>
              </div>
              <button
                className="modal-close"
                onClick={() => setModelSettingsOpen(false)}
                aria-label="关闭"
              >
                ×
              </button>
            </div>
            <div className="settings-layout">
              <nav className="settings-nav" aria-label="设置分类">
                <button className="active" type="button">模型接入</button>
              </nav>
              <div className="settings-content">
                <div className="model-settings-content">
                  <h3>模型接入</h3>
                  <p>配置 OpenAI 兼容接口后，研究会使用真实模型分析。</p>
            <label>
              提供商
              <Select
                className="settings-select"
                value={modelSettings.provider}
                options={[
                  { value: "deepseek", label: "DeepSeek" },
                  { value: "openai_compatible", label: "OpenAI 兼容" },
                ]}
                onChange={(value) =>
                  setModelSettings({
                    ...modelSettings,
                    provider: value,
                  })
                }
              />
            </label>
            <label>
              接口地址
              <input
                value={modelSettings.base_url}
                onChange={(e) =>
                  setModelSettings({
                    ...modelSettings,
                    base_url: e.target.value,
                  })
                }
                placeholder="https://api.deepseek.com/v1"
              />
            </label>
            <label>
              模型
              <input
                value={modelSettings.model}
                onChange={(e) =>
                  setModelSettings({ ...modelSettings, model: e.target.value })
                }
                placeholder="deepseek-chat"
              />
            </label>
            <label>
              API Key
              <input
                type="password"
                value={modelSettings.api_key || ""}
                onChange={(e) =>
                  setModelSettings({
                    ...modelSettings,
                    api_key: e.target.value,
                    api_key_configured: Boolean(e.target.value),
                  })
                }
                placeholder={
                  modelSettings.api_key_configured
                    ? "已配置（重新输入可替换）"
                    : "只保存在当前浏览器会话"
                }
              />
            </label>
            <div className="model-settings-actions">
              <span
                className={
                  modelSettings.api_key || modelSettings.api_key_configured
                    ? "configured"
                    : "unconfigured"
                }
              >
                {modelSettings.api_key || modelSettings.api_key_configured
                  ? "已配置真实模型"
                  : "未配置真实模型"}
              </span>
              <button
                disabled={modelSaving}
                onClick={async () => {
                  setModelSaving(true);
                  try {
                    const savePayload = { ...modelSettings };
                    if (!savePayload.api_key) delete savePayload.api_key;
                    const status = await api.saveSettings(savePayload);
                    setModelSettings((current) => ({
                      ...current,
                      ...status,
                      api_key: current.api_key,
                    }));
                    setError("");
                    setModelSettingsOpen(false);
                  } catch (err) {
                    setError(
                      err instanceof Error ? err.message : "模型设置保存失败",
                    );
                  } finally {
                    setModelSaving(false);
                  }
                }}
              >
                {modelSaving ? "保存中…" : "保存设置"}
              </button>
            </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
