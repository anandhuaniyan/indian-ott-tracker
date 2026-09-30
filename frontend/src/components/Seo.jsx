import { useEffect } from "react";
import { useLocation } from "react-router-dom";

const meta = (selector, attributes, value) => {
  let element = document.head.querySelector(selector);
  if (!element) {
    element = document.createElement("meta");
    Object.entries(attributes).forEach(([key, item]) => element.setAttribute(key, item));
    document.head.append(element);
  }
  element.content = value || "";
};

export default function Seo({ title, description = "Discover movies and OTT releases worldwide across theaters and streaming platforms.", image, type = "website", jsonLd, noindex = false, canonicalPath }) {
  const route = useLocation();
  useEffect(() => {
    const full = title === "OTT Tracker" ? "OTT Tracker – Movies & OTT Releases Worldwide" : `${title} | OTT Tracker`;
    const configuredSite = (import.meta.env.VITE_SITE_URL || "https://otttracker.in").replace(/\/$/, "");
    const site = import.meta.env.PROD ? "https://otttracker.in" : configuredSite;
    const path = canonicalPath || route.pathname;
    const canonical = `${site}${path.startsWith("/") ? path : `/${path}`}`;
    const hasParameters = Boolean(route.search);
    document.title = full;
    meta('meta[name="description"]', { name: "description" }, description);
    meta('meta[property="og:title"]', { property: "og:title" }, full);
    meta('meta[property="og:description"]', { property: "og:description" }, description);
    meta('meta[property="og:type"]', { property: "og:type" }, type);
    meta('meta[property="og:url"]', { property: "og:url" }, canonical);
    meta('meta[property="og:site_name"]', { property: "og:site_name" }, "OTT Tracker");
    meta('meta[property="og:locale"]', { property: "og:locale" }, "en_IN");
    meta('meta[name="twitter:card"]', { name: "twitter:card" }, image ? "summary_large_image" : "summary");
    meta('meta[name="twitter:title"]', { name: "twitter:title" }, full);
    meta('meta[name="twitter:description"]', { name: "twitter:description" }, description);
    meta('meta[name="robots"]', { name: "robots" }, noindex || hasParameters ? "noindex,follow" : "index,follow");
    if (import.meta.env.VITE_GOOGLE_SITE_VERIFICATION) meta('meta[name="google-site-verification"]', { name: "google-site-verification" }, import.meta.env.VITE_GOOGLE_SITE_VERIFICATION);
    if (image) {
      meta('meta[property="og:image"]', { property: "og:image" }, image);
      meta('meta[name="twitter:image"]', { name: "twitter:image" }, image);
    }
    const canonicalLinks = [...document.head.querySelectorAll('link[rel="canonical"]')];
    let link = canonicalLinks.shift();
    canonicalLinks.forEach((duplicate) => duplicate.remove());
    if (!link) { link = document.createElement("link"); link.rel = "canonical"; document.head.append(link); }
    link.href = canonical;
    document.head.querySelectorAll('script[data-page-jsonld="true"]').forEach(node => node.remove());
    const entries = Array.isArray(jsonLd) ? jsonLd : jsonLd ? [jsonLd] : [];
    entries.forEach(value => {
      const script = document.createElement("script");
      script.type = "application/ld+json";
      script.dataset.pageJsonld = "true";
      script.textContent = JSON.stringify(value);
      document.head.append(script);
    });
    return () => document.head.querySelectorAll('script[data-page-jsonld="true"]').forEach(node => node.remove());
  }, [title, description, image, type, jsonLd, noindex, canonicalPath, route.pathname, route.search]);
  return null;
}

export const breadcrumbJsonLd = items => ({
  "@context": "https://schema.org", "@type": "BreadcrumbList",
  itemListElement: items.map((item, index) => ({ "@type": "ListItem", position: index + 1, name: item.name, item: `${import.meta.env.VITE_SITE_URL || location.origin}${item.path}` })),
});
