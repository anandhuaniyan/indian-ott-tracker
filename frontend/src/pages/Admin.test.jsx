// @vitest-environment jsdom
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { Dashboard, Jobs, OttResearch, Requests } from "./Admin";
import { Request } from "./Public";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the admin guard when the session is absent", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }),
  );
  render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  );
  expect(await screen.findByText(/Unauthenticated/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sign in" })).toBeInTheDocument();
});

it("renders the validated movie request form", () => {
  render(
    <MemoryRouter>
      <Request />
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("heading", { name: "Request a movie" }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Email *")).toHaveAttribute("type", "email");
  expect(screen.getByLabelText("ID *")).toHaveAttribute("type", "number");
  expect(
    screen.getByRole("button", { name: "Submit request" }),
  ).toBeInTheDocument();
});

it("renders responsive request snapshots, SLA state and email actions", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      items: [{
        request_id: "REQ-123", verified_title: "A Very Long Verified Movie Title", original_title: "Original",
        movie_external_id: 123, imdb_id: "tt1234567", release_date: "2026-01-02", language: "ml", language_name: "Malayalam",
        poster_path: "/poster.jpg", director: "Director", email: "a-very-long-requester-address@example.com", details: "Details",
        status: "FOUND", created_at: "2026-01-01T00:00:00Z", age_seconds: 50000, target_seconds: 120000, local_movie_id: null,
        emails: {
          confirmation: { status: "FAILED", sent_at: null, last_error: "SMTP unavailable" },
          completion: { status: "PENDING", sent_at: null, last_error: null },
          rejection: { status: "PENDING", sent_at: null, last_error: null },
        },
      }],
    }),
  }));
  render(<MemoryRouter><Requests /></MemoryRouter>);
  expect(await screen.findByText("A Very Long Verified Movie Title")).toBeInTheDocument();
  expect(screen.getByText("Malayalam")).toBeInTheDocument();
  expect(screen.getByText(/remaining/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry confirmation" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add Movie" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Mark Added" })).toBeInTheDocument();
});

it("shows real backfill coverage and queues a selected repair", async () => {
  const fetch = vi
    .fn()
    .mockImplementation((url) =>
      Promise.resolve({
        ok: true,
        json: async () =>
          url.includes("/backfills")
            ? {
                progress: [
                  {
                    operation: "tmdb.metadata_backfill",
                    status: "PAUSED",
                    processed: 100,
                    total: 12281,
                    remaining: 12181,
                    failed: 0,
                  },
                ],
                coverage: { movies: 12281, movies_with_cast: 100 },
                configuration: { tmdb: true, imdb: false },
              }
            : [],
      }),
    );
  vi.stubGlobal("fetch", fetch);
  render(
    <MemoryRouter>
      <Jobs />
    </MemoryRouter>,
  );
  expect(await screen.findByText("12281")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Metadata" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/backfills/metadata/start"),
      expect.objectContaining({ method: "POST" }),
    ),
  );
});

it("explains release eligibility on the OTT research page", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        daily_usage: { remaining: 20 },
        tavily_usage: { remaining: 800 },
        items: [
          {
            movie_id: 10,
            id: 99,
            movie: "Upcoming Film",
            theatrical_release_date: "2026-10-10",
            release_status: "Upcoming",
            eligibility: "WAITING_RELEASE",
            eligibility_label: "Waiting for theatrical release",
            status: "WAITING_RELEASE",
            platform: null,
            date: null,
            source: null,
            url: null,
            confidence: 0,
            attempts: 0,
            last_check: null,
            next_check: "2026-10-17T00:00:00Z",
          },
        ],
      }),
    }),
  );
  render(
    <MemoryRouter>
      <OttResearch />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Upcoming Film")).toBeInTheDocument();
  expect(screen.getByText("Upcoming")).toBeInTheDocument();
  expect(screen.getByText("Waiting for theatrical release")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
});
