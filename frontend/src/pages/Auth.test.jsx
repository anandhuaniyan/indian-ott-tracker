// @vitest-environment jsdom
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { LoginPage, SignupPage, UserAuthControls } from "./Auth";

const userGet = vi.fn();
const userPost = vi.fn();
vi.mock("../services/api", () => ({
  userGet: (...args) => userGet(...args),
  userPost: (...args) => userPost(...args),
}));

beforeEach(() => {
  userGet.mockReset();
  userPost.mockReset();
});
afterEach(() => cleanup());

it("registers a normal user and returns to the movie", async () => {
  userPost.mockResolvedValue({ id: 1, email: "user@example.com", display_name: "Viewer" });
  render(
    <MemoryRouter initialEntries={["/signup?returnTo=/movies/12"]}>
      <Routes><Route path="/signup" element={<SignupPage />} /><Route path="/movies/:id" element={<p>Movie returned</p>} /></Routes>
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByLabelText(/Display name/), { target: { value: "Viewer" } });
  fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "user@example.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "StrongPass123" } });
  fireEvent.click(screen.getByRole("button", { name: "Create account" }));
  await waitFor(() => expect(userPost).toHaveBeenCalledWith("/api/v1/auth/register", {
    email: "user@example.com", password: "StrongPass123", display_name: "Viewer",
  }));
  expect(await screen.findByText("Movie returned")).toBeInTheDocument();
});

it("logs in and preserves the requested return path", async () => {
  userPost.mockResolvedValue({ id: 1, email: "user@example.com", display_name: null, role: "USER" });
  render(
    <MemoryRouter initialEntries={["/login?returnTo=/movies/9"]}>
      <Routes><Route path="/login" element={<LoginPage />} /><Route path="/movies/:id" element={<p>Back to rating</p>} /></Routes>
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "user@example.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "StrongPass123" } });
  fireEvent.click(screen.getByRole("button", { name: "Login" }));
  expect(await screen.findByText("Back to rating")).toBeInTheDocument();
});

it("routes a server-verified administrator from normal login to the dashboard", async () => {
  userPost.mockResolvedValue({ id: 2, email: "owner@example.com", display_name: "Administrator", role: "ADMIN" });
  render(
    <MemoryRouter initialEntries={["/login?returnTo=/movies/9"]}>
      <Routes><Route path="/login" element={<LoginPage />} /><Route path="/admin" element={<p>Admin dashboard</p>} /></Routes>
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "owner@example.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "admin-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Login" }));
  expect(await screen.findByText("Admin dashboard")).toBeInTheDocument();
});

it("routes an already-authenticated administrator away from login", async () => {
  userGet.mockResolvedValue({ id: 2, email: "owner@example.com", role: "ADMIN" });
  render(
    <MemoryRouter initialEntries={["/login"]}>
      <Routes><Route path="/login" element={<LoginPage />} /><Route path="/admin" element={<p>Existing admin dashboard</p>} /></Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByText("Existing admin dashboard")).toBeInTheDocument();
  expect(userPost).not.toHaveBeenCalled();
});

it("does not let a normal identity use an admin return path", async () => {
  userPost.mockResolvedValue({ id: 1, email: "user@example.com", display_name: null, role: "USER" });
  render(
    <MemoryRouter initialEntries={["/login?returnTo=/admin"]}>
      <Routes><Route path="/login" element={<LoginPage />} /><Route path="/" element={<p>Public home</p>} /><Route path="/admin" element={<p>Admin dashboard</p>} /></Routes>
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "user@example.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "StrongPass123" } });
  fireEvent.click(screen.getByRole("button", { name: "Login" }));
  expect(await screen.findByText("Public home")).toBeInTheDocument();
});

it("shows the current user and logs out", async () => {
  userGet.mockResolvedValueOnce({ id: 1, email: "user@example.com", display_name: "Viewer" }).mockRejectedValueOnce(new Error("Unauthenticated"));
  userPost.mockResolvedValue({ authenticated: false });
  render(<MemoryRouter><UserAuthControls /></MemoryRouter>);
  expect(await screen.findByText("Viewer")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Logout" }));
  await waitFor(() => expect(userPost).toHaveBeenCalledWith("/api/v1/auth/logout"));
  expect(await screen.findByRole("link", { name: "Login" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sign Up" })).toBeInTheDocument();
});
