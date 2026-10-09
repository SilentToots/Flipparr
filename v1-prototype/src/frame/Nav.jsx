import { useContext, useEffect, useRef, useState } from "react";
import { FlipparrMark } from "../brand.jsx";
import { useGlassIndicator } from "../components/glass.jsx";
import { countUpDuration, countUpValue } from "../count-up.js";
import { ComicsIcon, DiscoverIcon, MobileSearchIcon, PullListIcon, SettingsNavIcon } from "../design-icons.jsx";
import { catalogPending } from "../library/LibraryView.jsx";
import { jobsNeedingAttention } from "../nav-counts.js";
import { can, isAdmin } from "../profiles.js";
import { canTuck, nextTabBarState } from "../tab-bar.js";
import { useViewer } from "../viewer.js";
import { HeaderContext, HeaderSearchField } from "./header.jsx";
import { ArrowsClockwise, SignOut } from "@phosphor-icons/react";
import { useManages } from "../host.js";

export const NAV_ITEMS = [
  // Above 640px only, as the Apple TV app's sidebar leads with Search: its
  // own page, over your library and the catalogs. A phone searches from
  // Discover and keeps four tabs.
  { id: "search", label: "Search", icon: MobileSearchIcon, desktopOnly: true },
  { id: "library", label: "Comics", icon: ComicsIcon },
  { id: "discover", label: "Discover", icon: DiscoverIcon },
  { id: "requests", label: "Pull List", icon: PullListIcon },
  // Library health lives under Settings now, so its count travels with it --
  // the rail still says when something needs a decision, one level up.
  { id: "settings", label: "Settings", icon: SettingsNavIcon },
];

// What a rail badge counts, said in words for the tooltip and screen readers.
export const NAV_COUNT_LABELS = {
  // Stopped downloads and readers' requests: both wait on the admin.
  requests: (n) => `${n} waiting for you on the Pull List`,
  settings: (n) => `${n} item${n === 1 ? "" : "s"} in Library health need${n === 1 ? "s" : ""} a decision`,
};

// A number that counts to its new value rather than jumping, for the counts
// a scan or a pull changes under the reader. It starts at the first number it
// is given (a library's size should not run up from 0 on every page load) and
// animates every change after that.
export function useCountUp(value) {
  const target = Number.isFinite(Number(value)) ? Number(value) : 0;
  const [shown, setShown] = useState(target);
  const fromRef = useRef(target);
  useEffect(() => {
    const from = fromRef.current;
    if (from === target) return undefined;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      fromRef.current = target;
      setShown(target);
      return undefined;
    }
    const started = performance.now();
    const duration = countUpDuration(from, target);
    let frame = requestAnimationFrame(function step(now) {
      const elapsed = now - started;
      setShown(countUpValue(from, target, elapsed, duration));
      if (elapsed < duration) frame = requestAnimationFrame(step);
      else fromRef.current = target;
    });
    return () => cancelAnimationFrame(frame);
  }, [target]);
  return shown;
}

export function CountUp({ value }) {
  return <>{useCountUp(value).toLocaleString()}</>;
}

// The phone's tab bar gets out of the way while you read, as iOS apps do:
// scrolling down tucks it into the current tab, and scrolling back up,
// reaching the top of the page, changing screen, or tapping it brings it back.
// The styling only applies at phone widths, so a desktop never sees the state.
// The decision, including the iOS rubber-band case, is nextTabBarState.
export function useCollapsingTabBar(resetKey, tuckable = true) {
  const [collapsed, setCollapsed] = useState(false);
  const tuckableRef = useRef(tuckable);
  tuckableRef.current = tuckable;
  // The listener's own record of where the page was and what it decided. A
  // tap or a screen change opens the bar outside the listener, so they reset
  // this too -- otherwise the next scroll frame would tuck the bar straight
  // back from a stale "collapsed".
  const stateRef = useRef({ collapsed: false, lastY: 0 });
  useEffect(() => {
    stateRef.current = { collapsed: false, lastY: window.scrollY };
    let frame = 0;
    function onScroll() {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (!tuckableRef.current) {
          stateRef.current = { collapsed: false, lastY: window.scrollY };
          setCollapsed(false);
          return;
        }
        stateRef.current = nextTabBarState({
          scrollY: window.scrollY,
          maxScroll: document.documentElement.scrollHeight - window.innerHeight,
          lastY: stateRef.current.lastY,
          collapsed: stateRef.current.collapsed,
        });
        setCollapsed(stateRef.current.collapsed);
      });
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(frame);
    };
  }, []);
  function open() {
    stateRef.current = { collapsed: false, lastY: window.scrollY };
    setCollapsed(false);
  }
  useEffect(open, [resetKey]);
  return [collapsed, open];
}

// Sized in place rather than in styles.css: an inline bar the width of a short
// number, on the library skeleton's shimmer.
export const SIDEBAR_COUNT_SKELETON = { display: "inline-block", width: 24, verticalAlign: "middle" };

/**
 * Search, at the top of the sidebar above 900px, as Messages and Figma have it:
 * one field for everything, on every page. Typing shows the Search page with
 * your library's matches as you go; Enter asks the catalogs too.
 */
export function SidebarSearch() {
  const header = useContext(HeaderContext);
  const search = header?.search;
  if (!search) return null;
  return <HeaderSearchField className="sidebar-search" field={{
    id: "sidebar-search-input",
    value: search.draft, onChange: search.onDraft, onSubmit: search.onSubmit, onClear: search.onClear,
    onFocus: () => { if (!search.draft) search.onOpen(); }, onEscape: true,
    label: search.catalogs ? "Search your library and the comic catalogs" : "Search your library",
    placeholder: "Search",
  }} />;
}

// `items`, when given, are the host's own destinations (Flipparr View has
// Comics and Downloads); left out, they are the web app's, by permission.
export function Nav({ active, onNavigate, catalog, backendStatus, logicalSeriesCount, authStatus, onSignOut, scanning, onScanLibrary, items: hostItems = null }) {
  const viewer = useViewer();
  const admin = useManages();
  // A reader's Pull List is what they have asked for.
  const items = hostItems || NAV_ITEMS.filter(({ id }) => can(viewer, `nav.${id}`))
    .map((item) => (item.id === "requests" && !isAdmin(viewer) ? { ...item, label: "Requests" } : item));
  const [collapsed, setCollapsed] = useCollapsingTabBar(active, canTuck(active, items));
  // While the bar folds or opens, the glass pill sits out: its blur was
  // being re-placed and re-animated on every frame of the width animation,
  // and a phone could not keep that smooth. It pops in, in place, once the
  // bar has finished moving -- The owner's suggestion.
  const [morphing, setMorphing] = useState(false);
  const morphingRef = useRef(false);
  const firstMorph = useRef(true);
  useEffect(() => {
    if (firstMorph.current) { firstMorph.current = false; return undefined; }
    morphingRef.current = true;
    setMorphing(true);
    const wait = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--motion-duration-morph")) || 440;
    const timer = window.setTimeout(() => { morphingRef.current = false; setMorphing(false); }, wait + 30);
    return () => window.clearTimeout(timer);
  }, [collapsed]);
  const [navRef, navGlass] = useGlassIndicator(".nav-item.active", [active, collapsed, morphing], morphingRef);
  // A tap on the tucked bar opens it rather than going anywhere, so the
  // reader can see the other tabs before choosing one.
  function expandInsteadOfNavigating(event) {
    if (!collapsed || !window.matchMedia("(max-width: 640px)").matches) return;
    event.preventDefault();
    event.stopPropagation();
    setCollapsed(false);
  }
  // Library health is the admin's; a reader's Settings has nothing waiting.
  const counts = {
    requests: admin ? jobsNeedingAttention(catalog) + (catalog?.stats?.pendingRequests ?? 0) : 0,
    settings: admin ? catalog?.stats?.needAttention ?? 0 : 0,
  };
  return (
    <aside className={`sidebar${collapsed ? " tab-bar-collapsed" : ""}`}>
      <SidebarSearch />
      <nav aria-label="Primary navigation" ref={navRef} onClickCapture={expandInsteadOfNavigating}
        onFocusCapture={(event) => { if (event.target.matches?.(":focus-visible")) setCollapsed(false); }}>
        {/* Drawn only in the phone's tab bar; the desktop rail marks its item itself. */}
        <span className={`nav-glass glass-indicator${morphing ? " nav-glass--waiting" : ""}`} aria-hidden="true" style={navGlass || { opacity: 0 }} />
        {items.map(({ id, label, icon: Icon, count, desktopOnly }) => (
          <button className={`nav-item ${active === id ? "active" : ""}${desktopOnly ? " nav-item--desktop" : ""}`} data-nav={id} key={id} onClick={() => onNavigate(id)} aria-label={(counts[id] ?? count) ? `${label}. ${NAV_COUNT_LABELS[id]?.(counts[id] ?? count) ?? `${counts[id] ?? count}`}` : label} aria-current={active === id ? "page" : undefined}>
            <Icon size={null} /><span>{label}</span>{(counts[id] ?? count) ? <b className={(counts[id] ?? count) > 9 ? "wide" : ""} title={NAV_COUNT_LABELS[id]?.(counts[id] ?? count)}>{counts[id] ?? count}</b> : null}
          </button>
        ))}
      </nav>
      {/* Frame 8:284: the library's size sits at the sidebar's foot, beside
          the F mark -- the app's one piece of branding. */}
      <div className="sidebar-footer">
        <span className="sidebar-identity">
          <FlipparrMark size={21} decorative className="sidebar-mark" />
          {/* Until the catalog answers there is no count to show, and a 0 reads
              as an empty library -- so the numbers shimmer and the labels stay,
              which keeps the row from shifting when they arrive. */}
          {catalogPending(catalog, backendStatus)
            ? <span className="sidebar-counts" role="status" aria-busy="true" aria-label="Loading library size">
              <span aria-hidden="true"><span className="library-loading-cell" style={SIDEBAR_COUNT_SKELETON} /> Files</span>
              <i aria-hidden="true">|</i>
              <span aria-hidden="true"><span className="library-loading-cell" style={SIDEBAR_COUNT_SKELETON} /> Series</span>
            </span>
            : <span className="sidebar-counts">
              <span><strong><CountUp value={catalog?.stats?.files ?? 0} /></strong> Files</span>
              <i aria-hidden="true">|</i>
              <span><strong><CountUp value={logicalSeriesCount ?? 0} /></strong> Series</span>
            </span>}
          {/* A quick scan, for comics added outside Flipparr -- they stay
              invisible until one runs, and the only other way in was two
              clicks deep under Import library. It turns while any scan runs:
              started here, from the folders page, or on the server. */}
          {admin ? <button type="button" className={`sidebar-scan${scanning ? " scanning" : ""}`}
            onClick={onScanLibrary} disabled={scanning} aria-busy={scanning}
            aria-label={scanning ? "Scanning your library" : "Scan library"}
            title={scanning ? "Scanning your library…"
              : catalog?.lastScan?.iso ? `Scan library · last scanned ${catalog.lastScan.date}, ${catalog.lastScan.time}`
              : "Scan library"}>
            <ArrowsClockwise size={16} />
          </button> : null}
        </span>
      </div>
      <div className="sidebar-session">
        {/* Who is reading is the header's avatar, which opens the profile;
            the sidebar no longer repeats it (owner, 2026-09-29). */}
        {authStatus?.method === "forms" && authStatus?.authenticated && !authStatus?.household
          ? <button type="button" className="sign-out-button" onClick={() => onSignOut()}><SignOut size={16} /> Sign out</button>
          : null}
      </div>
    </aside>
  );
}
