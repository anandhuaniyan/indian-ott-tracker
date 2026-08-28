// @vitest-environment jsdom
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DeepMovie, DeepPerson, DeepSearch } from "./DeepSearch";

const get = vi.fn();
const adminGet = vi.fn();
const adminPost = vi.fn();
vi.mock("../services/api", () => ({
  get: (...args) => get(...args),
  adminGet: (...args) => adminGet(...args),
  adminPost: (...args) => adminPost(...args),
  imageUrl: (path) => path || "/placeholder.svg",
}));

beforeEach(() => {
  get.mockReset(); adminGet.mockReset(); adminPost.mockReset();
  adminGet.mockRejectedValue(new Error("Unauthenticated"));
});
afterEach(() => cleanup());

it("searches live movies with year/language and shows the external ID neutrally", async () => {
  get.mockImplementation((path) => Promise.resolve(path === "/api/v1/languages" ? [{ code: "ml", name: "Malayalam" }] : { page: 1, total_pages: 1, total_results: 1, results: [{ id: 1469458, title: "Aadu", original_title: "ആട്", release_date: "2026-01-01", original_language: "ml", original_language_name: "Malayalam", poster_path: "/aadu.jpg", overview: "Overview", in_library: false }] }));
  render(<MemoryRouter><DeepSearch/></MemoryRouter>);
  fireEvent.change(screen.getByLabelText("Movie name"), { target: { value: "Aadu" } });
  fireEvent.change(screen.getByLabelText("Year"), { target: { value: "2026" } });
  fireEvent.change(screen.getByLabelText("Language"), { target: { value: "ml" } });
  fireEvent.click(screen.getByRole("button", { name: "Search live source" }));
  expect(await screen.findByRole("heading", { name: "Aadu" })).toBeInTheDocument();
  expect(screen.getByText("1469458")).toBeInTheDocument();
  expect(screen.queryByText("TMDB ID")).not.toBeInTheDocument();
  expect(get.mock.calls.some(([path]) => path.includes("year=2026") && path.includes("language=ml"))).toBe(true);
  expect(screen.getAllByText("Malayalam")).toHaveLength(2);
  expect(screen.getByText("Not in local database")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Request Movie" })).toHaveAttribute("href", "/request-movie?movie_name=Aadu&movie_external_id=1469458&release_year=2026&language=ml");
  expect(document.body).not.toHaveTextContent(/TMDB/i);
});

it("renders live movie detail, images, credits, releases, providers, recommendations and similar separately", async () => {
  get.mockResolvedValue({
    movie: { id: 1469458, title: "Aadu", original_language: "ml", original_language_name: "Malayalam", poster_path: "/poster.jpg", overview: "Overview", vote_average: 7.9, vote_count: 100, in_library: true, local_movie_id: 91, genres: [], spoken_languages: [], production_countries: [], production_companies: [] },
    cast: [{ id: 1, name: "Actor", profile_path: "/actor.jpg", character: "Hero" }],
    crew: { Directing: [{ id: 2, name: "Director", job: "Director" }] },
    images: { posters: [{ file_path: "/alt.jpg" }], backdrops: [{ file_path: "/backdrop.jpg" }], logos: [{ file_path: "/logo.png" }] },
    releases: [{ country: "IN", releases: [{ type: "Theatrical", date: "2026-01-01", certification: "U/A" }] }],
    external_ids: { imdb_id: "tt1234567" }, keywords: [{ id: 1, name: "friendship" }], alternative_titles: [],
    watch_providers: { country: "IN", items: [{ id: 8, name: "Provider", type: "flatrate", logo_path: "/provider.jpg" }] },
    recommendations: [{ id: 20, title: "Recommended" }], similar: [{ id: 21, title: "Similar" }],
  });
  render(<MemoryRouter initialEntries={["/deep-search/movie/1469458"]}><Routes><Route path="/deep-search/movie/:tmdbId" element={<DeepMovie/>}/></Routes></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Aadu" })).toBeInTheDocument();
  expect(screen.getByText("Source Rating")).toBeInTheDocument();
  expect(screen.queryByText(/^IMDb ★/)).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Cast" })).toBeInTheDocument();
  expect(screen.getByText("India (IN)")).toBeInTheDocument();
  expect(screen.getByText(/2026-01-01 · U\/A/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Posters" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Recommended Movies" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Similar Movies" })).toBeInTheDocument();
  expect(screen.getByText(/does not establish an OTT release date/i)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open Local Movie" })).toHaveAttribute("href", "/movies/91");
  expect(screen.getByRole("link", { name: "Malayalam" })).toHaveAttribute("href", "/languages/ml");
  expect(document.body).not.toHaveTextContent(/TMDB/i);
});

it("renders live person details and grouped movie credits", async () => {
  get.mockResolvedValue({
    person: { id: 42, name: "Prithviraj Sukumaran", profile_path: "/profile.jpg", known_for_department: "Acting", biography: "Biography", in_library: false },
    profiles: [], external_ids: {},
    credits: { Acting: [{ id: 10, title: "Acted Film", release_date: "2025-01-01", character: "Hero" }], Directing: [{ id: 11, title: "Directed Film", release_date: "2024-01-01", job: "Director" }] },
  });
  render(<MemoryRouter initialEntries={["/deep-search/person/42"]}><Routes><Route path="/deep-search/person/:tmdbPersonId" element={<DeepPerson/>}/></Routes></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Prithviraj Sukumaran" })).toBeInTheDocument();
  expect(screen.getByText("42")).toBeInTheDocument();
  expect(screen.queryByText("TMDB ID")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Acting" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Directing" })).toBeInTheDocument();
  expect(screen.getByText("as Hero")).toBeInTheDocument();
});
