import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { Check, ChevronDown, ChevronRight, Loader2, TriangleAlert, Wrench } from "lucide-react";
import { cn } from "./cn";
import { toolVerb } from "./toolMeta";

/**
 * 工具调用卡片：无 result 时转圈（运行中），有 result 后显示观测摘要，可展开原始参数。
 */
export function ToolCard({ toolName, args, result, isError, status }: ToolCallMessagePartProps) {
  const running = status.type === "running";
  const [open, setOpen] = useState(false);
  const argsText = args ? JSON.stringify(args, null, 2) : "";
  const resultText = typeof result === "string" ? result : result ? JSON.stringify(result, null, 2) : "";
  const failed = isError || (resultText ? resultText.startsWith("工具执行失败") : false);
  const truncated = resultText.length > 800;

  return (
    <div
      className={cn(
        "my-1.5 rounded-[10px] border border-[var(--line)] bg-[var(--surface)] text-[12px]",
        failed && "border-[var(--danger)]/40 bg-[var(--danger-soft)]"
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left bg-transparent text-[var(--ink)] hover:bg-[var(--surface-soft)] rounded-[10px] transition-colors"
      >
        {running ? (
          <Loader2 size={13} className="shrink-0 animate-spin text-[var(--signal)]" />
        ) : failed ? (
          <TriangleAlert size={13} className="shrink-0 text-[var(--danger)]" />
        ) : (
          <Check size={13} className="shrink-0 text-[var(--tag-ok-ink)]" />
        )}
        <Wrench size={12} className="shrink-0 text-[var(--ink-muted)]" />
        <span className="font-medium text-[var(--ink-strong)]">{toolVerb(toolName)}</span>
        <span className="ml-auto flex items-center gap-1 text-[11px] text-[var(--ink-muted)]">
          {running ? "执行中…" : failed ? "失败" : "完成"}
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-[var(--line)] px-3 py-2">
          {argsText && (
            <div>
              <div className="mb-1 text-[11px] text-[var(--ink-muted)]">参数</div>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-[var(--surface-dim)] p-2 font-mono text-[11px] text-[var(--ink-soft)]">
                {argsText}
              </pre>
            </div>
          )}
          {resultText && (
            <div>
              <div className="mb-1 text-[11px] text-[var(--ink-muted)]">观测</div>
              <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-all rounded-md bg-[var(--surface-dim)] p-2 font-mono text-[11px] text-[var(--ink-soft)]">
                {truncated ? `${resultText.slice(0, 800)}\n…（已截断，共 ${resultText.length} 字符）` : resultText}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** 助手文本：markdown 渲染，样式由 thread.css 的 .aui-md 作用域控制 */
export function MarkdownText({ text }: { text: string }) {
  return (
    <div className="aui-md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

/** 折叠的思考块（reasoning part） */
export function Reasoning({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  if (!text.trim()) return null;
  return (
    <div className="my-1.5 rounded-[10px] border border-dashed border-[var(--line)] bg-[var(--surface-soft)] text-[12px]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-3 py-1.5 text-left bg-transparent text-[var(--ink-muted)] hover:text-[var(--ink-soft)] rounded-[10px] transition-colors"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span>思考过程</span>
      </button>
      {open && (
        <div className="whitespace-pre-wrap border-t border-dashed border-[var(--line)] px-3 py-2 text-[12px] leading-relaxed text-[var(--ink-muted)]">
          {text}
        </div>
      )}
    </div>
  );
}

/** 消息为空（乐观占位、首个 token 未到）时的三点打字动画 */
export function EmptyTyping() {
  return (
    <div className="typing flex items-center gap-1 py-2" aria-label="正在思考">
      <span />
      <span />
      <span />
    </div>
  );
}
