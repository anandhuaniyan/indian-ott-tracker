import React, { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { API, imageUrl } from "../services/api";

const call = (path, options) =>
  fetch(`${API}${path}`, { credentials: "include", ...options }).then(
    async (response) => {
      if (!response.ok)
        throw new Error(
          response.status === 401
            ? "Unauthenticated"
            : (await response.json().catch(() => ({}))).detail ||
              "Request failed",
        );
      return response.json();
    },
  );
const json = (method, body) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const formatDateTime = (value) =>
  value ? new Date(value).toLocaleString() : "Never";
const useAdmin = (path, refreshMilliseconds = 0) => {
  const [data, setData] = useState(),
    [error, setError] = useState();
  const load = () =>
    call(path)
      .then((value) => { setData(value); setError(undefined); })
      .catch((reason) => setError(reason.message));
  useEffect(() => {
    load();
    if (!refreshMilliseconds) return undefined;
    const timer = window.setInterval(load, refreshMilliseconds);
    return () => window.clearInterval(timer);
  }, [path, refreshMilliseconds]);
  return [data, error, load];
};

const Nav = () => (
  <nav className="admin-nav">
    <Link to="/admin">Dashboard</Link>
    <Link to="/admin/requests">Requests</Link>
    <Link to="/admin/movies">Movies</Link>
    <Link to="/admin/discovery">Discovery</Link>
    <Link to="/admin/comments">Comments</Link>
    <Link to="/admin/data-health">Data health</Link>
    <Link to="/admin/images">Images</Link>
    <Link to="/admin/ott-research">OTT research</Link>
    <Link to="/admin/ott-gold-set">OTT gold set</Link>
    <Link to="/admin/jobs">Jobs</Link>
    <Link to="/admin/notifications">Notifications</Link>
    <Link to="/admin/sources">Sources</Link>
    <Link to="/admin/system-health">System health</Link>
  </nav>
);
const Page = ({ title, children }) => (
  <main>
    <h1>{title}</h1>
    <Nav />
    {children}
  </main>
);
const Guard = ({ error }) =>
  error ? (
    <Page title="Admin">
      <p>
        {error}. <Link to="/admin/login">Sign in</Link>
      </p>
    </Page>
  ) : null;
const Table = ({ headers, rows }) => (
  <div className="table-wrap">
    <table>
      <thead>
        <tr>
          {headers.map((item) => (
            <th key={item}>{item}</th>
          ))}
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
);

const duration = (seconds) => {
  const value = Math.max(0, Math.abs(seconds || 0));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  return `${hours}h ${minutes}m`;
};
const when = (value) => value ? new Date(value).toLocaleString() : "Never";
const Status = ({ children }) => {
  const value = String(children || "UNKNOWN");
  const tone = /FAILED|DOWN|CONFLICT|OVERDUE|REJECTED/.test(value)
    ? "danger"
    : /PENDING|POSSIBLE|ATTENTION|URGENT|REVIEW|DEGRADED|QUEUED|RUNNING/.test(value)
      ? "warning"
      : /HEALTHY|SENT|ADDED|APPROVED|CONFIRMED|COMPLETE|MATCHED/.test(value)
        ? "success"
        : "neutral";
  return <strong className={`admin-status admin-status-${tone}`}>{value.replaceAll("_", " ")}</strong>;
};
const sourceStatus = (source) => {
  if (!source.enabled)
    return ["ottplay", "justwatch"].includes(source.source) ? "DISABLED" : "NOT_CONFIGURED";
  return source.status || (source.healthy ? "HEALTHY" : "DEGRADED");
};
const Pager = ({ data, onPage }) => data?.pages > 1 ? (
  <nav className="pager" aria-label="Pagination">
    <button disabled={data.page <= 1} onClick={() => onPage(data.page - 1)}>Previous</button>
    <span>Page {data.page} of {data.pages} · {data.total} records</span>
    <button disabled={data.page >= data.pages} onClick={() => onPage(data.page + 1)}>Next</button>
  </nav>
) : null;

export function Login() {
  const navigate = useNavigate(),
    [error, setError] = useState();
  const submit = (event) => {
    event.preventDefault();
    call(
      "/api/v1/admin/login",
      json("POST", { password: new FormData(event.target).get("password") }),
    )
      .then(() => navigate("/admin"))
      .catch((reason) => setError(reason.message));
  };
  return (
    <main>
      <h1>Admin login</h1>
      <form className="request" onSubmit={submit}>
        <input
          name="password"
          type="password"
          minLength="8"
          required
          autoComplete="current-password"
          placeholder="Password"
        />
        <button>Sign in</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </main>
  );
}

export function Dashboard() {
  const [data, error] = useAdmin("/api/v1/admin/dashboard");
  const navigate = useNavigate();
  if (error) return <Guard error={error} />;
  if (!data)
    return (
      <Page title="Admin dashboard">
        <p>Loading…</p>
      </Page>
    );
  const logout = () =>
    call("/api/v1/admin/logout", { method: "POST" }).then(() =>
      navigate("/admin/login"),
    );
  return (
    <Page title="Admin dashboard">
      <button onClick={logout}>Log out</button>
      <div className="metrics">
        {[
          ["Total movies", data.total_movies, "/admin/movies"],
          ["Movies added today", data.movies_added_today, "/admin/movies?sort=newest"],
          ["Movies added this week", data.movies_added_this_week, "/admin/movies?sort=newest"],
          ["Discovered today", data.discovered_today, "/admin/discovery"],
          ["Imported today", data.discovery_imported_today, "/admin/discovery?status=IMPORTED"],
          ["Discovery review", data.discovery_review_today, "/admin/discovery?status=NEEDS_REVIEW"],
          ["Requests pending", data.pending_requests, "/admin/requests?status=PENDING"],
          ["Requests reviewing", data.reviewing_requests, "/admin/requests?status=REVIEWING"],
          ["Requests added", data.added_requests, "/admin/requests?status=ADDED"],
          ["Comments pending", data.pending_comments, "/admin/comments?status=PENDING"],
          ["OTT releases confirmed", data.ott_confirmed, "/admin/ott-research"],
          ["Upcoming OTT releases", data.upcoming_ott, "/admin/ott-research?release=UPCOMING"],
          ["OTT missing", data.missing_ott, "/admin/movies?ott=missing"],
          ["OTT needs review", data.ott_needs_review, "/admin/ott-research?status=NEEDS_REVIEW"],
          ["Movies missing images", data.movies_missing_images, "/admin/movies?poster=missing"],
          ["Movies missing IMDb rating", data.movies_missing_imdb_rating, "/admin/movies?imdb=missing"],
          ["Movies missing trailer", data.movies_missing_trailer, "/admin/movies?trailer=missing"],
          ["Failed jobs", data.failed_jobs, "/admin/jobs?status=FAILED"],
        ].map(([label, value, href]) => (
          <Link className="metric-link" to={href} key={label}>
            <strong>{value}</strong>
            <span>{label}</span>
          </Link>
        ))}
      </div>
      <section className="admin-panel">
        <h2>Actionable alerts</h2>
        <div className="admin-alerts">
          {(data.alerts || []).map((item) => (
            <Link className={`admin-alert admin-alert-${item.severity}`} to={item.href} key={item.href}>{item.label}</Link>
          ))}
          {!data.alerts?.length && <p className="empty">No current alerts.</p>}
        </div>
      </section>
      <section className="admin-panel">
        <h2>Movie discovery schedule</h2>
        <p>
          Runs at 08:00 and 20:00 in {data.discovery?.timezone}. Next run: {when(data.discovery?.next_run)}.
        </p>
        <div className="metrics">
          {["morning", "evening"].map((slot) => {
            const run = data.discovery?.slots?.[slot];
            return <article className="metric-link" key={slot}>
              <strong>{run ? run.status : "NOT RUN"}</strong>
              <span>{slot[0].toUpperCase() + slot.slice(1)} · {run ? when(run.started_at) : "No history"}</span>
            </article>;
          })}
        </div>
      </section>
      <section className="admin-panel">
        <h2>Quick actions</h2>
        <div className="admin-quick-actions">
          <Link className="button-link" to="/admin/requests?status=PENDING">Review pending requests</Link>
          <Link className="button-link" to="/admin/ott-research?status=CONFLICTING">Review OTT conflicts</Link>
          <Link className="button-link" to="/admin/comments?status=PENDING">Moderate comments</Link>
          <Link className="button-link" to="/admin/requests?email_status=FAILED">Retry failed emails</Link>
          <Link className="button-link" to="/admin/movies?trailer=missing">View missing trailers</Link>
          <Link className="button-link" to="/admin/sources">Run source sync</Link>
        </div>
      </section>
      <h2>Source health</h2>
      <div className="admin-source-grid">
        {(data.sources || []).map((source) => (
          <article key={source.source}>
            <header><h3>{source.label}</h3><Status>{sourceStatus(source)}</Status></header>
            <small>Last success: {when(source.last_success)}</small>
            {source.last_error && <p className="admin-safe-error">{source.last_error}</p>}
          </article>
        ))}
      </div>
      <h2>Recent activity</h2>
      <div className="admin-activity">
        {(data.recent_activity || []).map((item) => (
          <article key={item.id}><time>{when(item.timestamp)}</time><strong>{item.event}</strong><span>{item.target}</span><small>{item.status || ""}</small></article>
        ))}
      </div>
      <h2>Job health</h2>
      <Table
        headers={[
          "Task",
          "Cursor / processed",
          "Last success",
          "Last failure",
          "Last error",
        ]}
        rows={data.jobs.map((job) => (
          <tr key={job.task}>
            <td>{job.task}</td>
            <td>
              {job.cursor} / {job.processed_count}
            </td>
            <td>{when(job.last_success)}</td>
            <td>{job.last_failure ? when(job.last_failure) : "—"}</td>
            <td>{job.last_error || "—"}</td>
          </tr>
        ))}
      />
      <h2>Recent notifications</h2>
      {data.recent_notifications.map((item) => (
        <p key={item.id}>
          <strong>{item.severity}</strong> · {item.channel} · {item.message}
        </p>
      ))}
    </Page>
  );
}

export function Discovery() {
  const location = useLocation();
  const initial = new URLSearchParams(location.search);
  const [status, setStatus] = useState(initial.get("status") || "NEEDS_REVIEW");
  const [language, setLanguage] = useState("");
  const [page, setPage] = useState(1);
  const path = `/api/v1/admin/discovery?status=${encodeURIComponent(status)}&language=${encodeURIComponent(language)}&page=${page}`;
  const [data, error, reload] = useAdmin(path, 60000);
  const act = (id, action) => {
    const movieId = action === "match_existing" ? window.prompt("Local movie ID") : undefined;
    if (action === "match_existing" && !movieId) return;
    call(`/api/v1/admin/discovery/${id}`, json("PATCH", {
      action,
      movie_id: movieId ? Number(movieId) : null,
    })).then(reload).catch((reason) => window.alert(reason.message));
  };
  if (error) return <Guard error={error} />;
  return <Page title="Movie discovery">
    <p>
      Automatic scans run at 08:00 and 20:00 in {data?.timezone || "the site timezone"}.
      {data?.next_run && <> Next run: {when(data.next_run)}.</>}
    </p>
    <div className="filters">
      <select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
        <option value="NEEDS_REVIEW">Needs review</option>
        <option value="IMPORTED">Imported</option>
        <option value="EXISTING">Already exists</option>
        <option value="FAILED">Failed</option>
        <option value="FILTERED">Filtered</option>
        <option value="IGNORED">Ignored</option>
      </select>
      <select value={language} onChange={(event) => { setLanguage(event.target.value); setPage(1); }}>
        <option value="">All languages</option>
        <option value="ml">Malayalam</option><option value="ta">Tamil</option>
        <option value="te">Telugu</option><option value="hi">Hindi</option>
        <option value="kn">Kannada</option>
      </select>
      <button onClick={reload}>Refresh</button>
    </div>
    {!data ? <p>Loading…</p> : <>
      <div className="metrics">
        {Object.entries(data.counts || {}).map(([label, value]) => (
          <article className="metric-link" key={label}><strong>{value}</strong><span>{label.replaceAll("_", " ")}</span></article>
        ))}
      </div>
      <Table headers={["Movie", "Identity", "Release", "Status / match", "Last seen", "Actions"]} rows={data.items.map((item) => (
        <tr key={item.id}>
          <td><strong>{item.title}</strong>{item.original_title && item.original_title !== item.title && <small>{item.original_title}</small>}</td>
          <td>{item.source} · {item.tmdb_id || item.external_key}{item.imdb_id && <small>{item.imdb_id}</small>}</td>
          <td>{item.language || "—"} · {item.release_date || "Unknown"}</td>
          <td><Status>{item.status}</Status><small>{item.match_reason || item.last_error || "—"}</small>{item.matched_movie_id && <Link to={`/movies/${item.matched_movie_id}`}>Local movie {item.matched_movie_id}</Link>}</td>
          <td>{when(item.last_seen_at)}</td>
          <td><div className="admin-actions"><button onClick={() => act(item.id, "match_existing")}>Match</button><button onClick={() => act(item.id, "duplicate")}>Duplicate</button><button onClick={() => act(item.id, "wrong_language")}>Wrong language</button><button onClick={() => act(item.id, "tv_series")}>TV</button><button onClick={() => act(item.id, "ignore")}>Ignore</button></div></td>
        </tr>
      ))}/>
      {!data.items.length && <p className="empty">No discovery candidates match these filters.</p>}
      <Pager data={data} onPage={setPage}/>
      <h2>Recent runs</h2>
      <Table headers={["Slot", "Window", "Result", "Counts", "Completed"]} rows={data.runs.map((run) => (
        <tr key={run.id}><td>{run.run_type} · {run.slot}</td><td>{run.window_start} – {run.window_end}</td><td><Status>{run.status}</Status></td><td>{run.candidates_discovered} found · {run.new_movies_imported} imported · {run.needs_review} review · {run.failed} failed</td><td>{when(run.completed_at)}</td></tr>
      ))}/>
    </>}
  </Page>;
}

export function Requests() {
  const location = useLocation();
  const initial = new URLSearchParams(location.search);
  const [status, setStatus] = useState(initial.get("status") || ""),
    [search, setSearch] = useState(initial.get("search") || ""),
    [emailStatus, setEmailStatus] = useState(initial.get("email_status") || ""),
    [local, setLocal] = useState(initial.get("local") || ""),
    [age, setAge] = useState(initial.get("age") || ""),
    [sort, setSort] = useState(initial.get("sort") || "newest"),
    [page, setPage] = useState(Number(initial.get("page")) || 1),
    [query, setQuery] = useState(initial.toString());
  const [reasons, setReasons] = useState({});
  const [actionError, setActionError] = useState();
  const [data, error, reload] = useAdmin(`/api/v1/admin/requests?${query}`, 15000);
  const action = (path, options) => {
    setActionError(undefined);
    return call(path, options).then(reload).catch((reason) => setActionError(reason.message));
  };
  const update = (id, value, extra = {}) => action(
    `/api/v1/admin/requests/${id}`,
    json("PATCH", { status: value, ...extra }),
  );
  const retry = (id, kind) => action(
    `/api/v1/admin/requests/${id}/emails/${kind}/retry`,
    { method: "POST" },
  );
  const applyFilters = (overrides = {}) => {
    const params = new URLSearchParams();
    const values = { search, status, email_status: emailStatus, local, age, sort, page: overrides.page || 1, ...overrides };
    Object.entries(values).forEach(([key, value]) => value && params.set(key, value));
    setPage(Number(values.page) || 1);
    setQuery(params.toString());
  };
  if (error) return <Guard error={error} />;
  return (
    <Page title="Movie requests">
      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          applyFilters();
        }}
      >
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search title, email or ID"
        />
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="">All statuses</option>
          {["PENDING", "REVIEWING", "FOUND", "ADDED", "REJECTED"].map(
            (item) => (
              <option key={item}>{item}</option>
            ),
          )}
        </select>
        <select value={emailStatus} onChange={(event) => setEmailStatus(event.target.value)} aria-label="Email status">
          <option value="">All email states</option>
          <option value="FAILED">Email failed</option>
          <option value="PENDING">Email pending</option>
          <option value="NOT_CONFIGURED">Email not configured</option>
          <option value="SENT">Email sent</option>
        </select>
        <select value={local} onChange={(event) => setLocal(event.target.value)} aria-label="Local movie status">
          <option value="">Any local status</option>
          <option value="exists">Movie exists locally</option>
          <option value="missing">Movie missing locally</option>
        </select>
        <select value={age} onChange={(event) => setAge(event.target.value)} aria-label="Request age">
          <option value="">Any age</option>
          <option value="24">Older than 24 hours</option>
          <option value="36">Older than 36 hours</option>
          <option value="48">Older than 48 hours</option>
        </select>
        <select value={sort} onChange={(event) => setSort(event.target.value)} aria-label="Sort requests">
          <option value="newest">Newest</option><option value="oldest">Oldest</option>
          <option value="highest_age">Highest age</option><option value="recently_updated">Recently updated</option>
        </select>
        <button>Filter</button>
        <button type="button" onClick={reload}>Refresh</button>
      </form>
      {actionError && <p className="admin-error" role="alert">{actionError}</p>}
      {data?.counters && (
        <div className="admin-counter-tabs" aria-label="Request status counters">
          {Object.entries(data.counters).map(([key, value]) => (
            <button className={status === (key === "ALL" ? "" : key) ? "active" : ""} key={key} onClick={() => { const next = key === "ALL" ? "" : key; setStatus(next); applyFilters({ status: next, page: 1 }); }}>{key.replaceAll("_", " ")} <strong>{value}</strong></button>
          ))}
        </div>
      )}
      {!data ? (
        <p>Loading…</p>
      ) : (
        <div className="admin-request-list">
          {data.items.map((item) => (
            <article className="admin-request-card" key={item.request_id}>
              <img className="admin-request-poster" src={imageUrl(item.poster_path)} alt={`${item.verified_title} poster`} loading="lazy" />
              <div className="admin-request-content">
                <div className="admin-request-heading">
                  <div>
                    <h2>{item.verified_title}</h2>
                    {item.original_title && item.original_title !== item.verified_title && <p>{item.original_title}</p>}
                  </div>
                  <Status>{item.status}</Status>
                  <Status>{item.sla || "NORMAL"}</Status>
                  {item.movie_existed_at_submission && <span className="local-info">Movie exists locally</span>}
                </div>
                <dl className="admin-request-facts">
                  <div><dt>External ID</dt><dd>{item.movie_external_id || "Historical request"}</dd></div>
                  {item.local_movie_id && <div><dt>Local Movie ID</dt><dd>{item.local_movie_id}</dd></div>}
                  <div><dt>IMDb</dt><dd>{item.imdb_id || "—"}</dd></div>
                  <div><dt>Release</dt><dd>{item.release_date || item.release_year || "—"}</dd></div>
                  <div><dt>Language</dt><dd>{item.language_name || item.language || "—"}</dd></div>
                  <div><dt>Director</dt><dd>{item.director || "—"}</dd></div>
                  <div><dt>Requester</dt><dd>{item.email}</dd></div>
                  <div><dt>Reference</dt><dd>{item.request_id}</dd></div>
                  <div><dt>Created</dt><dd>{new Date(item.created_at).toLocaleString()}</dd></div>
                  <div><dt>Last updated</dt><dd>{when(item.updated_at)}</dd></div>
                  <div><dt>Age</dt><dd>{duration(item.age_seconds)}</dd></div>
                  <div><dt>48-hour target</dt><dd>{duration(item.target_seconds)} {item.target_seconds < 0 ? "overdue" : "remaining"}</dd></div>
                </dl>
                {item.details && <p><strong>User details:</strong> {item.details}</p>}
                <div className="email-states">
                  {Object.entries(item.emails).map(([kind, delivery]) => (
                    <div key={kind}>
                      <strong>{kind.replaceAll("_", " ")} email</strong>
                      <span>{delivery.status}</span>
                      <small>{delivery.sent_at ? new Date(delivery.sent_at).toLocaleString() : delivery.last_error || "Not sent"} · {delivery.attempt_count || 0} attempts</small>
                      {delivery.status !== "SENT" && (kind === "confirmation" || kind === "admin_notification" || kind === "completion" && item.status === "ADDED" || kind === "rejection" && item.status === "REJECTED") && (
                        <button onClick={() => retry(item.request_id, kind)}>Retry {kind}</button>
                      )}
                    </div>
                  ))}
                </div>
                <div className="admin-request-actions">
                  <Link className="button-link" to={`/admin/requests/${item.request_id}`}>Open details</Link>
                  {item.status === "PENDING" && <button onClick={() => update(item.request_id, "REVIEWING")}>Review</button>}
                  {["PENDING", "REVIEWING"].includes(item.status) && <button onClick={() => update(item.request_id, "FOUND")}>Mark Found</button>}
                  {["PENDING", "REVIEWING", "FOUND"].includes(item.status) && item.movie_external_id && !item.local_movie_id && (
                    <button onClick={() => action(`/api/v1/admin/deep-search/movies/${item.movie_external_id}/import`, { method: "POST" })}>Add Movie</button>
                  )}
                  {["PENDING", "REVIEWING", "FOUND"].includes(item.status) && <button onClick={() => update(item.request_id, "ADDED")}>Mark Added</button>}
                  {["PENDING", "REVIEWING", "FOUND"].includes(item.status) && (
                    <>
                      <input aria-label={`Public rejection reason for ${item.verified_title}`} value={reasons[item.request_id] || ""} onChange={(event) => setReasons({ ...reasons, [item.request_id]: event.target.value })} placeholder="Optional public rejection reason" />
                      <button onClick={() => update(item.request_id, "REJECTED", { public_rejection_reason: reasons[item.request_id] || null })}>Reject</button>
                    </>
                  )}
                  {item.local_movie_id && <Link className="button-link" to={`/movies/${item.local_movie_id}`}>View Movie</Link>}
                </div>
              </div>
            </article>
          ))}
          {!data.items.length && <p className="empty">No movie requests match these filters.</p>}
          <Pager data={data} onPage={(next) => applyFilters({ page: next })} />
        </div>
      )}
    </Page>
  );
}

export function RequestDetail() {
  const { requestId } = useParams();
  const [data, error, reload] = useAdmin(`/api/v1/admin/requests/${encodeURIComponent(requestId)}`, 15000);
  const [actionError, setActionError] = useState("");
  const act = (path, options) => {
    setActionError("");
    return call(path, options).then(reload).catch((reason) => setActionError(reason.message));
  };
  const update = (status) => act(`/api/v1/admin/requests/${requestId}`, json("PATCH", { status }));
  if (error) return <Guard error={error} />;
  if (!data) return <Page title="Request detail"><p>Loading…</p></Page>;
  const localId = data.local?.id || data.local_movie_id;
  return (
    <Page title={`Request ${data.request_id}`}>
      <Link to="/admin/requests">← Back to requests</Link>
      {actionError && <p className="admin-error" role="alert">{actionError}</p>}
      <section className="admin-detail-hero">
        <img src={imageUrl(data.poster_path)} alt={`${data.verified_title} poster`} />
        <div>
          <div className="admin-request-heading"><h2>{data.verified_title}</h2><Status>{data.status}</Status><Status>{data.sla}</Status></div>
          {data.original_title && data.original_title !== data.verified_title && <p>{data.original_title}</p>}
          <dl className="admin-request-facts">
            <div><dt>Requested</dt><dd>{when(data.created_at)}</dd></div>
            <div><dt>Age</dt><dd>{duration(data.age_seconds)}</dd></div>
            <div><dt>Last updated</dt><dd>{when(data.updated_at)}</dd></div>
            <div><dt>Requester</dt><dd><a href={`mailto:${data.email}`}>{data.email}</a></dd></div>
          </dl>
        </div>
      </section>
      <section className="admin-panel">
        <h2>Verified movie</h2>
        <dl className="admin-request-facts">
          <div><dt>External / TMDB ID</dt><dd>{data.movie_external_id || "Unknown"}</dd></div>
          <div><dt>IMDb ID</dt><dd>{data.imdb_id || "Unknown"}</dd></div>
          <div><dt>Original language</dt><dd>{data.language_name || data.language || "Unknown"}</dd></div>
          <div><dt>Release date</dt><dd>{data.release_date || data.release_year || "Unknown"}</dd></div>
          <div><dt>Runtime</dt><dd>{data.local?.runtime ? `${data.local.runtime} minutes` : "Unknown"}</dd></div>
          <div><dt>Genres</dt><dd>{data.genres?.join(", ") || "Unknown"}</dd></div>
          <div><dt>Director</dt><dd>{data.local?.directors?.join(", ") || data.director || "Unknown"}</dd></div>
          <div><dt>Main cast</dt><dd>{data.local?.cast?.join(", ") || "Unknown"}</dd></div>
        </dl>
        {data.overview && <p>{data.overview}</p>}
      </section>
      <section className="admin-panel">
        <h2>Local database</h2>
        <dl className="admin-request-facts">
          <div><dt>Movie exists locally</dt><dd><Status>{data.local?.exists ? "YES" : "NO"}</Status></dd></div>
          <div><dt>Local movie ID</dt><dd>{localId || "Not associated"}</dd></div>
          <div><dt>Metadata status</dt><dd><Status>{data.local?.metadata_status || "NOT_LOCAL"}</Status></dd></div>
          <div><dt>IMDb rating</dt><dd>{data.imdb_rating ?? "Missing"}</dd></div>
          <div><dt>Added</dt><dd>{when(data.local?.added_at)}</dd></div>
        </dl>
      </section>
      <section className="admin-panel">
        <h2>OTT research</h2>
        <dl className="admin-request-facts">
          <div><dt>Platform</dt><dd>{data.ott?.platform || "Unknown"}</dd></div>
          <div><dt>OTT release date</dt><dd>{data.ott?.release_date || "Unknown"}</dd></div>
          <div><dt>Confidence</dt><dd>{data.ott?.confidence || 0}%</dd></div>
          <div><dt>Research status</dt><dd><Status>{data.ott?.status || "UNKNOWN"}</Status></dd></div>
          <div><dt>Verification</dt><dd><Status>{data.ott?.verification_status || "UNKNOWN"}</Status></dd></div>
          <div><dt>Last check</dt><dd>{when(data.ott?.last_check)}</dd></div>
        </dl>
        <div className="admin-evidence-list">
          {(data.ott?.sources || []).map((source) => <article key={source.id}><strong>{source.name}</strong><p>{source.platform || "No platform"} · {source.date || "No date"} · {source.confidence}%</p>{source.url && <a href={source.url} rel="noreferrer">Open source</a>}</article>)}
          {!data.ott?.sources?.length && <p className="empty">No inspected OTT evidence yet.</p>}
        </div>
      </section>
      <section className="admin-panel">
        <h2>Trailer</h2>
        <p><Status>{data.trailer?.available ? "AVAILABLE" : "MISSING"}</Status> {data.trailer?.name || ""}</p>
        {data.trailer?.video_key && <a href={`https://www.youtube.com/watch?v=${encodeURIComponent(data.trailer.video_key)}`} rel="noreferrer">Open YouTube</a>}
      </section>
      <section className="admin-panel">
        <h2>Data completeness</h2>
        <div className="admin-checklist">{Object.entries(data.data_completeness || {}).map(([label, complete]) => <span key={label} className={complete ? "complete" : "missing"}>{complete ? "✓" : "✕"} {label.replaceAll("_", " ")}</span>)}</div>
      </section>
      <section className="admin-panel">
        <h2>Request details</h2><p className="pre-wrap">{data.details || "No additional details supplied."}</p>
        <h3>Email status</h3>
        <div className="email-states">{Object.entries(data.emails || {}).map(([kind, delivery]) => {
          const retryAllowed = kind === "confirmation" || kind === "admin_notification" || (kind === "completion" && data.status === "ADDED") || (kind === "rejection" && data.status === "REJECTED");
          return <div key={kind}><strong>{kind.replaceAll("_", " ")}</strong><Status>{delivery.status}</Status><small>{delivery.sent_at ? when(delivery.sent_at) : delivery.last_error || "Not sent"}</small>{delivery.status !== "SENT" && retryAllowed && <button onClick={() => act(`/api/v1/admin/requests/${requestId}/emails/${kind}/retry`, { method: "POST" })}>Retry email</button>}</div>;
        })}</div>
      </section>
      <section className="admin-panel">
        <h2>Admin actions</h2>
        <div className="admin-request-actions">
          {data.status === "PENDING" && <button onClick={() => update("REVIEWING")}>Start review</button>}
          {["PENDING", "REVIEWING"].includes(data.status) && <button onClick={() => update("FOUND")}>Mark found</button>}
          {!localId && data.movie_external_id && <button onClick={() => act(`/api/v1/admin/deep-search/movies/${data.movie_external_id}/import`, { method: "POST" })}>Add / Import movie</button>}
          {localId && <><Link className="button-link" to={`/movies/${localId}`}>Open local movie</Link><button onClick={() => act(`/api/v1/admin/movies/${localId}/repair`, { method: "POST" })}>Refresh metadata / images / trailer / IMDb</button><button onClick={() => act(`/api/v1/admin/movies/${localId}/research-ott`, { method: "POST" })}>Research OTT now</button></>}
          {localId && data.status !== "ADDED" && <button onClick={() => update("ADDED")}>Mark added</button>}
          {!(["ADDED", "REJECTED"].includes(data.status)) && <button onClick={() => update("REJECTED")}>Reject</button>}
        </div>
      </section>
    </Page>
  );
}

export function Comments() {
  const initial = new URLSearchParams(useLocation().search);
  const [status, setStatus] = useState(initial.get("status") || ""),
    [today, setToday] = useState(initial.get("today") === "true");
  const [actionError, setActionError] = useState();
  const [data, error, reload] = useAdmin(`/api/v1/admin/comments?${new URLSearchParams({ ...(status && { status }), ...(today && { today: "true" }) })}`, 15000);
  const action = (id, options) => {
    setActionError(undefined);
    call(`/api/v1/admin/comments/${id}`, options).then(reload).catch((reason) => setActionError(reason.message));
  };
  if (error) return <Guard error={error} />;
  return (
    <Page title="Comments">
      <div className="toolbar">
        <label>Status<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option>{["PENDING", "APPROVED", "HIDDEN", "REJECTED"].map((item) => <option key={item}>{item}</option>)}</select></label>
        <label><input type="checkbox" checked={today} onChange={(event) => setToday(event.target.checked)} /> Submitted today</label>
        <button type="button" onClick={reload}>Refresh</button>
      </div>
      {actionError && <p className="admin-error" role="alert">{actionError}</p>}
      {!data ? <p>Loading…</p> : data.items.length ? (
        <div className="admin-comment-list">
          {data.items.map((item) => (
            <article className="admin-comment-card" key={item.id}>
              <header><div className="admin-comment-subject"><img src={imageUrl(item.poster_path)} alt="" loading="lazy" /><div><h2>{item.display_name}</h2><Link to={`/movies/${item.movie_id}`}>{item.movie_title}</Link></div></div><Status>{item.status}</Status></header>
              <p>{item.comment}</p>
              <dl className="admin-request-facts">
                <div><dt>Date</dt><dd>{new Date(item.created_at).toLocaleString()}</dd></div>
                <div><dt>Email (private)</dt><dd>{item.email || "—"}</dd></div>
              </dl>
              <div className="admin-request-actions">
                <button onClick={() => action(item.id, json("PATCH", { status: "APPROVED" }))}>Approve</button>
                <button onClick={() => action(item.id, json("PATCH", { status: "HIDDEN" }))}>Hide</button>
                <button onClick={() => action(item.id, json("PATCH", { status: "REJECTED" }))}>Reject</button>
                <button onClick={() => action(item.id, { method: "DELETE" })}>Delete</button>
              </div>
            </article>
          ))}
        </div>
      ) : <p className="empty">No comments match this filter.</p>}
    </Page>
  );
}

export function DataHealth() {
  const [status, setStatus] = useState("open"),
    [type, setType] = useState("");
  const [data, error] = useAdmin(
    `/api/v1/admin/data-health?status=${status}&issue_type=${encodeURIComponent(type)}`,
  );
  if (error) return <Guard error={error} />;
  const healthHref = (category, label) => {
    if (category === "trailers" && label === "missing") return "/admin/movies?trailer=missing";
    if (category === "ratings" && label === "imdb_missing") return "/admin/movies?imdb=missing";
    if (category === "identifiers" && label === "missing_imdb") return "/admin/movies?imdb=missing";
    if (category === "images" && label === "missing_poster") return "/admin/movies?poster=missing";
    if (category === "ott") return label === "needs_review" ? "/admin/ott-research?status=NEEDS_REVIEW" : "/admin/ott-research";
    if (category === "requests") return `/admin/requests?${label === "overdue" ? "age=48" : `status=${label.toUpperCase()}`}`;
    if (category === "comments") return "/admin/comments?status=PENDING";
    if (category === "jobs") return `/admin/jobs?status=${label.toUpperCase()}`;
    return `/admin/data-health?issue_type=${encodeURIComponent(label)}`;
  };
  return (
    <Page title="Data health">
      <div className="toolbar">
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="open">Open</option>
          <option value="resolved">Resolved</option>
          <option value="all">All</option>
        </select>
        <input
          value={type}
          onChange={(event) => setType(event.target.value)}
          placeholder="Issue type"
        />
      </div>
      {!data ? (
        <p>Loading…</p>
      ) : (
        <>
          <h2>Complete operational coverage</h2>
          <div className="admin-health-groups">
            {Object.entries(data.summary || {}).map(([category, values]) => (
              <section key={category}><h3>{category.replaceAll("_", " ")}</h3><div className="metrics">{Object.entries(values).map(([label, value]) => <Link className="metric-link" to={healthHref(category, label)} key={label}><strong>{value}</strong><span>{label.replaceAll("_", " ")}</span></Link>)}</div></section>
            ))}
          </div>
          <h2>IMDb coverage</h2>
          <div className="metrics">
            {Object.entries(data.imdb || {}).map(([label, value]) => (
              <article key={label}>
                <strong>{typeof value === "boolean" ? (value ? "Yes" : "No") : value ?? "Never"}</strong>
                <span>{label.replaceAll("_", " ")}</span>
              </article>
            ))}
          </div>
          <h2>OTT coverage</h2>
          <div className="metrics">
            {Object.entries(data.ott || {}).filter(([label]) => label !== "percentages").map(([label, value]) => (
              <article key={label}>
                <strong>{value}</strong>
                <span>{label.replaceAll("_", " ")}</span>
                {label !== "total_movies" && data.ott?.percentages?.[label] != null && (
                  <small>{data.ott.percentages[label]}%</small>
                )}
              </article>
            ))}
          </div>
          <h2>Data-quality issues</h2>
          <p>{data.total} issues</p>
          <Table
            headers={[
              "Movie",
              "Issue",
              "Severity",
              "Description",
              "Created",
              "Status",
            ]}
            rows={data.items.map((item) => (
              <tr key={item.id}>
                <td>{item.movie || `Movie ${item.movie_id}`}</td>
                <td>{item.issue_type}</td>
                <td>{item.severity}</td>
                <td>{item.description || "—"}</td>
                <td>{item.created_at}</td>
                <td>{item.status}</td>
              </tr>
            ))}
          />
        </>
      )}
    </Page>
  );
}

export function Images() {
  const [status, setStatus] = useState("unresolved");
  const [data, error, reload] = useAdmin(
    `/api/v1/admin/images?status=${status}`,
  );
  const retry = (item) =>
    call(
      item.person_id
        ? `/api/v1/admin/images/people/${item.person_id}/retry`
        : `/api/v1/admin/images/${item.movie_id}/retry?image_type=${item.type.includes("backdrop") ? "backdrop" : item.type.includes("logo") ? "logo" : "poster"}`,
      { method: "POST" },
    ).then(reload);
  if (error) return <Guard error={error} />;
  return (
    <Page title="Image health">
      <select
        value={status}
        onChange={(event) => setStatus(event.target.value)}
      >
        <option value="unresolved">Unresolved</option>
        <option value="recovered">Recovered</option>
        <option value="">All</option>
      </select>
      {data && (
        <>
          <div className="metrics">
            {Object.entries(data.counts).map(([label, value]) => (
              <article key={label}>
                <strong>{value}</strong>
                <span>{label}</span>
              </article>
            ))}
          </div>
          <Table
            headers={[
              "Movie or person",
              "Type",
              "Description",
              "Status",
              "Action",
            ]}
            rows={data.items.map((item) => (
              <tr key={item.id}>
                <td>{item.subject}</td>
                <td>{item.type}</td>
                <td>{item.description || "—"}</td>
                <td>{item.status}</td>
                <td>
                  {(item.movie_id || item.person_id) &&
                    item.status === "unresolved" && (
                      <button onClick={() => retry(item)}>Retry</button>
                    )}
                </td>
              </tr>
            ))}
          />
        </>
      )}
    </Page>
  );
}

export function OttResearch() {
  const initial = new URLSearchParams(useLocation().search);
  const [status, setStatus] = useState(initial.get("status") || ""),
    [data, error, reload] = useAdmin(
      `/api/v1/admin/ott-research?status=${status}`,
    );
  const [overview] = useAdmin("/api/v1/admin/ott-overview");
  const [command, commandError, reloadCommand] = useAdmin("/api/v1/admin/ott-command-center", 30000);
  const [releases] = useAdmin(`/api/v1/admin/ott-releases?${initial.get("release") ? `status=${initial.get("release")}` : "page_size=20"}`);
  const [detail, setDetail] = useState(null);
  const [actionError, setActionError] = useState("");
  const [message, setMessage] = useState("");
  const [manual, setManual] = useState({
    platform: "", ott_release_date: "", source_url: "",
    source_name: "", country: "IN", summary: "",
  });
  const action = (id, value) =>
    call(
      `/api/v1/admin/ott-research/${id}/action`,
      json("POST", { action: value }),
    ).then(reload);
  const inspect = (movieId) =>
    call(`/api/v1/admin/ott-research/movies/${movieId}`)
      .then((value) => {
        setDetail(value);
        setManual((current) => ({
          ...current,
          platform: value.canonical?.platform || "",
          ott_release_date: value.canonical?.ott_release_date || "",
        }));
        setActionError("");
      })
      .catch((reason) => setActionError(reason.message));
  const evidenceAction = (id, value) =>
    call(`/api/v1/admin/ott-evidence/${id}`, json("PATCH", { action: value }))
      .then(() => inspect(detail.movie.id))
      .then(reload)
      .catch((reason) => setActionError(reason.message));
  const verify = (event) => {
    event.preventDefault();
    call(
      `/api/v1/admin/ott-research/movies/${detail.movie.id}/verify`,
      json("POST", { ...manual, ott_release_date: manual.ott_release_date || null }),
    )
      .then(() => inspect(detail.movie.id))
      .then(reload)
      .catch((reason) => setActionError(reason.message));
  };
  const runIntelligence = (period) =>
    call(`/api/v1/admin/ott-intelligence/${period}/run`, { method: "POST" })
      .then(() => setMessage(`${period} OTT intelligence run queued`))
      .catch((reason) => setActionError(reason.message));
  const generateGoldSet = () =>
    call("/api/v1/admin/ott-gold-set/generate", { method: "POST" })
      .then((value) => { setMessage(`Gold set contains ${value.total} movies`); reloadCommand(); })
      .catch((reason) => setActionError(reason.message));
  const intelligence = command?.gold_set && command?.summary && Array.isArray(command?.providers)
    ? command
    : null;
  if (error) return <Guard error={error} />;
  return (
    <Page title="OTT research">
      <div className="admin-subnav"><Link to="/admin/ott-research">Research queue</Link><Link to="/admin/sources">Source health</Link><Link to="/admin/sources?source=ottplay&view=unmatched">OTTplay unmatched</Link></div>
      {message && <p role="status">{message}</p>}
      {commandError && <p className="admin-error" role="alert">{commandError}</p>}
      {intelligence && <>
        <section className="admin-panel">
          <header><h2>OTT intelligence command center</h2><Status>{intelligence.gold_set.gate_passed ? "ACCURACY_GATE_PASSED" : "ACCURACY_GATE_BLOCKED"}</Status></header>
          <div className="admin-request-actions"><button onClick={() => runIntelligence("daily")}>Run daily collection</button><button onClick={() => runIntelligence("weekly")}>Run weekly verification</button><button onClick={generateGoldSet}>Create / complete gold set</button></div>
          <p>Country: India · Gold set: {intelligence.gold_set.verified}/{intelligence.gold_set.target} manually verified · Automatic publication: {intelligence.gold_set.automatic_publication_enabled ? "enabled" : "disabled"}</p>
          <div className="metrics">{Object.entries(intelligence.summary).map(([label, value]) => <article key={label}><strong>{value}</strong><span>{label.replaceAll("_", " ")}</span></article>)}</div>
        </section>
        {intelligence.web_research && <section className="admin-panel">
          <header><h2>One-shot web research</h2><Status>{intelligence.web_research.status}</Status></header>
          <p>Last researched: {formatDateTime(intelligence.web_research.last_researched_at)}</p>
          <div className="metrics">
            <article><strong>{intelligence.web_research.web_researched ?? 0}</strong><span>researched</span></article>
            <article><strong>{intelligence.web_research.platforms_confirmed ?? 0}</strong><span>platforms confirmed</span></article>
            <article><strong>{intelligence.web_research.dates_confirmed ?? 0}</strong><span>dates confirmed</span></article>
            <article><strong>{intelligence.web_research.needs_review ?? 0}</strong><span>manual queue</span></article>
          </div>
          {intelligence.web_research.last_error && <p className="admin-safe-error">{intelligence.web_research.last_error}</p>}
        </section>}
        <h2>Independent provider health</h2>
        <div className="admin-source-grid">{intelligence.providers.map((provider) => <article key={provider.provider}><header><h3>{provider.provider.replaceAll("_", " ")}</h3><Status>{provider.status}</Status></header><p>{provider.enabled ? "Enabled" : "Disabled"} · {provider.requests} calls · {provider.latency_ms ?? "—"} ms</p><p>Success: {provider.success_rate ?? "—"}% · Matches/call: {provider.match_rate ?? "—"}%</p>{provider.last_error && <p className="admin-safe-error">{provider.last_error}</p>}</article>)}</div>
        <h2>Source agreement</h2>
        <div className="admin-source-grid">{Object.entries(intelligence.source_agreement || {}).map(([source, value]) => <article key={source}><h3>{source.replaceAll("_", " ")}</h3><p>Platform agreement: {value.platform_agreement ?? "Not measured"}{value.platform_agreement != null && "%"}</p><p>Date agreement: {value.date_agreement ?? "Not measured"}{value.date_agreement != null && "%"}</p><small>{value.platform_compared} platform comparisons · {value.date_compared} date comparisons</small></article>)}</div>
      </>}
      <select
        value={status}
        onChange={(event) => setStatus(event.target.value)}
      >
        <option value="">All states</option>
        <option value="ELIGIBLE">Eligible</option>
        <option value="WAITING_RELEASE">Waiting for release</option>
        <option value="UNKNOWN">Unknown</option>
        <option value="RESEARCHING">Researching</option>
        <option value="POSSIBLE">Possible</option>
        <option value="CONFIRMED">Confirmed</option>
        <option value="NOT_FOUND">Not found</option>
        <option value="CONFLICTING">Conflicting</option>
        <option value="NEEDS_REVIEW">Needs review</option>
      </select>
      {actionError && <p className="admin-error" role="alert">{actionError}</p>}
      {data && (
        <>
          <div className="metrics">
            <article>
              <strong>{data.daily_usage.remaining}</strong>
              <span>Daily movie slots remaining</span>
            </article>
            <article>
              <strong>{data.tavily_usage.remaining}</strong>
              <span>Tavily application credits remaining</span>
            </article>
            <article>
              <strong>{data.coverage?.movies_with_confirmed_ott_date ?? 0}</strong>
              <span>Confirmed OTT dates</span>
            </article>
            <article>
              <strong>{data.coverage?.movies_with_platform_but_missing_date ?? 0}</strong>
              <span>Platform known, date unknown</span>
            </article>
            <article>
              <strong>{data.coverage?.movies_with_conflicting_evidence ?? 0}</strong>
              <span>Conflicting</span>
            </article>
            <article>
              <strong>{data.coverage?.movies_needing_review ?? 0}</strong>
              <span>Needs review</span>
            </article>
          </div>
          {(command?.by_language || overview?.by_language) && <><h2>OTT coverage by language</h2><div className="admin-language-coverage">{(command?.by_language || overview.by_language).map((item) => <article key={item.code}><h3>{item.language}</h3><dl><div><dt>Movies</dt><dd>{item.movies}</dd></div><div><dt>Platform known</dt><dd>{item.platform_known}</dd></div><div><dt>Confirmed date</dt><dd>{item.date_confirmed ?? item.confirmed_date}</dd></div><div><dt>Unknown</dt><dd>{item.unknown ?? item.missing}</dd></div>{item.conflicts != null && <div><dt>Conflicts</dt><dd>{item.conflicts}</dd></div>}</dl></article>)}</div></>}
          {releases?.items?.some((item) => Object.hasOwn(item, "source_count")) && <><h2>OTT release records</h2><div className="admin-release-grid">{releases.items.map((item) => <article key={item.id}><img src={imageUrl(item.poster)} alt="" loading="lazy" /><div><Link to={`/movies/${item.movie_id}`}><strong>{item.movie}</strong></Link><p>{item.language || "Unknown language"} · {item.platform}</p><p>{item.ott_release_date || "Date unknown"}</p><Status>{item.status}</Status><button onClick={() => inspect(item.movie_id)}>Evidence ({item.source_count})</button></div></article>)}</div></>}
          <Table
            headers={[
              "Movie / theatrical release",
              "Release / eligibility",
              "Research status",
              "OTT platform / date",
              "Source / confidence",
              "Attempts / checks",
              "Actions",
            ]}
            rows={data.items.map((item) => (
              <tr key={item.movie_id}>
                <td>
                  <Link to={`/movies/${item.movie_id}`}>{item.movie}</Link>
                  <small>{item.original_language || "Unknown language"}</small>
                  <small>{item.theatrical_release_date || "Unknown"}</small>
                </td>
                <td>
                  {item.release_status}
                  <small>{item.eligibility_label}</small>
                </td>
                <td>
                  {item.status.replaceAll("_", " ")}
                  <small>{(item.verification_status || "UNKNOWN").replaceAll("_", " ")}</small>
                </td>
                <td>
                  {item.platform || "Not announced"}
                  <small>{item.date || "Not announced"}</small>
                </td>
                <td>
                  {item.url ? (
                    <a href={item.url} rel="noreferrer">
                      {item.source || "Source"}
                    </a>
                  ) : (
                    item.source || "—"
                  )}
                  <small>{item.confidence}% confidence</small>
                  <small>{item.sources ?? 0} evidence source{item.sources === 1 ? "" : "s"}</small>
                </td>
                <td>
                  {item.attempts}
                  <small>Last: {item.last_check || "never"}</small>
                  <small>Next: {item.next_check || "unscheduled"}</small>
                </td>
                <td>
                  {item.id && item.eligibility === "ELIGIBLE" && (
                    <>
                      <button onClick={() => action(item.id, "requeue")}>
                        Requeue
                      </button>{" "}
                      <button onClick={() => action(item.id, "retry")}>
                        Retry
                      </button>{" "}
                    </>
                  )}
                  {item.id && (
                    <button onClick={() => action(item.id, "needs_review")}>
                      Review
                    </button>
                  )}
                  <button onClick={() => inspect(item.movie_id)}>Inspect evidence</button>
                </td>
              </tr>
            ))}
          />
          {detail && (
            <section className="admin-evidence" aria-label="OTT evidence detail">
              <header>
                <h2>{detail.movie.title}: evidence</h2>
                <button onClick={() => setDetail(null)}>Close</button>
              </header>
              <p>{detail.movie.original_language || "Unknown language"} · Theatrical {detail.movie.theatrical_release_date || "unknown"}</p>
              <h3>Current canonical result</h3>
              <p>{detail.canonical ? `${detail.canonical.platform} · ${detail.canonical.ott_release_date || "date not confirmed"} · ${detail.canonical.verification_status} · ${detail.canonical.confidence}%` : "No canonical OTT availability"}</p>
              <div className="admin-evidence-list">
                {detail.evidence.length ? detail.evidence.map((item) => (
                  <article key={item.id}>
                    <strong>{item.source_name || item.source_type}</strong>
                    <p>{item.fact_type || "EVIDENCE"} · {item.platform_found || "No platform"} · {item.release_date_found || "No date"} · {item.result_status}</p>
                    <p>Match {item.movie_match_confidence}% · Platform {item.platform_confidence}% · Date {item.date_confidence}%</p>
                    <p>{item.evidence_summary || "No summary"}</p>
                    <a href={item.source_url} rel="noreferrer">Open source</a>
                    {!item.rejected_at && (
                      <>
                        <button onClick={() => evidenceAction(item.id, "trust")}>Mark trusted</button>
                        <button onClick={() => evidenceAction(item.id, "reject")}>Reject</button>
                      </>
                    )}
                    {item.rejected_at && <small>Rejected: {item.rejection_reason || "No reason supplied"}</small>}
                  </article>
                )) : <p className="empty">No inspected source evidence yet.</p>}
              </div>
              {detail.decisions?.length > 0 && <><h3>Reconciliation decisions</h3><div className="admin-evidence-list">{detail.decisions.map((item) => <article key={item.id}><header><strong>{item.platform || "No platform"}</strong><Status>{item.state}</Status></header><p>{item.release_date || "Date not confirmed"} · Health {item.health_score}/100</p><p>{item.reason}</p><small>Supporting evidence: {(item.supporting_evidence_ids || []).join(", ") || "none"}</small></article>)}</div></>}
              {detail.observations?.length > 0 && <><h3>Availability observations</h3><div className="admin-evidence-list">{detail.observations.map((item) => <article key={item.id}><strong>{item.provider || "No India availability returned"}</strong><p>{item.availability_type} · {item.available ? "Available" : "Not observed"} · {when(item.observed_at)}</p><small>{item.source_type}</small></article>)}</div></>}
              <form onSubmit={verify} className="admin-manual-ott">
                <h3>Manual verification</h3>
                <label>Platform<input required value={manual.platform} onChange={(event) => setManual({ ...manual, platform: event.target.value })} /></label>
                <label>OTT release date (optional when only platform is verified)<input type="date" value={manual.ott_release_date} onChange={(event) => setManual({ ...manual, ott_release_date: event.target.value })} /></label>
                <label>Source URL<input required type="url" pattern="https://.*" value={manual.source_url} onChange={(event) => setManual({ ...manual, source_url: event.target.value })} /></label>
                <label>Source name<input value={manual.source_name} onChange={(event) => setManual({ ...manual, source_name: event.target.value })} /></label>
                <label>Country<input required value={manual.country} onChange={(event) => setManual({ ...manual, country: event.target.value.toUpperCase() })} /></label>
                <label>Verification notes<textarea value={manual.summary} onChange={(event) => setManual({ ...manual, summary: event.target.value })} /></label>
                <button type="submit">Mark confirmed</button>
              </form>
            </section>
          )}
        </>
      )}
    </Page>
  );
}

function GoldSetCase({ item, onSaved }) {
  const [message, setMessage] = useState("");
  const submit = (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    call(`/api/v1/admin/ott-gold-set/${item.id}`, json("PATCH", {
      expected_platform: form.get("expected_platform") || null,
      expected_release_date: form.get("expected_release_date") || null,
      expected_availability_type: form.get("expected_availability_type") || null,
      expected_state: form.get("expected_state"),
      source_url: form.get("source_url") || null,
      notes: form.get("notes") || null,
    })).then(() => { setMessage("Ground truth saved"); onSaved(); }).catch((reason) => setMessage(reason.message));
  };
  return <article className="admin-panel">
    <header><div><h2>{item.movie}</h2><p>{item.year || "Unknown year"} · {item.language} · {item.category}</p></div><Status>{item.manually_verified_at ? "VERIFIED" : "UNVERIFIED"}</Status></header>
    <Link to={`/movies/${item.movie_id}`}>Open movie</Link>
    <form className="admin-manual-ott" onSubmit={submit}>
      <label>Expected platform<input name="expected_platform" defaultValue={item.expected_platform || ""} /></label>
      <label>Expected OTT date<input name="expected_release_date" type="date" defaultValue={item.expected_release_date || ""} /></label>
      <label>Availability type<select name="expected_availability_type" defaultValue={item.expected_availability_type || ""}><option value="">Unknown</option>{["SUBSCRIPTION", "FREE", "ADS", "RENT", "BUY", "CHANNEL"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>Expected state<select name="expected_state" defaultValue={item.expected_state}>{["UNKNOWN", "PLATFORM_ONLY", "UPCOMING_CONFIRMED", "RELEASED_CONFIRMED", "NOT_FOUND"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>Trusted source URL<input name="source_url" type="url" pattern="https://.*" defaultValue={item.source_url || ""} /></label>
      <label>Manual verification notes<textarea name="notes" defaultValue={item.notes || ""} /></label>
      <button>Save verified ground truth</button>
    </form>
    {message && <p role="status">{message}</p>}
  </article>;
}

export function OttGoldSet() {
  const [language, setLanguage] = useState("");
  const [verified, setVerified] = useState("");
  const [page, setPage] = useState(1);
  const [data, error, reload] = useAdmin(`/api/v1/admin/ott-gold-set?${new URLSearchParams({ page: String(page), page_size: "25", ...(language && { language }), ...(verified && { verified }) })}`);
  const [message, setMessage] = useState("");
  const generate = () => call("/api/v1/admin/ott-gold-set/generate", { method: "POST" }).then((value) => { setMessage(`${value.total}/${value.target} gold-set movies selected`); reload(); }).catch((reason) => setMessage(reason.message));
  if (error) return <Guard error={error} />;
  return <Page title="OTT accuracy gold set">
    <p>These cases must be manually verified before the new multi-provider engine may publish automatically. Unknown is a valid expected result.</p>
    <div className="toolbar"><select aria-label="Language" value={language} onChange={(event) => { setLanguage(event.target.value); setPage(1); }}><option value="">All languages</option>{[["ml", "Malayalam"], ["ta", "Tamil"], ["te", "Telugu"], ["hi", "Hindi"], ["kn", "Kannada"]].map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select><select aria-label="Verification state" value={verified} onChange={(event) => { setVerified(event.target.value); setPage(1); }}><option value="">All cases</option><option value="false">Unverified</option><option value="true">Verified</option></select><button onClick={generate}>Create / complete 100-movie set</button></div>
    {message && <p role="status">{message}</p>}
    {data?.accuracy && <section className="admin-panel"><header><h2>Accuracy gate</h2><Status>{data.accuracy.gate_passed ? "PASSED" : "BLOCKED"}</Status></header><p>{data.accuracy.verified}/{data.accuracy.target} verified · Platform precision {data.accuracy.platform_precision == null ? "not measured" : `${(data.accuracy.platform_precision * 100).toFixed(1)}%`} · Date precision {data.accuracy.date_precision == null ? "not measured" : `${(data.accuracy.date_precision * 100).toFixed(1)}%`} · False dates {data.accuracy.false_dates}</p></section>}
    {!data ? <p>Loading…</p> : <div className="admin-source-grid">{data.items.map((item) => <GoldSetCase key={item.id} item={item} onSaved={reload} />)}</div>}
    <Pager data={data} onPage={setPage} />
  </Page>;
}

export function Movies() {
  const initial = new URLSearchParams(useLocation().search);
  const [search, setSearch] = useState(initial.get("search") || ""),
    [language, setLanguage] = useState(initial.get("language") || ""),
    [year, setYear] = useState(initial.get("year") || ""),
    [platform, setPlatform] = useState(initial.get("platform") || ""),
    [ott, setOtt] = useState(initial.get("ott") || ""),
    [trailer, setTrailer] = useState(initial.get("trailer") || ""),
    [imdb, setImdb] = useState(initial.get("imdb") || ""),
    [poster, setPoster] = useState(initial.get("poster") || ""),
    [metadata, setMetadata] = useState(initial.get("metadata") || ""),
    [sort, setSort] = useState(initial.get("sort") || "updated"),
    [query, setQuery] = useState(initial.toString()),
    [message, setMessage] = useState("");
  const [data, error, reload] = useAdmin(`/api/v1/admin/movies?${query}`);
  const apply = (page = 1) => {
    const params = new URLSearchParams();
    Object.entries({ search, language, year, platform, ott, trailer, imdb, poster, metadata, sort, page }).forEach(([key, value]) => value && params.set(key, value));
    setQuery(params.toString());
  };
  const repair = (id, label = "Repair") => call(`/api/v1/admin/movies/${id}/repair`, { method: "POST" }).then(() => setMessage(`${label} queued for movie ${id}`)).catch((reason) => setMessage(reason.message));
  const research = (id) => call(`/api/v1/admin/movies/${id}/research-ott`, { method: "POST" }).then(() => setMessage(`OTT research queued for movie ${id}`)).catch((reason) => setMessage(reason.message));
  if (error) return <Guard error={error} />;
  return <Page title="Movies">
    <form className="admin-filter-grid" onSubmit={(event) => { event.preventDefault(); apply(); }}>
      <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Title, local ID, TMDB ID or IMDb ID" />
      <select value={language} onChange={(event) => setLanguage(event.target.value)}><option value="">All languages</option><option value="ml">Malayalam</option><option value="ta">Tamil</option><option value="te">Telugu</option><option value="hi">Hindi</option><option value="kn">Kannada</option></select>
      <input type="number" min="1888" max="2100" value={year} onChange={(event) => setYear(event.target.value)} placeholder="Year" />
      <input value={platform} onChange={(event) => setPlatform(event.target.value)} placeholder="OTT platform" />
      <select value={ott} onChange={(event) => setOtt(event.target.value)}><option value="">Any OTT state</option><option value="confirmed">OTT confirmed</option><option value="missing">OTT missing</option><option value="needs_review">OTT needs review</option></select>
      <select value={trailer} onChange={(event) => setTrailer(event.target.value)}><option value="">Any trailer state</option><option value="missing">Trailer missing</option></select>
      <select value={imdb} onChange={(event) => setImdb(event.target.value)}><option value="">Any IMDb state</option><option value="missing">IMDb missing</option></select>
      <select value={poster} onChange={(event) => setPoster(event.target.value)}><option value="">Any poster state</option><option value="missing">Poster missing</option></select>
      <select value={metadata} onChange={(event) => setMetadata(event.target.value)}><option value="">Any metadata state</option><option value="incomplete">Metadata incomplete</option></select>
      <select value={sort} onChange={(event) => setSort(event.target.value)}><option value="updated">Recently updated</option><option value="newest">Recently added</option><option value="oldest">Oldest added</option><option value="title">Title</option><option value="year">Release year</option></select>
      <button>Apply filters</button><button type="button" onClick={reload}>Refresh</button>
    </form>
    {message && <p role="status">{message}</p>}
    {!data ? <p>Loading…</p> : <><p>{data.total} movies</p><div className="admin-movie-grid">{data.items.map((movie) => <article key={movie.id}><img src={imageUrl(movie.poster_path)} alt={`${movie.title} poster`} loading="lazy" /><div><header><div><h2>{movie.title}</h2>{movie.original_title && movie.original_title !== movie.title && <small>{movie.original_title}</small>}</div><Status>{movie.metadata_health}</Status></header><dl className="admin-request-facts"><div><dt>Year / language</dt><dd>{movie.year || "Unknown"} · {movie.language || "Unknown"}</dd></div><div><dt>TMDB / IMDb</dt><dd>{movie.tmdb_id} · {movie.imdb_id || "Missing"}</dd></div><div><dt>IMDb rating</dt><dd>{movie.imdb_rating ?? "Missing"}</dd></div><div><dt>Theatrical</dt><dd>{movie.theatrical_date || "Unknown"}</dd></div><div><dt>OTT</dt><dd>{movie.ott_platform || "Missing"} · {movie.ott_release_date || "Date unknown"}</dd></div><div><dt>Trailer</dt><dd>{movie.trailer ? "Available" : "Missing"}</dd></div><div><dt>Image health</dt><dd>{movie.image_health}</dd></div><div><dt>Updated</dt><dd>{when(movie.updated_at)}</dd></div></dl>{movie.metadata_missing?.length > 0 && <p className="admin-safe-error">Missing: {movie.metadata_missing.join(", ")}</p>}<div className="admin-request-actions"><Link className="button-link" to={`/movies/${movie.id}`}>Open movie</Link><button onClick={() => repair(movie.id, "Metadata refresh")}>Refresh metadata</button><button onClick={() => research(movie.id)}>Research OTT</button><button onClick={() => repair(movie.id, "Trailer search")}>Find trailer</button><button onClick={() => repair(movie.id, "Image refresh")}>Refresh images</button><button onClick={() => repair(movie.id, "IMDb refresh")}>Refresh IMDb</button>{movie.trailer_key && <a className="button-link" href={`https://www.youtube.com/watch?v=${encodeURIComponent(movie.trailer_key)}`} rel="noreferrer">YouTube</a>}</div></div></article>)}</div><Pager data={data} onPage={apply} /></>}
  </Page>;
}

export function Sources() {
  const initial = new URLSearchParams(useLocation().search);
  const [selected, setSelected] = useState(initial.get("source") || "ottplay");
  const [data, error, reload] = useAdmin("/api/v1/admin/sources", 15000);
  const [releases, releaseError, reloadReleases] = useAdmin(`/api/v1/admin/sources/${selected}/releases?status=UNMATCHED`, 15000);
  const [message, setMessage] = useState("");
  const run = (source) => call(`/api/v1/admin/sources/${source}/run`, { method: "POST" }).then(() => { setMessage(`${source} sync queued`); reload(); }).catch((reason) => setMessage(reason.message));
  const updateRelease = (id, action, movieId = null) => call(`/api/v1/admin/sources/releases/${id}`, json("PATCH", { action, movie_id: movieId })).then(() => { setMessage(`Release marked ${action}`); reloadReleases(); reload(); }).catch((reason) => setMessage(reason.message));
  const emailAction = (path) => call(path, { method: "POST" }).then((result) => { setMessage(result.sent ? "Email sent" : `${result.attempted || 0} failed emails attempted`); reload(); }).catch((reason) => setMessage(reason.message));
  if (error || releaseError) return <Guard error={error || releaseError} />;
  return <Page title="Integration and source health">
    {message && <p role="status">{message}</p>}
    <div className="admin-source-grid">{(data?.items || []).map((source) => <article key={source.source}><header><h2>{source.label}</h2><Status>{sourceStatus(source)}</Status></header><dl><div><dt>Enabled</dt><dd>{source.enabled ? "Yes" : "No"}</dd></div>{source.integration_mode && <div><dt>Mode</dt><dd>{source.integration_mode}</dd></div>}<div><dt>Last check</dt><dd>{when(source.last_check)}</dd></div><div><dt>Last success</dt><dd>{when(source.last_success)}</dd></div><div><dt>Next run</dt><dd>{when(source.next_run)}</dd></div></dl>{source.stats && <dl>{Object.entries(source.stats).map(([label, value]) => <div key={label}><dt>{label.replaceAll("_", " ")}</dt><dd>{value}</dd></div>)}</dl>}{source.usage && <p>{source.usage.used} of {source.usage.limit} application credits used</p>}{source.last_error && <p className="admin-safe-error">{source.last_error}</p>}{["ottplay", "justwatch"].includes(source.source) && <div className="admin-request-actions"><button disabled={!source.configured || ["RUNNING", "QUEUED"].includes(source.status)} onClick={() => run(source.source)}>Run sync</button><button onClick={() => setSelected(source.source)}>View unmatched</button></div>}</article>)}</div>
    <section className="admin-panel"><h2>Email health</h2>{data?.email && <><div className="metrics">{Object.entries(data.email).filter(([, value]) => typeof value === "number").map(([label, value]) => <article key={label}><strong>{value}</strong><span>{label.replaceAll("_", " ")}</span></article>)}</div><p>SMTP: <Status>{data.email.smtp_configured ? "CONFIGURED" : "NOT_CONFIGURED"}</Status> · Last success: {when(data.email.last_successful_email)}</p><div className="admin-request-actions"><button onClick={() => emailAction("/api/v1/admin/email/retry-failed")}>Retry failed emails</button><button onClick={() => emailAction("/api/v1/admin/email/test")}>Send test email</button></div></>}</section>
    <section className="admin-panel"><h2>{selected === "ottplay" ? "OTTplay" : "JustWatch"} unmatched releases</h2><div className="toolbar"><button className={selected === "ottplay" ? "active" : ""} onClick={() => setSelected("ottplay")}>OTTplay</button><button className={selected === "justwatch" ? "active" : ""} onClick={() => setSelected("justwatch")}>JustWatch</button></div>{!releases ? <p>Loading…</p> : releases.items.length ? <div className="admin-unmatched-list">{releases.items.map((item) => <article key={item.id}><header><div><h3>{item.title}</h3><p>{item.language || "Unknown language"} · {item.platform || "Unknown platform"} · {item.date || "Date unknown"}</p></div><Status>{item.status}</Status></header>{item.source_url && <a href={item.source_url} rel="noreferrer">Open source</a>}<h4>Potential local matches</h4><div className="admin-match-list">{item.potential_matches.map((movie) => <button key={movie.id} onClick={() => updateRelease(item.id, "match", movie.id)}>{movie.title} ({movie.year || "?"}, {movie.language || "?"})</button>)}{!item.potential_matches.length && <span>No likely title match.</span>}</div><div className="admin-request-actions"><button onClick={() => updateRelease(item.id, "ignore")}>Ignore</button><button onClick={() => updateRelease(item.id, "tv_series")}>Mark TV / Series</button><button onClick={() => updateRelease(item.id, "duplicate")}>Duplicate</button></div></article>)}</div> : <p className="empty">No unmatched releases from this configured adapter.</p>}</section>
  </Page>;
}

export function SystemHealth() {
  const [health, healthError, reload] = useAdmin("/api/v1/admin/system-health", 30000);
  const [audit, auditError] = useAdmin("/api/v1/admin/audit");
  const error = healthError || auditError;
  if (error) return <Guard error={error} />;
  return <Page title="System health"><button onClick={reload}>Refresh health</button><div className="admin-source-grid">{(health?.services || []).map((service) => <article key={service.name}><header><h2>{service.name}</h2><Status>{service.status}</Status></header><p>Last heartbeat: {when(service.last_heartbeat)}</p>{service.queue_depth != null && <p>Queue depth: {service.queue_depth}</p>}{service.last_error && <p className="admin-safe-error">{service.last_error}</p>}</article>)}</div><h2>Administrator audit trail</h2>{audit && <Table headers={["Timestamp", "Action", "Target", "Summary"]} rows={audit.items.map((item) => <tr key={item.id}><td>{when(item.timestamp)}</td><td>{item.action.replaceAll("_", " ")}</td><td>{item.target_type} {item.target_id || ""}</td><td>{item.summary || "—"}</td></tr>)} />}</Page>;
}

export function Jobs() {
  const initial = new URLSearchParams(useLocation().search);
  const [status, setStatus] = useState(initial.get("status") || "");
  const [jobs, jobsError, reloadJobs] = useAdmin("/api/v1/admin/jobs");
  const [backfills, backfillError, reloadBackfills] = useAdmin(
    "/api/v1/admin/backfills",
  );
  const [message, setMessage] = useState(""),
    [movieId, setMovieId] = useState("");
  const start = (operation) =>
    call(`/api/v1/admin/backfills/${operation}/start`, { method: "POST" })
      .then((result) => {
        setMessage(result.detail || `${result.task} queued`);
        reloadJobs();
        reloadBackfills();
      })
      .catch((reason) => setMessage(reason.message));
  const repair = (event) => {
    event.preventDefault();
    call(`/api/v1/admin/movies/${movieId}/repair`, { method: "POST" })
      .then(() => setMessage(`Movie ${movieId} repair queued`))
      .catch((reason) => setMessage(reason.message));
  };
  const error = jobsError || backfillError;
  if (error) return <Guard error={error} />;
  const labels = {
    metadata: "Metadata",
    people: "People",
    images: "Images",
    "imdb-ids": "IMDb ID recovery",
    imdb: "IMDb ratings",
    ott: "OTT queue",
    all: "Run sequential repair",
  };
  return (
    <Page title="Job status">
      <h2>Accelerated data repair</h2>
      <p>
        Each action resumes its persistent checkpoint. Completed backfills do
        not restart.
      </p>
      <div className="toolbar">
        {Object.entries(labels).map(([key, label]) => (
          <button key={key} onClick={() => start(key)}>
            {label}
          </button>
        ))}
      </div>
      <form className="toolbar" onSubmit={repair}>
        <input
          aria-label="Movie database ID"
          type="number"
          min="1"
          required
          value={movieId}
          onChange={(event) => setMovieId(event.target.value)}
          placeholder="Movie database ID"
        />
        <button>Repair one movie</button>
      </form>
      {message && <p role="status">{message}</p>}
      {backfills && (
        <>
          <h2>Backfill progress</h2>
          <Table
            headers={[
              "Operation",
              "Status",
              "Processed / total",
              "Remaining",
              "Failures",
              "Last success",
              "Last error",
            ]}
            rows={backfills.progress.map((item) => (
              <tr key={item.operation}>
                <td>{item.operation}</td>
                <td>{item.status}</td>
                <td>
                  {item.processed} / {item.total || "not counted"}
                </td>
                <td>{item.remaining ?? "—"}</td>
                <td>{item.failed}</td>
                <td>{item.last_success || "Never"}</td>
                <td>{item.last_error || "—"}</td>
              </tr>
            ))}
          />
          <h2>Database coverage</h2>
          <div className="metrics">
            {Object.entries(backfills.coverage).map(([label, value]) => (
              <article key={label}>
                <strong>{value}</strong>
                <span>{label.replaceAll("_", " ")}</span>
              </article>
            ))}
          </div>
          <h2>Provider configuration</h2>
          <div className="metrics">
            {Object.entries(backfills.configuration).map(([label, value]) => (
              <article key={label}>
                <strong>{value ? "Ready" : "Missing"}</strong>
                <span>{label.replaceAll("_", " ")}</span>
              </article>
            ))}
          </div>
        </>
      )}
      {jobs && (
        <>
          <h2>All jobs</h2>
          <div className="toolbar">
            <select aria-label="Job status" value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="">All statuses</option>
              {["FAILED", "RUNNING", "QUEUED", "COMPLETED", "IDLE", "DISABLED"].map((value) => <option key={value}>{value}</option>)}
            </select>
          </div>
          <Table
            headers={[
              "Task",
              "Status",
              "Last success",
              "Last failure",
              "Last error",
              "Cursor / processed / total",
              "Progress",
            ]}
            rows={jobs.filter((item) => !status || item.status === status).map((item) => (
              <tr key={item.task}>
                <td>{item.task}</td>
                <td>{item.status}</td>
                <td>{item.last_success || "Never"}</td>
                <td>{item.last_failure || "—"}</td>
                <td>{item.last_error || "—"}</td>
                <td>
                  {item.cursor} / {item.processed_count} /{" "}
                  {item.total_count || "—"}
                </td>
                <td>{item.progress}</td>
              </tr>
            ))}
          />
        </>
      )}
    </Page>
  );
}

export function Notifications() {
  const [channel, setChannel] = useState(""),
    [severity, setSeverity] = useState("");
  const [data, error] = useAdmin(
    `/api/v1/admin/notifications?channel=${channel}&severity=${severity}`,
  );
  if (error) return <Guard error={error} />;
  return (
    <Page title="Notifications">
      <div className="toolbar">
        <input
          value={channel}
          onChange={(event) => setChannel(event.target.value)}
          placeholder="Channel"
        />
        <input
          value={severity}
          onChange={(event) => setSeverity(event.target.value)}
          placeholder="Severity"
        />
      </div>
      {data && (
        <Table
          headers={[
            "Timestamp",
            "Channel",
            "Severity",
            "Message",
            "Fingerprint",
            "Status",
          ]}
          rows={data.items.map((item) => (
            <tr key={item.id}>
              <td>{item.timestamp}</td>
              <td>{item.channel}</td>
              <td>{item.severity}</td>
              <td>{item.message}</td>
              <td>{item.fingerprint}</td>
              <td>{item.status}</td>
            </tr>
          ))}
        />
      )}
    </Page>
  );
}
