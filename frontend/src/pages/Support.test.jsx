// @vitest-environment jsdom
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { SupportPage } from "./Support";

const userGet = vi.fn();
vi.mock("../services/api", () => ({ API: "", userGet: (...args) => userGet(...args) }));
vi.mock("./Admin", () => ({ Nav: () => <nav>Admin nav</nav> }));

beforeEach(() => userGet.mockReset());
afterEach(() => cleanup());

it("renders private support status and history without internal details", async () => {
  userGet.mockImplementation((path = "") => Promise.resolve(path.endsWith("/me") ? {
    enabled: true,
    last_contribution: { reference: "public-ref", amount: "12.34", currency: "SGD", payment_date: "2026-09-01T00:00:00Z", payment_method: "PayNow", status: "CONFIRMED" },
  } : {
    items: [{ reference: "public-ref", amount: "12.34", currency: "SGD", payment_date: "2026-09-01T00:00:00Z", payment_method: "PayNow", status: "CONFIRMED" }],
  }));
  render(<MemoryRouter><SupportPage /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Support status" })).toBeInTheDocument();
  expect(screen.getAllByText(/12\.34/).length).toBeGreaterThan(0);
  expect(screen.queryByText("private note")).not.toBeInTheDocument();
  await waitFor(() => expect(userGet).toHaveBeenCalledWith("/api/v1/support/history"));
});
