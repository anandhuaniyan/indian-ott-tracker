// @vitest-environment jsdom
import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { Dashboard } from "./Admin";
import { Request } from "./Public";

afterEach(() => vi.unstubAllGlobals());

it("shows the admin guard when the session is absent", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }));
  render(<MemoryRouter><Dashboard/></MemoryRouter>);
  expect(await screen.findByText(/Unauthenticated/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sign in" })).toBeInTheDocument();
});

it("renders the validated movie request form", () => {
  render(<MemoryRouter><Request/></MemoryRouter>);
  expect(screen.getByRole("heading", { name: "Request a movie" })).toBeInTheDocument();
  expect(screen.getByPlaceholderText("Email")).toHaveAttribute("type", "email");
  expect(screen.getByRole("button", { name: "Submit request" })).toBeInTheDocument();
});
