// @vitest-environment jsdom

import React from "react";
import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import LanguageTabs from "./LanguageTabs";

const languages = [
  { code: "ml", name: "Malayalam", region: "indian", group: "primary" },
  { code: "ta", name: "Tamil", region: "indian", group: "primary" },
  { code: "te", name: "Telugu", region: "indian", group: "primary" },
  { code: "hi", name: "Hindi", region: "indian", group: "primary" },
  { code: "kn", name: "Kannada", region: "indian", group: "primary" },
  { code: "en", name: "English", region: "international", group: "primary" },
  { code: "ko", name: "Korean", region: "international", group: "primary" },
  { code: "ja", name: "Japanese", region: "international", group: "primary" },
  { code: "zh", name: "Chinese", region: "international", group: "primary" },
  { code: "es", name: "Spanish", region: "international", group: "primary" },
  { code: "fr", name: "French", region: "international", group: "primary" },
];

afterEach(cleanup);

describe("LanguageTabs", () => {
  it("renders the approved primary row without public movie counts", () => {
    const view = render(
      <LanguageTabs
        value="all"
        languages={languages}
        counts={{ all: 12281, en: 5400 }}
        onChange={() => {}}
      />,
    );

    const query = within(view.container);
    expect(query.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "All", "Indian", "English", "Korean", "Japanese", "Chinese", "Spanish", "French",
    ]);
    expect(query.queryByText("12281")).not.toBeInTheDocument();
    expect(query.queryByText("5400")).not.toBeInTheDocument();
    expect(query.queryByRole("tab", { name: "All Indian" })).not.toBeInTheDocument();
  });

  it("shows All Indian and the approved Indian languages when Indian is active", () => {
    const view = render(<LanguageTabs value="indian" languages={languages} onChange={() => {}} />);
    const query = within(view.container);

    expect(query.getByRole("tab", { name: "Indian" })).toHaveAttribute("aria-selected", "true");
    expect(query.getByRole("tab", { name: "All Indian" })).toHaveAttribute("aria-selected", "true");
    expect(query.getByRole("tab", { name: "Malayalam" })).toBeInTheDocument();
    expect(query.getByRole("tab", { name: "Kannada" })).toBeInTheDocument();
  });

  it("preserves exact language values and supports arrow, Home, and End keys", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <LanguageTabs value="all" languages={languages} onChange={onChange} />,
    );
    const query = within(document.body.lastElementChild);
    const all = query.getByRole("tab", { name: "All" });

    all.focus();
    fireEvent.keyDown(all, { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("indian");
    expect(query.getByRole("tab", { name: "Indian" })).toHaveFocus();

    fireEvent.keyDown(query.getByRole("tab", { name: "Indian" }), { key: "End" });
    expect(onChange).toHaveBeenLastCalledWith("fr");

    rerender(<LanguageTabs value="ml" languages={languages} onChange={onChange} />);
    const malayalam = query.getByRole("tab", { name: "Malayalam" });
    expect(malayalam).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(malayalam, { key: "Home" });
    expect(onChange).toHaveBeenLastCalledWith("indian");
  });
});
