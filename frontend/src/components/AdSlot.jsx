import React, { useEffect, useRef } from "react";
import { readConsent } from "../services/consent";

export default function AdSlot({ slot, format = "auto" }) {
  const ref = useRef(null);
  const client = import.meta.env.VITE_ADSENSE_CLIENT_ID;
  const consent = readConsent() || {};
  useEffect(() => {
    if (!client || !slot || !consent.advertising || !ref.current) return;
    try {
      window.adsbygoogle = window.adsbygoogle || [];
      window.adsbygoogle.requestNonPersonalizedAds = 1;
      window.adsbygoogle.push({});
    } catch { /* loader may still be starting */ }
  }, [client, slot, consent.advertising]);
  if (!client || !slot || !consent.advertising) return null;
  return <ins ref={ref} className="adsbygoogle ad-slot" data-ad-client={client} data-ad-slot={slot} data-ad-format={format} data-full-width-responsive="true" aria-label="Advertisement" />;
}
