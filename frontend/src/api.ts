export type Company = { id: string; project_ids?: string[]; name: string; symbol: string; market: string; latest_event_preview?: string; session_count: number };
export type CompanyCatalogEntry = { name: string; symbol: string; market: string };
export type Session = { id: string; project_id: string; title: string; status: string; latest_event_at: string; last_event_preview?: string };
export type SearchResult = { session: Session; company: { id: string; name: string; symbol: string; market: string } ; matched_text?: string };
export type Message = { id: string; role: string; message_type: string; content: { text?: string; summary?: string[]; report_id?: string }; created_at: string };
export type RunStep = { step_key: string; order: number; status: string; attempt: number; output?: Record<string, unknown> };
export type Run = { id: string; status: string; question: string; as_of_date: string; steps?: RunStep[] };
export type SessionDetail = { session: Session; messages: Message[]; runs: Run[]; jobs?: Job[] };
export type Job = { id: string; session_id: string; run_id?: string; status: "queued" | "running" | "completed" | "failed" | "canceled" | string; attempts: number; error?: { message?: string } };
export type Report = { report_id: string; company: { name: string; symbol: string }; summary: string[]; markdown: string; facts: Fact[]; calculations: Calculation[]; recalculated_from?: string; version?: number; diff?: ReportDiff; update_of?: string };
export type ReportDiff = { previous_report_id?: string; new_facts?: Fact[]; changed_facts?: { metric: string; period_end?: string | null; previous_value: number; current_value: number }[]; valuation?: { previous: number; current: number; change_pct: number } | null; conclusion_changed?: boolean };
export type Fact = { metric: string; value: number; currency?: string; period_end?: string; confidence: number; citation: { source_line: number; raw_text: string; page?: number; source_url?: string } };
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
  search: (query: string) => request<SearchResult[]>(`/api/search?q=${encodeURIComponent(query)}`),
  sessions: (projectId: string) => request<Session[]>(`/api/projects/${projectId}/sessions`),
  createProject: (name: string, symbol: string, market = "HK") => request<{ project: { id: string; name: string; symbol: string; market: string }; session: Session }>("/api/projects", { method: "POST", body: JSON.stringify({ name, symbol, market }) }),
  createSession: (projectId: string, title = "新研究") => request<{ session_id: string; session: Session }>(`/api/projects/${projectId}/sessions`, { method: "POST", body: JSON.stringify({ title }) }),
  session: (id: string) => request<SessionDetail>(`/api/sessions/${id}`),
  chat: (payload: Record<string, unknown>) => request<ChatResult>("/api/chat", { method: "POST", body: JSON.stringify(payload) }),
  message: (id: string, content: string, llm?: ModelSettings) => request<ChatResult>(`/api/sessions/${id}/messages`, { method: "POST", body: JSON.stringify({ content, llm }) }),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  run: (id: string) => request<{ artifacts: { reports: { id: string }[] } }>(`/api/runs/${id}`),
  report: (id: string) => request<Report>(`/api/reports/${id}`),
  recalculateReport: (reportId: string, assumptions: Record<string, number | number[]>) => request<Report>(`/api/reports/${reportId}/recalculate`, { method: "POST", body: JSON.stringify({ dcf_assumptions: assumptions }) }),
  companyPanel: (symbol: string, market: string) => request<CompanyPanel>(`/api/company-panel?symbol=${encodeURIComponent(symbol)}&market=${encodeURIComponent(market)}`),
  addCompanySource: (symbol: string, market: string, url: string, title?: string) => request<CompanyPanel>(`/api/company-panel/sources`, { method: "POST", body: JSON.stringify({ symbol, market, url, title }) }),
  removeCompanySource: (sourceId: string) => request<{ ok: boolean }>(`/api/company-panel/sources/${sourceId}`, { method: "DELETE" }),
  quote: (symbol: string, market: string) => request<Quote>(`/api/quote/${encodeURIComponent(symbol)}?market=${encodeURIComponent(market)}`),
  news: (name: string, symbol: string) => request<NewsItem[]>(`/api/news?name=${encodeURIComponent(name)}&symbol=${encodeURIComponent(symbol)}`),
  article: (url: string) => request<ArticleReader>(`/api/article?url=${encodeURIComponent(url)}`),
  settings: () => request<ModelSettings>('/api/settings'),
  saveSettings: (settings: ModelSettings) => request<ModelSettings>('/api/settings', { method: 'POST', body: JSON.stringify(settings) }),
};

export type ChatResult = { type: string; session_id: string; message?: string; report?: Report; job_id?: string; run_id?: string; report_id?: string };
export type ModelSettings = { provider: string; base_url: string; model: string; api_key?: string; llm_enabled?: boolean; api_key_configured?: boolean };
export type Quote = { available: boolean; symbol: string; delayed: boolean; currency?: string; last?: number; change?: number | null; change_pct?: number | null; high_52w?: number; low_52w?: number; as_of?: string };
export type PanelSource = { id: string; url: string; title?: string | null; created_at: string };
export type PanelDocument = { id: string; source_type: string; source_url: string; title: string; published_at?: string | null };
export type PanelReport = { id: string; run_id: string; version: number; created_at: string; run_status?: string; question?: string };
export type CompanyPanel = { available: boolean; project?: { id: string; name: string; symbol: string; market: string }; sources?: PanelSource[]; documents?: PanelDocument[]; reports?: PanelReport[] };
export type NewsItem = { title: string; url: string; source?: string; time?: string | null };
export type ArticleReader = { ok: boolean; url: string; text: string };
