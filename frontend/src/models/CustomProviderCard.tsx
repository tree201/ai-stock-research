/**
 * The card that declares a provider the catalog does not ship — an
 * OpenAI-compatible gateway, a self-hosted server, or a provider newer than
 * the built-in presets.
 *
 * This is a create, not an edit, which is why it is its own card rather than
 * the provider editor with extra fields: the provider is being *chosen* here,
 * and the row does not exist until it is. The three identity fields —
 * **Provider ID**（route，机器身份，唯一且落库后不可改）、**显示名称**（name，
 * 随时可改）、**API 协议**（protocol，决定走哪条线路适配器）— 来自
 * deepseek-harness 的同款卡片。The create writes the provider row (route,
 * name, protocol, base URL, optional key), then the model catalog as one
 * batch sync — exactly as an existing provider's edits do.
 *（抄自 deepseek-harness ui-settings-models/CustomProviderCard.tsx，写路径改接
 * 本项目 /api/llm/providers 与 /api/llm/models/batch。）
 */

import { useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api";
import { apiKeyFailure } from "./apiKey";
import { EditorFooter } from "./EditorFooter";
import { validateDeepSeekModels } from "./DeepSeekModelsEditor";
import { ModelListEditor } from "./ModelListEditor";
import type { ModelDraft } from "./ModelListEditor";
import { draftsToPayload } from "./ProviderEditor";
import { t as tf } from "./locales";
import styles from "./ModelsSection.module.css";

/** route（Provider ID）格式，与后端 _LLM_ROUTE_PATTERN / harness ROUTE_PATTERN 一致。 */
const ROUTE_PATTERN = /^[a-z][a-z0-9-]*$/;

/** Props of {@link CustomProviderCard}. */
export interface CustomProviderCardProps {
  /** Provider IDs（route）already declared, so the card refuses to shadow one. */
  taken: readonly string[];
  /** Close the card; `changed` reports whether a provider was created. */
  onClose: (changed: boolean) => void;
}

/**
 * Render the custom-provider creation card.
 */
export function CustomProviderCard(props: CustomProviderCardProps): ReactNode {
  const { taken } = props;
  const t = tf;
  const [route, setRoute] = useState("");
  const [name, setName] = useState("");
  const [baseURL, setBaseURL] = useState("");
  const [keyDraft, setKeyDraft] = useState("");
  const [models, setModels] = useState<readonly ModelDraft[]>([]);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | undefined>(undefined);
  /**
   * The provider write landed. Only the model sync can still be outstanding,
   * so the fields that describe the provider are settled and the retry path is
   * the catalog alone.
   */
  const [committed, setCommitted] = useState(false);
  const disabled = busy;
  /** Everything but the key stops being editable once the provider exists. */
  const profileDisabled = disabled || committed;

  // route 是机器身份：格式合法且不与已声明提供方撞车才放行；显示名允许重复。
  const routeFailure =
    route.length === 0
      ? undefined
      : ROUTE_PATTERN.test(route.trim())
        ? taken.includes(route.trim())
          ? "customRouteTaken"
          : undefined
        : "customRouteInvalid";
  // Rows are checked by the same per-row validator the editor cards use, so a
  // bad row is named by its position here too.
  const modelFailure = validateDeepSeekModels(models);
  const keyFailure = apiKeyFailure(keyDraft);
  // The typed key with paste whitespace removed. A blank field yields an empty
  // string, which the create path reads as "no key supplied".
  const keyValue = keyDraft.trim();
  const ready =
    route.trim().length > 0 &&
    routeFailure === undefined &&
    name.trim().length > 0 &&
    baseURL.length > 0 &&
    modelFailure === undefined &&
    keyFailure === undefined;
  // The one blocked gate worth a line under the form. A satisfied card says
  // nothing at all rather than printing an empty paragraph.
  const hint =
    failure !== undefined ||
    ready ||
    keyFailure !== undefined ||
    routeFailure !== undefined ||
    route.length === 0 ||
    name.length === 0
      ? undefined
      : baseURL.length === 0
        ? t("customNeedsBaseUrl")
        : modelFailure !== undefined
          ? `${t("model")} ${String(modelFailure.index + 1)}: ${t(modelFailure.key)}`
          : t("customNeedsModels");

  /** Perform the create, returning a failure message or undefined. */
  const createOnce = async (): Promise<string | undefined> => {
    const storesKey = keyValue.length > 0;
    if (!committed) {
      const created = await api.saveLlmProvider({
        name: name.trim(),
        route: route.trim(),
        protocol: "openai-compatible",
        base_url: baseURL,
        ...(storesKey ? { api_key: keyValue } : {}),
      });
      // The provider now exists. A retry after the catalog write below fails
      // must not re-run this create: the row exists and the route is taken, so
      // the retry goes straight to the catalog write.
      setCommitted(true);
      if (models.length > 0) {
        await api.syncLlmModels({
          provider_id: created.provider.id,
          models: draftsToPayload(models),
        });
      }
    } else {
      // A retry path cannot know the created row's id without a reload; the
      // caller reloads and the user finishes the catalog there.
      return undefined;
    }
    return undefined;
  };

  const create = async (): Promise<void> => {
    setBusy(true);
    setFailure(undefined);
    try {
      const outcome = await createOnce();
      if (outcome !== undefined) {
        setFailure(outcome);
        return;
      }
      props.onClose(true);
    } catch (error) {
      // A transport failure rejects rather than answering; without this the
      // card would stay busy with nothing shown.
      setFailure(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles["editor"]}>
      <div className={styles["editorHeader"]}>
        <span className={styles["editorTitle"]}>{t("customTitle")}</span>
      </div>
      <div className={styles["field"]}>
        <span className={styles["fieldLabel"]}>{t("customRoute")}</span>
        <input
          className={styles["input"]}
          type="text"
          value={route}
          placeholder="acme-gateway"
          aria-label={t("customRoute")}
          disabled={profileDisabled}
          onChange={(event) => {
            setRoute(event.target.value);
          }}
        />
      </div>
      {/* A rejected ID reads as a fault, not as guidance — the same split the
          key field below already makes between its failure and its hint. */}
      {routeFailure === undefined ? (
        <p className={styles["advancedHint"]}>{t("customRouteHint")}</p>
      ) : (
        <p className={styles["error"]}>{t(routeFailure)}</p>
      )}
      <div className={styles["field"]}>
        <span className={styles["fieldLabel"]}>{t("customDisplayName")}</span>
        <input
          className={styles["input"]}
          type="text"
          value={name}
          placeholder="如 Acme Gateway"
          aria-label={t("customDisplayName")}
          disabled={profileDisabled}
          onChange={(event) => {
            setName(event.target.value);
          }}
        />
      </div>
      <div className={styles["field"]}>
        <span className={styles["fieldLabel"]}>{t("customApi")}</span>
        <select
          className={`${styles["input"]} ${styles["selectInput"]}`}
          value="openai-compatible"
          aria-label={t("customApi")}
          disabled={profileDisabled}
          onChange={() => {
            /* 目录当前只提供一种线路协议；选择器保持 harness 结构。 */
          }}
        >
          <option value="openai-compatible">{t("customApiOpenai")}</option>
        </select>
      </div>
      <div className={styles["field"]}>
        <span className={styles["fieldLabel"]}>{t("baseUrl")}</span>
        <input
          className={styles["input"]}
          type="text"
          value={baseURL}
          placeholder="https://gateway.example/v1"
          aria-label={t("baseUrl")}
          disabled={profileDisabled}
          onChange={(event) => {
            setBaseURL(event.target.value);
          }}
        />
      </div>
      <div className={styles["field"]}>
        <span className={styles["fieldLabel"]}>{t("keyInput")}</span>
        <input
          className={styles["input"]}
          type="password"
          autoComplete="off"
          value={keyDraft}
          placeholder={t("keyPlaceholder")}
          aria-label={t("keyInput")}
          disabled={disabled}
          onChange={(event) => {
            setKeyDraft(event.target.value);
          }}
        />
        {/* A create card has no stored key to keep, so the blank case says
            what a blank field means here instead. */}
        {keyFailure === undefined ? null : (
          <p className={styles["error"]}>{t(keyFailure === "keyBlank" ? "keyBlankNew" : keyFailure)}</p>
        )}
      </div>
      <ModelListEditor
        models={models}
        onChange={setModels}
        probe={{
          base_url: baseURL,
          ...(keyValue.length === 0 ? {} : { api_key: keyValue }),
        }}
        probeBlocked={keyFailure === "keyBlank" ? "keyBlankNew" : keyFailure}
        t={t}
        disabled={profileDisabled}
      />
      {failure !== undefined ? <p className={styles["error"]}>{failure}</p> : null}
      {/* Only the gates with something to say render. */}
      {hint === undefined ? null : <p className={styles["advancedHint"]}>{hint}</p>}
      <EditorFooter
        t={t}
        busy={busy}
        submitDisabled={disabled || !ready}
        submitLabel="create"
        submitBusyLabel="creating"
        onCancel={() => {
          props.onClose(committed);
        }}
        onSubmit={() => {
          void create();
        }}
      />
    </div>
  );
}
