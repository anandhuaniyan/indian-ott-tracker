import React, { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { get, imageUrl, post } from "../services/api";
import { Card, Failure, Loading } from "../components/ui";
import Seo, { breadcrumbJsonLd } from "../components/Seo";
import AdSlot from "../components/AdSlot";
import { COMMON_LANGUAGE_OPTIONS, languageName } from "../services/languages";

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

const formatDate = (value) =>
  value
    ? new Intl.DateTimeFormat(undefined, {
        day: "numeric",
        month: "long",
        year: "numeric",
        timeZone: "UTC",
      }).format(new Date(`${value}T00:00:00Z`))
    : null;

const useData = (path) => {
  const [data, setData] = useState();
  const [error, setError] = useState();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let current = true;
    setData(undefined);
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
        {more && <Link to={more}>View all</Link>}
      </div>
      <div className="rail">
        {items.map((movie) => (
          <Card key={movie.id} movie={movie} />
        ))}
      </div>
    </section>
  ) : null;
const Art = ({ path, alt, className = "", size = "w500" }) => (
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
      event.currentTarget.src = "/placeholder.svg";
    }}
  />
);
const Empty = ({ children = "No movies match these filters yet." }) => (
  <p className="empty">{children}</p>
);

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
  const [data, error] = useData("/api/v1/home");
  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;
  const website = {
    "@context": "https://schema.org",
    "@type": "WebSite",
    name: "Indian OTT Tracker",
    url: import.meta.env.VITE_SITE_URL || location.origin,
    potentialAction: {
      "@type": "SearchAction",
      target: `${import.meta.env.VITE_SITE_URL || location.origin}/search?q={search_term_string}`,
      "query-input": "required name=search_term_string",
    },
  };
  return (
    <main>
      <Seo title="Indian OTT Tracker" jsonLd={website} />
      <div className="hero">
        <p>Indian cinema, all in one place</p>
        <h1>Find your next movie night.</h1>
        <Link to="/discover">Explore movies</Link>
      </div>
      <Rail
        title="Popular"
        items={data.popular}
        more="/discover?sort=popularity"
      />
      <Rail title="Latest theatrical" items={data.latest_theatrical} />
      <Rail title="Upcoming theatrical" items={data.upcoming_theatrical} />
      <Rail title="Recently added" items={data.recently_added} />
      <AdSlot slot={import.meta.env.VITE_ADSENSE_SLOT_ID} />
      <Rail title="Upcoming OTT" items={data.upcoming_ott} more="/ott" />
      <Rail
        title="Recently released on OTT"
        items={data.recent_ott}
        more="/ott"
      />
      {Object.entries(data.language_sections || {}).map(([code, section]) => (
        <Rail
          key={code}
          title={section.name}
          items={section.items}
          more={`/languages/${code}`}
        />
      ))}
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
    </main>
  );
}

const initialFilters = {
  q: "",
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

export function Discover({ modeTabs = null }) {
  const location = useLocation();
  const isSearch = location.pathname === "/search";
  const initial = useMemo(
    () => ({
      ...initialFilters,
      ...Object.fromEntries(new URLSearchParams(location.search)),
    }),
    [location.search],
  );
  const [filters, setFilters] = useState(initial);
  const [query, setQuery] = useState(
    new URLSearchParams(
      Object.entries(initial).filter(([, value]) => value),
    ).toString(),
  );
  const [page, setPage] = useState(
    Number(new URLSearchParams(location.search).get("page")) || 1,
  );
  const endpoint = isSearch
    ? `/api/v1/search?q=${encodeURIComponent(filters.q || initial.q || "")}&page=${page}`
    : `/api/v1/discover?${query}&page=${page}`;
  const [data, error] = useData(endpoint);
  const submit = (event) => {
    event.preventDefault();
    setPage(1);
    setQuery(
      new URLSearchParams(
        Object.entries(filters).filter(([, value]) => value),
      ).toString(),
    );
  };
  const set = (event) =>
    setFilters((value) => ({
      ...value,
      [event.target.name]: event.target.value,
    }));
  const movies = isSearch ? data?.movies?.items || [] : data?.items || [];
  const total = isSearch ? data?.movies?.total || 0 : data?.total || 0;
  return (
    <main>
      <Seo title={isSearch ? "Search" : "Discover movies"} />
      <h1>{isSearch ? "Search movies and people" : "Discover movies"}</h1>
      {isSearch && modeTabs}
      <form className="filters" onSubmit={submit}>
        <label className="wide">
          Search
          <input
            name="q"
            value={filters.q}
            onChange={set}
            placeholder="Title, actor, director, writer or keyword"
          />
        </label>
        {!isSearch && (
          <>
            <label>
              Language
              <select name="language" value={filters.language} onChange={set}>
                <option value="">All</option>
                {(data?.filters?.languages?.map((item) => [item.code, item.name]) || COMMON_LANGUAGE_OPTIONS).map(([code, name]) => (
                  <option key={code} value={code}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Genre
              <input
                name="genre"
                value={filters.genre}
                onChange={set}
                placeholder="e.g. drama"
              />
            </label>
            <label>
              Year
              <input
                name="year"
                type="number"
                value={filters.year}
                onChange={set}
              />
            </label>
            <label>
              Minimum IMDb rating
              <input
                name="rating"
                type="number"
                min="0"
                max="10"
                step="0.5"
                value={filters.rating}
                onChange={set}
              />
            </label>
            <label>
              Certification
              <input
                name="certification"
                value={filters.certification}
                onChange={set}
              />
            </label>
            <label>
              Release status
              <select
                name="release_status"
                value={filters.release_status}
                onChange={set}
              >
                <option value="">All</option>
                <option value="released">Released</option>
                <option value="upcoming">Upcoming</option>
                <option value="direct-to-ott">Direct-to-OTT</option>
              </select>
            </label>
            <label>
              OTT platform
              <input name="platform" value={filters.platform} onChange={set} />
            </label>
            {[
              "actor",
              "director",
              "writer",
              "cinematographer",
              "producer",
              "editor",
              "composer",
            ].map((name) => (
              <label key={name}>
                {name}
                <input name={name} value={filters[name]} onChange={set} />
              </label>
            ))}
            <label>
              From
              <input
                name="date_from"
                type="date"
                value={filters.date_from}
                onChange={set}
              />
            </label>
            <label>
              To
              <input
                name="date_to"
                type="date"
                value={filters.date_to}
                onChange={set}
              />
            </label>
            <label>
              Sort
              <select name="sort" value={filters.sort} onChange={set}>
                {SORTS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        <button className="wide">Apply</button>
      </form>
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
            <Empty />
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
          <Pager
            page={page}
            pages={
              isSearch
                ? Math.max(
                    Math.ceil(total / data.page_size),
                    Math.ceil(data.people.total / data.page_size),
                  )
                : data.pages
            }
            onPage={setPage}
          />
        </>
      )}
    </main>
  );
}

export function Browse() {
  const { slug, code } = useParams();
  const [sort, setSort] = useState("latest");
  const [page, setPage] = useState(1);
  const query = slug ? `genre=${slug}` : `language=${code}`;
  const [data, error] = useData(
    `/api/v1/discover?${query}&sort=${sort}&page=${page}`,
  );
  if (error) return <Failure error={error} />;
  const title = slug
    ? `${slug.replaceAll("-", " ")} movies`
    : `${languageName(code)} movies`;
  return (
    <main>
      <Seo
        title={title}
        jsonLd={breadcrumbJsonLd([
          { name: "Home", path: "/" },
          { name: title, path: location.pathname },
        ])}
      />
      <h1 className="capitalize">{title}</h1>
      <label>
        Sort{" "}
        <select
          value={sort}
          onChange={(event) => {
            setSort(event.target.value);
            setPage(1);
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
          <Pager page={page} pages={data.pages} onPage={setPage} />
        </>
      )}
    </main>
  );
}

export function Ott() {
  const [data, error] = useData("/api/v1/ott");
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
  const [sort, setSort] = useState("ott-release");
  const [page, setPage] = useState(1);
  const [data, error] = useData(
    `/api/v1/ott/${platform}?sort=${sort}&page=${page}`,
  );
  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;
  return (
    <main>
      <Seo
        title={`${data.platform} movies`}
        jsonLd={breadcrumbJsonLd([
          { name: "Home", path: "/" },
          { name: "OTT", path: "/ott" },
          { name: data.platform, path: location.pathname },
        ])}
      />
      <h1>{data.platform}</h1>
      <Rail title="Upcoming" items={data.upcoming} />
      <Rail title="Recently released" items={data.recent} />
      <div className="section-title">
        <h2>Available</h2>
        <select value={sort} onChange={(event) => setSort(event.target.value)}>
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
      <Pager page={page} pages={data.pages} onPage={setPage} />
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

const TrailerSection = ({ trailer, title }) => (
  <section className="trailer-section" aria-labelledby="trailer-heading">
    <h2 id="trailer-heading">Trailer</h2>
    {trailer ? (
      <div className="trailer-frame">
        <iframe
          src={trailer.embed_url}
          title={trailer.name || `${title} official trailer`}
          loading="lazy"
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
          allowFullScreen
          referrerPolicy="strict-origin-when-cross-origin"
        />
      </div>
    ) : (
      <p className="empty">Trailer not available.</p>
    )}
  </section>
);

const CommentsSection = ({ movieId }) => {
  const [data, setData] = useState();
  const [page, setPage] = useState(1);
  const [error, setError] = useState();
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState();
  const load = (nextPage = 1) => {
    setError(undefined);
    get(`/api/v1/movies/${movieId}/comments?page=${nextPage}&page_size=10`, { maxAge: 0 })
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

export function Movie() {
  const { id } = useParams();
  const [data, error] = useData(`/api/v1/movies/${id}/detail`);
  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;
  const movie = data.movie;
  const images = (type) =>
    data.images.filter((item) => item.type.toLowerCase().includes(type));
  const logo = images("logo")[0];
  const movieLd = {
    "@context": "https://schema.org",
    "@type": "Movie",
    name: movie.title,
    ...(movie.original_title && { alternateName: movie.original_title }),
    ...(movie.overview && { description: movie.overview }),
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
              <strong>{movie.ott_platform || "Information not found"}</strong>
            </span>
            <span data-testid="ott-release">
              <small>OTT Release</small>
              <strong>
                {formatDate(movie.ott_release_date) ||
                  (movie.ott_platform ? "Not confirmed" : "Information not found")}
              </strong>
            </span>
            <span data-testid="ott-status">
              <small>OTT Availability</small>
              <strong>
                {({
                  AVAILABLE_NOW: "Available now",
                  COMING_TO_OTT: "Coming to OTT",
                  PLATFORM_KNOWN_DATE_UNKNOWN: "Platform known — date unknown",
                  OTT_INFORMATION_NOT_FOUND: "OTT information not found",
                })[movie.ott_status] || "OTT information not found"}
              </strong>
            </span>
            {movie.ott_research_status && (
              <span data-testid="ott-research-status">
                <small>OTT Research</small>
                <strong>{movie.ott_research_status}</strong>
              </span>
            )}
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
                  item.release_date ? item.availability_state : "date not confirmed",
                  item.confidence != null && `${item.confidence}% confidence`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              {item.source_url && (
                <a href={item.source_url} rel="nofollow noreferrer">
                  {item.source === "official_platform"
                    ? `Watch on ${item.provider}`
                    : `View source (${item.source})`}
                </a>
              )}
            </div>
          </article>
        ))}
      </Values>
      <Values title="Ratings">
        {data.ratings.map((item, index) => (
          <p key={`${item.source}-${index}`}>
            <strong>{item.source}</strong>: {item.rating ?? (item.status === "pending" ? "Pending" : "Unavailable")}
            {item.votes != null && ` (${item.votes} votes)`}
          </p>
        ))}
      </Values>
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
      <TrailerSection trailer={data.trailer} title={movie.title} />
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
      <AdSlot slot={import.meta.env.VITE_ADSENSE_SLOT_ID} />
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
            <span className="calendar-platform">{movie.ott_platform}</span>
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
  const selectedLanguage = calendarParams.get("language") || "";
  const requestedMonth = calendarParams.get("month") || "";
  const focusToday = calendarParams.get("focus") === "today";
  const [todayRequest, setTodayRequest] = useState(0);
  const endpoint = `/api/v1/calendar/${requestedMonth ? "this-month" : period}${
    requestedMonth ? `?month=${encodeURIComponent(requestedMonth)}` : ""
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
  }, [data?.today, focusToday, selectedLanguage, tab, todayRequest]);
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
  const items = selected.items.filter(
    (movie) => !selectedLanguage || movie.language === selectedLanguage,
  );
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
  const availableLanguages = COMMON_LANGUAGE_OPTIONS.filter(([code]) =>
    selected.items.some((movie) => movie.language === code),
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
      <Seo title={seoTitle} />
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
        <label className="calendar-language-filter">
          <span>Language</span>
          <select
            value={selectedLanguage}
            onChange={(event) => {
              const next = new URLSearchParams(calendarParams);
              if (event.target.value) next.set("language", event.target.value);
              else next.delete("language");
              setCalendarParams(next, { replace: true });
            }}
          >
            <option value="">All languages</option>
            {availableLanguages.map(([code, name]) => (
              <option value={code} key={code}>{name}</option>
            ))}
          </select>
        </label>
      </div>
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

export function Request() {
  const [params] = useSearchParams();
  const [languages] = useData("/api/v1/languages");
  const [result, setResult] = useState();
  const [submitting, setSubmitting] = useState(false);
  const submit = (event) => {
    event.preventDefault();
    setSubmitting(true);
    setResult(undefined);
    const body = Object.fromEntries(new FormData(event.target));
    body.movie_external_id = Number(body.movie_external_id);
    if (body.release_year) body.release_year = Number(body.release_year);
    post("/api/v1/movie-requests", body)
      .then(setResult)
      .catch((error) => setResult({ error: error.message, ...(error.data || {}) }))
      .finally(() => setSubmitting(false));
  };
  const received = Boolean(result?.request_id);
  return (
    <main>
      <Seo title="Request a movie" />
      <h1>Request a movie</h1>
      <p>
        Ask us to review any movie, including one that is already listed here.
        Your email is used only to process this request.
      </p>
      <p><Link to="/search?mode=deep">Don&apos;t know the ID? Use Deep Search.</Link></p>
      {!received && <form className="request" onSubmit={submit}>
        <label>Movie Name *<input name="movie_name" required maxLength="500" defaultValue={params.get("movie_name") || ""} /></label>
        <label>Email *<input name="email" type="email" required maxLength="320" /></label>
        <label>ID *<input name="movie_external_id" type="number" required min="1" max="2147483647" step="1" inputMode="numeric" defaultValue={params.get("movie_external_id") || ""} /></label>
        <label>Year<input name="release_year" type="number" min="1888" max="2100" defaultValue={params.get("release_year") || ""} /></label>
        <label>Language<select name="language" defaultValue={params.get("language") || ""}><option value="">Not specified</option>{(languages || COMMON_LANGUAGE_OPTIONS.map(([code, name]) => ({ code, name }))).map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}</select></label>
        <label>Additional Details<textarea name="details" maxLength="2000" placeholder="Any details that help identify it" /></label>
        <button disabled={submitting}>{submitting ? "Verifying movie…" : "Submit request"}</button>
      </form>}
      {received && (
        <section className="request-success" role="status">
          {result.poster_path && <Art className="request-poster" path={result.poster_path} alt={`${result.verified_title} poster`} />}
          <div>
            <h2>Request received</h2>
            <h3>{result.verified_title}</h3>
            {result.original_title && result.original_title !== result.verified_title && <p>{result.original_title}</p>}
            <p>We aim to review your request within 48 hours. We’ll email you when its status changes.</p>
            {result.confirmation_email_status === "SENT" ? (
              <p>Confirmation email sent.</p>
            ) : (
              <p>Your request was received, but we could not send the confirmation email.</p>
            )}
            <small>Request reference: {result.request_id}</small>
          </div>
        </section>
      )}
      {result?.error && (
        <div className="request-error" role="alert">
          <p>{result.error}</p>
          {result.local_movie_id && <Link className="button-link" to={`/movies/${result.local_movie_id}`}>View Movie</Link>}
        </div>
      )}
    </main>
  );
}

export function Legal({ title, children }) {
  return (
    <main className="legal">
      <Seo title={title} />
      <h1>{title}</h1>
      {children}
    </main>
  );
}
