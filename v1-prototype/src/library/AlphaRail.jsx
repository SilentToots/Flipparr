import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ALPHA_LETTERS, alphaSections, titleLetter } from "../library.js";

/**
 * The A-Z rail beside a title-sorted grid, as Plex has it (owner,
 * 2026-10-03): # and A-Z down the right edge. A letter jumps to its first
 * card, or the next letter's when it has none (iOS's section index);
 * dragging along the rail scrubs, with the letter large beside the finger;
 * the letter at the top of the screen is lit. One tab stop, arrows to move.
 * Its cards are the grid's own, in order (`.series-grid > *`, list rows).
 */
export function AlphaRail({ items, containerRef }) {
  const sections = useMemo(() => alphaSections(items), [items]);
  const railRef = useRef(null);
  const [current, setCurrent] = useState(null);
  const [focused, setFocused] = useState(null);
  const [bubble, setBubble] = useState(null);
  const [compact, setCompact] = useState(false);
  const scrubbing = useRef(false);
  // The letter last jumped to stays lit while its card is on screen -- near
  // the end the page cannot scroll its row to the top, and the first card
  // showing is an earlier letter's.
  const jumped = useRef(null);
  const cards = useCallback(() => [...(containerRef.current?.querySelectorAll(".series-grid > *, .series-table > .series-row") || [])], [containerRef]);
  const landing = useCallback((letter) => {
    const at = ALPHA_LETTERS.indexOf(letter);
    for (let index = at; index < ALPHA_LETTERS.length; index += 1) if (ALPHA_LETTERS[index] in sections) return ALPHA_LETTERS[index];
    for (let index = at - 1; index >= 0; index -= 1) if (ALPHA_LETTERS[index] in sections) return ALPHA_LETTERS[index];
    return null;
  }, [sections]);
  const jump = useCallback((letter, smooth) => {
    const target = landing(letter);
    const card = target ? cards()[sections[target]] : null;
    if (!card) return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    card.scrollIntoView({ block: "start", behavior: smooth && !still ? "smooth" : "auto" });
    jumped.current = { letter: target, card, at: performance.now() };
    setCurrent(target);
  }, [landing, cards, sections]);
  // The lit letter: the first card whose bottom is below the header.
  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      if (scrubbing.current) return;
      const list = cards();
      const top = (parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--page-header-height")) || 0) + 8;
      if (jumped.current) {
        const box = jumped.current.card.getBoundingClientRect();
        // Through the smooth scroll on its way there, then while it shows.
        const travelling = performance.now() - jumped.current.at < 1200;
        if (jumped.current.card.isConnected && (travelling || (box.bottom > top && box.top < window.innerHeight))) { setCurrent(jumped.current.letter); return; }
        jumped.current = null;
      }
      let low = 0;
      let high = list.length - 1;
      while (low < high) {
        const middle = (low + high) >> 1;
        if (list[middle].getBoundingClientRect().bottom > top) high = middle; else low = middle + 1;
      }
      const item = items[low];
      if (item) setCurrent(titleLetter(item.title ?? item.name));
    };
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(update); };
    window.addEventListener("scroll", onScroll, { passive: true });
    update();
    return () => { window.removeEventListener("scroll", onScroll); cancelAnimationFrame(frame); };
  }, [items, cards]);
  // A short screen shows every other letter, a dot for the rest.
  useLayoutEffect(() => {
    const rail = railRef.current;
    if (!rail || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(() => setCompact(rail.clientHeight / ALPHA_LETTERS.length < 14));
    observer.observe(rail);
    return () => observer.disconnect();
  }, []);
  // The letter under the finger, by the letters' own places: they are
  // centred in the rail, not spread over all of it.
  const letterAt = (clientY) => {
    const rail = railRef.current;
    const buttons = [...rail.querySelectorAll("button")];
    let index = buttons.findIndex((button) => clientY < button.getBoundingClientRect().bottom);
    if (index < 0) index = buttons.length - 1;
    return { letter: ALPHA_LETTERS[index], y: clientY - rail.getBoundingClientRect().top };
  };
  function scrub(event) {
    const { letter, y } = letterAt(event.clientY);
    setBubble({ letter: landing(letter) || letter, y });
    jump(letter, false);
  }
  function onKeyDown(event) {
    const order = { ArrowDown: 1, ArrowUp: -1 };
    const from = Math.max(0, ALPHA_LETTERS.indexOf(focused || current || "A"));
    const to = event.key === "Home" ? 0 : event.key === "End" ? ALPHA_LETTERS.length - 1 : event.key in order ? from + order[event.key] : null;
    if (to === null || to < 0 || to >= ALPHA_LETTERS.length) return;
    event.preventDefault();
    setFocused(ALPHA_LETTERS[to]);
    railRef.current?.querySelectorAll("button")[to]?.focus();
  }
  const tabStop = focused || current || landing("A");
  return <nav className={`alpha-rail${compact ? " alpha-rail--compact" : ""}`} aria-label="Jump to letter" ref={railRef} onKeyDown={onKeyDown}
    onPointerDown={(event) => { if (event.button !== 0) return; scrubbing.current = true; event.currentTarget.setPointerCapture?.(event.pointerId); scrub(event); }}
    onPointerMove={(event) => { if (scrubbing.current) scrub(event); }}
    onPointerUp={() => { scrubbing.current = false; setBubble(null); }}
    onPointerCancel={() => { scrubbing.current = false; setBubble(null); }}>
    {ALPHA_LETTERS.map((letter, index) => {
      const present = letter in sections;
      return <button type="button" key={letter} tabIndex={letter === tabStop ? 0 : -1}
        className={`${letter === current ? "current" : ""}${present ? "" : " empty"}`}
        aria-current={letter === current ? "true" : undefined} aria-disabled={present ? undefined : "true"}
        aria-label={present ? `Jump to titles starting with ${letter === "#" ? "a number or symbol" : letter}` : `No titles starting with ${letter === "#" ? "a number or symbol" : letter}`}
        onFocus={() => setFocused(letter)}
        // A pointer has jumped already on its way down; this is the keyboard's.
        onClick={(event) => { if (event.detail === 0 && present) jump(letter, true); }}>
        <span>{compact && index % 2 === 1 ? "\u2022" : letter}</span>
      </button>;
    })}
    {bubble ? <span className="alpha-bubble glass-capsule" style={{ top: bubble.y }} aria-hidden="true">{bubble.letter}</span> : null}
  </nav>;
}
