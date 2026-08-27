import { useEffect } from "react";

const meta = (selector, attributes, value) => {
  let element = document.head.querySelector(selector);
  if (!element) {
    element = document.createElement("meta");
    Object.entries(attributes).forEach(([key, item]) => element.setAttribute(key, item));
    document.head.append(element);
  }
  element.content = value || "";
};

export default function Seo({ title, description = "Discover Indian movies and verified lawful OTT availability.", image, type = "website", jsonLd }) {
  useEffect(() => {
    const full = title === "Indian OTT Tracker" ? title : `${title} | Indian OTT Tracker`;
    const canonical = `${import.meta.env.VITE_SITE_URL || location.origin}${location.pathname}`;
    document.title = full;
    meta('meta[name="description"]', { name: "description" }, description);
    meta('meta[property="og:title"]', { property: "og:title" }, full);
    meta('meta[property="og:description"]', { property: "og:description" }, description);
    meta('meta[property="og:type"]', { property: "og:type" }, type);
    meta('meta[property="og:url"]', { property: "og:url" }, canonical);
    meta('meta[name="twitter:card"]', { name: "twitter:card" }, image ? "summary_large_image" : "summary");
    meta('meta[name="twitter:title"]', { name: "twitter:title" }, full);
    meta('meta[name="twitter:description"]', { name: "twitter:description" }, description);
    if (import.meta.env.VITE_GOOGLE_SITE_VERIFICATION) meta('meta[name="google-site-verification"]', { name: "google-site-verification" }, import.meta.env.VITE_GOOGLE_SITE_VERIFICATION);
    if (image) {
      meta('meta[property="og:image"]', { property: "og:image" }, image);
      meta('meta[name="twitter:image"]', { name: "twitter:image" }, image);
    }
    let link = document.head.querySelector('link[rel="canonical"]');
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
  }, [title, description, image, type, jsonLd]);
  return null;
}

export const breadcrumbJsonLd = items => ({
  "@context": "https://schema.org", "@type": "BreadcrumbList",
  itemListElement: items.map((item, index) => ({ "@type": "ListItem", position: index + 1, name: item.name, item: `${import.meta.env.VITE_SITE_URL || location.origin}${item.path}` })),
});
