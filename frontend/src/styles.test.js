// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
const enhancements = readFileSync(
  resolve(process.cwd(), "src/v1-enhancements.css"),
  "utf8",
);
const nginx = readFileSync(resolve(process.cwd(), "nginx.conf"), "utf8");

describe("fluid responsive layout", () => {
  it("does not impose a desktop minimum width on the application shell", () => {
    expect(css).not.toMatch(/min-width:\s*(?:1200|1280|1400|1440|1920)px/);
    expect(css).toMatch(/#root[\s\S]*min-width:\s*0/);
    expect(css).toMatch(/main\s*\{[\s\S]*width:\s*100%/);
  });

  it("uses shrinkable responsive grids and contained table overflow", () => {
    expect(css).toContain("minmax(min(145px, 100%), 1fr)");
    expect(css).toContain("grid-template-columns: repeat(auto-fit, minmax(min(170px, 100%), 1fr))");
    expect(css).toMatch(/\.table-wrap\s*\{[\s\S]*overflow-x:\s*auto/);
    expect(css).toMatch(/\.detail\s*\{[\s\S]*minmax\(0, 1fr\)/);
  });

  it("keeps the calendar tabs and cards in the shared fluid container", () => {
    expect(css).toContain("--site-content-max: 1440px");
    expect(css).toMatch(/\.site-bar\s*\{[\s\S]*max-width:\s*var\(--site-content-max\)/);
    expect(css).toMatch(/main\s*\{[\s\S]*max-width:\s*var\(--site-content-max\)/);
    expect(css).toMatch(/\.calendar-content,[\s\S]*\.calendar-tabs,[\s\S]*width:\s*100%/);
    expect(css).toMatch(/\.calendar-page\s*\{[\s\S]*width:\s*100%;[\s\S]*min-width:\s*0/);
    expect(css).not.toMatch(/\.calendar-page\s*\{[^}]*max-width:\s*100%/);
    expect(css).toContain("repeat(auto-fill, minmax(min(195px, 100%), 1fr))");
    expect(css).toMatch(/@media \(max-width: 640px\)[\s\S]*\.calendar-grid\s*\{[\s\S]*repeat\(2, minmax\(0, 1fr\)\)/);
    expect(css).toMatch(/\.calendar-movie-link\s*\{[\s\S]*grid-template-rows:\s*auto 1fr/);
    expect(css).toMatch(/\.calendar-poster\s*\{[\s\S]*aspect-ratio:\s*2 \/ 3/);
    expect(css).toMatch(/\.calendar-movie-info\s*\{[\s\S]*flex-direction:\s*column/);
    expect(enhancements).not.toContain(".calendar-grid");
    expect(enhancements).not.toContain(".calendar-tabs");
  });

  it("contains trailers and comments at phone widths without horizontal overflow", () => {
    expect(css).toMatch(/\.trailer-frame\s*\{[\s\S]*aspect-ratio:\s*16 \/ 9;[\s\S]*overflow:\s*hidden/);
    expect(css).toMatch(/\.trailer-frame iframe\s*\{[\s\S]*width:\s*100%;[\s\S]*height:\s*100%/);
    expect(css).toMatch(/\.comment-form input,[\s\S]*max-width:\s*100%/);
    expect(css).toMatch(/\.comment p,[\s\S]*overflow-wrap:\s*anywhere/);
    expect(css).toMatch(/@media \(max-width: 640px\)[\s\S]*\.comment-form button,[\s\S]*width:\s*100%/);
    expect(nginx).toMatch(/frame-src[^;]*https:\/\/www\.youtube-nocookie\.com/);
  });
});
