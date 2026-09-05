import { test, expect } from "@playwright/test";
import { dismissOnboarding } from "./support/onboarding";

/**
 * 公司名全局显示：创建公司时从 HKEX 官方中文目录补全 name_zh（00700 → 腾讯控股），
 * 默认中文名优先；设置 → 外观 可切英文名优先/双语，偏好持久化到后端。
 */

test("company shows Chinese name and display preference persists", async ({ page }) => {
  // 通过 API 造一家公司（后端会 best-effort 从 HKEX 目录补中文名）
  const created = await page.request.post("/api/projects", {
    data: { name: "Tencent", symbol: "00700", market: "HK" },
  });
  expect(created.ok()).toBeTruthy();
  const project = (await created.json()).project;
  expect(project.name_zh).toBe("腾讯控股");

  await page.goto("/");
  await dismissOnboarding(page);
  const sidebar = page.locator(".sidebar");
  await expect(sidebar).toContainText("腾讯控股");

  // 设置 → 外观 → 切英文名优先
  await page.locator(".sidebar-footer-button").click();
  await page.getByRole("button", { name: "外观" }).click();
  const card = page.locator(".display-pref-card");
  await expect(card).toContainText("公司名称显示");
  await card.getByText("英文名优先", { exact: true }).click();
  await page.keyboard.press("Escape");

  await expect(sidebar).toContainText("Tencent");
  await expect(sidebar).not.toContainText("腾讯控股");

  // 刷新后偏好仍生效
  await page.reload();
  await expect(sidebar).toContainText("Tencent");

  // 切回中文名优先
  await page.locator(".sidebar-footer-button").click();
  await page.getByRole("button", { name: "外观" }).click();
  await page.locator(".display-pref-card").getByText("中文名优先", { exact: true }).click();
  await expect(sidebar).toContainText("腾讯控股");
});
