// @vitest-environment jsdom

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { GroupChartDialog } from "./group-dialog";

// jsdom doesn't implement HTMLDialogElement.showModal; polyfill lightly:
if (typeof HTMLDialogElement !== "undefined" && !HTMLDialogElement.prototype.showModal) {
  // eslint-disable-next-line @typescript-eslint/no-empty-function
  HTMLDialogElement.prototype.showModal = function () { (this as HTMLDialogElement).open = true; };
  // eslint-disable-next-line @typescript-eslint/no-empty-function
  HTMLDialogElement.prototype.close = function () { (this as HTMLDialogElement).open = false; };
}

describe("GroupChartDialog", () => {
  it("renders group display name + constituent tickers", () => {
    render(
      <GroupChartDialog
        groupKey="sp500"
        dailyTickers={[]}
        fidelityTxns={[]}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("S&P 500")).toBeTruthy();
    expect(screen.getByText(/VOO.*IVV.*SPY/)).toBeTruthy();
  });
});
