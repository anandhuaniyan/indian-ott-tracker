import { get } from "./api";

const PUBLIC_LANGUAGE_NAMES = {
  ml: "Malayalam", ta: "Tamil", te: "Telugu", hi: "Hindi", kn: "Kannada",
  bn: "Bengali", mr: "Marathi", pa: "Punjabi", gu: "Gujarati", or: "Odia",
  as: "Assamese", en: "English", ko: "Korean", ja: "Japanese", zh: "Chinese",
  es: "Spanish", fr: "French", th: "Thai",
};

export function loadLanguageOptions() {
  return get("/api/v1/languages", { maxAge: 60 * 60 * 1000 })
    .then((items) => (Array.isArray(items) ? items : []));
}

export function languageName(code, storedName) {
  if (storedName) return storedName;
  const normalized = String(code || "").trim().toLowerCase();
  return PUBLIC_LANGUAGE_NAMES[normalized] || normalized.toUpperCase();
}
