/**
 * Official-DeepSeek first-run step. Readiness comes from the same config
 * snapshot as the Models page: any provider the user can already talk to ends
 * the step, and only a user with none is offered the official DeepSeek route.
 * The step reuses that page's credential editor in the shared onboarding
 * modal, so the key is entered once.
 *（抄自 deepseek-harness ui-settings-models/DeepSeekOnboardingDialog.tsx，
 * 就绪状态从本项目 /api/llm/config 投影。）
 */

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api";
import type { LlmConfig } from "../api";
import { ProviderEditor } from "./ProviderEditor";
import { OnboardingModal } from "./OnboardingModal";
import { t as tf } from "./locales";
import css from "./DeepSeekOnboardingDialog.module.css";

const t = tf;

/** Props delivered by the app shell. */
export interface DeepSeekOnboardingDialogProps {
  /** The config snapshot the shell already holds, for the first render. */
  config: LlmConfig | null;
  /** Push every reloaded config back to the shell. */
  onConfigChange: (config: LlmConfig) => void;
}

/** First-run readiness projected from the shared config snapshot. */
type OnboardingReadiness =
  | { kind: "loading" }
  | { kind: "provider-ready" }
  | { kind: "credential-missing"; rowId?: number }
  | { kind: "preset-missing" }
  | { kind: "adapter-absent" };

/**
 * Project first-run readiness from the provider/config join used by the
 * Models page. ANY usable provider ends it; only when none exists is the
 * official DeepSeek route — the one route the prompt can offer a key field
 * for — asked to help.
 */
export function onboardingReadiness(config: LlmConfig): OnboardingReadiness {
  if (config.providers.some((provider) => provider.has_api_key)) return { kind: "provider-ready" };
  const row = config.providers.find((provider) => provider.name === "DeepSeek");
  if (row !== undefined) return { kind: "credential-missing", rowId: row.id };
  if (config.catalog.some((entry) => entry.name === "DeepSeek")) return { kind: "preset-missing" };
  return { kind: "adapter-absent" };
}

/**
 * Prompt a first-run user for the official DeepSeek credential while no
 * provider can serve requests.
 */
export function DeepSeekOnboardingDialog(props: DeepSeekOnboardingDialogProps): ReactNode {
  const { onConfigChange } = props;
  // The config snapshot rides the shell's state; the dialog keeps its own
  // session dismissal so 「稍后配置」 ends the step without a write.
  const [config, setConfig] = useState<LlmConfig | null>(props.config);
  const [dismissed, setDismissed] = useState(false);
  useEffect(() => {
    setConfig(props.config);
  }, [props.config]);

  if (dismissed || config === null) return null;
  const readiness = onboardingReadiness(config);
  if (readiness.kind === "loading" || readiness.kind === "provider-ready" || readiness.kind === "adapter-absent") {
    return null;
  }

  const row = readiness.kind === "credential-missing"
    ? config.providers.find((provider) => provider.id === readiness.rowId)
    : undefined;
  const entry = readiness.kind === "preset-missing"
    ? config.catalog.find((candidate) => candidate.name === "DeepSeek")
    : undefined;
  if (row === undefined && entry === undefined) return null;

  const finishCredential = (changed: boolean): void => {
    if (!changed) {
      setDismissed(true);
      return;
    }
    void api
      .llmConfig()
      .then((next) => {
        setConfig(next);
        onConfigChange(next);
      })
      .catch(() => setDismissed(true));
  };

  return (
    <OnboardingModal title={t("onboardingTitle")}>
      <p className={css.description}>{t("onboardingDescription")}</p>
      <div className={css.editor}>
        {row !== undefined ? (
          <ProviderEditor
            provider={row}
            displayName={row.name}
            models={[]}
            hideTitle
            credentialOnly
            credentialRequired
            autoFocusCredential
            cancelLabel="onboardingLater"
            submitLabel="onboardingSave"
            submitBusyLabel="onboardingSaving"
            onClose={finishCredential}
          />
        ) : entry !== undefined ? (
          <ProviderEditor
            preset={entry.key}
            catalogEntry={entry}
            displayName={entry.name}
            hideTitle
            credentialOnly
            credentialRequired
            autoFocusCredential
            cancelLabel="onboardingLater"
            submitLabel="onboardingSave"
            submitBusyLabel="onboardingSaving"
            onClose={finishCredential}
          />
        ) : null}
      </div>
    </OnboardingModal>
  );
}
