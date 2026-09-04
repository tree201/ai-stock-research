/**
 * 输入框左下角的权限授权选择器（对照 Trae 的 完全访问 pill）：
 * 手动审批 — 仅白名单来源可抓取；自动审批 — 用户消息里的链接视为已授权；
 * 完全访问 — 任意来源自动抓取。模式持久化到后端 app_settings。
 */

import { useEffect, useId, useRef, useState, type FocusEvent, type KeyboardEvent } from "react";
import { Check, ChevronDown, ShieldCheck } from "lucide-react";
import { api, APPROVAL_LABELS, ApprovalMode, LlmConfig } from "./api";

const MODELS: { mode: ApprovalMode; meta: string }[] = [
  { mode: "manual", meta: "仅白名单来源可抓取，其余需先加入信任白名单" },
  { mode: "auto", meta: "白名单自动抓取；你消息里给出的链接视为已授权" },
  { mode: "full", meta: "任意来源自动抓取，不经审批" },
];

type Props = {
  config: LlmConfig | null;
  onConfigChange: (config: LlmConfig) => void;
};

export function ApprovalPicker({ config, onConfigChange }: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  if (!config) return null;
  const mode = config.approval_mode ?? "manual";

  async function choose(next: ApprovalMode) {
    if (next === mode) {
      close(true);
      return;
    }
    setBusy(true);
    try {
      const result = await api.setLlmApproval(next);
      onConfigChange(result.config);
      close(true);
    } finally {
      setBusy(false);
    }
  }

  function close(restoreFocus = false) {
    setOpen(false);
    if (restoreFocus) queueMicrotask(() => triggerRef.current?.focus());
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      close(true);
    }
  }

  function onBlur(event: FocusEvent<HTMLDivElement>) {
    if (event.relatedTarget instanceof Node && rootRef.current?.contains(event.relatedTarget)) return;
    close();
  }

  return (
    <div ref={rootRef} className="picker-root approval-picker" onKeyDown={onKeyDown} onBlur={onBlur}>
      <button
        ref={triggerRef}
        type="button"
        className={`picker-trigger approval-trigger ${mode === "full" ? "approval-full" : ""} ${open ? "open" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? `${id}-menu` : undefined}
        title={`权限授权：${APPROVAL_LABELS[mode]}`}
        disabled={busy}
        onClick={() => setOpen(!open)}
      >
        <ShieldCheck size={13} />
        <span className="picker-trigger-label">{APPROVAL_LABELS[mode]}</span>
        <ChevronDown size={13} className="picker-chevron" />
      </button>

      {open && (
        <div id={`${id}-menu`} className="picker-menu approval-menu" role="menu" aria-label="权限授权" aria-busy={busy}>
          <div className="approval-menu-head">如何批准研究抓取的来源？</div>
          {MODELS.map((item) => {
            const selected = mode === item.mode;
            return (
              <button
                type="button"
                role="menuitemradio"
                aria-checked={selected}
                className={`picker-option ${selected ? "selected" : ""}`}
                key={item.mode}
                disabled={busy}
                onClick={() => void choose(item.mode)}
              >
                <span className="picker-option-copy">
                  <span className="picker-option-name">{APPROVAL_LABELS[item.mode]}</span>
                  <span className="picker-option-meta">{item.meta}</span>
                </span>
                <span className="picker-check">{selected ? <Check size={15} /> : null}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
