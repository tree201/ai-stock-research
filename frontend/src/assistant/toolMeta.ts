/** 工具中文名映射（原 App.tsx TOOL_VERBS）。 */
export const TOOL_VERBS: Record<string, string> = {
  collect_filings: "收集公司资料",
  extract_financials: "抽取财务事实",
  review: "核对研究证据",
  compile_report: "编写研究报告",
  get_quote: "查询行情",
  search_news: "搜索新闻",
  fetch_filings: "抓取网页资料",
  get_financials: "查询财务数据",
  plan: "制定研究计划",
};

export const toolVerb = (tool: string) => TOOL_VERBS[tool] || tool;

/** 研究流水线步骤标签（原 App.tsx STEP_LABELS）。 */
export const STEP_LABELS: Record<string, string> = {
  collect_filings: "获取资料",
  extract_financials: "抽取财务事实",
  analyze_business: "分析业务",
  analyze_risks: "分析风险",
  calculate_valuation: "计算估值",
  review: "校验证据",
  compile_report: "编写报告",
};

/** plan 工具参数 → "获取资料 → 分析业务" 形式的计划链摘要。 */
export const planChain = (args?: Record<string, unknown>) => {
  const steps = Array.isArray(args?.steps)
    ? (args.steps as { key?: string; purpose?: string }[])
    : [];
  return steps
    .map((step) =>
      step.purpose?.trim() || (step.key ? STEP_LABELS[step.key] || step.key : ""),
    )
    .filter(Boolean)
    .join(" → ");
};
