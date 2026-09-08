const measurementId = () => import.meta.env.VITE_GA_MEASUREMENT_ID;

const isEnabled = () =>
  Boolean(measurementId()) &&
  typeof window !== "undefined" &&
  typeof window.gtag === "function";

const clean = (params) =>
  Object.fromEntries(
    Object.entries(params).filter(([, value]) => value != null),
  );

const track = (name, params = {}) => {
  if (!isEnabled()) return;
  try {
    window.gtag("event", name, clean(params));
  } catch {
    // Analytics must never break the app.
  }
};

export const analytics = {
  isEnabled,
  trackEvent: track,
  trackPageView() {
    track("page_view", {
      page_path: window.location.pathname + window.location.search,
      page_location: window.location.href,
      page_title: document.title,
    });
  },
  search(term, { language, genre, platform } = {}) {
    track("search", { search_term: term, language, genre, platform });
  },
  filterUsed(name, value) {
    track("filter_used", { filter_name: name, filter_value: value });
  },
  movieView(movie) {
    track("movie_view", {
      movie_id: movie?.id,
      movie_title: movie?.title,
      m_language: movie?.original_language,
    });
  },
  personView(person) {
    track("person_view", {
      person_id: person?.id,
      person_name: person?.name,
    });
  },
  ottPlatformClick(movie, platform) {
    track("ott_platform_click", {
      movie_id: movie?.id,
      movie_title: movie?.title,
      ott_platform: platform,
    });
  },
  movieRequest(eventName, movieId) {
    track(eventName, { movie_id: movieId });
  },
};

export default analytics;