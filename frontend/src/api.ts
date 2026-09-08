export type NameDisplayPref = "zh" | "en" | "bilingual";
export type Company = { id: string; project_ids?: string[]; name: string; name_en?: string; name_zh?: string | null; symbol: string; market: string; latest_event_preview?: string; session_count: number };
export type CompanyCatalogEntry = { name: string; name_zh?: string | null; symbol: string; market: string };
export type Session = { id: string; project_id: string; title: string; status: string; latest_event_at: string; last_event_preview?: string };
export type SearchResult = { session: Session; company: { id: string; name: string; name_zh?: string | null; symbol: string; market: string } ; matched_text?: string };
export type Message = { id: string; role: string; message_type: string; content: { text?: string; summary?: string[]; report_id?: string; tool?: string; args?: Record<string, unknown>; observation?: string; citations?: { ref?: string; title?: string; url?: string; trust?: string; observation?: string }[]; observation_refs?: string[] }; created_at: string };
export type RunStep = { step_key: string; order: number; status: string; attempt: number; output?: Record<string, unknown> };
export type Run = { id: string; status: string; question: string; as_of_date: string; steps?: RunStep[] };
export type SessionDetail = { session: Session; messages: Message[]; runs: Run[]; jobs?: Job[] };
export type Job = { id: string; session_id: string; run_id?: string; status: "queued" | "running" | "completed" | "failed" | "canceled" | string; attempts: number; error?: { message?: string } };
export type Report = { report_id: string; company: { name: string; symbol: string }; summary: string[]; markdown: string; facts: Fact[]; calculations: Calculation[]; recalculated_from?: string; version?: number; diff?: ReportDiff; update_of?: string };
export type ReportDiff = { previous_report_id?: string; new_facts?: Fact[]; changed_facts?: { metric: string; period_end?: string | null; previous_value: number; current_value: number }[]; valuation?: { previous: number; current: number; change_pct: number } | null; conclusion_changed?: boolean };
export type Fact = { metric: string; value: number; currency?: string; period_end?: string; confidence: number; citation: { source_line: number; raw_text: string; page?: number; source_url?: string; trust?: string | null } };
export type SourceTrust = "verified" | "whitelist" | "unverified" | string;
export type Calculation = { calculation_type: string; inputs: Record<string, unknown>; outputs: Record<string, number>; };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "请求失败");
  return body;
}
export const api = {
  companies: () => request<Company[]>("/api/companies"),
  companyCatalog: () => request<CompanyCatalogEntry[]>("/api/company-catalog"),
  removeCompany: (symbol: string, market: string) => request<{ ok: boolean; deleted: Record<string, number> }>(`/api/companies/${encodeURIComponent(symbol)}?market=${encodeURIComponent(market)}`, { method: "DELETE" }),
  search: (query: string) => request<SearchResult[]>(`/api/search?q=${encodeURIComponent(query)}`),
  sessions: (projectId: string) => request<Session[]>(`/api/projects/${projectId}/sessions`),
  createProject: (name: string, symbol: string, market = "HK") => request<{ project: { id: string; name: string; symbol: string; market: string }; session: Session }>("/api/projects", { method: "POST", body: JSON.stringify({ name, symbol, market }) }),
  createSession: (projectId: string, title = "新研究") => request<{ session_id: string; session: Session }>(`/api/projects/${projectId}/sessions`, { method: "POST", body: JSON.stringify({ title }) }),
  session: (id: string) => request<SessionDetail>(`/api/sessions/${id}`),
  chat: (payload: Record<string, unknown>) => request<ChatResult>("/api/chat", { method: "POST", body: JSON.stringify(payload) }),
  message: (id: string, content: string, llm?: ModelSettings) => request<ChatResult>(`/api/sessions/${id}/messages`, { method: "POST", body: JSON.stringify({ content, llm }) }),
  messageStream: (id: string, content: string, onEvent?: (event: AgentStreamEvent) => void) => streamChat(`/api/sessions/${id}/messages/stream`, { content }, onEvent),
  chatStream: (payload: Record<string, unknown>, onEvent?: (event: AgentStreamEvent) => void) => streamChat("/api/chat/stream", payload, onEvent),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  run: (id: string) => request<{ artifacts: { reports: { id: string }[] } }>(`/api/runs/${id}`),
  report: (id: string) => request<Report>(`/api/reports/${id}`),
  recalculateReport: (reportId: string, assumptions: Record<string, number | number[]>) => request<Report>(`/api/reports/${reportId}/recalculate`, { method: "POST", body: JSON.stringify({ dcf_assumptions: assumptions }) }),
  companyPanel: (symbol: string, market: string) => request<CompanyPanel>(`/api/company-panel?symbol=${encodeURIComponent(symbol)}&market=${encodeURIComponent(market)}`),
  addCompanySource: (symbol: string, market: string, url: string, title?: string, sourceClass?: string) => request<CompanyPanel>(`/api/company-panel/sources`, { method: "POST", body: JSON.stringify({ symbol, market, url, title, source_class: sourceClass || "private" }) }),
  removeCompanySource: (sourceId: string) => request<{ ok: boolean }>(`/api/company-panel/sources/${sourceId}`, { method: "DELETE" }),
  trustedHosts: () => request<TrustedHostList>("/api/trusted-hosts"),
  addTrustedHost: (host: string, label?: string) => request<TrustedHostList & { host: TrustedHost }>("/api/trusted-hosts", { method: "POST", body: JSON.stringify({ host, label }) }),
  removeTrustedHost: (id: number) => request<{ ok: boolean }>(`/api/trusted-hosts/${id}`, { method: "DELETE" }),
  quote: (symbol: string, market: string) => request<Quote>(`/api/quote/${encodeURIComponent(symbol)}?market=${encodeURIComponent(market)}`),
  news: (name: string, symbol: string, q?: string) => request<NewsItem[]>(`/api/news?name=${encodeURIComponent(name)}&symbol=${encodeURIComponent(symbol)}${q ? `&q=${encodeURIComponent(q)}` : ""}`),
  article: (url: string) => request<ArticleReader>(`/api/article?url=${encodeURIComponent(url)}`),
  settings: () => request<ModelSettings>('/api/settings'),
  saveSettings: (settings: ModelSettings) => request<ModelSettings>('/api/settings', { method: 'POST', body: JSON.stringify(settings) }),
  llmConfig: () => request<LlmConfig>('/api/llm/config'),
  saveLlmProvider: (payload: { id?: number; name?: string; route?: string; protocol?: string; base_url?: string; api_key?: string; preset?: string }) => request<{ ok?: boolean; provider: LlmProvider; config: LlmConfig }>('/api/llm/providers', { method: 'POST', body: JSON.stringify(payload) }),
  removeLlmProvider: (id: number) => request<{ ok: boolean; config: LlmConfig }>(`/api/llm/providers/${id}`, { method: 'DELETE' }),
  syncLlmModels: (payload: { provider_id: number; models: LlmModelDraft[] }) => request<{ ok: boolean; config: LlmConfig }>('/api/llm/models/batch', { method: 'POST', body: JSON.stringify(payload) }),
  discoverLlmModels: (payload: { provider_id?: number; base_url?: string; api_key?: string }) => request<{ models: { model_id: string; added: boolean }[] }>('/api/llm/models/discover', { method: 'POST', body: JSON.stringify(payload) }),
  setLlmSelection: (payload: { model_row_id: number; level?: string }) => request<{ ok: boolean; config: LlmConfig }>('/api/llm/selection', { method: 'POST', body: JSON.stringify(payload) }),
  setLlmApproval: (mode: ApprovalMode) => request<{ ok: boolean; config: LlmConfig }>('/api/llm/approval', { method: 'POST', body: JSON.stringify({ mode }) }),
  setDisplayNamePref: (display: NameDisplayPref) => request<{ ok: boolean; company_name_display: NameDisplayPref }>('/api/settings/display', { method: 'POST', body: JSON.stringify({ display }) }),
};

export type LlmProvider = { id: number; route: string; name: string; protocol: string; base_url: string; has_api_key: boolean; builtin: number; enabled: number };
export type LlmModelDraft = { model_id: string; display_name?: string | null; context_window?: number | null; max_tokens?: number | null };
export type LlmModel = { id: number; provider_id: number; model_id: string; display_name: string; thinking_levels: Record<string, Record<string, unknown>>; levels: string[]; default_level: string; context_window?: number | null; max_tokens?: number | null };
export type LlmCatalogEntry = { key: string; route: string; protocol: string; name: string; base_url: string; model_count: number; models: { model_id: string; display_name: string }[] };
export type LlmLevelOption = { value: string; label: string };
export type LlmSelectionEntry = { model_row_id: number; provider_id: number; provider_name: string; model_id: string; display_name: string; level: string; has_api_key: boolean };
export type LlmConfig = { providers: LlmProvider[]; models: LlmModel[]; catalog: LlmCatalogEntry[]; levels: LlmLevelOption[]; selection: LlmSelectionEntry | null; recent: LlmSelectionEntry[]; approval_mode: ApprovalMode };
export type ApprovalMode = "manual" | "auto" | "full";
export const APPROVAL_LABELS: Record<ApprovalMode, string> = { manual: "手动审批", auto: "自动审批", full: "完全访问" };

export type ChatResult = { type: string; session_id: string; message?: string; report?: Report; job_id?: string; run_id?: string; report_id?: string };
export type AgentStreamEvent = { type: string; ref?: string; tool?: string; args?: Record<string, unknown>; thought?: string; observation?: string };

/** 读 NDJSON 流式响应：每个 agent 步骤实时回调，最终返回 done 帧。 */
async function streamChat(path: string, body: unknown, onEvent?: (event: AgentStreamEvent) => void): Promise<ChatResult> {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok || !response.body) {
    const err = await response.json().catch(() => ({}) as { error?: string });
    throw new Error((err as { error?: string }).error || "请求失败");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let final: ChatResult | null = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      let frame: { event: string; data: unknown };
      try {
        frame = JSON.parse(line);
      } catch {
        continue;
      }
      if (frame.event === "agent_event") onEvent?.(frame.data as AgentStreamEvent);
      else if (frame.event === "done") final = frame.data as ChatResult;
      else if (frame.event === "error") throw new Error((frame.data as { message?: string })?.message || "处理失败");
    }
  }
  if (!final) throw new Error("连接中断，未收到完整回复");
  return final;
}
export type ModelSettings = { provider: string; base_url: string; model: string; api_key?: string; llm_enabled?: boolean; api_key_configured?: boolean; company_name_display?: NameDisplayPref };
export type Quote = { available: boolean; symbol: string; delayed: boolean; currency?: string; last?: number; change?: number | null; change_pct?: number | null; high_52w?: number; low_52w?: number; as_of?: string };
export type PanelSource = { id: string; url: string; title?: string | null; created_at: string; source_class?: string | null };
export type PanelDocument = { id: string; source_type: string; source_url: string; title: string; published_at?: string | null; source_class?: string | null; trust?: string | null };
export type PanelReport = { id: string; run_id: string; version: number; created_at: string; run_status?: string; question?: string };
export type CompanyPanel = { available: boolean; project?: { id: string; name: string; name_zh?: string | null; symbol: string; market: string }; sources?: PanelSource[]; documents?: PanelDocument[]; reports?: PanelReport[] };

/** 按全局偏好计算公司显示名；缺失时回退另一语言名，最后回退代码。 */
export function fmtName(company: { name: string; name_zh?: string | null; symbol?: string }, pref: NameDisplayPref): string {
  const zh = company.name_zh?.trim();
  const en = company.name?.trim();
  if (pref === "en") return en || zh || company.symbol || "";
  if (pref === "bilingual") {
    if (zh && en && zh !== en) return `${zh} · ${en}`;
    return zh || en || company.symbol || "";
  }
  return zh || en || company.symbol || "";
}
export type NewsItem = { title: string; url: string; source?: string; time?: string | null; external?: boolean; trust?: string | null };
export type TrustedHost = { id: number; host: string; label?: string | null; created_at: string };
export type TrustedHostList = { hosts: TrustedHost[] };
export type ArticleReader = { ok: boolean; url: string; text: string };
