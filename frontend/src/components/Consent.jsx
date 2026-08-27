import React, { useEffect, useState } from "react";

const read = () => { try { return JSON.parse(localStorage.getItem("ott-consent") || "null"); } catch { return null; } };

export default function Consent() {
  const [open, setOpen] = useState(!read());
  const [analytics, setAnalytics] = useState(Boolean(read()?.analytics));
  const [advertising, setAdvertising] = useState(Boolean(read()?.advertising));
  useEffect(() => {
    const show = () => { const value = read(); setAnalytics(Boolean(value?.analytics)); setAdvertising(Boolean(value?.advertising)); setOpen(true); };
    window.addEventListener("open-cookie-preferences", show);
    return () => window.removeEventListener("open-cookie-preferences", show);
  }, []);
  if (!open) return null;
  const save = (choices = { analytics, advertising }) => { localStorage.setItem("ott-consent", JSON.stringify({ necessary: true, ...choices, updatedAt: new Date().toISOString() })); setOpen(false); if (import.meta.env.MODE !== "test") location.reload(); };
  return <aside className="consent" role="dialog" aria-modal="true" aria-label="Cookie preferences"><strong>Your privacy choices</strong><p>Necessary storage keeps this site working. Optional analytics measures usage; advertising may personalize or measure ads. You can change or revoke these choices at any time.</p><label><input type="checkbox" checked disabled/> Necessary</label><label><input type="checkbox" checked={analytics} onChange={event => setAnalytics(event.target.checked)}/> Analytics</label><label><input type="checkbox" checked={advertising} onChange={event => setAdvertising(event.target.checked)}/> Advertising</label><div><button onClick={() => save({ analytics: false, advertising: false })}>Reject optional</button><button onClick={() => save()}>Save choices</button></div></aside>;
}
