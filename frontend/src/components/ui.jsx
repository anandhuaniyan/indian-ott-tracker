import React from "react";
import { Link } from "react-router-dom";
import { imageUrl } from "../services/api";
export function Card({ movie }) { return <Link className="card" to={`/movies/${movie.id}`}><img loading="lazy" src={imageUrl(movie.poster_path)} alt={`${movie.title} poster`} onError={e => e.currentTarget.src = "/placeholder.svg"}/><div><strong>{movie.title}</strong><small>{movie.release_date?.slice(0, 4) || "—"} · ★ {movie.rating?.toFixed?.(1) || "—"}</small></div></Link>; }
export function Loading() { return <main className="loading" aria-live="polite">Loading Indian cinema…</main>; }
export function Failure({ error }) { return <main className="loading"><h1>Unable to load this page</h1><p>{error}</p><Link to="/">Return home</Link></main>; }
