import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { authenticatedPut, get, imageUrl, post, userGet } from "../services/api";
import { Card, Failure, Loading } from "../components/ui";
import Seo, { breadcrumbJsonLd } from "../components/Seo";
import AdSlot from "../components/AdSlot";
import analytics from "../services/analytics";
import { languageName } from "../services/languages";
import LanguageTabs from "../components/LanguageTabs";

const SORTS = [
  ["latest", "Latest"],
  ["oldest", "Oldest"],
  ["highest-rated", "Highest rated"],
  ["popularity", "Popularity"],
  ["recently-added", "Recently added"],
  ["ott-release", "OTT release date"],
  ["name-asc", "Name A–Z"],
  ["name-desc", "Name Z–A"],
];

const GENRES = [
  ["action", "Action"],
  ["adventure", "Adventure"],
  ["animation", "Animation"],
  ["comedy", "Comedy"],
  ["crime", "Crime"],
  ["documentary", "Documentary"],
  ["drama", "Drama"],
  ["family", "Family"],
  ["fantasy", "Fantasy"],
  ["history", "History"],
  ["horror", "Horror"],
  ["music", "Music"],
  ["mystery", "Mystery"],
  ["romance", "Romance"],
  ["science-fiction", "Science Fiction"],
  ["thriller", "Thriller"],
  ["war", "War"],
  ["western", "Western"],
];

const formatDate = (value) => {
  if (!value) return null;
  const parsed = new Date(String(value).includes("T") ? value : `${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(parsed);
};

const useData = (path) => {
  const [data, setData] = useState();
  const [error, setError] = useState();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let current = true;
    setError(undefined);
    get(path)
      .then((value) => current && setData(value))
      .catch((reason) => current && setError(reason.message));
    return () => {
      current = false;
    };
  }, [path, revision]);
  return [data, error, () => setRevision((value) => value + 1)];
};

const Rail = ({ title, items = [], more }) =>
  items.length ? (
    <section>
      <div className="section-title">
        <h2>{title}</h2>
        {more && <Link to={more}>View more</Link>}
      </div>
      <div className="rail">
        {items.map((movie) => (
          <Card key={movie.id} movie={movie} />
        ))}
      </div>
    </section>
  ) : null;
const sectionListingUrl = (section, filters = {}) => {
  const query = new URLSearchParams({
    view_mode: "section_listing",
    section,
    ...filters,
  });
  return `/discover?${query}`;
};
const languageParams = (value) => (
  value && value !== "all" ? { language: value } : {}
);
const Art = ({ path, alt, className = "", size = "w500", hideOnError = false }) => (
  <img
    className={className}
    loading="lazy"
    decoding="async"
    src={imageUrl(path, size)}
    srcSet={
      path
        ? `${imageUrl(path, "w342")} 342w, ${imageUrl(path, "w500")} 500w, ${imageUrl(path, "w780")} 780w`
        : undefined
    }
    sizes="(max-width: 640px) 90vw, 500px"
    alt={alt}
    onError={(event) => {
      event.currentTarget.onerror = null;
      event.currentTarget.removeAttribute("srcset");
      event.currentTarget.alt = "";
      if (hideOnError) {
        event.currentTarget.hidden = true;
        return;
      }
      event.currentTarget.src = "/placeholder.svg";
    }}
  />
);
const Empty = ({ children = "No movies match these filters yet." }) => (
  <p className="empty">{children}</p>
);

const languageItems = (items = []) => items.map((item) => (
  Array.isArray(item)
    ? { code: item[0], name: item[1] }
    : item
));

function LanguageDirectory({ groups }) {
  const [region, setRegion] = useState("indian");
  const selected = groups?.[region] || {
    primary: [],
    more: [],
  };
  const primary = languageItems(selected.primary);
  const more = languageItems(selected.more);
  return (
    <section className="language-directory" aria-labelledby="language-directory-title">
      <div className="section-title">
        <h2 id="language-directory-title">Languages</h2>
      </div>
      <div className="language-region-tabs" role="tablist" aria-label="Language region">
        {[["indian", "Indian"], ["international", "International"]].map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={region === value}
            className={region === value ? "active" : ""}
            onClick={() => setRegion(value)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="chips language-primary-links">
        {primary.map((item) => <Link key={item.code} to={`/languages/${item.code}`}>{item.name}</Link>)}
      </div>
      {more.length > 0 && (
        <div className="language-more">
          <h3>More {region === "indian" ? "Indian" : "International"}</h3>
          <div className="chips">
            {more.map((item) => <Link key={item.code} to={`/languages/${item.code}`}>{item.name}</Link>)}
          </div>
        </div>
      )}
    </section>
  );
}

function Pager({ page, pages, onPage }) {
  if (!pages || pages < 2) return null;
  return (
    <nav className="pager" aria-label="Pagination">
      <button disabled={page <= 1} onClick={() => onPage(page - 1)}>
        Previous
      </button>
      <span>
        Page {page} of {pages}
      </span>
      <button disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Next
      </button>
    </nav>
  );
}

export function Home() {
  const [homeParams, setHomeParams] = useSearchParams();
  const selectedLanguage = homeParams.get("language") || "all";
  const homeQuery = new URLSearchParams(languageParams(selectedLanguage));
  const [data, error, retry] = useData(
    `/api/v1/home${homeQuery.size ? `?${homeQuery}` : ""}`,
  );
  const setLanguage = (value) => {
    const next = new URLSearchParams(homeParams);
    if (value && value !== "all") next.set("language", value);
    else next.delete("language");
    setHomeParams(next, { replace: true });
  };
  const website = {
    "@context": "https://schema.org",
    "@type": "WebSite",
    name: "OTT Tracker",
    description: "Discover movies from India and around the world across theaters and streaming platforms.",
    url: import.meta.env.VITE_SITE_URL || location.origin,
    potentialAction: {
      "@type": "SearchAction",
      target: `${import.meta.env.VITE_SITE_URL || location.origin}/search?q={search_term_string}`,
      "query-input": "required name=search_term_string",
    },
  };
  return (
    <main>
      <Seo title="OTT Tracker" description="Discover popular, upcoming and recently released movies across theaters and streaming platforms worldwide, with release dates, ratings, trailers and verified OTT availability." jsonLd={website} />
      <div className="hero">
        <p>Movies from India and around the world, all in one place</p>
        <h1>Movies &amp; OTT Releases Worldwide</h1>
        <p>
          Discover popular, upcoming and recently released movies across theaters
          and streaming platforms worldwide, with release dates, ratings, trailers
          and verified OTT availability.
        </p>
        <Link to="/discover">Explore movies</Link>
      </div>
      {error && (
        <div className="request-error" role="alert">
          <h2>Some homepage sections could not be loaded</h2>
          <p>
            {error}. You can still browse{" "}
            <Link to="/discover">all movies</Link>,{" "}
            <Link to="/ott">OTT releases</Link> and the{" "}
            <Link to="/calendar/this-week">release calendar</Link>.
          </p>
          <button type="button" onClick={retry}>Retry</button>
        </div>
      )}
      {!data ? (
        !error && <p className="loading">Loading movies…</p>
      ) : (
        <>
          <LanguageTabs
            value={selectedLanguage}
            languages={data.languages}
            onChange={setLanguage}
            className="homepage-language-filter"
          />
          <Rail
            title="Popular"
            items={data.popular}
            more={sectionListingUrl("popular", { sort: "popularity", ...languageParams(selectedLanguage) })}
          />
          <Rail title="Latest theatrical" items={data.latest_theatrical} more={sectionListingUrl("latest-theatrical", { sort: "latest", ...languageParams(selectedLanguage) })} />
          <Rail title="Upcoming theatrical" items={data.upcoming_theatrical} more={sectionListingUrl("upcoming-theatrical", { sort: "oldest", ...languageParams(selectedLanguage) })} />
          <Rail title="Recently added" items={data.recently_added} more={sectionListingUrl("recently-added", { sort: "recently-added", ...languageParams(selectedLanguage) })} />
          <Rail title="Highest rated" items={data.highest_rated} more={sectionListingUrl("highest-rated", { sort: "highest-rated", ...languageParams(selectedLanguage) })} />
          <AdSlot slot={import.meta.env.VITE_ADSENSE_SLOT_ID} />
          <Rail title="Upcoming OTT" items={data.upcoming_ott} more={sectionListingUrl("upcoming-ott", { sort: "ott-release", ...languageParams(selectedLanguage) })} />
          <Rail
            title="Recently released on OTT"
            items={data.recent_ott}
            more={sectionListingUrl("recent-ott", { sort: "ott-release", ...languageParams(selectedLanguage) })}
          />
          <section className="editorial-copy" aria-labelledby="what-is-otttracker">
            <h2 id="what-is-otttracker">What is OTTTracker?</h2>
            <p>OTTTracker follows theatrical and streaming releases across multiple languages, with strong coverage of Indian cinema and an expanding international catalogue. It records OTT platforms, release dates and lawful viewing links without hosting copyrighted movies.</p>
            <p>Platform information and dates are treated separately: a service may be identified before a reliable streaming date is known. Release calendars and verification labels show what is confirmed, what is still being checked and when information was last reviewed.</p>
          </section>
          <section className="editorial-copy" aria-labelledby="how-we-verify">
            <h2 id="how-we-verify">How OTTTracker verifies releases</h2>
            <p>We compare platform records, release evidence and freshness signals, and preserve uncertainty when sources conflict. Confirmed records are rechecked because regional catalogues and dates can change.</p>
            <Link to="/methodology">Read the full verification methodology</Link>
          </section>
          <section aria-labelledby="recently-verified">
            <div className="section-title"><h2 id="recently-verified">Recently verified OTT information</h2><Link to="/ott">Browse OTT releases</Link></div>
            <p>{data.verification_activity?.titles_updated || 0} titles updated in the last {data.verification_activity?.period_days || 7} days{data.verification_activity?.dates_confirmed ? ` · ${data.verification_activity.dates_confirmed} release dates confirmed` : ""}.</p>
            {data.verification_activity?.titles?.length > 0 && <div className="rail">{data.verification_activity.titles.map(movie => <Card key={movie.id} movie={movie}/>)}</div>}
          </section>
          <LanguageDirectory groups={data.language_groups} />
          <section>
            <h2>Browse genres</h2>
            <div className="chips">
              {data.genres.map((item) => (
                <Link key={item.slug} to={`/genres/${item.slug}`}>
                  {item.name}
                </Link>
              ))}
            </div>
          </section>
          <section>
            <h2>OTT platforms</h2>
            <div className="platforms">
              {data.platforms.map((item) => (
                <Link key={item.slug} to={`/ott/${item.slug}`}>
                  {item.logo && <Art path={item.logo} alt="" />}
                  <strong>{item.name}</strong>
                  <small>{item.movie_count} movies</small>
                </Link>
              ))}
            </div>
          </section>
        </>
      )}
    </main>
  );
}

const initialFilters = {
  q: "",
  language_region: "all",
  language: "",
  genre: "",
  year: "",
  rating: "",
  certification: "",
  release_status: "",
  platform: "",
  actor: "",
  director: "",
  writer: "",
  cinematographer: "",
  producer: "",
  editor: "",
  composer: "",
  date_from: "",
  date_to: "",
  sort: "latest",
};

const SECTION_TITLES = {
  popular: "Popular movies",
  "latest-theatrical": "Latest theatrical releases",
  "upcoming-theatrical": "Upcoming theatrical releases",
  "recently-added": "Recently added movies",
  "highest-rated": "Highest rated movies",
  "upcoming-ott": "Upcoming OTT releases",
  "recent-ott": "Recently released on OTT",
};

const FILTER_KEYS = Object.keys(initialFilters);

function MissingMovieCandidates({ query, language }) {
  const params = new URLSearchParams({ q: query, page: "1" });
  if (language) params.set("language", language);
  const [live, error] = useData(`/api/v1/deep-search/movies?${params}`);
  if (error) return <p className="muted">Live movie lookup is temporarily unavailable.</p>;
  if (!live) return <p className="muted">Checking live movie results…</p>;
  if (!live.results?.length) return <p className="muted">No verified live movie candidates were found.</p>;
  return <section className="search-live-fallback" aria-labelledby="live-movie-candidates"><h3 id="live-movie-candidates">Live movie candidates</h3><p>These titles are not in the local results. Open the live details first, or request the matching movie.</p><div className="search-live-list">{live.results.slice(0, 5).map(movie => { const request = new URLSearchParams({ movie_name: movie.title, movie_external_id: String(movie.id) }); if (movie.release_date) request.set("release_year", movie.release_date.slice(0, 4)); if (movie.original_language) request.set("language", movie.original_language); return <article key={movie.id}><span><strong>{movie.title}</strong>{movie.release_date && <small>{movie.release_date.slice(0, 4)}</small>}</span><div className="deep-actions"><Link className="button-link" to={`/deep-search/movie/${movie.id}`}>View live details</Link>{movie.local_movie_id ? <Link to={`/movies/${movie.local_movie_id}`}>Open Local Movie</Link> : <Link to={`/request-movie?${request}`}>Request Movie</Link>}</div></article>; })}</div></section>;
}

export function Discover({ modeTabs = null }) {
  const location = useLocation();
  const [urlParams, setUrlParams] = useSearchParams();
  const isSearch = location.pathname === "/search";
  const section = urlParams.get("section") || "";
  const isSectionListing = !isSearch && urlParams.get("view_mode") === "section_listing";
  const initial = useMemo(
    () => {
      const values = { ...initialFilters };
      const source = new URLSearchParams(location.search);
      FILTER_KEYS.forEach((key) => {
        if (source.has(key)) values[key] = source.get(key) || "";
      });
      return values;
    },
    [location.search],
  );
  const [filters, setFilters] = useState(initial);
  useEffect(() => setFilters(initial), [initial]);
  const page = Math.max(1, Number(urlParams.get("page")) || 1);
  const query = useMemo(() => {
    const apiQuery = new URLSearchParams(
      Object.entries(initial).filter(([, value]) => value),
    );
    if (section) apiQuery.set("section", section);
    apiQuery.set("page", String(page));
    return apiQuery.toString();
  }, [initial, page, section]);
  const searchQuery = new URLSearchParams({
    q: initial.q || "",
    page: String(page),
    language_region: initial.language_region || "all",
    ...(initial.language
      ? { language: initial.language }
      : {}),
  });
  const endpoint = isSearch
    ? `/api/v1/search?${searchQuery}`
    : `/api/v1/discover?${query}`;
  const [data, error] = useData(endpoint);
  const allLanguageItems = languageItems(data?.filters?.languages || []);
  const selectedLanguage = filters.language || "all";
  const submit = (event) => {
    event.preventDefault();
    const next = new URLSearchParams(
      Object.entries(filters).filter(([, value]) => value),
    );
    if (isSectionListing) next.set("view_mode", "section_listing");
    if (section) next.set("section", section);
    if (isSearch) {
      analytics.search(filters.q, {
        language: filters.language,
        genre: filters.genre,
        platform: filters.platform,
      });
    } else {
      Object.entries(filters)
        .filter(([name, value]) => value && name !== "q")
        .forEach(([name, value]) => analytics.filterUsed(name, value));
    }
    setUrlParams(next);
  };
  const set = (event) =>
    setFilters((value) => ({
      ...value,
      [event.target.name]: event.target.value,
    }));
  const selectLanguage = (value) => {
    const language = value === "all" ? "" : value;
    setFilters((current) => ({
      ...current,
      language,
      language_region: "all",
    }));
    const next = new URLSearchParams(urlParams);
    if (language) next.set("language", language);
    else next.delete("language");
    next.delete("language_region");
    next.delete("page");
    setUrlParams(next, { replace: true });
  };
  const selectGenre = (event) => {
    const genre = event.target.value;
    setFilters((current) => ({ ...current, genre }));
    const next = new URLSearchParams(urlParams);
    if (genre) next.set("genre", genre);
    else next.delete("genre");
    next.delete("page");
    setUrlParams(next, { replace: true });
  };
  const movies = isSearch ? data?.movies?.items || [] : data?.items || [];
  const total = isSearch ? data?.movies?.total || 0 : data?.total || 0;
  const pageTitle = isSearch
    ? "Search movies and people"
    : section === "language"
      ? `${allLanguageItems.find((item) => item.code === initial.language)?.name || languageName(initial.language)} movies`
      : SECTION_TITLES[section] || "Discover movies";
  const onPage = (nextPage) => {
    const next = new URLSearchParams(urlParams);
    if (nextPage > 1) next.set("page", String(nextPage));
    else next.delete("page");
    setUrlParams(next);
  };
  const filterForm = (
    <form className="filters" onSubmit={submit} aria-label="Movie filters">
      <label className="wide">
        Search
        <input
          name="q"
          value={filters.q}
          onChange={set}
          placeholder={isSearch ? "Movie title or person name" : "Title, actor, director, writer or keyword"}
        />
      </label>
      <LanguageTabs
        value={selectedLanguage}
        languages={allLanguageItems}
        onChange={selectLanguage}
        className="wide"
      />
      {!isSearch && (
        <>
          <label>
            Genre
            <select name="genre" value={filters.genre} onChange={selectGenre}>
              <option value="">All Genres</option>
              {GENRES.map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <label>Year<input name="year" type="number" value={filters.year} onChange={set} /></label>
          <label>Minimum IMDb rating<input name="rating" type="number" min="0" max="10" step="0.5" value={filters.rating} onChange={set} /></label>
          <label>Certification<input name="certification" value={filters.certification} onChange={set} /></label>
          <label>Release status<select name="release_status" value={filters.release_status} onChange={set}><option value="">All</option><option value="released">Released</option><option value="upcoming">Upcoming</option><option value="direct-to-ott">Direct-to-OTT</option></select></label>
          <label>OTT platform<input name="platform" value={filters.platform} onChange={set} /></label>
          {["actor", "director", "writer", "cinematographer", "producer", "editor", "composer"].map((name) => <label key={name}>{name}<input name={name} value={filters[name]} onChange={set} /></label>)}
          <label>From<input name="date_from" type="date" value={filters.date_from} onChange={set} /></label>
          <label>To<input name="date_to" type="date" value={filters.date_to} onChange={set} /></label>
          <label>Sort<select name="sort" value={filters.sort} onChange={set}>{SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        </>
      )}
      <button className="wide">Apply</button>
    </form>
  );
  const pager = data ? (
    <Pager
      page={page}
      pages={isSearch ? Math.max(Math.ceil(total / data.page_size), Math.ceil(data.people.total / data.page_size)) : data.pages}
      onPage={onPage}
    />
  ) : null;
  return (
    <main className={isSectionListing ? "section-listing" : undefined}>
      <Seo title={pageTitle} />
      <h1>{pageTitle}</h1>
      {isSearch && modeTabs}
      {!isSectionListing && filterForm}
      {error && <p role="alert">{error}</p>}
      {!data ? (
        <Loading />
      ) : (
        <>
          <h2>
            Movies <small>({total})</small>
          </h2>
          {movies.length ? (
            <div className="grid">
              {movies.map((movie) => (
                <Card key={movie.id} movie={movie} />
              ))}
            </div>
          ) : (
            <><Empty />{isSearch && initial.q && <MissingMovieCandidates query={initial.q} language={initial.language} />}</>
          )}
          {isSearch && (
            <>
              <h2>
                People <small>({data.people.total})</small>
              </h2>
              <div className="person-results">
                {data.people.items.map((person) => (
                  <Link key={person.id} to={`/people/${person.id}`}>
                    <Art path={person.profile_path} alt="" />
                    <span>
                      <strong>{person.name}</strong>
                      <small>{person.department}</small>
                    </span>
                  </Link>
                ))}
              </div>
            </>
          )}
          {pager}
          {isSearch && <AdSlot slot={import.meta.env.VITE_ADSENSE_SLOT_ID} />}
          {isSectionListing && filterForm}
        </>
      )}
    </main>
  );
}

export function Browse() {
  const { slug, code } = useParams();
  const navigate = useNavigate();
  const [browseParams, setBrowseParams] = useSearchParams();
  const sort = browseParams.get("sort") || "latest";
  const page = Math.max(1, Number(browseParams.get("page")) || 1);
  const selectedLanguage = slug ? browseParams.get("language") || "all" : code;
  const query = new URLSearchParams({
    ...(slug ? { genre: slug, ...languageParams(selectedLanguage) } : { language: code }),
    sort,
    page: String(page),
  });
  const [data, error] = useData(
    `/api/v1/discover?${query}`,
  );
  if (error) return <Failure error={error} />;
  const configuredLanguage = data?.filters?.languages?.find(
    (item) => item.code === code,
  );
  const browseLanguageName = configuredLanguage?.name || languageName(code);
  const title = slug
    ? `${slug.replaceAll("-", " ")} movies`
    : `${browseLanguageName} movies`;
  return (
    <main>
      <Seo
        title={title}
        description={slug
          ? `Browse ${slug.replaceAll("-", " ")} movies, release information and verified India OTT availability.`
          : `Browse ${browseLanguageName} movies, release information and verified India OTT availability.`}
        noindex={Boolean(data && data.total === 0)}
        jsonLd={breadcrumbJsonLd([
          { name: "Home", path: "/" },
          { name: title, path: location.pathname },
        ])}
      />
      <h1 className="capitalize">{title}</h1>
      <LanguageTabs
        value={selectedLanguage}
        languages={data?.filters?.languages}
        onChange={(value) => {
          if (!slug) {
            navigate(value === "all" ? "/discover" : `/discover?language=${value}`);
            return;
          }
          const next = new URLSearchParams(browseParams);
          if (value !== "all") next.set("language", value);
          else next.delete("language");
          next.delete("page");
          setBrowseParams(next, { replace: true });
        }}
      />
      <label>
        Sort{" "}
        <select
          value={sort}
          onChange={(event) => {
            const next = new URLSearchParams(browseParams);
            next.set("sort", event.target.value);
            next.delete("page");
            setBrowseParams(next, { replace: true });
          }}
        >
          {SORTS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {!data ? (
        <Loading />
      ) : (
        <>
          <p>{data.total} movies</p>
          {data.items.length ? (
            <div className="grid">
              {data.items.map((movie) => (
                <Card key={movie.id} movie={movie} />
              ))}
            </div>
          ) : (
            <Empty />
          )}
          <Pager page={page} pages={data.pages} onPage={(nextPage) => {
            const next = new URLSearchParams(browseParams);
            if (nextPage > 1) next.set("page", String(nextPage));
            else next.delete("page");
            setBrowseParams(next);
          }} />
        </>
      )}
    </main>
  );
}

export function Ott() {
  const [ottParams, setOttParams] = useSearchParams();
  const selectedLanguage = ottParams.get("language") || "all";
  const query = new URLSearchParams(languageParams(selectedLanguage));
  const [data, error] = useData(`/api/v1/ott${query.size ? `?${query}` : ""}`);
  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;
  return (
    <main>
      <Seo
        title="OTT releases"
        jsonLd={breadcrumbJsonLd([
          { name: "Home", path: "/" },
          { name: "OTT", path: "/ott" },
        ])}
      />
      <h1>OTT releases</h1>
      <p>
        Confirmed and currently available streaming information from canonical
        records.
      </p>
      <LanguageTabs
        value={selectedLanguage}
        languages={data.languages}
        onChange={(value) => {
          const next = new URLSearchParams(ottParams);
          if (value !== "all") next.set("language", value);
          else next.delete("language");
          setOttParams(next, { replace: true });
        }}
      />
      <div className="platforms">
        {data.platforms.map((item) => (
          <Link key={item.slug} to={`/ott/${item.slug}`}>
            {item.logo && <Art path={item.logo} alt="" />}
            <strong>{item.name}</strong>
            <small>{item.movie_count} movies</small>
          </Link>
        ))}
      </div>
      <Rail title="Upcoming OTT releases" items={data.upcoming} />
      <Rail title="Recently released on OTT" items={data.recent} />
      <Rail title="Confirmed releases" items={data.confirmed} />
    </main>
  );
}

export function OttPlatform() {
  const { platform } = useParams();
  const [platformParams, setPlatformParams] = useSearchParams();
  const sort = platformParams.get("sort") || "ott-release";
  const page = Math.max(1, Number(platformParams.get("page")) || 1);
  const selectedLanguage = platformParams.get("language") || "all";
  const query = new URLSearchParams({
    sort,
    page: String(page),
    ...languageParams(selectedLanguage),
  });
  const [data, error] = useData(
    `/api/v1/ott/${platform}?${query}`,
  );
  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;
  return (
    <main>
      <Seo
        title={`${data.platform} movies`}
        description={`Browse ${data.platform} movies and OTT releases available across global and regional streaming markets.`}
        canonicalPath={`/ott/${data.platform.toLowerCase().replaceAll(" ", "-")}`}
        jsonLd={breadcrumbJsonLd([
          { name: "Home", path: "/" },
          { name: "OTT", path: "/ott" },
          { name: data.platform, path: location.pathname },
        ])}
      />
      <h1>{data.platform}</h1>
      <LanguageTabs
        value={selectedLanguage}
        languages={data.languages}
        onChange={(value) => {
          const next = new URLSearchParams(platformParams);
          if (value !== "all") next.set("language", value);
          else next.delete("language");
          next.delete("page");
          setPlatformParams(next, { replace: true });
        }}
      />
      <Rail title="Upcoming" items={data.upcoming} />
      <Rail title="Recently released" items={data.recent} />
      <div className="section-title">
        <h2>Available</h2>
        <select value={sort} onChange={(event) => {
          const next = new URLSearchParams(platformParams);
          next.set("sort", event.target.value);
          next.delete("page");
          setPlatformParams(next, { replace: true });
        }}>
          {SORTS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      {data.available.length ? (
        <div className="grid">
          {data.available.map((movie) => (
            <Card key={movie.id} movie={movie} />
          ))}
        </div>
      ) : (
        <Empty />
      )}
      <Pager page={page} pages={data.pages} onPage={(nextPage) => {
        const next = new URLSearchParams(platformParams);
        if (nextPage > 1) next.set("page", String(nextPage));
        else next.delete("page");
        setPlatformParams(next);
      }} />
    </main>
  );
}

const Values = ({ title, children }) =>
  children && React.Children.count(children) ? (
    <section>
      <h2>{title}</h2>
      {children}
    </section>
  ) : null;

const relativeTime = (value) => {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return "Just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  const days = Math.floor(hours / 24);
  return `${days} ${days === 1 ? "day" : "days"} ago`;
};

const videoLabel = (item) => {
  const raw = (item.video_type === "Behind The Scenes" ? "Behind the Scenes" : item.video_type) || "Trailer";
  if (item.official) return `Official ${raw}`;
  return raw;
};

const TrailersSection = ({ videos, title }) => {
  const [activeKey, setActiveKey] = useState(videos?.[0]?.video_key);
  const [playing, setPlaying] = useState(false);
  const [expanded, setExpanded] = useState(false);
  if (!videos || !videos.length) return null;
  const visible = expanded ? videos : videos.slice(0, 3);
  const active = videos.find((item) => item.video_key === activeKey) || videos[0];
  const play = (key) => {
    setActiveKey(key);
    setPlaying(true);
  };
  return (
    <section className="trailer-section" aria-labelledby="trailer-heading">
      <h2 id="trailer-heading">Trailers &amp; Videos</h2>
      <div className="trailer-frame">
        {playing ? (
          <iframe
            key={active.video_key}
            src={`${active.embed_url}?autoplay=0`}
            title={active.name || `${title} ${videoLabel(active)}`}
            loading="lazy"
            allow="encrypted-media; picture-in-picture; web-share"
            allowFullScreen
            referrerPolicy="strict-origin-when-cross-origin"
          />
        ) : (
          <button
            type="button"
            className="trailer-poster"
            onClick={() => play(active.video_key)}
            aria-label={`Play ${active.name || `${title} ${videoLabel(active)}`}`}
          >
            <img
              src={`https://i.ytimg.com/vi/${active.video_key}/hqdefault.jpg`}
              alt=""
              loading="lazy"
              width={480}
              height={360}
            />
            <span className="trailer-play">Play</span>
          </button>
        )}
      </div>
      <div className="trailer-list">
        {visible.map((item) => {
          const isActive = item.video_key === active.video_key && playing;
          return (
            <button
              key={item.video_key}
              type="button"
              className={`trailer-card${isActive ? " active" : ""}`}
              onClick={() => play(item.video_key)}
              aria-pressed={isActive}
            >
              <span className="trailer-thumb">
                <img
                  src={`https://i.ytimg.com/vi/${item.video_key}/hqdefault.jpg`}
                  alt={item.name || `${title} ${videoLabel(item)}`}
                  loading="lazy"
                  width={120}
                  height={90}
                />
                <span className="trailer-play">&#9654;</span>
              </span>
              <span className="trailer-meta">
                <span className="trailer-name">{item.name || `${title} ${videoLabel(item)}`}</span>
                <small>
                  {videoLabel(item)}
                  {item.language ? ` · ${languageName(item.language, item.language_name)}` : ""}
                </small>
              </span>
            </button>
          );
        })}
      </div>
      {videos.length > 3 && (
        <button
          type="button"
          className="link-button"
          onClick={() => setExpanded((current) => !current)}
          aria-expanded={expanded}
        >
          {expanded ? "Show fewer videos" : `View all ${videos.length} videos`}
        </button>
      )}
    </section>
  );
};

const CommentsSection = ({ movieId }) => {
  const [data, setData] = useState();
  const [page, setPage] = useState(1);
  const [error, setError] = useState();
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState();
  const load = (nextPage = 1) => {
    setError(undefined);
    get(`/api/v1/movies/${movieId}/public-comments?page=${nextPage}&page_size=10`, { maxAge: 0 })
      .then((value) => {
        setData((current) => nextPage === 1 ? value : { ...value, items: [...(current?.items || []), ...value.items] });
        setPage(nextPage);
      })
      .catch((reason) => setError(reason.message));
  };
  useEffect(() => { load(1); }, [movieId]);
  const submit = (event) => {
    event.preventDefault();
    setSubmitting(true);
    setError(undefined);
    setMessage(undefined);
    const form = event.currentTarget;
    const body = Object.fromEntries(new FormData(form));
    if (!body.email) delete body.email;
    post(`/api/v1/movies/${movieId}/comments`, body)
      .then((result) => {
        setMessage(result.message);
        form.reset();
      })
      .catch((reason) => setError(reason.message))
      .finally(() => setSubmitting(false));
  };
  return (
    <section className="comments-section" aria-labelledby="comments-heading">
      <h2 id="comments-heading">Comments</h2>
      <form className="comment-form" onSubmit={submit}>
        <label>Display Name *<input name="display_name" minLength="2" maxLength="50" required autoComplete="name" /></label>
        <label>Email <input name="email" type="email" maxLength="320" autoComplete="email" /><small>Optional and never displayed publicly.</small></label>
        <label>Comment *<textarea name="comment" minLength="2" maxLength="2000" required rows="5" /></label>
        <button disabled={submitting}>{submitting ? "Submitting…" : "Submit comment"}</button>
      </form>
      {message && <p className="comment-success" role="status">{message}</p>}
      {error && <div className="request-error" role="alert"><p>{error}</p><button onClick={() => load(1)}>Retry</button></div>}
      {!data ? <p>Loading comments…</p> : data.items.length ? (
        <div className="comment-list">
          {data.items.map((item) => (
            <article className="comment" key={item.id}>
              <header><strong>{item.display_name}</strong><time dateTime={item.created_at}>{relativeTime(item.created_at)}</time></header>
              <p>{item.comment}</p>
            </article>
          ))}
          {page < data.pages && <button onClick={() => load(page + 1)}>Load more comments</button>}
        </div>
      ) : <p className="empty">No comments yet. Be the first to comment.</p>}
    </section>
  );
};

const COMMUNITY_RATING_OPTIONS = Array.from({ length: 19 }, (_, index) => 1 + index * 0.5);

const CommunityRating = ({ movieId, initial }) => {
  const [summary, setSummary] = useState(initial || { average: null, count: 0 });
  const [authenticated, setAuthenticated] = useState(false);
  const [rating, setRating] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let current = true;
    userGet(`/api/v1/movies/${movieId}/community-rating/mine`)
      .then((value) => {
        if (!current) return;
        setAuthenticated(true);
        if (value?.rating != null) setRating(String(value.rating));
      })
      .catch(() => current && setAuthenticated(false));
    return () => { current = false; };
  }, [movieId]);

  const submit = async (event) => {
    event.preventDefault();
    if (!rating) return;
    setSaving(true);
    setMessage("");
    try {
      const result = await authenticatedPut(`/api/v1/movies/${movieId}/community-rating`, {
        rating: Number(rating),
      });
      setSummary({ average: result.average, count: result.count });
      setRating(String(result.user_rating));
      setMessage(result.created ? "Rating saved." : "Rating updated.");
    } catch (error) {
      if (error.status === 401) setAuthenticated(false);
      setMessage(error.status === 401 ? "Log in to submit a rating." : error.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="community-rating" aria-labelledby="community-rating-title">
      <div>
        <h3 id="community-rating-title">Community Rating</h3>
        <p className="community-rating-score">
          <strong>{summary.average == null ? "Not rated" : `${Number(summary.average).toFixed(1)} / 10`}</strong>
          <span>{summary.count} {summary.count === 1 ? "rating" : "ratings"}</span>
        </p>
      </div>
      {authenticated ? (
        <form onSubmit={submit}>
          <label htmlFor="community-rating-value">Your rating</label>
          <div>
            <select id="community-rating-value" value={rating} onChange={(event) => setRating(event.target.value)} required>
              <option value="" disabled>Select rating</option>
              {COMMUNITY_RATING_OPTIONS.map((value) => <option key={value} value={value}>{value.toFixed(1)}</option>)}
            </select>
            <button type="submit" disabled={saving || !rating}>{saving ? "Saving…" : "Save rating"}</button>
          </div>
        </form>
      ) : <LoggedOutRatingActions movieId={movieId} />}
      <p className="community-rating-message" role="status" aria-live="polite">{message}</p>
    </section>
  );
};

const LoggedOutRatingActions = ({ movieId }) => {
  const [open, setOpen] = useState(false);
  const returnTo = encodeURIComponent(`/movies/${movieId}`);
  return open ? (
    <div className="community-rating-signin">
      <p>Sign in to rate this movie</p>
      <div><Link to={`/login?returnTo=${returnTo}`}>Login</Link><Link to={`/signup?returnTo=${returnTo}`}>Create account</Link></div>
    </div>
  ) : <button className="community-rating-prompt" type="button" onClick={() => setOpen(true)}>Rate this movie</button>;
};

export function Movie() {
  const { id } = useParams();
  const [data, error] = useData(`/api/v1/movies/${id}/detail`);
  const trackedMovie = useRef();
  useEffect(() => {
    const movie = data?.movie;
    if (movie?.id && movie.id !== trackedMovie.current) {
      trackedMovie.current = movie.id;
      analytics.movieView(movie);
    }
  }, [data]);
  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;
  const movie = data.movie;
  const displayedOttPlatform = movie.ott_platform;
  const displayedOttDate = movie.ott_release_date;

  const images = (type) =>
    data.images.filter((item) => item.type.toLowerCase().includes(type));
  const logo = images("logo")[0];
  const movieLd = {
    "@context": "https://schema.org",
    "@type": "Movie",
    name: movie.title,
    ...(movie.original_title && { alternateName: movie.original_title }),
    ...(movie.overview && { description: movie.overview }),
    ...(data.trailer?.video_key && {
      video: {
        "@type": "VideoObject",
        name: data.trailer.name || `${movie.title} Trailer`,
        description: movie.overview || `Official trailer for ${movie.title}.`,
        ...(data.trailer.published_at && {
          uploadDate: new Date(data.trailer.published_at).toISOString().slice(0, 10),
        }),
        thumbnailUrl: `https://i.ytimg.com/vi/${data.trailer.video_key}/hqdefault.jpg`,
        embedUrl: data.trailer.embed_url,
      },
    }),
    ...(movie.release_date && { dateCreated: movie.release_date }),
    ...(movie.poster_path && {
      image: imageUrl(movie.poster_path, "original"),
    }),
    ...(movie.runtime_minutes && { duration: `PT${movie.runtime_minutes}M` }),
    ...(movie.rating != null && {
      aggregateRating: {
        "@type": "AggregateRating",
        ratingValue: movie.rating,
        ratingCount: movie.vote_count || 0,
        bestRating: 10,
        author: { "@type": "Organization", name: "IMDb" },
      },
    }),
    ...(data.cast.length && {
      actor: data.cast.map((item) => ({ "@type": "Person", name: item.name })),
    }),
    ...(data.crew_by_role.director?.length && {
      director: data.crew_by_role.director.map((item) => ({
        "@type": "Person",
        name: item.name,
      })),
    }),
  };
  return (
    <main>
      <Seo
        title={movie.title}
        description={
          movie.overview || `Details and OTT availability for ${movie.title}.`
        }
        image={imageUrl(movie.backdrop_path || movie.poster_path, "original")}
        type="video.movie"
        jsonLd={[
          movieLd,
          breadcrumbJsonLd([
            { name: "Home", path: "/" },
            { name: "Movies", path: "/discover" },
            { name: movie.title, path: location.pathname },
          ]),
        ]}
      />
      {movie.backdrop_path && (
        <Art
          className="hero-backdrop"
          path={movie.backdrop_path}
          size="original"
          alt={`${movie.title} backdrop`}
        />
      )}
      <div className="detail movie-detail">
        <Art
          className="poster"
          path={movie.poster_path}
          alt={`${movie.title} poster`}
        />
        <article>
          {logo && (
            <Art
              className="title-logo"
              path={logo.url}
              alt={`${movie.title} logo`}
              hideOnError
            />
          )}
          <p>
            {[
              movie.runtime_minutes && `${movie.runtime_minutes} min`,
              movie.certification,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <h1>{movie.title}</h1>
          {movie.original_title && movie.original_title !== movie.title && (
            <p>Original title: {movie.original_title}</p>
          )}
          {data.alternative_titles.length > 0 && (
            <p>
              Also known as:{" "}
              {data.alternative_titles.map((item) => item.title).join(", ")}
            </p>
          )}
          {movie.tagline && <blockquote>{movie.tagline}</blockquote>}
          {movie.overview && <p>{movie.overview}</p>}
          <div className="status-grid" aria-label="Release and OTT status">
            <span data-testid="release-status">
              <small>Release Status</small>
              <strong>{movie.release_status || "Unknown"}</strong>
            </span>
            <span data-testid="theatrical-release">
              <small>Theatrical Release</small>
              <strong>
                {movie.release_status_code === "DIRECT_TO_OTT"
                  ? "Not applicable"
                  : formatDate(movie.theatrical_release_date) || "Unknown"}
              </strong>
            </span>
            <span data-testid="ott-platform">
              <small>OTT Platform</small>
              <strong>{displayedOttPlatform || "Information not found"}</strong>
            </span>
            <span data-testid="ott-release">
              <small>OTT Release</small>
              <strong>
                {formatDate(displayedOttDate) ||
                  (displayedOttPlatform ? "Unknown" : "Information not found")}
              </strong>
            </span>
          </div>
          <div className="facts">
            <span>
              {movie.rating != null
                ? `IMDb ★ ${Number(movie.rating).toFixed(1)}${movie.vote_count != null ? ` (${movie.vote_count} votes)` : ""}`
                : movie.rating_status === "pending"
                  ? "IMDb rating pending"
                  : "IMDb rating unavailable"}
            </span>
            {movie.original_language && (
              <span>Original language: <Link to={`/languages/${movie.original_language}`}>{languageName(movie.original_language, movie.original_language_name)}</Link></span>
            )}
            {movie.spoken_languages.length > 0 && (
              <span>
                Spoken: {movie.spoken_languages.map((x) => x.name).join(", ")}
              </span>
            )}
            {movie.production_countries.length > 0 && (
              <span>
                Countries:{" "}
                {movie.production_countries.map((x) => x.name).join(", ")}
              </span>
            )}
            {movie.collection && (
              <span>Collection: {movie.collection.name}</span>
            )}
            {movie.budget > 0 && (
              <span>Budget: ${movie.budget.toLocaleString()}</span>
            )}
            {movie.revenue > 0 && (
              <span>Revenue: ${movie.revenue.toLocaleString()}</span>
            )}
            {movie.display_id != null && (
              <span className="identifier" data-testid="movie-display-id">
                <small>ID</small>
                <strong>{movie.display_id}</strong>
              </span>
            )}
          </div>
        </article>
      </div>
      {data.release_timeline?.length > 0 && <section className="timeline" aria-labelledby="release-timeline-title"><h2 id="release-timeline-title">Release Timeline</h2><ol>{data.release_timeline.map((event, index) => <li key={`${event.date}-${event.label}-${index}`}><time>{formatDate(event.date)}</time><strong>{event.label}</strong></li>)}</ol></section>}
      {data.international_releases?.length > 0 && <details className="international-releases"><summary>View international releases ({data.international_releases.length})</summary><ol>{data.international_releases.map((event, index) => <li key={`${event.country}-${event.date}-${event.type}-${index}`}><time>{formatDate(event.date)}</time><strong>{[event.country, event.label, event.certification].filter(Boolean).join(" · ")}</strong></li>)}</ol></details>}
      <Values title="Watch legally">
        {movie.ott.map((item, index) => (
          <article className="ott-row" key={`${item.provider}-${index}`}>
            {item.logo && <Art path={item.logo} alt="" />}
            <div>
              <strong>{item.provider}</strong>
              <p>
                {[
                  item.watch_type,
                  item.release_date,
                  item.country,
                  item.release_date ? null : "date not confirmed",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              {item.source_url && item.source === "official_platform" && (
                <a
                  href={item.source_url}
                  rel="nofollow noreferrer"
                  onClick={() => analytics.ottPlatformClick(movie, item.provider)}
                >
                  {`Watch on ${item.provider}`}
                </a>
              )}
              {item.original_premiere && <small>Original OTT premiere</small>}

            </div>
          </article>
        ))}
      </Values>
      <Values title="Ratings">
        <CommunityRating movieId={movie.id} initial={data.community_rating} />
        {data.ratings.map((item, index) => (
          <p key={`${item.source}-${index}`}>
            <strong>{item.source}</strong>: {item.rating ?? (item.status === "pending" ? "Pending" : "Unavailable")}
            {item.votes != null && ` (${item.votes} votes)`}
          </p>
        ))}
      </Values>
      <TrailersSection videos={data.videos} title={movie.title} />
      <Values title="Cast">
        <div className="people">
          {data.cast.map((item) => (
            <Link
              key={`${item.person_id}-${item.order}`}
              to={`/people/${item.person_id}`}
            >
              <Art path={item.profile_path} alt={`${item.name} profile`} />
              <span>
                {item.name}
                <small>{item.character}</small>
              </span>
            </Link>
          ))}
        </div>
      </Values>
      <Values title="Crew">
        <div className="people">
          {data.crew.map((item, index) => (
            <Link
              key={`${item.person_id}-${index}`}
              to={`/people/${item.person_id}`}
            >
              <Art path={item.profile_path} alt={`${item.name} profile`} />
              <span>
                {item.name}
                <small>{item.job || item.department}</small>
              </span>
            </Link>
          ))}
        </div>
      </Values>
      {["poster", "backdrop", "logo"].map((type) =>
        images(type).length ? (
          <Values
            key={type}
            title={`${type[0].toUpperCase()}${type.slice(1)} gallery`}
          >
            <div className={`image-gallery ${type}`}>
              {images(type).map((item, index) => (
                <Art
                  key={`${item.url}-${index}`}
                  path={item.url}
                  alt={`${movie.title} ${type} ${index + 1}`}
                />
              ))}
            </div>
          </Values>
        ) : null,
      )}
      <Values title="Release information">
        {data.releases.map((item, index) => (
          <p key={index}>
            {item.country}: {item.date} · {item.type}
            {item.certification && ` · ${item.certification}`}
            {item.note && ` · ${item.note}`}
          </p>
        ))}
      </Values>
      <Values title="Production companies">
        <div className="chips">
          {movie.production_companies.map((item) => (
            <span key={item.name}>{item.name}</span>
          ))}
        </div>
      </Values>
      <Values title="Keywords">
        <div className="chips">
          {data.keywords.map((item) => (
            <span key={item}>{item}</span>
          ))}
        </div>
      </Values>
      <Values title="External IDs">
        {data.external_ids
          .filter((item) => item.provider.toLowerCase() !== "tmdb")
          .map((item) => (
            <p key={item.provider}>
              <strong>{item.provider}</strong>:{" "}
              {item.url ? (
                <a
                  href={item.url}
                  target="_blank"
                  rel="nofollow noopener noreferrer"
                >
                  {item.id}
                </a>
              ) : (
                item.id
              )}
            </p>
          ))}
      </Values>
      <CommentsSection movieId={movie.id} />
      <AdSlot slot={import.meta.env.VITE_ADSENSE_SLOT_ID} eligible={data.ads?.eligible} />
    </main>
  );
}

export function Person() {
  const { id } = useParams();
  const [sort, setSort] = useState("newest");
  const [creditType, setCreditType] = useState("all");
  const [role, setRole] = useState("");
  const [data, error] = useData(
    `/api/v1/people/${id}?sort=${sort}&credit_type=${creditType}&role=${encodeURIComponent(role)}`,
  );
  const trackedPerson = useRef();
  useEffect(() => {
    if (data?.id && data.id !== trackedPerson.current) {
      trackedPerson.current = data.id;
      analytics.personView(data);
    }
  }, [data]);
  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;
  const personLd = {
    "@context": "https://schema.org",
    "@type": "Person",
    name: data.name,
    ...(data.profile_path && {
      image: imageUrl(data.profile_path, "original"),
    }),
    ...(data.department && { jobTitle: data.department }),
    ...(data.birthday && { birthDate: data.birthday }),
    ...(data.place_of_birth && { birthPlace: data.place_of_birth }),
    ...(data.imdb_url && { sameAs: [data.imdb_url] }),
  };
  const groups = data.filmography.reduce((result, item) => {
    const key =
      item.normalized_role || (item.credit_type === "cast" ? "actor" : "other");
    (result[key] ||= []).push(item);
    return result;
  }, {});
  const roleTitle = (value) =>
    ({
      actor: "Acting",
      director: "Directing",
      writer: "Writing",
      cinematography: "Cinematography",
      producer: "Production",
      editor: "Editing",
      composer: "Composer / Music",
    })[value] || value.replaceAll("_", " ");
  return (
    <main>
      <Seo
        title={data.name}
        description={`${data.name} filmography and movie credits.`}
        image={imageUrl(data.profile_path, "original")}
        noindex={!data.seo_indexable}
        jsonLd={[
          personLd,
          breadcrumbJsonLd([
            { name: "Home", path: "/" },
            { name: data.name, path: location.pathname },
          ]),
        ]}
      />
      <div className="detail">
        <Art
          className="poster profile"
          path={data.profile_path}
          alt={`${data.name} profile`}
        />
        <article>
          <h1>{data.name}</h1>
          <p>{data.department || "Film professional"}</p>
          {data.display_id != null && (
            <div className="facts">
              <span className="identifier" data-testid="person-display-id">
                <small>ID</small>
                <strong>{data.display_id}</strong>
              </span>
            </div>
          )}
          {data.birthday && (
            <p>
              Born {data.birthday}
              {data.place_of_birth && ` · ${data.place_of_birth}`}
            </p>
          )}
          {data.biography && <p>{data.biography}</p>}
          {data.imdb_url && (
            <p>
              <a href={data.imdb_url} rel="noreferrer">
                IMDb profile
              </a>
            </p>
          )}
        </article>
      </div>
      <div className="toolbar">
        <label>
          Order
          <select
            value={sort}
            onChange={(event) => setSort(event.target.value)}
          >
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
          </select>
        </label>
        <label>
          Credits
          <select
            value={creditType}
            onChange={(event) => setCreditType(event.target.value)}
          >
            <option value="all">Cast and crew</option>
            <option value="cast">Cast only</option>
            <option value="crew">Crew only</option>
          </select>
        </label>
        <label>
          Role
          <select
            value={role}
            onChange={(event) => setRole(event.target.value)}
          >
            <option value="">All roles</option>
            {data.roles.map((item) => (
              <option key={item} value={item}>
                {roleTitle(item)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <h2>Filmography</h2>
      {data.filmography.length ? (
        Object.entries(groups).map(([group, items]) => (
          <section className="filmography-group" key={group}>
            <h3 className="capitalize">{roleTitle(group)}</h3>
            <div className="grid">
              {items.map((item, index) => (
                <div key={`${item.movie.id}-${index}`}>
                  <Card movie={item.movie} />
                  <small>
                    {item.character ||
                      item.job ||
                      item.department ||
                      item.credit_type}
                  </small>
                </div>
              ))}
            </div>
          </section>
        ))
      ) : (
        <Empty>No credits match this filter.</Empty>
      )}
    </main>
  );
}

const calendarDate = (movie, type) =>
  type === "ott" ? movie.ott_release_date : movie.theatrical_release_date;

const CalendarMovie = ({ movie, type }) => {
  const releaseDate = calendarDate(movie, type);
  const language = movie.language_name || languageName(movie.language);
  const isOtt = type === "ott";
  return (
    <article className="calendar-movie">
      <Link
        className="calendar-movie-link"
        to={`/movies/${movie.id}`}
        aria-label={`${movie.title}, ${isOtt ? "OTT" : "theatrical"} release ${formatDate(releaseDate)}`}
      >
        <span className="calendar-poster">
          <img
            loading="lazy"
            decoding="async"
            src={imageUrl(movie.poster_path, "w342")}
            srcSet={
              movie.poster_path
                ? `${imageUrl(movie.poster_path, "w185")} 185w, ${imageUrl(movie.poster_path, "w342")} 342w, ${imageUrl(movie.poster_path, "w500")} 500w`
                : undefined
            }
            sizes="(max-width: 600px) 46vw, (max-width: 1024px) 30vw, 210px"
            alt={`${movie.title} poster`}
            onError={(event) => {
              event.currentTarget.onerror = null;
              event.currentTarget.src = "/placeholder.svg";
              event.currentTarget.removeAttribute("srcset");
            }}
          />
        </span>
        <span className="calendar-movie-info">
          <strong className="calendar-movie-title" title={movie.title}>
            {movie.title}
          </strong>
          <time dateTime={releaseDate}>{formatDate(releaseDate)}</time>
          <span>{language || "Language unavailable"}</span>
          <span className={`release-kind ${isOtt ? "ott" : "theatrical"}`}>
            {isOtt ? "OTT" : "Theatrical"}
          </span>
          {isOtt && movie.ott_platform && (
            <span className="calendar-platform">{movie.ott_releases?.length
              ? movie.ott_releases.map((release) => `${release.platform} (${formatDate(release.release_date)})`).join(" · ")
              : movie.ott_platform}</span>
          )}
          {movie.rating != null && (
            <span className="calendar-rating">
              IMDb ★ {Number(movie.rating).toFixed(1)}
            </span>
          )}
        </span>
      </Link>
    </article>
  );
};

const CalendarLoading = () => (
  <main className="calendar-page" aria-busy="true" aria-live="polite">
    <div className="calendar-heading-skeleton skeleton" />
    <div className="calendar-controls-skeleton skeleton" />
    <span className="sr-only">Loading release calendar…</span>
    <section className="calendar-date-group calendar-skeleton-group">
      <div className="calendar-date-skeleton skeleton" />
      <div className="calendar-grid">
        {Array.from({ length: 6 }, (_, index) => (
          <div className="calendar-card-skeleton" key={index}>
            <div className="calendar-poster-skeleton skeleton" />
            <div className="calendar-line-skeleton skeleton" />
            <div className="calendar-line-skeleton short skeleton" />
          </div>
        ))}
      </div>
    </section>
  </main>
);

const monthValue = (value, offset) => {
  const current = new Date(`${value.slice(0, 7)}-01T00:00:00Z`);
  current.setUTCMonth(current.getUTCMonth() + offset);
  return current.toISOString().slice(0, 7);
};

const monthLabel = (value) =>
  new Intl.DateTimeFormat(undefined, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value.slice(0, 7)}-01T00:00:00Z`));

export function Calendar() {
  const { period } = useParams();
  const [calendarParams, setCalendarParams] = useSearchParams();
  const tab = calendarParams.get("tab") === "ott" ? "ott" : "theatrical";
  const selectedLanguage = calendarParams.get("language") || "all";
  const requestedMonth = calendarParams.get("month") || "";
  const focusToday = calendarParams.get("focus") === "today";
  const [todayRequest, setTodayRequest] = useState(0);
  const calendarApiParams = new URLSearchParams();
  if (requestedMonth) calendarApiParams.set("month", requestedMonth);
  if (selectedLanguage !== "all") calendarApiParams.set("language", selectedLanguage);
  const endpoint = `/api/v1/calendar/${requestedMonth ? "this-month" : period}${
    calendarApiParams.size ? `?${calendarApiParams}` : ""
  }`;
  const [data, error, retry] = useData(endpoint);
  useEffect(() => {
    if (!data?.today || !focusToday) return;
    const section = document.getElementById(`release-date-${data.today}`);
    if (!section) return;
    const reducedMotion =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    section.scrollIntoView?.({
      behavior: reducedMotion ? "auto" : "smooth",
      block: "start",
    });
    section.focus?.({ preventScroll: true });
  }, [data, focusToday, selectedLanguage, tab, todayRequest]);
  if (error)
    return (
      <main className="calendar-page">
        <Seo title="Release calendar" />
        <h1>Release calendar</h1>
        <div className="calendar-error" role="alert">
          <h2>Release information could not be loaded</h2>
          <p>Please try again.</p>
          <button type="button" onClick={retry}>Retry</button>
        </div>
      </main>
    );
  if (!data) return <CalendarLoading />;
  const periods = [
    "previous-week",
    "this-week",
    "next-week",
    "this-month",
  ];
  const periodLabel = requestedMonth
    ? monthLabel(data.start_date)
    : period.replaceAll("-", " ");
  const selected = data[tab];
  const items = selected.items;
  const groups = items.reduce((result, movie) => {
    const value = calendarDate(movie, tab);
    if (!result.has(value)) result.set(value, []);
    result.get(value).push(movie);
    return result;
  }, new Map());
  const todayIsInRange =
    data.today && data.start_date <= data.today && data.today <= data.end_date;
  if (todayIsInRange && !groups.has(data.today)) groups.set(data.today, []);
  const groupEntries = Array.from(groups.entries()).sort(([left], [right]) =>
    left.localeCompare(right),
  );
  const rangeMonth = data.start_date.slice(0, 7);
  const calendarHref = (targetPeriod, changes = {}) => {
    const next = new URLSearchParams(calendarParams);
    Object.entries(changes).forEach(([key, value]) => {
      if (value) next.set(key, value);
      else next.delete(key);
    });
    const query = next.toString();
    return `/calendar/${targetPeriod}${query ? `?${query}` : ""}`;
  };
  const seoTitle =
    tab === "ott"
      ? `OTT Releases ${periodLabel}`
      : `Movies Releasing ${periodLabel}`;
  return (
    <main className="calendar-page">
      <Seo title={seoTitle} description={`Browse ${periodLabel} movie and OTT releases worldwide by date and language.`} />
      <header className="calendar-hero">
        <p className="eyebrow">Movie release calendar</p>
        <h1 className="capitalize">{periodLabel} releases</h1>
        <p>Browse movie releases by date, language, and release type.</p>
      </header>
      <nav className="calendar-month-nav" aria-label="Month navigation">
        <Link
          to={calendarHref("this-month", {
            month: monthValue(rangeMonth, -1),
            focus: null,
          })}
          aria-label={`Previous month, ${monthLabel(monthValue(rangeMonth, -1))}`}
        >
          <span aria-hidden="true">←</span> Previous month
        </Link>
        <strong>{monthLabel(rangeMonth)}</strong>
        <Link
          to={calendarHref("this-month", {
            month: monthValue(rangeMonth, 1),
            focus: null,
          })}
          aria-label={`Next month, ${monthLabel(monthValue(rangeMonth, 1))}`}
        >
          Next month <span aria-hidden="true">→</span>
        </Link>
        <Link
          className="calendar-today"
          to={calendarHref("this-month", {
            month: null,
            focus: "today",
            tab,
          })}
          aria-label={`Today, ${formatDate(data.today)}`}
          onClick={() => setTodayRequest((value) => value + 1)}
        >
          Today
        </Link>
      </nav>
      <nav className="chips period-nav" aria-label="Calendar period">
        {periods.map((item) => (
          <Link
            className={item === period ? "active" : ""}
            key={item}
            to={calendarHref(item, { month: null, focus: null })}
          >
            {item.replaceAll("-", " ")}
          </Link>
        ))}
      </nav>
      <div className="calendar-toolbar">
        <nav className="calendar-tabs" aria-label="Release type">
        <Link
          className={tab === "theatrical" ? "active" : ""}
          to={calendarHref(requestedMonth ? "this-month" : period, { tab: "theatrical" })}
        >
          Theatrical Releases
        </Link>
        <Link
          className={tab === "ott" ? "active" : ""}
          to={calendarHref(requestedMonth ? "this-month" : period, { tab: "ott" })}
        >
          OTT Releases
        </Link>
        </nav>
      </div>
      <LanguageTabs
        value={selectedLanguage}
        languages={data.languages}
        onChange={(value) => {
          const next = new URLSearchParams(calendarParams);
          if (value !== "all") next.set("language", value);
          else next.delete("language");
          setCalendarParams(next, { replace: true });
        }}
        label={`Filter ${tab === "ott" ? "OTT" : "theatrical"} releases by language`}
        className="calendar-language-filter"
      />
      <section className="calendar-content" aria-live="polite">
        {groupEntries.length ? (
          groupEntries.map(([releaseDate, movies]) => {
            const dateId = `release-date-${releaseDate}`;
            const date = new Date(`${releaseDate}T00:00:00Z`);
            const weekday = new Intl.DateTimeFormat(undefined, {
              weekday: "long", timeZone: "UTC",
            }).format(date);
            const isToday = releaseDate === data.today;
            const timing = releaseDate > data.today
              ? "Upcoming"
              : isToday
                ? "Releasing today"
                : "Released";
            return (
              <section
                className={`calendar-date-group${isToday ? " today" : ""}`}
                aria-labelledby={`${dateId}-heading`}
                aria-label={isToday ? `Today, ${formatDate(releaseDate)}` : undefined}
                id={dateId}
                key={releaseDate}
                tabIndex={isToday ? -1 : undefined}
                ref={isToday && focusToday ? (node) => node?.focus({ preventScroll: true }) : undefined}
              >
                <header className="calendar-date-heading">
                  <div>
                    <span>{isToday ? "Today" : timing} · {weekday}</span>
                    <h2 id={`${dateId}-heading`}>
                      <time dateTime={releaseDate}>{formatDate(releaseDate)}</time>
                    </h2>
                  </div>
                  <small>{movies.length} {movies.length === 1 ? "release" : "releases"}</small>
                </header>
                {movies.length ? (
                  <div className="calendar-grid">
                    {movies.map((movie, index) => (
                      <CalendarMovie
                        key={`${movie.id}-${movie.ott_platform || "theatrical"}-${index}`}
                        movie={movie}
                        type={tab}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="calendar-today-empty">
                    <strong>No releases today.</strong>
                    <span>
                      No {tab === "ott" ? "confirmed OTT" : "theatrical"} releases match the selected filters.
                    </span>
                  </div>
                )}
              </section>
            );
          })
        ) : (
          <div className="calendar-empty">
            <h2>No releases found for this period</h2>
            <p>Try another month, release type, or language.</p>
          </div>
        )}
      </section>
    </main>
  );
}

const maintenanceTime = (value, timeZone, zoneLabel) => {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return `${new Intl.DateTimeFormat("en-IN", {
    day: "numeric", month: "long", year: "numeric",
    hour: "numeric", minute: "2-digit", timeZone,
  }).format(parsed)} ${zoneLabel}`;
};

const maintenanceServices = ["Movies", "Movie Search", "OTT Calendar", "Images", "API"];

function MaintenanceIcon({ type = "maintenance" }) {
  const paths = {
    Movies: <><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 5v14M3 9h4M3 15h4M17 5v14M17 9h4M17 15h4"/></>,
    "Movie Search": <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/></>,
    "OTT Calendar": <><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/></>,
    Images: <><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="9" r="1.5"/><path d="m4 17 5-5 3.5 3.5 2-2L20 19"/></>,
    API: <><path d="M8 9 4 12l4 3M16 9l4 3-4 3M14 5l-4 14"/></>,
    maintenance: <><path d="M14.7 6.3a4 4 0 0 0-5-5L12 3.6 9.6 6 7.3 3.7a4 4 0 0 0 5 5L5 16a2.1 2.1 0 1 0 3 3l7.2-7.2a4 4 0 0 0 5-5L18 9l-2.4-2.4 2.3-2.3a4 4 0 0 0-3.2 2Z"/></>,
  };
  return <svg className="maintenance-icon" viewBox="0 0 24 24" aria-hidden="true">{paths[type] || paths.maintenance}</svg>;
}

const liveMaintenanceStatus = (item, now) => {
  if (["COMPLETED", "CANCELLED"].includes(item.status)) return item.status;
  const start = new Date(item.start_time).getTime();
  const end = new Date(item.end_time).getTime();
  if (item.status === "ACTIVE" || now >= start) return "ACTIVE";
  return "SCHEDULED";
};

const maintenanceCountdown = (target, now) => {
  const remaining = Math.max(0, new Date(target).getTime() - now);
  const minutes = Math.floor(remaining / 60000);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  return [days && `${String(days).padStart(2, "0")}d`, (days || hours) && `${String(hours).padStart(2, "0")}h`, `${String(mins).padStart(2, "0")}m`].filter(Boolean).join(" ");
};

const maintenanceDuration = (item) => {
  const minutes = Math.max(0, Math.round((new Date(item.end_time) - new Date(item.start_time)) / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60), remainder = minutes % 60;
  return `${hours}h${remainder ? ` ${remainder}m` : ""}`;
};

function MaintenanceTimeline({ status }) {
  const steps = status === "COMPLETED" ? ["Scheduled", "Start", "In progress", "Completed"] : ["Scheduled", "Start", "In progress", "Expected end"];
  const activeIndex = status === "COMPLETED" ? 3 : status === "ACTIVE" ? 2 : 0;
  return <ol className="maintenance-timeline" aria-label="Maintenance timeline">
    {steps.map((step, index) => <li className={index < activeIndex ? "passed" : index === activeIndex ? "current" : ""} key={step}><span aria-hidden="true" />{step}</li>)}
  </ol>;
}

function MaintenanceWindowCard({ item, compact = false, now }) {
  const status = liveMaintenanceStatus(item, now);
  const start = new Date(item.start_time).getTime(), end = new Date(item.end_time).getTime();
  const progress = status === "ACTIVE" ? Math.max(0, Math.min(100, Math.round(((now - start) / (end - start)) * 100))) : 0;
  return <article className={`maintenance-window-card status-card-${status.toLowerCase()}${compact ? " compact" : ""}`}>
    <header><div className="maintenance-title"><span className="maintenance-icon-shell"><MaintenanceIcon /></span><h3>{item.title}</h3></div><strong className={`maintenance-status status-${status.toLowerCase()}`}>{status}</strong></header>
    {!compact && status === "SCHEDULED" && <div className="maintenance-countdown" aria-live="polite"><span>Maintenance begins in</span><strong>{maintenanceCountdown(item.start_time, now)}</strong></div>}
    {!compact && status === "ACTIVE" && <div className="maintenance-progress"><div><span>Estimated maintenance progress</span><strong>{progress}%</strong></div><div className="maintenance-progress-track" role="progressbar" aria-label="Estimated maintenance progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow={progress}><span style={{ transform: `scaleX(${progress / 100})` }} /></div></div>}
    <dl className="maintenance-facts">
      <div><dt>Starts</dt><dd>{maintenanceTime(item.start_time, "Asia/Kolkata", "IST")}<small>{maintenanceTime(item.start_time, "Asia/Singapore", "SGT")}</small></dd></div>
      <div><dt>Ends</dt><dd>{maintenanceTime(item.end_time, "Asia/Kolkata", "IST")}<small>{maintenanceTime(item.end_time, "Asia/Singapore", "SGT")}</small></dd></div>
      {compact && <><div><dt>Duration</dt><dd>{maintenanceDuration(item)}</dd></div><div><dt>Impact</dt><dd>{item.impact_level}</dd></div></>}
      {!compact && <div><dt>Expected impact</dt><dd><strong>{item.impact_level}</strong> · {item.impact_message}</dd></div>}
    </dl>
    {item.affected_services?.length > 0 && <div className="maintenance-service-chips" aria-label="Affected services">{item.affected_services.map((service) => <span key={service}><MaintenanceIcon type={service} />{service}</span>)}</div>}
    {!compact && !item.affected_services?.length && <p className="maintenance-unaffected">No service interruption expected</p>}
    {!compact && <p>{item.description}</p>}
    {status !== "CANCELLED" && <MaintenanceTimeline status={status} />}
    {status === "ACTIVE" && now >= end && <p>Expected end time has passed. Awaiting administrator confirmation of completion.</p>}
    <small>Last updated {maintenanceTime(item.updated_at, "Asia/Kolkata", "IST")}</small>
  </article>;
}

function MaintenancePanel() {
  const [data, error, reload] = useData("/api/v1/maintenance");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  if (error) return <div className="request-error" role="alert"><p>{error}</p><button type="button" onClick={reload}>Retry</button></div>;
  if (!data) return <p className="maintenance-loading" role="status">Loading maintenance status…</p>;
  const scheduled = [...(data.active || []), ...(data.upcoming || [])];
  const active = scheduled.filter((item) => liveMaintenanceStatus(item, now) === "ACTIVE");
  const upcoming = scheduled.filter((item) => liveMaintenanceStatus(item, now) === "SCHEDULED");
  const history = data.history || [];
  const affected = new Set(active.flatMap((item) => item.affected_services || []));
  const visibleServices = [...new Set([...maintenanceServices, ...affected])];
  return <section className="maintenance-panel" role="tabpanel" id="maintenance-panel" aria-labelledby="maintenance-tab">
    <div className={`maintenance-overall ${active.length ? "active" : upcoming.length ? "scheduled" : "operational"}`} role="status">
      <div className="maintenance-hero-copy"><span className="maintenance-hero-icon"><MaintenanceIcon /></span><div><h2><span className="maintenance-status-dot" aria-hidden="true" />{active.length ? "Maintenance in progress" : upcoming.length ? "Scheduled maintenance" : "All systems operational"}</h2>
      <p>{active.length ? "Some services may be temporarily interrupted." : upcoming.length ? "An upcoming maintenance window has been scheduled." : "There is no scheduled maintenance at this time."}</p></div></div>
      <div className="maintenance-signal" aria-hidden="true"><span/><span/><span/><span/><span/></div>
    </div>
    <section className="maintenance-service-board" aria-labelledby="service-status-heading"><div><span className="maintenance-eyebrow">Live status</span><h2 id="service-status-heading">Service status</h2></div><div className="maintenance-service-grid">{visibleServices.map((service) => { const isAffected = affected.has(service); return <article className={isAffected ? "affected" : "operational"} key={service}><MaintenanceIcon type={service}/><span>{service}</span><strong><i aria-hidden="true" />{isAffected ? "Affected" : "Operational"}</strong></article>; })}</div></section>
    {active.length > 0 && <section aria-labelledby="active-maintenance-heading"><span className="maintenance-eyebrow">Current incident</span><h2 id="active-maintenance-heading">Active maintenance</h2>{active.map((item) => <MaintenanceWindowCard key={item.id} item={item} now={now} />)}</section>}
    {upcoming.length > 0 && <section aria-labelledby="upcoming-maintenance-heading"><span className="maintenance-eyebrow">Plan ahead</span><h2 id="upcoming-maintenance-heading">Upcoming maintenance</h2>{upcoming.map((item) => <MaintenanceWindowCard key={item.id} item={item} now={now} />)}</section>}
    <section aria-labelledby="maintenance-history-heading"><span className="maintenance-eyebrow">Recent activity</span><h2 id="maintenance-history-heading">Maintenance history</h2>{history.length ? <div className="maintenance-history">{history.map((item) => <MaintenanceWindowCard key={item.id} item={item} compact now={now} />)}</div> : <p className="empty">No recent maintenance history.</p>}</section>
  </section>;
}

export function Request() {
  const [params, setParams] = useSearchParams();
  const [languages] = useData("/api/v1/languages");
  const [tab, setTab] = useState(params.get("tab") === "issue" || params.get("tab") === "contact" ? "issue" : params.get("tab") === "access" ? "access" : params.get("tab") === "maintenance" ? "maintenance" : "movie");
  const [contactType, setContactType] = useState(params.get("type") || "WEBSITE_ISSUE");
  const [movieId, setMovieId] = useState(params.get("movie_external_id") || "");
  const [requestLanguage, setRequestLanguage] = useState(params.get("language") || "");
  const [result, setResult] = useState();
  const [submitting, setSubmitting] = useState(false);
  const [screenshot, setScreenshot] = useState();
  const [screenshotError, setScreenshotError] = useState("");
  const submit = (event) => {
    event.preventDefault();
    setSubmitting(true);
    setResult(undefined);
    const body = Object.fromEntries(new FormData(event.target));
    delete body.screenshot;
    Object.keys(body).forEach((key) => body[key] === "" && delete body[key]);
    if (body.release_year) body.release_year = Number(body.release_year);
    if (body.movie_external_id) body.movie_external_id = Number(body.movie_external_id);
    if (body.tmdb_id) body.tmdb_id = Number(body.tmdb_id);
    if (tab === "movie") {
      analytics.movieRequest("movie_request_started", body.movie_external_id);
      post("/api/v1/movie-requests", body)
        .then((value) => {
          setResult(value);
          if (value?.request_id) analytics.movieRequest("movie_request_submitted", body.movie_external_id);
        })
        .catch((error) => setResult({ error: error.message, ...(error.data || {}) }))
        .finally(() => setSubmitting(false));
    } else {
      const requestBody = screenshot ? new FormData() : body;
      if (screenshot) {
        Object.entries(body).forEach(([key, value]) => requestBody.append(key, String(value)));
        requestBody.append("screenshot", screenshot, screenshot.name);
      }
      post("/api/v1/contact-requests", requestBody)
        .then((value) => { setResult(value); setScreenshot(undefined); setScreenshotError(""); })
        .catch((error) => setResult({ error: error.message, ...(error.data || {}) }))
        .finally(() => setSubmitting(false));
    }
  };
  const received = Boolean(result?.request_id);
  const movieFields = ["INCORRECT_MOVIE", "INCORRECT_OTT"].includes(contactType);
  const changeTab = (nextTab) => {
    const next = new URLSearchParams(params);
    if (nextTab === "movie") next.delete("tab");
    else next.set("tab", nextTab);
    setParams(next);
    setTab(nextTab);
    setResult(undefined);
    setScreenshot(undefined);
    setScreenshotError("");
  };
  return (
    <main className="request-page">
      <Seo title="Request a movie" description="Request a missing movie or report incorrect movie and OTT availability information to OTT Tracker." />
      <h1>Request a movie</h1>
      <p>Ask us to review any movie, including one that is already listed here, or report an issue and request access. Your contact details are used only to process this request and are never shown publicly.{tab === "movie" && <> Can't find the movie? You can also search for movie and OTT information using <Link className="request-deep-search-link" to="/deep-search">Deep Search</Link>.</>}</p>
      {tab === "movie" && <Link className="request-deep-search-action" to="/deep-search">Try Deep Search</Link>}
      <div className="request-tabs" role="tablist" aria-label="Request type">
        <button type="button" role="tab" aria-selected={tab === "movie"} className={tab === "movie" ? "active" : ""} onClick={() => changeTab("movie")}>Request movie</button>
        <button type="button" role="tab" aria-selected={tab === "issue"} className={tab === "issue" ? "active" : ""} onClick={() => changeTab("issue")}>Report issue</button>
        <button type="button" role="tab" aria-selected={tab === "access"} className={tab === "access" ? "active" : ""} onClick={() => changeTab("access")}>Request access</button>
        <button id="maintenance-tab" type="button" role="tab" aria-controls="maintenance-panel" aria-selected={tab === "maintenance"} className={tab === "maintenance" ? "active" : ""} onClick={() => changeTab("maintenance")}>Maintenance Window</button>
      </div>
      {tab === "maintenance" && <MaintenancePanel />}
      {!received && tab === "movie" && <form className="request" onSubmit={submit}>
        <div className="hp-field" aria-hidden="true"><label>Website<input name="website" type="text" tabIndex={-1} autoComplete="off" /></label></div>
        <label>Movie Name *<input name="movie_name" required maxLength="500" defaultValue={params.get("movie_name") || ""} /></label>
        <label>Email *<input name="email" type="email" required maxLength="320" autoComplete="email" /></label>
        <label>ID (optional)<input name="movie_external_id" type="number" min="1" max="2147483647" step="1" inputMode="numeric" value={movieId} onChange={(event) => setMovieId(event.target.value)} /></label>
        <label>Year<input name="release_year" type="number" min="1888" max="2100" defaultValue={params.get("release_year") || ""} /></label>
        <label>Language<select name="language" value={requestLanguage} onChange={(event) => setRequestLanguage(event.target.value)}><option value="">Not specified</option>{(languages || []).map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}</select></label>
        <label>WhatsApp / Phone<input name="whatsapp_phone" type="tel" maxLength="50" autoComplete="tel" /></label>
        <label>Comments<textarea name="details" maxLength="2000" placeholder="Any details that help identify it" /></label>
        <button disabled={submitting}>{submitting ? "Verifying movie…" : "Submit request"}</button>
      </form>}
      {!received && tab === "issue" && <form className="request request-card" onSubmit={submit}>
        <div className="hp-field" aria-hidden="true"><label>Website<input name="website" type="text" tabIndex={-1} autoComplete="off" /></label></div>
        <div className="request-intro"><h2>Report an issue</h2><p>Report a website issue, incorrect movie information or incorrect OTT information. Submissions go to private administrator review and are never applied automatically.</p></div>
        <label>Request type *<select name="request_type" required value={contactType} onChange={(event) => setContactType(event.target.value)}>
          <option value="WEBSITE_ISSUE">Report website issue</option><option value="INCORRECT_MOVIE">Report incorrect movie information</option><option value="INCORRECT_OTT">Report incorrect OTT information</option><option value="OTHER">Other</option>
        </select></label>
        <div className="request-field-grid">
          <label>Name<input name="name" maxLength="200" autoComplete="name" /></label>
          <label>WhatsApp Number (Preferred)<input name="whatsapp" type="tel" maxLength="50" autoComplete="tel" /></label>
          <label>Phone Number<input name="phone" type="tel" maxLength="50" autoComplete="tel" /></label>
          <label>Email Address<input name="email" type="email" maxLength="320" autoComplete="email" /></label>
          {movieFields && <><label>Movie Name<input name="movie_name" maxLength="500" defaultValue={params.get("movie_name") || ""} /></label><label>ID<input name="tmdb_id" type="number" min="1" inputMode="numeric" defaultValue={params.get("tmdb_id") || ""} /></label><label className="wide">Movie URL<input name="movie_url" type="url" pattern="https?://.*" defaultValue={params.get("movie_url") || ""} /></label><label className="wide">Issue Type<input name="issue_type" maxLength="100" /></label></>}
          {contactType === "INCORRECT_OTT" && <><label>Expected OTT Platform<input name="expected_ott_platform" maxLength="100" /></label><label>Expected OTT Release Date<input name="expected_ott_release_date" type="date" /></label><label className="wide">Evidence URL<input name="evidence_url" type="url" pattern="https?://.*" /></label></>}
          <label className="wide">Comment / Description *<textarea name="comment" required minLength="5" maxLength="5000" /></label>
          <ScreenshotUpload file={screenshot} error={screenshotError} onFile={setScreenshot} onError={setScreenshotError} />
        </div>
        <p className="form-help">Provide at least one contact method: WhatsApp, phone, or email.</p>
        <button disabled={submitting || Boolean(screenshotError)}>{submitting ? "Sending securely…" : "Send to administrator"}</button>
      </form>}
      {!received && tab === "access" && <form className="request request-card" onSubmit={submit}>
        <div className="hp-field" aria-hidden="true"><label>Website<input name="website" type="text" tabIndex={-1} autoComplete="off" /></label></div>
        <div className="request-intro"><h2>Request access</h2><p>Request elevated website access. Access is reviewed by an administrator and is never granted automatically.</p></div>
        <input type="hidden" name="request_type" value="ACCESS_REQUEST" />
        <div className="request-field-grid">
          <label>Name<input name="name" maxLength="200" autoComplete="name" /></label>
          <label>WhatsApp Number (Preferred)<input name="whatsapp" type="tel" maxLength="50" autoComplete="tel" /></label>
          <label>Phone Number<input name="phone" type="tel" maxLength="50" autoComplete="tel" /></label>
          <label>Email Address<input name="email" type="email" maxLength="320" autoComplete="email" /></label>
          <label className="wide">Reason for access *<textarea name="comment" required minLength="5" maxLength="5000" placeholder="Tell us why you need access" /></label>
        </div>
        <p className="form-help">Provide at least one contact method: WhatsApp, phone, or email. We will respond if your request is approved.</p>
        <button disabled={submitting}>{submitting ? "Sending securely…" : "Submit access request"}</button>
      </form>}
      {!received && tab === "movie" && result?.candidates?.length > 0 && <section className="request-candidates" aria-labelledby="movie-match-heading"><h2 id="movie-match-heading">Choose the correct movie</h2><p>We found more than one possible match.</p>{result.candidates.map((candidate) => <button type="button" key={candidate.id} onClick={() => { setMovieId(String(candidate.id)); setResult(undefined); }}><span>{candidate.poster_path && <Art path={candidate.poster_path} alt="" />}</span><strong>{candidate.title}</strong><small>{candidate.release_date?.slice(0, 4) || "Year unknown"} · {candidate.original_language_name || candidate.original_language || "Language unknown"} · ID {candidate.id}</small></button>)}</section>}
      {received && (
        <section className="request-success" role="status">
          {tab === "movie" && result.poster_path && <Art className="request-poster" path={result.poster_path} alt={`${result.verified_title} poster`} />}
          <div>
            <h2>{tab === "movie" ? "Request received" : "Submission received"}</h2>
            {tab === "movie" && <><h3>{result.verified_title}</h3>{result.original_title && result.original_title !== result.verified_title && <p>{result.original_title}</p>}<p>We aim to review your request within 48 hours. We’ll email you when its status changes.</p>{result.confirmation_email_status === "SENT" ? <p>Confirmation email sent.</p> : <p>Your request was received, but we could not send the confirmation email.</p>}</>}
            {tab !== "movie" && <p>An administrator can now review reference {result.request_id}. No access or correction is applied automatically.</p>}
            {tab !== "movie" && (result.receipt_email_status === "SENT" ? <p>Confirmation email sent.</p> : result.receipt_email_status === "NOT_SUPPLIED" ? null : <p>Your submission was saved; email delivery is pending or not currently available.</p>)}
            <small>Request reference: {result.request_id}</small>
          </div>
        </section>
      )}
      {result?.error && !result?.candidates?.length && (
        <div className="request-error" role="alert">
          <p>{result.error}</p>
          {result.local_movie_id && <Link className="button-link" to={`/movies/${result.local_movie_id}`}>View Movie</Link>}
        </div>
      )}
    </main>
  );
}

const SCREENSHOT_TYPES = ["image/png", "image/jpeg", "image/webp"];
const MAX_SCREENSHOT_SIZE = 5 * 1024 * 1024;

function ScreenshotUpload({ file, error, onFile, onError }) {
  const inputRef = useRef();
  const [dragging, setDragging] = useState(false);
  const [preview, setPreview] = useState("");

  useEffect(() => {
    if (!file || typeof URL.createObjectURL !== "function") {
      setPreview("");
      return undefined;
    }
    const nextPreview = URL.createObjectURL(file);
    setPreview(nextPreview);
    return () => URL.revokeObjectURL(nextPreview);
  }, [file]);

  const select = (nextFile) => {
    if (!nextFile) return;
    if (!SCREENSHOT_TYPES.includes(nextFile.type)) {
      onFile(undefined);
      onError("Please upload a PNG, JPG, or WEBP image.");
      return;
    }
    if (nextFile.size > MAX_SCREENSHOT_SIZE) {
      onFile(undefined);
      onError("Screenshot must be 5 MB or smaller.");
      return;
    }
    onError("");
    onFile(nextFile);
  };
  const remove = () => {
    onFile(undefined);
    onError("");
    if (inputRef.current) inputRef.current.value = "";
  };
  return (
    <div className="screenshot-field wide">
      <div className="screenshot-label"><strong>Attach screenshot</strong> <span>(optional)</span></div>
      {!file ? (
        <label
          className={`screenshot-dropzone${dragging ? " dragging" : ""}`}
          onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
          onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
          onDragLeave={(event) => { event.preventDefault(); setDragging(false); }}
          onDrop={(event) => { event.preventDefault(); setDragging(false); select(event.dataTransfer.files?.[0]); }}
        >
          <input ref={inputRef} type="file" name="screenshot" accept="image/png,image/jpeg,image/webp" aria-describedby="screenshot-help screenshot-error" onClick={(event) => { event.currentTarget.value = ""; }} onChange={(event) => select(event.target.files?.[0])} />
          <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4" /></svg>
          <span>Drag &amp; drop screenshot here</span>
          <span className="screenshot-or">or</span>
          <strong className="screenshot-choose">Choose image</strong>
          <small id="screenshot-help">PNG, JPG or WEBP · Max 5 MB</small>
        </label>
      ) : (
        <div className="screenshot-preview">
          {preview ? <img src={preview} alt="Selected screenshot preview" /> : <div className="screenshot-preview-placeholder" aria-hidden="true" />}
          <div><strong>{file.name}</strong><small>{(file.size / (file.size < 1024 * 1024 ? 1024 : 1024 * 1024)).toFixed(file.size < 1024 * 1024 ? 0 : 1)} {file.size < 1024 * 1024 ? "KB" : "MB"}</small></div>
          <div className="screenshot-actions"><label><input ref={inputRef} type="file" accept="image/png,image/jpeg,image/webp" aria-label="Replace screenshot" onClick={(event) => { event.currentTarget.value = ""; }} onChange={(event) => select(event.target.files?.[0])} /><span>Replace</span></label><button type="button" onClick={remove}>Remove</button></div>
        </div>
      )}
      <p id="screenshot-error" className="screenshot-error" role={error ? "alert" : undefined}>{error}</p>
    </div>
  );
}

export function Support() {
  return <Request />;
}

export function ContactPage() {
  const contact = import.meta.env.VITE_SITE_CONTACT_EMAIL || "otttrackerin@gmail.com";
  const [result, setResult] = useState();
  const [submitting, setSubmitting] = useState(false);
  const [requestType, setRequestType] = useState("OTHER");
  const submit = (event) => {
    event.preventDefault();
    setSubmitting(true);
    setResult(undefined);
    const form = event.currentTarget;
    const body = Object.fromEntries(new FormData(form));
    Object.keys(body).forEach((key) => body[key] === "" && delete body[key]);
    post("/api/v1/contact-requests", body)
      .then((value) => {
        setResult(value);
        form.reset();
        setRequestType("OTHER");
      })
      .catch((error) => setResult({ error: error.message, ...(error.data || {}) }))
      .finally(() => setSubmitting(false));
  };
  const received = Boolean(result?.request_id);
  return (
    <main className="legal contact-page">
      <Seo title="Contact Us" description="Contact OTT Tracker about movie data, OTT availability, corrections, copyright enquiries or website feedback." />
      <h1>Contact Us</h1>
      <section className="contact-email-block">
        <h2>Email us directly</h2>
        <p className="contact-email">
          <a href={`mailto:${contact}`}>{contact}</a>
        </p>
        <p>
          Use this address for general enquiries, movie correction requests,
          OTT information corrections, content/data issues, copyright/contact
          enquiries, and website feedback. We aim to reply within 48 hours.
        </p>
      </section>
      <section className="contact-request-form">
        <h2>Send a message</h2>
        <p>
          Prefer a form? Submit your enquiry below. It is reviewed privately
          by an administrator. For movie requests, use the{" "}
          <Link to="/request-movie">Request a movie</Link> form instead.
        </p>
        {received ? (
          <section className="request-success" role="status">
            <div>
              <h3>Message received</h3>
              <p>
                {result.receipt_email_status === "SENT"
                  ? "A confirmation email is on its way to you."
                  : result.receipt_email_status === "NOT_SUPPLIED"
                    ? "Thank you — no confirmation email was sent because none was supplied."
                    : "Your message was received. We will reply as soon as possible."}
              </p>
              <small>Request reference: {result.request_id}</small>
              <button className="link-button" type="button" onClick={() => setResult(undefined)}>
                Send another message
              </button>
            </div>
          </section>
        ) : (
          <form className="request request-card" onSubmit={submit}>
            <div className="hp-field" aria-hidden="true"><label>Website<input name="website" type="text" tabIndex={-1} autoComplete="off" /></label></div>
            <label>Subject / request type *<select name="request_type" required value={requestType} onChange={(event) => setRequestType(event.target.value)}>
              <option value="WEBSITE_ISSUE">Website issue / feedback</option>
              <option value="INCORRECT_MOVIE">Incorrect movie information</option>
              <option value="INCORRECT_OTT">Incorrect OTT information</option>
              <option value="ACCESS_REQUEST">Request access</option>
              <option value="OTHER">Other enquiry</option>
            </select></label>
            <label>Name<input name="name" maxLength="200" autoComplete="name" /></label>
            <label>Email *<input name="email" type="email" required maxLength="320" autoComplete="email" /><small>So we can reply to you.</small></label>
            <label>Subject<input name="issue_type" maxLength="100" placeholder="A short subject line" /></label>
            {requestType === "INCORRECT_OTT" && <><label>Movie / Title<input name="movie_name" maxLength="500" /></label><label>Expected OTT platform<input name="expected_ott_platform" maxLength="100" placeholder="e.g. Netflix, Prime Video" /></label><label>Evidence URL<input name="evidence_url" type="url" pattern="https?://.*" /></label></>}
            {requestType === "INCORRECT_MOVIE" && <><label>Movie / Title<input name="movie_name" maxLength="500" /></label><label>Movie URL<input name="movie_url" type="url" pattern="https?://.*" /></label></>}
            <label className="wide">Message *<textarea name="comment" required minLength="5" maxLength="5000" rows="6" /></label>
            <button disabled={submitting}>{submitting ? "Sending…" : "Send message"}</button>
          </form>
        )}
        {result?.error && !result?.candidates?.length && (
          <div className="request-error" role="alert"><p>{result.error}</p></div>
        )}
      </section>
      <p>Do not send passwords, payment details or other sensitive information.</p>
    </main>
  );
}

const TRUST_CONTENT = {
  methodology: { title: "How OTTTracker verifies releases", description: "OTTTracker's methodology for movie metadata, theatrical dates, OTT platforms, streaming dates and data freshness.", sections: [
    ["What we track", "OTTTracker tracks movie metadata, theatrical releases, streaming platforms, OTT release dates and regional availability. It does not host movies."],
    ["Movie metadata", "Titles, artwork, cast, crew, trailers and production details may originate with configured metadata providers. These descriptive fields do not by themselves confirm streaming availability."],
    ["Theatrical and OTT releases", "Theatrical dates and digital or OTT dates are stored separately. Regional releases can differ, so the region shown with a streaming record matters."],
    ["Verification process", "Platform identification, date evidence and current availability are evaluated independently. We compare configured sources, retain supporting timestamps and recheck records as information changes."],
    ["Verification states", "Confirmed means reliable supporting evidence is available. Platform confirmed — date unknown means the service is identified but the release date is not sufficiently reliable. Verification in progress means information has not met confirmation requirements. No verified information means there is not enough evidence to publish a platform or date as confirmed."],
    ["Conflicting information", "When credible sources disagree, OTTTracker preserves uncertainty rather than selecting an unsupported date. A record can remain under review until evidence is reconciled."],
    ["Freshness and update frequency", "Where timestamps are available, pages show when OTT data was last checked or verified. Important and upcoming releases may be checked more often, but no fixed interval is guaranteed."],
    ["Corrections, limitations and regions", "Streaming catalogues change, provider links can expire and coverage varies by country. Readers can report errors through the corrections form; reports are reviewed against current evidence."],
  ]},
  "editorial-policy": { title: "Editorial Policy", description: "How OTTTracker handles accuracy, sources, updates, automation, corrections and editorial review.", sections: [
    ["Accuracy and source checking", "We distinguish sourced facts from rumours and do not treat third-party metadata as automatic confirmation of an OTT date."],
    ["Editorial review", "Only updates marked published after the review gate are public. Drafts and ready-for-review material are not exposed on public article routes."],
    ["Automation and AI assistance", "Automation may help collect structured data or prepare drafts. Publication requires an explicit editorial review state; generated material is not automatically published."],
    ["Updates and corrections", "Articles show publication and update dates. Material factual changes should be corrected promptly and, where useful, explained in the updated copy."],
    ["Independence", "Editorial judgements should not be altered by platform relationships. Sponsored content, if introduced, will be clearly labelled and kept distinct from verification decisions."],
  ]},
  sources: { title: "Sources and attribution", description: "The source categories OTTTracker uses and how evidence becomes verified streaming information.", sections: [
    ["Primary source categories", "Sources may include official streaming platforms, studios, distributors and official announcements."],
    ["Metadata and media", "TMDB, IMDb and YouTube may provide metadata, ratings, artwork or trailers. Watch-provider data supplied through TMDB may be sourced from JustWatch. Required attribution remains in place."],
    ["Research providers", "Configured licensed or research services may help locate evidence. Private provider credentials and confidential integrations are never published."],
    ["Verification is separate", "A third-party metadata field does not automatically mean OTTTracker considers an OTT platform or date confirmed. OTTTracker applies its own verification state and freshness checks."],
  ]},
  corrections: { title: "Corrections", description: "Report incorrect movie, OTT platform, release-date, poster or streaming-link information to OTTTracker.", sections: [
    ["How to report an error", "Choose a category, identify the movie and explain what appears wrong. A source link or the correct platform and date is useful, but never submit passwords or sensitive information."],
    ["What happens next", "Reports enter a private review queue. OTTTracker checks the current record and available evidence, then may correct, resolve or dismiss the report. Information can change after a report is submitted."],
  ]},
};

export function TrustPage({ kind }) {
  const content = TRUST_CONTENT[kind];
  const [params] = useSearchParams();
  const movieId = params.get("movie");
  const [state, setState] = useState({ category: "WRONG_OTT_PLATFORM", message: "", email: "" });
  const [notice, setNotice] = useState("");
  const submitCorrection = async (event) => {
    event.preventDefault(); setNotice("Sending…");
    try { await post(`/api/v1/movies/${movieId}/corrections`, state); setNotice("Thank you. Your correction report has been received."); setState({...state, message: ""}); }
    catch (error) { setNotice(error.message); }
  };
  return <main className="prose-page"><Seo title={content.title} description={content.description}/><h1>{content.title}</h1>{content.sections.map(([heading, copy]) => <section key={heading}><h2>{heading}</h2><p>{copy}</p></section>)}{kind === "corrections" && (movieId ? <form className="correction-form" onSubmit={submitCorrection}><h2>Report incorrect movie information</h2><label htmlFor="correction-category">What is wrong?</label><select id="correction-category" value={state.category} onChange={e => setState({...state, category: e.target.value})}><option value="WRONG_OTT_PLATFORM">Wrong OTT platform</option><option value="WRONG_OTT_DATE">Wrong OTT release date</option><option value="BROKEN_STREAMING_LINK">Broken streaming link</option><option value="INCORRECT_POSTER">Incorrect poster</option><option value="INCORRECT_METADATA">Incorrect metadata</option><option value="OTHER">Other</option></select><label htmlFor="correction-message">Details</label><textarea id="correction-message" required minLength="10" value={state.message} onChange={e => setState({...state, message: e.target.value})}/><label htmlFor="correction-email">Email (optional, never shown publicly)</label><input id="correction-email" type="email" autoComplete="email" value={state.email} onChange={e => setState({...state, email: e.target.value})}/><button type="submit">Submit correction</button><p role="status" aria-live="polite">{notice}</p></form> : <p><Link to="/discover">Find the movie</Link>, then use its “Report incorrect information” link to open a movie-specific form.</p>)}</main>;
}

export function Updates() {
  const [data, error] = useData("/api/v1/updates");
  if (error) return <Failure error={error}/>; if (!data) return <Loading/>;
  return <main className="prose-page"><Seo title="OTT Updates" description="Reviewed OTTTracker updates about streaming releases, verified OTT dates, platforms and methodology."/><h1>OTT Updates</h1><p>Reviewed release roundups, recent confirmations and explanations based on OTTTracker’s current records. Drafts are never shown here.</p>{data.items.length ? <div className="update-grid">{data.items.map(item => <article key={item.slug}><p className="eyebrow">{item.content_type.replaceAll("_", " ")}</p><h2><Link to={`/updates/${item.slug}`}>{item.title}</Link></h2><p>{item.summary}</p><small>Published {formatDate(item.published_at)} · {item.author_name}</small></article>)}</div> : <p>No reviewed updates have been published yet.</p>}</main>;
}

export function UpdateArticle() {
  const { slug } = useParams(); const [data, error] = useData(`/api/v1/updates/${slug}`);
  if (error) return <Failure error={error}/>; if (!data) return <Loading/>;
  const schema = {"@context":"https://schema.org","@type":"Article",headline:data.title,description:data.summary,datePublished:data.published_at,dateModified:data.updated_at,author:{"@type":"Organization",name:data.author_name},publisher:{"@type":"Organization",name:"OTTTracker"},mainEntityOfPage:`${location.origin}/updates/${data.slug}`};
  return <main className="prose-page article-page"><Seo title={data.seo_title || data.title} description={data.seo_description || data.summary} image={data.hero_image} type="article" jsonLd={schema} noindex={!data.is_indexable}/><article><p className="eyebrow">OTT Updates</p><h1>{data.title}</h1><p className="article-summary">{data.summary}</p><p className="byline">By {data.author_name} · Published {formatDate(data.published_at)}{data.updated_at !== data.published_at ? ` · Updated ${formatDate(data.updated_at)}` : ""}</p>{data.body.split(/\n\n+/).map((paragraph, index) => <p key={index}>{paragraph}</p>)}<p className="trust-links"><Link to="/methodology">Methodology</Link><Link to="/sources">Sources</Link><Link to="/corrections">Corrections</Link></p></article></main>;
}

export function Legal({ title, description, children }) {
  return (
    <main className="legal">
      <Seo title={title} description={description || `${title} for the OTT Tracker movie and streaming discovery service.`} />
      <h1>{title}</h1>
      {children}
    </main>
  );
}
