// @vitest-environment jsdom
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { analytics } from "../services/analytics";

const moduleEnv = { ...import.meta.env };

describe("analytics helper", () => {
  beforeEach(() => {
    vi.stubGlobal("window", { location: { pathname: "/", search: "", href: "https://x/" }, gtag: vi.fn() });
    window.document = { title: "" };
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is a no-op when no measurement id is configured", () => {
    import.meta.env.VITE_GA_MEASUREMENT_ID = "";
    expect(analytics.isEnabled()).toBe(false);
    analytics.search("x");
    expect(window.gtag).not.toHaveBeenCalled();
  });

  it("tracks page_view when configured", () => {
    import.meta.env.VITE_GA_MEASUREMENT_ID = "G-TEST";
    analytics.trackPageView();
    expect(window.gtag).toHaveBeenCalledWith(
      "event",
      "page_view",
      expect.objectContaining({ page_path: "/" }),
    );
  });

  it("only sends permitted parameters", () => {
    import.meta.env.VITE_GA_MEASUREMENT_ID = "G-TEST";
    analytics.movieView({ id: 1, title: "Film" });
    expect(window.gtag).toHaveBeenCalledWith(
      "event",
      "movie_view",
      { movie_id: 1, movie_title: "Film", m_language: undefined },
    );
  });
});
