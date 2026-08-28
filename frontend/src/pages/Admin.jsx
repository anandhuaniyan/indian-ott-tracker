import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { API } from "../services/api";

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
const useAdmin = (path) => {
  const [data, setData] = useState(),
    [error, setError] = useState();
  const load = () =>
    call(path)
      .then(setData)
      .catch((reason) => setError(reason.message));
  useEffect(() => {
    load();
  }, [path]);
  return [data, error, load];
};

const Nav = () => (
  <nav className="admin-nav">
    <Link to="/admin">Dashboard</Link>
    <Link to="/admin/requests">Requests</Link>
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
  const [data, error, reload] = useAdmin(`/api/v1/admin/requests?${query}`);
  const update = (id, value) =>
    call(`/api/v1/admin/requests/${id}`, json("PATCH", { status: value })).then(
      reload,
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
      </form>
      {!data ? (
        <p>Loading…</p>
      ) : (
        <Table
          headers={["Request", "Contact", "Details", "Status"]}
          rows={data.items.map((item) => (
            <tr key={item.request_id}>
              <td>
                <strong>{item.movie_name}</strong>
                <small>
                  {item.release_year} {item.language}
                </small>
                {item.movie_external_id && <small>ID {item.movie_external_id}</small>}
                <small>{item.request_id}</small>
              </td>
              <td>{item.email}</td>
              <td>{item.details || "—"}</td>
              <td>
                <select
                  aria-label={`Status for ${item.movie_name}`}
                  value={item.status}
                  onChange={(event) =>
                    update(item.request_id, event.target.value)
                  }
                >
                  {["PENDING", "REVIEWING", "FOUND", "ADDED", "REJECTED"].map(
                    (value) => (
                      <option key={value}>{value}</option>
                    ),
                  )}
                </select>
              </td>
            </tr>
          ))}
        />
      )}
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
  const action = (id, value) =>
    call(
      `/api/v1/admin/ott-research/${id}/action`,
      json("POST", { action: value }),
    ).then(reload);
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
        <option value="RESEARCHING">Researching</option>
        <option value="CONFIRMED">Confirmed</option>
        <option value="NOT_FOUND">Not found</option>
        <option value="CONFLICTING">Conflicting</option>
      </select>
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
                  {item.movie}
                  <small>{item.theatrical_release_date || "Unknown"}</small>
                </td>
                <td>
                  {item.release_status}
                  <small>{item.eligibility_label}</small>
                </td>
                <td>{item.status.replaceAll("_", " ")}</td>
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
                </td>
              </tr>
            ))}
          />
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
