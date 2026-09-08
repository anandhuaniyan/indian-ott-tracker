// Share validation between the preference UI and optional script loaders.
export function readConsent() {
  try {
    const value = JSON.parse(localStorage.getItem("ott-consent") || "null");
    return value && !Array.isArray(value) && value.necessary === true &&
      typeof value.analytics === "boolean" && typeof value.advertising === "boolean"
      ? value : null;
  } catch { return null; }
}
