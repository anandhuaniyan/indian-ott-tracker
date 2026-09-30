import React, { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { API } from "../services/api";


const request = async (path) => {
  const response = await fetch(`${API}${path}`, { credentials: "include" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.detail || (response.status === 401 ? "Admin sign-in required" : "Unable to load AI activity"));
    error.status = response.status;
    throw error;
  }
  return payload;
};

const usePolling = (path, interval = 5000) => {
  const [data, setData] = useState();
  const [error, setError] = useState("");
  useEffect(() => {
    let current = true;
    const load = () => request(path)
      .then((value) => { if (current) { setData(value); setError(""); } })
      .catch((reason) => { if (current) setError(reason.message); });
    load();
    const timer = interval ? window.setInterval(load, interval) : undefined;
    return () => { current = false; if (timer) window.clearInterval(timer); };
  }, [path, interval]);
  return [data, error];
};

const when = (value) => value ? new Date(value).toLocaleString() : "—";
const duration = (seconds) => seconds == null ? "—" : `${Number(seconds).toFixed(1)}s`;
const percent = (value) => `${Math.round(Number(value || 0))}%`;

const tone = (value) => {
  const text = String(value || "UNKNOWN").toUpperCase();
  if (/FAILED|ERROR|CONFLICT|REJECTED/.test(text)) return "danger";
  if (/REVIEW|RUNNING|RATE|QUOTA|DEFERRED/.test(text)) return "warning";
  if (/COMPLETED|ACCEPTED|AUTO_ACCEPT|HEALTHY|CONFIGURED/.test(text)) return "success";
  return "neutral";
};

const Status = ({ children }) => (
  <strong className={`ai-status ai-status-${tone(children)}`}>{String(children || "UNKNOWN").replaceAll("_", " ")}</strong>
);

const Page = ({ title, subtitle, children }) => (
  <main className="ai-activity-page">
    <nav className="ai-breadcrumb" aria-label="AI activity navigation">
      <Link to="/admin">Admin</Link><span aria-hidden="true">/</span><Link to="/admin/ai-activity">AI activity</Link>
    </nav>
    <header className="ai-page-header"><div><p className="eyebrow">Persistent audit history</p><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div></header>
    {children}
  </main>
);

const ErrorState = ({ message }) => (
  <section className="ai-empty" role="alert"><h2>AI activity could not be loaded</h2><p>{message}</p><Link to="/admin/login">Open admin sign-in</Link></section>
);

const Pager = ({ data, setPage }) => data?.pages > 1 ? (
  <nav className="pager" aria-label="AI activity pagination">
    <button disabled={data.page <= 1} onClick={() => setPage(data.page - 1)}>Previous</button>
    <span>Page {data.page} of {data.pages} · {data.total} records</span>
    <button disabled={data.page >= data.pages} onClick={() => setPage(data.page + 1)}>Next</button>
  </nav>
) : null;

const RunsTable = ({ data }) => (
  <div className="table-wrap">
    <table>
      <thead><tr><th>Started</th><th>Run</th><th>Mode</th><th>Progress</th><th>Outcome</th><th>Status</th></tr></thead>
      <tbody>{data?.items?.map((run) => <tr key={run.run_id}>
        <td>{when(run.started_at)}</td>
        <td><Link className="ai-record-link" to={`/admin/ai-activity/runs/${run.run_id}`}>{run.run_id.slice(0, 12)}</Link><small>{run.provider} · {run.model}</small></td>
        <td>{run.trigger}<small>{run.mode}</small></td>
        <td>{run.movies_processed}/{run.movies_total}<small>{run.sources_inspected} sources inspected</small></td>
        <td>{run.successful} success · {run.needs_review} review<small>{run.provider_failures} provider failures · {run.database_updates} updates</small></td>
        <td><Status>{run.status}</Status><small>{duration(run.duration_seconds)}</small></td>
      </tr>)}</tbody>
    </table>
  </div>
);

const defaultFilters = {
  date_from: "", date_to: "", run_id: "", title: "", movie_id: "", tmdb_id: "", imdb_id: "",
  status: "", decision: "", provider: "", model: "", dry_run: "", database_changed: "",
  needs_review: "", conflict: "", provider_failure: "",
};

const queryString = (filters, page) => {
  const params = new URLSearchParams({ page: String(page), page_size: "25" });
  Object.entries(filters).forEach(([key, value]) => { if (value !== "") params.set(key, value); });
  return params.toString();
};

export function AIActivity() {
  const [overview, overviewError] = usePolling("/api/v1/admin/ai-activity/overview");
  const [recent, recentError] = usePolling("/api/v1/admin/ai-activity/runs?page=1&page_size=10", 10000);
  const [filters, setFilters] = useState(defaultFilters);
  const [applied, setApplied] = useState(defaultFilters);
  const [page, setPage] = useState(1);
  const historyPath = useMemo(() => `/api/v1/admin/ai-activity/movies?${queryString(applied, page)}`, [applied, page]);
  const [history, historyError] = usePolling(historyPath, 10000);
  const error = overviewError || recentError || historyError;
  if (error && !overview && !recent && !history) return <Page title="AI research activity"><ErrorState message={error} /></Page>;
  const today = overview?.today || {};
  const provider = overview?.provider || {};
  const active = overview?.current?.active_runs || [];
  const apply = (event) => { event.preventDefault(); setPage(1); setApplied({ ...filters }); };
  const clear = () => { setFilters(defaultFilters); setApplied(defaultFilters); setPage(1); };
  return <Page title="AI research activity" subtitle="Live progress and complete PostgreSQL-backed research history.">
    <section aria-labelledby="current-activity"><div className="ai-section-heading"><div><h2 id="current-activity">Current status</h2><p>Refreshes every five seconds from the persistent event stream.</p></div><Status>{active.length ? "RUNNING" : "IDLE"}</Status></div>
      {active.length ? <div className="ai-active-grid">{active.map((run) => <article className="ai-active-run" key={run.run_id}><Link to={`/admin/ai-activity/runs/${run.run_id}`}>{run.run_id.slice(0, 12)}</Link><strong>{run.movies_processed} / {run.movies_total}</strong><progress max={Math.max(run.movies_total, 1)} value={run.movies_processed}>{run.movies_processed}/{run.movies_total}</progress><span>{run.provider} · {run.model}</span></article>)}</div> : <p className="ai-quiet-state">No AI research run is active. Historical records remain available below.</p>}
    </section>

    <section aria-labelledby="today-activity"><h2 id="today-activity">Today</h2><div className="ai-stat-grid">
      {[['Movies researched', today.movies_researched], ['Successful', today.successful], ['Needs review', today.needs_review], ['Conflicts', today.conflicts], ['No reliable result', today.no_reliable_result], ['Provider failures', today.provider_failures], ['Sources inspected', today.sources_inspected], ['Database updates', today.database_updates]].map(([label, value]) => <article key={label}><strong>{value ?? 0}</strong><span>{label}</span></article>)}
    </div></section>

    <section className="ai-provider-panel" aria-labelledby="provider-status"><div><h2 id="provider-status">Provider status</h2><p>{provider.name || "Not configured"} · {provider.model || "No model"}</p></div><div><Status>{provider.status}</Status><p>{Object.values(provider.errors || {}).reduce((sum, value) => sum + Number(value || 0), 0)} provider errors today · {provider.fallback_count || 0} fallbacks active</p><p>Discord: {overview?.discord_configured ? "configured" : "not configured"}</p></div></section>

    <section aria-labelledby="recent-runs"><h2 id="recent-runs">Recent runs</h2><RunsTable data={recent} /></section>

    <section aria-labelledby="complete-history"><div className="ai-section-heading"><div><h2 id="complete-history">Complete movie history</h2><p>Filters are applied server-side; results are paginated.</p></div></div>
      <form className="ai-filter-grid" onSubmit={apply}>
        <label>From date<input type="date" value={filters.date_from} onChange={(e) => setFilters({ ...filters, date_from: e.target.value })}/></label>
        <label>To date<input type="date" value={filters.date_to} onChange={(e) => setFilters({ ...filters, date_to: e.target.value })}/></label>
        <label>Run ID<input value={filters.run_id} onChange={(e) => setFilters({ ...filters, run_id: e.target.value })}/></label>
        <label>Movie title<input value={filters.title} onChange={(e) => setFilters({ ...filters, title: e.target.value })}/></label>
        <label>Local movie ID<input inputMode="numeric" value={filters.movie_id} onChange={(e) => setFilters({ ...filters, movie_id: e.target.value })}/></label>
        <label>TMDB ID<input inputMode="numeric" value={filters.tmdb_id} onChange={(e) => setFilters({ ...filters, tmdb_id: e.target.value })}/></label>
        <label>IMDb ID<input value={filters.imdb_id} onChange={(e) => setFilters({ ...filters, imdb_id: e.target.value })}/></label>
        <label>Status<select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}><option value="">Any</option><option>RUNNING</option><option>COMPLETED</option><option>FAILED</option></select></label>
        <label>Decision<select value={filters.decision} onChange={(e) => setFilters({ ...filters, decision: e.target.value })}><option value="">Any</option><option>AUTO_ACCEPT</option><option>NEEDS_REVIEW</option><option>CONFLICT</option><option>NO_RELIABLE_RESULT</option><option>DEFERRED_PROVIDER_ERROR</option></select></label>
        <label>Provider<input value={filters.provider} onChange={(e) => setFilters({ ...filters, provider: e.target.value })}/></label>
        <label>Model<input value={filters.model} onChange={(e) => setFilters({ ...filters, model: e.target.value })}/></label>
        <label>Mode<select value={filters.dry_run} onChange={(e) => setFilters({ ...filters, dry_run: e.target.value })}><option value="">Any</option><option value="true">Dry run</option><option value="false">Live</option></select></label>
        <label>Database changed<select value={filters.database_changed} onChange={(e) => setFilters({ ...filters, database_changed: e.target.value })}><option value="">Any</option><option value="true">Yes</option><option value="false">No</option></select></label>
        <label>Needs review<select value={filters.needs_review} onChange={(e) => setFilters({ ...filters, needs_review: e.target.value })}><option value="">Any</option><option value="true">Yes</option><option value="false">No</option></select></label>
        <label>Conflict<select value={filters.conflict} onChange={(e) => setFilters({ ...filters, conflict: e.target.value })}><option value="">Any</option><option value="true">Yes</option><option value="false">No</option></select></label>
        <label>Provider failure<select value={filters.provider_failure} onChange={(e) => setFilters({ ...filters, provider_failure: e.target.value })}><option value="">Any</option><option value="true">Yes</option><option value="false">No</option></select></label>
        <div className="ai-filter-actions"><button type="submit">Apply filters</button><button type="button" className="secondary" onClick={clear}>Clear</button></div>
      </form>
      <div className="table-wrap"><table><thead><tr><th>Movie</th><th>Run</th><th>Research</th><th>Result</th><th>Safety</th></tr></thead><tbody>{history?.items?.map((movie) => <tr key={movie.id}>
        <td><Link className="ai-record-link" to={`/admin/ai-activity/movies/${movie.id}`}>{movie.title}</Link><small>Local {movie.movie_id} · TMDB {movie.tmdb_id || "—"} · {movie.imdb_id || "No IMDb"}</small></td>
        <td><Link to={`/admin/ai-activity/runs/${movie.run_id}`}>{movie.run_id.slice(0, 12)}</Link><small>{when(movie.started_at)}</small></td>
        <td>{movie.provider} · {movie.model}<small>{movie.sources_inspected}/{movie.sources_discovered} sources inspected</small></td>
        <td><Status>{movie.decision || movie.status}</Status><small>{movie.error_type || `${percent(movie.platform_confidence)} platform · ${percent(movie.date_confidence)} date`}</small></td>
        <td>{movie.dry_run ? "Dry run" : "Live"}<small>Database changed: {movie.database_changed ? "yes" : "no"}</small></td>
      </tr>)}</tbody></table></div>
      <Pager data={history} setPage={setPage}/>
    </section>
  </Page>;
}

export function AIActivityRun() {
  const { runId } = useParams();
  const [data, error] = usePolling(`/api/v1/admin/ai-activity/runs/${encodeURIComponent(runId)}`, 5000);
  if (error && !data) return <Page title="AI research run"><ErrorState message={error}/></Page>;
  if (!data) return <Page title="AI research run"><p className="ai-quiet-state">Loading run history…</p></Page>;
  const run = data.run;
  return <Page title={`Run ${run.run_id.slice(0, 12)}`} subtitle={`${run.trigger} · ${run.mode} · ${run.provider} · ${run.model}`}>
    <section className="ai-detail-summary"><div><span>Status</span><Status>{run.status}</Status></div><div><span>Started</span><strong>{when(run.started_at)}</strong></div><div><span>Completed</span><strong>{when(run.completed_at)}</strong></div><div><span>Duration</span><strong>{duration(run.duration_seconds)}</strong></div><div><span>Progress</span><strong>{run.movies_processed}/{run.movies_total}</strong></div><div><span>Sources inspected</span><strong>{run.sources_inspected}</strong></div><div><span>Provider failures</span><strong>{run.provider_failures}</strong></div><div><span>Database updates</span><strong>{run.database_updates}</strong></div></section>
    {run.error_summary && <section className="ai-error-panel" role="alert"><h2>Run error</h2><p>{run.error_summary}</p></section>}
    <section><h2>Movies processed</h2><div className="table-wrap"><table><thead><tr><th>Movie</th><th>Progress</th><th>Evidence</th><th>Decision</th></tr></thead><tbody>{data.movies.items.map((movie) => <tr key={movie.id}><td><Link className="ai-record-link" to={`/admin/ai-activity/movies/${movie.id}`}>{movie.title}</Link><small>Local {movie.movie_id} · TMDB {movie.tmdb_id || "—"}</small></td><td><Status>{movie.status}</Status><small>{duration(movie.duration_seconds)}</small></td><td>{movie.sources_inspected}/{movie.sources_discovered} inspected<small>{movie.platforms_found.join(", ") || "No accepted platform"}</small></td><td><Status>{movie.decision}</Status><small>{movie.error_type || "No provider error"}</small></td></tr>)}</tbody></table></div></section>
    <section><h2>Run timeline</h2><ol className="ai-timeline">{data.events.map((event) => <li key={event.id}><time>{when(event.occurred_at)}</time><div><Status>{event.event_type}</Status><p>{event.message}</p></div></li>)}</ol></section>
  </Page>;
}

const JsonBlock = ({ value }) => <pre className="ai-json">{JSON.stringify(value ?? {}, null, 2)}</pre>;

export function AIActivityMovie() {
  const { activityId } = useParams();
  const [data, error] = usePolling(`/api/v1/admin/ai-activity/movies/${encodeURIComponent(activityId)}`, 5000);
  if (error && !data) return <Page title="Movie research activity"><ErrorState message={error}/></Page>;
  if (!data) return <Page title="Movie research activity"><p className="ai-quiet-state">Loading movie activity…</p></Page>;
  const movie = data.movie;
  return <Page title={movie.title} subtitle={`Local ${movie.movie_id} · TMDB ${movie.tmdb_id || "—"} · ${movie.imdb_id || "No IMDb ID"}`}>
    <section className="ai-detail-summary"><div><span>Status</span><Status>{movie.status}</Status></div><div><span>Decision</span><Status>{movie.decision}</Status></div><div><span>Provider/model</span><strong>{movie.provider} · {movie.model}</strong></div><div><span>Mode</span><strong>{movie.dry_run ? "DRY RUN" : "LIVE"}</strong></div><div><span>Identity confidence</span><strong>{percent(movie.identity_confidence)}</strong></div><div><span>Platform confidence</span><strong>{percent(movie.platform_confidence)}</strong></div><div><span>Date confidence</span><strong>{percent(movie.date_confidence)}</strong></div><div><span>Database changed</span><strong>{movie.database_changed ? "YES" : "NO"}</strong></div></section>
    {movie.error_type && <section className="ai-error-panel" role="alert"><h2>{movie.error_type.replaceAll("_", " ")}</h2><p>{movie.error_summary}</p></section>}
    <section><h2>Research queries</h2><ol className="ai-query-list">{movie.queries.map((query, index) => <li key={`${index}-${query}`}>{query}</li>)}</ol></section>
    <section><h2>Sources</h2>{data.sources.length ? <div className="ai-source-list">{data.sources.map((source) => <article key={source.id}><header><div><a href={source.url} target="_blank" rel="noreferrer">{source.page_title || source.domain}</a><p>{source.domain} · {source.source_tier || "unknown tier"}</p></div><Status>{source.accepted ? "ACCEPTED" : "REJECTED"}</Status></header><dl><dt>Fetch</dt><dd>{source.fetch_status}</dd><dt>Identity</dt><dd>{source.identity_match || "—"}</dd><dt>Platform</dt><dd>{source.platform_evidence || "—"}</dd><dt>Date</dt><dd>{source.date_evidence || "—"}</dd><dt>Region</dt><dd>{source.region_evidence || "—"}</dd><dt>Watch type</dt><dd>{source.watch_type_evidence || "—"}</dd></dl>{source.evidence_excerpt && <p>{source.evidence_excerpt}</p>}{source.rejection_reason && <p className="ai-rejection">Rejected: {source.rejection_reason}</p>}</article>)}</div> : <p className="ai-quiet-state">No source metadata was produced for this movie.</p>}</section>
    <section><h2>Chronological activity</h2><ol className="ai-timeline">{data.events.map((event) => <li key={event.id}><time>{when(event.occurred_at)}</time><div><Status>{event.event_type}</Status><p>{event.message}</p>{event.metadata && <details><summary>Event metadata</summary><JsonBlock value={event.metadata}/></details>}</div></li>)}</ol></section>
    <section><h2>Database proposal</h2><div className="ai-value-grid"><article><h3>Before</h3><JsonBlock value={movie.before_value}/></article><article><h3>Proposed</h3><JsonBlock value={movie.proposed_value}/></article><article><h3>After</h3><JsonBlock value={movie.after_value}/></article></div></section>
  </Page>;
}
