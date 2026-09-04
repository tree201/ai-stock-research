/**
 * Models settings section: the provider rows joined from the configurable
 * directory and credential states, with one editor card at a time. Rows expose
 * only the API-key state through accessible solid configured or missing dots.
 * A whole-section provider without a configured key renders as its open setup
 * card instead of a row, but only in the first-run posture — no provider on
 * the page can serve requests yet — and only until the user closes that card.
 * The add flow is the custom-provider card. Every mutation writes through the
 * wire, while a provider removal first requires confirmation; the page
 * re-renders from the post-apply reload.
 *（抄自 deepseek-harness ui-settings-models/ModelsSection.tsx + store.ts，
 * 状态接本项目 /api/llm/config。）
 */

import { useState } from "react";
import type { ReactNode } from "react";
import { Modal } from "antd";
import { api } from "../api";
import type { LlmCatalogEntry, LlmConfig, LlmProvider, LlmModel } from "../api";
import { CustomProviderCard } from "./CustomProviderCard";
import { ProviderEditor } from "./ProviderEditor";
import { IconPlus } from "./DeepSeekModelsEditor";
import { t as tf } from "./locales";
import type { en } from "./locales";
import styles from "./ModelsSection.module.css";

const t = tf;

/** One provider row the page renders. */
export interface ProviderRow {
  /** The provider row as the backend stores it. */
  provider: LlmProvider;
  /** Models owned by this provider, as loaded. */
  models: readonly LlmModel[];
  /** Whether an API key is stored for this provider. */
  configured: boolean;
}

/** Page snapshot. */
export interface ModelsSettingsState {
  status: "idle" | "loading" | "ready" | "error";
  /** Whole-load failure text; row-level write failures stay in the editor. */
  error: string | null;
  /** The loaded config, once ready. */
  config: LlmConfig | null;
}

/** Human text for a rejected wire call. */
export function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Whether a whole-section provider still needs its first key: an unconfigured
 * credential opens the setup card instead of showing a row. This is the
 * first-run posture alone — a user who can already reach some provider gets an
 * ordinary row with the missing-key dot, since nothing here is blocking them.
 */
export function needsSetup(row: ProviderRow, anyUsable: boolean, firstPending: boolean): boolean {
  if (anyUsable) return false;
  if (row.configured) return false;
  return firstPending;
}

/** Provider identity shared by row actions and confirmation copy. */
export interface ProviderIdentity {
  /** Stable provider id. */
  id: number
  /** Human-facing provider name. */
  displayName: string;
}

/** Stable visible and accessible identity for one provider target. */
export function providerTargetLabel(target: ProviderIdentity): string {
  return target.displayName;
}

/** Replace the one provider placeholder in localized destructive-action copy. */
export function providerCopy(template: string, target: ProviderIdentity): string {
  return template.replace("{provider}", () => providerTargetLabel(target));
}

/**
 * Remove one provider and its stored key. The backend cascades the catalog;
 * the removal is idempotent, so a retry after a transport failure is safe.
 */
export async function removeProvider(target: ProviderIdentity): Promise<string | undefined> {
  try {
    await api.removeLlmProvider(target.id);
    return undefined;
  } catch (error) {
    return messageOf(error);
  }
}

/** Props delivered by the settings shell. */
export interface ModelsSectionProps {
  /** The config snapshot the shell already holds, for the first render. */
  config: LlmConfig | null;
  /** Push every reloaded config back to the shell (the composer picker reads it). */
  onConfigChange: (config: LlmConfig) => void;
}

/**
 * Render the Models section content column.
 */
export function ModelsSection(props: ModelsSectionProps): ReactNode {
  const { onConfigChange } = props;
  const [state, setState] = useState<ModelsSettingsState>(() => ({
    status: props.config === null ? "idle" : "ready",
    error: null,
    config: props.config,
  }));
  const [editing, setEditing] = useState<number | undefined>(undefined);
  const [adding, setAdding] = useState(false);
  const [addChoice, setAddChoice] = useState<LlmCatalogEntry | undefined>(undefined);
  const [deleteTarget, setDeleteTarget] = useState<ProviderIdentity | undefined>(undefined);
  const [deleting, setDeleting] = useState(false);
  const [deleteFailure, setDeleteFailure] = useState<string | undefined>(undefined);
  const [savedTarget, setSavedTarget] = useState<ProviderIdentity | undefined>(undefined);
  const [declaring, setDeclaring] = useState(false);
  const [dismissedSetup, setDismissedSetup] = useState<ReadonlySet<number>>(() => new Set());

  const load = async (): Promise<void> => {
    setState((current) => ({ ...current, status: "loading", error: null }));
    try {
      const config = await api.llmConfig();
      setState({ status: "ready", error: null, config });
      onConfigChange(config);
    } catch (error) {
      setState((current) => ({ ...current, status: "error", error: messageOf(error) }));
    }
  };

  const announceSaved = (target: ProviderIdentity): void => {
    // Announced only once the refreshed directory is in the snapshot the
    // notice reads its name from: an apply can rename the route, and the
    // target captured when the card opened still carries the old name.
    void api.llmConfig().then((config) => {
      setState({ status: "ready", error: null, config });
      onConfigChange(config);
      setSavedTarget(target);
    });
  };

  const closeEditor = (changed: boolean, target: ProviderIdentity): void => {
    setEditing(undefined);
    setAdding(false);
    setAddChoice(undefined);
    setDeclaring(false);
    if (changed) announceSaved(target);
  };

  /**
   * Close a setup card, which owns none of the state above. Dismissal is this
   * card's own — the provider falls back to an ordinary row for the rest of
   * the session, and reopens through Edit.
   */
  const closeSetup = (changed: boolean, target: ProviderIdentity): void => {
    setDismissedSetup((previous) => new Set([...previous, target.id]));
    if (changed) announceSaved(target);
  };

  const closeDelete = (): void => {
    if (deleting) return;
    setDeleteTarget(undefined);
    setDeleteFailure(undefined);
  };

  const confirmDelete = (): void => {
    if (deleteTarget === undefined || deleting) return;
    setDeleting(true);
    setDeleteFailure(undefined);
    void removeProvider(deleteTarget)
      .then((failure) => {
        if (failure !== undefined) {
          setDeleteFailure(failure);
          return;
        }
        setDeleteTarget(undefined);
        void load();
      })
      .finally(() => {
        setDeleting(false);
      });
  };

  if (state.status === "idle") void load();
  if (state.status === "error") {
    const errorText = state.error ?? "";
    return (
      <div className={styles["section"]}>
        <p className={styles["error"]}>{`${t("loadFailed")}: ${errorText}`}</p>
        <button
          type="button"
          className={styles["secondaryButton"]}
          onClick={() => {
            void load();
          }}
        >
          {t("retry")}
        </button>
      </div>
    );
  }
  if (state.config === null) return <div className={styles["section"]} />;

  const config = state.config;
  const rows: ProviderRow[] = config.providers.map((provider) => ({
    provider,
    models: config.models.filter((model) => model.provider_id === provider.id),
    configured: provider.has_api_key,
  }));

  // One fact decides the first-run posture on this page: whether the user
  // already has a provider to talk to.
  const anyUsable = rows.some((row) => row.configured);
  const firstPendingProvider = rows.find((row) => !row.configured);
  // Presets the catalog knows that no row uses yet (deepseek-harness 的
  // configurable directory：目录条目出现在「添加提供方」而不是默认铺满整页).
  const addable = config.catalog;
  // The saved provider as the directory currently names it. The name is what
  // the apply cannot change for a preset create, so the lookup falls back to
  // it when the row was only just landed (its id was unknown at close time).
  const savedRow =
    savedTarget === undefined
      ? undefined
      : rows.find(
          (row) => row.provider.id === savedTarget.id || row.provider.name === savedTarget.displayName,
        );
  const savedIdentity =
    savedRow === undefined ? savedTarget : { id: savedRow.provider.id, displayName: savedRow.provider.name };

  return (
    <div className={styles["section"]}>
      <h2 className={styles["title"]}>{t("title")}</h2>
      <p className={styles["intro"]}>{t("intro")}</p>
      {savedIdentity === undefined ? null : (
        <p className={styles["savedNotice"]} role="status" aria-live="polite">
          {providerCopy(t("savedProvider"), savedIdentity)}
        </p>
      )}
      <ul className={styles["rows"]}>
        {rows.map((row) => {
          const target: ProviderIdentity = { id: row.provider.id, displayName: row.provider.name };
          if (
            needsSetup(row, anyUsable, firstPendingProvider?.provider.id === row.provider.id) &&
            !dismissedSetup.has(row.provider.id)
          ) {
            // First-run posture: the provider exists but has no key — the
            // setup card IS its presence on the page, until the user closes it.
            return (
              <li key={row.provider.id} className={styles["setupCard"]}>
                <ProviderEditor
                  provider={row.provider}
                  displayName={row.provider.name}
                  models={row.models}
                  autoFocusCredential
                  onClose={(changed) => {
                    closeSetup(changed, target);
                  }}
                />
              </li>
            );
          }
          const open = !adding && editing === row.provider.id;
          const credentialConfigured = row.provider.has_api_key;
          const credentialMissing = !credentialConfigured;
          return (
            <li key={row.provider.id} className={styles["rowCard"]}>
              <div className={styles["rowHead"]}>
                <span className={styles["rowIdentity"]}>
                  <span className={styles["rowName"]}>{row.provider.name}</span>
                  {row.provider.builtin === 0 ? (
                    <span className={styles["rowTag"]}>{t("customTag")}</span>
                  ) : null}
                  {credentialConfigured ? (
                    <span
                      className={`${styles["credentialDot"]} ${styles["credentialDotConfigured"]}`}
                      role="img"
                      aria-label={t("credentialConfigured")}
                      title={t("credentialConfigured")}
                    />
                  ) : credentialMissing ? (
                    <span
                      className={`${styles["credentialDot"]} ${styles["credentialDotMissing"]}`}
                      role="img"
                      aria-label={t("credentialMissing")}
                      title={t("credentialMissing")}
                    />
                  ) : null}
                  <span className={styles["rowRoute"]}>{row.provider.base_url}</span>
                </span>
                <span className={styles["rowActions"]}>
                  <button
                    type="button"
                    className={styles["secondaryButton"]}
                    aria-label={providerCopy(t("editProvider"), target)}
                    onClick={() => {
                      setSavedTarget(undefined);
                      // One card at a time: leaving `declaring` set would show
                      // the create card beside this editor, and closing either
                      // one discards the other's draft.
                      setDeclaring(false);
                      setAdding(false);
                      setEditing(open ? undefined : row.provider.id);
                    }}
                  >
                    {t("edit")}
                  </button>
                  <button
                    type="button"
                    className={styles["dangerButton"]}
                    aria-label={providerCopy(t("removeProvider"), target)}
                    onClick={() => {
                      setSavedTarget(undefined);
                      setDeleteFailure(undefined);
                      setDeleteTarget(target);
                    }}
                  >
                    {t("remove")}
                  </button>
                </span>
              </div>
              {open ? (
                <ProviderEditor
                  provider={row.provider}
                  displayName={row.provider.name}
                  models={row.models}
                  onClose={(changed) => {
                    closeEditor(changed, target);
                  }}
                />
              ) : null}
            </li>
          );
        })}
      </ul>
      <div className={styles["addBlock"]}>
        {adding && addChoice !== undefined ? (
          <div className={styles["addCard"]}>
            <div className={styles["field"]}>
              <span className={styles["fieldLabel"]}>{t("provider")}</span>
              <select
                className={`${styles["input"]} ${styles["selectInput"]}`}
                value={addChoice.key}
                aria-label={t("provider")}
                onChange={(event) => {
                  const row = addable.find((candidate) => candidate.key === event.target.value);
                  /* v8 ignore next -- the select only lists addable rows */
                  if (row === undefined) return;
                  setAddChoice(row);
                }}
              >
                {addable.map((entry) => (
                  <option key={entry.key} value={entry.key}>
                    {entry.name}
                  </option>
                ))}
              </select>
            </div>
            <ProviderEditor
              key={addChoice.key}
              preset={addChoice.key}
              catalogEntry={addChoice}
              displayName={addChoice.name}
              hideTitle
              onClose={(changed) => {
                closeEditor(changed, { id: -1, displayName: addChoice.name });
              }}
            />
          </div>
        ) : declaring ? (
          <div className={styles["addCard"]}>
            <CustomProviderCard
              taken={rows.map((row) => row.provider.route)}
              onClose={(changed) => {
                setDeclaring(false);
                if (changed) void load();
              }}
            />
          </div>
        ) : (
          // One row for the two ways to gain a provider: adopt one the catalog
          // already knows, or declare one it does not. Side by side and
          // equal-width so they read as siblings and line up with the rows
          // above, rather than two pills of different lengths.
          <div className={styles["addActions"]}>
            <button
              type="button"
              className={styles["addButton"]}
              disabled={addable.length === 0}
              onClick={() => {
                const first = addable[0];
                /* v8 ignore next -- the button is disabled while nothing is addable */
                if (first === undefined) return;
                setSavedTarget(undefined);
                setDeclaring(false);
                setEditing(undefined);
                setAdding(true);
                setAddChoice(first);
              }}
            >
              <IconPlus />
              {t("add")}
            </button>
            <button
              type="button"
              className={styles["addButton"]}
              onClick={() => {
                setSavedTarget(undefined);
                setAdding(false);
                setAddChoice(undefined);
                setEditing(undefined);
                setDeclaring(true);
              }}
            >
              <IconPlus />
              {t("customAdd")}
            </button>
          </div>
        )}
      </div>
      <Modal
        open={deleteTarget !== undefined}
        onCancel={closeDelete}
        title={deleteTarget === undefined ? "" : providerCopy(t("deleteTitle"), deleteTarget)}
        footer={null}
        width={480}
      >
        <p className={styles["advancedHint"]}>
          {deleteTarget === undefined
            ? ""
            : providerCopy(
                rows.some((row) => row.provider.id === deleteTarget.id && row.provider.has_api_key)
                  ? t("deleteDescriptionWithCredential")
                  : t("deleteDescription"),
                deleteTarget,
              )}
        </p>
        {deleteFailure === undefined ? null : <p className={styles["error"]}>{deleteFailure}</p>}
        <div className={styles["editorActions"]}>
          <button
            type="button"
            className={styles["secondaryButton"]}
            disabled={deleting}
            onClick={closeDelete}
          >
            {t("cancel")}
          </button>
          <button
            type="button"
            className={`${styles["secondaryButton"]} ${styles["deleteConfirm"]}`}
            disabled={deleting}
            onClick={confirmDelete}
          >
            {deleteTarget === undefined
              ? ""
              : providerCopy(deleting ? t("deleting") : t("deleteConfirm"), deleteTarget)}
          </button>
        </div>
      </Modal>
    </div>
  );
}
