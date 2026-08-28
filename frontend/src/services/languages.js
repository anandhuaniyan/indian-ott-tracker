export const COMMON_LANGUAGE_OPTIONS = [
  ["ml", "Malayalam"], ["ta", "Tamil"], ["te", "Telugu"], ["hi", "Hindi"],
  ["kn", "Kannada"], ["en", "English"], ["bn", "Bengali"], ["mr", "Marathi"],
  ["pa", "Punjabi"], ["gu", "Gujarati"], ["ur", "Urdu"], ["or", "Odia"],
  ["as", "Assamese"], ["ne", "Nepali"], ["si", "Sinhala"], ["ar", "Arabic"],
  ["es", "Spanish"], ["fr", "French"], ["de", "German"], ["it", "Italian"],
  ["ja", "Japanese"], ["ko", "Korean"], ["zh", "Chinese"], ["ru", "Russian"],
];

const NAMES = Object.fromEntries(COMMON_LANGUAGE_OPTIONS);

export function languageName(code, storedName) {
  if (storedName) return storedName;
  if (!code) return "";
  return NAMES[String(code).toLowerCase()] || code;
}
