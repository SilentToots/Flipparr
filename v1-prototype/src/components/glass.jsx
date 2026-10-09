import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useDialog } from "../dialogs.jsx";
import { LoadingIndicator } from "./LoadingIndicator";
import { CaretUpDown, Check } from "@phosphor-icons/react";

// One glass pill that slides to whichever item is selected, as iOS does,
// rather than each item lighting up on its own. It is measured before paint so
// it never lands late, and only animates once it has a position to animate
// from. The phone's tab bar and the comic drawer's tabs share it.
// A damped spring, sampled into a CSS linear() curve.
//
// iOS's motion is spring based rather than bezier: it carries past its target
// and settles back, which no cubic-bezier can say. linear() can, by naming
// enough points along the real curve. Apple's own tab bar is quoted around
// response 0.3 with a damping fraction near 0.6; this is a little stiffer, so
// the pill settles rather than wobbles.
export const GLASS_SPRING_DURATION = 460;

export function springEasing(response = 0.3, damping = 0.66, samples = 40) {
  const omega = (2 * Math.PI) / response;
  const omegaDamped = omega * Math.sqrt(1 - damping * damping);
  const seconds = GLASS_SPRING_DURATION / 1000;
  const points = [];
  for (let step = 0; step <= samples; step += 1) {
    const time = (step / samples) * seconds;
    const decay = Math.exp(-damping * omega * time);
    const value = 1 - decay * (
      Math.cos(omegaDamped * time) + ((damping * omega) / omegaDamped) * Math.sin(omegaDamped * time)
    );
    points.push(Number(value.toFixed(4)));
  }
  // The last point has to be exactly 1, or the pill settles slightly off.
  points[points.length - 1] = 1;
  return `linear(${points.join(",")})`;
}

export const GLASS_TRAVEL_EASING = springEasing();

// Something with weight, thrown: it stretches along the way it is going,
// arrives compressed, and springs back. The stretch follows the distance
// travelled, so a neighbouring tab barely deforms and a jump across the bar
// does it visibly.
export function animateGlassIndicator(glass, from, to) {
  if (!glass || typeof glass.animate !== "function") return;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) return;
  const distance = Math.abs(to.left - from.left);
  if (distance < 1) return;
  const pull = Math.min(0.22, distance / Math.max(to.width * 4, 1));
  const options = { duration: GLASS_SPRING_DURATION, fill: "none" };
  // Two animations rather than one: position springs, and the squish runs on
  // its own shorter curve. Separate properties, so they do not overwrite each
  // other the way two transforms would.
  glass.animate(
    [
      { translate: `${from.left}px ${from.top}px`, width: `${from.width}px`, height: `${from.height}px` },
      { translate: `${to.left}px ${to.top}px`, width: `${to.width}px`, height: `${to.height}px` },
    ],
    { ...options, easing: GLASS_TRAVEL_EASING },
  );
  glass.animate(
    [
      { scale: "1 1", offset: 0 },
      { scale: `${(1 + pull).toFixed(3)} ${(1 - pull * 0.55).toFixed(3)}`, offset: 0.35 },
      { scale: `${(1 - pull * 0.42).toFixed(3)} ${(1 + pull * 0.32).toFixed(3)}`, offset: 0.68 },
      { scale: "1 1", offset: 1 },
    ],
    { ...options, easing: "cubic-bezier(.33, .1, .24, 1)" },
  );
}

// A choice of one, as SegmentedTabs draws it but choosing a setting rather
// than a page: grid or list, runs or collections. An item with only an icon
// names itself with `title`.
export function GlassSegmented({ label, items, value, onChange, className = "" }) {
  const [trackRef, glass] = useGlassIndicator(".segmented-tab.active", [value, items.map((item) => item.id).join()]);
  return <div className={`segmented-tabs ${className}`.trim()} role="radiogroup" aria-label={label} ref={trackRef}>
    <span className="glass-indicator" aria-hidden="true" style={glass || { opacity: 0 }} />
    {items.map((item) => <button type="button" role="radio" aria-checked={value === item.id}
      className={`segmented-tab${item.label ? "" : " segmented-tab--icon"}${value === item.id ? " active" : ""}`}
      aria-label={item.label ? undefined : item.title} title={item.label ? undefined : item.title}
      disabled={item.disabled} onClick={() => onChange(item.id)} key={item.id}>{item.icon}{item.label}</button>)}
  </div>;
}

export function useGlassIndicator(selector, deps, quietRef = null) {
  const containerRef = useRef(null);
  const [style, setStyle] = useState(null);
  // Where the pill was last placed, so a move knows where it is coming from.
  const placedRef = useRef(null);
  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    function place() {
      // A container taken out of the document reports every size as zero, and
      // the observer fires on the way out. Measuring then threw the pill's
      // position away, so a tab strip that unmounted and came back -- the run
      // drawer's, while Edit is open -- returned with no tab marked at all.
      if (!container.isConnected) return;
      // Told to keep quiet -- the container is mid-animation -- the pill is
      // hidden and forgets where it was, so when it is placed again it
      // appears there rather than travelling from where the animation began.
      if (quietRef?.current) {
        setStyle(null);
        placedRef.current = null;
        return;
      }
      const active = container.querySelector(selector);
      if (!active || !active.offsetWidth) {
        setStyle(null);
        placedRef.current = null;
        return;
      }
      const next = {
        left: active.offsetLeft, top: active.offsetTop,
        width: active.offsetWidth, height: active.offsetHeight,
      };
      setStyle({
        translate: `${next.left}px ${next.top}px`,
        width: `${next.width}px`,
        height: `${next.height}px`,
      });
      const previous = placedRef.current;
      placedRef.current = next;
      // Only a pill that moves is animated. Re-placing it where it already
      // is -- a label beside it changed -- ran a no-op animation on a
      // backdrop-filter layer, and Safari left the glyph next to it
      // unpainted (the "I" of Issues, 2026-09-28).
      const moved = previous && ["left", "top", "width", "height"].some((key) => previous[key] !== next[key]);
      if (container.dataset.glassPlaced && moved) {
        animateGlassIndicator(container.querySelector(".glass-indicator"), previous, next);
      }
      if (!container.dataset.glassPlaced) requestAnimationFrame(() => { container.dataset.glassPlaced = "true"; });
    }
    place();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(place);
    observer.observe(container);
    [...container.children].filter((child) => !child.classList.contains("glass-indicator")).forEach((child) => observer.observe(child));
    return () => observer.disconnect();
  }, deps);
  return [containerRef, style];
}

// A choice from a list, as iOS 26's pop-up button: a capsule showing the
// current value, which opens a glass menu of the options with a check on the
// chosen one. It replaces the native select, whose open list the browser
// draws in its own style. The menu is drawn over everything (a portal, placed
// from the button's position) so a dialog's scrolling body cannot clip it,
// and it joins the dialog stack, so Escape closes the menu and not the dialog
// under it. `label` names the control for assistive technology.
export function GlassSelect({ value, options, onChange, label, placeholder = "", disabled = false, className = "" }) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef(null);
  // With a placeholder, nothing chosen shows it, muted; without one, the
  // first option stands in, as a native select's does.
  const current = options.find((option) => String(option.value) === String(value)) || (placeholder ? null : options[0]);
  // Choosing or Escape hands focus back to the button, as a native select
  // does; a tap elsewhere leaves it where the tap put it. Safari does not
  // focus a button on click, so nothing can be left to the browser.
  function close(refocus) {
    setOpen(false);
    // After the menu has gone and its dialog-stack cleanup has run; a timeout
    // rather than a frame, which a background tab never draws.
    if (refocus) window.setTimeout(() => triggerRef.current?.focus(), 0);
  }
  function openWith(event) {
    if (disabled) return;
    if (event.type === "keydown") {
      if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
      event.preventDefault();
    }
    setOpen(true);
  }
  return <>
    <button type="button" ref={triggerRef} className={`glass-select ${className}`.trim()} disabled={disabled}
      aria-haspopup="listbox" aria-expanded={open} aria-label={`${label}: ${current?.label ?? placeholder}`}
      onClick={() => (open ? setOpen(false) : openWith({ type: "click" }))} onKeyDown={openWith}>
      <span className={current ? "" : "glass-select-placeholder"}>{current ? current.label : placeholder}</span><CaretUpDown size={16} aria-hidden="true" />
    </button>
    {open ? <GlassSelectMenu options={options} value={current?.value} label={label} anchor={triggerRef}
      onChoose={(next) => { close(true); if (String(next) !== String(value)) onChange(next); }}
      onClose={() => close(true)} onDismiss={() => close(false)} /> : null}
  </>;
}

export function GlassSelectMenu({ options, value, label, anchor, onChoose, onClose, onDismiss }) {
  const menuRef = useDialog(onClose);
  const [place, setPlace] = useState(null);
  // Under the button, or over it when there is more room above; never wider
  // than the window, and scrolling past its own height.
  useLayoutEffect(() => {
    const button = anchor.current?.getBoundingClientRect();
    const menu = menuRef.current;
    if (!button || !menu) return;
    const gap = 6;
    const margin = 12;
    const below = window.innerHeight - button.bottom - gap - margin;
    const above = button.top - gap - margin;
    const height = menu.scrollHeight;
    const up = height > below && above > below;
    const maxHeight = Math.max(120, up ? above : below);
    const width = Math.min(Math.max(button.width, 220), window.innerWidth - margin * 2);
    const left = Math.min(Math.max(margin, button.left), window.innerWidth - margin - width);
    setPlace({
      left, width, maxHeight,
      ...(up ? { bottom: window.innerHeight - button.top + gap } : { top: button.bottom + gap }),
      transformOrigin: up ? "bottom left" : "top left",
    });
  }, []);
  // The chosen option has focus once placed, as a native list opens on it.
  useEffect(() => {
    if (!place) return;
    (menuRef.current?.querySelector('[aria-selected="true"]') || menuRef.current?.querySelector('[role="option"]'))?.focus();
  }, [place]);
  // A tap outside closes it; so does the page or dialog scrolling under it,
  // which would leave it hanging away from its button.
  useEffect(() => {
    function outside(event) {
      if (menuRef.current?.contains(event.target) || anchor.current?.contains(event.target)) return;
      onDismiss();
    }
    function scrolled(event) {
      if (!menuRef.current?.contains(event.target)) onDismiss();
    }
    document.addEventListener("pointerdown", outside, true);
    window.addEventListener("scroll", scrolled, true);
    window.addEventListener("resize", onDismiss);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("scroll", scrolled, true);
      window.removeEventListener("resize", onDismiss);
    };
  }, [onDismiss]);
  function onKeyDown(event) {
    const items = [...(menuRef.current?.querySelectorAll('[role="option"]') || [])];
    const index = items.indexOf(document.activeElement);
    let next = null;
    if (event.key === "ArrowDown") next = index + 1;
    else if (event.key === "ArrowUp") next = index - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    else if (event.key.length === 1 && /\S/.test(event.key)) {
      // Type-ahead: the next option starting with the letter pressed.
      const letter = event.key.toLowerCase();
      const order = [...items.slice(index + 1), ...items.slice(0, index + 1)];
      order.find((item) => item.textContent.trim().toLowerCase().startsWith(letter))?.focus();
      return;
    }
    if (next === null) return;
    event.preventDefault();
    items[(next + items.length) % items.length]?.focus();
  }
  return createPortal(<div className="glass-menu glass-select-menu" ref={menuRef} role="listbox" aria-label={label}
    style={place ? { ...place } : { visibility: "hidden", top: 0, left: 0 }} onKeyDown={onKeyDown}
    onMouseDown={(event) => event.stopPropagation()}>
    {options.map((option) => {
      const chosen = String(option.value) === String(value);
      return <button type="button" role="option" aria-selected={chosen} className={chosen ? "selected" : ""}
        onClick={() => onChoose(option.value)} key={String(option.value)}>
        <span className="sort-menu-check" aria-hidden="true">{chosen ? <Check size={16} weight="bold" /> : null}</span>
        <span>{option.label}</span>
      </button>;
    })}
  </div>, document.body);
}

export const LoadingSpinner = LoadingIndicator;
