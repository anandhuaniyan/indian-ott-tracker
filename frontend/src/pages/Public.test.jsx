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
  language: "ml",
  language_name: "Malayalam",
};

beforeEach(() => {
  get.mockReset();
  post.mockReset();
});
afterEach(() => cleanup());

it("renders Popular once and omits the duplicate Trending section", async () => {
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
  const popularHeading = screen.getByRole("heading", { name: "Popular" });
  expect(popularHeading).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Trending" })).not.toBeInTheDocument();
  expect(
    popularHeading.closest("section").querySelector('a[href="/discover?sort=popularity"]'),
  ).toBeTruthy();
  expect(screen.getAllByText("Malayalam").length).toBeGreaterThanOrEqual(1);
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
  const detail = {
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
    trailer: {
      provider: "YouTube",
      video_key: "OfficialML1",
      name: "Official Trailer",
      embed_url: "https://www.youtube-nocookie.com/embed/OfficialML1",
    },
  };
  get.mockImplementation((path) => Promise.resolve(path.includes("/comments") ? { items: [], total: 0, page: 1, pages: 0 } : detail));
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
    /^OTT PlatformInformation not found$/,
  );
  expect(screen.getByTestId("ott-release")).toHaveTextContent(
    /^OTT ReleaseInformation not found$/,
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
  expect(screen.getByTitle("Official Trailer")).toHaveAttribute("src", "https://www.youtube-nocookie.com/embed/OfficialML1");
  expect(await screen.findByText("No comments yet. Be the first to comment.")).toBeInTheDocument();
});

it("shows a clean no-trailer state and treats submitted comment HTML as text", async () => {
  const detail = {
    movie: { ...card, display_id: 101, release_status: "Released", release_status_code: "THEATRICALLY_RELEASED", original_language: "ml", spoken_languages: [], production_countries: [], production_companies: [], ott: [], collection: null },
    cast: [], crew: [], crew_by_role: {}, images: [], releases: [], ratings: [], keywords: [], alternative_titles: [], external_ids: [], trailer: null,
  };
  get.mockImplementation((path) => Promise.resolve(path.includes("/comments") ? { items: [{ id: 1, display_name: "Anand", comment: "<script>alert(1)</script> Great movie", created_at: new Date().toISOString() }], total: 1, page: 1, pages: 1 } : detail));
  post.mockResolvedValue({ id: 2, status: "PENDING", message: "Your comment has been submitted for review." });
  render(<MemoryRouter initialEntries={["/movies/1"]}><Routes><Route path="/movies/:id" element={<Movie />} /></Routes></MemoryRouter>);
  expect(await screen.findByText("Trailer not available.")).toBeInTheDocument();
  expect(await screen.findByText("<script>alert(1)</script> Great movie")).toBeInTheDocument();
  expect(document.querySelector(".comment script")).toBeNull();
  fireEvent.change(screen.getByLabelText("Display Name *"), { target: { value: "Viewer" } });
  fireEvent.change(screen.getByLabelText("Comment *"), { target: { value: "Loved it" } });
  fireEvent.click(screen.getByRole("button", { name: "Submit comment" }));
  expect(await screen.findByText("Your comment has been submitted for review.")).toBeInTheDocument();
  expect(post).toHaveBeenCalledWith("/api/v1/movies/1/comments", { display_name: "Viewer", comment: "Loved it" });
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
  expect(screen.getByText(/including one that is already listed here/i)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /use deep search/i })).toHaveAttribute("href", "/search?mode=deep");
  fireEvent.change(screen.getByLabelText("Email *"), { target: { value: "viewer@example.com" } });
  fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/v1/movie-requests", expect.objectContaining({ movie_name: "Aadu", movie_external_id: 326282, release_year: 2015, language: "ml" })));
  expect(await screen.findByRole("heading", { name: "Request received" })).toBeInTheDocument();
  expect(screen.getByAltText("Aadu poster")).toBeInTheDocument();
  expect(screen.getByText(/aim to review your request within 48 hours/i)).toBeInTheDocument();
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

it("shows a neutral pending state when an IMDb lookup remains eligible", async () => {
  get.mockResolvedValue({
    items: [{ ...card, rating: null, rating_source: null, rating_status: "pending" }],
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
  expect(await screen.findByText("IMDb rating pending")).toBeInTheDocument();
});

it("renders date-grouped poster-first theatrical and confirmed OTT calendar cards", async () => {
  get.mockResolvedValue({
    period: "this-week",
    today: "2026-08-27",
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
  expect(await screen.findByRole("heading", { name: "27 August 2026" })).toBeInTheDocument();
  expect(screen.getByRole("main")).toHaveClass("calendar-page");
  expect(document.querySelector(".calendar-content")).toContainElement(
    document.querySelector(".calendar-grid"),
  );
  const movieLink = screen.getByRole("link", { name: /Example Film, theatrical release/ });
  expect(movieLink).toHaveAttribute("href", "/movies/1");
  expect(movieLink.querySelector("img")).toBeTruthy();
  expect(movieLink.querySelector(".calendar-poster").nextElementSibling).toHaveClass(
    "calendar-movie-info",
  );
  expect(screen.getAllByText("Malayalam").length).toBeGreaterThanOrEqual(1);
  const ottTab = screen.getByRole("link", { name: "OTT Releases" });
  expect(ottTab).toHaveAttribute("href", "/calendar/this-week?tab=ott");
  fireEvent.click(ottTab);
  expect(await screen.findByText("Netflix")).toBeInTheDocument();
  expect(screen.getByText("OTT")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "next week" })).toHaveAttribute(
    "href",
    "/calendar/next-week?tab=ott",
  );
});

it("filters calendar releases by language and provides month navigation", async () => {
  get.mockResolvedValue({
    period: "this-month",
    today: "2026-08-29",
    start_date: "2026-08-01",
    end_date: "2026-08-31",
    theatrical: {
      items: [
        { ...card, title: "Malayalam Film", theatrical_release_date: "2026-08-27" },
        { ...card, id: 2, title: "Tamil Film", language: "ta", language_name: "Tamil", theatrical_release_date: "2026-08-28" },
      ],
      total: 2,
    },
    ott: { items: [], total: 0 },
  });
  render(
    <MemoryRouter initialEntries={["/calendar/this-month"]}>
      <Routes><Route path="/calendar/:period" element={<Calendar />} /></Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByRole("link", { name: "Previous month, July 2026" })).toHaveAttribute(
    "href", "/calendar/this-month?month=2026-07",
  );
  fireEvent.change(screen.getByLabelText("Language"), { target: { value: "ta" } });
  expect(screen.queryByText("Malayalam Film")).not.toBeInTheDocument();
  expect(screen.getByText("Tamil Film")).toBeInTheDocument();
});

it("takes Today to the actual date group and focuses it when a release exists", async () => {
  get.mockResolvedValue({
    period: "this-month",
    today: "2026-08-29",
    start_date: "2026-08-01",
    end_date: "2026-08-31",
    theatrical: {
      items: [{ ...card, theatrical_release_date: "2026-08-29" }],
      total: 1,
    },
    ott: { items: [], total: 0 },
  });
  render(
    <MemoryRouter initialEntries={["/calendar/this-month?tab=theatrical"]}>
      <Routes><Route path="/calendar/:period" element={<Calendar />} /></Routes>
    </MemoryRouter>,
  );
  const todayLink = await screen.findByRole("link", { name: "Today, 29 August 2026" });
  expect(todayLink).toHaveAttribute(
    "href",
    "/calendar/this-month?tab=theatrical&focus=today",
  );
  fireEvent.click(todayLink);
  const todayGroup = document.getElementById("release-date-2026-08-29");
  await waitFor(() => expect(todayGroup).toHaveClass("today"));
  await waitFor(() => expect(document.activeElement).toBe(todayGroup));
  expect(todayGroup).toHaveTextContent("Today");
  expect(todayGroup).toHaveTextContent("Example Film");
});

it.each(["2026-07", "2026-09"])(
  "returns from %s to today's date without losing the OTT tab or language filter",
  async (viewedMonth) => {
    const current = {
      period: "this-month",
      today: "2026-08-29",
      start_date: "2026-08-01",
      end_date: "2026-08-31",
      theatrical: { items: [], total: 0 },
      ott: { items: [], total: 0 },
    };
    const viewed = {
      ...current,
      start_date: `${viewedMonth}-01`,
      end_date: `${viewedMonth}-${viewedMonth.endsWith("-07") ? "31" : "30"}`,
    };
    get.mockImplementation((path) =>
      Promise.resolve(path.includes("?month=") ? viewed : current),
    );
    render(
      <MemoryRouter initialEntries={[`/calendar/this-month?tab=ott&language=ml&month=${viewedMonth}`]}>
        <Routes><Route path="/calendar/:period" element={<Calendar />} /></Routes>
      </MemoryRouter>,
    );
    const todayLink = await screen.findByRole("link", { name: "Today, 29 August 2026" });
    expect(todayLink).toHaveAttribute(
      "href",
      "/calendar/this-month?tab=ott&language=ml&focus=today",
    );
    fireEvent.click(todayLink);
    expect(await screen.findByText("No releases today.")).toBeInTheDocument();
    const todayGroup = document.getElementById("release-date-2026-08-29");
    await waitFor(() => expect(document.activeElement).toBe(todayGroup));
    expect(todayGroup).toHaveTextContent("No confirmed OTT releases match the selected filters.");
    expect(get).toHaveBeenCalledWith("/api/v1/calendar/this-month");
  },
);

it("shows a neutral retryable calendar error without leaking provider details", async () => {
  get.mockRejectedValue(new Error("secret provider URL http://internal:8000"));
  render(
    <MemoryRouter initialEntries={["/calendar/this-month"]}>
      <Routes><Route path="/calendar/:period" element={<Calendar />} /></Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Release information could not be loaded",
  );
  expect(screen.queryByText(/internal:8000/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
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
