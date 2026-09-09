import type { ThreadMessageLike } from "@assistant-ui/react";
import type { Message } from "../api";

/** Record<string, unknown> 在结构上是 JSON，仅做类型窄化以满足 tool-call part 的 args 形状。 */
export const toolArgs = (args?: Record<string, unknown>) =>
  args && Object.keys(args).length ? (args as never) : undefined;

const toolCallPart = (message: Message) => ({
  type: "tool-call" as const,
  toolCallId: message.id,
  toolName: message.content.tool || "unknown",
  args: toolArgs(message.content.args),
  result: message.content.observation,
  isError: message.content.observation?.startsWith("工具执行失败") || undefined,
});

/**
 * 后端历史消息 → assistant-ui ThreadMessageLike[]：
 * - 丢弃 report_card 与空文本；
 * - 连续 tool 消息聚合为一条 assistant（多 tool-call 部件）；
 * - role:text 相同文本去重（user/assistant 各自独立判重）。
 */
export function messagesToThread(messages: Message[]): ThreadMessageLike[] {
  const seen = new Set<string>();
  const result: ThreadMessageLike[] = [];
  for (const message of messages) {
    if (message.message_type === "report_card") continue;
    if (message.message_type === "tool") {
      const part = toolCallPart(message);
      const last = result[result.length - 1];
      if (
        last &&
        last.role === "assistant" &&
        Array.isArray(last.content) &&
        last.content.some((p) => p.type === "tool-call")
      ) {
        last.content.push(part);
      } else {
        result.push({
          role: "assistant",
          id: `tools-${message.id}`,
          content: [part],
        });
      }
      continue;
    }
    const text = message.content.text || message.content.summary?.join("\n") || "";
    if (!text.trim()) continue;
    const key = `${message.role}:${text.trim()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push({
      role: message.role === "user" ? "user" : "assistant",
      id: message.id,
      content: text,
    });
  }
  return result;
}
