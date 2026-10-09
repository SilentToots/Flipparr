// The one-line message after an action, shared by the web app and Flipparr
// View: "Added to your reading list", or why something failed.
import { useCallback, useRef, useState } from "react";
import { CheckCircle, WarningCircle } from "@phosphor-icons/react";

/**
 * `[toast, showToast]`: the element to render once, at the end of the app,
 * and the function that shows a message in it.
 */
export function useToast() {
  const [toast, setToast] = useState("");
  const [tone, setTone] = useState("success");
  const [leaving, setLeaving] = useState(false);
  const timers = useRef([]);
  // A new message replaces the one showing, and restarts its time; the last
  // 300ms play its exit rather than cutting it off.
  // `tone` is "success" (a green check) or "error" (a red warning): a failed
  // action said with a check read as though it had worked.
  const showToast = useCallback((message, nextTone = "success") => {
    timers.current.forEach((timer) => window.clearTimeout(timer));
    setToast(message);
    setTone(nextTone);
    setLeaving(false);
    timers.current = [
      window.setTimeout(() => setLeaving(true), 2900),
      window.setTimeout(() => { setToast(""); setLeaving(false); }, 3200),
    ];
  }, []);
  const element = toast
    ? <div className={`toast toast--${tone}${leaving ? " leaving" : ""}`} role={tone === "error" ? "alert" : "status"} key={toast}>
      {tone === "error" ? <WarningCircle size={20} weight="fill" /> : <CheckCircle size={20} weight="fill" />} {toast}
    </div>
    : null;
  return [element, showToast];
}
