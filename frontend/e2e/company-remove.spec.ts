import { test, expect } from "@playwright/test";
import { dismissOnboarding } from "./support/onboarding";

/**
 * 公司行 ⋯ 菜单 → 移除该公司：确认弹窗弹出（modal.confirm 曾因在渲染
 * <AntdApp> 的组件里调用 useApp() 而拿不到 confirm，点击后静默无效），
 * 确认后公司从侧栏与后端一同消失。
 */

test("company row menu removes company after confirm", async ({ page }) => {
  const created = await page.request.post("/api/projects", {
    data: { name: "Temp", symbol: "00005", market: "HK" },
  });
  expect(created.ok()).toBeTruthy();

  await page.goto("/");
  await dismissOnboarding(page);
  const sidebar = page.locator(".sidebar");
  // 显示偏好默认中文名优先：00005 会被 HKEX 目录解析为「汇丰控股」
  await expect(sidebar).toContainText("汇丰控股");

  // 共享 scratch 库里可能有其他测试遗留的公司，必须按 aria-label 精确定位本行的 ⋯ 菜单
  // （exact 必需：外层 .company-main 容器按钮的 accessible name 也包含该子串）
  await page.getByRole("button", { name: "更多选项：汇丰控股", exact: true }).click();
  await page.getByText("移除该公司", { exact: true }).click();
  const confirm = page.locator(".ant-modal-confirm");
  await expect(confirm).toBeVisible();
  await confirm.getByRole("button", { name: /移\s*除/ }).click();

  await expect(sidebar).not.toContainText("汇丰控股");
  const companies = await page.request.get("/api/companies").then((r) => r.json());
  expect(companies.find((c: { symbol: string }) => c.symbol === "00005")).toBeUndefined();
});
