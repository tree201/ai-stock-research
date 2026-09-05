import {
  CSSProperties,
  FormEvent,
  PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { App as AntdApp, Button, Badge, ConfigProvider, Dropdown, Modal, Segmented, Select, Tabs, theme as antdTheme } from "antd";
import { ANTD_THEME_TOKENS, ThemeName, applyTheme, initialTheme } from "./theme";
import { ApprovalPicker } from "./ApprovalPicker";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  BarChart3,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileText,
  ListTodo,
  MessageSquare,
  Palette,
  Server,
  ArrowUp,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Settings,
  Sparkles,
  Trash2,
} from "lucide-react";
import {
  api,
  fmtName,
  ArticleReader,
  Company,
  CompanyCatalogEntry,
  CompanyPanel,
  Job,
  Message,
  ModelSettings,
  NameDisplayPref,
  NewsItem,
  Quote,
  Report,
  Run,
  SearchResult,
  Session,
  TrustedHost,
  LlmConfig,
} from "./api";
import { ModelPicker } from "./ModelPicker";
import { ModelsSection } from "./models/ModelsSection";
import { DeepSeekOnboardingDialog } from "./models/DeepSeekOnboardingDialog";

function relativeTime(value?: string | null): string {
  if (!value) return "";
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return "";
  const minutes = Math.floor((Date.now() - ts) / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  return new Date(ts).toLocaleDateString("zh-CN");
}

const TRUST_BADGES: Record<string, { label: string; className: string }> = {
  verified: { label: "私有", className: "trust-tag trust-private" },
  whitelist: { label: "可信", className: "trust-tag trust-whitelist" },
  unverified: { label: "未验证", className: "trust-tag trust-unverified" },
};

function TrustBadge({ trust }: { trust?: string | null }) {
  const badge = TRUST_BADGES[trust || "unverified"] || TRUST_BADGES.unverified;
  return <span className={badge.className}>{badge.label}</span>;
}

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

function AssumptionsForm({
  report,
  onUpdated,
}: {
  report: Report;
  onUpdated?: (report: Report) => void;
}) {
  const dcf =
    report.calculations?.filter((item) => item.calculation_type === "dcf") ||
    [];
  const base =
    dcf.find((item) => item.inputs.scenario === "base") || dcf[0];
  const [growth, setGrowth] = useState(() =>
    Array.isArray(base?.inputs.growth_rates)
      ? (base.inputs.growth_rates as number[]).join(", ")
      : "",
  );
  const [discount, setDiscount] = useState(() =>
    base?.inputs.discount_rate != null ? String(base.inputs.discount_rate) : "",
  );
  const [terminal, setTerminal] = useState(() =>
    base?.inputs.terminal_growth != null
      ? String(base.inputs.terminal_growth)
      : "",
  );
  const [shares, setShares] = useState(() =>
    base?.inputs.shares != null ? String(base.inputs.shares) : "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    const growthRates = growth
      .split(/[,，]/)
      .map((item) => Number(item.trim()))
      .filter((item) => Number.isFinite(item) && item !== 0);
    const discountRate = Number(discount.trim());
    const terminalGrowth = Number(terminal.trim());
    if (!growthRates.length || !Number.isFinite(discountRate)) {
      setError("请填写有效的增长率和折现率（小数形式，如 0.12）");
      return;
    }
    const assumptions: Record<string, number | number[]> = {
      growth_rates: growthRates,
      discount_rate: discountRate,
    };
    if (Number.isFinite(terminalGrowth))
      assumptions.terminal_growth = terminalGrowth;
    const shareCount = Number(shares.trim());
    if (Number.isFinite(shareCount) && shareCount > 0)
      assumptions.shares = shareCount;
    setBusy(true);
    setError("");
    try {
      const updated = await api.recalculateReport(report.report_id, assumptions);
      onUpdated?.(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "重算失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="assumption-form">
      <div className="assumption-title">修改估值假设并重算（无需重新研究）</div>
      <div className="assumption-grid">
        <label>
          增长率（逗号分隔）
          <input
            value={growth}
            onChange={(e) => setGrowth(e.target.value)}
            placeholder="0.12, 0.10, 0.08"
          />
        </label>
        <label>
          折现率
          <input
            value={discount}
            onChange={(e) => setDiscount(e.target.value)}
            placeholder="0.09"
          />
        </label>
        <label>
          永续增长率
          <input
            value={terminal}
            onChange={(e) => setTerminal(e.target.value)}
            placeholder="0.03"
          />
        </label>
        <label>
          股本
          <input
            value={shares}
            onChange={(e) => setShares(e.target.value)}
            placeholder="9000000000"
          />
        </label>
      </div>
      {error && <div className="assumption-error">{error}</div>}
      <button
        className="assumption-submit"
        disabled={busy}
        onClick={() => void submit()}
      >
        {busy ? "重算中…" : "重算估值"}
      </button>
    </div>
  );
}

function ReportCard({
  report,
  onUpdated,
}: {
  report: Report;
  onUpdated?: (report: Report) => void;
}) {
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
      {report.recalculated_from && (
        <div className="recalc-note">
          已按新假设重算（版本 {report.version || "-"}）
        </div>
      )}
      {report.diff && (
        <details className="diff-details" open>
          <summary>与上一版差异</summary>
          <div className="diff-list">
            {(report.diff.new_facts?.length ?? 0) > 0 ? (
              report.diff.new_facts!.map((fact, index) => (
                <div className="diff-row" key={`new-${fact.metric}-${fact.period_end}-${index}`}>
                  <span className="diff-tag new">新增</span>
                  <span>
                    {fact.metric}（{fact.period_end || "-"}）＝{" "}
                    {fact.value.toLocaleString()} {fact.currency || ""}
                  </span>
                </div>
              ))
            ) : (
              <div className="diff-row">无新增事实</div>
            )}
            {(report.diff.changed_facts || []).map((change) => (
              <div
                className="diff-row"
                key={`chg-${change.metric}-${change.period_end}`}
              >
                <span className="diff-tag changed">变化</span>
                <span>
                  {change.metric}（{change.period_end || "-"}）：
                  {change.previous_value.toLocaleString()} →{" "}
                  {change.current_value.toLocaleString()}
                </span>
              </div>
            ))}
            {report.diff.valuation && (
              <div className="diff-row">
                <span className="diff-tag valuation">估值</span>
                <span>
                  基准情景每股价值 {report.diff.valuation.previous.toLocaleString()} →{" "}
                  {report.diff.valuation.current.toLocaleString()}（
                  {(report.diff.valuation.change_pct * 100).toFixed(1)}%）
                </span>
              </div>
            )}
            <div className="diff-row muted">
              结论变化：{report.diff.conclusion_changed ? "是" : "否"}
            </div>
          </div>
        </details>
      )}
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
                  <TrustBadge trust={fact.citation.trust} />
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
          <AssumptionsForm report={report} onUpdated={onUpdated} />
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
  onRemoveCompany: (company: Company) => void;
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
  onRemoveCompany,
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
                  <Dropdown
                    trigger={["click"]}
                    overlayClassName="company-more-dropdown"
                    menu={{
                      items: [
                        {
                          key: "remove",
                          label: "移除该公司",
                          danger: true,
                          icon: <Trash2 size={13} />,
                          onClick: () => onRemoveCompany(company),
                        },
                      ],
                    }}
                  >
                    <button
                      className="company-more"
                      title="更多选项"
                      aria-label={`更多选项：${company.name}`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <MoreHorizontal size={13} />
                    </button>
                  </Dropdown>
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

const DEFAULT_SIDEBAR_WIDTH = 248;
const DEFAULT_PANEL_WIDTH = 380;
const SIDEBAR_MIN = 200;
const SIDEBAR_MAX = 520;
const PANEL_MIN = 300;
const PANEL_MAX = 720;

function storedWidth(key: string, fallback: number, min: number, max: number) {
  const stored = Math.round(Number(localStorage.getItem(key)));
  return Number.isFinite(stored) && stored >= min && stored <= max
    ? stored
    : fallback;
}

export default function App() {
  const [theme, setTheme] = useState<ThemeName>(initialTheme);
  const [settingsSection, setSettingsSection] = useState<
    "model" | "appearance"
  >("model");
  useEffect(() => {
    applyTheme(theme);
    localStorage.setItem("theme", theme);
  }, [theme]);
  const { modal } = AntdApp.useApp();
  const [companies, setCompanies] = useState<Company[]>([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(() =>
    storedWidth("sidebarWidth", DEFAULT_SIDEBAR_WIDTH, SIDEBAR_MIN, SIDEBAR_MAX),
  );
  const [panelWidth, setPanelWidth] = useState(() =>
    storedWidth("panelWidth", DEFAULT_PANEL_WIDTH, PANEL_MIN, PANEL_MAX),
  );
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
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelTab, setPanelTab] = useState("overview");
  const [panelCompany, setPanelCompany] = useState<{
    name: string;
    symbol: string;
    market: string;
  } | null>(null);
  const [panelData, setPanelData] = useState<CompanyPanel | null>(null);
  const [panelQuote, setPanelQuote] = useState<Quote | null>(null);
  const [panelLoading, setPanelLoading] = useState(false);
  const [newsItems, setNewsItems] = useState<NewsItem[] | null>(null);
  const [newsLoading, setNewsLoading] = useState(false);
  const [newsQuery, setNewsQuery] = useState("");
  const [newsSearchActive, setNewsSearchActive] = useState(false);
  const [visibleNews, setVisibleNews] = useState(15);
  const [newReportBadge, setNewReportBadge] = useState(false);
  const [reportViewer, setReportViewer] = useState<Report | null>(null);
  const [reportLoadingId, setReportLoadingId] = useState<string | null>(null);
  const [articleViewer, setArticleViewer] = useState<{
    url: string;
    title: string;
  } | null>(null);
  const [articleReader, setArticleReader] = useState<ArticleReader | null>(
    null,
  );
  const [articleLoading, setArticleLoading] = useState(false);
  const [articleError, setArticleError] = useState("");
  const [pendingJob, setPendingJob] = useState<Job | null>(null);
  const [progressRun, setProgressRun] = useState<Run | undefined>();
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [settings, setSettings] = useState({
    name: "腾讯（示例）",
    symbol: "00700",
  });
  const [newSourceUrl, setNewSourceUrl] = useState("");
  const [newSourceClass, setNewSourceClass] = useState("private");
  const [sourceBusy, setSourceBusy] = useState(false);
  const [trustedHosts, setTrustedHosts] = useState<TrustedHost[]>([]);
  const [newTrustedHost, setNewTrustedHost] = useState("");
  const [hostBusy, setHostBusy] = useState(false);
  const [modelSettings, setModelSettings] = useState<ModelSettings>({
    provider: "deepseek",
    base_url: "https://api.deepseek.com/v1",
    model: "deepseek-chat",
    api_key: "",
  });
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
  const [namePref, setNamePref] = useState<NameDisplayPref>("zh");
  const [llmConfig, setLlmConfig] = useState<LlmConfig | null>(null);
  const submittingRef = useRef(false);
  const [newResearchOpen, setNewResearchOpen] = useState(false);
  const [newResearchCompanyKey, setNewResearchCompanyKey] = useState("");
  const [companyCatalog, setCompanyCatalog] = useState<CompanyCatalogEntry[]>([]);
  const [companyCatalogLoading, setCompanyCatalogLoading] = useState(false);
  const [companyCatalogLoaded, setCompanyCatalogLoaded] = useState(false);
  const [newResearchBusy, setNewResearchBusy] = useState(false);
  const threadScrollRef = useRef<HTMLDivElement>(null);
  const threadPinnedRef = useRef(true);

  // 用户向上滚动超过阈值时暂停自动跟随，回到底部附近则恢复。
  function handleThreadScroll(event: React.UIEvent<HTMLDivElement>) {
    const el = event.currentTarget;
    threadPinnedRef.current =
      el.scrollTop + el.clientHeight >= el.scrollHeight - 140;
  }

  // 消息、进度或会话变化时，若处于跟随状态则滚到底部。
  useEffect(() => {
    const el = threadScrollRef.current;
    if (!el || !threadPinnedRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, pendingJob, progressRun, busy, sessionId]);

  useEffect(() => {
    localStorage.setItem("sidebarWidth", String(sidebarWidth));
  }, [sidebarWidth]);
  useEffect(() => {
    localStorage.setItem("panelWidth", String(panelWidth));
  }, [panelWidth]);

  function beginResize(side: "left" | "right", event: React.PointerEvent) {
    event.preventDefault();
    const start = {
      side,
      startX: event.clientX,
      startWidth: Math.round(side === "left" ? sidebarWidth : panelWidth),
    };
    document.body.classList.add("is-resizing");
    const onMove = (ev: PointerEvent) => {
      const delta = Math.round(
        start.side === "left"
          ? ev.clientX - start.startX
          : start.startX - ev.clientX,
      );
      if (start.side === "left") {
        setSidebarWidth(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, start.startWidth + delta)));
      } else {
        setPanelWidth(Math.min(PANEL_MAX, Math.max(PANEL_MIN, start.startWidth + delta)));
      }
    };
    // pointerup can be lost when the cursor leaves the window (e.g. over
    // DevTools); pointercancel + window scope make sure listeners never leak.
    const finish = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      document.body.classList.remove("is-resizing");
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
  }

  const shellStyle = {
    "--sidebar-width": `${sidebarCollapsed ? 62 : sidebarWidth}px`,
    "--composer-offset": sidebarCollapsed ? "0px" : "-28px",
    "--panel-width": `${panelWidth}px`,
  } as CSSProperties;

  const openSettings = () => {
    setModelSettingsOpen(true);
  };

  useEffect(() => {
    if (!modelSettingsOpen) return;
    api.llmConfig().then(setLlmConfig).catch(() => {});
  }, [modelSettingsOpen]);

  useEffect(() => {
    api.llmConfig().then(setLlmConfig).catch(() => {});
  }, []);

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

  // 公司名统一显示：viewCompanies 的 name 已按全局偏好替换（原始英文名保留在 name_en 供搜索/创建回退）
  const viewCompanies = useMemo(
    () =>
      companies.map((company) => ({
        ...company,
        name_en: company.name,
        name: fmtName(company, namePref),
      })),
    [companies, namePref],
  );

  const researchCompanyOptions = useMemo(() => {
    const options = new Map<string, { value: string; label: string }>();
    [...companyCatalog, ...viewCompanies].forEach((company) => {
      const key = `${company.market}:${company.symbol}`;
      options.set(key, {
        value: key,
        label: `${fmtName(company, namePref)}（${company.symbol} · ${company.market}）`,
      });
    });
    return [...options.values()];
  }, [viewCompanies, companyCatalog, namePref]);

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
      .then((status) => {
        setNamePref(status.company_name_display || "zh");
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
        }));
      })
      .catch(() => undefined);
  }, []);
  const visibleCompanies = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return viewCompanies;
    return viewCompanies.filter((company) => {
      const key = `${company.market}:${company.symbol}`;
      const sessions = companySessions[key] || [];
      return (
        [company.name, company.name_en, company.name_zh || "", company.symbol, company.market].some((value) =>
          value.toLowerCase().includes(query),
        ) ||
        sessions.some((session) =>
          `${session.title} ${session.last_event_preview || ""}`
            .toLowerCase()
            .includes(query),
        )
      );
    });
  }, [viewCompanies, companySessions, searchQuery]);

  useEffect(() => {
    if (!searchOpen) { setSearchResults([]); return; }
    const query = searchQuery.trim();
    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (query) {
        api.search(query).then(results => { if (!cancelled) setSearchResults(results); }).catch(() => { if (!cancelled) setSearchResults([]); });
      } else {
        Promise.all(viewCompanies.flatMap(company => (company.project_ids?.length ? company.project_ids : [company.id]).map(projectId => api.sessions(projectId).then(sessions => sessions.map(session => ({ session, company: { id: company.id, name: company.name, name_zh: company.name_zh, symbol: company.symbol, market: company.market } }))))))
          .then(groups => { if (!cancelled) setSearchResults(groups.flat().sort((a, b) => new Date(b.session.latest_event_at).getTime() - new Date(a.session.latest_event_at).getTime()).slice(0, 20)); })
          .catch(() => { if (!cancelled) setSearchResults([]); });
      }
    }, 180);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [searchOpen, searchQuery, viewCompanies]);

  useEffect(() => {
    if (!panelOpen || !panelCompany) return;
    void loadPanel(panelCompany);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panelOpen, panelCompany?.symbol, panelCompany?.market]);

  useEffect(() => {
    if (!panelOpen) return;
    api
      .trustedHosts()
      .then((data) => setTrustedHosts(data.hosts || []))
      .catch(() => setTrustedHosts([]));
  }, [panelOpen]);

  useEffect(() => {
    if (!panelOpen || panelTab !== "news" || !panelCompany || newsItems) return;
    void loadNews(panelCompany);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panelOpen, panelTab, panelCompany, newsItems]);

  async function loadNews(
    company: { name: string; symbol: string } | null,
    query?: string,
  ) {
    if (!company) return;
    setNewsLoading(true);
    try {
      const items = await api.news(
        company.name,
        company.symbol,
        query?.trim() || undefined,
      );
      setNewsItems(items);
      setVisibleNews(15);
    } catch {
      setNewsItems([]);
    } finally {
      setNewsLoading(false);
    }
  }

  async function submitNewsSearch() {
    if (!panelCompany || newsLoading) return;
    const q = newsQuery.trim();
    setNewsSearchActive(!!q);
    await loadNews(panelCompany, q || undefined);
  }

  async function clearNewsSearch() {
    if (!panelCompany || newsLoading) return;
    setNewsQuery("");
    setNewsSearchActive(false);
    await loadNews(panelCompany);
  }

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

  async function loadPanel(
    company: { name: string; symbol: string; market: string } | null,
  ) {
    if (!company) {
      setPanelData(null);
      setPanelQuote(null);
      return;
    }
    setPanelLoading(true);
    try {
      const [panel, quote] = await Promise.all([
        api.companyPanel(company.symbol, company.market),
        api.quote(company.symbol, company.market).catch(() => null),
      ]);
      setPanelData(panel);
      setPanelQuote(quote);
    } catch {
      setPanelData(null);
      setPanelQuote(null);
    } finally {
      setPanelLoading(false);
    }
  }

  async function openReport(reportId: string) {
    setReportLoadingId(reportId);
    try {
      const report = await api.report(reportId);
      setReportViewer(report);
    } catch (err) {
      setError(err instanceof Error ? err.message : "报告加载失败");
    } finally {
      setReportLoadingId(null);
    }
  }

  async function openArticle(item: NewsItem) {
    setArticleViewer({ url: item.url, title: item.title });
    setArticleReader(null);
    setArticleError("");
    setArticleLoading(true);
    try {
      const reader = await api.article(item.url);
      setArticleReader(reader);
    } catch (err) {
      setArticleError(err instanceof Error ? err.message : "正文抓取失败");
    } finally {
      setArticleLoading(false);
    }
  }

  async function addSource() {
    if (!panelCompany || !newSourceUrl.trim()) return;
    setSourceBusy(true);
    try {
      const updated = await api.addCompanySource(
        panelCompany.symbol,
        panelCompany.market,
        newSourceUrl.trim(),
      );
      setPanelData((prev) =>
        prev ? { ...prev, sources: updated.sources } : updated,
      );
      setNewSourceUrl("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "资料源登记失败");
    } finally {
      setSourceBusy(false);
    }
  }

  async function removeSource(sourceId: string) {
    try {
      await api.removeCompanySource(sourceId);
      setPanelData((prev) =>
        prev
          ? {
              ...prev,
              sources: (prev.sources || []).filter(
                (item) => item.id !== sourceId,
              ),
            }
          : prev,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "资料源移除失败");
    }
  }

  async function addTrustedHost() {
    const host = newTrustedHost.trim();
    if (!host) return;
    setHostBusy(true);
    try {
      const updated = await api.addTrustedHost(host);
      setTrustedHosts(updated.hosts || []);
      setNewTrustedHost("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "白名单添加失败");
    } finally {
      setHostBusy(false);
    }
  }

  async function removeTrustedHost(id: number) {
    try {
      await api.removeTrustedHost(id);
      const listing = await api.trustedHosts();
      setTrustedHosts(listing.hosts || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "白名单移除失败");
    }
  }

  async function removeCompanyConfirmed(company: Company) {
    modal.confirm({
      title: `移除「${company.name}」？`,
      content:
        "将永久删除该公司的全部会话、研究报告和资料，操作不可恢复。",
      okText: "移除",
      okType: "danger",
      cancelText: "取消",
      onOk: async () => {
        try {
          await api.removeCompany(company.symbol, company.market);
          const key = `${company.market}:${company.symbol}`;
          if (selectedCompanyKey === key) {
            setSessionId(null);
            setMessages([]);
            setCompanyName("");
            setSelectedCompanyKey(null);
            setPendingJob(null);
            setProgressRun(undefined);
            setPanelOpen(false);
            setPanelCompany(null);
            setPanelData(null);
            setPanelQuote(null);
          }
          setCompanySessions((prev) => ({ ...prev, [key]: [] }));
          await refreshCompanies();
        } catch (err) {
          setError(err instanceof Error ? err.message : "移除失败");
        }
      },
    });
  }

  async function openSession(
    session: Session,
    companyNameValue: string,
    companyKey?: string,
  ) {
    setError("");
    setCompanyName(companyNameValue);
    if (companyKey) setSelectedCompanyKey(companyKey);
    const [market, symbol] = (companyKey || "").split(":");
    if (market && symbol) {
      setPanelCompany({ name: companyNameValue, symbol, market });
      setNewsItems(null);
      setNewsQuery("");
      setNewsSearchActive(false);
      setNewReportBadge(false);
    }
    const detail = await api.session(session.id);
    threadPinnedRef.current = true;
    setSessionId(detail.session.id);
    setMessages(cleanMessages(detail.messages));
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
    await openSession(active, company.name, key);
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
          if (!stopped) {
            setNewReportBadge(true);
            if (panelCompany) void loadPanel(panelCompany);
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
    const modelReady = llmConfig
      ? Boolean(llmConfig.selection && llmConfig.selection.has_api_key)
      : Boolean(modelSettings.api_key_configured || modelSettings.api_key?.trim());
    if (!modelReady) {
      setError("尚未配置真实模型，请打开‘设置 → 模型接入’选择模型并配置 API Key。");
      return;
    }
    submittingRef.current = true;
    threadPinnedRef.current = true;
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
      // 模型与强度档位由后端存储的选择决定（llm.selection），前端不再逐请求携带密钥。
      const result = sessionId
        ? await api.message(sessionId, text)
        : await api.chat({
            name: settings.name,
            symbol: settings.symbol,
            content: text,
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
        setNewReportBadge(true);
        if (panelCompany) void loadPanel(panelCompany);
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
    <ConfigProvider
      theme={{
        token: ANTD_THEME_TOKENS[theme],
        algorithm:
          theme === "dark"
            ? antdTheme.darkAlgorithm
            : antdTheme.defaultAlgorithm,
      }}
    >
    <AntdApp>
    <div className="app-shell" style={shellStyle}>
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
                onRemoveCompany={(company) => removeCompanyConfirmed(company)}
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
      {!sidebarCollapsed && (
        <div
          className="drag-handle drag-handle-left"
          title="拖拽调整宽度，双击复位"
          onPointerDown={(e) => beginResize("left", e)}
          onDoubleClick={() => setSidebarWidth(DEFAULT_SIDEBAR_WIDTH)}
        />
      )}
      <main className="main-shell">
        <header className="topbar">
          <h1>{companyName || "开始一段研究"}</h1>
          <div className="topbar-actions">
            <button
              className="topbar-button panel-toggle"
              onClick={() => {
                setPanelOpen((value) => !value);
                setNewReportBadge(false);
              }}
            >
              <Badge dot={newReportBadge} color="#c2410c" offset={[-3, 3]}>
                <span className="panel-toggle-inner">
                  <FileText size={14} /> 公司详情
                </span>
              </Badge>
            </button>
          </div>
        </header>
        <div
          className="thread-scroll"
          ref={threadScrollRef}
          onScroll={handleThreadScroll}
        >
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
          className={`composer-wrap ${panelOpen ? "panel-open" : ""}`}
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
            <div className="composer-bar">
              <ApprovalPicker config={llmConfig} onConfigChange={setLlmConfig} />
              <div className="composer-bar-right">
                <ModelPicker
                  config={llmConfig}
                  onConfigChange={setLlmConfig}
                  onOpenSettings={openSettings}
                />
                <button
                  type="submit"
                  className="send"
                  disabled={busy || !!pendingJob || !input.trim()}
                  aria-label="发送"
                >
                  <ArrowUp size={16} />
                </button>
              </div>
            </div>
          </form>
        </div>
      </main>
      {panelOpen && (
        <>
          <div
            className="drag-handle drag-handle-right"
            title="拖拽调整宽度，双击复位"
            onPointerDown={(e) => beginResize("right", e)}
            onDoubleClick={() => setPanelWidth(DEFAULT_PANEL_WIDTH)}
          />
          <aside className="company-panel">
          <div className="report-panel-heading">
            <div>
              <strong>{panelCompany ? fmtName(panelCompany, namePref) : companyName || "公司详情"}</strong>
              <small>
                {panelCompany
                  ? `${panelCompany.symbol} · ${panelCompany.market}`
                  : "选择公司后展示详情"}
              </small>
            </div>
            <button onClick={() => setPanelOpen(false)} aria-label="收起档案">
              <ChevronRight size={16} />
            </button>
          </div>
          <div
            className="company-panel-body"
            onScroll={(e) => {
              const el = e.currentTarget;
              if (
                newsItems &&
                visibleNews < newsItems.length &&
                el.scrollTop + el.clientHeight >= el.scrollHeight - 140
              ) {
                setVisibleNews((count) =>
                  Math.min(count + 15, newsItems.length),
                );
              }
            }}
          >
            <Tabs
              activeKey={panelTab}
              onChange={(key) => setPanelTab(key)}
              size="small"
              items={[
                {
                  key: "overview",
                  label: "概览",
                  children: (
                    <div className="panel-section">
                      <div className="quote-card">
                        {panelQuote?.available ? (
                          <>
                            <div className="quote-row">
                              <strong className="quote-last">
                                {panelQuote.last?.toLocaleString()}
                              </strong>
                              <span
                                className={`quote-change ${
                                  (panelQuote.change ?? 0) >= 0 ? "up" : "down"
                                }`}
                              >
                                {panelQuote.change != null
                                  ? `${panelQuote.change >= 0 ? "+" : ""}${panelQuote.change}`
                                  : "-"}
                                {panelQuote.change_pct != null
                                  ? ` (${panelQuote.change_pct >= 0 ? "+" : ""}${(panelQuote.change_pct * 100).toFixed(2)}%)`
                                  : ""}
                              </span>
                            </div>
                            <div className="quote-meta">
                              <span>
                                52周 {panelQuote.low_52w?.toLocaleString()} ~{" "}
                                {panelQuote.high_52w?.toLocaleString()}{" "}
                                {panelQuote.currency}
                              </span>
                              <span>截至 {panelQuote.as_of}</span>
                            </div>
                            <div className="quote-note">
                              延迟行情（Yahoo 原型数据，仅供参考）
                            </div>
                          </>
                        ) : (
                          <div className="panel-empty">行情快照暂不可用</div>
                        )}
                      </div>
                      <div className="panel-kv">
                        <span>公司</span>
                        <strong>{panelCompany?.name || "-"}</strong>
                        <span>代码</span>
                        <strong>
                          {panelCompany
                            ? `${panelCompany.symbol} · ${panelCompany.market}`
                            : "-"}
                        </strong>
                        <span>资料</span>
                        <strong>{panelData?.documents?.length ?? "-"} 份</strong>
                        <span>报告</span>
                        <strong>{panelData?.reports?.length ?? "-"} 份</strong>
                      </div>
                    </div>
                  ),
                },
                {
                  key: "sources",
                  label: `资料${panelData?.sources?.length ? ` (${panelData.sources.length})` : ""}`,
                  children: (
                    <div className="panel-section">
                      <div className="panel-subtitle">登记资料源</div>
                      <div className="source-add">
                        <input
                          value={newSourceUrl}
                          placeholder="https://www1.hkexnews.hk/..."
                          onChange={(e) => setNewSourceUrl(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              void addSource();
                            }
                          }}
                        />
                        <Select
                          size="small"
                          value={newSourceClass}
                          onChange={(value) => setNewSourceClass(value)}
                          style={{ width: 76 }}
                          options={[
                            { value: "private", label: "私有" },
                            { value: "public", label: "公有" },
                          ]}
                        />
                        <button
                          disabled={
                            sourceBusy || !panelCompany || !newSourceUrl.trim()
                          }
                          onClick={() => void addSource()}
                        >
                          {sourceBusy ? "登记中…" : "登记"}
                        </button>
                      </div>
                      <div className="panel-hint">
                        私有来源视为已人工确认；公有来源按可信域名白名单判定。
                      </div>
                      {(panelData?.sources?.length ?? 0) > 0 ? (
                        panelData!.sources!.map((source) => (
                          <div className="source-item" key={source.id}>
                            <FileText size={13} />
                            <a
                              className="doc-title"
                              href={source.url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              {source.title || source.url}
                            </a>
                            <TrustBadge
                              trust={
                                source.source_class === "public"
                                  ? "unverified"
                                  : "verified"
                              }
                            />
                            <button
                              className="source-remove"
                              aria-label="移除资料源"
                              disabled={sourceBusy}
                              onClick={() => void removeSource(source.id)}
                            >
                              ×
                            </button>
                          </div>
                        ))
                      ) : (
                        <div className="panel-empty">
                          暂无登记资料源。粘贴 HKEX/公司 IR 链接并登记。
                        </div>
                      )}
                      <div className="panel-subtitle">
                        已归档文档（{panelData?.documents?.length ?? 0}）
                      </div>
                      {(panelData?.documents?.length ?? 0) > 0 ? (
                        panelData!.documents!.map((doc) => (
                          <a
                            className="doc-item"
                            key={doc.id}
                            href={doc.source_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <FileText size={13} />
                            <span className="doc-title">{doc.title}</span>
                            <TrustBadge trust={doc.trust} />
                            <small>
                              {doc.published_at
                                ? doc.published_at.slice(0, 10)
                                : ""}
                            </small>
                          </a>
                        ))
                      ) : (
                        <div className="panel-empty">
                          暂无归档文档。研究完成后抓取的资料会沉淀在这里。
                        </div>
                      )}
                      <details className="host-whitelist">
                        <summary className="panel-subtitle">
                          可信域名白名单（{trustedHosts.length}）
                        </summary>
                        <div className="panel-hint">
                          白名单域名的公有资料标记为「可信」；仅影响之后入库的资料。
                        </div>
                        <div className="source-add">
                          <input
                            value={newTrustedHost}
                            placeholder="example.com"
                            onChange={(e) => setNewTrustedHost(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                e.preventDefault();
                                void addTrustedHost();
                              }
                            }}
                          />
                          <button
                            disabled={hostBusy || !newTrustedHost.trim()}
                            onClick={() => void addTrustedHost()}
                          >
                            {hostBusy ? "添加中…" : "添加"}
                          </button>
                        </div>
                        {trustedHosts.map((host) => (
                          <div className="source-item" key={host.id}>
                            <span className="doc-title">{host.host}</span>
                            <button
                              className="source-remove"
                              aria-label="移除白名单域名"
                              disabled={hostBusy}
                              onClick={() => void removeTrustedHost(host.id)}
                            >
                              ×
                            </button>
                          </div>
                        ))}
                      </details>
                    </div>
                  ),
                },
                {
                  key: "reports",
                  label: `报告${panelData?.reports?.length ? ` (${panelData.reports.length})` : ""}`,
                  children: (
                    <div className="panel-section">
                      {panelLoading ? (
                        <div className="panel-empty">正在加载…</div>
                      ) : (panelData?.reports?.length ?? 0) > 0 ? (
                        panelData!.reports!.map((item) => (
                          <button
                            className="report-item"
                            key={item.id}
                            disabled={reportLoadingId === item.id}
                            onClick={() => void openReport(item.id)}
                          >
                            <FileText size={13} />
                            <span className="report-item-title">
                              {item.question || "研究报告"}
                            </span>
                            <small>
                              {item.created_at?.slice(0, 10)} · v{item.version}
                            </small>
                          </button>
                        ))
                      ) : (
                        <div className="panel-empty">
                          暂无研究报告。在对话中说「研究这家公司」即可生成。
                        </div>
                      )}
                    </div>
                  ),
                },
                {
                  key: "news",
                  label: "动态",
                  children: (
                    <div className="panel-section">
                      <div className="news-search">
                        <Search size={13} />
                        <input
                          value={newsQuery}
                          placeholder="在本公司新闻中搜索，回车确认…"
                          onChange={(e) => setNewsQuery(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void submitNewsSearch();
                          }}
                        />
                        {newsQuery.trim() && (
                          <button
                            className="news-search-clear"
                            aria-label="清除搜索"
                            disabled={newsLoading}
                            onClick={() => void clearNewsSearch()}
                          >
                            ×
                          </button>
                        )}
                      </div>
                      {newsSearchActive && (
                        <div className="news-search-hint">
                          「{newsQuery}」的搜索结果
                        </div>
                      )}
                      {newsLoading ? (
                        <div className="panel-empty">正在加载动态…</div>
                      ) : (newsItems?.length ?? 0) > 0 ? (
                        <>
                          {newsItems!.slice(0, visibleNews).map((item, index) => (
                            <button
                              className="news-card"
                              key={`${item.url}-${index}`}
                              onClick={() => {
                                if (item.external) {
                                  window.open(item.url, "_blank", "noopener");
                                  return;
                                }
                                void openArticle(item);
                              }}
                            >
                              {item.external ? (
                                <span className="news-external-tag">
                                  <ExternalLink size={10} /> 站外
                                </span>
                              ) : (
                                <TrustBadge trust={item.trust} />
                              )}
                              <span className="news-title">{item.title}</span>
                              <span className="news-meta">
                                {item.source && (
                                  <span className="news-chip">{item.source}</span>
                                )}
                                <span className="news-time">
                                  {relativeTime(item.time)}
                                </span>
                              </span>
                            </button>
                          ))}
                          <div className="news-load-more">
                            {visibleNews < newsItems!.length
                              ? `下滑加载更多（已显示 ${visibleNews}/${newsItems!.length} 条）`
                              : `已加载全部 ${newsItems!.length} 条`}
                          </div>
                        </>
                      ) : (
                        <div className="panel-empty">
                          {newsSearchActive
                            ? "没有找到相关新闻，换个关键词试试"
                            : "暂无相关动态"}
                        </div>
                      )}
                    </div>
                  ),
                },
              ]}
            />
          </div>
        </aside>
        </>
      )}
      <Modal
        open={!!reportViewer}
        onCancel={() => setReportViewer(null)}
        footer={null}
        width={880}
        title={reportViewer ? `${(viewCompanies.find(item => item.symbol === reportViewer.company.symbol && item.market === (reportViewer.company as { market?: string }).market)?.name) || reportViewer.company.name} 研究报告` : "研究报告"}
      >
        {reportViewer && (
          <ReportCard
            key={reportViewer.report_id}
            report={reportViewer}
            onUpdated={setReportViewer}
          />
        )}
      </Modal>
      <Modal
        open={!!articleViewer}
        onCancel={() => setArticleViewer(null)}
        footer={null}
        width={760}
        title={articleViewer?.title || "新闻阅读"}
      >
        <div className="article-reader">
          <div className="article-actions">
            <button
              className="article-open"
              disabled={!articleViewer}
              onClick={() =>
                articleViewer &&
                window.open(articleViewer.url, "_blank", "noopener")
              }
            >
              <ExternalLink size={13} /> 在新标签页打开原文
            </button>
          </div>
          {articleLoading ? (
            <div className="panel-empty">正在抓取正文…</div>
          ) : articleError ? (
            <div className="panel-empty">
              {articleError}
              <br />
              可以点击上方按钮直接访问原文。
            </div>
          ) : (
            articleReader && (
              <div className="article-text">{articleReader.text}</div>
            )
          )}
        </div>
      </Modal>
      {searchOpen && (
        <div className="search-overlay" role="dialog" aria-modal="true" aria-label="搜索会话" onMouseDown={() => setSearchOpen(false)}>
          <div className="search-dialog" onMouseDown={event => event.stopPropagation()}>
            <div className="search-dialog-input"><Search size={20} /><input autoFocus value={searchQuery} onChange={event => setSearchQuery(event.target.value)} onKeyDown={event => { if (event.key === "Escape") setSearchOpen(false); if (event.key === "Enter" && searchResults[0]) { const result = searchResults[0]; const company = companies.find(item => item.symbol === result.company.symbol && item.market === result.company.market); setSearchOpen(false); if (company) void openSession(result.session, fmtName(result.company, namePref), `${result.company.market}:${result.company.symbol}`); } }} placeholder="搜索会话内容、标题…" /></div>
            <div className="search-dialog-body">
              <div className="search-dialog-label">{searchQuery.trim() ? "搜索结果" : "最近会话"}</div>
              {searchResults.length ? searchResults.map(result => {
                const company = companies.find(item => item.symbol === result.company.symbol && item.market === result.company.market);
                return <button className="search-dialog-result" key={result.session.id} onClick={() => { setSearchOpen(false); if (company) void openSession(result.session, fmtName(result.company, namePref), `${result.company.market}:${result.company.symbol}`); }}><MessageSquare size={16} /><span className="search-dialog-result-copy"><strong>{result.session.title || "新研究"}</strong><small>{fmtName(result.company, namePref)} · {result.company.symbol}{result.session.last_event_preview ? ` · ${result.session.last_event_preview}` : ""}</small></span><span className="search-dialog-result-key">↵</span></button>;
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
            <button
              className="modal-close settings-close"
              onClick={() => setModelSettingsOpen(false)}
              aria-label="关闭设置"
            >
              ×
            </button>
            <div className="settings-sidebar">
              <div className="settings-sidebar-title">
                <h2>设置</h2>
              </div>
              <nav className="settings-nav" aria-label="设置分类">
                <button
                  className={settingsSection === "model" ? "active" : ""}
                  type="button"
                  onClick={() => setSettingsSection("model")}
                >
                  <Server size={14} />
                  模型接入
                </button>
                <button
                  className={settingsSection === "appearance" ? "active" : ""}
                  type="button"
                  onClick={() => setSettingsSection("appearance")}
                >
                  <Palette size={14} />
                  外观
                </button>
              </nav>
            </div>
            <div className="settings-main">
              {settingsSection === "appearance" ? (
                <>
                  <header className="settings-section-head">
                    <h3>外观</h3>
                    <p>切换亮色与暗色皮肤，调整公司名称显示；立即生效并记住选择。</p>
                  </header>
                  <div className="settings-card appearance-card">
                    <div className="appearance-options">
                      <button
                        type="button"
                        className={`appearance-option ${theme === "light" ? "active" : ""}`}
                        onClick={() => setTheme("light")}
                      >
                        <span className="appearance-swatch appearance-swatch-light" />
                        <span>亮色</span>
                      </button>
                      <button
                        type="button"
                        className={`appearance-option ${theme === "dark" ? "active" : ""}`}
                        onClick={() => setTheme("dark")}
                      >
                        <span className="appearance-swatch appearance-swatch-dark" />
                        <span>暗色</span>
                      </button>
                    </div>
                  </div>
                  <div className="settings-card display-pref-card">
                    <div className="display-pref-row">
                      <div className="display-pref-copy">
                        <strong>公司名称显示</strong>
                        <small>侧栏、报告标题与新研究下拉统一生效；中文名来自 HKEX 官方名录，缺失时回退英文名。</small>
                      </div>
                      <Segmented
                        value={namePref}
                        onChange={(value) => {
                          const next = value as NameDisplayPref;
                          const previous = namePref;
                          setNamePref(next);
                          api.setDisplayNamePref(next).catch(() => {
                            setNamePref(previous);
                            setError("显示偏好保存失败");
                          });
                        }}
                        options={[
                          { label: "中文名优先", value: "zh" },
                          { label: "英文名优先", value: "en" },
                          { label: "双语", value: "bilingual" },
                        ]}
                      />
                    </div>
                  </div>
                </>
              ) : (
                <ModelsSection config={llmConfig} onConfigChange={setLlmConfig} />
              )}
            </div>
          </div>
        </div>
      )}
      {/* 首启引导（deepseek-harness settings.onboarding 语义）：尚无可用
          提供方时提示配置 DeepSeek 官方密钥。 */}
      <DeepSeekOnboardingDialog config={llmConfig} onConfigChange={setLlmConfig} />
    </div>
    </AntdApp>
    </ConfigProvider>
  );
}
