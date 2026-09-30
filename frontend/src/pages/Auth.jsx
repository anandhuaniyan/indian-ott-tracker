import React, { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import Seo from "../components/Seo";
import { userGet, userPost } from "../services/api";

const safeReturnTo = (value) => value?.startsWith("/") && !value.startsWith("//") ? value : "/";

const PasswordInput = ({ value, onChange, autoComplete, minLength = 1, label = "Password" }) => {
  const [visible, setVisible] = useState(false);
  return <label>{label}<span className="password-field"><input name="password" type={visible ? "text" : "password"} autoComplete={autoComplete} required minLength={minLength} maxLength="128" value={value} onChange={onChange} /><button type="button" className="secondary" aria-pressed={visible} onClick={() => setVisible(current => !current)}>{visible ? "Hide" : "Show"}</button></span></label>;
};

const AuthPage = ({ register }) => {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [form, setForm] = useState({ email: "", password: "", display_name: "" });
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const returnTo = safeReturnTo(params.get("returnTo"));
  const alternate = register ? "/login" : "/signup";
  const alternateParams = new URLSearchParams({ returnTo }).toString();

  useEffect(() => {
    if (register) return undefined;
    let current = true;
    Promise.resolve(userGet("/api/v1/auth/me"))
      .then((identity) => {
        if (!current || !identity) return;
        const destination = identity.role === "ADMIN"
          ? "/admin"
          : returnTo.startsWith("/admin") ? "/" : returnTo;
        navigate(destination, { replace: true });
      })
      .catch(() => {});
    return () => { current = false; };
  }, [navigate, register, returnTo]);

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    setNotice("");
    setSaving(true);
    try {
      const identity = await userPost(`/api/v1/auth/${register ? "register" : "login"}`, {
        email: form.email,
        password: form.password,
        ...(register && form.display_name.trim() ? { display_name: form.display_name.trim() } : {}),
      });
      if (register && identity.verification_required) {
        setNotice("Account created. Check your email for the verification link before logging in.");
        return;
      }
      window.dispatchEvent(new Event("user-auth-changed"));
      const destination = identity.role === "ADMIN"
        ? "/admin"
        : returnTo.startsWith("/admin") ? "/" : returnTo;
      navigate(destination, { replace: true });
    } catch (reason) {
      setError(reason.message);
    } finally {
      setSaving(false);
    }
  };
  const resend = async () => {
    setSaving(true);
    try {
      const result = await userPost("/api/v1/auth/resend-verification", { email: form.email });
      setNotice(result.message);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="user-auth-page">
      <Seo title={register ? "Create account" : "Login"} noindex />
      <form className="user-auth-card" onSubmit={submit}>
        <h1>{register ? "Create account" : "Welcome back"}</h1>
        <p>{register ? "Create an OTT Tracker account to rate movies." : "Sign in to rate movies and update your ratings."}</p>
        {register && <label>Display name <span>(optional)</span><input name="display_name" autoComplete="name" maxLength="100" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} /></label>}
        <label>Email address<input name="email" type="email" autoComplete="email" required maxLength="320" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} /></label>
        <PasswordInput value={form.password} autoComplete={register ? "new-password" : "current-password"} minLength={register ? 10 : 1} onChange={(event) => setForm({ ...form, password: event.target.value })} />
        {register && <small>Use at least 10 characters, including a letter and a number.</small>}
        {error && <p className="user-auth-error" role="alert">{error}</p>}
        {notice && <p className="user-auth-notice" role="status">{notice}</p>}
        <button type="submit" disabled={saving}>{saving ? "Please wait…" : register ? "Create account" : "Login"}</button>
        {register && notice && <button type="button" className="secondary" disabled={saving} onClick={resend}>Resend verification email</button>}
        {!register && <Link to="/forgot-password">Forgot password?</Link>}
        <p>{register ? "Already have an account?" : "New to OTT Tracker?"} <Link to={`${alternate}?${alternateParams}`}>{register ? "Login" : "Create account"}</Link></p>
      </form>
    </main>
  );
};

export const LoginPage = () => <AuthPage register={false} />;
export const SignupPage = () => <AuthPage register />;

export const VerifyEmailPage = () => {
  const [params] = useSearchParams();
  const [state, setState] = useState({ loading: true, message: "Verifying your email…", error: false });
  const token = params.get("token") || "";
  useEffect(() => {
    let current = true;
    if (!token) { setState({ loading: false, message: "This verification link is incomplete.", error: true }); return undefined; }
    userPost("/api/v1/auth/verify-email", { token })
      .then(value => current && setState({ loading: false, message: value.message, error: false }))
      .catch(reason => current && setState({ loading: false, message: reason.message, error: true }));
    return () => { current = false; };
  }, [token]);
  return <main className="user-auth-page"><Seo title="Verify email" noindex /><section className="user-auth-card" aria-live="polite"><h1>Verify email</h1><p className={state.error ? "user-auth-error" : "user-auth-notice"}>{state.message}</p>{!state.loading && <Link className="button-link" to="/login">Continue to login</Link>}</section></main>;
};

export const ForgotPasswordPage = () => {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const submit = async (event) => {
    event.preventDefault(); setSaving(true); setMessage("");
    try { const result = await userPost("/api/v1/auth/forgot-password", { email }); setMessage(result.message); } catch (reason) { setMessage(reason.message); } finally { setSaving(false); }
  };
  return <main className="user-auth-page"><Seo title="Forgot password" noindex /><form className="user-auth-card" onSubmit={submit}><h1>Reset your password</h1><p>Enter your account email. The response is intentionally the same whether or not an account exists.</p><label>Email address<input type="email" autoComplete="email" required maxLength="320" value={email} onChange={event => setEmail(event.target.value)} /></label>{message && <p className="user-auth-notice" role="status">{message}</p>}<button type="submit" disabled={saving}>{saving ? "Please wait…" : "Send reset link"}</button><Link to="/login">Back to login</Link></form></main>;
};

export const ResetPasswordPage = () => {
  const [params] = useSearchParams();
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  const [saving, setSaving] = useState(false);
  const token = params.get("token") || "";
  const submit = async (event) => {
    event.preventDefault(); setSaving(true); setMessage(""); setError(false);
    try { const result = await userPost("/api/v1/auth/reset-password", { token, password }); setMessage(result.message); } catch (reason) { setMessage(reason.message); setError(true); } finally { setSaving(false); }
  };
  return <main className="user-auth-page"><Seo title="Reset password" noindex /><form className="user-auth-card" onSubmit={submit}><h1>Choose a new password</h1><PasswordInput label="New password" value={password} autoComplete="new-password" minLength={10} onChange={event => setPassword(event.target.value)} /><small>Use at least 10 characters, including a letter and a number.</small>{message && <p className={error ? "user-auth-error" : "user-auth-notice"} role="status">{message}</p>}<button type="submit" disabled={saving || !token}>{saving ? "Saving…" : "Update password"}</button><Link to="/login">Back to login</Link></form></main>;
};

export const UserAuthControls = ({ onIdentityChange } = {}) => {
  const [user, setUser] = useState(undefined);
  const update = (identity) => {
    setUser(identity);
    onIdentityChange?.(identity);
  };
  const load = () => userGet("/api/v1/auth/me").then(update).catch(() => update(null));

  useEffect(() => {
    load();
    window.addEventListener("user-auth-changed", load);
    return () => window.removeEventListener("user-auth-changed", load);
  }, []);

  const logout = async () => {
    await userPost("/api/v1/auth/logout");
    update(null);
    window.dispatchEvent(new Event("user-auth-changed"));
  };

  if (user === undefined) return <span className="user-auth-controls" aria-label="Checking account" />;
  return user ? (
    <span className="user-auth-controls"><span>{user.display_name || user.email}</span><button className="link-button" onClick={logout}>Logout</button></span>
  ) : (
    <span className="user-auth-controls"><Link to="/login">Login</Link><Link to="/signup">Sign Up</Link></span>
  );
};
