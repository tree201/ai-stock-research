/**
 * One provider's editor card, hand-written per provider family: the primary
 * field is a single write-only **API key** input (a typed key stores with the
 * provider; a blank field keeps the stored key);
 * the collapsed 自定义设置 area carries the per-family extras (the base URL and
 * the model catalog — the DeepSeek family edits its curated id/name/catalog
 * capacities, every other family edits an open list with a fetch action).
 * Reasoning effort is deliberately absent: it is a per-MODEL capability, and
 * the models under one provider disagree about it, so a provider-scoped
 * control can only be set to a value some of them reject. The composer's
 * model picker offers each model its own levels.
 *（抄自 deepseek-harness ui-settings-models/ProviderEditor.tsx，写路径改接本项目
 * /api/llm/providers 与 /api/llm/models/batch；目录预设经 preset 参数落地。）
 */

import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api";
import type { LlmCatalogEntry, LlmModel, LlmProvider } from "../api";
import { apiKeyFailure } from "./apiKey";
import { EditorFooter } from "./EditorFooter";
import { ModelListEditor } from "./ModelListEditor";
import type { ModelDraft } from "./ModelListEditor";
import { DeepSeekModelsEditor, validateDeepSeekModels } from "./DeepSeekModelsEditor";
import { t as tf } from "./locales";
import type { en } from "./locales";
import styles from "./ModelsSection.module.css";

/** The public DeepSeek endpoint shown as the deepseek base-URL placeholder. */
const DEEPSEEK_PUBLIC_BASE_URL = "https://api.deepseek.com";

/** Props of {@link ProviderEditor}. */
export interface ProviderEditorProps {
  /** Provider row being edited; absent when creating from a catalog preset. */
  provider?: LlmProvider;
  /** Display name for the card title. */
  displayName: string;
  /** Hide the title row (the add card renders its own provider select). */
  hideTitle?: boolean;
  /** Models owned by this provider, as loaded (edit mode). */
  models?: readonly LlmModel[];
  /** The catalog preset this card lands a NEW provider row from (create mode). */
  preset?: string;
  /** The catalog entry backing {@link preset}: default endpoint and models. */
  catalogEntry?: LlmCatalogEntry;
  /** Render only the credential field and actions, without provider settings. */
  credentialOnly?: boolean;
  /** Require a newly entered credential before this editor can submit. */
  credentialRequired?: boolean;
  /** Give the credential field initial focus when this editor mounts. */
  autoFocusCredential?: boolean;
  /** Override the dismiss action copy. */
  cancelLabel?: keyof typeof en;
  /** Override the idle commit action copy. */
  submitLabel?: keyof typeof en;
  /** Override the in-flight commit action copy. */
  submitBusyLabel?: keyof typeof en;
  /** Close the editor; `changed` reports whether an Apply committed. */
  onClose: (changed: boolean) => void;
}

/** A stored model row as a plain draft object. */
export function modelToDraft(model: LlmModel): ModelDraft {
  return {
    id: model.model_id,
    ...(model.display_name ? { name: model.display_name } : {}),
    ...(model.context_window ? { contextWindow: model.context_window } : {}),
    ...(model.max_tokens ? { maxTokens: model.max_tokens } : {}),
  };
}

/** Draft models → the batch-sync payload the backend accepts. */
export function draftsToPayload(models: readonly ModelDraft[]) {
  return models
    .filter((model) => typeof model["id"] === "string" && (model["id"] as string).trim().length > 0)
    .map((model) => ({
      model_id: (model["id"] as string).trim(),
      display_name: typeof model["name"] === "string" && model["name"].length > 0 ? model["name"] : null,
      context_window: typeof model["contextWindow"] === "number" ? model["contextWindow"] : null,
      max_tokens: typeof model["maxTokens"] === "number" ? model["maxTokens"] : null,
    }));
}

/** The editor layout the provider family selects. */
function layoutOf(provider: LlmProvider | undefined, preset: string | undefined): "deepseek" | "custom" {
  const name = provider?.name ?? preset ?? "";
  return name === "DeepSeek" ? "deepseek" : "custom";
}

/** Render one provider's editing card. */
export function ProviderEditor(props: ProviderEditorProps): ReactNode {
  const { provider, models, preset, catalogEntry } = props;
  const t = tf;
  // Create mode drafts from the catalog preset's own defaults; edit mode from
  // the stored row. Both feed the same committed baselines the writes diff
  // against, so a retry never rewrites settled fields.
  const [draft, setDraft] = useState<Record<string, unknown>>(() => {
    if (provider === undefined) {
      const presetModels = (catalogEntry?.models ?? []).map((model) => ({
        id: model.model_id,
        ...(model.display_name ? { name: model.display_name } : {}),
      }));
      return {
        ...(catalogEntry?.base_url ? { baseURL: catalogEntry.base_url } : {}),
        models: presetModels,
      };
    }
    return {
      ...(provider.base_url ? { baseURL: provider.base_url } : {}),
      models: (models ?? []).map(modelToDraft),
    };
  });
  const [keyDraft, setKeyDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | undefined>(undefined);
  // The committed baselines the writes are diffed against: a write success
  // advances them immediately so a retry never rewrites settled fields.
  const [committedOriginal] = useState<Record<string, unknown>>(() => ({
    ...(provider === undefined
      ? catalogEntry?.base_url
        ? { baseURL: catalogEntry.base_url }
        : {}
      : provider.base_url
        ? { baseURL: provider.base_url }
        : {}),
    models:
      provider === undefined
        ? (catalogEntry?.models ?? []).map((model) => ({
            id: model.model_id,
            ...(model.display_name ? { name: model.display_name } : {}),
          }))
        : (models ?? []).map(modelToDraft),
  }));
  const disabled = busy;
  const layout = layoutOf(provider, preset);

  const stringAt = (source: unknown, key: string): string | undefined => {
    const value = (source as Record<string, unknown> | undefined)?.[key];
    return typeof value === "string" && value.trim().length > 0 ? value : undefined;
  };

  // The model list is validated by the same per-row checker for both families,
  // so a bad row is named by its position rather than by a blanket message.
  const modelFailure = validateDeepSeekModels(draft["models"]);
  const keyFailure = apiKeyFailure(keyDraft);
  // What a probe or a write must carry: the typed key with paste whitespace
  // removed. A blank field yields an empty string, which both call sites read
  // as "no key supplied" rather than as a key — that is how a card whose
  // provider already has a stored key is edited without re-entering it.
  const keyValue = keyDraft.trim();
  const shownKeyFailure = keyFailure;
  // What the form currently shows, which is what an interrogation must ask:
  // an edited-but-unsaved endpoint, and a key typed but not yet stored.
  const probeBaseURL = stringAt(draft, "baseURL") ?? stringAt(committedOriginal, "baseURL");
  const probe = {
    provider_id: provider?.id,
    ...(probeBaseURL === undefined ? {} : { base_url: probeBaseURL }),
    ...(keyValue.length === 0 ? {} : { api_key: keyValue }),
  };

  /** The write for this card, or a failure message. */
  const applyOnce = async (): Promise<string | undefined> => {
    const nextBaseURL = stringAt(draft, "baseURL") ?? "";
    const baseURLChanged = nextBaseURL !== (stringAt(committedOriginal, "baseURL") ?? "");
    const modelsChanged =
      JSON.stringify(draft["models"] ?? null) !== JSON.stringify(committedOriginal["models"] ?? null);
    if (provider === undefined) {
      // Create: the catalog preset lands a new row (endpoint may be customized
      // on the card); the model catalog then syncs as one batch, exactly as an
      // existing provider's edits do.
      const created = await api.saveLlmProvider({
        preset,
        name: props.displayName,
        ...(nextBaseURL ? { base_url: nextBaseURL } : {}),
        ...(keyValue.length > 0 ? { api_key: keyValue } : {}),
      });
      await api.syncLlmModels({
        provider_id: created.provider.id,
        models: draftsToPayload((draft["models"] as ModelDraft[] | undefined) ?? []),
      });
      setKeyDraft("");
      return undefined;
    }
    // Edit: provider fields the card can see — base URL and the typed key. A
    // blank key keeps the stored one rather than clearing it.
    if (baseURLChanged || keyValue.length > 0) {
      await api.saveLlmProvider({
        id: provider.id,
        ...(baseURLChanged ? { base_url: nextBaseURL } : {}),
        ...(keyValue.length > 0 ? { api_key: keyValue } : {}),
      });
    }
    if (modelsChanged) {
      await api.syncLlmModels({
        provider_id: provider.id,
        models: draftsToPayload((draft["models"] as ModelDraft[] | undefined) ?? []),
      });
    }
    setKeyDraft("");
    return undefined;
  };

  const apply = async (): Promise<void> => {
    setBusy(true);
    setFailure(undefined);
    try {
      const failure = await applyOnce();
      if (failure !== undefined) {
        setFailure(failure);
        return;
      }
      props.onClose(true);
    } catch (error) {
      // A transport failure (disconnect, a request the host refuses) rejects
      // rather than answering; without this the card would stay busy forever
      // with no error shown.
      setFailure(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const keyLocked = false;

  const catalogProps = useMemo(
    () => ({
      models: (draft["models"] as readonly ModelDraft[] | undefined) ?? [],
      overridden: provider !== undefined && (models ?? []).length > 0,
      t,
      disabled,
      onChange: (next: readonly ModelDraft[]) => {
        setDraft((current) => ({ ...current, models: next }));
      },
      onReset: () => {
        setDraft((current) => ({ ...current, models: [] }));
      },
    }),
    [draft, provider, models?.length, t, disabled],
  );

  /**
   * The curated fields of one known provider family. The family arrives
   * narrowed so the per-family branches below are total.
   */
  const curatedFields = (family: "deepseek" | "custom"): ReactNode => (
    <>
      <div className={styles["field"]}>
        <span className={styles["fieldLabel"]}>{t("keyInput")}</span>
        <input
          className={styles["input"]}
          type="password"
          autoComplete="off"
          value={keyDraft}
          placeholder={
            keyLocked
              ? t("keyBlank")
              : provider?.has_api_key && props.credentialRequired !== true
                ? t("keyStored")
                : family === "custom"
                  ? t("keyPlaceholderNative")
                  : t("keyPlaceholder")
          }
          aria-label={t("keyInput")}
          aria-invalid={shownKeyFailure !== undefined}
          required={props.credentialRequired === true}
          autoFocus={props.autoFocusCredential === true}
          disabled={disabled || keyLocked}
          onChange={(event) => {
            setKeyDraft(event.target.value);
          }}
        />
        {shownKeyFailure === undefined ? null : <p className={styles["error"]}>{t(shownKeyFailure)}</p>}
      </div>
      {props.credentialOnly === true ? null : (
        <details className={styles["customized"]}>
          <summary className={styles["customizedSummary"]}>{t("customized")}</summary>
          <div className={styles["customizedBody"]}>
            <div className={styles["field"]}>
              <span className={styles["fieldLabel"]}>{t("baseUrl")}</span>
              <input
                className={styles["input"]}
                type="text"
                value={stringAt(draft, "baseURL") ?? ""}
                placeholder={
                  family === "deepseek"
                    ? DEEPSEEK_PUBLIC_BASE_URL
                    : stringAt(committedOriginal, "baseURL") ?? t("baseUrlDefault")
                }
                aria-label={t("baseUrl")}
                disabled={disabled}
                onChange={(event) => {
                  const value = event.target.value;
                  setDraft((current) => ({ ...current, baseURL: value }));
                }}
              />
            </div>
            {/* Both families edit the same rows through the same contract; only
                the extras differ — the DeepSeek family edits its curated
                catalog, every other family interrogates the endpoint. */}
            {family === "deepseek" ? (
              <DeepSeekModelsEditor
                {...catalogProps}
                defaultContextWindow={undefined}
                defaultMaxTokens={undefined}
              />
            ) : (
              <ModelListEditor {...catalogProps} probe={probe} probeBlocked={keyFailure} />
            )}
          </div>
        </details>
      )}
    </>
  );

  return (
    <div className={props.credentialOnly === true ? styles["addBlock"] : styles["editor"]}>
      {props.hideTitle === true || props.credentialOnly === true ? null : (
        <div className={styles["editorHeader"]}>
          <span className={styles["editorTitle"]}>{props.displayName}</span>
          {provider !== undefined && provider.name !== props.displayName ? (
            <span className={styles["editorRoute"]}>{provider.name}</span>
          ) : null}
        </div>
      )}
      {curatedFields(layout)}
      {failure !== undefined ? <p className={styles["error"]}>{failure}</p> : null}
      {props.credentialOnly === true || modelFailure === undefined ? null : (
        <p className={styles["advancedHint"]}>
          {`${t("model")} ${String(modelFailure.index + 1)}: ${t(modelFailure.key)}`}
        </p>
      )}
      <EditorFooter
        t={t}
        busy={busy}
        submitDisabled={
          disabled ||
          (props.credentialOnly !== true && modelFailure !== undefined) ||
          shownKeyFailure !== undefined ||
          (props.credentialRequired === true && keyValue.length === 0)
        }
        submitLabel={props.submitLabel ?? "apply"}
        submitBusyLabel={props.submitBusyLabel ?? "applying"}
        {...(props.cancelLabel === undefined ? {} : { cancelLabel: props.cancelLabel })}
        onCancel={() => {
          props.onClose(false);
        }}
        onSubmit={() => {
          void apply();
        }}
      />
    </div>
  );
}
