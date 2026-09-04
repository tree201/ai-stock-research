import { test, expect } from "@playwright/test";

/**
 * Model hub acceptance: the composer model picker lists builtin domestic
 * providers, switching a model persists server-side, and reasoning-level
 * options appear for models that support thinking controls.
 */

test("model picker switches model and reasoning level", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".picker-trigger")).toContainText("选择模型");

  await page.locator(".picker-trigger").click();
  await expect(page.locator(".picker-group", { hasText: "DeepSeek" })).toBeVisible();
  await expect(page.locator(".picker-group", { hasText: "智谱 GLM" })).toBeVisible();

  await page.locator(".picker-item", { hasText: "GLM-4.6" }).click();
  await expect(page.locator(".picker-trigger")).toContainText("GLM-4.6");

  // GLM-4.6 支持强度档位（关闭/低/中/高），切换档位即保存
  await expect(page.locator(".level-option")).toHaveCount(4);
  await page.locator(".level-option", { hasText: "高" }).click();
  await expect(page.locator(".level-option.active")).toHaveText("高");

  // 刷新后选择仍在（持久化到后端）
  await page.reload();
  await expect(page.locator(".picker-trigger")).toContainText("GLM-4.6");
  await expect(page.locator(".level-option.active")).toHaveText("高");
});

test("model without thinking levels shows fixed mode", async ({ page }) => {
  await page.goto("/");
  await page.locator(".picker-trigger").click();
  await page.locator(".picker-item", { hasText: "DeepSeek-V3（对话）" }).click();
  await expect(page.locator(".picker-trigger")).toContainText("DeepSeek-V3");
  await expect(page.locator(".level-empty")).toHaveText("固定模式");
});
