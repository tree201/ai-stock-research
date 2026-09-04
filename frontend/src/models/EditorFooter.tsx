/**
 * The action row every provider card ends with: dismiss on the left, commit on
 * the right.
 *（抄自 deepseek-harness ui-settings-models/EditorFooter.tsx）
 */

import type { ReactNode } from "react";
import type { en } from "./locales";
import styles from "./ModelsSection.module.css";

/** Props of {@link EditorFooter}. */
export interface EditorFooterProps {
  /** Localizer for the row's own labels. */
  t: (key: keyof typeof en) => string;
  /** Whether a commit is in flight; holds Cancel and swaps the commit label. */
  busy: boolean;
  /** Whether the commit is refused, as judged by the owning card. */
  submitDisabled: boolean;
  /** Commit label while idle. */
  submitLabel: keyof typeof en;
  /** Commit label while a commit is in flight. */
  submitBusyLabel: keyof typeof en;
  /** Dismiss label; defaults to the settings editor copy. */
  cancelLabel?: keyof typeof en;
  /** Dismiss the card without committing. */
  onCancel: () => void;
  /** Run the card's commit. */
  onSubmit: () => void;
}

/**
 * Render one provider card's action row.
 */
export function EditorFooter(props: EditorFooterProps): ReactNode {
  const { t } = props;
  return (
    <div className={styles["editorActions"]}>
      <button
        type="button"
        className={styles["secondaryButton"]}
        disabled={props.busy}
        onClick={props.onCancel}
      >
        {t(props.cancelLabel ?? "cancel")}
      </button>
      <button
        type="button"
        className={styles["primaryButton"]}
        disabled={props.submitDisabled}
        onClick={props.onSubmit}
      >
        {props.busy ? t(props.submitBusyLabel) : t(props.submitLabel)}
      </button>
    </div>
  );
}
