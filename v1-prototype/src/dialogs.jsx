// Dialogs, drawers and phone sheets: the one stack every dialog joins, so
// Escape, the focus trap, Back and the page's scroll lock apply to the one in
// front. Shared by the web app and the reader (src/reader), which is why it is
// a module of its own rather than part of App.jsx.
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, X } from "@phosphor-icons/react";
import { sheetPullDecision } from "./sheet.js";

export const DIALOG_FOCUSABLE =
  'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

// Dialogs stack: Fix match opens on top of the series drawer. Escape and the
// focus trap must apply only to the topmost one. Listener order alone can't do
// this — every dialog listens on document, and capture order favours the one
// that mounted first, which is the one underneath.
export const openDialogs = [];
// Whether a dialog is the one in front. Keys belong to the front layer only:
// Escape closes one layer at a time, and the reader's page turns must not
// reach it while a drawer is open over it.
export const isTopDialog = (node) => Boolean(node) && openDialogs[openDialogs.length - 1] === node;
// A dialog and Back. On a phone the edge swipe is Back, and a drawer with no
// history entry of its own left the page under it. Putting the entry back
// from the popstate handler works under a Back button and not under the
// swipe: Safari has committed to the previous page before the handler runs.
// So a dialog pushes an entry of its own when it opens -- the same address,
// marked with its id -- and Back pops that: the address never changes, and
// the dialog closes because its mark is gone (App, applyLocation). Closed
// any other way, it takes its entry with it, so no Back press is a dead one;
// and a view change made while it was open writes over the entry instead of
// stacking on it. The two dialogs that live in the address, the run drawer
// (?series=) and the reader (?read=), say so with data-in-address and close
// by the address changing.
export const dialogClosers = new WeakMap();
export const dialogMarks = new WeakMap();
export let dialogMarkCount = 0;
export function closeTopDialog() {
  const node = openDialogs[openDialogs.length - 1];
  if (!node || node.hasAttribute("data-in-address")) return false;
  const close = dialogClosers.get(node);
  if (!close) return false;
  close();
  return true;
}
/** The dialog in front, when Back has just taken its entry: it should close. */
export function dialogLeftByBack() {
  const node = openDialogs[openDialogs.length - 1];
  if (!node || node.hasAttribute("data-in-address")) return false;
  return window.history.state?.dialog !== dialogMarks.get(node);
}

// While any dialog or drawer is open the page behind it does not scroll: only
// the dialog does, and there is one scroll bar. The gutter stays reserved so
// the page does not shift sideways when its scroll bar goes.
export function lockPageScroll() {
  document.documentElement.classList.add("page-scroll-locked");
}
export function unlockPageScroll() {
  if (!openDialogs.length) document.documentElement.classList.remove("page-scroll-locked");
}

// Shared modal keyboard behaviour. Without this a dialog opens with focus left
// on <body>, so its first control sits behind every focusable element on the
// page and there is no way to leave from the keyboard. Attach the returned ref
// to the dialog element and give it role="dialog", aria-modal and a name.
// Attaches native touch listeners to the node the dialog already holds a ref
// to. Native rather than React's onTouch* props: gesture handling wants the
// real event stream, and passive listeners keep scrolling smooth.
// Keeps a drawer mounted long enough for its exit animation to play, then
// hands control back to the parent. Returns the class to apply and a close
// function to use everywhere in place of the raw onClose.
// How long the exit animation actually lasts, read from the same token the
// stylesheet animates on. Hard-coding it meant unmounting at 200ms against a
// 220ms animation, cutting off the end of every close.
export function exitDurationMs() {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue("--motion-duration-exit").trim();
  const value = parseFloat(raw);
  const ms = !Number.isFinite(value) ? 300 : raw.endsWith("ms") ? value : value * 1000;
  // Two frames past the end, so the last frame of the slide is painted before
  // the drawer leaves the tree rather than racing it.
  return ms + 34;
}

export function useDrawerExit(onClose) {
  const [closing, setClosing] = useState(false);
  const timer = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => () => window.clearTimeout(timer.current), []);
  function requestClose() {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      closeRef.current();
      return;
    }

    // A second request while the exit is already running is ignored, not
    // honoured immediately. One swipe on iOS raises two of them -- the drawer's
    // own gesture and the browser's back gesture that the same swipe triggers
    // -- and closing on the second cut the animation the first had started.
    if (closing) return;
    setClosing(true);
    timer.current = window.setTimeout(() => closeRef.current(), exitDurationMs());
  }
  return { closing, requestClose };
}

// Close a drawer from outside it -- specifically when Back removes the ?series=
// parameter -- through the same exit animation the close button uses, instead
// of yanking the element out of the tree. On iOS a rightward swipe near the
// edge IS the browser's back gesture, so this is the path a swipe-to-dismiss
// actually takes.
export function useExternalDismiss(signal, requestClose) {
  const closeRef = useRef(requestClose);
  closeRef.current = requestClose;
  const seen = useRef(signal);
  useEffect(() => {
    if (signal === seen.current) return;
    seen.current = signal;
    closeRef.current();
  }, [signal]);
}

export function useSwipeToDismiss(ref, onClose) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    let start = null;
    function onStart(event) {
      if (event.touches.length !== 1) { start = null; return; }
      // A swipe beginning on something scrollable sideways -- the tab strip,
      // a wide table -- belongs to that element, not to the drawer.
      let el = event.target;
      while (el && el !== node) {
        if (el.scrollWidth > el.clientWidth + 1) { start = null; return; }
        el = el.parentElement;
      }
      const touch = event.touches[0];
      start = { x: touch.clientX, y: touch.clientY, at: Date.now() };
    }
    function onEnd(event) {
      const from = start;
      start = null;
      const touch = event.changedTouches && event.changedTouches[0];
      if (!from || !touch) return;
      const dx = touch.clientX - from.x;
      const dy = touch.clientY - from.y;
      // Rightward, clearly horizontal, and a flick rather than a slow drag.
      if (dx > 70 && Math.abs(dx) > Math.abs(dy) * 1.8 && Date.now() - from.at < 800) {
        closeRef.current?.();
      }
    }
    node.addEventListener("touchstart", onStart, { passive: true });
    node.addEventListener("touchend", onEnd, { passive: true });
    return () => {
      node.removeEventListener("touchstart", onStart);
      node.removeEventListener("touchend", onEnd);
    };
  }, [ref]);
}

export function useDialog(onClose) {
  const ref = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const previouslyFocused = document.activeElement;
    const focusable = () =>
      [...node.querySelectorAll(DIALOG_FOCUSABLE)].filter((el) => el.offsetParent !== null);
    const initial = focusable()[0] || node;
    if (initial === node && !node.hasAttribute("tabindex")) node.setAttribute("tabindex", "-1");
    initial.focus();
    // A phone sheet leaves by sliding down, however it is closed.
    const isSheet = () => node.matches(".modal, .library-sheet") && Boolean(window.matchMedia?.(SHEET_QUERY).matches);
    const leave = () => (isSheet() ? slideSheetAway(node, () => closeRef.current?.()) : closeRef.current?.());
    // The backdrop's own handler would close it at once; a tap on a sheet's
    // backdrop is taken here first and slides it away instead.
    const backdrop = node.parentElement?.matches?.(".modal-backdrop") ? node.parentElement : null;
    function onBackdrop(event) {
      if (event.target !== backdrop || !isSheet()) return;
      event.stopPropagation();
      leave();
    }
    backdrop?.addEventListener("mousedown", onBackdrop);
    openDialogs.push(node);
    dialogClosers.set(node, leave);
    let mark = 0;
    if (!node.hasAttribute("data-in-address")) {
      mark = ++dialogMarkCount;
      dialogMarks.set(node, mark);
      window.history.pushState({ ...(window.history.state || {}), dialog: mark }, "", window.location.href);
    }
    lockPageScroll();
    function handleKeyDown(event) {
      if (!isTopDialog(node)) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        leave();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !node.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown, true);
    return () => {
      document.removeEventListener("keydown", handleKeyDown, true);
      backdrop?.removeEventListener("mousedown", onBackdrop);
      const index = openDialogs.indexOf(node);
      if (index !== -1) openDialogs.splice(index, 1);
      dialogClosers.delete(node);
      dialogMarks.delete(node);
      // Its entry goes with it -- a moment later, so a view change made in
      // the same breath (a drawer's "View pull list") has written its own
      // address over the entry first, and there is nothing left to take.
      if (mark) window.setTimeout(() => { if (window.history.state?.dialog === mark) window.history.back(); }, 0);
      unlockPageScroll();
      // Return focus to whatever opened the dialog, not the top of the page.
      if (previouslyFocused instanceof HTMLElement && document.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
    };
  }, []);
  return ref;
}

// A dialog's close control: a glass circle in its corner. A drawer's (`drawer`)
// is a back arrow on a phone, for the reason DrawerTopBar gives.
export function DialogCloseButton({ onClose, label, drawer = false }) {
  const phone = usePhoneWidth();
  const back = drawer && phone;
  return <>
    {drawer ? null : <SheetGrabber onClose={onClose} />}
    <button type="button" className={`glass-button glass-button--icon modal-close${back ? " modal-back" : ""}`}
      onClick={(event) => (drawer ? onClose() : slideSheetAway(event.currentTarget.closest(".modal"), onClose))}
      aria-label={back ? "Back" : label}>{back ? <ArrowLeft size={20} /> : <X size={20} />}</button>
  </>;
}

// On a phone a dialog is a sheet from the bottom, as iOS 26 draws one: inset
// from the screen's edges, and with a grabber along its top that it follows
// under a finger. Let go past a quarter of its height, or with a flick, and it
// leaves; otherwise it settles back. A sheet taller than most of the screen
// (`detents`) opens at half height and is pulled up to full, then down to half
// and away. Above 640px dialogs are centred and the grabber is not drawn.
export const SHEET_QUERY = "(max-width: 640px)";
// A motion token's duration in milliseconds, as the stylesheet has it now
// (reduced motion collapses them all to 1ms).
export function motionMs(token) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  const ms = value.endsWith("ms") ? parseFloat(value) : value.endsWith("s") ? parseFloat(value) * 1000 : NaN;
  return Number.isFinite(ms) ? ms : 0;
}

// A sheet leaving slides down and its backdrop fades, however it is closed --
// Done, the backdrop, Escape, the back gesture -- as an iOS sheet does,
// rather than vanishing. `ms` is how long it takes: the token's, or less when
// a flick has already given it speed. Off a phone, or with reduced motion, it
// just closes.
export function slideSheetAway(sheet, then, ms = null) {
  const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  if (!sheet || still || !window.matchMedia?.(SHEET_QUERY).matches) { then(); return; }
  if (sheet.dataset.leaving) return;
  sheet.dataset.leaving = "true";
  // Still arriving: the opening animation would override the transform and
  // the sheet would vanish at its end instead of sliding.
  sheet.getAnimations?.().forEach((animation) => animation.finish());
  const duration = ms ?? motionMs("--motion-duration-sheet-exit");
  const backdrop = sheet.parentElement;
  sheet.style.transition = `transform ${duration}ms var(--motion-ease-sheet)`;
  sheet.style.transform = "translateY(110%)";
  if (backdrop) {
    backdrop.style.transition = `opacity ${duration}ms var(--motion-ease-sheet)`;
    backdrop.style.opacity = "0";
  }
  window.setTimeout(then, duration + 20);
}

// The sheet's grabber, and the pull that closes a sheet. The grabber follows a
// finger, mouse or pen, and pulls up to a taller detent. With `pullAnywhere`
// (the bell, the profiles, View & sort -- sheets with nothing to lose) a
// finger pulling down anywhere on the sheet, with everything under it
// scrolled to its top, moves the sheet too, as iOS does; a sheet holding a
// form keeps to its grabber, so a stray drag cannot throw the edits away.
// Past a quarter of its height, or on a flick, the sheet leaves; otherwise it
// settles back.
export function SheetGrabber({ onClose, detents = false, pullAnywhere = false }) {
  const ref = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const grabber = ref.current;
    const sheet = grabber?.parentElement;
    if (!grabber || !sheet || !window.matchMedia?.(SHEET_QUERY).matches) return undefined;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    // Half height only when the whole sheet would take most of the screen;
    // a shorter one opens at its own height, with nothing to pull up.
    if (detents && sheet.scrollHeight > window.innerHeight * 0.75) sheet.dataset.detent = "medium";
    let drag = null;
    // Back to where it rests, on the sheet's own curve: no bounce.
    function settle(transform) {
      const ms = motionMs("--motion-duration-sheet");
      sheet.style.transition = still ? "none" : `transform ${ms}ms var(--motion-ease-sheet)`;
      sheet.style.transform = transform;
      if (still) sheet.style.transition = ""; else window.setTimeout(() => { sheet.style.transition = ""; }, ms + 20);
    }
    function follow(dy) {
      // Down follows the finger; up past where it rests stiffens, unless there
      // is a taller detent to pull it to.
      sheet.style.transform = `translateY(${dy > 0 ? dy : dy / (sheet.dataset.detent === "medium" ? 1.4 : 4)}px)`;
    }
    function release(dy, at) {
      const speed = dy / Math.max(1, performance.now() - at);
      if (sheet.dataset.detent === "medium" && dy < -48) {
        sheet.dataset.detent = "large";
        settle("translateY(0)");
      } else if (sheet.dataset.detent === "large" && detents && dy > 48 && dy < sheet.offsetHeight * 0.5 && speed < 0.9) {
        sheet.dataset.detent = "medium";
        settle("translateY(0)");
      } else if (dy > sheet.offsetHeight * 0.25 || speed > 0.9) {
        // It carries on at the speed the finger let go at, rather than
        // slowing to the token's pace -- a flick leaves at once. The curve
        // starts about 2.2 times faster than its average, hence the factor.
        const remaining = sheet.offsetHeight * 1.1 - dy;
        const exit = motionMs("--motion-duration-sheet-exit");
        slideSheetAway(sheet, () => closeRef.current?.(), speed > 0.3 ? Math.max(180, Math.min(exit, remaining / speed * 2.2)) : exit);
      } else {
        settle("translateY(0)");
      }
    }
    function onDown(event) {
      if (event.button !== 0 || event.pointerType === "touch") return;
      drag = { y: event.clientY, at: performance.now(), dy: 0 };
      grabber.setPointerCapture(event.pointerId);
      sheet.style.transition = "none";
    }
    function onMove(event) {
      if (!drag) return;
      drag.dy = event.clientY - drag.y;
      follow(drag.dy);
    }
    function onUp() {
      if (!drag) return;
      const { dy, at } = drag;
      drag = null;
      release(dy, at);
    }
    // A finger. Nothing is decided on the first pixel: sideways is a swipe,
    // up is a scroll, and down inside anything with somewhere to scroll is a
    // scroll too (sheetPullDecision). Only a pull is taken from the browser.
    let touch = null;
    function scrollersFor(target) {
      const found = [];
      for (let node = target; node && node !== sheet.parentElement; node = node.parentElement) {
        if (node.scrollHeight > node.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(node).overflowY)) found.push(node);
        if (node === sheet) break;
      }
      return found;
    }
    function onTouchStart(event) {
      touch = null;
      if (event.touches.length !== 1 || sheet.dataset.leaving) return;
      if (event.target.closest?.("input, textarea, select, [data-sheet-no-drag]")) return;
      const onGrabber = grabber.contains(event.target);
      if (!onGrabber && !pullAnywhere) return;
      const point = event.touches[0];
      touch = {
        x: point.clientX, y: point.clientY, at: performance.now(), onGrabber,
        scrollers: onGrabber ? [] : scrollersFor(event.target), dragging: false, dy: 0,
      };
    }
    function onTouchMove(event) {
      if (!touch) return;
      const point = event.touches[0];
      if (!touch.dragging) {
        const decision = sheetPullDecision({
          dx: point.clientX - touch.x, dy: point.clientY - touch.y,
          atTop: touch.scrollers.every((node) => node.scrollTop <= 0),
          onGrabber: touch.onGrabber, pullAnywhere,
        });
        if (decision === "wait") return;
        if (decision === "pass") { touch = null; return; }
        touch = { ...touch, dragging: true, y: point.clientY, at: performance.now() };
        sheet.style.transition = "none";
      }
      if (event.cancelable) event.preventDefault();
      touch.dy = point.clientY - touch.y;
      follow(touch.dy);
    }
    function onTouchEnd() {
      const ended = touch;
      touch = null;
      if (ended?.dragging) release(ended.dy, ended.at);
    }
    grabber.addEventListener("pointerdown", onDown);
    grabber.addEventListener("pointermove", onMove);
    grabber.addEventListener("pointerup", onUp);
    grabber.addEventListener("pointercancel", onUp);
    sheet.addEventListener("touchstart", onTouchStart, { passive: true });
    sheet.addEventListener("touchmove", onTouchMove, { passive: false });
    sheet.addEventListener("touchend", onTouchEnd);
    sheet.addEventListener("touchcancel", onTouchEnd);
    return () => {
      grabber.removeEventListener("pointerdown", onDown);
      grabber.removeEventListener("pointermove", onMove);
      grabber.removeEventListener("pointerup", onUp);
      grabber.removeEventListener("pointercancel", onUp);
      sheet.removeEventListener("touchstart", onTouchStart);
      sheet.removeEventListener("touchmove", onTouchMove);
      sheet.removeEventListener("touchend", onTouchEnd);
      sheet.removeEventListener("touchcancel", onTouchEnd);
    };
  }, [detents, pullAnywhere]);
  return <div className="sheet-grabber" ref={ref} aria-hidden="true" />;
}

export const PHONE_QUERY = "(max-width: 640px)";
export const isPhoneWidth = () => Boolean(window.matchMedia?.(PHONE_QUERY).matches);

export function usePhoneWidth() {
  const [phone, setPhone] = useState(isPhoneWidth);
  useEffect(() => {
    const query = window.matchMedia?.(PHONE_QUERY);
    if (!query) return undefined;
    const change = () => setPhone(query.matches);
    change();
    query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);
  return phone;
}
