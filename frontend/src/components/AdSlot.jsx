import React, { useEffect, useRef } from "react";
import { readConsent } from "../services/consent";
import { useLocation } from "react-router-dom";
import { isPrivateRoute } from "../services/privateRoutes";

export default function AdSlot({ slot, format = "auto", eligible = true }) {
  const ref = useRef(null);
  const location = useLocation();
  const client = import.meta.env.VITE_ADSENSE_CLIENT_ID;
  const consent = readConsent() || {};
  useEffect(() => {
    if (!eligible || !client || !slot || !consent.advertising || !ref.current) return;
    try {
      window.adsbygoogle = window.adsbygoogle || [];
      window.adsbygoogle.requestNonPersonalizedAds = 1;
      window.adsbygoogle.push({});
    } catch { /* loader may still be starting */ }
  }, [client, slot, consent.advertising, eligible]);
  const blockedRoute = isPrivateRoute(location.pathname);
  if (!eligible || blockedRoute || !client || !slot || !consent.advertising) return null;
  return <ins ref={ref} className="adsbygoogle ad-slot" data-ad-client={client} data-ad-slot={slot} data-ad-format={format} data-full-width-responsive="true" aria-label="Advertisement" />;
}
