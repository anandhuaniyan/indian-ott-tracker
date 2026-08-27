// @vitest-environment jsdom
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Consent from "../components/Consent";
import { Browse, Home, Movie, Ott, OttPlatform, Person } from "./Public";

const get = vi.fn();
vi.mock("../services/api", () => ({
  get: (...args) => get(...args), post: vi.fn(), imageUrl: path => path || "/placeholder.svg",
}));

const card = { id: 1, title: "Example Film", release_date: "2026-08-27", poster_path: "/poster.jpg", rating: 8.2 };

beforeEach(() => { get.mockReset(); });

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
  get.mockResolvedValue({ movie: { ...card, tmdb_id: 101, original_title: "Original", overview: "Stored overview", backdrop_path: "/backdrop.jpg", runtime_minutes: 120, status: "Released", certification: "U/A", spoken_languages: [], production_countries: [], production_companies: [], ott: [], collection: null }, cast: [{ person_id: 2, name: "Actor", character: "Hero", order: 0 }], crew: [], crew_by_role: {}, images: [{ type: "poster", url: "/alt.jpg" }], releases: [], ratings: [{ source: "TMDB", rating: 8.2, votes: 100 }], keywords: ["friendship"], alternative_titles: [], external_ids: [{ provider: "tmdb", id: "101" }] });
  render(<MemoryRouter initialEntries={["/movies/1"]}><Routes><Route path="/movies/:id" element={<Movie/>}/></Routes></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Example Film" })).toBeInTheDocument();
  expect(screen.getByText("Poster gallery")).toBeInTheDocument();
  expect(screen.getByText("Actor")).toBeInTheDocument();
});

it("supports person credit controls", async () => {
  get.mockResolvedValue({ id: 2, name: "Actor", department: "Acting", profile_path: null, roles: ["actor"], filmography: [{ movie: card, character: "Hero", credit_type: "cast" }] });
  render(<MemoryRouter initialEntries={["/people/2"]}><Routes><Route path="/people/:id" element={<Person/>}/></Routes></MemoryRouter>);
  expect(await screen.findByText("Filmography")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Credits"), { target: { value: "cast" } });
  await waitFor(() => expect(get.mock.calls.at(-1)[0]).toContain("credit_type=cast"));
});

it("persists granular consent", () => {
  render(<Consent/>);
  fireEvent.click(screen.getByLabelText("Analytics"));
  fireEvent.click(screen.getByText("Save choices"));
  expect(JSON.parse(localStorage.getItem("ott-consent"))).toMatchObject({ necessary: true, analytics: true, advertising: false });
});
