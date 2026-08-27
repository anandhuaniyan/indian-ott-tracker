// @vitest-environment jsdom
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Consent from "../components/Consent";
import { Browse, Calendar, Home, Movie, Ott, OttPlatform, Person } from "./Public";

const get = vi.fn();
vi.mock("../services/api", () => ({
  get: (...args) => get(...args), post: vi.fn(), imageUrl: path => path || "/placeholder.svg",
}));

const card = { id: 1, title: "Example Film", release_date: "2026-08-27", poster_path: "/poster.jpg", rating: 8.2, rating_source: "IMDb" };

beforeEach(() => { get.mockReset(); });
afterEach(() => cleanup());

it("renders every backend-driven home section", async () => {
  get.mockResolvedValue({ trending: [card], popular: [card], latest_theatrical: [card], upcoming_theatrical: [card], recently_added: [card], upcoming_ott: [card], recent_ott: [card], language_sections: { ml: { name: "Malayalam", items: [card] } }, genres: [{ slug: "drama", name: "Drama" }], platforms: [{ slug: "netflix", name: "Netflix", movie_count: 1 }] });
  render(<MemoryRouter><Home/></MemoryRouter>);
  expect(await screen.findByText("Upcoming OTT")).toBeInTheDocument();
  expect(screen.getByText("Malayalam")).toBeInTheDocument();
  expect(screen.getByText("Netflix")).toBeInTheDocument();
});

describe("browse routes", () => {
  it.each([
    ["/genres/drama", "/genres/:slug", Browse, "drama movies"],
    ["/languages/ml", "/languages/:code", Browse, "Malayalam movies"],
    ["/ott", "/ott", Ott, "OTT releases"],
    ["/ott/netflix", "/ott/:platform", OttPlatform, "Netflix"],
  ])("renders %s", async (path, route, Component, heading) => {
    get.mockResolvedValue(path === "/ott" ? { platforms: [], upcoming: [], recent: [], confirmed: [] } : path.startsWith("/ott/") ? { platform: "Netflix", upcoming: [], recent: [], available: [], page: 1, pages: 0 } : { items: [card], total: 1, page: 1, pages: 1 });
    render(<MemoryRouter initialEntries={[path]}><Routes><Route path={route} element={<Component/>}/></Routes></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: new RegExp(heading, "i") })).toBeInTheDocument();
  });
});

it("renders stored movie metadata and galleries without fabricating empty fields", async () => {
  get.mockResolvedValue({ movie: { ...card, tmdb_id: 101, original_title: "Original", overview: "Stored overview", backdrop_path: "/backdrop.jpg", runtime_minutes: 120, status: "Released", certification: "U/A", spoken_languages: [], production_countries: [], production_companies: [], ott: [], collection: null }, cast: [{ person_id: 2, name: "Actor", profile_path: "/actor.jpg", character: "Hero", order: 0 }], crew: [], crew_by_role: {}, images: [{ type: "poster", url: "/alt.jpg" }], releases: [], ratings: [{ source: "IMDb", rating: 8.2, votes: 100 }, { source: "TMDB", rating: 7.4, votes: 90 }], keywords: ["friendship"], alternative_titles: [], external_ids: [{ provider: "tmdb", id: "101" }, { provider: "IMDb", id: "tt1234567", url: "https://www.imdb.com/title/tt1234567/" }] });
  render(<MemoryRouter initialEntries={["/movies/1"]}><Routes><Route path="/movies/:id" element={<Movie/>}/></Routes></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Example Film" })).toBeInTheDocument();
  expect(screen.getByText("Poster gallery")).toBeInTheDocument();
  expect(screen.getByText("Actor")).toBeInTheDocument();
  expect(screen.getByText(/IMDb ★ 8.2/)).toBeInTheDocument();
  expect(screen.queryByText("101")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "tt1234567" })).toHaveAttribute("href", "https://www.imdb.com/title/tt1234567/");
  expect(screen.getByAltText("Actor profile")).toBeInTheDocument();
});

it("supports person credit controls and separates normalized roles", async () => {
  get.mockResolvedValue({ id: 2, name: "Actor", department: "Acting", profile_path: null, roles: ["actor", "director"], filmography: [{ movie: card, character: "Hero", credit_type: "cast", normalized_role: "actor" }, { movie: { ...card, id: 2, title: "Directed Film" }, job: "Director", credit_type: "crew", normalized_role: "director" }] });
  render(<MemoryRouter initialEntries={["/people/2"]}><Routes><Route path="/people/:id" element={<Person/>}/></Routes></MemoryRouter>);
  expect(await screen.findByText("Filmography")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Acting" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Directing" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Credits"), { target: { value: "cast" } });
  await waitFor(() => expect(get.mock.calls.at(-1)[0]).toContain("credit_type=cast"));
});

it("shows unavailable instead of fabricating a missing IMDb rating", async () => {
  get.mockResolvedValue({ items: [{ ...card, rating: null, rating_source: null }], total: 1, page: 1, pages: 1 });
  render(<MemoryRouter initialEntries={["/genres/drama"]}><Routes><Route path="/genres/:slug" element={<Browse/>}/></Routes></MemoryRouter>);
  expect(await screen.findByText("IMDb rating unavailable")).toBeInTheDocument();
});

it("renders theatrical and confirmed OTT calendar tabs while preserving period", async () => {
  get.mockResolvedValue({ period: "this-week", start_date: "2026-08-24", end_date: "2026-08-30", theatrical: { items: [{ ...card, theatrical_release_date: "2026-08-27", certification: "U/A" }], total: 1 }, ott: { items: [{ ...card, ott_release_date: "2026-08-28", ott_platform: "Netflix", ott_platform_slug: "netflix", ott_platform_logo: "/netflix.png", verification_state: "confirmed" }], total: 1 } });
  render(<MemoryRouter initialEntries={["/calendar/this-week?tab=theatrical"]}><Routes><Route path="/calendar/:period" element={<Calendar/>}/></Routes></MemoryRouter>);
  expect(await screen.findByText("Certification: U/A")).toBeInTheDocument();
  const ottTab = screen.getByRole("link", { name: "OTT Releases" });
  expect(ottTab).toHaveAttribute("href", "/calendar/this-week?tab=ott");
  fireEvent.click(ottTab);
  expect(await screen.findByRole("link", { name: "Netflix" })).toHaveAttribute("href", "/ott/netflix");
  expect(screen.getByRole("link", { name: "next week" })).toHaveAttribute("href", "/calendar/next-week?tab=ott");
});

it("persists granular consent", () => {
  render(<Consent/>);
  fireEvent.click(screen.getByLabelText("Analytics"));
  fireEvent.click(screen.getByText("Save choices"));
  expect(JSON.parse(localStorage.getItem("ott-consent"))).toMatchObject({ necessary: true, analytics: true, advertising: false });
});
