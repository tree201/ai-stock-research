/**
 * 输入框左下角的模型选择器（照抄 deepseek-harness ui-model-selection 的
 * ModelSelect：两级钻入菜单——root 面板是「模型 / 推理等级」两行 cell，
 * 各自钻入供应商分组的模型列表与推理等级列表；触发器显示「模型名 · 档位名」。
 * 选中模型不带档位提交，由后端应用默认档（defaultEffort 语义）。
 */

import { useEffect, useId, useMemo, useRef, useState, type FocusEvent, type KeyboardEvent } from "react";
import { Check, ChevronDown, ChevronRight, Settings2 } from "lucide-react";
import { api, LlmConfig } from "./api";

const LEVEL_LABELS: Record<string, string> = { off: "关闭", low: "低", medium: "中", high: "高" };

/** 菜单当前面板：root 两行入口 / model 模型列表 / effort 推理等级列表。 */
type Pane = "root" | "model" | "effort";

type Props = {
  config: LlmConfig | null;
  onConfigChange: (config: LlmConfig) => void;
  onOpenSettings: () => void;
};

export function ModelPicker({ config, onConfigChange, onOpenSettings }: Props) {
  const [open, setOpen] = useState(false);
  const [pane, setPane] = useState<Pane>("root");
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  const groups = useMemo(() => {
    if (!config) return [];
    return config.providers
      .map((provider) => ({ provider, models: config.models.filter((model) => model.provider_id === provider.id) }))
      .filter((group) => group.models.length > 0);
  }, [config]);

  if (!config) return null;

  const selection = config.selection;
  const currentModel = selection ? config.models.find((model) => model.id === selection.model_row_id) : undefined;
  const effortLabel = currentModel && currentModel.levels.length > 0 && selection
    ? LEVEL_LABELS[selection.level] ?? selection.level
    : undefined;

  async function chooseModel(modelRowId: number) {
    if (selection?.model_row_id === modelRowId) {
      close(true);
      return;
    }
    setBusy(true);
    try {
      const result = await api.setLlmSelection({ model_row_id: modelRowId });
      onConfigChange(result.config);
      close(true);
    } finally {
      setBusy(false);
    }
  }

  async function chooseEffort(level: string) {
    if (!selection) return;
    if (selection.level === level) {
      close(true);
      return;
    }
    setBusy(true);
    try {
      // 空档位 = 跟随模型默认档（后端 defaultEffort 语义）
      const result = await api.setLlmSelection(level ? { model_row_id: selection.model_row_id, level } : { model_row_id: selection.model_row_id });
      onConfigChange(result.config);
      close(true);
    } finally {
      setBusy(false);
    }
  }

  function close(restoreFocus = false) {
    setOpen(false);
    setPane("root");
    if (restoreFocus) queueMicrotask(() => triggerRef.current?.focus());
  }

  function show() {
    setPane("root");
    setOpen(true);
  }

  function moveFocus(offset: number) {
    const items = itemRefs.current.filter((item) => item !== null) as HTMLButtonElement[];
    if (items.length === 0) return;
    const active = items.findIndex((item) => item === document.activeElement);
    const next = (Math.max(active, 0) + offset + items.length) % items.length;
    items[next]?.focus();
  }

  function onRootKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      if (pane !== "root") setPane("root");
      else close(true);
      return;
    }
    if (!open) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      moveFocus(event.key === "ArrowDown" ? 1 : -1);
    }
  }

  function onBlur(event: FocusEvent<HTMLDivElement>) {
    if (event.relatedTarget instanceof Node && rootRef.current?.contains(event.relatedTarget)) return;
    close();
  }

  const modelLabel = selection ? selection.display_name : "选择模型";
  itemRefs.current = [];
  const itemRef = () => {
    const at = itemRefs.current.length;
    return (node: HTMLButtonElement | null) => {
      itemRefs.current[at] = node;
    };
  };

  return (
    <div ref={rootRef} className="picker-root" onKeyDown={onRootKeyDown} onBlur={onBlur}>
      <button
        ref={triggerRef}
        type="button"
        className={`picker-trigger ${open ? "open" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? `${id}-menu` : undefined}
        title={effortLabel ? `${modelLabel} · ${effortLabel}` : modelLabel}
        disabled={busy}
        onClick={() => (open ? close() : show())}
      >
        <span className="picker-trigger-label">{modelLabel}</span>
        {effortLabel !== undefined && <span className="picker-trigger-effort">{effortLabel}</span>}
        <ChevronDown size={13} className="picker-chevron" />
      </button>

      {open && (
        <div id={`${id}-menu`} className="picker-menu" role="menu" aria-label="模型与推理等级" aria-busy={busy}>
          {pane === "root" && (
            <>
              <button ref={itemRef()} type="button" role="menuitem" className="picker-cell" onClick={() => setPane("model")}>
                <span className="picker-cell-label">模型</span>
                <span className="picker-cell-value">{modelLabel}</span>
                <ChevronRight size={14} className="picker-cell-chevron" />
              </button>
              {currentModel && currentModel.levels.length > 0 && (
                <button ref={itemRef()} type="button" role="menuitem" className="picker-cell" onClick={() => setPane("effort")}>
                  <span className="picker-cell-label">推理等级</span>
                  <span className="picker-cell-value">{effortLabel}</span>
                  <ChevronRight size={14} className="picker-cell-chevron" />
                </button>
              )}
            </>
          )}

          {pane === "model" && (
            <>
              <div className="picker-groups">
                {groups.map(({ provider, models }) => (
                  <section role="group" aria-label={provider.name} className="picker-group" key={provider.id}>
                    <div className="picker-group-title" id={`${id}-${provider.id}`}>
                      {provider.name}
                      {!provider.has_api_key && <span className="picker-group-hint">未配置密钥</span>}
                    </div>
                    {models.map((model) => {
                      const selected = selection?.model_row_id === model.id;
                      return (
                        <button
                          ref={itemRef()}
                          type="button"
                          role="menuitemradio"
                          aria-checked={selected}
                          className={`picker-option ${selected ? "selected" : ""}`}
                          key={model.id}
                          title={model.display_name}
                          disabled={busy || !provider.has_api_key}
                          onClick={() => void chooseModel(model.id)}
                        >
                          <span className="picker-option-copy">
                            <span className="picker-option-name">{model.display_name}</span>
                          </span>
                          <span className="picker-check">{selected ? <Check size={15} /> : null}</span>
                        </button>
                      );
                    })}
                  </section>
                ))}
                {groups.length === 0 && <div className="picker-empty">没有可用的模型。</div>}
              </div>
              <button type="button" className="picker-manage" onClick={() => { close(); onOpenSettings(); }}>
                <Settings2 size={13} /> 管理模型接入…
              </button>
            </>
          )}

          {pane === "effort" && (
            <div className="picker-groups">
              {currentModel && (
                <>
                  <button
                    ref={itemRef()}
                    type="button"
                    role="menuitemradio"
                    aria-checked={selection?.level === currentModel.default_level}
                    className={`picker-option ${selection?.level === currentModel.default_level ? "selected" : ""}`}
                    disabled={busy}
                    onClick={() => void chooseEffort("")}
                  >
                    <span className="picker-option-copy">
                      <span className="picker-option-name">Default</span>
                      <span className="picker-option-meta">跟随模型默认档（{LEVEL_LABELS[currentModel.default_level] ?? currentModel.default_level}）</span>
                    </span>
                    <span className="picker-check">{selection?.level === currentModel.default_level ? <Check size={15} /> : null}</span>
                  </button>
                  {currentModel.levels.map((level) => {
                    const selected = selection?.level === level;
                    return (
                      <button
                        ref={itemRef()}
                        type="button"
                        role="menuitemradio"
                        aria-checked={selected}
                        className={`picker-option ${selected ? "selected" : ""}`}
                        key={level}
                        disabled={busy}
                        onClick={() => void chooseEffort(level)}
                      >
                        <span className="picker-option-copy">
                          <span className="picker-option-name">{LEVEL_LABELS[level] ?? level}</span>
                        </span>
                        <span className="picker-check">{selected ? <Check size={15} /> : null}</span>
                      </button>
                    );
                  })}
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
