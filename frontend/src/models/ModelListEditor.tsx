/**
 * The model list of one provider profile, plus the action that asks the
 * provider what it serves.
 *（抄自 deepseek-harness ui-settings-models/ModelListEditor.tsx，
 * 探测调用改接本项目 /api/llm/models/discover。）
 */

import { useState } from "react";
import type { ReactNode } from "react";
import { Modal } from "antd";
import { api } from "../api";
import { formatCapacity, parseCapacity, IconChevron, IconTrash } from "./DeepSeekModelsEditor";
import type { DeepSeekModelDraft } from "./DeepSeekModelsEditor";
import type { en } from "./locales";
import styles from "./ModelsSection.module.css";

/** One configured model row. Structurally open, exactly like the catalog editor's rows. */
export type ModelDraft = DeepSeekModelDraft;

/** A row's text field, or the empty string when unset or not a string. */
function textOf(model: ModelDraft, key: string): string {
  const value = model[key];
  return typeof value === "string" ? value : "";
}

/** A row's numeric field, or `undefined` when unset or not a number. */
function numberOf(model: ModelDraft, key: string): number | undefined {
  const value = model[key];
  return typeof value === "number" ? value : undefined;
}

/** What an interrogation needs, taken from the live form. */
export interface ProbeTarget {
  /** Provider row id, when the card edits one — the backend answers from the stored profile. */
  provider_id?: number;
  /** Endpoint as the form currently shows it. */
  base_url?: string;
  /** Key typed into the form and not yet stored, when there is one. */
  api_key?: string;
}

/** One discovered candidate as the backend reports it. */
interface DiscoveredModel {
  model_id: string;
  added: boolean;
}

/** Props of {@link ModelListEditor}. */
export interface ModelListEditorProps {
  /** The rows as currently drafted. */
  models: readonly ModelDraft[];
  /** Whether the user layer currently owns the whole array; absent on a create. */
  overridden?: boolean;
  /** Replace the drafted rows. */
  onChange: (models: ModelDraft[]) => void;
  /** Remove the user-owned array and return to inheritance; absent on a create. */
  onReset?: () => void;
  /** Endpoint facts for the fetch action. */
  probe: ProbeTarget;
  /** Copy key naming why the fetch action is unavailable, or `undefined` when it is. */
  probeBlocked?: keyof typeof en | undefined;
  /** Section copy. */
  t: (key: keyof typeof en) => string;
  /** Disable every control (read-only deployment or a pending write). */
  disabled: boolean;
}

/** The two token counts edited as K/M-suffixed text behind a row's disclosure. */
type CapacityField = "contextWindow" | "maxTokens";

/**
 * What an empty capacity field is worth, shown as its placeholder so a row left
 * blank does not read as a model with no capacity at all.
 */
const CAPACITY_HINT: Readonly<Record<CapacityField, string>> = {
  contextWindow: "256K",
  maxTokens: "32K",
};

/** Spell a stored count for a field that may be unset. */
function capacitySpelling(value: number | undefined): string {
  return value === undefined ? "" : formatCapacity(value);
}

/** Adopt a candidate, keeping whatever capacities the provider disclosed. */
function adopt(candidate: DiscoveredModel): ModelDraft {
  return { id: candidate.model_id };
}

/**
 * Render the model list with its fetch action.
 */
export function ModelListEditor(props: ModelListEditorProps): ReactNode {
  const { models, onChange, probe, t, disabled } = props;
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | undefined>(undefined);
  const [candidates, setCandidates] = useState<readonly DiscoveredModel[] | undefined>(undefined);
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  // Rows carry an id and a name; capacities are the exception, so they stay
  // folded until asked for rather than crowding every row with four inputs.
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(new Set());
  // Capacities are edited as text, so a field's keystrokes are held here rather
  // than re-derived from the parsed count on every change — that would rewrite
  // `1000` to `1K` mid-word. Unreadable text is kept past blur so the refusal
  // names a row the user can still see, which is why this is one entry PER
  // FIELD.
  const [editing, setEditing] = useState<ReadonlyMap<string, string>>(new Map());

  /** Buffer key for one capacity field; the row half moves when rows do. */
  const bufferKey = (index: number, field: CapacityField): string => `${String(index)}:${field}`;

  const editCapacity = (index: number, field: CapacityField, text: string): void => {
    setEditing((current) => new Map(current).set(bufferKey(index, field), text));
    patch(index, { [field]: parseCapacity(text) });
  };

  /** What a capacity field shows: the buffer while typing, else the stored count. */
  const capacityText = (model: ModelDraft, index: number, field: CapacityField): string =>
    editing.get(bufferKey(index, field)) ?? capacitySpelling(numberOf(model, field));

  /** Drop one row's entries and shift the rows after it down, in one pass. */
  const reindexOnRemove = (
    current: ReadonlyMap<string, string>,
    index: number,
  ): Map<string, string> => {
    const next = new Map<string, string>();
    for (const [key, value] of current) {
      const at = Number(key.slice(0, key.indexOf(":")));
      if (at === index) continue;
      // Only the row number moves; the field half of the key is untouched.
      next.set(at > index ? key.replace(/^\d+/, String(at - 1)) : key, value);
    }
    return next;
  };

  const toggleExpanded = (index: number): void => {
    setExpanded((current) => {
      const next = new Set(current);
      if (!next.delete(index)) next.add(index);
      return next;
    });
  };

  const patch = (index: number, next: Record<string, string | number | undefined>): void => {
    onChange(
      models.map((model, at) => {
        if (at !== index) return model;
        // Rebuilt rather than spread over: an emptied optional field has to leave
        // the profile, not be stored as a value its schema would reject.
        const cleared = new Set(
          Object.entries(next)
            .filter(([, value]) => value === undefined || value === "")
            .map(([key]) => key),
        );
        return Object.fromEntries(
          Object.entries({ ...model, ...next }).filter(([key]) => !cleared.has(key)),
        );
      }),
    );
  };

  const fetchModels = async (): Promise<void> => {
    setBusy(true);
    setFailure(undefined);
    try {
      const result = await api.discoverLlmModels({
        ...(probe.provider_id === undefined ? {} : { provider_id: probe.provider_id }),
        ...(probe.base_url === undefined || probe.base_url.length === 0
          ? {}
          : { base_url: probe.base_url }),
        ...(probe.api_key === undefined || probe.api_key.length === 0
          ? {}
          : { api_key: probe.api_key }),
      });
      const found: DiscoveredModel[] = result.models;
      if (found.length === 0) {
        setFailure(t("fetchEmpty"));
        return;
      }
      // Everything already configured starts unchecked, so adopting a
      // selection never silently rewrites a capacity the user corrected.
      const known = new Set(models.map((model) => textOf(model, "id")));
      setCandidates(found);
      setPicked(
        new Set(found.filter((model) => !model.added && !known.has(model.model_id)).map((model) => model.model_id)),
      );
    } catch (error) {
      // The transport rejected rather than answering; without this the button
      // would stay busy with nothing shown.
      setFailure(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const closePicker = (): void => {
    setCandidates(undefined);
    setPicked(new Set());
  };

  const adoptPicked = (): void => {
    if (candidates === undefined) return;
    const byId = new Map(models.map((model) => [textOf(model, "id"), model]));
    for (const candidate of candidates) {
      if (!picked.has(candidate.model_id)) continue;
      // A row the user already tuned wins over the provider's own numbers.
      byId.set(candidate.model_id, byId.get(candidate.model_id) ?? adopt(candidate));
    }
    onChange([...byId.values()]);
    closePicker();
  };

  const toggle = (id: string): void => {
    setPicked((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  };

  // A route the backend already stores answers without an endpoint; only a
  // draft with neither has nothing to ask about.
  const askable =
    probe.provider_id !== undefined || (probe.base_url !== undefined && probe.base_url.length > 0);
  return (
    <section className={styles["modelCatalog"]} aria-label={t("models")}>
      <div className={styles["modelListHead"]}>
        <div className={styles["modelCatalogHeading"]}>
          <span className={styles["modelCatalogTitle"]}>{t("models")}</span>
          {props.overridden === undefined ? null : (
            <span className={styles["modelCatalogMeta"]}>
              {props.overridden ? t("modelsCustomized") : t("modelsInherited")}
            </span>
          )}
        </div>
        {props.overridden === true && props.onReset !== undefined ? (
          <button
            type="button"
            className={styles["linkButton"]}
            disabled={disabled}
            onClick={props.onReset}
          >
            {t("resetModels")}
          </button>
        ) : null}
        <button
          type="button"
          className={styles["linkButton"]}
          disabled={disabled || busy || !askable || props.probeBlocked !== undefined}
          title={
            props.probeBlocked !== undefined
              ? t(props.probeBlocked)
              : askable
                ? undefined
                : t("fetchNeedsBaseUrl")
          }
          onClick={() => {
            void fetchModels();
          }}
        >
          {busy ? t("fetching") : t("fetchModels")}
        </button>
      </div>
      {models.length === 0 ? <p className={styles["modelEmpty"]}>{t("modelsEmpty")}</p> : null}
      {models.map((model, index) => (
        <div key={index} className={styles["modelEntry"]}>
          <div className={styles["modelRow"]}>
            <input
              className={styles["input"]}
              type="text"
              value={textOf(model, "id")}
              placeholder={t("modelId")}
              aria-label={`${t("modelId")} ${index + 1}`}
              disabled={disabled}
              onChange={(event) => {
                patch(index, { id: event.target.value });
              }}
            />
            <input
              className={styles["input"]}
              type="text"
              value={textOf(model, "name")}
              placeholder={t("modelName")}
              aria-label={`${t("modelName")} ${index + 1}`}
              disabled={disabled}
              onChange={(event) => {
                patch(index, { name: event.target.value === "" ? undefined : event.target.value });
              }}
            />
            <button
              type="button"
              className={styles["iconButton"]}
              aria-label={`${t("modelAdvanced")} ${index + 1}`}
              aria-expanded={expanded.has(index)}
              title={t("modelAdvanced")}
              onClick={() => {
                toggleExpanded(index);
              }}
            >
              <IconChevron open={expanded.has(index)} />
            </button>
            <button
              type="button"
              className={`${styles["iconButton"]} ${styles["iconButtonDanger"]}`}
              aria-label={`${t("removeModel")} ${index + 1}`}
              title={t("removeModel")}
              disabled={disabled}
              onClick={() => {
                onChange(models.filter((_model, at) => at !== index));
                // Both stores are keyed by position, so every row after this
                // one shifts down and would otherwise inherit its neighbour's
                // state.
                setExpanded((current) => {
                  const next = new Set<number>();
                  for (const at of current) {
                    if (at < index) next.add(at);
                    else if (at > index) next.add(at - 1);
                  }
                  return next;
                });
                setEditing((current) => reindexOnRemove(current, index));
              }}
            >
              <IconTrash />
            </button>
          </div>
          {expanded.has(index) ? (
            <div className={styles["modelAdvanced"]}>
              <label className={styles["modelField"]}>
                <span className={styles["modelFieldLabel"]}>{t("modelContextWindow")}</span>
                <input
                  className={styles["input"]}
                  type="text"
                  inputMode="numeric"
                  value={capacityText(model, index, "contextWindow")}
                  placeholder={CAPACITY_HINT.contextWindow}
                  aria-label={`${t("modelContextWindow")} ${index + 1}`}
                  disabled={disabled}
                  onChange={(event) => {
                    editCapacity(index, "contextWindow", event.target.value);
                  }}
                />
              </label>
              <label className={styles["modelField"]}>
                <span className={styles["modelFieldLabel"]}>{t("modelMaxTokens")}</span>
                <input
                  className={styles["input"]}
                  type="text"
                  inputMode="numeric"
                  value={capacityText(model, index, "maxTokens")}
                  placeholder={CAPACITY_HINT.maxTokens}
                  aria-label={`${t("modelMaxTokens")} ${index + 1}`}
                  disabled={disabled}
                  onChange={(event) => {
                    editCapacity(index, "maxTokens", event.target.value);
                  }}
                />
              </label>
            </div>
          ) : null}
        </div>
      ))}
      <button
        type="button"
        className={styles["addModelButton"]}
        disabled={disabled}
        onClick={() => {
          onChange([...models, { id: "" }]);
        }}
      >
        {t("addModel")}
      </button>
      {failure !== undefined ? <p className={styles["error"]}>{failure}</p> : null}
      <Modal
        open={candidates !== undefined}
        onCancel={closePicker}
        title={t("fetchTitle")}
        footer={null}
        width={520}
      >
        <p className={styles["advancedHint"]}>{t("fetchDescription")}</p>
        <ul className={styles["candidateList"]}>
          {(candidates ?? []).map((candidate) => (
            <li key={candidate.model_id} className={styles["candidate"]}>
              <label className={styles["candidateLabel"]}>
                <input
                  type="checkbox"
                  checked={picked.has(candidate.model_id)}
                  onChange={() => {
                    toggle(candidate.model_id);
                  }}
                />
                {/* The id alone: it is the string adoption writes. */}
                <span className={styles["candidateId"]}>{candidate.model_id}</span>
              </label>
            </li>
          ))}
        </ul>
        <div className={styles["editorActions"]}>
          <button type="button" className={styles["secondaryButton"]} onClick={closePicker}>
            {t("cancel")}
          </button>
          <button type="button" className={styles["primaryButton"]} onClick={adoptPicked}>
            {t("fetchAdopt")}
          </button>
        </div>
      </Modal>
    </section>
  );
}
