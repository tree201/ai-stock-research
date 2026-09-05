import type { Page } from "@playwright/test";

/** 空库首启会出现 DeepSeek 引导弹窗，挡住后续交互；点击「稍后配置」关闭。 */
export async function dismissOnboarding(page: Page): Promise<void> {
  const later = page.locator(".ant-modal").getByRole("button", { name: "稍后配置" });
  if (await later.count()) {
    await later.first().click().catch(() => undefined);
    await page.waitForTimeout(200);
  }
}
