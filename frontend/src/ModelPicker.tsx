/** 输入框左下角的模型切换器：当前模型 + 强度档位，点击即切换并存到后端。 */

import { Fragment, useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Cpu, KeyRound, Settings2 } from "lucide-react";
import { api, LlmConfig, LlmSelectionEntry } from "./api";

const LEVEL_LABELS: Record<string, string> = { off: "关闭", low: "低", medium: "中", high: "高" };

type Props = {
  config: LlmConfig | null;
  onConfigChange: (config: LlmConfig) => void;
  onOpenSettings: () => void;
};

export function ModelPicker({ config, onConfigChange, onOpenSettings }: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  if (!config) return null;

  const selection = config.selection;
  const currentModel = selection
    ? config.models.find((model) => model.id === selection.model_row_id)
    : undefined;
  const supportedLevels = currentModel?.levels ?? [];

  async function applySelection(modelRowId: number, level: string) {
    setBusy(true);
    try {
      const result = await api.setLlmSelection({ model_row_id: modelRowId, level });
      onConfigChange(result.config);
    } finally {
      setBusy(false);
    }
  }

  async function pickModel(entry: { model_row_id: number; levels: string[] }) {
    const level = entry.levels.includes("off") || entry.levels.length === 0 ? (entry.levels[0] ?? "off") : "off";
    await applySelection(entry.model_row_id, level);
    setOpen(false);
  }

  const recent = config.recent.slice(0, 4);
  const providersWithModels = config.providers
    .map((provider) => ({
      provider,
      models: config.models.filter((model) => model.provider_id === provider.id),
    }))
    .filter((group) => group.models.length > 0);

  return (
    <div className="composer-meta" ref={rootRef}>
      <div className="model-picker">
        <button
          type="button"
          className={`picker-trigger ${open ? "open" : ""}`}
          onClick={() => setOpen((value) => !value)}
          disabled={busy}
        >
          <Cpu size={13} />
          <span className="picker-label">
            {selection
              ? `${selection.display_name}`
              : "选择模型"}
          </span>
          {selection && (
            <span className={`picker-key ${selection.has_api_key ? "ok" : "missing"}`} title={selection.has_api_key ? "密钥已配置" : "该供应商缺少 API Key"}>
              <KeyRound size={11} />
            </span>
          )}
          <ChevronDown size={13} />
        </button>
        {open && (
          <div className="picker-menu" role="listbox">
            {recent.length > 0 && (
              <>
                <div className="picker-group">最近使用</div>
                {recent.map((entry) => (
                  <PickerItem
                    key={`recent-${entry.model_row_id}`}
                    entry={entry}
                    selected={selection?.model_row_id === entry.model_row_id}
                    levels={config.models.find((model) => model.id === entry.model_row_id)?.levels ?? []}
                    onPick={pickModel}
                  />
                ))}
              </>
            )}
            {providersWithModels.map(({ provider, models }) => (
              <Fragment key={`group-${provider.id}`}>
                <div className="picker-group">
                  {provider.name}
                  {!provider.has_api_key && <span className="picker-group-hint">未配置密钥</span>}
                </div>
                {models.map((model) => (
                  <PickerItem
                    key={model.id}
                    entry={{
                      model_row_id: model.id,
                      provider_id: model.provider_id,
                      provider_name: provider.name,
                      model_id: model.model_id,
                      display_name: model.display_name,
                      level: "off",
                      has_api_key: provider.has_api_key,
                    }}
                    levels={model.levels}
                    selected={selection?.model_row_id === model.id}
                    onPick={pickModel}
                  />
                ))}
              </Fragment>
            ))}
            <button type="button" className="picker-manage" onClick={() => { setOpen(false); onOpenSettings(); }}>
              <Settings2 size={13} /> 管理模型接入…
            </button>
          </div>
        )}
      </div>
      {selection && (
        <div className="level-picker" title={supportedLevels.length ? "推理强度" : "该模型不支持强度调节"}>
          {supportedLevels.length === 0 ? (
            <span className="level-empty">固定模式</span>
          ) : (
            supportedLevels.map((level) => (
              <button
                key={level}
                type="button"
                disabled={busy}
                className={`level-option ${selection.level === level ? "active" : ""}`}
                onClick={() => void applySelection(selection.model_row_id, level)}
              >
                {LEVEL_LABELS[level] ?? level}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function PickerItem({
  entry,
  levels,
  selected,
  onPick,
}: {
  entry: LlmSelectionEntry;
  levels: string[];
  selected: boolean;
  onPick: (entry: { model_row_id: number; levels: string[] }) => void | Promise<void>;
}) {
  return (
    <button
      type="button"
      className={`picker-item ${selected ? "selected" : ""} ${entry.has_api_key ? "" : "no-key"}`}
      onClick={() => void onPick({ model_row_id: entry.model_row_id, levels })}
    >
      <span className="picker-item-name">{entry.display_name}</span>
      <span className="picker-item-meta">
        {levels.length > 0 ? `${levels.length} 档强度` : "固定模式"}
      </span>
      {selected && <Check size={13} />}
    </button>
  );
}
