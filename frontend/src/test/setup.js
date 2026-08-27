import "@testing-library/jest-dom/vitest";
import { beforeEach } from "vitest";

beforeEach(() => {
  localStorage.clear();
  document.head.querySelectorAll("meta, link[rel='canonical'], script[data-page-jsonld]").forEach(node => node.remove());
});
