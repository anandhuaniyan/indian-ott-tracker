// @vitest-environment jsdom
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("./Public", () => ({ Discover: ({ modeTabs }) => <main>{modeTabs}<h1>Local results</h1></main> }));
vi.mock("./DeepSearch", () => ({ DeepSearch: ({ modeTabs }) => <main>{modeTabs}<h1>Live results</h1></main> }));
import SearchPage from "./Search";

afterEach(() => cleanup());

it.each([
  ["/search", "Local results", "Search"],
  ["/search?mode=deep", "Live results", "Deep Search"],
])("renders integrated search mode %s", (url, heading, selected) => {
  render(<MemoryRouter initialEntries={[url]}><SearchPage/></MemoryRouter>);
  expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: selected })).toHaveClass("active");
  expect(screen.getByRole("link", { name: "Search" })).toHaveAttribute("href", "/search");
  expect(screen.getByRole("link", { name: "Deep Search" })).toHaveAttribute("href", "/search?mode=deep");
});
