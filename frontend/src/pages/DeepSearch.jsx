import React, { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import Seo from "../components/Seo";
import { adminGet, adminPost, get, imageUrl } from "../services/api";
import { COMMON_LANGUAGE_OPTIONS, languageName } from "../services/languages";

const DEFAULT_LANGUAGES = COMMON_LANGUAGE_OPTIONS.map(([code, name]) => ({ code, name }));

const Art = ({ path, alt, className = "", size = "w500" }) => (
  <img
    className={className}
    loading="lazy"
    decoding="async"
    src={imageUrl(path, size)}
    srcSet={path ? `${imageUrl(path, "w342")} 342w, ${imageUrl(path, "w500")} 500w, ${imageUrl(path, "w780")} 780w` : undefined}
    sizes="(max-width: 640px) 92vw, 360px"
    alt={alt}
    onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = "/placeholder.svg"; }}
  />
);

const SourceNotice = () => (
  <aside className="deep-notice">
    <strong>Live Movie Search</strong>
    <span>External details are fetched live and may differ from locally stored movie data.</span>
  </aside>
);

const Empty = ({ children = "No live results matched this search." }) => <p className="empty deep-empty">{children}</p>;
const ErrorState = ({ message }) => <div className="deep-error" role="alert"><strong>Deep Search unavailable</strong><p>{message}</p></div>;

function Pager({ page, pages, onPage }) {
  if (!pages || pages <= 1) return null;
  return <nav className="pager" aria-label="Deep Search pagination">
    <button disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
    <span>Page {page} of {pages}</span>
    <button disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
  </nav>;
}

const requestMovieHref = (movie) => {
  const params = new URLSearchParams({ movie_name: movie.title, movie_external_id: String(movie.id) });
  if (movie.release_date) params.set("release_year", movie.release_date.slice(0, 4));
  if (movie.original_language) params.set("language", movie.original_language);
  return `/request-movie?${params}`;
};

function MovieResult({ movie }) {
  return <article className="deep-result-card">
    <Link className="deep-result-art" to={`/deep-search/movie/${movie.id}`}>
      <Art path={movie.poster_path} alt={`${movie.title} poster`} />
    </Link>
    <div>
      <Link to={`/deep-search/movie/${movie.id}`}><h2>{movie.title}</h2></Link>
      {movie.original_title && movie.original_title !== movie.title && <p className="muted">{movie.original_title}</p>}
      <dl className="deep-inline-facts">
        <div><dt>ID</dt><dd>{movie.id}</dd></div>
        {movie.release_date && <div><dt>Release</dt><dd>{movie.release_date}</dd></div>}
        {movie.original_language && <div><dt>Language</dt><dd>{languageName(movie.original_language, movie.original_language_name)}</dd></div>}
        {movie.popularity != null && <div><dt>Popularity</dt><dd>{Number(movie.popularity).toFixed(1)}</dd></div>}
      </dl>
      {movie.overview && <p className="deep-snippet">{movie.overview}</p>}
      <p className={movie.in_library ? "library-state present" : "library-state"}>{movie.in_library ? "Already in library" : "Not in local database"}</p>
      <div className="deep-actions">
        <Link className="button-link" to={`/deep-search/movie/${movie.id}`}>View live details</Link>
        {movie.local_movie_id && <Link to={`/movies/${movie.local_movie_id}`}>Open Local Movie</Link>}
        {!movie.in_library && <Link to={requestMovieHref(movie)}>Request Movie</Link>}
      </div>
    </div>
  </article>;
}

function PersonResult({ person }) {
  return <Link className="deep-person-card" to={`/deep-search/person/${person.id}`}>
    <Art path={person.profile_path} alt={`${person.name} profile`} />
    <span>
      <strong>{person.name}</strong>
      <small>{person.known_for_department || "Department unavailable"}</small>
      <small>ID {person.id}</small>
      {person.known_for?.length > 0 && <small>Known for: {person.known_for.map((item) => item.title).join(", ")}</small>}
    </span>
  </Link>;
}

export function DeepSearch({ modeTabs = null }) {
  const [params, setParams] = useSearchParams();
  const initialType = ["movies", "people", "imdb"].includes(params.get("type")) ? params.get("type") : "movies";
  const [type, setType] = useState(initialType);
  const [query, setQuery] = useState(params.get("q") || "");
  const [year, setYear] = useState(params.get("year") || "");
  const [language, setLanguage] = useState(params.get("language") || "");
  const [page, setPage] = useState(Number(params.get("page")) || 1);
  const [data, setData] = useState();
  const [error, setError] = useState();
  const [loading, setLoading] = useState(false);
  const [languages, setLanguages] = useState(DEFAULT_LANGUAGES);

  useEffect(() => { get("/api/v1/languages").then((value) => Array.isArray(value) && setLanguages(value)).catch(() => {}); }, []);

  const path = useMemo(() => {
    const q = query.trim();
    if (!q) return null;
    const search = new URLSearchParams();
    if (type === "imdb") search.set("external_id", q);
    else { search.set("q", q); search.set("page", page); }
    if (type === "movies" && year) search.set("year", year);
    if (type === "movies" && language) search.set("language", language);
    return `/api/v1/deep-search/${type === "imdb" ? "find" : type}?${search}`;
  }, [type, query, year, language, page]);

  const run = (event) => {
    event?.preventDefault();
    if (!path) { setError("Enter a movie, person, or IMDb ID."); return; }
    const next = { mode: "deep", type, q: query.trim() };
    if (type === "movies" && year) next.year = year;
    if (type === "movies" && language) next.language = language;
    if (page > 1) next.page = String(page);
    setParams(next);
    setLoading(true); setError(undefined);
    get(path, { maxAge: 10 * 60 * 1000 })
      .then(setData)
      .catch((reason) => { setData(undefined); setError(reason.message); })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (params.get("q")) run();
    // The URL parameters are the initial invocation only; form state owns later requests.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { if (data && page > 0) run(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [page]);

  const movieResults = type === "imdb" ? data?.movies || [] : type === "movies" ? data?.results || [] : [];
  const personResults = type === "imdb" ? data?.people || [] : type === "people" ? data?.results || [] : [];
  const total = data?.total_results ?? movieResults.length + personResults.length;
  return <main className="deep-search-page">
    <Seo title="Deep Search" description="Live movie and people lookup for titles missing from the local library." noindex />
    <h1>Deep Search</h1>
    {modeTabs}
    <SourceNotice />
    <div className="deep-tabs" role="tablist" aria-label="Search type">
      {[['movies', 'Movies'], ['people', 'People'], ['imdb', 'IMDb ID']].map(([value, label]) =>
        <button key={value} type="button" role="tab" aria-selected={type === value} className={type === value ? "active" : ""} onClick={() => { setType(value); setData(undefined); setError(undefined); setPage(1); }}>{label}</button>
      )}
    </div>
    <form className="deep-search-form" onSubmit={(event) => { setPage(1); run(event); }}>
      <label className="deep-query">{type === "movies" ? "Movie name" : type === "people" ? "Person name" : "IMDb ID"}
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={type === "movies" ? "e.g. Aadu" : type === "people" ? "e.g. Prithviraj Sukumaran" : "e.g. tt1234567"} required />
      </label>
      {type === "movies" && <>
        <label>Year<input type="number" min="1870" max="2200" value={year} onChange={(event) => setYear(event.target.value)} placeholder="Optional" /></label>
        <label>Language<select value={language} onChange={(event) => setLanguage(event.target.value)}><option value="">Any language</option>{languages.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}</select></label>
      </>}
      <button disabled={loading}>{loading ? "Searching…" : "Search live source"}</button>
    </form>
    {error && <ErrorState message={error} />}
    {data && <section aria-live="polite">
      <p className="deep-count">{total} live result{total === 1 ? "" : "s"}</p>
      {movieResults.map((movie) => <MovieResult key={movie.id} movie={movie} />)}
      {personResults.length > 0 && <div className="deep-people-grid">{personResults.map((person) => <PersonResult key={person.id} person={person} />)}</div>}
      {!movieResults.length && !personResults.length && <Empty />}
      {type !== "imdb" && <Pager page={data.page} pages={data.total_pages} onPage={setPage} />}
    </section>}
  </main>;
}

function useLive(path) {
  const [state, setState] = useState({ loading: true });
  useEffect(() => {
    let current = true;
    setState({ loading: true });
    get(path, { maxAge: 45 * 60 * 1000 })
      .then((data) => current && setState({ data, loading: false }))
      .catch((error) => current && setState({ error: error.message, loading: false }));
    return () => { current = false; };
  }, [path]);
  return state;
}

function AdminMovieAction({ movie }) {
  const [admin, setAdmin] = useState(false);
  const [result, setResult] = useState();
  const [error, setError] = useState();
  const [busy, setBusy] = useState(false);
  useEffect(() => { adminGet("/api/v1/admin/session").then(() => setAdmin(true)).catch(() => setAdmin(false)); }, []);
  if (!admin) return null;
  const action = movie.in_library ? "repair" : "import";
  const submit = () => {
    setBusy(true); setError(undefined);
    adminPost(`/api/v1/admin/deep-search/movies/${movie.id}/${action}`)
      .then(setResult).catch((reason) => setError(reason.message)).finally(() => setBusy(false));
  };
  return <div className="admin-deep-action">
    <button onClick={submit} disabled={busy}>{busy ? "Queueing…" : movie.in_library ? "Repair Local Data" : "Add to Database"}</button>
    {result && <p role="status">{result.status === "already_exists" ? "Already in library." : "Repair workflow queued."} {result.local_movie_id && <Link to={`/movies/${result.local_movie_id}`}>Open Local Movie</Link>}</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}

const DeepMovieRail = ({ title, items }) => items?.length ? <section><h2>{title}</h2><div className="deep-mini-grid">{items.map((movie) => <Link key={movie.id} to={`/deep-search/movie/${movie.id}`}><Art path={movie.poster_path} alt=""/><strong>{movie.title}</strong><small>ID {movie.id}</small></Link>)}</div></section> : null;

export function DeepMovie() {
  const { tmdbId } = useParams();
  const { data, error, loading } = useLive(`/api/v1/deep-search/movies/${tmdbId}`);
  if (loading) return <main className="loading">Loading live movie details…</main>;
  if (error) return <main><Seo title="Deep movie" noindex/><ErrorState message={error}/></main>;
  const movie = data.movie;
  const externalLabels = { imdb_id: "IMDb", wikidata_id: "Wikidata", facebook_id: "Facebook", instagram_id: "Instagram", twitter_id: "Twitter" };
  const externalUrl = (key, value) => ({
    imdb_id: `https://www.imdb.com/title/${value}/`, wikidata_id: `https://www.wikidata.org/wiki/${value}`,
    facebook_id: `https://www.facebook.com/${value}`, instagram_id: `https://www.instagram.com/${value}`,
    twitter_id: `https://x.com/${value}`,
  })[key];
  return <main className="deep-detail">
    <Seo title={`${movie.title} live details`} description={movie.overview || `Live details for ${movie.title}.`} image={imageUrl(movie.backdrop_path || movie.poster_path, "original")} noindex />
    <SourceNotice />
    {movie.backdrop_path && <Art className="deep-hero" path={movie.backdrop_path} size="original" alt={`${movie.title} backdrop`}/>} 
    <div className="deep-detail-lead">
      <Art className="poster" path={movie.poster_path} alt={`${movie.title} poster`}/>
      <article>
        <p className={movie.in_library ? "library-state present" : "library-state"}>{movie.in_library ? "Already in library" : "Not in local database"}</p>
        <h1>{movie.title}</h1>
        {movie.original_title && movie.original_title !== movie.title && <p>Original title: {movie.original_title}</p>}
        {movie.tagline && <blockquote>{movie.tagline}</blockquote>}
        {movie.overview && <p>{movie.overview}</p>}
        <dl className="deep-facts">
          <div><dt>ID</dt><dd>{movie.id}</dd></div>
          {movie.vote_average != null && <div><dt>Source Rating</dt><dd>{Number(movie.vote_average).toFixed(1)}{movie.vote_count != null && ` (${movie.vote_count} votes)`}</dd></div>}
          {movie.release_date && <div><dt>Release date</dt><dd>{movie.release_date}</dd></div>}
          {movie.status && <div><dt>Status</dt><dd>{movie.status}</dd></div>}
          {movie.runtime && <div><dt>Runtime</dt><dd>{movie.runtime} min</dd></div>}
          {movie.original_language && <div><dt>Original language</dt><dd><Link to={`/languages/${movie.original_language}`}>{languageName(movie.original_language, movie.original_language_name)}</Link></dd></div>}
          {movie.popularity != null && <div><dt>Popularity</dt><dd>{Number(movie.popularity).toFixed(1)}</dd></div>}
          {movie.budget > 0 && <div><dt>Budget</dt><dd>${movie.budget.toLocaleString()}</dd></div>}
          {movie.revenue > 0 && <div><dt>Revenue</dt><dd>${movie.revenue.toLocaleString()}</dd></div>}
        </dl>
        <div className="deep-actions">
          {movie.local_movie_id && <Link className="button-link" to={`/movies/${movie.local_movie_id}`}>Open Local Movie</Link>}
          {!movie.in_library && <Link className="button-link" to={requestMovieHref(movie)}>Request Movie</Link>}
          {movie.homepage && <a href={movie.homepage} rel="nofollow noreferrer">Official homepage</a>}
        </div>
        <AdminMovieAction movie={movie}/>
      </article>
    </div>
    {movie.genres?.length > 0 && <section><h2>Genres</h2><div className="chips">{movie.genres.map((genre) => <Link key={genre.id} to={`/discover?genre=${encodeURIComponent(genre.name)}`}>{genre.name}</Link>)}</div></section>}
    {(movie.spoken_languages?.length > 0 || movie.production_countries?.length > 0 || movie.production_companies?.length > 0 || movie.collection) && <section><h2>Production</h2>
      {movie.spoken_languages?.length > 0 && <p><strong>Spoken languages:</strong> {movie.spoken_languages.map((item) => item.english_name || item.name).join(", ")}</p>}
      {movie.production_countries?.length > 0 && <p><strong>Countries:</strong> {movie.production_countries.map((item) => item.name).join(", ")}</p>}
      {movie.production_companies?.length > 0 && <p><strong>Companies:</strong> {movie.production_companies.map((item) => item.name).join(", ")}</p>}
      {movie.collection && <p><strong>Collection:</strong> {movie.collection.name}</p>}
    </section>}
    {data.external_ids && Object.keys(data.external_ids).length > 0 && <section><h2>External IDs</h2><div className="deep-links">{Object.entries(data.external_ids).map(([key, value]) => <a key={key} href={externalUrl(key, value)} rel="nofollow noreferrer"><strong>{externalLabels[key]}</strong><span>{value}</span></a>)}</div></section>}
    {data.cast?.length > 0 && <section><h2>Cast</h2><div className="deep-credit-grid">{data.cast.slice(0, 40).map((person, index) => <Link key={`${person.id}-${index}`} to={`/deep-search/person/${person.id}`}><Art path={person.profile_path} alt={`${person.name} profile`}/><strong>{person.name}</strong>{person.character && <small>{person.character}</small>}</Link>)}</div></section>}
    {Object.entries(data.crew || {}).map(([group, people]) => <section key={group}><h2>{group}</h2><div className="deep-crew-list">{people.map((person, index) => <Link key={`${person.id}-${person.job}-${index}`} to={`/deep-search/person/${person.id}`}><strong>{person.name}</strong><small>{person.job}</small></Link>)}</div></section>)}
    {data.releases?.length > 0 && <section><h2>Country release information</h2><div className="deep-release-list">{data.releases.map((country) => <article key={country.country}><h3>{country.country === "IN" ? "India (IN)" : country.country}</h3>{country.releases.map((item, index) => <p key={`${item.date}-${item.type}-${index}`}><strong>{item.type}</strong> · {item.date}{item.certification && ` · ${item.certification}`}{item.note && ` · ${item.note}`}</p>)}</article>)}</div></section>}
    {data.watch_providers?.items?.length > 0 && <section><h2>Watch Providers — India</h2><p className="muted">Provider availability does not establish an OTT release date.</p><div className="deep-provider-grid">{data.watch_providers.items.map((provider, index) => <article key={`${provider.id}-${provider.type}-${index}`}><Art path={provider.logo_path} alt=""/><strong>{provider.name}</strong><small>{provider.type}</small></article>)}</div>{data.watch_providers.link && <a href={data.watch_providers.link} rel="nofollow noreferrer">View external provider information</a>}</section>}
    {data.keywords?.length > 0 && <section><h2>Keywords</h2><div className="chips">{data.keywords.map((keyword) => <Link key={keyword.id} to={`/search?q=${encodeURIComponent(keyword.name)}`}>{keyword.name}</Link>)}</div></section>}
    {data.alternative_titles?.length > 0 && <section><h2>Alternative titles</h2><div className="deep-alt-titles">{data.alternative_titles.slice(0, 40).map((item, index) => <span key={`${item.country}-${item.title}-${index}`}><strong>{item.title}</strong><small>{[item.country, item.type].filter(Boolean).join(" · ")}</small></span>)}</div></section>}
    {Object.entries(data.images || {}).map(([type, images]) => images.length > 0 && <section key={type}><h2>{type[0].toUpperCase() + type.slice(1)}</h2><div className={`deep-gallery ${type}`}>{images.map((item, index) => <Art key={`${item.file_path}-${index}`} path={item.file_path} size={type === "backdrops" ? "w780" : "w500"} alt={`${movie.title} ${type.slice(0, -1)}`}/>)}</div></section>)}
    <DeepMovieRail title="Recommended Movies" items={data.recommendations}/>
    <DeepMovieRail title="Similar Movies" items={data.similar}/>
  </main>;
}

export function DeepPerson() {
  const { tmdbPersonId } = useParams();
  const { data, error, loading } = useLive(`/api/v1/deep-search/people/${tmdbPersonId}`);
  if (loading) return <main className="loading">Loading live person details…</main>;
  if (error) return <main><Seo title="Deep person" noindex/><ErrorState message={error}/></main>;
  const person = data.person;
  return <main className="deep-detail">
    <Seo title={`${person.name} live details`} description={person.biography || `Live details for ${person.name}.`} image={imageUrl(person.profile_path, "original")} noindex />
    <SourceNotice />
    <div className="deep-detail-lead person-lead">
      <Art className="poster" path={person.profile_path} alt={`${person.name} profile`}/>
      <article><h1>{person.name}</h1>
        <dl className="deep-facts">
          <div><dt>ID</dt><dd>{person.id}</dd></div>
          {person.known_for_department && <div><dt>Department</dt><dd>{person.known_for_department}</dd></div>}
          {person.birthday && <div><dt>Born</dt><dd>{person.birthday}</dd></div>}
          {person.deathday && <div><dt>Died</dt><dd>{person.deathday}</dd></div>}
          {person.place_of_birth && <div><dt>Place of birth</dt><dd>{person.place_of_birth}</dd></div>}
          {person.popularity != null && <div><dt>Popularity</dt><dd>{Number(person.popularity).toFixed(1)}</dd></div>}
        </dl>
        {person.biography && <p>{person.biography}</p>}
        <div className="deep-actions">{person.local_person_id && <Link className="button-link" to={`/people/${person.local_person_id}`}>Open Local Person</Link>}{person.homepage && <a href={person.homepage} rel="nofollow noreferrer">Official homepage</a>}</div>
      </article>
    </div>
    {data.profiles?.length > 1 && <section><h2>Profile images</h2><div className="deep-gallery posters">{data.profiles.slice(0, 12).map((item, index) => <Art key={`${item.file_path}-${index}`} path={item.file_path} alt={`${person.name} profile`}/>)}</div></section>}
    {Object.entries(data.credits || {}).map(([group, credits]) => <section key={group}><h2>{group}</h2><div className="deep-filmography">{credits.map((credit, index) => <Link key={`${credit.id}-${credit.job}-${credit.character}-${index}`} to={`/deep-search/movie/${credit.id}`}><Art path={credit.poster_path} alt=""/><span><strong>{credit.title}</strong><small>{credit.release_date?.slice(0, 4) || "Year unavailable"}</small>{credit.character && <small>as {credit.character}</small>}{credit.job && <small>{credit.job}</small>}</span></Link>)}</div></section>)}
  </main>;
}
