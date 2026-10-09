import { useEffect, useRef, useState } from "react";
import { DiscoverCover } from "../components/CoverArt.jsx";
import { ShelfBackIcon, ShelfNextIcon } from "../design-icons.jsx";
import { CaretRight } from "@phosphor-icons/react";

export function LibraryShelf({ title, items, onSeeAll, onOpen }) {
  const { scroller, atStart, atEnd, measure, page } = useShelfPaging([items.length]);
  if (!items.length) return null;
  return <section className="release-shelf library-shelf" aria-label={title}>
    <header>
      <div className="release-shelf-heading"><h2>{onSeeAll
        ? <button type="button" className="shelf-see-all" onClick={onSeeAll}>{title}<CaretRight size={16} weight="bold" aria-hidden="true" /></button>
        : title}</h2></div>
      <div className="shelf-scroll">
        <button type="button" onClick={() => page(-1)} disabled={atStart} aria-label={`Scroll ${title} back`}><ShelfBackIcon /></button>
        <button type="button" onClick={() => page(1)} disabled={atEnd} aria-label={`Scroll ${title} forward`}><ShelfNextIcon /></button>
      </div>
    </header>
    <div className="shelf-row" ref={scroller} onScroll={measure}>
      {items.map((card) => <article className="pull-card shelf-card" key={card.key}>
        <button type="button" className="discover-open shelf-card-art" onClick={() => onOpen(card)}
          aria-label={[card.title, card.note].filter(Boolean).join(", ")}>
          <DiscoverCover src={card.cover} alt="" glyph={30} />
          {card.progress ? <span className="profile-history-progress" style={{ "--progress": `${Math.round(card.progress * 100)}%` }} aria-hidden="true" /> : null}
        </button>
        <div className="pull-card-body">
          <h3 title={card.title}>{card.title}</h3>
          {card.note ? <small>{card.note}</small> : null}
        </div>
      </article>)}
    </div>
  </section>;
}

// The comic drawer's pieces (node-id=72-1306). A row pages by its own width,
// the way Discover's release shelves do, and says when there is nowhere left
// to go.
export function useShelfPaging(deps = []) {
  const scroller = useRef(null);
  const [atStart, setAtStart] = useState(true);
  const [atEnd, setAtEnd] = useState(false);
  function measure() {
    const node = scroller.current;
    if (!node) return;
    setAtStart(node.scrollLeft <= 1);
    setAtEnd(node.scrollLeft + node.clientWidth >= node.scrollWidth - 1);
  }
  useEffect(measure, deps);
  // A drawer that changes width can bring a row into or out of overflow.
  useEffect(() => {
    const node = scroller.current;
    if (!node || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  function page(direction) {
    const node = scroller.current;
    if (!node) return;
    // A row that bleeds to its container's edges pads its ends; a page is
    // the width between them, so it still lands on a card's edge.
    const style = getComputedStyle(node);
    const inner = node.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    node.scrollBy({ left: direction * inner, behavior: "smooth" });
  }
  return { scroller, atStart, atEnd, measure, page };
}
