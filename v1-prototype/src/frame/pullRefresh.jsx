// Pull to refresh, shared by the web app on a Home Screen and Flipparr View:
// neither has a browser's own reload. It rides the phone's rubber band:
// while a finger is down at the top, the stretch it reports is the pull's
// depth. As iOS's own refresh control does (UIRefreshControl), a spinner
// appears in the gap the pull opens under the header, its marks filling in
// one by one as the pull deepens; full, letting go refreshes and it spins
// until the refresh is done. No words on screen (the owner, 2026-10-17: the
// "Release to refresh" pill over a white bounce was not iOS's); a screen
// reader still hears what happens. The pull's depth reaches the spinner as
// a CSS variable on one small fixed element, not through React, so nothing
// re-renders per frame. A page a dialog holds is left alone.
import { useEffect, useRef, useState } from "react";
import { LoadingIndicator } from "../components/LoadingIndicator.jsx";
import { PULL_REFRESH_PX } from "../reader.js";

const SAID = { armed: "Release to refresh", refreshing: "Refreshing" };

/**
 * `enabled`: whether this page has no reload of its own (a Home Screen web
 * app, the app). `onRefresh`: what letting go does -- the web reloads the
 * page; the app fetches the library again and resolves when it has.
 * `onArm`: called once each time the pull passes the point of refreshing
 * (the app taps the phone's haptic there, as iOS's control does).
 * Returns the spinner to render once, near the end of the page.
 */
export function usePullToRefresh({ enabled, onRefresh, onArm }) {
  const [pull, setPull] = useState("idle");
  const refresh = useRef(onRefresh);
  refresh.current = onRefresh;
  const arm = useRef(onArm);
  arm.current = onArm;
  const spinner = useRef(null);
  useEffect(() => {
    if (!enabled) return undefined;
    let pulling = false;
    let state = "idle";
    let busy = false;
    const show = (next) => { if (next !== state) { state = next; setPull(next); } };
    // How far the pull is, in px, and how much of the way to refreshing (0-1).
    const depth = (px) => {
      spinner.current?.style.setProperty("--pull", `${px}px`);
      spinner.current?.style.setProperty("--pull-progress", String(Math.min(1, px / PULL_REFRESH_PX)));
    };
    const onStart = () => {
      pulling = !busy && window.scrollY <= 0 && !document.documentElement.classList.contains("page-scroll-locked");
    };
    const onScroll = () => {
      if (!pulling) return;
      const stretch = Math.max(0, -window.scrollY);
      depth(stretch);
      const next = stretch >= PULL_REFRESH_PX ? "armed" : stretch > 0 ? "pulling" : "idle";
      if (next === "armed" && state !== "armed") arm.current?.();
      show(next);
    };
    const onEnd = () => {
      if (!pulling) return;
      pulling = false;
      if (state !== "armed") { show("idle"); depth(0); return; }
      busy = true;
      show("refreshing");
      Promise.resolve(refresh.current?.()).finally(() => { busy = false; show("idle"); depth(0); });
    };
    window.addEventListener("touchstart", onStart, { passive: true });
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("touchend", onEnd, { passive: true });
    window.addEventListener("touchcancel", onEnd, { passive: true });
    return () => {
      window.removeEventListener("touchstart", onStart);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("touchend", onEnd);
      window.removeEventListener("touchcancel", onEnd);
    };
  }, [enabled]);
  // The live region is always in the document and only its text changes: a
  // region mounted already filled is skipped by screen readers.
  return <>
    <div ref={spinner} className={`pull-refresh pull-refresh--${pull}`} aria-hidden="true">
      <LoadingIndicator size="lg" />
    </div>
    <span className="sr-only" role="status" aria-live="polite">{SAID[pull] || ""}</span>
  </>;
}
