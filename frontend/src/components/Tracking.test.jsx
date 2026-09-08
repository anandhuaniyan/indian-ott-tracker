// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter } from "react-router-dom";
import Tracking from "./Tracking";

const commands = () => (window.dataLayer || []).filter(item => item[0]);
describe("GA command transport", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_GA_MEASUREMENT_ID", "G-TEST123456");
    localStorage.clear();
    delete window.dataLayer;
    delete window.gtag;
    document.getElementById("ga-gtag-script")?.remove();
  });
  afterEach(() => { cleanup(); vi.unstubAllEnvs(); });
  const mount = () => render(<MemoryRouter><Tracking /><Link to="/discover">Discover</Link></MemoryRouter>);

  it("uses Google's Arguments command format, with consent before config", () => {
    mount();
    expect(commands().map(item => Object.prototype.toString.call(item)))
      .toEqual(Array(4).fill("[object Arguments]"));
    expect(commands().map(item => item[0])).toEqual(["consent", "js", "config", "event"]);
    expect(commands()[0][2].analytics_storage).toBe("denied");
    expect(commands()[2][1]).toBe("G-TEST123456");
    expect(commands()[2][2].send_page_view).toBe(false);
    expect(commands()[3][1]).toBe("page_view");
    expect(document.getElementById("ga-gtag-script").async).toBe(true);
  });

  it("configures once and sends one additional page view on SPA navigation", () => {
    mount();
    fireEvent.click(screen.getByText("Discover"));
    expect(commands().filter(item => item[0] === "config")).toHaveLength(1);
    expect(commands().filter(item => item[1] === "page_view")).toHaveLength(2);
    expect(document.querySelectorAll('#ga-gtag-script')).toHaveLength(1);
  });

  it("updates granted and revoked consent using valid Google commands", () => {
    mount();
    for (const analytics of [true, false]) {
      localStorage.setItem("ott-consent", JSON.stringify({ necessary: true, analytics, advertising: false }));
      window.dispatchEvent(new Event("consent-updated"));
      const command = commands().at(-1);
      expect(Object.prototype.toString.call(command)).toBe("[object Arguments]");
      expect(command[0]).toBe("consent");
      expect(command[1]).toBe("update");
      expect(command[2].analytics_storage).toBe(analytics ? "granted" : "denied");
    }
  });

  it("does not install GA without a build-time ID", () => {
    vi.stubEnv("VITE_GA_MEASUREMENT_ID", "");
    mount();
    expect(window.gtag).toBeUndefined();
    expect(document.getElementById("ga-gtag-script")).toBeNull();
  });

  it("loads the AdSense script only once after advertising consent", () => {
    vi.stubEnv("VITE_ADSENSE_CLIENT_ID", "ca-pub-1234567890123456");
    mount();
    expect(document.getElementById("adsense-script")).toBeNull();
    localStorage.setItem("ott-consent", JSON.stringify({ necessary: true, analytics: true, advertising: true }));
    window.dispatchEvent(new Event("consent-updated"));
    window.dispatchEvent(new Event("consent-updated"));
    fireEvent.click(screen.getByText("Discover"));
    const scripts = document.querySelectorAll('#adsense-script');
    expect(scripts).toHaveLength(1);
    expect(scripts[0].async).toBe(true);
    expect(scripts[0].crossOrigin).toBe("anonymous");
    expect(scripts[0].src).toContain("client=ca-pub-1234567890123456");
    expect(window.adsbygoogle.requestNonPersonalizedAds).toBe(1);
    expect(commands().filter(item=>item[0]==="consent").at(-1)[2].ad_personalization).toBe("denied");
    scripts[0].remove();
  });
});
