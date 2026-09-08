import React, { useEffect, useRef, useState } from "react";
import { readConsent as read } from "../services/consent";


export default function Consent() {
  const [open, setOpen] = useState(!read());
  const [analytics, setAnalytics] = useState(Boolean(read()?.analytics));
  const [advertising, setAdvertising] = useState(Boolean(read()?.advertising));
  const [error, setError] = useState("");
  const banner = useRef(null);
  const previousFocus = useRef(null);
  useEffect(() => {
    if (!open) return;
    previousFocus.current = document.activeElement;
    banner.current?.focus();
  }, [open]);
  useEffect(() => {
    const show = () => { const value = read(); setAnalytics(Boolean(value?.analytics)); setAdvertising(Boolean(value?.advertising)); setOpen(true); };
    window.addEventListener("open-cookie-preferences", show);
    return () => window.removeEventListener("open-cookie-preferences", show);
  }, []);
  if (!open) return null;
  const save = (choices = { analytics, advertising }) => {
    try {
      localStorage.setItem("ott-consent", JSON.stringify({ necessary: true, ...choices, updatedAt: new Date().toISOString() }));
    } catch {
      setError("Your browser could not save preferences. Allow site storage and try again. Optional cookies remain unchanged.");
      return;
    }
    setAnalytics(choices.analytics);
    setAdvertising(choices.advertising);
    setError("");
    setOpen(false);
    previousFocus.current?.focus();
    window.dispatchEvent(new Event("consent-updated"));
    // Preserve the existing reload so withdrawn advertising stops running.
    if (import.meta.env.MODE !== "test") location.reload();
  };
  return <aside ref={banner} tabIndex={-1} className="consent" role="dialog" aria-label="Cookie preferences" aria-describedby="consent-description">
    <strong>Your privacy choices</strong>
    <p id="consent-description">Necessary storage keeps this site working. Optional analytics measures usage; advertising supports ads where configured. Choose which optional cookies to allow. You can change or revoke your choices at any time.</p>
    <label><input type="checkbox" checked disabled/> Necessary</label>
    <label><input type="checkbox" checked={analytics} onChange={event => setAnalytics(event.target.checked)}/> Analytics</label>
    <label><input type="checkbox" checked={advertising} onChange={event => setAdvertising(event.target.checked)}/> Advertising</label>
    {error && <p role="alert">{error}</p>}
    <div><button onClick={() => save({ analytics: true, advertising: true })}>Allow All</button><button onClick={() => save({ analytics: false, advertising: false })}>Reject optional</button><button onClick={() => save()}>Save choices</button></div>
  </aside>;
}
