export const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
const cache = new Map();
export async function get(path, { maxAge = 60000 } = {}) {
  const existing = cache.get(path);
  if (existing && Date.now() - existing.time < maxAge) return existing.value;
  const request = fetch(`${API}${path}`).then(response => {
    if (!response.ok) throw new Error(response.status === 404 ? "Not found" : "Unable to load data");
    return response.json();
  });
  cache.set(path, { time: Date.now(), value: request });
  try { return await request; } catch (error) { cache.delete(path); throw error; }
}
export async function post(path, body) { const response = await fetch(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); if (!response.ok) throw new Error("Unable to submit request"); return response.json(); }
export const imageUrl = (path, size="w500") => path ? (path.startsWith("http") ? path : path.startsWith("/media/") || path.startsWith("/storage/") ? `${API}${path}` : `https://image.tmdb.org/t/p/${size}${path}`) : "/placeholder.svg";
