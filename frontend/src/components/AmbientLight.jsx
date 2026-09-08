import React, { useEffect, useRef } from "react";

const DESKTOP_POINTER = "(hover: hover) and (pointer: fine) and (min-width: 769px)";
const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

export default function AmbientLight() {
  const lightRef = useRef(null);

  useEffect(() => {
    const light = lightRef.current;
    if (!light || typeof window.matchMedia !== "function") return undefined;

    const desktopPointer = window.matchMedia(DESKTOP_POINTER);
    const reducedMotion = window.matchMedia(REDUCED_MOTION);
    let enabled = false;
    let frame = 0;
    let pointerX = 0;
    let pointerY = 0;

    const paint = () => {
      frame = 0;
      if (!enabled) return;
      light.style.transform = `translate3d(${pointerX}px, ${pointerY}px, 0) translate3d(-50%, -50%, 0)`;
      light.classList.add("is-visible");
    };

    const onPointerMove = (event) => {
      pointerX = event.clientX;
      pointerY = event.clientY;
      if (!frame) frame = window.requestAnimationFrame(paint);
    };

    const onPointerOut = (event) => {
      if (event.relatedTarget == null) light.classList.remove("is-visible");
    };

    const detach = () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerout", onPointerOut);
      if (frame) window.cancelAnimationFrame(frame);
      frame = 0;
      light.classList.remove("is-visible");
      light.removeAttribute("style");
    };

    const sync = () => {
      const shouldEnable = desktopPointer.matches && !reducedMotion.matches;
      if (shouldEnable === enabled) return;
      detach();
      enabled = shouldEnable;
      if (enabled) {
        window.addEventListener("pointermove", onPointerMove, { passive: true });
        window.addEventListener("pointerout", onPointerOut, { passive: true });
      }
    };

    desktopPointer.addEventListener?.("change", sync);
    reducedMotion.addEventListener?.("change", sync);
    sync();

    return () => {
      enabled = false;
      detach();
      desktopPointer.removeEventListener?.("change", sync);
      reducedMotion.removeEventListener?.("change", sync);
    };
  }, []);

  return <div ref={lightRef} className="ambient-pointer-light" aria-hidden="true" />;
}
