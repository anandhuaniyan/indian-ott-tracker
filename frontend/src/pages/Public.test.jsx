// @vitest-environment jsdom
import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Consent from "../components/Consent";
import {
  Browse,
  Calendar,
  Home,
  Movie,
  Ott,
  OttPlatform,
  Person,
  Request,
} from "./Public";

const get = vi.fn();
const post = vi.fn();
vi.mock("../services/api", () => ({
  get: (...args) => get(...args),
  post: (...args) => post(...args),
  imageUrl: (path) => path || "/placeholder.svg",
}));

const card = {
  id: 1,
  title: "Example Film",
  release_date: "2026-08-27",
  poster_path: "/poster.jpg",
  rating: 8.2,
  rating_source: "IMDb",
};

beforeEach(() => {
  get.mockReset();
  post.mockReset();
});
afterEach(() => cleanup());

it("renders every backend-driven home section", async () => {
  get.mockResolvedValue({
    trending: [card],
    popular: [card],
    latest_theatrical: [card],
    upcoming_theatrical: [card],
    recently_added: [card],
    upcoming_ott: [card],
    recent_ott: [card],
    language_sections: { ml: { name: "Malayalam", items: [card] } },
    genres: [{ slug: "drama", name: "Drama" }],
    platforms: [{ slug: "netflix", name: "Netflix", movie_count: 1 }],
  });
  render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  );
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
    get.mockResolvedValue(
      path === "/ott"
        ? { platforms: [], upcoming: [], recent: [], confirmed: [] }
        : path.startsWith("/ott/")
          ? {
              platform: "Netflix",
              upcoming: [],
              recent: [],
              available: [],
              page: 1,
              pages: 0,
            }
          : { items: [card], total: 1, page: 1, pages: 1 },
    );
    render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={route} element={<Component />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(
      await screen.findByRole("heading", { name: new RegExp(heading, "i") }),
    ).toBeInTheDocument();
  });
});

it("renders stored movie metadata and galleries without fabricating empty fields", async () => {
  get.mockResolvedValue({
    movie: {
      ...card,
      display_id: 101,
      release_status: "Released",
      release_status_code: "THEATRICALLY_RELEASED",
      theatrical_release_date: "2026-08-14",
      ott_platform: null,
      ott_release_date: null,
      ott_research_status: "Researching",
      original_title: "Original",
      original_language: "ml",
      original_language_name: "Malayalam",
      overview: "Stored overview",
      backdrop_path: "/backdrop.jpg",
      runtime_minutes: 120,
      status: "Released",
      certification: "U/A",
      spoken_languages: [],
      production_countries: [],
      production_companies: [],
      ott: [],
      collection: null,
    },
    cast: [
      {
        person_id: 2,
        name: "Actor",
        profile_path: "/actor.jpg",
        character: "Hero",
        order: 0,
      },
    ],
    crew: [],
    crew_by_role: {},
    images: [{ type: "poster", url: "/alt.jpg" }],
    releases: [],
    ratings: [
      { source: "IMDb", rating: 8.2, votes: 100 },
      { source: "Source Rating", rating: 7.4, votes: 90 },
    ],
    keywords: ["friendship"],
    alternative_titles: [],
    external_ids: [
      {
        provider: "IMDb",
        id: "tt1234567",
        url: "https://www.imdb.com/title/tt1234567/",
      },
    ],
  });
  render(
    <MemoryRouter initialEntries={["/movies/1"]}>
      <Routes>
        <Route path="/movies/:id" element={<Movie />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(
    await screen.findByRole("heading", { name: "Example Film" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Poster gallery")).toBeInTheDocument();
  expect(screen.getByText("Actor")).toBeInTheDocument();
  expect(screen.getByText(/IMDb ★ 8.2/)).toBeInTheDocument();
  expect(screen.getByTestId("release-status")).toHaveTextContent(
    /^Release StatusReleased$/,
  );
  expect(screen.getByTestId("theatrical-release")).toHaveTextContent(
    /Theatrical Release.*14 August 2026/,
  );
  expect(screen.getByTestId("ott-platform")).toHaveTextContent(
    /^OTT PlatformNot announced$/,
  );
  expect(screen.getByTestId("ott-release")).toHaveTextContent(
    /^OTT ReleaseNot announced$/,
  );
  expect(screen.getByTestId("ott-research-status")).toHaveTextContent(
    /^OTT ResearchResearching$/,
  );
  expect(screen.getByTestId("movie-display-id")).toHaveTextContent(/^ID101$/);
  expect(screen.getByText("Malayalam")).toHaveAttribute("href", "/languages/ml");
  expect(screen.queryByText("TMDB ID")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "tt1234567" })).toHaveAttribute(
    "href",
    "https://www.imdb.com/title/tt1234567/",
  );
  expect(screen.getByAltText("Actor profile")).toBeInTheDocument();
});

it("requires the external movie ID and preserves Deep Search prefills", async () => {
  get.mockResolvedValue([{ code: "ml", name: "Malayalam" }]);
  post.mockResolvedValue({ request_id: "REQ-1", status: "PENDING", verified_title: "Aadu", original_title: "ആട്", poster_path: "/aadu.jpg", confirmation_email_status: "SENT" });
  render(
    <MemoryRouter initialEntries={["/request-movie?movie_name=Aadu&movie_external_id=326282&release_year=2015&language=ml"]}>
      <Routes><Route path="/request-movie" element={<Request/>}/></Routes>
    </MemoryRouter>,
  );
  expect(screen.getByLabelText("Movie Name *")).toHaveValue("Aadu");
  expect(screen.getByLabelText("ID *")).toHaveValue(326282);
  expect(screen.getByLabelText("Year")).toHaveValue(2015);
  expect(screen.getByLabelText("Language")).toHaveValue("ml");
  expect(screen.getByRole("link", { name: /use deep search/i })).toHaveAttribute("href", "/search?mode=deep");
  fireEvent.change(screen.getByLabelText("Email *"), { target: { value: "viewer@example.com" } });
  fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/v1/movie-requests", expect.objectContaining({ movie_name: "Aadu", movie_external_id: 326282, release_year: 2015, language: "ml" })));
  expect(await screen.findByRole("heading", { name: "Request received" })).toBeInTheDocument();
  expect(screen.getByAltText("Aadu poster")).toBeInTheDocument();
  expect(screen.getByText("Confirmation email sent.")).toBeInTheDocument();
});

it("keeps a successful request when confirmation email delivery fails", async () => {
  get.mockResolvedValue([]);
  post.mockResolvedValue({ request_id: "REQ-2", status: "PENDING", verified_title: "Verified Film", poster_path: "/verified.jpg", confirmation_email_status: "FAILED" });
  render(<MemoryRouter><Request /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText("Movie Name *"), { target: { value: "Typo Film" } });
  fireEvent.change(screen.getByLabelText("Email *"), { target: { value: "viewer@example.com" } });
  fireEvent.change(screen.getByLabelText("ID *"), { target: { value: "123" } });
  fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
  expect(await screen.findByRole("heading", { name: "Verified Film" })).toBeInTheDocument();
  expect(screen.getByText(/request was received, but we could not send/i)).toBeInTheDocument();
});

it("supports person credit controls and separates normalized roles", async () => {
  get.mockResolvedValue({
    id: 2,
    display_id: 10,
    name: "Actor",
    department: "Acting",
    profile_path: null,
    roles: ["actor", "director"],
    filmography: [
      {
        movie: card,
        character: "Hero",
        credit_type: "cast",
        normalized_role: "actor",
      },
      {
        movie: { ...card, id: 2, title: "Directed Film" },
        job: "Director",
        credit_type: "crew",
        normalized_role: "director",
      },
    ],
  });
  render(
    <MemoryRouter initialEntries={["/people/2"]}>
      <Routes>
        <Route path="/people/:id" element={<Person />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByText("Filmography")).toBeInTheDocument();
  expect(screen.getByTestId("person-display-id")).toHaveTextContent(/^ID10$/);
  expect(screen.queryByText("TMDB ID")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Acting" })).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Directing" }),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Credits"), {
    target: { value: "cast" },
  });
  await waitFor(() =>
    expect(get.mock.calls.at(-1)[0]).toContain("credit_type=cast"),
  );
});

it("shows unavailable instead of fabricating a missing IMDb rating", async () => {
  get.mockResolvedValue({
    items: [{ ...card, rating: null, rating_source: null }],
    total: 1,
    page: 1,
    pages: 1,
  });
  render(
    <MemoryRouter initialEntries={["/genres/drama"]}>
      <Routes>
        <Route path="/genres/:slug" element={<Browse />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(
    await screen.findByText("IMDb rating unavailable"),
  ).toBeInTheDocument();
});

it("renders theatrical and confirmed OTT calendar tabs while preserving period", async () => {
  get.mockResolvedValue({
    period: "this-week",
    start_date: "2026-08-24",
    end_date: "2026-08-30",
    theatrical: {
      items: [
        {
          ...card,
          theatrical_release_date: "2026-08-27",
          certification: "U/A",
        },
      ],
      total: 1,
    },
    ott: {
      items: [
        {
          ...card,
          ott_release_date: "2026-08-28",
          ott_platform: "Netflix",
          ott_platform_slug: "netflix",
          ott_platform_logo: "/netflix.png",
          verification_state: "confirmed",
        },
      ],
      total: 1,
    },
  });
  render(
    <MemoryRouter initialEntries={["/calendar/this-week?tab=theatrical"]}>
      <Routes>
        <Route path="/calendar/:period" element={<Calendar />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByText("Certification: U/A")).toBeInTheDocument();
  const ottTab = screen.getByRole("link", { name: "OTT Releases" });
  expect(ottTab).toHaveAttribute("href", "/calendar/this-week?tab=ott");
  fireEvent.click(ottTab);
  expect(await screen.findByRole("link", { name: "Netflix" })).toHaveAttribute(
    "href",
    "/ott/netflix",
  );
  expect(screen.getByRole("link", { name: "next week" })).toHaveAttribute(
    "href",
    "/calendar/next-week?tab=ott",
  );
});

it("persists granular consent", () => {
  render(<Consent />);
  fireEvent.click(screen.getByLabelText("Analytics"));
  fireEvent.click(screen.getByText("Save choices"));
  expect(JSON.parse(localStorage.getItem("ott-consent"))).toMatchObject({
    necessary: true,
    analytics: true,
    advertising: false,
  });
});
