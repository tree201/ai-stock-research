import { ThreadPrimitive } from "@assistant-ui/react";
import { ArrowDown } from "lucide-react";
import type { ReactNode } from "react";
import { AssistantMessage, ThreadSuggestions, UserMessage } from "./Message";

/**
 * 中央聊天区：滚动视口 + 消息列表 + 空态建议 + 回到底部浮动按钮。
 * children 会渲染在消息列表之后（用于追加 pendingJob 的进度卡片等）。
 */
export function Thread({ children }: { children?: ReactNode }) {
  return (
    <ThreadPrimitive.Root className="thread-aui h-full">
      <ThreadPrimitive.Viewport className="thread-viewport">
        <div className="thread-inner">
          <ThreadPrimitive.Empty>
            <ThreadSuggestions />
          </ThreadPrimitive.Empty>
          <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
          {children}
        </div>
        <ThreadPrimitive.ScrollToBottom asChild>
          <button
            type="button"
            aria-label="回到底部"
            className="scroll-bottom-btn"
          >
            <ArrowDown size={14} />
          </button>
        </ThreadPrimitive.ScrollToBottom>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}
