// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AIActivity, AIActivityMovie } from "./AIActivity";


const response = (payload) => Promise.resolve({ ok: true, status: 200, json: async () => payload });

describe("AI research activity admin UI", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn((url) => {
      const path = String(url);
      if (path.includes("/overview")) return response({
        current: { active_runs: [], current_movies: [] },
        today: { movies_researched: 10, successful: 7, needs_review: 1, conflicts: 0, no_reliable_result: 2, provider_failures: 0, database_updates: 0, sources_inspected: 24 },
        provider: { name: "omniroute", status: "CONFIGURED", model: "web-model", fallback_count: 0, errors: {} },
        discord_configured: false,
      });
      if (path.includes("/runs?")) return response({ items: [{ run_id: "run-1234567890", started_at: "2026-09-26T00:00:00Z", completed_at: "2026-09-26T00:01:00Z", status: "COMPLETED", trigger: "MANUAL", mode: "DRY_RUN", provider: "omniroute", model: "web-model", movies_total: 10, movies_processed: 10, successful: 7, needs_review: 1, provider_failures: 0, database_updates: 0, sources_inspected: 24, duration_seconds: 60 }], page: 1, pages: 1, total: 1 });
      return response({ items: [{ id: 41, run_id: "run-1234567890", movie_id: 198, tmdb_id: 1309121, imdb_id: "tt27541778", title: "Anomie", started_at: "2026-09-26T00:00:00Z", status: "COMPLETED", provider: "omniroute", model: "web-model", sources_inspected: 3, sources_discovered: 6, decision: "AUTO_ACCEPT", error_type: null, platform_confidence: 96, date_confidence: 91, dry_run: true, database_changed: false }], page: 1, pages: 1, total: 1 });
    }));
  });

  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("shows current status, today metrics, filters, and paginated history", async () => {
    render(<MemoryRouter><AIActivity /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "AI research activity" })).toBeInTheDocument();
    expect(await screen.findByText("Movies researched")).toBeInTheDocument();
    expect(screen.getByLabelText("TMDB ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Provider failure")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Anomie" })).toHaveAttribute("href", "/admin/ai-activity/movies/41");
    expect(screen.getByText("Discord: not configured")).toBeInTheDocument();
  });

  it("renders clickable sources, event timeline, and database values", async () => {
    fetch.mockImplementation(() => response({
      movie: { id: 41, run_id: "run-1234567890", movie_id: 198, tmdb_id: 1309121, imdb_id: "tt27541778", title: "Anomie", status: "COMPLETED", decision: "AUTO_ACCEPT", provider: "omniroute", model: "web-model", dry_run: true, database_changed: false, identity_confidence: 98, platform_confidence: 96, date_confidence: 91, queries: ["Anomie TMDB 1309121 OTT India"], before_value: { availability: [] }, proposed_value: { platform: "Prime Video" }, after_value: { availability: [] } },
      events: [{ id: 1, occurred_at: "2026-09-26T00:00:01Z", event_type: "SOURCE_ACCEPTED", message: "Source accepted", metadata: { tier: "trade_press" } }],
      sources: [{ id: 1, url: "https://example.test/anomie", domain: "example.test", page_title: "Anomie OTT release", source_tier: "trade_press", fetch_status: "FETCHED", identity_match: "MATCHED", platform_evidence: "Prime Video", date_evidence: "2026-07-24", region_evidence: "IN", watch_type_evidence: "SUBSCRIPTION", accepted: true, rejection_reason: null, evidence_excerpt: "Anomie streams on Prime Video." }],
    }));
    render(<MemoryRouter initialEntries={["/admin/ai-activity/movies/41"]}><Routes><Route path="/admin/ai-activity/movies/:activityId" element={<AIActivityMovie/>}/></Routes></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Anomie" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Anomie OTT release" })).toHaveAttribute("href", "https://example.test/anomie");
    expect(screen.getByText("Source accepted")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Database proposal" })).toBeInTheDocument();
  });
});
