import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import analytics from "../services/analytics";
import { readConsent as savedConsent } from "../services/consent";

const MEASUREMENT_ID = () => import.meta.env.VITE_GA_MEASUREMENT_ID;

const readConsent = () => savedConsent() || {};

const loadScript = (id, src) => {
  if (document.getElementById(id)) return;
  const script = document.createElement("script");
  script.id = id;
  script.async = true;
  if (id === "adsense-script") {
    script.crossOrigin = "anonymous";
    window.adsbygoogle = window.adsbygoogle || [];
    window.adsbygoogle.requestNonPersonalizedAds = 1;
  }
  script.src = src;
  document.head.append(script);
};

const consentState = (consent) => {
  const advertising = consent.advertising ? "granted" : "denied";
  const analyticsStorage = consent.analytics ? "granted" : "denied";
  return { advertising, analyticsStorage };
};

export default function Tracking() {
  const ga = MEASUREMENT_ID();
  const adsClient = /^ca-pub-\d{16}$/.test(import.meta.env.VITE_ADSENSE_CLIENT_ID || "")
    ? import.meta.env.VITE_ADSENSE_CLIENT_ID : "";
  const location = useLocation();
  const configured = useRef(false);

  useEffect(() => {
    const consent = readConsent();
    const { advertising, analyticsStorage } = consentState(consent);

    if (ga && !configured.current) {
      configured.current = true;
      window.dataLayer = window.dataLayer || [];
      // gtag commands must be Arguments objects. Arrays are interpreted by
      // Google's data layer as method calls, so config/events never execute.
      window.gtag = function gtag() { window.dataLayer.push(arguments); };
      window.gtag("consent", "default", {
        ad_storage: advertising,
        ad_user_data: advertising,
        ad_personalization: "denied",
        analytics_storage: analyticsStorage,
        functionality_storage: "granted",
        personalization_storage: "denied",
        security_storage: "granted",
        wait_for_update: 500,
      });
      window.gtag("js", new Date());
      window.gtag("config", ga, { anonymize_ip: true, send_page_view: false });
      loadScript(
        "ga-gtag-script",
        `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(ga)}`,
      );
    }

    if (consent.advertising && adsClient) {
      loadScript(
        "adsense-script",
        `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${encodeURIComponent(adsClient)}`,
      );
    }

    const onConsentUpdated = () => {
      const updated = readConsent();
      const state = consentState(updated);
      if (ga && window.gtag) {
        window.gtag("consent", "update", {
          ad_storage: state.advertising,
          ad_user_data: state.advertising,
          ad_personalization: "denied",
          analytics_storage: state.analyticsStorage,
        });
      }
      if (updated.advertising && adsClient) {
        loadScript(
          "adsense-script",
          `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${encodeURIComponent(adsClient)}`,
        );
      }
    };
    window.addEventListener("consent-updated", onConsentUpdated);
    return () => window.removeEventListener("consent-updated", onConsentUpdated);
  }, [ga, adsClient, location.pathname, location.search]);

  useEffect(() => {
    if (!configured.current) return;
    analytics.trackPageView();
  }, [location.pathname, location.search]);

  return null;
}
