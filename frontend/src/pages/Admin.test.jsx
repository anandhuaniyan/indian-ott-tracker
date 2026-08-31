// @vitest-environment jsdom
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { Comments, Dashboard, DataHealth, Jobs, Movies, OttResearch, RequestDetail, Requests, Sources } from "./Admin";
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
      }, {
        request_id: "REQ-LOCAL", verified_title: "Already Listed Movie", original_title: null,
        movie_external_id: 1602337, imdb_id: null, release_date: "2026-06-05", language: "ta", language_name: "Tamil",
        poster_path: null, director: null, email: "viewer@example.com", details: null,
        status: "PENDING", created_at: "2026-01-02T00:00:00Z", age_seconds: 120, target_seconds: 172680,
        local_movie_id: 6204, movie_existed_at_submission: true, emails: {},
      }],
    }),
  }));
  render(<MemoryRouter><Requests /></MemoryRouter>);
  expect(await screen.findByText("A Very Long Verified Movie Title")).toBeInTheDocument();
  expect(screen.getByText("Malayalam")).toBeInTheDocument();
  expect(screen.getAllByText(/remaining/)).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Retry confirmation" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add Movie" })).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Mark Added" })).toHaveLength(2);
  expect(screen.getByText("Local Movie ID")).toBeInTheDocument();
  expect(screen.getByText("6204")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "View Movie" })).toHaveAttribute("href", "/movies/6204");
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

it("renders and moderates pending comments from the admin route", async () => {
  const fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ items: [{ id: 7, movie_id: 1, movie_title: "Example Film", display_name: "Anand", email: "private@example.com", comment: "Great movie", status: "PENDING", created_at: "2026-08-29T00:00:00Z" }] }),
  });
  vi.stubGlobal("fetch", fetch);
  render(<MemoryRouter><Comments /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Comments" })).toBeInTheDocument();
  expect(await screen.findByText("Great movie")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Example Film" })).toHaveAttribute("href", "/movies/1");
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/v1/admin/comments/7"), expect.objectContaining({ method: "PATCH" })));
});

it("shows IMDb lifecycle coverage on Data Health", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      total: 0,
      items: [],
      imdb: {
        movies_total: 12281,
        imdb_id_available: 9999,
        imdb_id_missing: 2282,
        imdb_rating_available: 0,
        imdb_rating_pending: 9999,
        imdb_provider_configured: false,
      },
    }),
  }));
  render(<MemoryRouter><DataHealth /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "IMDb coverage" })).toBeInTheDocument();
  expect(screen.getAllByText("9999")).toHaveLength(2);
  expect(screen.getByText("imdb rating pending")).toBeInTheDocument();
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

it("renders complete request detail and queues existing workflows", async () => {
  const fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      request_id: "REQ-DETAIL", verified_title: "Detailed Film", original_title: "Original Film",
      movie_external_id: 101, imdb_id: "tt101", email: "viewer@example.test", status: "REVIEWING", sla: "ATTENTION",
      created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T01:00:00Z", age_seconds: 90000,
      language_name: "Malayalam", release_date: "2026-08-20", genres: ["Drama"], details: "Please review OTT data",
      local_movie_id: 1, local: { exists: true, id: 1, runtime: 130, directors: ["Director"], cast: ["Actor"], metadata_status: "COMPLETE" },
      ott: { status: "POSSIBLE", verification_status: "NEEDS_REVIEW", platform: "Netflix", confidence: 82, sources: [] },
      trailer: { available: true, video_key: "abcdefghijk", name: "Official Trailer" },
      data_completeness: { poster: true, ott_date: false },
      emails: { confirmation: { status: "SENT", sent_at: "2026-08-29T00:01:00Z" } },
    }),
  });
  vi.stubGlobal("fetch", fetch);
  render(<MemoryRouter initialEntries={["/admin/requests/REQ-DETAIL"]}><Routes><Route path="/admin/requests/:requestId" element={<RequestDetail />} /></Routes></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Detailed Film" })).toBeInTheDocument();
  expect(screen.getByText("viewer@example.test")).toHaveAttribute("href", "mailto:viewer@example.test");
  expect(screen.getByText(/OTT date/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Research OTT now" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/movies/1/research-ott"), expect.objectContaining({ method: "POST" })));
});

it("renders paginated admin movie operations", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ total: 1, page: 1, pages: 1, items: [{ id: 1, tmdb_id: 101, title: "Admin Film", poster_path: "/poster.jpg", year: 2026, language: "ml", metadata_health: "INCOMPLETE", metadata_missing: ["runtime"], image_health: "HEALTHY", trailer: false }] }) }));
  render(<MemoryRouter><Movies /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Admin Film" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Research OTT" })).toBeInTheDocument();
  expect(screen.getByText("Missing: runtime")).toBeInTheDocument();
});

it("shows isolated source health and unmatched adapter releases", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url) => Promise.resolve({ ok: true, json: async () => url.includes("/releases") ? { items: [{ id: 8, title: "Unmatched Film", platform: "Prime Video", status: "UNMATCHED", potential_matches: [] }] } : { items: [{ source: "ottplay", label: "OTTplay", enabled: false, configured: false, status: "DISABLED", stats: {} }], email: { smtp_configured: false, failed: 0, pending: 0 } } })));
  render(<MemoryRouter><Sources /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "OTTplay" })).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Unmatched Film" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Run sync" })).toBeDisabled();
});
