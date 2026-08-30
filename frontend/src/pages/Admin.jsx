import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
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
    <Link to="/admin/comments">Comments</Link>
    <Link to="/admin/data-health">Data health</Link>
    <Link to="/admin/images">Images</Link>
    <Link to="/admin/ott-research">OTT research</Link>
    <Link to="/admin/jobs">Jobs</Link>
    <Link to="/admin/notifications">Notifications</Link>
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
          ["Total movies", data.total_movies],
          ["Movies with issues", data.movies_with_issues],
          ["Open issues", data.open_issues],
          ["Image issues", data.image_issues],
          ["Missing OTT", data.missing_ott],
          ["Conflicting OTT", data.conflicting_ott],
          ["OTT queue", data.ott_queue],
          ["Failed research", data.failed_research],
          ["Pending requests", data.pending_requests],
          ["Pending comments", data.pending_comments],
        ].map(([label, value]) => (
          <article key={label}>
            <strong>{value}</strong>
            <span>{label}</span>
          </article>
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
            <td>{job.last_success || "Never"}</td>
            <td>{job.last_failure || "—"}</td>
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

export function Requests() {
  const [status, setStatus] = useState(""),
    [search, setSearch] = useState(""),
    [query, setQuery] = useState("");
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
  if (error) return <Guard error={error} />;
  return (
    <Page title="Movie requests">
      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(
            new URLSearchParams({
              ...(search && { search }),
              ...(status && { status }),
            }).toString(),
          );
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
        <button>Filter</button>
        <button type="button" onClick={reload}>Refresh</button>
      </form>
      {actionError && <p className="admin-error" role="alert">{actionError}</p>}
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
                  <strong className="request-status">{item.status}</strong>
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
        </div>
      )}
    </Page>
  );
}

export function Comments() {
  const [status, setStatus] = useState("");
  const [actionError, setActionError] = useState();
  const [data, error, reload] = useAdmin(`/api/v1/admin/comments${status ? `?status=${status}` : ""}`, 15000);
  const action = (id, options) => {
    setActionError(undefined);
    call(`/api/v1/admin/comments/${id}`, options).then(reload).catch((reason) => setActionError(reason.message));
  };
  if (error) return <Guard error={error} />;
  return (
    <Page title="Comments">
      <div className="toolbar">
        <label>Status<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option>{["PENDING", "APPROVED", "HIDDEN", "REJECTED"].map((item) => <option key={item}>{item}</option>)}</select></label>
        <button type="button" onClick={reload}>Refresh</button>
      </div>
      {actionError && <p className="admin-error" role="alert">{actionError}</p>}
      {!data ? <p>Loading…</p> : data.items.length ? (
        <div className="admin-comment-list">
          {data.items.map((item) => (
            <article className="admin-comment-card" key={item.id}>
              <header><div><h2>{item.display_name}</h2><Link to={`/movies/${item.movie_id}`}>{item.movie_title}</Link></div><strong className="request-status">{item.status}</strong></header>
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
  const [status, setStatus] = useState(""),
    [data, error, reload] = useAdmin(
      `/api/v1/admin/ott-research?status=${status}`,
    );
  const [detail, setDetail] = useState(null);
  const [actionError, setActionError] = useState("");
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
      json("POST", manual),
    )
      .then(() => inspect(detail.movie.id))
      .then(reload)
      .catch((reason) => setActionError(reason.message));
  };
  if (error) return <Guard error={error} />;
  return (
    <Page title="OTT research">
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
                    <p>{item.platform_found || "No platform"} · {item.release_date_found || "No date"} · {item.result_status} · {item.confidence}%</p>
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
              <form onSubmit={verify} className="admin-manual-ott">
                <h3>Manual verification</h3>
                <label>Platform<input required value={manual.platform} onChange={(event) => setManual({ ...manual, platform: event.target.value })} /></label>
                <label>OTT release date<input required type="date" value={manual.ott_release_date} onChange={(event) => setManual({ ...manual, ott_release_date: event.target.value })} /></label>
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

export function Jobs() {
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
            rows={jobs.map((item) => (
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
