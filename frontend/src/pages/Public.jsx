import React, { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { get, imageUrl, post } from "../services/api";
import { Card, Failure, Loading } from "../components/ui";
import Seo, { breadcrumbJsonLd } from "../components/Seo";
import AdSlot from "../components/AdSlot";

const LANGUAGE_NAMES = { ml: "Malayalam", ta: "Tamil", te: "Telugu", hi: "Hindi", kn: "Kannada" };
const SORTS = [["latest", "Latest"], ["oldest", "Oldest"], ["highest-rated", "Highest rated"], ["popularity", "Popularity"], ["recently-added", "Recently added"], ["ott-release", "OTT release date"], ["name-asc", "Name A–Z"], ["name-desc", "Name Z–A"]];

const useData = path => {
  const [data, setData] = useState();
  const [error, setError] = useState();
  useEffect(() => {
    let current = true;
    setData(undefined); setError(undefined);
    get(path).then(value => current && setData(value)).catch(reason => current && setError(reason.message));
    return () => { current = false; };
  }, [path]);
  return [data, error];
};

const Rail = ({ title, items = [], more }) => items.length ? <section><div className="section-title"><h2>{title}</h2>{more && <Link to={more}>View all</Link>}</div><div className="rail">{items.map(movie => <Card key={movie.id} movie={movie} />)}</div></section> : null;
const Art = ({ path, alt, className = "", size = "w500" }) => <img className={className} loading="lazy" decoding="async" src={imageUrl(path, size)} srcSet={path ? `${imageUrl(path, "w342")} 342w, ${imageUrl(path, "w500")} 500w, ${imageUrl(path, "w780")} 780w` : undefined} sizes="(max-width: 640px) 90vw, 500px" alt={alt} onError={event => { event.currentTarget.onerror = null; event.currentTarget.src = "/placeholder.svg"; }} />;
const Empty = ({ children = "No movies match these filters yet." }) => <p className="empty">{children}</p>;

function Pager({ page, pages, onPage }) {
  if (!pages || pages < 2) return null;
  return <nav className="pager" aria-label="Pagination"><button disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button><span>Page {page} of {pages}</span><button disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button></nav>;
}

export function Home() {
  const [data, error] = useData("/api/v1/home");
  if (error) return <Failure error={error} />;
  if (!data) return <Loading />;
  const website = { "@context": "https://schema.org", "@type": "WebSite", name: "Indian OTT Tracker", url: import.meta.env.VITE_SITE_URL || location.origin, potentialAction: { "@type": "SearchAction", target: `${import.meta.env.VITE_SITE_URL || location.origin}/search?q={search_term_string}`, "query-input": "required name=search_term_string" } };
  return <main><Seo title="Indian OTT Tracker" jsonLd={website} /><div className="hero"><p>Indian cinema, all in one place</p><h1>Find your next movie night.</h1><Link to="/discover">Explore movies</Link></div><Rail title="Trending" items={data.trending} more="/discover?sort=popularity"/><Rail title="Popular" items={data.popular} more="/discover?sort=highest-rated"/><Rail title="Latest theatrical" items={data.latest_theatrical}/><Rail title="Upcoming theatrical" items={data.upcoming_theatrical}/><Rail title="Recently added" items={data.recently_added}/><AdSlot slot={import.meta.env.VITE_ADSENSE_SLOT_ID}/><Rail title="Upcoming OTT" items={data.upcoming_ott} more="/ott"/><Rail title="Recently released on OTT" items={data.recent_ott} more="/ott"/>{Object.entries(data.language_sections || {}).map(([code, section]) => <Rail key={code} title={section.name} items={section.items} more={`/languages/${code}`}/>)}<section><h2>Browse genres</h2><div className="chips">{data.genres.map(item => <Link key={item.slug} to={`/genres/${item.slug}`}>{item.name}</Link>)}</div></section><section><h2>OTT platforms</h2><div className="platforms">{data.platforms.map(item => <Link key={item.slug} to={`/ott/${item.slug}`}>{item.logo && <Art path={item.logo} alt=""/>}<strong>{item.name}</strong><small>{item.movie_count} movies</small></Link>)}</div></section></main>;
}

const initialFilters = { q: "", language: "", genre: "", year: "", rating: "", certification: "", release_status: "", platform: "", actor: "", director: "", writer: "", cinematographer: "", producer: "", editor: "", composer: "", date_from: "", date_to: "", sort: "latest" };

export function Discover() {
  const location = useLocation();
  const isSearch = location.pathname === "/search";
  const initial = useMemo(() => ({ ...initialFilters, ...Object.fromEntries(new URLSearchParams(location.search)) }), [location.search]);
  const [filters, setFilters] = useState(initial);
  const [query, setQuery] = useState(new URLSearchParams(Object.entries(initial).filter(([, value]) => value)).toString());
  const [page, setPage] = useState(Number(new URLSearchParams(location.search).get("page")) || 1);
  const endpoint = isSearch ? `/api/v1/search?q=${encodeURIComponent(filters.q || initial.q || "")}&page=${page}` : `/api/v1/discover?${query}&page=${page}`;
  const [data, error] = useData(endpoint);
  const submit = event => { event.preventDefault(); setPage(1); setQuery(new URLSearchParams(Object.entries(filters).filter(([, value]) => value)).toString()); };
  const set = event => setFilters(value => ({ ...value, [event.target.name]: event.target.value }));
  const movies = isSearch ? data?.movies?.items || [] : data?.items || [];
  const total = isSearch ? data?.movies?.total || 0 : data?.total || 0;
  return <main><Seo title={isSearch ? "Search" : "Discover movies"}/><h1>{isSearch ? "Search movies and people" : "Discover movies"}</h1><form className="filters" onSubmit={submit}><label className="wide">Search<input name="q" value={filters.q} onChange={set} placeholder="Title, actor, director, writer or keyword"/></label>{!isSearch && <><label>Language<select name="language" value={filters.language} onChange={set}><option value="">All</option>{Object.entries(LANGUAGE_NAMES).map(([code, name]) => <option key={code} value={code}>{name}</option>)}</select></label><label>Genre<input name="genre" value={filters.genre} onChange={set} placeholder="e.g. drama"/></label><label>Year<input name="year" type="number" value={filters.year} onChange={set}/></label><label>Minimum IMDb rating<input name="rating" type="number" min="0" max="10" step="0.5" value={filters.rating} onChange={set}/></label><label>Certification<input name="certification" value={filters.certification} onChange={set}/></label><label>Status<input name="release_status" value={filters.release_status} onChange={set} placeholder="Released"/></label><label>OTT platform<input name="platform" value={filters.platform} onChange={set}/></label>{["actor", "director", "writer", "cinematographer", "producer", "editor", "composer"].map(name => <label key={name}>{name}<input name={name} value={filters[name]} onChange={set}/></label>)}<label>From<input name="date_from" type="date" value={filters.date_from} onChange={set}/></label><label>To<input name="date_to" type="date" value={filters.date_to} onChange={set}/></label><label>Sort<select name="sort" value={filters.sort} onChange={set}>{SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></>}<button className="wide">Apply</button></form>{error && <p role="alert">{error}</p>}{!data ? <Loading/> : <><h2>Movies <small>({total})</small></h2>{movies.length ? <div className="grid">{movies.map(movie => <Card key={movie.id} movie={movie}/>)}</div> : <Empty/>}{isSearch && <><h2>People <small>({data.people.total})</small></h2><div className="person-results">{data.people.items.map(person => <Link key={person.id} to={`/people/${person.id}`}><Art path={person.profile_path} alt=""/><span><strong>{person.name}</strong><small>{person.department}</small></span></Link>)}</div></>}<Pager page={page} pages={isSearch ? Math.max(Math.ceil(total / data.page_size), Math.ceil(data.people.total / data.page_size)) : data.pages} onPage={setPage}/></>}</main>;
}

export function Browse() {
  const { slug, code } = useParams();
  const [sort, setSort] = useState("latest");
  const [page, setPage] = useState(1);
  const query = slug ? `genre=${slug}` : `language=${code}`;
  const [data, error] = useData(`/api/v1/discover?${query}&sort=${sort}&page=${page}`);
  if (error) return <Failure error={error}/>;
  const title = slug ? `${slug.replaceAll("-", " ")} movies` : `${LANGUAGE_NAMES[code] || code} movies`;
  return <main><Seo title={title} jsonLd={breadcrumbJsonLd([{ name: "Home", path: "/" }, { name: title, path: location.pathname }])}/><h1 className="capitalize">{title}</h1><label>Sort <select value={sort} onChange={event => { setSort(event.target.value); setPage(1); }}>{SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>{!data ? <Loading/> : <><p>{data.total} movies</p>{data.items.length ? <div className="grid">{data.items.map(movie => <Card key={movie.id} movie={movie}/>)}</div> : <Empty/>}<Pager page={page} pages={data.pages} onPage={setPage}/></>}</main>;
}

export function Ott() {
  const [data, error] = useData("/api/v1/ott");
  if (error) return <Failure error={error}/>;
  if (!data) return <Loading/>;
  return <main><Seo title="OTT releases" jsonLd={breadcrumbJsonLd([{ name: "Home", path: "/" }, { name: "OTT", path: "/ott" }])}/><h1>OTT releases</h1><p>Confirmed and currently available streaming information from canonical records.</p><div className="platforms">{data.platforms.map(item => <Link key={item.slug} to={`/ott/${item.slug}`}>{item.logo && <Art path={item.logo} alt=""/>}<strong>{item.name}</strong><small>{item.movie_count} movies</small></Link>)}</div><Rail title="Upcoming OTT releases" items={data.upcoming}/><Rail title="Recently released on OTT" items={data.recent}/><Rail title="Confirmed releases" items={data.confirmed}/></main>;
}

export function OttPlatform() {
  const { platform } = useParams(); const [sort, setSort] = useState("ott-release"); const [page, setPage] = useState(1);
  const [data, error] = useData(`/api/v1/ott/${platform}?sort=${sort}&page=${page}`);
  if (error) return <Failure error={error}/>; if (!data) return <Loading/>;
  return <main><Seo title={`${data.platform} movies`} jsonLd={breadcrumbJsonLd([{ name: "Home", path: "/" }, { name: "OTT", path: "/ott" }, { name: data.platform, path: location.pathname }])}/><h1>{data.platform}</h1><Rail title="Upcoming" items={data.upcoming}/><Rail title="Recently released" items={data.recent}/><div className="section-title"><h2>Available</h2><select value={sort} onChange={event => setSort(event.target.value)}>{SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>{data.available.length ? <div className="grid">{data.available.map(movie => <Card key={movie.id} movie={movie}/>)}</div> : <Empty/>}<Pager page={page} pages={data.pages} onPage={setPage}/></main>;
}

const Values = ({ title, children }) => children && React.Children.count(children) ? <section><h2>{title}</h2>{children}</section> : null;

export function Movie() {
  const { id } = useParams(); const [data, error] = useData(`/api/v1/movies/${id}/detail`);
  if (error) return <Failure error={error}/>; if (!data) return <Loading/>;
  const movie = data.movie;
  const images = type => data.images.filter(item => item.type.toLowerCase().includes(type));
  const logo = images("logo")[0];
  const movieLd = { "@context": "https://schema.org", "@type": "Movie", name: movie.title, ...(movie.original_title && { alternateName: movie.original_title }), ...(movie.overview && { description: movie.overview }), ...(movie.release_date && { dateCreated: movie.release_date }), ...(movie.poster_path && { image: imageUrl(movie.poster_path, "original") }), ...(movie.runtime_minutes && { duration: `PT${movie.runtime_minutes}M` }), ...(movie.rating != null && { aggregateRating: { "@type": "AggregateRating", ratingValue: movie.rating, ratingCount: movie.vote_count || 0, bestRating: 10, author: { "@type": "Organization", name: "IMDb" } } }), ...(data.cast.length && { actor: data.cast.map(item => ({ "@type": "Person", name: item.name })) }), ...(data.crew_by_role.director?.length && { director: data.crew_by_role.director.map(item => ({ "@type": "Person", name: item.name })) }) };
  return <main><Seo title={movie.title} description={movie.overview || `Details and OTT availability for ${movie.title}.`} image={imageUrl(movie.backdrop_path || movie.poster_path, "original")} type="video.movie" jsonLd={[movieLd, breadcrumbJsonLd([{ name: "Home", path: "/" }, { name: "Movies", path: "/discover" }, { name: movie.title, path: location.pathname }])]}/>{movie.backdrop_path && <Art className="hero-backdrop" path={movie.backdrop_path} size="original" alt={`${movie.title} backdrop`}/>}<div className="detail movie-detail"><Art className="poster" path={movie.poster_path} alt={`${movie.title} poster`}/><article>{logo && <Art className="title-logo" path={logo.url} alt={`${movie.title} logo`}/>}<p>{[movie.release_date, movie.runtime_minutes && `${movie.runtime_minutes} min`, movie.status, movie.certification].filter(Boolean).join(" · ")}</p><h1>{movie.title}</h1>{movie.original_title && movie.original_title !== movie.title && <p>Original title: {movie.original_title}</p>}{data.alternative_titles.length > 0 && <p>Also known as: {data.alternative_titles.map(item => item.title).join(", ")}</p>}{movie.tagline && <blockquote>{movie.tagline}</blockquote>}{movie.overview && <p>{movie.overview}</p>}<div className="facts"><span>{movie.rating != null ? `IMDb ★ ${Number(movie.rating).toFixed(1)}${movie.vote_count != null ? ` (${movie.vote_count} votes)` : ""}` : "IMDb rating unavailable"}</span>{movie.original_language && <span>Original language: {movie.original_language}</span>}{movie.spoken_languages.length > 0 && <span>Spoken: {movie.spoken_languages.map(x => x.name).join(", ")}</span>}{movie.production_countries.length > 0 && <span>Countries: {movie.production_countries.map(x => x.name).join(", ")}</span>}{movie.collection && <span>Collection: {movie.collection.name}</span>}{movie.budget > 0 && <span>Budget: ${movie.budget.toLocaleString()}</span>}{movie.revenue > 0 && <span>Revenue: ${movie.revenue.toLocaleString()}</span>}</div></article></div><Values title="Watch legally">{movie.ott.map((item, index) => <article className="ott-row" key={`${item.provider}-${index}`}>{item.logo && <Art path={item.logo} alt=""/>}<div><strong>{item.provider}</strong><p>{[item.watch_type, item.release_date, item.country, item.verification_state, item.confidence != null && `${item.confidence}% confidence`].filter(Boolean).join(" · ")}</p>{item.source_url && <a href={item.source_url} rel="nofollow noreferrer">View source ({item.source})</a>}</div></article>)}</Values><Values title="Ratings">{data.ratings.map((item, index) => <p key={`${item.source}-${index}`}><strong>{item.source}</strong>: {item.rating ?? "Unavailable"}{item.votes != null && ` (${item.votes} votes)`}</p>)}</Values><Values title="Cast"><div className="people">{data.cast.map(item => <Link key={`${item.person_id}-${item.order}`} to={`/people/${item.person_id}`}><Art path={item.profile_path} alt={`${item.name} profile`}/><span>{item.name}<small>{item.character}</small></span></Link>)}</div></Values><Values title="Crew"><div className="people">{data.crew.map((item, index) => <Link key={`${item.person_id}-${index}`} to={`/people/${item.person_id}`}><Art path={item.profile_path} alt={`${item.name} profile`}/><span>{item.name}<small>{item.job || item.department}</small></span></Link>)}</div></Values>{["poster", "backdrop", "logo"].map(type => images(type).length ? <Values key={type} title={`${type[0].toUpperCase()}${type.slice(1)} gallery`}><div className={`image-gallery ${type}`}>{images(type).map((item, index) => <Art key={`${item.url}-${index}`} path={item.url} alt={`${movie.title} ${type} ${index + 1}`}/>)}</div></Values> : null)}<Values title="Release information">{data.releases.map((item, index) => <p key={index}>{item.country}: {item.date} · {item.type}{item.certification && ` · ${item.certification}`}{item.note && ` · ${item.note}`}</p>)}</Values><Values title="Production companies"><div className="chips">{movie.production_companies.map(item => <span key={item.name}>{item.name}</span>)}</div></Values><Values title="Keywords"><div className="chips">{data.keywords.map(item => <span key={item}>{item}</span>)}</div></Values><Values title="External IDs">{data.external_ids.filter(item => item.provider.toLowerCase() !== "tmdb").map(item => <p key={item.provider}><strong>{item.provider}</strong>: {item.url ? <a href={item.url} target="_blank" rel="nofollow noopener noreferrer">{item.id}</a> : item.id}</p>)}</Values><AdSlot slot={import.meta.env.VITE_ADSENSE_SLOT_ID}/></main>;
}

export function Person() {
  const { id } = useParams(); const [sort, setSort] = useState("newest"); const [creditType, setCreditType] = useState("all"); const [role, setRole] = useState("");
  const [data, error] = useData(`/api/v1/people/${id}?sort=${sort}&credit_type=${creditType}&role=${encodeURIComponent(role)}`);
  if (error) return <Failure error={error}/>; if (!data) return <Loading/>;
  const personLd = { "@context": "https://schema.org", "@type": "Person", name: data.name, ...(data.profile_path && { image: imageUrl(data.profile_path, "original") }), ...(data.department && { jobTitle: data.department }) };
  const groups = data.filmography.reduce((result, item) => { const key = item.normalized_role || (item.credit_type === "cast" ? "actor" : "other"); (result[key] ||= []).push(item); return result; }, {});
  const roleTitle = value => ({ actor: "Acting", director: "Directing", writer: "Writing", cinematography: "Cinematography", producer: "Production", editor: "Editing", composer: "Composer / Music" }[value] || value.replaceAll("_", " "));
  return <main><Seo title={data.name} description={`${data.name} filmography and movie credits.`} image={imageUrl(data.profile_path, "original")} jsonLd={[personLd, breadcrumbJsonLd([{ name: "Home", path: "/" }, { name: data.name, path: location.pathname }])]}/><div className="detail"><Art className="poster profile" path={data.profile_path} alt={`${data.name} profile`}/><article><h1>{data.name}</h1><p>{data.department || "Film professional"}</p></article></div><div className="toolbar"><label>Order<select value={sort} onChange={event => setSort(event.target.value)}><option value="newest">Newest first</option><option value="oldest">Oldest first</option></select></label><label>Credits<select value={creditType} onChange={event => setCreditType(event.target.value)}><option value="all">Cast and crew</option><option value="cast">Cast only</option><option value="crew">Crew only</option></select></label><label>Role<select value={role} onChange={event => setRole(event.target.value)}><option value="">All roles</option>{data.roles.map(item => <option key={item} value={item}>{roleTitle(item)}</option>)}</select></label></div><h2>Filmography</h2>{data.filmography.length ? Object.entries(groups).map(([group, items]) => <section className="filmography-group" key={group}><h3 className="capitalize">{roleTitle(group)}</h3><div className="grid">{items.map((item, index) => <div key={`${item.movie.id}-${index}`}><Card movie={item.movie}/><small>{item.character || item.job || item.department || item.credit_type}</small></div>)}</div></section>) : <Empty>No credits match this filter.</Empty>}</main>;
}

const CalendarMovie = ({ movie, type }) => <article className="calendar-entry"><Card movie={movie}/><div>{type === "theatrical" ? <>{movie.certification && <small>Certification: {movie.certification}</small>}<small>Release: {movie.theatrical_release_date}</small></> : <>{movie.ott_platform_logo && <Art path={movie.ott_platform_logo} alt={`${movie.ott_platform} logo`}/>}<Link className="platform-link" to={`/ott/${movie.ott_platform_slug}`}>{movie.ott_platform}</Link><small>OTT release: {movie.ott_release_date}</small>{movie.verification_state && <small className="capitalize">{movie.verification_state} release</small>}</>}</div></article>;

export function Calendar() {
  const { period } = useParams(); const pageLocation = useLocation();
  const tab = new URLSearchParams(pageLocation.search).get("tab") === "ott" ? "ott" : "theatrical";
  const [data, error] = useData(`/api/v1/calendar/${period}`);
  if (error) return <Failure error={error}/>; if (!data) return <Loading/>;
  const periods = ["previous-week", "this-week", "next-week", "previous-month", "this-month", "next-month"];
  const periodLabel = period.replaceAll("-", " "); const selected = data[tab];
  const seoTitle = tab === "ott" ? `OTT Releases ${periodLabel}` : `Movies Releasing ${periodLabel}`;
  return <main><Seo title={seoTitle}/><h1 className="capitalize">{periodLabel} release calendar</h1><p>{data.start_date} to {data.end_date}</p><nav className="chips period-nav" aria-label="Calendar period">{periods.map(item => <Link className={item === period ? "active" : ""} key={item} to={`/calendar/${item}?tab=${tab}`}>{item.replaceAll("-", " ")}</Link>)}</nav><nav className="calendar-tabs" aria-label="Release type"><Link className={tab === "theatrical" ? "active" : ""} to={`/calendar/${period}?tab=theatrical`}>Theatrical Releases</Link><Link className={tab === "ott" ? "active" : ""} to={`/calendar/${period}?tab=ott`}>OTT Releases</Link></nav>{selected.items.length ? <div className="calendar-grid">{selected.items.map((movie, index) => <CalendarMovie key={`${movie.id}-${movie.ott_platform || "theatrical"}-${index}`} movie={movie} type={tab}/>)}</div> : <Empty>{tab === "ott" ? "No confirmed OTT releases found for this period yet." : "No theatrical releases found for this period."}</Empty>}</main>;
}

export function Request() {
  const [result, setResult] = useState(); const submit = event => { event.preventDefault(); const body = Object.fromEntries(new FormData(event.target)); if (body.release_year) body.release_year = Number(body.release_year); post("/api/v1/movie-requests", body).then(setResult).catch(error => setResult({ error: error.message })); };
  return <main><Seo title="Request a movie"/><h1>Request a movie</h1><p>Tell us about a missing movie. Your email is used only to process this request.</p><form className="request" onSubmit={submit}><input name="movie_name" required maxLength="500" placeholder="Movie name"/><input name="email" type="email" required maxLength="320" placeholder="Email"/><input name="release_year" type="number" min="1888" max="2100" placeholder="Release year"/><input name="language" maxLength="20" placeholder="Language"/><textarea name="details" maxLength="2000" placeholder="Any details that help identify it"/><button>Submit request</button></form>{result && <p role="status">{result.error || `Request ${result.request_id} is ${result.status.toLowerCase()}.`}</p>}</main>;
}

export function Legal({ title, children }) { return <main className="legal"><Seo title={title}/><h1>{title}</h1>{children}</main>; }
