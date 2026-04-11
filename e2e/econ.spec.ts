import { test, expect } from "@playwright/test";

test.describe("Economy Dashboard", () => {
  test("econ page is statically generated", async () => {
    // Verify the build output contains econ.html
    const fs = await import("fs");
    expect(fs.existsSync("out/econ.html")).toBe(true);
  });

  test("sidebar economy link is enabled", async ({ page }) => {
    await page.goto("/finance");
    await page.getByText("Dashboard for Yuer").waitFor({ timeout: 5000 });
    const sidebar = page.locator("aside").first();
    const econLink = sidebar.locator("a").filter({ hasText: "Economy" });
    await expect(econLink).toBeVisible();
    // Should not have "soon" badge
    await expect(econLink.locator("text=soon")).not.toBeVisible();
    // Should link to /econ
    await expect(econLink).toHaveAttribute("href", "/econ");
  });

  test("econ page renders", async ({ page }) => {
    await page.goto("/econ");
    // Econ page should at least show the sidebar with Economy link active
    await expect(page.locator("aside").first()).toBeVisible({ timeout: 5000 });
  });
});
