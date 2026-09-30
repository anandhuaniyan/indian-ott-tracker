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
  Discover,
  Home,
  Movie,
  Ott,
  OttPlatform,
  Person,
  Request,
} from "./Public";

const get = vi.fn();
const post = vi.fn();
const userGet = vi.fn();
const authenticatedPut = vi.fn();
vi.mock("../services/api", () => ({
  get: (...args) => get(...args),
  post: (...args) => post(...args),
  userGet: (...args) => userGet(...args),
  authenticatedPut: (...args) => authenticatedPut(...args),
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
const languageOptions = [
  { code: "ml", name: "Malayalam", region: "indian", group: "primary" },
  { code: "ta", name: "Tamil", region: "indian", group: "primary" },
  { code: "te", name: "Telugu", region: "indian", group: "primary" },
  { code: "hi", name: "Hindi", region: "indian", group: "primary" },
  { code: "kn", name: "Kannada", region: "indian", group: "primary" },
  { code: "en", name: "English", region: "international", group: "primary" },
  { code: "ko", name: "Korean", region: "international", group: "primary" },
  { code: "ja", name: "Japanese", region: "international", group: "primary" },
  { code: "zh", name: "Chinese", region: "international", group: "primary" },
  { code: "es", name: "Spanish", region: "international", group: "primary" },
  { code: "fr", name: "French", region: "international", group: "primary" },
];

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  userGet.mockReset();
  authenticatedPut.mockReset();
  userGet.mockRejectedValue(new Error("Unauthenticated"));
});
afterEach(() => cleanup());

it("renders Popular once and omits the duplicate Trending section", async () => {
  get.mockResolvedValue({
    trending: [card],
    popular: [card],
    latest_theatrical: [card],
    upcoming_theatrical: [card],
    recently_added: [card],
    highest_rated: [card],
    upcoming_ott: [card],
    recent_ott: [card],
    language_sections: { ml: { name: "Malayalam", items: [card] } },
    languages: languageOptions,
    language_groups: {
      indian: { primary: [{ code: "ml", name: "Malayalam" }], more: [] },
      international: { primary: [{ code: "en", name: "English" }, { code: "ko", name: "Korean" }, { code: "ja", name: "Japanese" }, { code: "zh", name: "Chinese" }, { code: "es", name: "Spanish" }, { code: "fr", name: "French" }], more: [{ code: "th", name: "Thai" }] },
    },
    genres: [{ slug: "drama", name: "Drama" }],
    platforms: [{ slug: "netflix", name: "Netflix", movie_count: 1 }],
  });
  render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  );
  expect(screen.getByText("Movies from India and around the world, all in one place")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Movies & OTT Releases Worldwide" })).toBeInTheDocument();
  expect(screen.getByText(/streaming platforms worldwide, with release dates, ratings, trailers/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Explore movies" })).toHaveAttribute("href", "/discover");
  expect(screen.queryByText("Indian cinema and international movies, all in one place")).not.toBeInTheDocument();
  expect(await screen.findByText("Upcoming OTT")).toBeInTheDocument();
  const popularHeading = screen.getByRole("heading", { name: "Popular" });
  expect(popularHeading).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Trending" })).not.toBeInTheDocument();
  expect(
    popularHeading.closest("section").querySelector('a[href*="view_mode=section_listing"]'),
  ).toBeTruthy();
  expect(screen.getAllByRole("link", { name: "View more" }).length).toBeGreaterThanOrEqual(7);
  expect(screen.getAllByText("Malayalam").length).toBeGreaterThanOrEqual(1);
  expect(screen.queryByRole("link", { name: "Korean" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "International" }));
  expect(screen.getByRole("link", { name: "Korean" })).toHaveAttribute("href", "/languages/ko");
  expect(screen.getByRole("link", { name: "French" })).toHaveAttribute("href", "/languages/fr");
  expect(screen.getByText("More International")).toBeInTheDocument();
  expect(screen.getByText("Netflix")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Japanese" }));
  await waitFor(() => expect(get).toHaveBeenCalledWith("/api/v1/home?language=ja"));
  expect(screen.getAllByRole("link", { name: "View more" })[0]).toHaveAttribute(
    "href", expect.stringContaining("language=ja"),
  );
});

describe("browse routes", () => {
  it.each([
    ["/genres/drama", "/genres/:slug", Browse, "drama movies"],
    ["/languages/ml", "/languages/:code", Browse, "Malayalam movies"],
    ["/languages/en", "/languages/:code", Browse, "English movies"],
    ["/languages/ko", "/languages/:code", Browse, "Korean movies"],
    ["/languages/ja", "/languages/:code", Browse, "Japanese movies"],
    ["/languages/zh", "/languages/:code", Browse, "Chinese movies"],
    ["/languages/es", "/languages/:code", Browse, "Spanish movies"],
    ["/languages/fr", "/languages/:code", Browse, "French movies"],
    ["/languages/th", "/languages/:code", Browse, "Thai movies"],
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
          : {
              items: [card],
              total: 1,
              page: 1,
              pages: 1,
              filters: {
                languages: path.startsWith("/languages/")
                  ? [{ code: path.split("/").pop(), name: heading.replace(/ movies$/i, "") }]
                  : [],
              },
            },
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
      ott: [{
        provider: "Prime Video", watch_type: "subscription", release_date: null,
        country: "IN", availability_state: "available", platform_confidence: 75,
        last_verified: "2026-08-31T12:34:56+00:00",
      }],
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
    images: [
      { type: "poster", url: "/alt.jpg" },
      { type: "logo", url: "/broken-logo.png" },
    ],
    releases: [],
    release_timeline: [{ date: "2026-08-14", label: "Primary theatrical release" }],
    international_releases: [{ country: "US", date: "2026-08-21", type: "3", label: "Theatrical release" }],
    ratings: [
      { source: "IMDb", rating: 8.2, votes: 100 },
      { source: "Source Rating", rating: 7.4, votes: 90 },
    ],
    community_rating: { average: 8.5, count: 2 },
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
    videos: [
      {
        provider: "YouTube",
        video_key: "OfficialML1",
        name: "Official Trailer",
        video_type: "Trailer",
        official: true,
        language: "ml",
        embed_url: "https://www.youtube-nocookie.com/embed/OfficialML1",
      },
    ],
  };
  get.mockImplementation((path) => Promise.resolve(path.includes("/public-comments") ? { items: [], total: 0, page: 1, pages: 0 } : detail));
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
  expect(screen.getByText("Primary theatrical release")).toBeInTheDocument();
  expect(screen.getByText("View international releases (1)")).toBeInTheDocument();
  const logo = screen.getByAltText("Example Film logo");
  fireEvent.error(logo);
  expect(logo).not.toBeVisible();
  const moviePoster = screen.getByAltText("Example Film poster");
  fireEvent.error(moviePoster);
  expect(moviePoster).toHaveAttribute("src", "/placeholder.svg");
  expect(moviePoster).not.toHaveAttribute("srcset");
  expect(screen.getByText("Actor")).toBeInTheDocument();
  expect(screen.getByText(/IMDb ★ 8.2/)).toBeInTheDocument();
  expect(screen.getByText("8.5 / 10")).toBeInTheDocument();
  expect(screen.getByText("2 ratings")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Rate this movie" }));
  expect(screen.getByText("Sign in to rate this movie")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Login" })).toHaveAttribute("href", "/login?returnTo=%2Fmovies%2F1");
  expect(screen.getByRole("link", { name: "Create account" })).toHaveAttribute("href", "/signup?returnTo=%2Fmovies%2F1");
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
  expect(screen.queryByText("OTT Availability")).not.toBeInTheDocument();
  expect(screen.queryByText("OTT Research")).not.toBeInTheDocument();
  expect(screen.queryByText("OTTTracker Verification")).not.toBeInTheDocument();
  expect(screen.getByTestId("movie-display-id")).toHaveTextContent(/^ID101$/);
  expect(screen.getByText("Malayalam")).toHaveAttribute("href", "/languages/ml");
  expect(screen.queryByText("TMDB ID")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "tt1234567" })).toHaveAttribute(
    "href",
    "https://www.imdb.com/title/tt1234567/",
  );
  expect(screen.getByAltText("Actor profile")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Trailers & Videos" })).toBeInTheDocument();
  const poster = screen.getByRole("button", { name: "Play Official Trailer" });
  expect(poster).toBeInTheDocument();
  fireEvent.click(poster);
  expect(screen.getByTitle("Official Trailer")).toHaveAttribute("src", "https://www.youtube-nocookie.com/embed/OfficialML1?autoplay=0");
  expect(screen.queryByText(/platform confidence|date confidence|View source|View candidate source/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/Last verified:/)).not.toBeInTheDocument();
  expect(await screen.findByText("No comments yet. Be the first to comment.")).toBeInTheDocument();
});

it("lets an authenticated user create and update a community rating", async () => {
  const detail = {
    movie: { ...card, display_id: 101, original_language: "ml", spoken_languages: [], production_countries: [], production_companies: [], ott: [], collection: null },
    cast: [], crew: [], crew_by_role: {}, images: [], releases: [], ratings: [], community_rating: { average: null, count: 0 },
    keywords: [], alternative_titles: [], external_ids: [], trailer: null, videos: [],
  };
  get.mockImplementation((path) => Promise.resolve(path.includes("/public-comments") ? { items: [], total: 0, page: 1, pages: 0 } : detail));
  userGet.mockResolvedValue({ rating: 7.5 });
  authenticatedPut.mockResolvedValue({ average: 8, count: 1, user_rating: 8, created: false });

  render(<MemoryRouter initialEntries={["/movies/1"]}><Routes><Route path="/movies/:id" element={<Movie />} /></Routes></MemoryRouter>);
  const selector = await screen.findByLabelText("Your rating");
  expect(selector).toHaveValue("7.5");
  expect(selector.querySelectorAll("option")).toHaveLength(20);
  fireEvent.change(selector, { target: { value: "8" } });
  fireEvent.click(screen.getByRole("button", { name: "Save rating" }));

  await waitFor(() => expect(authenticatedPut).toHaveBeenCalledWith(
    "/api/v1/movies/1/community-rating", { rating: 8 },
  ));
  expect(await screen.findByText("Rating updated.")).toBeInTheDocument();
  expect(screen.getByText("8.0 / 10")).toBeInTheDocument();
  expect(screen.getByText("1 rating")).toBeInTheDocument();
});

it("shows a clean no-trailer state and treats submitted comment HTML as text", async () => {
  const detail = {
    movie: { ...card, display_id: 101, release_status: "Released", release_status_code: "THEATRICALLY_RELEASED", original_language: "ml", spoken_languages: [], production_countries: [], production_companies: [], ott: [], collection: null },
    cast: [], crew: [], crew_by_role: {}, images: [], releases: [], ratings: [], keywords: [], alternative_titles: [], external_ids: [], trailer: null, videos: [],
  };
  get.mockImplementation((path) => Promise.resolve(path.includes("/public-comments") ? { items: [{ id: 1, display_name: "Anand", comment: "<script>alert(1)</script> Great movie", created_at: new Date().toISOString() }], total: 1, page: 1, pages: 1 } : detail));
  post.mockResolvedValue({ id: 2, status: "PENDING", message: "Your comment has been submitted for review." });
  render(<MemoryRouter initialEntries={["/movies/1"]}><Routes><Route path="/movies/:id" element={<Movie />} /></Routes></MemoryRouter>);
  expect(await screen.findByText("<script>alert(1)</script> Great movie")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Trailers & Videos" })).not.toBeInTheDocument();
  expect(await screen.findByText("<script>alert(1)</script> Great movie")).toBeInTheDocument();
  expect(document.querySelector(".comment script")).toBeNull();
  fireEvent.change(screen.getByLabelText("Display Name *"), { target: { value: "Viewer" } });
  fireEvent.change(screen.getByLabelText("Comment *"), { target: { value: "Loved it" } });
  fireEvent.click(screen.getByRole("button", { name: "Submit comment" }));
  expect(await screen.findByText("Your comment has been submitted for review.")).toBeInTheDocument();
  expect(post).toHaveBeenCalledWith("/api/v1/movies/1/comments", { display_name: "Viewer", comment: "Loved it" });
});

it("restores the previous request template and accepts a Deep Search selection", async () => {
  get.mockResolvedValue([{ code: "ml", name: "Malayalam" }]);
  post.mockResolvedValue({ request_id: "REQ-1", status: "PENDING", verified_title: "Aadu", original_title: "ആട്", poster_path: "/aadu.jpg", confirmation_email_status: "SENT" });
  render(
    <MemoryRouter initialEntries={["/request-movie?movie_name=Aadu&movie_external_id=326282&release_year=2015&language=ml"]}>
      <Routes><Route path="/request-movie" element={<Request/>}/></Routes>
    </MemoryRouter>,
  );
  await screen.findByRole("option", { name: "Malayalam" });
  expect(screen.getByLabelText("Movie Name *")).toHaveValue("Aadu");
  expect(screen.getByRole("tablist")).toBeInTheDocument();
  expect(document.querySelector("main")).toHaveClass("request-page");
  expect(screen.getByRole("tab", { name: "Request movie" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Report issue" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Request access" })).toBeInTheDocument();
  expect(document.querySelector("main form.request")).not.toHaveClass("request-card");
  expect(screen.getByText(/Ask us to review any movie, including one that is already listed here/)).toHaveTextContent("Your contact details are used only to process this request and are never shown publicly. Can't find the movie? You can also search for movie and OTT information using Deep Search.");
  expect(screen.getByRole("link", { name: "Deep Search" })).toHaveAttribute("href", "/deep-search");
  expect(screen.getByRole("link", { name: "Try Deep Search" })).toHaveAttribute("href", "/deep-search");
  expect(screen.getByLabelText("ID (optional)")).toHaveValue(326282);
  expect(screen.queryByText(/TMDB ID/i)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Year")).toHaveValue(2015);
  expect(screen.getByLabelText("Language")).toHaveValue("ml");
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
  fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
  expect(await screen.findByRole("heading", { name: "Verified Film" })).toBeInTheDocument();
  expect(screen.getByText(/request was received, but we could not send the confirmation email/i)).toBeInTheDocument();
});

it("keeps issue and access on the single request page with tabs", async () => {
  get.mockResolvedValue([]);
  post.mockResolvedValue({ request_id: "WEB-1", status: "NEW", type: "ACCESS_REQUEST", discord_status: "PENDING", receipt_email_status: "NOT_SUPPLIED" });
  render(<MemoryRouter initialEntries={["/request-movie?tab=issue&type=INCORRECT_MOVIE"]}><Routes><Route path="/request-movie" element={<Request/>}/></Routes></MemoryRouter>);
  expect(screen.getByRole("tablist")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Report issue" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Request access" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Report an issue" })).toBeInTheDocument();
  expect(screen.getByLabelText("Name")).not.toBeRequired();
  fireEvent.change(screen.getByRole("combobox", { name: /Request type/i }), { target: { value: "INCORRECT_MOVIE" } });
  expect(screen.getByLabelText("ID")).toBeInTheDocument();
  expect(screen.queryByText(/TMDB ID/i)).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: /Request type/i }), { target: { value: "WEBSITE_ISSUE" } });
  fireEvent.change(screen.getByLabelText("WhatsApp Number (Preferred)"), { target: { value: "+65 8000 0000" } });
  fireEvent.change(screen.getByLabelText("Comment / Description *"), { target: { value: "Please review access." } });
  fireEvent.click(screen.getByRole("button", { name: "Send to administrator" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/v1/contact-requests", expect.objectContaining({ request_type: "WEBSITE_ISSUE", whatsapp: "+65 8000 0000" })));
  expect(await screen.findByRole("heading", { name: "Submission received" })).toBeInTheDocument();
});

it("opens the maintenance tab directly and shows the operational empty state", async () => {
  get.mockImplementation((path) => Promise.resolve(
    path === "/api/v1/maintenance"
      ? { overall_status: "OPERATIONAL", active: [], upcoming: [], history: [] }
      : [],
  ));
  render(<MemoryRouter initialEntries={["/request-movie?tab=maintenance"]}><Routes><Route path="/request-movie" element={<Request/>}/></Routes></MemoryRouter>);
  expect(screen.getByRole("tab", { name: "Maintenance Window" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByRole("heading", { name: "All systems operational" })).toBeInTheDocument();
  expect(screen.getByText("There is no scheduled maintenance at this time.")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Service status" })).toBeInTheDocument();
  expect(screen.getByText("Movies")).toBeInTheDocument();
  expect(screen.getByText("No recent maintenance history.")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Request movie" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Report issue" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Request access" })).toBeInTheDocument();
});

it("shows active progress, upcoming countdown, services, timelines and completed history", async () => {
  const now = Date.now();
  const iso = (offset) => new Date(now + offset).toISOString();
  const base = { description: "Routine maintenance.", affected_services: ["Movies", "Movie Search", "OTT Calendar"], impact_level: "LOW", impact_message: "Brief interruptions may occur.", updated_at: "2026-09-17T12:00:00Z" };
  get.mockImplementation((path) => Promise.resolve(path === "/api/v1/maintenance" ? {
    overall_status: "ACTIVE",
    active: [{ ...base, id: 1, title: "Active work", status: "ACTIVE", start_time: iso(-30 * 60_000), end_time: iso(30 * 60_000) }],
    upcoming: [{ ...base, id: 2, title: "Upcoming work", status: "SCHEDULED", start_time: iso(24 * 60 * 60_000), end_time: iso(25 * 60 * 60_000) }],
    history: [{ ...base, id: 3, title: "Completed work", status: "COMPLETED", start_time: iso(-48 * 60 * 60_000), end_time: iso(-47 * 60 * 60_000) }],
  } : []));
  render(<MemoryRouter initialEntries={["/request-movie?tab=maintenance"]}><Routes><Route path="/request-movie" element={<Request/>}/></Routes></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Maintenance in progress" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Active work" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Upcoming work" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Completed work" })).toBeInTheDocument();
  expect(screen.getAllByText(/IST/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/SGT/).length).toBeGreaterThan(0);
  expect(document.body).toHaveTextContent("Brief interruptions may occur.");
  expect(screen.getByRole("progressbar", { name: "Estimated maintenance progress" })).toBeInTheDocument();
  expect(screen.getByText("Maintenance begins in")).toBeInTheDocument();
  expect(screen.getAllByLabelText("Maintenance timeline")).toHaveLength(3);
  expect(screen.getAllByText("Movies").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Affected").length).toBeGreaterThan(0);
});

it("previews, removes, replaces, drops and submits an optional issue screenshot", async () => {
  get.mockResolvedValue([]);
  post.mockResolvedValue({ request_id: "WEB-SCREEN", status: "NEW", type: "WEBSITE_ISSUE", screenshot_attached: true });
  render(<MemoryRouter initialEntries={["/request-movie?tab=issue"]}><Routes><Route path="/request-movie" element={<Request/>}/></Routes></MemoryRouter>);
  const input = screen.getByLabelText(/Drag & drop screenshot here/i);
  const png = new File([new Uint8Array([1, 2, 3])], "calendar.png", { type: "image/png" });
  fireEvent.change(input, { target: { files: [png] } });
  expect(screen.getByText("calendar.png")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
  expect(screen.getByLabelText("Replace screenshot")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  expect(screen.queryByText("calendar.png")).not.toBeInTheDocument();

  const dropped = new File([new Uint8Array([4, 5, 6])], "phone.webp", { type: "image/webp" });
  fireEvent.drop(screen.getByLabelText(/Drag & drop screenshot here/i).closest("label"), { dataTransfer: { files: [dropped] } });
  expect(screen.getByText("phone.webp")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("WhatsApp Number (Preferred)"), { target: { value: "+65 8000 0000" } });
  fireEvent.change(screen.getByLabelText("Comment / Description *"), { target: { value: "The mobile calendar is misaligned." } });
  fireEvent.click(screen.getByRole("button", { name: "Send to administrator" }));
  await waitFor(() => expect(post).toHaveBeenCalled());
  const submitted = post.mock.calls.at(-1)[1];
  expect(submitted).toBeInstanceOf(FormData);
  expect(submitted.get("screenshot").name).toBe("phone.webp");
  expect(submitted.get("request_type")).toBe("WEBSITE_ISSUE");
});

it("rejects invalid screenshot type and files over 5 MB before submission", () => {
  get.mockResolvedValue([]);
  render(<MemoryRouter initialEntries={["/request-movie?tab=issue"]}><Routes><Route path="/request-movie" element={<Request/>}/></Routes></MemoryRouter>);
  const input = screen.getByLabelText(/Drag & drop screenshot here/i);
  fireEvent.change(input, { target: { files: [new File(["text"], "notes.txt", { type: "text/plain" })] } });
  expect(screen.getByRole("alert")).toHaveTextContent("Please upload a PNG, JPG, or WEBP image.");
  expect(screen.getByRole("button", { name: "Send to administrator" })).toBeDisabled();
  fireEvent.change(input, { target: { files: [new File([new Uint8Array(5 * 1024 * 1024 + 1)], "huge.jpg", { type: "image/jpeg" })] } });
  expect(screen.getByRole("alert")).toHaveTextContent("Screenshot must be 5 MB or smaller.");
  expect(post).not.toHaveBeenCalled();
});

it("submits a request access submission", async () => {
  get.mockResolvedValue([]);
  post.mockResolvedValue({ request_id: "ACC-1", status: "NEW", type: "ACCESS_REQUEST", discord_status: "PENDING", receipt_email_status: "NOT_SUPPLIED" });
  render(<MemoryRouter initialEntries={["/request-movie?tab=access"]}><Routes><Route path="/request-movie" element={<Request/>}/></Routes></MemoryRouter>);
  expect(screen.getByRole("heading", { name: "Request access" })).toBeInTheDocument();
  expect(screen.getByLabelText("Reason for access *")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("WhatsApp Number (Preferred)"), { target: { value: "+65 8000 0000" } });
  fireEvent.change(screen.getByLabelText("Email Address"), { target: { value: "viewer@example.com" } });
  fireEvent.change(screen.getByLabelText("Reason for access *"), { target: { value: "Please review access." } });
  fireEvent.click(screen.getByRole("button", { name: "Submit access request" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/v1/contact-requests", expect.objectContaining({ request_type: "ACCESS_REQUEST", whatsapp: "+65 8000 0000", email: "viewer@example.com" })));
  expect(await screen.findByRole("heading", { name: "Submission received" })).toBeInTheDocument();
});

it("orders homepage section listings as movies, pagination, then functional filters", async () => {
  get.mockResolvedValue({
    items: [card], total: 30, page: 1, page_size: 24, pages: 2,
    filters: { languages: languageOptions, genres: [], platforms: [], roles: [] },
    section: "popular",
  });
  render(<MemoryRouter initialEntries={["/discover?view_mode=section_listing&section=popular&sort=popularity"]}><Routes><Route path="/discover" element={<Discover/>}/></Routes></MemoryRouter>);
  const title = await screen.findByRole("heading", { name: "Popular movies" });
  const grid = document.querySelector(".section-listing .grid");
  const pager = screen.getByRole("navigation", { name: "Pagination" });
  const filters = screen.getByRole("form", { name: "Movie filters" });
  expect(title.compareDocumentPosition(grid) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(grid.compareDocumentPosition(pager) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(pager.compareDocumentPosition(filters) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Indian" }));
  fireEvent.click(screen.getByRole("tab", { name: "Malayalam" }));
  await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringMatching(/section=popular.*language=ml|language=ml.*section=popular/)));
});

it("supports person credit controls and separates normalized roles", async () => {
  get.mockResolvedValue({
    id: 2,
    display_id: 10,
    name: "Actor",
    department: "Acting",
    profile_path: null,
    seo_indexable: true,
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
  const calendarPayload = {
    period: "this-month",
    today: "2026-08-29",
    start_date: "2026-08-01",
    end_date: "2026-08-31",
    languages: [
      { code: "ml", name: "Malayalam", region: "indian", group: "primary" },
      { code: "ta", name: "Tamil", region: "indian", group: "primary" },
    ],
    theatrical: {
      items: [
        { ...card, title: "Malayalam Film", theatrical_release_date: "2026-08-27" },
        { ...card, id: 2, title: "Tamil Film", language: "ta", language_name: "Tamil", theatrical_release_date: "2026-08-28" },
      ],
      total: 2,
    },
    ott: { items: [], total: 0 },
  };
  get.mockImplementation((path) => Promise.resolve(
    path.includes("language=ta")
      ? {
          ...calendarPayload,
          theatrical: {
            items: calendarPayload.theatrical.items.filter((item) => item.language === "ta"),
            total: 1,
          },
        }
      : calendarPayload,
  ));
  render(
    <MemoryRouter initialEntries={["/calendar/this-month"]}>
      <Routes><Route path="/calendar/:period" element={<Calendar />} /></Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByRole("link", { name: "Previous month, July 2026" })).toHaveAttribute(
    "href", "/calendar/this-month?month=2026-07",
  );
  fireEvent.click(screen.getByRole("tab", { name: "Indian" }));
  fireEvent.click(screen.getByRole("tab", { name: "Tamil" }));
  await waitFor(() => expect(screen.queryByText("Malayalam Film")).not.toBeInTheDocument());
  expect(await screen.findByText("Tamil Film")).toBeInTheDocument();
  expect(get).toHaveBeenCalledWith("/api/v1/calendar/this-month?language=ta");
});

it("marks a low-value accessible person page noindex", async () => {
  get.mockResolvedValue({
    id: 4, display_id: 44, name: "Minor Contributor", department: "Other",
    profile_path: null, seo_indexable: false, roles: [], filmography: [],
  });
  render(<MemoryRouter initialEntries={["/people/4"]}><Routes><Route path="/people/:id" element={<Person />} /></Routes></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "Minor Contributor" })).toBeInTheDocument();
  await waitFor(() => expect(document.head.querySelector('meta[name="robots"]')).toHaveAttribute("content", "noindex,follow"));
});

it("filters discover by the global all, Indian group, and individual language tabs", async () => {
  get.mockResolvedValue({
    items: [], total: 0, page: 1, page_size: 24, pages: 0,
    filters: {
      languages: languageOptions,
      genres: [], platforms: [], roles: [],
    },
  });
  render(<MemoryRouter initialEntries={["/discover"]}><Routes><Route path="/discover" element={<Discover/>}/></Routes></MemoryRouter>);
  await screen.findByRole("heading", { name: /Movies/ });
  expect(screen.getByRole("tab", { name: "All" })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("tab", { name: "Indian" }));
  await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringContaining("language=indian")));
  expect(screen.getByRole("tab", { name: "Malayalam" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Malayalam" }));
  await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringContaining("language=ml")));
  fireEvent.click(screen.getByRole("tab", { name: "English" }));
  await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringContaining("language=en")));
});

it("renders a genre dropdown and immediately applies or clears the genre filter", async () => {
  get.mockResolvedValue({
    items: [], total: 0, page: 1, page_size: 24, pages: 0,
    filters: {
      languages: languageOptions,
      genres: [], platforms: [], roles: [],
    },
  });
  render(<MemoryRouter initialEntries={["/discover"]}><Routes><Route path="/discover" element={<Discover/>}/></Routes></MemoryRouter>);

  const genre = await screen.findByRole("combobox", { name: "Genre" });
  expect(genre).toHaveValue("");
  expect(screen.getByRole("option", { name: "All Genres" })).toBeInTheDocument();
  expect(genre.querySelectorAll("option")).toHaveLength(19);

  fireEvent.change(genre, { target: { value: "science-fiction" } });
  await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringContaining("genre=science-fiction")));

  fireEvent.change(genre, { target: { value: "" } });
  await waitFor(() => expect(get.mock.calls.at(-1)[0]).not.toContain("genre="));

  fireEvent.change(screen.getByRole("combobox", { name: "Sort" }), { target: { value: "popularity" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Release status" }), { target: { value: "released" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  await waitFor(() => {
    const request = get.mock.calls.at(-1)[0];
    expect(request).toContain("sort=popularity");
    expect(request).toContain("release_status=released");
  });
});

it.each([
  ["OTT landing", "/ott", "/ott", Ott, "/api/v1/ott?language=ja"],
  ["OTT platform", "/ott/netflix", "/ott/:platform", OttPlatform, "/api/v1/ott/netflix?sort=ott-release&page=1&language=ja"],
])("keeps global language selection in the %s URL-backed request", async (_name, path, route, Component, expected) => {
  get.mockResolvedValue(path === "/ott" ? {
    platforms: [], upcoming: [], recent: [], confirmed: [],
    languages: languageOptions, language_counts: {},
  } : {
    platform: "Netflix", upcoming: [], recent: [], available: [],
    page: 1, pages: 0, languages: languageOptions, language_counts: {},
  });
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path={route} element={<Component />} /></Routes>
    </MemoryRouter>,
  );
  await screen.findByRole("tab", { name: "Japanese" });
  fireEvent.click(screen.getByRole("tab", { name: "Japanese" }));
  await waitFor(() => expect(get).toHaveBeenCalledWith(expected));
});

it("renders every OTT release returned for a date without a two-card cap", async () => {
  const releases = Array.from({ length: 4 }, (_, index) => ({
    ...card,
    id: index + 10,
    title: `OTT Film ${index + 1}`,
    ott_release_date: "2026-09-11",
    ott_platform: index % 2 ? "Prime Video" : "Netflix",
    ott_releases: [
      {
        platform: index % 2 ? "Prime Video" : "Netflix",
        release_date: "2026-09-11",
        country: "IN",
      },
    ],
  }));
  get.mockResolvedValue({
    period: "this-month",
    today: "2026-09-10",
    start_date: "2026-09-01",
    end_date: "2026-09-30",
    theatrical: { items: [], total: 0 },
    ott: { items: releases, total: releases.length },
  });

  render(
    <MemoryRouter initialEntries={["/calendar/this-month?tab=ott&month=2026-09"]}>
      <Routes><Route path="/calendar/:period" element={<Calendar />} /></Routes>
    </MemoryRouter>,
  );

  expect(await screen.findByRole("heading", { name: "11 September 2026" })).toBeInTheDocument();
  expect(screen.getByText("4 releases")).toBeInTheDocument();
  expect(document.querySelectorAll(".calendar-grid .calendar-movie")).toHaveLength(4);
  releases.forEach((release) => {
    expect(screen.getByText(release.title)).toBeInTheDocument();
  });
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
    expect(get).toHaveBeenCalledWith("/api/v1/calendar/this-month?language=ml");
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
