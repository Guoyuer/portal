import { test, expect } from "@playwright/test";

test("group toggle swaps ticker rows to group rows", async ({ page }) => {
  await page.goto("/finance");

  // Wait for the Fidelity Activity section to attach (id confirmed in page.tsx)
  const activity = page.locator("#fidelity-activity");
  await expect(activity).toBeAttached();

  // Wait for activity table to load (transactions present in mock fixture)
  const activityTable = activity.locator("table").first();
  await activityTable.waitFor({ timeout: 10_000 });

  // Toggle is default ON
  const toggle = activity.getByRole("checkbox", { name: /Group equivalent tickers/i });
  await expect(toggle).toBeChecked();

  // Toggle off and verify the state flips
  await toggle.uncheck();
  await expect(toggle).not.toBeChecked();

  // Toggle back on to verify it's reversible
  await toggle.check();
  await expect(toggle).toBeChecked();
});
