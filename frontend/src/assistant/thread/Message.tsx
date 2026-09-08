import { MessagePrimitive, useAui } from "@assistant-ui/react";
import { EmptyTyping, MarkdownText, Reasoning, ToolCard } from "../ToolUIs";

function UserMessage() {
  return (
    <MessagePrimitive.Root className="message-aui flex justify-end py-2">
      <div className="max-w-[70%] rounded-[17px] border border-[var(--bubble-user-line)] bg-[var(--bubble-user)] px-[15px] py-[10px] text-[13px] leading-relaxed text-[var(--ink-strong)] whitespace-pre-wrap">
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="message-aui py-2">
      <MessagePrimitive.Parts
        components={{
          Text: ({ text }) => <MarkdownText text={text} />,
          Reasoning: ({ text }) => <Reasoning text={text} />,
          tools: { Fallback: ToolCard },
          Empty: EmptyTyping,
        }}
      />
    </MessagePrimitive.Root>
  );
}

export { UserMessage, AssistantMessage };

/** 空线程时的建议入口：点击直接填充并发送 */
export function ThreadSuggestions() {
  const aui = useAui();
  const suggestions = [
    "研究这家公司是否适合长期持有，重点看现金流和估值",
    "分析公司的主要风险和反方证据",
  ];
  return (
    <div className="mx-auto flex max-w-[820px] flex-col items-center justify-center gap-3 px-7 py-24">
      <div className="flex items-center gap-2 text-[17px] font-semibold text-[var(--ink-strong)]">
        <span className="text-[var(--signal)]">◆</span>
        从一家公司开始研究
      </div>
      <div className="text-[12px] text-[var(--ink-muted)]">在左侧选择公司，或试试下面的问题</div>
      <div className="mt-2 flex flex-col gap-2">
        {suggestions.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => {
              aui.composer.setText(s);
              aui.composer.send();
            }}
            className="rounded-[11px] border border-[var(--line)] bg-[var(--surface)] px-4 py-2.5 text-left text-[13px] text-[var(--ink-soft)] shadow-[0_1px_4px_#0000000a] transition-colors hover:border-[var(--signal)] hover:text-[var(--ink-strong)]"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
