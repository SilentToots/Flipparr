// Pull to refresh, shared by the web app on a Home Screen and Flipparr View:
// neither has a browser's own reload. It rides the phone's rubber band:
// while a finger is down at the top, the stretch it reports is the pull's
// depth; past a threshold the pill says so, and letting go there refreshes.
// Nothing is moved by hand, so nothing repaints per frame. A page a dialog
// holds is left alone.
import { useEffect, useRef, useState } from "react";
import { ArrowsClockwise } from "@phosphor-icons/react";
import { LoadingSpinner } from "../components/glass.jsx";
import { PULL_REFRESH_PX } from "../reader.js";

/**
 * `enabled`: whether this page has no reload of its own (a Home Screen web
 * app, the app). `onRefresh`: what letting go does -- the web reloads the
 * page; the app fetches the library again and resolves when it has.
 * Returns the pill to render once, near the end of the page.
 */
export function usePullToRefresh({ enabled, onRefresh }) {
  const [pull, setPull] = useState("idle");
  const refresh = useRef(onRefresh);
  refresh.current = onRefresh;
  useEffect(() => {
    if (!enabled) return undefined;
    let pulling = false;
    let armed = false;
    const onStart = () => {
      pulling = window.scrollY <= 0 && !document.documentElement.classList.contains("page-scroll-locked");
      armed = false;
    };
    const onScroll = () => {
      if (!pulling) return;
      const past = -window.scrollY >= PULL_REFRESH_PX;
      if (past !== armed) { armed = past; setPull(past ? "armed" : "idle"); }
    };
    const onEnd = () => {
      if (!pulling) return;
      pulling = false;
      if (!armed) { setPull("idle"); return; }
      setPull("refreshing");
      Promise.resolve(refresh.current?.()).finally(() => setPull("idle"));
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
  // Always in the document: a live region mounted already filled is skipped
  // by screen readers; one that stays and changes its text is announced.
  return <div className={`pull-refresh${pull === "idle" ? " idle" : ""}`} role="status" aria-live="polite">
    {pull === "idle" ? null : <>{pull === "refreshing" ? <LoadingSpinner size={16} /> : <ArrowsClockwise size={16} />} {pull === "refreshing" ? "Refreshing…" : "Release to refresh"}</>}
  </div>;
}
