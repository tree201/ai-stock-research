import {
  AssistantRuntimeProvider,
  useAui,
  useExternalStoreRuntime,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { api, type AgentStreamEvent, type ChatResult, type Message } from "../api";
import { messagesToThread, toolArgs } from "./convertMessages";

export type ResearchSendTarget = { name: string; symbol: string; market: string };
export type ResearchGateResult = { error?: string; target?: ResearchSendTarget | null };

type ComposerApi = { setText(text: string): void };

type ToolCallPart = {
  type: "tool-call";
  toolCallId: string;
  toolName: string;
  args?: ReturnType<typeof toolArgs>;
  argsText?: string;
  result?: string;
  isError?: boolean;
};
type ReasoningPart = { type: "reasoning"; text: string };
type DraftPart = ToolCallPart | ReasoningPart;

export type ResearchRuntimeProviderProps = {
  sessionId: string | null;
  /** 已持久化的历史消息（来自 API），会话切换/刷新时据此重建线程。 */
  history: Message[];
  /** 后台研究任务轮询中置 true：输入不可用（对齐旧版 textarea 禁用）。 */
  disabled?: boolean;
  /** 发送前门禁：模型就绪校验 + 新会话目标推导（原 submit 的纯逻辑部分）。 */
  resolveSend: (text: string) => ResearchGateResult;
  onResult: (
    result: ChatResult,
    meta: { isNewSession: boolean; target: ResearchSendTarget | null },
  ) => void;
  onError: (message: string) => void;
  children: ReactNode;
};

/** 把 assistant-ui client 的 composer 能力桥接给 provider（门禁失败时回填草稿）。 */
function RuntimeApiBridge({ apiRef }: { apiRef: RefObject<ComposerApi | null> }) {
  const aui = useAui();
  useEffect(() => {
    apiRef.current = {
      setText: (text: string) => aui.composer.setText(text),
    };
  });
  return null;
}

/**
 * 研究运行时：把现有 NDJSON 流式后端接入 assistant-ui 线程模型。
 * - 乐观 user 消息 + 空 assistant 草稿由 onNew 追加（草稿由皮肤 Empty 组件渲染 typing 态）；
 * - agent_event 原位填充草稿：step_started 追加 reasoning/tool-call，完成帧回填 result；
 * - done 追加最终回复（丢弃空草稿）；取消保留已生成的部分部件；
 * - history prop 变化（会话切换/刷新）时按服务端历史重建线程。
 */
export function ResearchRuntimeProvider({
  sessionId,
  history,
  disabled,
  resolveSend,
  onResult,
  onError,
  children,
}: ResearchRuntimeProviderProps) {
  const [threadMessages, setThreadMessages] = useState<ThreadMessageLike[]>(() =>
    messagesToThread(history),
  );
  const [running, setRunning] = useState(false);

  const runningRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const runSessionRef = useRef<string | null>(sessionId);
  const sessionIdRef = useRef(sessionId);
  const historyRef = useRef(history);
  const syncedRef = useRef<{ sessionId: string | null; history: Message[] }>({
    sessionId,
    history,
  });
  const composerRef = useRef<ComposerApi | null>(null);

  useEffect(() => {
    sessionIdRef.current = sessionId;
    historyRef.current = history;
    if (runningRef.current) {
      // 运行中会话被切换：中止当前流（catch 中按最新历史重建）。
      if (runSessionRef.current !== sessionId) abortRef.current?.abort();
      return;
    }
    if (
      syncedRef.current.sessionId === sessionId &&
      syncedRef.current.history === history
    )
      return;
    syncedRef.current = { sessionId, history };
    setThreadMessages(messagesToThread(history));
  }, [sessionId, history]);

  const handleNew = async (message: AppendMessage) => {
    if (runningRef.current) return;
    const text = message.content
      .filter((part) => part.type === "text")
      .map((part) => part.text)
      .join("\n")
      .trim();
    if (!text) return;

    const gate = resolveSend(text);
    // 已有会话时 resolveSend 不推导 target（messageStream 无需公司目标），
    // 仅在无会话时才要求 target；否则会话内追问会被误判为门禁失败。
    if (gate.error || (!sessionIdRef.current && !gate.target)) {
      // 门禁失败：文本已被 composer 消费，回填草稿避免用户重打。
      composerRef.current?.setText(text);
      onError(gate.error || "请先在左侧选择一家公司，再发送消息。");
      return;
    }

    const draftId = crypto.randomUUID();
    const now = new Date();
    const userMsg: ThreadMessageLike = {
      id: `u-${draftId}`,
      role: "user",
      content: text,
      createdAt: now,
    };
    const draft: ThreadMessageLike = {
      id: draftId,
      role: "assistant",
      content: [],
      createdAt: now,
    };

    runningRef.current = true;
    setThreadMessages((prev) => [...prev, userMsg, draft]);
    setRunning(true);
    onError("");

    const controller = new AbortController();
    abortRef.current = controller;
    const startSessionId = sessionIdRef.current;
    runSessionRef.current = startSessionId;
    const target = gate.target ?? null;

    const updateDraft = (update: (parts: DraftPart[]) => DraftPart[]) => {
      setThreadMessages((prev) =>
        prev.map((m) => {
          if (m.id !== draftId || !Array.isArray(m.content)) return m;
          return { ...m, content: update([...(m.content as DraftPart[])]) };
        }),
      );
    };

    const handleAgentEvent = (event: AgentStreamEvent) => {
      updateDraft((parts) => {
        if (event.type === "step_started") {
          const next = [...parts];
          if (event.thought) next.push({ type: "reasoning", text: event.thought });
          next.push({
            type: "tool-call",
            toolCallId: event.ref || crypto.randomUUID(),
            toolName: event.tool || "unknown",
            args: toolArgs(event.args),
            argsText:
              event.args && Object.keys(event.args).length
                ? JSON.stringify(event.args, null, 2)
                : undefined,
          });
          return next;
        }
        const next = [...parts];
        const idx = next.findIndex(
          (p) => p.type === "tool-call" && p.toolCallId === event.ref,
        );
        if (idx >= 0) {
          const part = next[idx] as ToolCallPart;
          next[idx] = {
            ...part,
            result: event.observation ?? "",
            isError: event.observation?.startsWith("工具执行失败") || undefined,
          };
          if (event.thought && idx > 0 && next[idx - 1].type === "reasoning") {
            next[idx - 1] = { type: "reasoning", text: event.thought };
          }
        } else if (event.thought) {
          const last = next.length - 1;
          if (last >= 0 && next[last].type === "reasoning") {
            next[last] = { type: "reasoning", text: event.thought };
          } else {
            next.push({ type: "reasoning", text: event.thought });
          }
        }
        return next;
      });
    };

    try {
      // 模型与强度档位由后端存储的选择决定（llm.selection），前端不逐请求携带密钥。
      const result = startSessionId
        ? await api.messageStream(startSessionId, text, handleAgentEvent, controller.signal)
        : await api.chatStream(
            // 走此分支时必然无会话，门禁已保证 target 存在。
            { name: target!.name, symbol: target!.symbol, content: text },
            handleAgentEvent,
            controller.signal,
          );

      runningRef.current = false;
      setRunning(false);

      const reply: ThreadMessageLike | null = result.message
        ? {
            id: `r-${draftId}`,
            role: "assistant",
            content: result.message,
            createdAt: new Date(),
            metadata: { custom: { reportId: result.report_id ?? null } },
          }
        : null;
      setThreadMessages((prev) => {
        const kept = prev.filter(
          (m) =>
            !(
              m.id === draftId &&
              Array.isArray(m.content) &&
              m.content.length === 0
            ),
        );
        return reply ? [...kept, reply] : kept;
      });
      syncedRef.current = {
        sessionId: result.session_id || startSessionId,
        history: historyRef.current,
      };
      onResult(result, { isNewSession: !startSessionId, target });
    } catch (err) {
      runningRef.current = false;
      setRunning(false);
      if (sessionIdRef.current !== runSessionRef.current) {
        // 会话已切换：按最新历史重建，放弃当前流残留部件。
        setThreadMessages(messagesToThread(historyRef.current));
        syncedRef.current = {
          sessionId: sessionIdRef.current,
          history: historyRef.current,
        };
        return;
      }
      if ((err as Error)?.name === "AbortError") return; // 用户停止：保留部分部件
      onError(err instanceof Error ? err.message : "处理失败");
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  };

  const runtime = useExternalStoreRuntime({
    isRunning: running,
    isDisabled: disabled,
    isSendDisabled: running,
    messages: threadMessages,
    convertMessage: (m) => m,
    setMessages: (msgs) => setThreadMessages([...msgs]),
    onNew: handleNew,
    onCancel: async () => abortRef.current?.abort(),
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <RuntimeApiBridge apiRef={composerRef} />
      {children}
    </AssistantRuntimeProvider>
  );
}
