import { useContext, useEffect, useRef, useState } from "react";
import { Host, useImageSrc } from "../host.js";
import { apiRequest, lastAnswer } from "../api.js";
import { artTone } from "../art-tone.js";
import { DiscoverCover, SeriesCover } from "../components/CoverArt.jsx";
import { LoadingIndicator } from "../components/LoadingIndicator";
import { LoadingSpinner } from "../components/glass.jsx";
import { DrawerCloseIcon, ReadingListIcon, ShelfBackIcon, ShelfNextIcon } from "../design-icons.jsx";
import { usePhoneWidth } from "../dialogs.jsx";
import { issueLabel } from "../discover.js";
import { identityKey } from "../editions.js";
import { useShelfPaging } from "../library/shelves.jsx";
import { RATING_SOURCES, filledStars, ratingForPress, ratingLabel } from "../ratings.js";
import { readingAriaLabel, readingNoun, readingVerb } from "../reading-target.js";
import { creatorRoleLabel } from "../run-details.js";
import { issueReadState } from "./inventory.jsx";
import { ArrowLeft, BookOpen, Check, CheckCircle, DownloadSimple, Star } from "@phosphor-icons/react";

// The skeleton occupies the loaded card's box exactly. A shelf that grows when
// its comics arrive moves everything under it, which is the moment the reader
// is most likely to be reaching for something.
export function PullCardSkeleton() {
  return <div className="pull-card pull-card-skeleton" aria-hidden="true">
    <span className="discover-cover" />
    <div className="pull-card-body"><h3><i /></h3><div className="pull-card-action"><i /></div></div>
  </div>;
}

export function useRunReading(version) {
  const [runs, setRuns] = useState(() => lastAnswer("/api/v1/reading/runs")?.runs ?? null);
  useEffect(() => {
    let live = true;
    apiRequest("/api/v1/reading/runs")
      .then((data) => { if (live) setRuns(data.runs || {}); })
      .catch(() => { if (live) setRuns((current) => current || {}); });
    return () => { live = false; };
  }, [version]);
  return runs;
}

export function formatLongDate(iso) {
  if (!iso) return "";
  const date = new Date(`${String(iso).slice(0, 10)}T12:00:00`);
  return Number.isNaN(date.getTime())
    ? "" : date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

// A drawer's colour, from its art (see art-tone.js). The page can only read
// the pixels of an image from this origin, so a remote cover is read through
// the server's small swatch of it. Tones are kept per image for the session.
export const ART_TONES = new Map();

export function useArtTone(src) {
  const pageSource = useContext(Host)?.pageSource || null;
  const [tone, setTone] = useState(() => (src && ART_TONES.get(src)) || null);
  useEffect(() => {
    if (!src) {
      setTone(null);
      return undefined;
    }
    if (ART_TONES.has(src)) {
      setTone(ART_TONES.get(src));
      return undefined;
    }
    let live = true;
    // Only a tone that resolved is kept. A swatch that failed to load -- a
    // blip on the proxy, a container restarting -- used to be remembered as
    // "no tone" for the rest of the session, so the run stayed untoned on
    // every reopen until a refresh emptied the map.
    const settle = (value) => {
      if (value) ART_TONES.set(src, value);
      if (live) setTone(value);
    };
    let local = false;
    try { local = new URL(src, window.location.href).origin === window.location.origin; } catch { local = false; }
    const image = new Image();
    image.decoding = "async";
    image.onload = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = 24;
        canvas.height = 24;
        const context = canvas.getContext("2d", { willReadFrequently: true });
        context.drawImage(image, 0, 0, 24, 24);
        settle(artTone(context.getImageData(0, 0, 24, 24).data));
      } catch {
        settle(null);
      }
    };
    image.onerror = () => settle(null);
    // Flipparr View's host hands back a local copy, fetched with its token;
    // the web loads its own origin's art directly and anyone else's through
    // the swatch proxy, which a canvas may read.
    if (pageSource) pageSource(src).then((ready) => { if (live) image.src = ready; }, () => settle(null));
    else image.src = local ? src : `/api/v1/art-swatch?src=${encodeURIComponent(src)}`;
    return () => { live = false; };
  }, [src, pageSource]);
  return tone;
}

// The drawer's class and style for a tone, or nothing for art without one.
export function toneProps(tone) {
  return tone
    ? { className: " toned", style: { "--art-tone": tone.tone, "--art-tone-deep": tone.deep } }
    : { className: "", style: undefined };
}

// On a phone a drawer is a page pushed over the one it came from, so it
// leaves with the back arrow, as an iPhone page does; above 640px it is a
// panel beside the page and closes with the X. `onBack`, when the drawer was
// opened from another (a run from its collection), is where back goes.
export function DrawerTopBar({ title, onClose, onBack, closeLabel, stacked = false, children }) {
  const phone = usePhoneWidth();
  const barRef = useRef(null);
  const [pinned, setPinned] = useState(false);
  useEffect(() => {
    const drawer = barRef.current?.closest(".series-drawer");
    if (!drawer) return undefined;
    let frame = 0;
    function update() {
      frame = 0;
      const y = drawer.scrollTop;
      const hero = drawer.querySelector(".comic-drawer-hero");
      const heading = drawer.querySelector(".comic-drawer-titles h2");
      drawer.style.setProperty("--hero-scroll", `${Math.max(0, y)}px`);
      drawer.style.setProperty("--hero-pull", `${Math.max(0, -y)}px`);
      drawer.style.setProperty("--hero-progress", String(Math.min(1, Math.max(0, y / (hero?.offsetHeight || 1)))));
      if (heading && barRef.current) {
        setPinned(heading.getBoundingClientRect().bottom <= barRef.current.getBoundingClientRect().bottom);
      }
    }
    function onScroll() {
      if (!frame) frame = requestAnimationFrame(update);
    }
    update();
    drawer.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      drawer.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(frame);
    };
  }, []);
  return <div className={`comic-drawer-bar${pinned ? " pinned" : ""}`} ref={barRef}>
    {/* Back at every width while a section is pushed: a close button there
        would throw away the drawer rather than the section. */}
    {stacked || phone
      ? <button type="button" className="glass-button glass-button--icon comic-drawer-close" onClick={onBack || onClose} aria-label="Back"><ArrowLeft size={20} /></button>
      : <button type="button" className="glass-button glass-button--icon comic-drawer-close" onClick={onClose} aria-label={closeLabel}><DrawerCloseIcon size={null} /></button>}
    <span className="comic-drawer-bar-title" aria-hidden="true">{title}</span>
    <span className="comic-drawer-bar-end">{children}</span>
  </div>;
}

// The run drawer's header, for the Discover drawers: the cover as art behind a
// violet-to-black scrim, the cover itself, the title, a byline and chips. The
// same classes as the Comics drawer, so the two read as one kind of panel.
export function DiscoverDrawerHero({ art, cover, title, titleId, byline, onClose, closeLabel, children, actions = null, ownership = null }) {
  // A picture that cannot be fetched is no backdrop, rather than a broken image.
  const [failed, setFailed] = useState(null);
  const imageSrc = useImageSrc();
  const shown = art && failed !== art ? art : null;
  return <><DrawerTopBar title={title} onClose={onClose} closeLabel={closeLabel}>{actions}</DrawerTopBar><header className="comic-drawer-hero">
    {shown ? <>
      <img className="comic-drawer-backdrop" src={imageSrc(shown)} alt="" aria-hidden="true" key={shown} onError={() => setFailed(shown)} />
      <img className="comic-drawer-backdrop blurred" src={imageSrc(shown)} alt="" aria-hidden="true" key={`${shown}-blurred`} onError={() => setFailed(shown)} />
    </> : null}
    <span className="comic-drawer-scrim" aria-hidden="true" />
    <div className="comic-drawer-identity">
      <div className="comic-drawer-cover">{cover}</div>
      <div className="comic-drawer-copy">
        <div className="comic-drawer-titles">
          <h2 id={titleId}>{title}</h2>
          {byline ? <p>{byline}</p> : null}
        </div>
        {children ? <div className="comic-drawer-statuses">{children}</div> : null}
        {ownership}
      </div>
    </div>
  </header></>;
}

/**
 * The one thing a drawer is for, as the big pill under its header -- Read in
 * the library's drawers (`ReadRunButton`), and here Pull, Request or Save in
 * Discover's and Read next after a comic. The verb is bright, what it acts on
 * beside it, and a line under it says what happened or what will.
 */
export function DrawerPrimary({ icon, verb, noun = "", onClick, disabled = false, busy = false, settled = false, note = "", label }) {
  return <div className="comic-drawer-actions">
    <button type="button" className={`primary-button comic-drawer-read${settled ? " comic-drawer-read--settled" : ""}`}
      onClick={onClick} disabled={disabled || busy} aria-busy={busy || undefined} aria-label={label}>
      {busy ? <LoadingIndicator size={20} /> : icon}
      <span>{verb}{noun ? <b>{noun}</b> : null}</span>
    </button>
    {note ? <small className="comic-drawer-action-note" role="status" aria-live="polite">{note}</small> : null}
  </div>;
}

// Discover's drawers' tabs: the library drawers' bar, glass pill and all.
export function DrawerTabs({ tabs, tab, onTab, label, tabsRef, glass, disabled = () => false }) {
  return <nav className="drawer-tabs comic-drawer-tabs" aria-label={label} ref={tabsRef}>
    <span className="comic-drawer-tab-glass glass-indicator" aria-hidden="true" style={glass || { opacity: 0 }} />
    {tabs.map(([id, text]) => <button type="button" className={tab === id ? "active" : ""} aria-current={tab === id ? "page" : undefined} onClick={() => onTab(id)} key={id} disabled={disabled(id)}>{text}</button>)}
  </nav>;
}

// Where a description or a match came from, linked back to it. Comic Vine
// asks every page that uses its data to link back; Google Books asks for
// "Powered by Google" beside its results; the rest are owed the credit.
export const PROVIDER_HOMES = {
  "Metron": "https://metron.cloud/",
  "Comic Vine": "https://comicvine.gamespot.com/",
  "Grand Comics Database": "https://www.comics.org/",
  "GCD": "https://www.comics.org/",
  "Open Library": "https://openlibrary.org/",
  "Google Books": "https://books.google.com/",
};

export function ProviderCredit({ source, url }) {
  const href = /^https?:\/\//i.test(String(url || "")) ? url : PROVIDER_HOMES[source];
  const name = source === "Google Books" ? "Google Books · Powered by Google" : source;
  return href ? <a className="provider-credit" href={href} target="_blank" rel="noopener noreferrer">{name}</a> : name;
}

// What a run is about, in its catalog's own words. Clamped: Comic Vine's lead
// paragraph can run long, and in both drawers the issues are the point.
// `heading` is null where the text sits under a heading of its own already,
// as the finish drawer's next-issue card does.
export function RunSynopsis({ text, source, sourceUrl, sourcePrefix = "From", loading = false, heading = "Story" }) {
  const [open, setOpen] = useState(false);
  if (loading) return <section className="run-synopsis" role="status" aria-busy="true" aria-label="Loading the story">
    <span className="discover-drawer-lines" aria-hidden="true"><i /><i /><i /></span>
  </section>;
  if (!text) return null;
  const long = text.length > 220;
  return <section className="run-synopsis" aria-label={heading || "Story"}>
    {heading ? <h3>{heading}</h3> : null}
    <p className={long && !open ? "clamped" : ""}>{text}</p>
    <footer>
      {long ? <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>{open ? "Show less" : "Read more"}</button> : null}
      {source ? <small>{sourcePrefix} <ProviderCredit source={source} url={sourceUrl} /></small> : null}
    </footer>
  </section>;
}

// Following is on or off, so it is a switch -- not a button that changes into
// "View wanted issues" once pressed, beside a second button that undoes it.
export function FollowSwitch({ following, busy = false, label, onChange }) {
  return <label className={`follow-switch${busy ? " busy" : ""}`}>
    <input type="checkbox" role="switch" checked={following} disabled={busy}
      aria-checked={following} onChange={(event) => onChange(event.target.checked)} />
    <i aria-hidden="true" />
    <span>{busy ? <LoadingSpinner size={14} /> : null}{label}</span>
  </label>;
}

// "#1 · The Dark Knight" -- the run's own title would say the same thing on
// every card, so the number leads and the issue's title follows when it has
// one worth showing.
export function issueCardLabel(issue, medium) {
  const number = issueLabel(issue.number, medium);
  const generic = !issue.title || identityKey(issue.title) === identityKey(`Issue ${issue.number}`)
    || (medium === "manga" && identityKey(issue.title) === identityKey(`Volume ${issue.number}`));
  return generic ? number : `${number} · ${issue.title}`;
}

/**
 * The run's one reading action, worded by what the server resolved.
 *
 * It renders nothing at all when there is nothing readable. A greyed-out Read
 * would say the library holds a comic it does not, and "why is this disabled"
 * is a worse question than "where is the button".
 */
/** On this profile's reading list, or not: the bookmark, as Plex's watchlist. */
/**
 * The drawer's Read button, with Download for the whole run, story arc or
 * collection to its left (the host's `groupDownload`: Flipparr View's,
 * Phase 6; the owner, 2026-10-09). Where the host gives none -- the web --
 * this is the Read button alone, exactly as before.
 */
export function ReadWithDownload({ group, children }) {
  const describe = useContext(Host)?.groupDownload;
  const state = describe && group ? describe(group) : null;
  if (!state) return children;
  return <div className="comic-drawer-read-row">
    <button type="button" className={`secondary-button comic-drawer-download${state.done ? " done" : ""}`}
      aria-busy={state.busy || undefined} aria-label={state.label} title={state.label} onClick={state.onPress}>
      {state.busy ? <LoadingSpinner size={20} /> : state.done ? <CheckCircle size={24} weight="fill" /> : <DownloadSimple size={24} />}
    </button>
    {children}
  </div>;
}

export function ReadingListToggle({ on, title, onChange, className = "glass-button glass-button--icon comic-drawer-follow-button", label = false }) {
  return <button type="button" className={`${className}${on ? " active" : ""}`} aria-pressed={on}
    aria-label={on ? `Take ${title} off your reading list` : `Add ${title} to your reading list`}
    title={on ? "On your reading list" : "Add to reading list"} onClick={() => onChange(!on)}>
    <ReadingListIcon size={20} />{label ? (on ? " On your reading list" : " Add to reading list") : <b>{on ? "Listed" : "Read later"}</b>}
  </button>;
}

export function ReadRunButton({ reading, title, medium, onRead }) {
  const target = reading?.resume;
  // "Continue Issue #7", about the comic that opens rather than the run: the
  // verb bright, the comic it names beside it. Starting the run over lives in
  // Advanced -- it is a tool, not something a reader reaches for mid-run.
  const verb = readingVerb(target);
  if (!verb || !onRead) return null;
  const issue = readingNoun(target, medium);
  return <button type="button" className="primary-button comic-drawer-read"
    onClick={() => onRead({ id: target.fileId })}
    aria-label={readingAriaLabel(target, medium, title)}>
    <BookOpen size={20} weight="fill" />
    <span>{verb}{issue ? <b>{issue}</b> : null}</span>
  </button>;
}

/**
 * Five stars, and whose they are.
 *
 * Read-only when it is showing the average of a run's rated issues: those
 * stars are a summary, and pressing one would silently turn it into a rating
 * of the run itself. The control says which it is in its accessible name and
 * draws the average in outline so the two never look alike.
 */
export function StarRating({ rating, title, size = 18, onRate }) {
  const [hover, setHover] = useState(0);
  const average = rating.source === RATING_SOURCES.average;
  const shown = hover || filledStars(rating.value);
  const label = ratingLabel(rating, title);
  if (!onRate || average) {
    return <span className={`star-rating${average ? " average" : ""}`} role="img" aria-label={label} title={label}>
      {[1, 2, 3, 4, 5].map((star) => <Star key={star} size={size}
        weight={shown >= star ? "fill" : shown >= star - 0.5 ? "duotone" : "regular"} />)}
      {average ? <small>{rating.value}</small> : null}
    </span>;
  }
  return <span className="star-rating" role="group" aria-label={label} onMouseLeave={() => setHover(0)}>
    {[1, 2, 3, 4, 5].map((star) => <button type="button" key={star}
      onMouseEnter={() => setHover(star)}
      onClick={() => onRate(ratingForPress(star, rating.value))}
      aria-pressed={rating.value >= star}
      aria-label={star === rating.value
        ? `Remove your rating of ${star} star${star === 1 ? "" : "s"}`
        : `Rate ${star} star${star === 1 ? "" : "s"}`}>
      <Star size={size} weight={shown >= star ? "fill" : "regular"} />
    </button>)}
  </span>;
}

export function ComicDrawerRow({ title, count, children }) {
  const { scroller, atStart, atEnd, measure, page } = useShelfPaging([count]);
  // When every card fits there is nothing to page through, so no arrows.
  const fits = atStart && atEnd;
  return <section className="comic-drawer-row" aria-label={title}>
    <header>
      <h3>{title}</h3>
      {fits ? null : <div className="shelf-scroll">
        <button type="button" onClick={() => page(-1)} disabled={atStart} aria-label={`Scroll ${title} back`}><ShelfBackIcon /></button>
        <button type="button" onClick={() => page(1)} disabled={atEnd} aria-label={`Scroll ${title} forward`}><ShelfNextIcon /></button>
      </div>}
    </header>
    <div className="comic-drawer-shelf" ref={scroller} onScroll={measure}>{children}</div>
  </section>;
}

// An issue you do not own keeps its card, dimmed, with the same state badge
// the Issues tab gives it -- a gap in a run should look like a gap.
export function ComicDrawerIssueCard({ issue, medium, onOpen, onRead, readingFiles }) {
  const owned = issue.ownership !== "unowned";
  const state = issue.ownership === "collection" ? "In volume"
    : owned ? null
    : issue.releaseState === "upcoming" ? "Upcoming"
    : issue.releaseState === "unknown" ? "Date needed" : "Missing";
  const label = issueCardLabel(issue, medium);
  // A cover you can read opens the comic; one you cannot still opens the list.
  // The two are told apart by the label, which is what Komga and Kavita do.
  const read = onRead ? issueReadState(issue, readingFiles) : null;
  return <article className={`pull-card comic-drawer-card${owned ? "" : " unowned"}`}>
    <button type="button" className="discover-open"
      onClick={read ? () => onRead({ id: issue.fileId }) : onOpen}
      aria-label={read
        ? `${read.label} ${label}${read.detail ? `, ${read.detail}` : ""}`
        : `${label}${state ? `, ${state.toLowerCase()}` : ""}. Show all issues`}>
      <span className="comic-drawer-card-art">
        <DiscoverCover src={issue.fileCover || issue.cover} alt="" glyph={30} />
        {state ? <span className={`ownership-source ${issue.ownership} ${issue.acquisitionState || ""}`}>{state}</span> : null}
        {read ? <span className="issue-tile-read" aria-hidden="true"><BookOpen size={14} weight="fill" /></span> : null}
        {read?.fraction ? <span className="read-progress-bar" aria-hidden="true"><i style={{ width: `${Math.round(read.fraction * 100)}%` }} /></span> : null}{read?.finished ? <span className="issue-tile-mark done" aria-hidden="true"><Check size={13} weight="bold" /></span> : null}
      </span>
    </button>
    <div className="pull-card-body"><h3 title={label}>{label}</h3></div>
  </article>;
}

export function ComicDrawerRunCard({ run, onOpen }) {
  return <article className="pull-card comic-drawer-card">
    <button type="button" className="discover-open" onClick={() => onOpen(run)} aria-label={`Open ${run.title}${run.year ? ` (${run.year})` : ""}`}>
      <span className="comic-drawer-card-art"><span className="discover-cover"><SeriesCover series={run} decorative /></span></span>
    </button>
    <div className="pull-card-body"><h3 title={run.title}>{run.title}</h3></div>
  </article>;
}

// Everyone credited, as the file writes it: one run of names. Past two lines
// it folds, and opening it lists each person with what they did.
export function ComicDrawerCreators({ creators }) {
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const namesRef = useRef(null);
  useEffect(() => {
    const node = namesRef.current;
    if (!node || open) return;
    setOverflows(node.scrollHeight > node.clientHeight + 1);
  }, [creators, open]);
  return <section className="comic-drawer-creators" aria-label="Creators">
    <h3>Creators</h3>
    {open
      ? <ul className="comic-drawer-creator-list">{creators.map((creator) => <li key={creator.name}><strong>{creator.name}</strong><span>{creatorRoleLabel(creator.roles)}</span></li>)}</ul>
      : <p ref={namesRef} className="comic-drawer-creator-names">{creators.map((creator) => creator.name).join(", ")}</p>}
    {overflows || open ? <button type="button" className="comic-drawer-more" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{open ? "Show less" : `Show all ${creators.length} creators`}</button> : null}
  </section>;
}
