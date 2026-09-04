import { test, expect } from "@playwright/test";

/**
 * Model hub acceptance（照抄 deepseek-harness ModelSelect 交互）：
 * 触发器显示「模型 · 档位」；root 面板两行 cell（模型 / 推理等级）各自
 * 钻入列表；选模型不带档位 → 后端应用默认档；档位可切、持久化到后端。
 */

test("model picker drills into model and effort panes", async ({ page }) => {
  // 未配置密钥的供应商模型为禁用态，先给 DeepSeek / 智谱配上密钥
  await page.request.post("/api/llm/providers", { data: { id: 1, name: "DeepSeek", base_url: "https://api.deepseek.com/v1", api_key: "sk-e2e" } });
  await page.request.post("/api/llm/providers", { data: { id: 3, name: "智谱 GLM", base_url: "https://open.bigmodel.cn/api/paas/v4", api_key: "sk-e2e" } });
  await page.goto("/");
  await expect(page.locator(".picker-trigger")).toContainText("选择模型");

  await page.locator(".picker-trigger").click();
  // root 面板：两行钻入 cell
  await expect(page.locator(".picker-cell", { hasText: "模型" })).toBeVisible();

  await page.locator(".picker-cell", { hasText: "模型" }).click();
  await expect(page.locator(".picker-group-title", { hasText: "DeepSeek" })).toBeVisible();
  await expect(page.locator(".picker-group-title", { hasText: "智谱 GLM" })).toBeVisible();

  // 选模型不带档位 → 后端落默认档（GLM-4.6 默认 high）
  await page.locator(".picker-option", { hasText: "GLM-4.6" }).click();
  await expect(page.locator(".picker-trigger")).toContainText("GLM-4.6");
  await expect(page.locator(".picker-trigger-effort")).toHaveText("高");

  // 刷新后选择仍在（持久化到后端）
  await page.reload();
  await expect(page.locator(".picker-trigger")).toContainText("GLM-4.6");
  await expect(page.locator(".picker-trigger-effort")).toHaveText("高");

  // 推理等级钻入面板：Default 跟随默认档 + 各档位可切
  await page.locator(".picker-trigger").click();
  await page.locator(".picker-cell", { hasText: "推理等级" }).click();
  await expect(page.locator(".picker-option", { hasText: "Default" })).toBeVisible();
  await page.locator(".picker-option", { hasText: "关闭" }).click();
  await expect(page.locator(".picker-trigger-effort")).toHaveText("关闭");
});

test("model without reasoning levels omits the effort row", async ({ page }) => {
  await page.request.post("/api/llm/providers", { data: { id: 1, name: "DeepSeek", base_url: "https://api.deepseek.com/v1", api_key: "sk-e2e" } });
  await page.goto("/");
  await page.locator(".picker-trigger").click();
  await page.locator(".picker-cell", { hasText: "模型" }).click();
  await page.locator(".picker-option", { hasText: "DeepSeek-V3（对话）" }).click();
  await expect(page.locator(".picker-trigger")).toContainText("DeepSeek-V3");
  // 固定模式模型：root 面板不显示「推理等级」行，触发器无档位后缀
  await page.locator(".picker-trigger").click();
  await expect(page.locator(".picker-cell", { hasText: "推理等级" })).toHaveCount(0);
  await expect(page.locator(".picker-trigger-effort")).toHaveCount(0);
});
