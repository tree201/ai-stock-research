/** Shared modal chrome for the first-run onboarding step（抄自
 * deepseek-harness ui-settings-models/OnboardingModal，外壳用 antd Modal，
 * headless 渲染本组件自己的标题与正文）。 */

import type { ReactNode } from "react";
import { Modal } from "antd";
import css from "./OnboardingModal.module.css";

/**
 * Render a blocking onboarding dialog over the application.
 * @param props.title - visible dialog title.
 * @param props.children - step-owned body and actions.
 * @returns the modal.
 */
export function OnboardingModal({ title, children }: { title: string; children: ReactNode }): ReactNode {
  return (
    <Modal
      open
      footer={null}
      closable={false}
      maskClosable={false}
      keyboard={false}
      width={600}
      centered
      className={css.dialog as string}
    >
      <div className={css.content}>
        <h2 className={css.title}>{title}</h2>
        <div className={css.body}>{children}</div>
      </div>
    </Modal>
  );
}
