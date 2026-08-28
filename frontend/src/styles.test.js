// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");

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
});
