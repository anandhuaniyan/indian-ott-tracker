import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import Seo from "../components/Seo";
import { API, userGet } from "../services/api";
import { Nav } from "./Admin";

const request = async (path, options = {}) => {
  const response = await fetch(`${API}${path}`, { credentials: "include", ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "Request failed");
  return payload;
};
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const money = (value, currency) => new Intl.NumberFormat(undefined, { style: "currency", currency }).format(Number(value));
const when = value => value ? new Date(value).toLocaleString() : "Not recorded";

export function SupportPage() {
  const [summary, setSummary] = useState();
  const [history, setHistory] = useState();
  const [paynow, setPaynow] = useState();
  const [error, setError] = useState("");
  useEffect(() => {
    let current = true;
    Promise.all([userGet("/api/v1/support/me"), userGet("/api/v1/support/history")])
      .then(([nextSummary, nextHistory]) => { if (current) { setSummary(nextSummary); setHistory(nextHistory); } })
      .catch(reason => current && setError(reason.message));
    return () => { current = false; };
  }, []);
  const loadPaynow = () => userGet("/api/v1/support/paynow").then(setPaynow).catch(reason => setError(reason.message));
  return <main className="support-page"><Seo title="Support" noindex /><header><p className="eyebrow">Private account area</p><h1>Support OTT Tracker</h1><p>Support eligibility and contribution records are managed separately from all media-access entitlements.</p></header>{error && <p className="user-auth-error" role="alert">{error}</p>}{!summary && !error && <p>Loading…</p>}{summary && <><section className="support-card"><h2>Support status</h2><p><strong>{summary.enabled ? "Enabled" : "Not enabled"}</strong></p>{summary.last_contribution ? <dl className="support-facts"><div><dt>Last contribution</dt><dd>{money(summary.last_contribution.amount, summary.last_contribution.currency)}</dd></div><div><dt>Date</dt><dd>{when(summary.last_contribution.payment_date)}</dd></div><div><dt>Method</dt><dd>{summary.last_contribution.payment_method}</dd></div><div><dt>Status</dt><dd>{summary.last_contribution.status}</dd></div></dl> : <p>No contributions are recorded.</p>}<button type="button" className="secondary" onClick={loadPaynow}>View PayNow instructions</button>{paynow && <div className="paynow-panel"><h3>PayNow</h3>{paynow.qr_url && <img src={paynow.qr_url} alt="Configured PayNow QR code" width="240" height="240" />}<p>{paynow.instructions}</p>{paynow.recipient_label && <p><strong>Recipient:</strong> {paynow.recipient_label}</p>}</div>}</section><section className="support-card"><h2>Contribution history</h2><div className="table-scroll"><table><thead><tr><th>Date</th><th>Amount</th><th>Method</th><th>Status</th></tr></thead><tbody>{history?.items?.map(item => <tr key={item.reference}><td>{when(item.payment_date)}</td><td>{money(item.amount, item.currency)}</td><td>{item.payment_method}</td><td>{item.status}</td></tr>)}</tbody></table></div>{!history?.items?.length && <p>No contribution history yet.</p>}</section></>}</main>;
}

export function AdminSupportPage() {
  const [data, setData] = useState();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState();
  const [form, setForm] = useState({ amount: "", currency: "SGD", payment_method: "PayNow", status: "CONFIRMED", reference: "", admin_note: "" });
  const [message, setMessage] = useState("");
  const load = (term = search) => request(`/api/v1/admin/support/overview?search=${encodeURIComponent(term)}`).then(setData).catch(reason => setMessage(reason.message));
  useEffect(() => { load(""); }, []);
  const mutateEligibility = user => request(`/api/v1/admin/support/users/${user.user_id}/eligibility`, json("POST", { enabled: !user.support_enabled })).then(() => { setMessage("Support eligibility updated."); load(); });
  const select = user => request(`/api/v1/admin/support/users/${user.user_id}`).then(setSelected).catch(reason => setMessage(reason.message));
  const addContribution = event => {
    event.preventDefault();
    request(`/api/v1/admin/support/users/${selected.user.user_id}/contributions`, json("POST", form))
      .then(() => select(selected.user).then(() => { setMessage("Contribution recorded."); setForm(current => ({ ...current, amount: "", reference: "", admin_note: "" })); }))
      .catch(reason => setMessage(reason.message));
  };
  const updateContribution = (item, status) => request(`/api/v1/admin/support/contributions/${item.reference}`, json("PATCH", { status }))
    .then(() => select(selected.user).then(() => setMessage("Contribution status updated.")))
    .catch(reason => setMessage(reason.message));
  return <main><Seo title="Support management" noindex /><h1>Support Management</h1><Nav />{message && <p className="admin-message" role="status">{message}</p>}<form className="admin-form support-search" onSubmit={event => { event.preventDefault(); load(); }}><label>Search users<input type="search" value={search} onChange={event => setSearch(event.target.value)} /></label><button type="submit">Search</button></form><div className="table-scroll"><table><thead><tr><th>User</th><th>Verified</th><th>Support</th><th>Contributions</th><th>Actions</th></tr></thead><tbody>{data?.items?.map(user => <tr key={user.user_id}><td>{user.display_name || user.email}<small>{user.email}</small></td><td>{user.email_verified ? "Yes" : "No"}</td><td>{user.support_enabled ? "Enabled" : "Disabled"}</td><td>{user.contribution_count}</td><td><span className="admin-actions"><button type="button" className="secondary" onClick={() => select(user)}>View</button><button type="button" onClick={() => mutateEligibility(user)}>{user.support_enabled ? "Disable" : "Enable"}</button></span></td></tr>)}</tbody></table></div>{selected && <section className="support-admin-detail"><h2>{selected.user.display_name || selected.user.email}</h2><form className="admin-form" onSubmit={addContribution}><h3>Add contribution</h3><label>Amount<input type="number" min="0.01" step="0.01" required value={form.amount} onChange={event => setForm({ ...form, amount: event.target.value })} /></label><label>Currency<input maxLength="3" required value={form.currency} onChange={event => setForm({ ...form, currency: event.target.value.toUpperCase() })} /></label><label>Payment method<input required value={form.payment_method} onChange={event => setForm({ ...form, payment_method: event.target.value })} /></label><label>Status<select value={form.status} onChange={event => setForm({ ...form, status: event.target.value })}>{["PENDING", "CONFIRMED", "FAILED", "REFUNDED", "CANCELLED"].map(value => <option key={value}>{value}</option>)}</select></label><label>Reference (optional)<input value={form.reference} onChange={event => setForm({ ...form, reference: event.target.value })} /></label><label>Admin note (optional)<textarea value={form.admin_note} onChange={event => setForm({ ...form, admin_note: event.target.value })} /></label><button type="submit">Record contribution</button></form><h3>Contribution history</h3><div className="table-scroll"><table><thead><tr><th>Date</th><th>Amount</th><th>Method</th><th>Status</th><th>Note</th><th>Update</th></tr></thead><tbody>{selected.contributions.map(item => <tr key={item.reference}><td>{when(item.payment_date)}</td><td>{money(item.amount, item.currency)}</td><td>{item.payment_method}</td><td>{item.status}</td><td>{item.admin_note || "—"}</td><td><select aria-label={`Update ${item.reference} status`} value={item.status} onChange={event => updateContribution(item, event.target.value)}>{["PENDING", "CONFIRMED", "FAILED", "REFUNDED", "CANCELLED"].map(value => <option key={value}>{value}</option>)}</select></td></tr>)}</tbody></table></div><h3>Audit history</h3><ol className="support-audit-list">{selected.audit.map((item, index) => <li key={`${item.created_at}-${index}`}><strong>{item.action.replaceAll("_", " ")}</strong><span>{item.source} · {item.actor} · {when(item.created_at)}</span></li>)}</ol></section>}</main>;
}
