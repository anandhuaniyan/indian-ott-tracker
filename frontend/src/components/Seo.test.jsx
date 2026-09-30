// @vitest-environment jsdom
import React from "react";
import { render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import Seo from "./Seo";

const renderSeo = (url, props = {}) => render(
  <MemoryRouter initialEntries={[url]}>
    <Seo title="Search movies" description="Search OTT Tracker movies and people." {...props} />
  </MemoryRouter>,
);

describe("search indexing metadata", () => {
  it("uses worldwide homepage metadata without changing the canonical domain", async () => {
    renderSeo("/", { title: "OTT Tracker", description: "Movies from India and around the world." });
    await waitFor(() => expect(document.title).toBe("OTT Tracker – Movies & OTT Releases Worldwide"));
    expect(document.head.querySelector('meta[property="og:title"]')).toHaveAttribute("content", "OTT Tracker – Movies & OTT Releases Worldwide");
    expect(document.head.querySelector('meta[name="twitter:title"]')).toHaveAttribute("content", "OTT Tracker – Movies & OTT Releases Worldwide");
    expect(document.head.querySelector('meta[property="og:description"]')).toHaveAttribute("content", "Movies from India and around the world.");
  });
  it("indexes a canonical landing page with complete metadata", async () => {
    renderSeo("/search");
    await waitFor(() => expect(document.title).toBe("Search movies | OTT Tracker"));
    expect(document.head.querySelector('meta[name="robots"]')).toHaveAttribute("content", "index,follow");
    expect(document.head.querySelector('link[rel="canonical"]')?.href).toMatch(/\/search$/);
    expect(document.head.querySelector('meta[property="og:url"]')?.content).toMatch(/\/search$/);
    expect(document.head.querySelector('meta[name="description"]')).toHaveAttribute("content", "Search OTT Tracker movies and people.");
  });

  it("noindexes parameter combinations and canonicalizes them to the base path", async () => {
    renderSeo("/search?q=actor&page=5");
    await waitFor(() => expect(document.head.querySelector('meta[name="robots"]')).toHaveAttribute("content", "noindex,follow"));
    expect(document.head.querySelector('link[rel="canonical"]')?.href).toMatch(/\/search$/);
    expect(document.head.querySelector('link[rel="canonical"]')?.href).not.toContain("?");
  });

  it("supports a normalized canonical path for provider aliases", async () => {
    renderSeo("/ott/amazon-prime-video", { canonicalPath: "/ott/prime-video" });
    await waitFor(() => expect(document.head.querySelector('link[rel="canonical"]')?.href).toMatch(/\/ott\/prime-video$/));
  });

  it("removes duplicate canonicals and keeps exactly one route canonical", async () => {
    document.head.insertAdjacentHTML("beforeend", '<link rel="canonical" href="https://otttracker.in/"><link rel="canonical" href="https://example.test/wrong">');
    renderSeo("/movies/38514?ref=test");
    await waitFor(() => expect(document.head.querySelectorAll('link[rel="canonical"]')).toHaveLength(1));
    expect(document.head.querySelector('link[rel="canonical"]')?.href).toMatch(/\/movies\/38514$/);
    expect(document.head.querySelector('link[rel="canonical"]')?.href).not.toContain("?");
  });
});
