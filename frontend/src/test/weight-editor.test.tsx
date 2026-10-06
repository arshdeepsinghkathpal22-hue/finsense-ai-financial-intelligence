import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WeightEditor } from "../components/PortfolioEditor";
import { jsonResponse, mockFetch } from "./utils";

function renderEditor(rows: { fundId: number | null; weightPct: string }[]) {
  mockFetch(() => jsonResponse(200, { items: [{ id: 1, name: "Fund A", scheme_code: "A" }, { id: 2, name: "Fund B", scheme_code: "B" }], total: 2, page: 1, page_size: 100 }));
  const client = new QueryClient();
  render(<QueryClientProvider client={client}><WeightEditor rows={rows} onChange={() => {}} /></QueryClientProvider>);
}

describe("WeightEditor", () => {
  it("shows the running total and flags totals other than 100%", () => {
    renderEditor([{ fundId: 1, weightPct: "70" }, { fundId: 2, weightPct: "20" }]);
    expect(screen.getByRole("status")).toHaveTextContent("Total 90.00% (must be 100%)");
  });
  it("accepts a complete allocation", () => {
    renderEditor([{ fundId: 1, weightPct: "70" }, { fundId: 2, weightPct: "30" }]);
    expect(screen.getByRole("status")).toHaveTextContent("Total 100.00%");
    expect(screen.getByRole("status")).not.toHaveTextContent("must be");
  });
});
