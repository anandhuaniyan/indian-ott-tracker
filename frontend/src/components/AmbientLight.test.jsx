// @vitest-environment jsdom
import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import AmbientLight from "./AmbientLight";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function installMedia({ reduced = false } = {}) {
  const listeners = new Set();
  vi.stubGlobal("matchMedia", vi.fn((query) => ({
    matches: query.includes("prefers-reduced-motion") ? reduced : true,
    addEventListener: (_event, listener) => listeners.add(listener),
    removeEventListener: (_event, listener) => listeners.delete(listener),
  })));
}

it("coalesces desktop pointer movement into an animation frame without React state", () => {
  installMedia();
  const frames = [];
  vi.stubGlobal("requestAnimationFrame", vi.fn((callback) => {
    frames.push(callback);
    return frames.length;
  }));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());

  const { container } = render(<AmbientLight />);
  const light = container.querySelector(".ambient-pointer-light");
  window.dispatchEvent(new MouseEvent("pointermove", { clientX: 420, clientY: 240 }));
  window.dispatchEvent(new MouseEvent("pointermove", { clientX: 430, clientY: 250 }));

  expect(frames).toHaveLength(1);
  frames[0]();
  expect(light).toHaveClass("is-visible");
  expect(light.style.transform).toContain("translate3d(430px, 250px, 0)");
});

it("does not activate when reduced motion is requested", () => {
  installMedia({ reduced: true });
  const frame = vi.fn();
  vi.stubGlobal("requestAnimationFrame", frame);
  vi.stubGlobal("cancelAnimationFrame", vi.fn());

  const { container } = render(<AmbientLight />);
  window.dispatchEvent(new MouseEvent("pointermove", { clientX: 300, clientY: 180 }));

  expect(frame).not.toHaveBeenCalled();
  expect(container.querySelector(".ambient-pointer-light")).not.toHaveClass("is-visible");
});
