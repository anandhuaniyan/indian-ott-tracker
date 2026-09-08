// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import Consent from "./Consent";
import { readConsent } from "../services/consent";

beforeEach(() => localStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each([null, "{}", "true", '"yes"', "[]", "invalid", '{"analytics":"true","advertising":true}'])("shows the banner for invalid stored consent %s", value => {
  if (value !== null) localStorage.setItem("ott-consent", value);
  render(<Consent />);
  expect(screen.getByRole("dialog")).toBeVisible();
  expect(screen.getByLabelText("Analytics", { exact: true })).not.toBeChecked();
  expect(readConsent()).toBeNull();
});

it("allows all only after a click, persists, and restores choices on reopen", () => {
  const updated = vi.fn();
  window.addEventListener("consent-updated", updated);
  const view = render(<Consent />);
  expect(readConsent()).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Allow All" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(readConsent()).toMatchObject({ necessary: true, analytics: true, advertising: true });
  expect(updated).toHaveBeenCalledTimes(1);
  view.unmount();
  render(<Consent />);
  expect(screen.queryByRole("dialog")).toBeNull();
  act(() => window.dispatchEvent(new Event("open-cookie-preferences")));
  expect(screen.getByLabelText("Analytics", { exact: true })).toBeChecked();
  expect(screen.getByLabelText("Advertising", { exact: true })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Reject optional" }));
  expect(readConsent()).toMatchObject({ analytics: false, advertising: false });
  window.removeEventListener("consent-updated", updated);
});

it("saves individual categories", () => {
  render(<Consent />);
  fireEvent.click(screen.getByLabelText("Analytics", { exact: true }));
  fireEvent.click(screen.getByRole("button", { name: "Save choices" }));
  expect(readConsent()).toMatchObject({ analytics: true, advertising: false });
});

it("does not pretend to save when browser storage fails", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  render(<Consent />);
  fireEvent.click(screen.getByRole("button", { name: "Allow All" }));
  expect(screen.getByRole("alert")).toHaveTextContent("could not save");
  expect(screen.getByRole("dialog")).toBeVisible();
  expect(readConsent()).toBeNull();
});
