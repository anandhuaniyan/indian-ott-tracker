export const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
export async function get(path) { const response = await fetch(`${API}${path}`); if (!response.ok) throw new Error(response.status === 404 ? "Not found" : "Unable to load data"); return response.json(); }
export async function post(path, body) { const response = await fetch(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); if (!response.ok) throw new Error("Unable to submit request"); return response.json(); }
export const imageUrl = path => path ? (path.startsWith("http") ? path : `https://image.tmdb.org/t/p/w500${path}`) : "/placeholder.svg";
