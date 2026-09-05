import { test, expect } from "@playwright/test";
import { dismissOnboarding } from "./support/onboarding";

/**
 * Composer 底栏布局验收（对照 Trae 输入框）：
 * 左下 = 权限授权 pill（手动审批/自动审批/完全访问），右下 = 模型 pill + 发送按钮。
 * 授权模式持久化到后端，完全访问时触发器转警示色。
 */

test("approval pill sits bottom-left, model pill bottom-right of composer", async ({ page }) => {
  await page.goto("/");
  await dismissOnboarding(page);
  const approval = page.locator(".composer .approval-trigger");
  const model = page.locator(".composer .model-picker .picker-trigger");
  const send = page.locator(".composer .send");
  await expect(approval).toBeVisible();
  await expect(model).toBeVisible();
  await expect(send).toBeVisible();

  // 左右位置：权限在模型左侧
  const [approvalBox, modelBox] = await Promise.all([approval.boundingBox(), model.boundingBox()]);
  expect(approvalBox && modelBox).toBeTruthy();
  expect(approvalBox!.x + approvalBox!.width).toBeLessThan(modelBox!.x);

  // 默认手动审批；菜单含三档说明
  await expect(approval).toContainText("手动审批");
  await approval.click();
  await expect(page.locator(".approval-menu-head")).toContainText("如何批准");
  for (const label of ["手动审批", "自动审批", "完全访问"]) {
    await expect(page.locator(".approval-menu .picker-option", { hasText: label })).toBeVisible();
  }

  // 切换到完全访问 → 触发器更新为警示色态并持久化
  await page.locator(".approval-menu .picker-option", { hasText: "完全访问" }).click();
  await expect(approval).toContainText("完全访问");
  await expect(page.locator(".composer .approval-trigger.approval-full")).toHaveCount(1);

  await page.reload();
  await expect(page.locator(".composer .approval-trigger")).toContainText("完全访问");

  // 切回手动审批
  await page.locator(".composer .approval-trigger").click();
  await page.locator(".approval-menu .picker-option", { hasText: "手动审批" }).click();
  await expect(page.locator(".composer .approval-trigger")).toContainText("手动审批");
});
