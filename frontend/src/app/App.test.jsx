// @vitest-environment jsdom
import React from "react";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const userGet = vi.fn();
const userPost = vi.fn();
vi.mock("../services/api", async (original) => ({
  ...(await original()),
  userGet: (...args) => userGet(...args),
  userPost: (...args) => userPost(...args),
}));
vi.mock("../components/Seo", () => ({ default: () => null }));
vi.mock("../pages/Public", () => ({
  Home: () => <main>Home page</main>,
  Updates: () => <main>OTT Updates page</main>,
  UpdateArticle: () => <main>OTT Update article</main>,
  Discover: () => null, Browse: () => null, Ott: () => null,
  OttPlatform: () => null, Movie: () => null, Person: () => null,
  Calendar: () => null, Request: () => null, Legal: () => null,
  ContactPage: () => null, TrustPage: () => null,
}));
vi.mock("../pages/Admin", () => ({
  Login: () => null, Dashboard: () => <main>Admin dashboard</main>,
  Requests: () => null, RequestDetail: () => null, ContactRequests: () => null,
  Maintenance: () => null, Movies: () => null, Discovery: () => null,
  ResearchHistory: () => null, ResearchRunDetail: () => null, Comments: () => null,
  ContentSafety: () => null, DataHealth: () => null, Images: () => null,
  OttResearch: () => null, OttGoldSet: () => null, Jobs: () => null,
  Notifications: () => null, Sources: () => null, SystemHealth: () => null,
  Pinterest: () => null,
}));

const renderApp = (path = "/") => render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);

beforeEach(() => {
  userGet.mockReset();
  userPost.mockReset();
  vi.stubEnv("VITE_ENABLE_OTT_UPDATES", "false");
});
afterEach(() => { cleanup(); vi.unstubAllEnvs(); });

describe("role-aware primary navigation", () => {
  it.each([
    ["anonymous", null],
    ["normal user", { id: 1, role: "USER", email: "viewer@example.com" }],
  ])("hides Admin for %s", async (_label, identity) => {
    if (identity) userGet.mockResolvedValue(identity);
    else userGet.mockRejectedValue(new Error("Unauthenticated"));
    renderApp();
    await waitFor(() => expect(userGet).toHaveBeenCalled());
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
  });

  it("shows Admin immediately after OTT Calendar for an administrator", async () => {
    userGet.mockResolvedValue({ id: 2, role: "ADMIN", email: "owner@example.com" });
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Primary navigation" });
    const admin = await within(nav).findByRole("link", { name: "Admin" });
    expect(admin).toHaveAttribute("href", "/admin");
    const links = within(nav).getAllByRole("link");
    expect(links[links.indexOf(admin) - 1]).toHaveTextContent("OTT Calendar");
  });

  it("shows Support only for an eligible authenticated user", async () => {
    userGet.mockResolvedValue({ id: 3, role: "USER", email: "supporter@example.com", support_eligible: true });
    const { unmount } = renderApp();
    expect(await screen.findByRole("link", { name: "Support" })).toHaveAttribute("href", "/support");
    unmount();
    userGet.mockResolvedValue({ id: 4, role: "USER", email: "viewer@example.com", support_eligible: false });
    renderApp();
    await waitFor(() => expect(screen.getByText("viewer@example.com")).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: "Support" })).not.toBeInTheDocument();
  });

  it("preserves the same role visibility on a mobile viewport", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 375 });
    userGet.mockResolvedValue({ id: 2, role: "ADMIN", email: "owner@example.com" });
    const { unmount } = renderApp();
    expect(await screen.findByRole("link", { name: "Admin" })).toBeInTheDocument();
    unmount();
    userGet.mockResolvedValue({ id: 1, role: "USER", email: "viewer@example.com" });
    renderApp();
    await waitFor(() => expect(screen.getByText("viewer@example.com")).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
  });
});

describe("OTT Updates feature flag", () => {
  it("hides navigation and redirects direct routes while disabled", async () => {
    userGet.mockRejectedValue(new Error("Unauthenticated"));
    renderApp("/updates");
    expect(await screen.findByText("Home page")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "OTT Updates" })).not.toBeInTheDocument();
    expect(screen.queryByText("OTT Updates page")).not.toBeInTheDocument();
  });

  it("remains hidden for an administrator while disabled", async () => {
    userGet.mockResolvedValue({ id: 2, role: "ADMIN", email: "owner@example.com" });
    renderApp();
    expect(await screen.findByRole("link", { name: "Admin" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "OTT Updates" })).not.toBeInTheDocument();
  });

  it("restores navigation and route behavior when enabled", async () => {
    vi.stubEnv("VITE_ENABLE_OTT_UPDATES", "true");
    userGet.mockRejectedValue(new Error("Unauthenticated"));
    renderApp("/updates");
    expect(await screen.findByText("OTT Updates page")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "OTT Updates" }).length).toBeGreaterThan(0);
  });
});
