// @vitest-environment jsdom
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { Dashboard, Jobs } from "./Admin";
import { Request } from "./Public";

afterEach(() => vi.unstubAllGlobals());

it("shows the admin guard when the session is absent", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }));
  render(<MemoryRouter><Dashboard/></MemoryRouter>);
  expect(await screen.findByText(/Unauthenticated/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sign in" })).toBeInTheDocument();
});

it("renders the validated movie request form", () => {
  render(<MemoryRouter><Request/></MemoryRouter>);
  expect(screen.getByRole("heading", { name: "Request a movie" })).toBeInTheDocument();
  expect(screen.getByPlaceholderText("Email")).toHaveAttribute("type", "email");
  expect(screen.getByRole("button", { name: "Submit request" })).toBeInTheDocument();
});

it("shows real backfill coverage and queues a selected repair", async () => {
  const fetch = vi.fn().mockImplementation(url => Promise.resolve({ ok: true, json: async () => url.includes("/backfills") ? { progress: [{ operation: "tmdb.metadata_backfill", status: "PAUSED", processed: 100, total: 12281, remaining: 12181, failed: 0 }], coverage: { movies: 12281, movies_with_cast: 100 }, configuration: { tmdb: true, imdb: false } } : [] }));
  vi.stubGlobal("fetch", fetch);
  render(<MemoryRouter><Jobs/></MemoryRouter>);
  expect(await screen.findByText("12281")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Metadata" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/backfills/metadata/start"), expect.objectContaining({ method: "POST" })));
});
