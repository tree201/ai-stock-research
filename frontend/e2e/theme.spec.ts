import { test, expect } from "@playwright/test";

/**
 * Theme acceptance: every shell region must follow the active skin.
 * Light → light surfaces with dark text; dark → dark surfaces with light
 * text. We assert on computed luminance rather than hardcoded colors so the
 * palette can evolve without breaking the suite.
 */

const REGIONS = [".sidebar", ".topbar", ".main-shell", ".aui-composer"];

async function luminance(rgb: string): Promise<number> {
  const parts = rgb.match(/\d+/g);
  if (!parts) throw new Error(`not a color: ${rgb}`);
  const [r, g, b] = parts.slice(0, 3).map(Number);
  return (r * 299 + g * 587 + b * 114) / 1000;
}

async function regionLuma(page: import("@playwright/test").Page, selector: string) {
  const bg = await page
    .locator(selector)
    .first()
    .evaluate((el) => getComputedStyle(el).backgroundColor);
  return luminance(bg);
}

for (const theme of ["light", "dark"] as const) {
  test(`${theme} theme paints every shell region consistently`, async ({
    page,
  }) => {
    await page.addInitScript((t) => localStorage.setItem("theme", t), theme);
    await page.goto("/");

    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);

    for (const selector of REGIONS) {
      const luma = await regionLuma(page, selector);
      if (theme === "light") {
        expect(luma, `${selector} background ${luma}`).toBeGreaterThan(128);
      } else {
        expect(luma, `${selector} background ${luma}`).toBeLessThan(128);
      }
    }

    // Sidebar text must flip with the rail surface (light rail → dark text).
    const railTextLuma = await luminance(
      await page
        .locator(".sidebar-action")
        .first()
        .evaluate((el) => getComputedStyle(el).color),
    );
    if (theme === "light") expect(railTextLuma).toBeLessThan(128);
    else expect(railTextLuma).toBeGreaterThan(128);

    // Composer input text follows too.
    const composerTextLuma = await luminance(
      await page
        .locator(".aui-composer textarea")
        .evaluate((el) => getComputedStyle(el).color),
    );
    if (theme === "light") expect(composerTextLuma).toBeLessThan(128);
    else expect(composerTextLuma).toBeGreaterThan(128);
  });
}

test("switching skin in settings persists across reloads", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

  await page.locator(".sidebar-footer-button").click();
  await page.getByRole("button", { name: "外观" }).click();
  await page.getByRole("button", { name: "暗色", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  // The click itself must durably store the choice (not just the DOM state).
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("theme")))
    .toBe("dark");

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  const luma = await regionLuma(page, ".main-shell");
  expect(luma).toBeLessThan(128);
});
