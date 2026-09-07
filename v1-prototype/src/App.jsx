import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { FlipparrMark, FlipparrWordmark } from "./brand.jsx";
import {
  ArrowsClockwise,
  ArrowUpRight,
  ArrowLeft,
  ArrowRight,
  Bell,
  BookmarkSimple,
  BookOpen,
  Books,
  CaretDown,
  Check,
  CheckCircle,
  ClockCounterClockwise,
  CloudArrowDown,
  Database,
  DotsThree,
  Eye,
  FolderOpen,
  Funnel,
  Gear,
  HardDrive,
  List,
  ListBullets,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  ShieldCheck,
  SignOut,
  SquaresFour,
  UploadSimple,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { LoadingIndicator } from "./components/LoadingIndicator";
import { Button } from "./components/Button";
import { StatusBadge } from "./components/StatusBadge";

const NAV_ITEMS = [
  { id: "library", label: "Your library", icon: BookOpen },
  { id: "discover", label: "Discover", icon: MagnifyingGlass },
  { id: "requests", label: "Requests", icon: BookmarkSimple },
  { id: "metadata", label: "Library health", icon: Database, count: 3 },
  { id: "settings", label: "Settings", icon: Gear },
];

const DEMO_SERIES = [
  {
    id: "absolute-batman", title: "Absolute Batman", year: "2024", publisher: "DC Comics",
    run: "2024 – Ongoing", tags: ["Superhero", "Detective"], owned: 3, total: 3,
    ownership: "Complete to date", format: "Single issues", updated: "Aug 26, 2026", time: "10:14 AM",
    cover: "/covers/absolute-batman.jpg", status: "complete",
  },
  {
    id: "birthright", title: "Birthright", year: "2014", publisher: "Image Comics",
    run: "2014 – 2017", tags: ["Fantasy", "Adventure"], owned: 2, total: 50,
    ownership: "48 issues missing", format: "Single issues", updated: "Aug 26, 2026", time: "10:02 AM",
    cover: "/covers/birthright.jpg", status: "partial",
  },
  {
    id: "locke-key", title: "Locke & Key", year: "2008", publisher: "IDW Publishing",
    run: "Head Games · Volume 2", tags: ["Horror", "Volume"], owned: 1, total: 1,
    ownership: "Volume owned", format: "EPUB volume", updated: "Aug 26, 2026", time: "9:58 AM",
    cover: "/covers/locke-key.jpg", status: "complete",
  },
  {
    id: "southern-bastards", title: "Southern Bastards", year: "2014", publisher: "Image Comics",
    run: "Gridiron · Volume 2", tags: ["Crime", "Volume"], owned: 1, total: 1,
    ownership: "Needs a metadata fix", format: "CBZ volume", updated: "Aug 26, 2026", time: "9:41 AM",
    cover: "/covers/southern-bastards.jpg", status: "warning",
  },
];

const DEMO_META_ITEMS = [
  { file: "strangetalentoflutherstrode_vol2.cbz", issue: "Empty archive", detail: "The archive is only 22 bytes and contains no comic pages.", severity: "error", category: "file", code: "empty_archive" },
  { file: "southernbastards_vol1.cbz", issue: "No image pages", detail: "Metadata exists, but the archive has no readable cover or pages.", severity: "error", category: "file", code: "no_image_pages" },
  { file: "southernbastards_vol2.cbz", issue: "Conflicting volume metadata", detail: "The filename and external catalog disagree with ComicInfo.xml.", severity: "warning", category: "metadata", code: "metadata_conflict" },
];

const EDITION_KIND_LABELS = {
  omnibus: "Omnibus",
  compendium: "Compendium",
  deluxe_edition: "Deluxe volume",
  graphic_novel: "Graphic novel",
  hardcover: "Hardcover",
  collected_volume: "Collected volume",
  collection: "Collected volume",
  edition: "Volume",
};

const ACQUISITION_LABELS = {
  volumes: "Volumes preferred",
  issues: "Single issues preferred",
  either: "Issues or volumes",
};

const JOB_STATUS_LABELS = {
  queued: "Queued",
  waiting: "Waiting",
  searching: "Searching",
  grabbed: "Download sent",
  failed: "Search failed",
  fulfilled: "Covered",
  cancelled: "Cancelled",
};

const DOWNLOAD_STATUS_LABELS = {
  queued: "Waiting for SABnzbd",
  downloading: "Downloading",
  completed: "Download complete",
  importing: "Adding to library",
  waiting_for_files: "Waiting for completed file",
  imported: "Added to library",
  failed: "Import needs attention",
};

const METADATA_PROVIDER_LABELS = {
  gcd: "Grand Comics Database",
  metron: "Metron",
  comic_vine: "Comic Vine",
};

function editionKindLabel(kind) {
  return EDITION_KIND_LABELS[kind] || "Volume";
}

// Collected editions are always catalogued; this flag only gates the surfaces
// for browsing and managing them (the Volumes tab, its stats, and editing an
// edition's issue contents). Wording is deliberately unchanged: a trade or
// hardcover is still a volume, and the files stay visible and fixable either
// way — the user just isn't offered edition management.
const CollectedEditionsContext = createContext(false);

function useCollectedEditions() {
  return useContext(CollectedEditionsContext);
}

const DIALOG_FOCUSABLE =
  'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

// Dialogs stack: Fix match opens on top of the series drawer. Escape and the
// focus trap must apply only to the topmost one. Listener order alone can't do
// this — every dialog listens on document, and capture order favours the one
// that mounted first, which is the one underneath.
const openDialogs = [];

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
function exitDurationMs() {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue("--motion-duration-standard").trim();
  const value = parseFloat(raw);
  if (!Number.isFinite(value)) return 220;
  return raw.endsWith("ms") ? value : value * 1000;
}

function useDrawerExit(onClose) {
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
function useExternalDismiss(signal, requestClose) {
  const closeRef = useRef(requestClose);
  closeRef.current = requestClose;
  const seen = useRef(signal);
  useEffect(() => {
    if (signal === seen.current) return;
    seen.current = signal;
    closeRef.current();
  }, [signal]);
}

function useSwipeToDismiss(ref, onClose) {
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

function useDialog(onClose) {
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
    openDialogs.push(node);
    function handleKeyDown(event) {
      // Only the topmost dialog reacts, so Escape closes one layer at a time.
      if (openDialogs[openDialogs.length - 1] !== node) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current?.();
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
      const index = openDialogs.indexOf(node);
      if (index !== -1) openDialogs.splice(index, 1);
      // Return focus to whatever opened the dialog, not the top of the page.
      if (previouslyFocused instanceof HTMLElement && document.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
    };
  }, []);
  return ref;
}

function publicationState(series) {
  const state = series?.publicationStatus
    || (series?.issueCatalog?.status === "complete" ? "completed"
      : series?.issueCatalog?.status === "complete_to_date" ? "ongoing" : "unknown");
  return state === "completed"
    ? { label: "Completed", tone: "completed", known: true }
    : state === "ongoing"
      ? { label: "Ongoing", tone: "ongoing", known: true }
      // "Unknown" read as a fault on a freshly scanned library, where almost
      // every row is waiting on provider metadata that will arrive.
      : { label: "Status pending", tone: "pending", known: false };
}

function PublicationStatus({ series }) {
  const state = publicationState(series);
  // A badge on 94% of rows is not a status, it is background texture -- and it
  // buried the handful of rows that do carry one. Say nothing until there is
  // something to say, so a real Ongoing or Completed stands out.
  if (!state.known) return null;
  return <span className={`publication-status ${state.tone}`}>{state.label}</span>;
}

function MonitoringStatus({ series }) {
  if (series.monitoringStatus !== "monitored") return null;
  return <span className="monitoring-status"><CheckCircle size={13} weight="fill" /> Following</span>;
}

function seriesAttentionLabel(series) {
  const unhealthyFiles = (series?.fileDetails || []).filter((file) => file.health?.status === "error").length;
  if (unhealthyFiles) return `${unhealthyFiles} comic file${unhealthyFiles === 1 ? "" : "s"} ${unhealthyFiles === 1 ? "needs" : "need"} attention`;
  return "Match needs attention";
}

function seriesAttentionDetail(series) {
  const hasFileError = (series?.fileDetails || []).some((file) => file.health?.status === "error");
  return hasFileError ? "Review the comic files for page or archive errors." : "Review the series match before using its ownership totals.";
}

function volumeTerminology(value) {
  if (!value) return value;
  return String(value)
    .replace(/collected[- ]editions/gi, "volumes")
    .replace(/collected[- ]edition/gi, "volume")
    .replace(/collection (coverage|contents|identity|source)/gi, "volume $1")
    .replace(/file edition/gi, "file volume");
}

function parseIssueInput(value) {
  const issues = [];
  for (const token of value.split(",").map((item) => item.trim()).filter(Boolean)) {
    const range = token.match(/^(\d+)\s*[-–]\s*(\d+)$/);
    if (!range) { issues.push(token); continue; }
    const start = Number(range[1]);
    const end = Number(range[2]);
    if (end < start || end - start > 499) throw new Error(`Invalid issue range: ${token}`);
    for (let issue = start; issue <= end; issue += 1) issues.push(String(issue));
  }
  return [...new Set(issues)];
}

function identityKey(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "");
}

function familyRepresentsSeries(family) {
  const runs = family?.runs || [];
  const arcs = family?.storyArcs || [];
  if (family?.structureStatus !== "confirmed" || !runs.length || !arcs.length) return false;
  const groupedRunIds = new Set(arcs.flatMap((arc) => arc.runIds || []).map(String));
  if (!runs.every((run) => groupedRunIds.has(String(run.id)))) return false;
  const root = identityKey(family.name);
  if (!root) return false;
  const alignedRuns = runs.filter((run) => identityKey(run.title).includes(root));
  return alignedRuns.length / runs.length >= 0.75;
}

function collectionSeriesEntry(family) {
  const runs = family.runs || [];
  const runLifecycle = runs.map((run) => publicationState(run).tone);
  const directIssueFiles = runs.reduce((count, run) => count + (run.inventory?.directIssueFiles || 0), 0);
  const editionCount = runs.reduce((count, run) => count + (run.inventory?.editionCount || 0), 0);
  const fileDetails = runs.flatMap((run) => run.fileDetails || []);
  const issues = runs.flatMap((run) => run.issues || []);
  const ownedIssueCount = issues.filter((issue) => issue.ownership !== "unowned").length;
  const missingIssueCount = Math.max(0, issues.length - ownedIssueCount);
  const latestRun = [...runs].sort((a, b) => `${b.updated || ""} ${b.time || ""}`.localeCompare(`${a.updated || ""} ${a.time || ""}`))[0] || {};
  const storyArcCount = family.mainArcCount || 0;
  const specialCount = family.specialGroupCount || 0;
  return {
    ...family,
    id: `collection-series-${family.id}`,
    title: family.name,
    run: `${storyArcCount} run${storyArcCount === 1 ? "" : "s"}${specialCount ? ` · ${specialCount} special${specialCount === 1 ? "" : "s"}` : ""}`,
    tags: ["Series"],
    format: "Series",
    updated: latestRun.updated || "",
    time: latestRun.time || "",
    inventory: { directIssueFiles, editionCount },
    fileDetails,
    issues,
    owned: ownedIssueCount,
    total: issues.length,
    unowned: missingIssueCount,
    catalogKnown: issues.length > 0,
    publicationStatus: runLifecycle.includes("ongoing")
      ? "ongoing"
      : runLifecycle.length && runLifecycle.every((state) => state === "completed")
        ? "completed" : "unknown",
    status: issues.length ? (missingIssueCount ? "partial" : "complete") : family.status,
    ownership: issues.length ? (missingIssueCount ? `${missingIssueCount} issues missing` : "All issues owned") : family.ownership,
    files: fileDetails.map((file) => file.path),
    isCollectionSeries: true,
    collection: family,
    searchText: `${family.name} ${family.publisher} ${runs.map((run) => run.title).join(" ")} ${(family.storyArcs || []).map((arc) => arc.name).join(" ")}`,
  };
}

function logicalCatalogSeries(catalog, fallback = []) {
  const runs = catalog?.series ?? fallback;
  const logicalFamilies = (catalog?.families || []).filter(familyRepresentsSeries);
  const hiddenRunIds = new Set(logicalFamilies.flatMap((family) => family.runIds || []).map(String));
  return [
    ...runs.filter((run) => !hiddenRunIds.has(String(run.id))),
    ...logicalFamilies.map(collectionSeriesEntry),
  ].sort((a, b) => a.title.localeCompare(b.title));
}

async function apiRequest(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error || `Request failed (${response.status})`);
    // Carry the status alongside the message. Callers need to tell "you are
    // signed out" apart from "the backend is down", and the message alone
    // cannot do that -- the server sends readable prose, not a status code.
    error.status = response.status;
    throw error;
  }
  return payload;
}

function Nav({ active, onNavigate, catalog, backendStatus, logicalSeriesCount, authStatus, onSignOut }) {
  const counts = { requests: catalog?.stats?.openRequests ?? 0, metadata: catalog?.stats?.needAttention ?? 0 };
  return (
    <aside className="sidebar">
      <nav aria-label="Primary navigation">
        {NAV_ITEMS.map(({ id, label, icon: Icon, count }) => (
          <button className={`nav-item ${active === id ? "active" : ""}`} data-nav={id} key={id} onClick={() => onNavigate(id)} aria-label={label} aria-current={active === id ? "page" : undefined}>
            <Icon size={21} weight={active === id ? "fill" : "regular"} /><span>{label}</span>{(counts[id] ?? count) ? <b>{counts[id] ?? count}</b> : null}
          </button>
        ))}
      </nav>
      <div className="sidebar-footer">
        <div className={`health-line ${backendStatus === "offline" ? "offline" : ""}`}><i /> {backendStatus === "offline" ? "Library unavailable" : "Library ready"}</div><span>Last scan: {catalog?.lastScan?.iso ? `${catalog.lastScan.date} ${catalog.lastScan.time}` : "Not run yet"}</span>
        <div className="storage-title">Library</div><strong>{catalog?.stats?.files ?? 0} comics across {logicalSeriesCount} series</strong><div className="storage-track"><i style={{ width: catalog?.stats?.files ? "100%" : "0%" }} /></div>
        {authStatus?.method === "forms" && authStatus?.authenticated
          ? <button type="button" className="sign-out-button" onClick={onSignOut}><SignOut size={16} /> Sign out</button>
          : null}
      </div>
    </aside>
  );
}

// The bar that sits above everything on desktop, unchanged from screen to
// screen: the mark, one search, and the way into settings. A phone keeps the
// bottom bar and the search inside the page -- 375px cannot hold this row, and
// the layout it belongs to is not the one a phone uses.
// The app's first popover. It borrows useDialog for Escape and the focus
// trap, and adds the outside-click that a menu needs and a modal gets from
// its backdrop. It must be mounted and unmounted rather than hidden: the
// hook's effect runs once, on mount.
function NotificationsMenu({ items, onClose, onReview }) {
  const dialogRef = useDialog(onClose);
  useEffect(() => {
    function handlePointerDown(event) {
      const node = dialogRef.current;
      // The button that opened the menu toggles it itself; closing here too
      // would reopen it on the same click.
      if (!node || node.contains(event.target) || event.target.closest?.(".appbar-notifications")) return;
      onClose();
    }
    document.addEventListener("pointerdown", handlePointerDown, true);
    return () => document.removeEventListener("pointerdown", handlePointerDown, true);
  }, [onClose]);
  const shown = items.slice(0, 5);
  return <div className="notifications-menu" ref={dialogRef} role="dialog" aria-modal="false" aria-labelledby="notifications-title">
    <header><strong id="notifications-title">Needs attention</strong>{items.length ? <b>{items.length}</b> : null}</header>
    {shown.length ? <div className="notifications-list">{shown.map((item) =>
      <button type="button" key={item.id} onClick={() => { onClose(); onReview(item); }}>
        <WarningCircle size={17} weight={item.severity === "error" ? "fill" : "regular"} className={item.severity === "error" ? "severity-error" : "severity-warning"} />
        <span><strong>{item.issue}</strong><small>{item.file}</small></span>
      </button>)}
    </div> : <p className="notifications-empty"><CheckCircle size={19} weight="fill" /> Nothing needs attention.</p>}
    {items.length ? <button type="button" className="notifications-all" onClick={() => { onClose(); onReview(); }}>
      {items.length > shown.length ? `Review all ${items.length}` : "Review all"} <ArrowRight size={15} />
    </button> : null}
  </div>;
}

function AppBar({ query, collapsed, settingsActive, inbox, onToggleNav, onSearch, onNavigate }) {
  const [draft, setDraft] = useState(query || "");
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const items = inbox || [];
  // Reloading /search?q=… must not leave the field empty under its own results.
  useEffect(() => { setDraft(query || ""); }, [query]);
  return <header className="appbar">
    <button
      type="button" className="appbar-menu" onClick={onToggleNav}
      aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
      aria-expanded={!collapsed}
    ><List size={22} /></button>
    <button
      type="button" className="appbar-brand" onClick={() => onNavigate("library")}
      aria-label="Flipparr — go to your library"
    ><FlipparrMark size={30} decorative /></button>
    <div className="appbar-search">
      <SearchBar
        embedded value={draft} onChange={setDraft} onSubmit={() => onSearch(draft)}
        label="Search your library"
        placeholder="Search your library or add a series…"
      />
    </div>
    <div className="appbar-actions">
      <div className="appbar-notifications">
        <button
          type="button" className={`appbar-action ${notificationsOpen ? "active" : ""}`}
          onClick={() => setNotificationsOpen((open) => !open)}
          aria-label={items.length ? `Needs attention: ${items.length}` : "Needs attention"}
          aria-expanded={notificationsOpen}
        >
          <Bell size={21} weight={notificationsOpen ? "fill" : "regular"} />
          {items.length ? <b className="appbar-badge">{items.length > 99 ? "99+" : items.length}</b> : null}
        </button>
        {notificationsOpen ? <NotificationsMenu
          items={items}
          onClose={() => setNotificationsOpen(false)}
          onReview={() => onNavigate("metadata")}
        /> : null}
      </div>
      <button
        type="button" className={`appbar-action ${settingsActive ? "active" : ""}`}
        onClick={() => onNavigate("settings")}
        aria-label="Settings" aria-current={settingsActive ? "page" : undefined}
      ><Gear size={21} weight={settingsActive ? "fill" : "regular"} /></button>
    </div>
  </header>;
}

function SearchBar({ value, onChange, onSubmit, actionLabel = "Search online", busy = false, placeholder = "Search series, issue, or creator…", label = "Search", embedded = false }) {
  // Submit sits outside the field and carries the magnifier, so the field no
  // longer repeats it. actionLabel becomes the button's accessible name, since
  // an icon-only control has no visible text of its own.
  //
  // `embedded` is the app bar's shape instead: the magnifier moves inside the
  // field as a label rather than a control, and Enter is the only way to
  // submit. A bar that is always on screen should not carry a button that is
  // only occasionally the thing you want.
  const field = <div className="search-field">{embedded ? <MagnifyingGlass size={17} weight="bold" aria-hidden="true" /> : null}<input aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} onKeyDown={(event) => {
    if (event.key === "Enter" && onSubmit) {
      event.preventDefault();
      onSubmit();
    }
  }} placeholder={placeholder} /></div>;
  if (!onSubmit || embedded) return field;
  return <div className="search-row">{field}<button type="button" className="search-submit" aria-label={busy ? "Searching…" : actionLabel} title={actionLabel} disabled={busy || value.trim().length < 2} aria-busy={busy} onClick={onSubmit}>{busy ? <LoadingSpinner size={18} /> : <MagnifyingGlass size={19} />}</button></div>;
}

const LoadingSpinner = LoadingIndicator;

function PageHeader({ eyebrow, title, description, children }) {
  return <header className="page-header"><div>{eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}<h1>{title}</h1><p>{description}</p></div>{children ? <div className="page-actions">{children}</div> : null}</header>;
}

function StatStrip({ stats }) {
  // An issue that is not out yet is not missing from the library. It is
  // counted separately and named for what it is, rather than adding to a
  // number that reads as a gap someone has to close.
  const upcoming = stats?.unpublishedIssues ?? 0;
  const cards = [
    { icon: Books, value: stats?.series ?? 0, label: "Series", tone: "green" },
    { icon: BookOpen, value: stats?.files ?? 0, label: "Comic files", tone: "green" },
    {
      icon: ClockCounterClockwise, value: stats?.unownedIssues ?? 0,
      label: "Issues missing", tone: "muted",
      note: upcoming ? `${upcoming} not published yet` : "",
    },
    { icon: WarningCircle, value: stats?.damaged ?? 0, label: "Comic file problems", tone: "danger" },
  ];
  return <section className="stat-strip" aria-label="Library summary">{cards.map(({ icon: Icon, value, label, tone, note }) => <div className={`stat ${tone}`} key={label}><Icon size={27} weight="duotone" /><div><strong>{value}</strong><span>{label}</span>{note ? <small className="stat-note">{note}</small> : null}</div></div>)}</section>;
}

function MetadataSetupStatus({ enrichment, lastScanAt, onNavigate }) {
  const active = (enrichment?.active ?? 0) > 0;
  const completionSignature = enrichment?.total && !active
    ? [lastScanAt || "initial", enrichment.total, enrichment.complete, enrichment.review, enrichment.failed].join(":")
    : "";
  const [dismissedSignature, setDismissedSignature] = useState(() => {
    if (typeof window === "undefined") return "";
    return window.localStorage.getItem("sonicboom.metadata-check-dismissed") || "";
  });

  useEffect(() => {
    if (!active) return;
    window.localStorage.removeItem("sonicboom.metadata-check-dismissed");
    setDismissedSignature("");
  }, [active]);

  useEffect(() => {
    if (!completionSignature || enrichment?.review || enrichment?.failed || dismissedSignature === completionSignature) return undefined;
    const timer = window.setTimeout(() => {
      window.localStorage.setItem("sonicboom.metadata-check-dismissed", completionSignature);
      setDismissedSignature(completionSignature);
    }, 8000);
    return () => window.clearTimeout(timer);
  }, [completionSignature, dismissedSignature, enrichment?.failed, enrichment?.review]);

  if (!enrichment?.total || (!active && dismissedSignature === completionSignature)) return null;
  const processed = enrichment.complete + enrichment.review + enrichment.failed;
  const percent = active
    ? Math.min(99, Math.round((processed / enrichment.total) * 100))
    : 100;
  const remaining = Math.max(0, enrichment.total - processed);
  const current = enrichment.nextJobs?.find((job) => job.status === "running");
  const next = enrichment.nextJobs?.find((job) => ["queued", "waiting"].includes(job.status));
  const cooldowns = enrichment.providerCooldowns || [];
  const cooldown = cooldowns[0];
  const paused = active && !current && enrichment.waiting > 0 && enrichment.allProvidersCooling === true;
  const degraded = active && !paused && cooldowns.length > 0;
  const providerName = METADATA_PROVIDER_LABELS[cooldown?.provider] || cooldown?.provider || "The metadata service";
  const retryTime = cooldown?.nextRetryAt
    ? new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(cooldown.nextRetryAt))
    : null;
  const availableProviderNames = (enrichment.availableProviders || []).map((provider) => METADATA_PROVIDER_LABELS[provider] || provider);
  const workingLabel = current ? `Checking ${current.title}` : degraded ? `Continuing with ${availableProviderNames.join(" and ") || "other available sources"}` : enrichment.waiting && !enrichment.queued ? "Retrying unresolved series automatically" : next ? `${next.title} is next` : "Preparing the next series";
  const heading = paused
    ? "Adding comic details in the background"
    : active && remaining === 1
      ? "Finishing your comic details"
      : active
        ? "Building your comic details in the background"
        : enrichment.review || enrichment.failed
          ? "Initial metadata check finished"
          : "Comic details are ready";
  const detail = paused
    ? `${processed} of ${enrichment.total} series · ${providerName} paused${retryTime ? ` until ${retryTime}` : ""}`
    : active
    ? `${processed} of ${enrichment.total} series checked · ${workingLabel}`
    : enrichment.review || enrichment.failed
      ? [
          `${enrichment.complete} series identified automatically`,
          enrichment.review ? `${enrichment.review} need a match review` : null,
          enrichment.failed ? `${enrichment.failed} need another metadata source or manual match` : null,
        ].filter(Boolean).join(" · ")
      : `${enrichment.complete} series identified automatically`;
  const providerNotices = cooldowns.map((item) => {
    const name = METADATA_PROVIDER_LABELS[item.provider] || item.provider;
    const authenticationError = /(?:401|unauthori[sz]ed|authentication|credentials?)/i.test(item.error || "");
    const time = item.nextRetryAt ? new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(item.nextRetryAt)) : null;
    return authenticationError
      ? `${name} rejected its saved credentials. Reconnect it under Settings → Metadata services.`
      : `${name} is temporarily paused${time ? ` until ${time}` : ""}.`;
  });
  const dismiss = () => {
    window.localStorage.setItem("sonicboom.metadata-check-dismissed", completionSignature);
    setDismissedSignature(completionSignature);
  };
  return <section className={`metadata-setup-status ${paused ? "paused" : active ? "active" : enrichment.review || enrichment.failed ? "review settled" : "complete settled"}`} aria-live="polite"><span className={`metadata-setup-icon${active && !paused ? " active-loader" : ""}`}>{paused ? <ClockCounterClockwise size={21} weight="fill" /> : active ? <LoadingSpinner size={25} /> : enrichment.review || enrichment.failed ? <MagnifyingGlass size={21} /> : <CheckCircle size={21} weight="fill" />}</span><div className="metadata-setup-copy"><strong>{heading}</strong><small>{detail}</small><div className="metadata-setup-actions">{(paused || degraded) && onNavigate ? <button type="button" className="metadata-setup-action" onClick={() => onNavigate("settings", "metadata")}><Plus size={14} /> Add a source to speed this up</button> : null}<details><summary>Details</summary><div className="metadata-progress-details"><span><b>{enrichment.complete}</b> Matched</span><span><b>{enrichment.queued + enrichment.running}</b> Waiting</span><span><b>{enrichment.waiting}</b> Retrying later</span><span><b>{enrichment.review}</b> Need review</span>{enrichment.failed ? <span><b>{enrichment.failed}</b> Could not finish</span> : null}</div>{providerNotices.map((notice) => <p className="metadata-pause-detail" key={notice}>{notice}</p>)}<p className="metadata-pause-detail">Your comics, covers and folders are already available — this only fills in titles, dates and publication status.</p>{paused ? <p className="metadata-pause-detail">Flipparr retries on its own. Metron and Comic Vine are separate catalogs, so adding one lets intake carry on instead of waiting.</p> : degraded ? <p className="metadata-pause-detail">One source needs attention; Flipparr is continuing with the others.</p> : null}</details></div></div>{active ? <div className="metadata-setup-meter"><b>{percent}%</b><div className="metadata-setup-progress" aria-label={`${percent}% of initial metadata jobs processed`}><i style={{ width: `${percent}%` }} /></div></div> : <button type="button" className="metadata-setup-dismiss" onClick={dismiss} aria-label="Dismiss metadata check summary" title="Dismiss"><X size={17} /></button>}</section>;
}

function Ownership({ series, compact = false }) {
  const percent = Math.max(5, Math.round((series.owned / series.total) * 100));
  const catalogUnknown = series.catalogKnown === false || series.status === "unknown";
  const volumeCount = series.inventory?.editionCount || 0;
  const collectedOnly = volumeCount > 0 && !series.inventory?.directIssueFiles;
  const coveredIssues = series.issues?.filter((issue) => issue.ownership !== "unowned").length || 0;
  const arcCoverageKnown = collectedOnly && coveredIssues > 0;
  const ownedLabel = arcCoverageKnown ? `${coveredIssues} issue${coveredIssues === 1 ? "" : "s"} owned` : collectedOnly ? `${volumeCount} volume${volumeCount === 1 ? "" : "s"} owned` : `${series.owned} owned`;
  const label = series.status === "warning" ? seriesAttentionLabel(series) : catalogUnknown ? ownedLabel : compact ? `${series.owned} of ${series.total} owned` : `${series.owned} of ${series.total}`;
  const coverageDetail = arcCoverageKnown ? `${coveredIssues} issue${coveredIssues === 1 ? "" : "s"} collected in ${volumeCount} volume${volumeCount === 1 ? "" : "s"}${series.family ? " · complete-series progress is tracked separately" : ""}` : "";
  const detail = series.status === "warning" ? [coverageDetail, seriesAttentionDetail(series)].filter(Boolean).join(" · ") : series.isCollectionSeries ? series.ownership : coverageDetail || series.ownership;
  // An issue that is not out yet is not missing. Counting the two together
  // read as a gap to close on a run that is simply still being published.
  const summary = series.releaseSummary || {};
  const shortfall = Math.max(0, series.total - series.owned);
  const upcoming = Math.max(0, Number(summary.upcoming ?? 0));
  const missing = summary.releasedMissing == null
    ? Math.max(0, shortfall - upcoming)
    : Math.max(0, Number(summary.releasedMissing));
  const plural = (count) => (count === 1 ? "" : "s");
  const compactDetail = series.status === "warning"
    ? "Review required"
    : catalogUnknown
      ? "Series total unknown"
      : missing && upcoming
        ? `${missing} missing · ${upcoming} not published yet`
        : missing
          ? `${missing} issue${plural(missing)} missing`
          : upcoming
            ? `${upcoming} issue${plural(upcoming)} not published yet`
            : "Complete";
  // With the total unknown, the backing sentence restates the count already in
  // the label -- "23 owned" above "23 issues owned · complete-run progress
  // unknown". Use the concise form at every width, not just on mobile. Warning
  // rows keep the long form: theirs explains what needs attention.
  const showsConciseDetail = compact || (catalogUnknown && series.status !== "warning");
  const visibleDetail = showsConciseDetail ? compactDetail : detail;
  return <div className={`ownership ${series.status}${compact ? " compact" : ""}`}><div className="ownership-label">{series.status === "warning" ? <WarningCircle size={19} weight="fill" /> : catalogUnknown ? <ClockCounterClockwise size={19} weight="fill" /> : <CheckCircle size={19} weight="fill" />}<strong>{label}</strong></div>{series.status !== "warning" && !catalogUnknown ? <div className="progress"><i style={{ width: `${percent}%` }} /></div> : null}{visibleDetail && visibleDetail !== label ? <span>{visibleDetail}</span> : null}</div>;
}

function CollectionCoverage({ series, editionsOn }) {
  const directOwned = series.issues?.filter((issue) => issue.directOwned).length ?? 0;
  const inVolumes = series.issues?.filter((issue) => issue.collectionOwned).length ?? 0;
  const toVerify = editionsOn
    ? series.editions?.reduce((count, edition) => count + (edition.coverageGroups?.filter((claim) => !claim.resolved).length ?? 0), 0) ?? 0
    : 0;
  // A collected-volume series owns volumes, not loose issues, so every figure
  // here read 0 -- "0 Single issues owned / 0 Issues in volumes" sitting
  // directly beneath "3 volumes owned". Most of a collected library is these,
  // so most series showed a panel of zeros that read as "you own nothing".
  if (!directOwned && !inVolumes && !toVerify) return null;
  return <section className="coverage-overview"><span><strong>{directOwned}</strong>Single issues owned</span><span><strong>{inVolumes}</strong>Issues in volumes</span>{editionsOn ? <span><strong>{toVerify}</strong>Volume contents to verify</span> : null}</section>;
}

function CoverArt({ id, title, cover, coverCandidates, decorative = false, placeholderSize = 28 }) {
  const candidates = coverCandidates?.length ? coverCandidates : (cover ? [cover] : []);
  const [candidateIndex, setCandidateIndex] = useState(0);
  useEffect(() => setCandidateIndex(0), [id]);
  if (candidates[candidateIndex]) return <img src={candidates[candidateIndex]} alt={decorative ? "" : `${title} cover`} onError={() => setCandidateIndex((index) => index + 1)} />;
  return <span className="cover-placeholder" role="img" aria-label={decorative ? undefined : `No cover available for ${title}`}><BookOpen size={placeholderSize} weight="duotone" /></span>;
}

function SeriesCover({ series, decorative = false }) {
  return <CoverArt id={series.id} title={series.title} cover={series.cover} coverCandidates={series.coverCandidates} decorative={decorative} />;
}

function SeriesList({ series, onOpen, view }) {
  if (view === "grid") return <div className="series-grid">{series.map((item) => <button className="series-card" onClick={() => onOpen(item)} key={item.id}><SeriesCover series={item} /><div><strong>{item.title}</strong><span className="series-card-byline">{item.year} · {item.publisher}</span><span className="series-card-statuses"><PublicationStatus series={item} /><MonitoringStatus series={item} /></span><Ownership series={item} compact /></div></button>)}</div>;
  return (
    <div className="series-table">
      <div className="series-table-head"><span>Series</span><span>Ownership</span><span>Format</span><span>Last updated</span><span /></div>
      {series.map((item) => (
        <button className="series-row" onClick={() => onOpen(item)} key={item.id}>
          <div className="series-identity"><SeriesCover series={item} decorative /><div className="series-identity-copy"><strong>{item.title} <em>({item.year})</em></strong><small>{item.isCollectionSeries ? `${item.publisher} · ${item.run}` : item.publisher}</small><span className="tag-line"><PublicationStatus series={item} /><MonitoringStatus series={item} /></span><div className="mobile-list-ownership"><Ownership series={item} compact /></div></div></div>
          <Ownership series={item} /><span className="table-copy">{item.format}</span><span className="table-copy">{item.updated}<small>{item.time}</small></span><DotsThree size={22} />
        </button>
      ))}
    </div>
  );
}

function CollectionGroups({ families, onOpenCollection }) {
  return <div className="family-groups">
    {families.map((family) => {
      const display = { ...family, id: `family-${family.id}`, title: family.name };
      return <section className="family-group" key={family.id}>
        <button className="family-summary" onClick={() => onOpenCollection(family)}>
          <span className="family-cover"><SeriesCover series={display} decorative /></span>
          <span className="family-copy"><b>Collection</b><strong>{family.name}</strong><small>{family.year} · {family.publisher} · {family.runCount} publication run{family.runCount === 1 ? "" : "s"} · {family.mainArcCount || 0} story arc{family.mainArcCount === 1 ? "" : "s"}</small></span>
          <Ownership series={family} />
          <ArrowRight size={20} />
        </button>
      </section>;
    })}
  </div>;
}

function CollectionEmpty({ query }) {
  return <div className="empty-state"><Books size={35} weight="duotone" /><strong>{query ? `No collections match “${query}”` : "No collections created yet"}</strong><span>{query ? "Try another collection or run title." : "Open a run and use its Collection tab to group related publication runs."}</span></div>;
}

function AttentionPanel({ onReview, items }) {
  if (!items.length) return null;
  return <section className="attention-panel"><header><WarningCircle size={24} weight="fill" /><strong>Needs attention ({items.length})</strong><button onClick={onReview}>Review problems <ArrowRight size={16} /></button></header><div className="attention-details">{items.slice(0, 2).map((item) => <button onClick={onReview} key={item.id}><span>{item.issue}</span><small>{item.file}: {item.detail}</small><b>{item.category === "file" ? "Review comic file" : "Review metadata"}</b></button>)}</div></section>;
}

function discoveryRunStatus(item) {
  const status = String(item?.status || "").toLowerCase();
  if (item?.yearEnded || ["completed", "complete", "cancelled"].includes(status)) return "Completed run";
  if (["ongoing", "continuing"].includes(status)) return "Ongoing run";
  return "Status unknown";
}

function discoveryByline(item) {
  const rolePriority = { writer: 0, artist: 1, penciller: 1, inker: 2, colorist: 3, letterer: 4 };
  const creators = (item?.creators || []).filter((creator) => creator?.name && creator?.roles?.length).sort((a, b) => {
    const priority = (creator) => Math.min(...creator.roles.map((role) => rolePriority[role.toLowerCase()] ?? 9));
    return priority(a) - priority(b);
  });
  if (!creators.length) return "";
  const visible = creators.slice(0, 3).map((creator) => (
    `${creator.name} (${creator.roles.map((role) => role.toLowerCase()).join(", ")})`
  ));
  if (creators.length > visible.length) visible.push(`+${creators.length - visible.length} more`);
  return `By ${visible.join(" · ")}`;
}

function searchQueryParts(value) {
  const cleaned = String(value || "").trim().replace(/\s+/g, " ");
  const match = cleaned.match(/^(.+?)(?:\s+|\s*\()((?:19|20)\d{2})\)?$/);
  return match ? { title: match[1].trim(), year: match[2] } : { title: cleaned, year: "" };
}

function DiscoveryCover({ item }) {
  const [failed, setFailed] = useState(false);
  return <span className="discovery-cover">{item.cover && !failed ? <img src={item.cover} alt={`${item.title} cover`} onError={() => setFailed(true)} /> : <BookOpen size={22} weight="duotone" />}</span>;
}

function DiscoveryCard({ item, provider, busyId, onAdd }) {
  const byline = discoveryByline(item);
  const itemId = `${item.provider}-${item.providerSeriesId}`;
  return <article className="discovery-result">
    <DiscoveryCover item={item} />
    <div className="discovery-copy">
      <div className="discovery-title-row"><strong>{item.title}</strong><b>{item.yearLabel}</b></div>
      {byline ? <span className="discovery-byline" title={`${item.creatorCreditsSource || "Provider"} credits`}>{byline}</span> : null}
      <span>{item.publisher || "Publisher unknown"} · {item.issueCount} known issue{item.issueCount === 1 ? "" : "s"}</span>
      <small>{discoveryRunStatus(item)} · {item.providerName || provider}</small>
    </div>
    <Button
      busy={busyId === itemId}
      busyLabel="Adding…"
      icon={<Plus size={16} />}
      onClick={() => onAdd(item)}
    >
      Add &amp; request
    </Button>
  </article>;
}

function DiscoveryResults({ query, results, state, error, provider, yearHint, busyId, onAdd, onRetry, page = false }) {
  const available = results.filter((item) => !item.inLibrary);
  const present = results.filter((item) => item.inLibrary);
  const providerSummary = provider ? `${available.length || results.length} publication runs from ${provider}${yearHint ? ` · closest to ${yearHint} first` : ""}` : "Searching comic databases";
  const cards = available.map((item) => <DiscoveryCard item={item} provider={provider} busyId={busyId} onAdd={onAdd} key={`${item.provider}-${item.providerSeriesId}`} />);
  return <section className={`discovery-results${page ? " discovery-results-page" : ""}`} aria-label="Discover series results">
    <header><div><strong>Discover new series</strong><span>{providerSummary}</span></div>{state === "loading" ? <LoadingSpinner size={18} label="Searching metadata services" /> : null}</header>
    {error ? <div className="discovery-message warning"><WarningCircle size={18} /><span><strong>Online search needs another try</strong><small>The comics provider is busy. Your library results are still available above.</small></span><button type="button" onClick={onRetry}>Try again</button></div> : null}
    {!error && state === "done" && !available.length ? <div className="discovery-message"><MagnifyingGlass size={18} /><span><strong>{present.length ? "All matching runs are already in your library" : `No new series found for “${query}”`}</strong><small>Try the exact publication title or add a four-digit publication year.</small></span></div> : null}
    {page ? <div className="discovery-grid">{cards}</div> : cards}
  </section>;
}

// A scan in progress and a first page load look identical to someone arriving:
// nothing is on screen yet. The difference worth telling them is why, and how
// far along it is -- so the skeleton carries the scan's own count when there is
// one, rather than a generic "loading".
function scanCounts(scan) {
  const total = Number(scan?.total_files || 0);
  const processed = Number(scan?.processed_files || 0);
  return { total, processed, percent: total ? Math.min(99, Math.round((processed / total) * 100)) : 0 };
}

function LibraryScanNotice({ scan }) {
  const { total, processed, percent } = scanCounts(scan);
  return <div className={`scan-progress ${total ? "determinate" : ""}`} aria-live="polite">
    <span>
      <strong>{total ? `Reading comic ${Math.min(processed + 1, total)} of ${total}` : "Finding your comic files…"}</strong>
      <small>{total ? `${processed} read · titles, cover art and file health` : "Counting files before the scan begins"}</small>
    </span>
    <b>{total ? `${percent}%` : "Starting"}</b>
    <i style={total ? { width: `${percent}%` } : undefined} />
  </div>;
}

function LibraryLoadingSkeleton({ scan = null }) {
  const { total, processed } = scanCounts(scan);
  return <div className="library-loading" role="status" aria-live="polite" aria-busy="true">
    <section className="library-loading-message">
      <span className="library-loading-icon"><LoadingSpinner size={21} /></span>
      {scan
        ? <span><strong>Scanning your library…</strong><small>{total ? `Comic ${Math.min(processed + 1, total)} of ${total} · your runs appear as they are found.` : "Counting your comic files. Nothing is renamed or moved."}</small></span>
        : <span><strong>Loading your library…</strong><small>Bringing in your comic runs, covers, and collection status.</small></span>}
    </section>
    <section className="library-loading-stats" aria-hidden="true">
      {[0, 1, 2, 3].map((item) => <span key={item}><i /><b /></span>)}
    </section>
    <div className="library-loading-tools" aria-hidden="true"><i /><i /><i /></div>
    <section className="library-loading-table" aria-hidden="true">
      <header><i /><i /><i /><i /></header>
      {[0, 1, 2, 3, 4].map((item) => <article key={item}><span className="library-loading-cover" /><span className="library-loading-copy"><i /><i /><i /></span><span className="library-loading-progress"><i /><i /></span><span className="library-loading-cell" /><span className="library-loading-cell short" /></article>)}
    </section>
  </div>;
}

// One ordering for runs and for collections, so the control is never inert on
// whichever of the two is on screen. A collection has no addedAt or status of
// its own, so those keys fall back to the title rather than to an arbitrary
// order that would look like a broken sort.
function sortLibrary(items, sort) {
  const byTitle = (a, b) => String(a.title ?? a.name ?? "").localeCompare(String(b.title ?? b.name ?? ""), undefined, { numeric: true, sensitivity: "base" });
  const sorted = [...items];
  if (sort === "added") {
    return sorted.sort((a, b) => String(b.addedAt ?? "").localeCompare(String(a.addedAt ?? "")) || byTitle(a, b));
  }
  if (sort === "attention") {
    const rank = (item) => (item.status === "warning" ? 0 : 1);
    return sorted.sort((a, b) => rank(a) - rank(b) || (b.unowned ?? 0) - (a.unowned ?? 0) || byTitle(a, b));
  }
  return sorted.sort(byTitle);
}

function LibraryView({ onNavigate, onOpenSeries, onOpenCollection, onSearch, catalog, backendStatus }) {
  const [query, setQuery] = useState("");
  // Covers are the point of a comic library, so the grid leads.
  const [view, setView] = useState("grid");
  const [scope, setScope] = useState("runs");
  const [sort, setSort] = useState("title");
  const [followingOnly, setFollowingOnly] = useState(false);
  const fallbackSeries = backendStatus === "offline" ? DEMO_SERIES : [];
  const series = useMemo(() => logicalCatalogSeries(catalog, fallbackSeries), [catalog, backendStatus]);
  const families = useMemo(() => (catalog?.families || []).map((family) => ({ ...family, runCount: family.runs?.length || family.runCount || 0 })), [catalog?.families]);
  const initialLoading = backendStatus === "loading" && !catalog;
  // The server has always reported this; nothing read it, so arriving during a
  // scan showed the "no comics yet" empty state on a library that was filling.
  const activeScan = catalog?.activeScan || null;
  const editionsOn = Boolean(catalog?.collectedEditionsEnabled);
  const effectiveScope = editionsOn ? scope : "runs";
  const scopedSeries = editionsOn ? series : series.filter((item) => !item.isCollectionSeries);
  const filteredSeries = followingOnly ? scopedSeries.filter((item) => item.monitoringStatus === "monitored") : scopedSeries;
  const displayedSeries = useMemo(() => sortLibrary(filteredSeries, sort), [filteredSeries, sort]);
  const sortedFamilies = useMemo(() => sortLibrary(families, sort), [families, sort]);
  return (
    <>
      <div className="topbar"><div className="library-search"><SearchBar value={query} onChange={setQuery} onSubmit={() => onSearch(query)} actionLabel="Search" label="Search library and discover series" placeholder="Search your library or add a series…" /></div></div>
      <PageHeader title="Your library" description="See what you own, what’s missing, and what needs your attention." />
      {initialLoading ? <LibraryLoadingSkeleton /> : null}
      {!initialLoading && activeScan && !series.length ? <LibraryLoadingSkeleton scan={activeScan} /> : null}
      {!initialLoading && !(activeScan && !series.length) ? <>
      {activeScan ? <LibraryScanNotice scan={activeScan} /> : null}
      {backendStatus === "offline" ? <div className="backend-banner"><WarningCircle size={19} weight="fill" /> Showing sample comics because your library is unavailable.</div> : null}
      <MetadataSetupStatus enrichment={catalog?.enrichment} lastScanAt={catalog?.lastScan?.iso} onNavigate={onNavigate} />
      <StatStrip stats={{ ...(catalog?.stats ?? { files: series.reduce((count, item) => count + item.owned, 0), needAttention: 0, damaged: 0 }), series: series.length }} />
      <div className="library-tools">{editionsOn ? <div className="scope-toggle" aria-label="Choose catalog grouping"><button className={effectiveScope === "runs" ? "active" : ""} onClick={() => setScope("runs")}><ListBullets size={17} /> Runs</button><button className={effectiveScope === "collections" ? "active" : ""} onClick={() => setScope("collections")}><Books size={17} /> Collections</button></div> : null}{effectiveScope === "runs" ? <div className="view-toggle" aria-label="Choose library view"><button className={view === "grid" ? "active" : ""} onClick={() => setView("grid")} aria-label="Grid view"><SquaresFour size={18} /></button><button className={view === "list" ? "active" : ""} onClick={() => setView("list")} aria-label="List view"><ListBullets size={18} /></button></div> : null}<label className="sort-field"><span>Sort by</span><select value={sort} onChange={(event) => setSort(event.target.value)}><option value="title">Title (A–Z)</option><option value="added">Recently added</option><option value="attention">Needs attention</option></select></label>{effectiveScope === "runs" ? <button className={`filter-button ${followingOnly ? "active" : ""}`} aria-pressed={followingOnly} onClick={() => setFollowingOnly((value) => !value)}><CheckCircle size={18} weight={followingOnly ? "fill" : "regular"} /> Following</button> : null}</div>
      {effectiveScope === "collections" ? (sortedFamilies.length ? <CollectionGroups families={sortedFamilies} onOpenCollection={onOpenCollection} /> : <CollectionEmpty query="" />) : displayedSeries.length ? <SeriesList series={displayedSeries} onOpen={(item) => item.isCollectionSeries && editionsOn ? onOpenCollection(item.collection) : onOpenSeries(item)} view={view} /> : followingOnly ? <div className="empty-state"><CheckCircle size={35} weight="duotone" /><strong>No followed runs</strong><span>Open any run and choose Follow run to monitor future issues.</span><button className="ghost-button" onClick={() => setFollowingOnly(false)}>Show all runs</button></div> : <CatalogEmpty onAdd={() => onNavigate("import")} />}
      <AttentionPanel items={catalog?.inbox ?? []} onReview={() => onNavigate("metadata")} />
      </> : null}
    </>
  );
}

function SearchResultsView({ query, catalog, backendStatus, onSearch, onOpenSeries, onOpenCollection, onDiscoverRequest }) {
  const [draft, setDraft] = useState(query);
  const [discovery, setDiscovery] = useState({ state: "loading", results: [], error: "", provider: "", yearHint: null });
  const [discoverBusyId, setDiscoverBusyId] = useState(null);
  const allSeries = useMemo(() => logicalCatalogSeries(catalog, backendStatus === "offline" ? DEMO_SERIES : []), [catalog, backendStatus]);
  const localResults = useMemo(() => {
    const parts = searchQueryParts(query);
    const needle = parts.title.toLowerCase();
    return allSeries.filter((item) => {
      const textMatches = `${item.searchText || item.title} ${item.publisher || ""}`.toLowerCase().includes(needle);
      return textMatches && (!parts.year || `${item.year || ""} ${item.run || ""}`.includes(parts.year));
    });
  }, [allSeries, query]);

  async function searchProviders(searchQuery = query) {
    const cleaned = searchQuery.trim();
    if (cleaned.length < 2 || backendStatus === "offline") {
      setDiscovery({ state: "done", results: [], error: backendStatus === "offline" ? "Library service is unavailable" : "", provider: "", yearHint: null });
      return;
    }
    setDiscovery({ state: "loading", results: [], error: "", provider: "", yearHint: null });
    try {
      const result = await apiRequest(`/api/v1/discover?query=${encodeURIComponent(cleaned)}`);
      setDiscovery({ state: "done", results: result.results || [], error: "", provider: result.provider || "", yearHint: result.yearHint || null });
    } catch (error) {
      setDiscovery({ state: "done", results: [], error: error.message, provider: "", yearHint: null });
    }
  }
  useEffect(() => { setDraft(query); searchProviders(query); }, [query, backendStatus]);
  async function addDiscovered(item) {
    setDiscoverBusyId(`${item.provider}-${item.providerSeriesId}`);
    await onDiscoverRequest({ ...item, query });
    setDiscoverBusyId(null);
  }
  return <>
    <div className="search-results-topbar"><SearchBar value={draft} onChange={setDraft} onSubmit={() => onSearch(draft)} actionLabel="Search" label="Search comics" placeholder="Try a title and year, such as Wolverine 2026…" /></div>
    <PageHeader eyebrow="Discover" title={`Results for “${query}”`} description="Comics already in your library appear first, followed by new publication runs you can follow." />
    <section className="search-results-section owned-results"><header><div><span className="eyebrow">In your library</span><h2>{localResults.length ? `${localResults.length} match${localResults.length === 1 ? "" : "es"}` : "No matches"}</h2></div></header>{localResults.length ? <SeriesList series={localResults} onOpen={(item) => item.isCollectionSeries ? onOpenCollection(item.collection) : onOpenSeries(item)} view="list" /> : <div className="search-section-empty"><BookOpen size={25} /><span>No comics in your library match this search.</span></div>}</section>
    <DiscoveryResults page query={query} results={discovery.results} state={discovery.state} error={discovery.error} provider={discovery.provider} yearHint={discovery.yearHint} busyId={discoverBusyId} onAdd={addDiscovered} onRetry={() => searchProviders(query)} />
  </>;
}

function DiscoverView({ onSearch }) {
  const [query, setQuery] = useState("");
  return <>
    <PageHeader eyebrow="Discover" title="Find a comic run" description="Search by title, creator, publisher, or start year, then choose the exact publication run you want to follow." />
    <section className="focused-panel discover-panel">
      <div className="panel-icon"><MagnifyingGlass size={30} weight="duotone" /></div>
      <h2>Search comic catalogs</h2>
      <p>Your library matches appear first. New runs are separated by publisher, publication year, creators, and run status so similarly named comics are easier to tell apart.</p>
      <SearchBar value={query} onChange={setQuery} onSubmit={() => onSearch(query)} actionLabel="Search" label="Discover comic runs" placeholder="Try a title and year, such as Wolverine 2026…" />
    </section>
  </>;
}

function EmptySearch({ query }) {
  return <div className="empty-state"><MagnifyingGlass size={32} /><strong>No series match “{query}”</strong><span>Try a title, creator, or publisher.</span></div>;
}

function CatalogEmpty({ onAdd }) {
  return <div className="empty-state"><Books size={35} weight="duotone" /><strong>Your library is ready for its first comics</strong><span>Choose a folder to scan without changing your files.</span><button className="primary-button" onClick={onAdd}><FolderOpen size={18} /> Import library</button></div>;
}

function ImportLibraryView({ onNavigate, onStartInventory, onScanLibrary, onUpdateRoot, onRemoveRoot, catalog, scanState, scanProgress }) {
  const [path, setPath] = useState("");
  const [recursive, setRecursive] = useState(true);
  const [pickerState, setPickerState] = useState("");
  const [editingRoot, setEditingRoot] = useState(null);
  const [rootRecursive, setRootRecursive] = useState(true);
  const busy = scanState === "scanning";
  const roots = catalog?.roots || [];
  const lastScan = catalog?.lastScan?.iso ? `${catalog.lastScan.date} ${catalog.lastScan.time}` : "Not scanned yet";
  const scanTotal = Number(scanProgress?.total_files || 0);
  const scanProcessed = Number(scanProgress?.processed_files || 0);
  const scanPercent = scanTotal ? Math.min(100, Math.round((scanProcessed / scanTotal) * 100)) : 0;
  async function chooseFolder() {
    setPickerState("Opening Finder…");
    try {
      const result = await apiRequest("/api/pick-folder");
      if (result.folder) setPath(result.folder);
      setPickerState(result.folder ? "Folder selected" : "Selection canceled");
    } catch (error) {
      setPickerState(error.message);
    }
  }
  async function removeRoot(root) {
    const confirmed = window.confirm(`Remove ${root.path} from Flipparr?\n\nYour comic files will stay exactly where they are, but this folder's comics will no longer appear in your library.`);
    if (!confirmed) return;
    await onRemoveRoot(root);
    if (editingRoot === root.id) setEditingRoot(null);
  }
  function rootScanLabel(value) {
    if (!value) return "Not scanned yet";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }
  return <><PageHeader eyebrow="Library setup" title="Import library" description="Manage folders containing comics you already own and scan them for changes."><button className="ghost-button page-back-action" onClick={() => onNavigate("settings")}><ArrowLeft size={18} /> Back to Settings</button></PageHeader>
    {scanState === "scanning" ? <div className={`scan-progress ${scanTotal ? "determinate" : ""}`} aria-live="polite"><span><strong>{scanTotal ? `Scanning comic ${Math.min(scanProcessed + 1, scanTotal)} of ${scanTotal}` : "Finding comic files…"}</strong><small>{scanProgress?.rootTotal > 1 ? `Folder ${scanProgress.rootIndex} of ${scanProgress.rootTotal} · ${scanProgress.rootPath}` : scanTotal ? `${scanProcessed} complete · reading embedded details, cover art, and file health` : "Counting files before the library scan begins"}</small></span><b>{scanTotal ? `${scanPercent}%` : "Starting"}</b><i style={scanTotal ? { width: `${scanPercent}%` } : undefined} /></div> : null}
    {scanState === "done" ? <div className="success-banner"><CheckCircle size={19} weight="fill" /> Scan complete. Your library is up to date.</div> : null}
    {roots.length ? <section className="library-sources-panel"><header><div><span className="eyebrow">Library folders</span><h2>Your comic sources</h2><p>Each folder is scanned independently. Removing one from Flipparr never deletes or moves its files.</p></div><div className="library-scan-action"><span>Last library scan</span><strong>{lastScan}</strong><button className={`primary-button ${busy ? "loading" : ""}`} onClick={onScanLibrary} disabled={busy}>{busy ? <LoadingSpinner size={19} /> : <ArrowsClockwise size={19} />}{busy ? "Scanning…" : "Scan all folders"}</button></div></header><div className="library-source-list">{roots.map((root) => <article className="library-source" key={root.id}><span className="library-source-icon"><FolderOpen size={22} weight="duotone" /></span><div className="library-source-copy"><strong>{root.path}</strong><span>{root.recursive ? "Includes subfolders" : "Top-level comics only"} · {rootScanLabel(root.last_scan_at)}</span></div><div className="library-source-actions"><button className="ghost-button" disabled={busy} onClick={() => onStartInventory(root.path, Boolean(root.recursive))}><ArrowsClockwise size={16} /> Scan</button><button className="ghost-button" disabled={busy} onClick={() => { setEditingRoot(editingRoot === root.id ? null : root.id); setRootRecursive(Boolean(root.recursive)); }}><PencilSimple size={16} /> Manage</button></div>{editingRoot === root.id ? <div className="library-source-editor"><label className="check-row"><input type="checkbox" checked={rootRecursive} onChange={(event) => setRootRecursive(event.target.checked)} /><span><strong>Include subfolders</strong><small>Apply this setting on future scans</small></span></label><div><button className="secondary-button" onClick={async () => { await onUpdateRoot(root, rootRecursive); setEditingRoot(null); }}>Save setting</button><button className="danger-button" onClick={() => removeRoot(root)}>Remove from Flipparr</button></div><small>To change the folder path, add the new folder below, then remove this source.</small></div> : null}</article>)}</div></section> : null}
    <section className="focused-panel add-panel"><div className="panel-icon"><FolderOpen size={30} weight="duotone" /></div><h2>Add another library folder</h2><p>We’ll inventory the issues and volumes already in this folder, use covers and metadata from the files, and check for damaged archives. Online details can be refreshed after the library is visible.</p><label className="form-field"><span>Library folder</span><div className="path-input"><input value={path} placeholder="/comics-archive" onChange={(event) => setPath(event.target.value)} /><button type="button" onClick={chooseFolder}>Choose folder</button></div>{pickerState ? <small>{pickerState}</small> : null}</label><label className="check-row"><input type="checkbox" checked={recursive} onChange={(event) => setRecursive(event.target.checked)} /><span><strong>Include subfolders</strong><small>Useful when each series has its own folder</small></span></label><div className="safety-note"><ShieldCheck size={22} weight="fill" /><span><strong>Your files stay untouched</strong><small>No files will be renamed, moved, or modified during this scan.</small></span></div><div className="docker-path-note"><HardDrive size={20} /><span><strong>Using Docker?</strong><small>Mount each NAS share into the Flipparr container first, then enter its container path here. Avoid adding a folder inside an existing source.</small></span></div><div className="panel-actions"><button className={`primary-button ${busy ? "loading" : ""}`} disabled={busy || !path.trim()} onClick={() => onStartInventory(path, recursive)}>{busy ? <LoadingSpinner size={19} /> : <UploadSimple size={19} />} {busy ? "Scanning…" : "Import and scan folder"}</button><button className="ghost-button" onClick={() => onNavigate("settings")}>Cancel</button></div></section></>;
}

function RequestsView({ catalog, onCreateRequest, onCancelReplacement, onRefresh }) {
  const [requestOpen, setRequestOpen] = useState(false);
  const [releaseJob, setReleaseJob] = useState(null);
  const [tab, setTab] = useState("wanted");
  const [searchingMissing, setSearchingMissing] = useState(false);
  const [searchMissingMessage, setSearchMissingMessage] = useState("");
  const [pendingSearch, setPendingSearch] = useState(null);
  // The Wanted list's own action, the way Radarr and Sonarr put one there:
  // everything still missing, searched on demand. It covers issues nothing
  // good enough was found for last time, and requests made before Flipparr
  // searched on its own.
  async function searchMissing(confirmed) {
    // Only a literal true starts downloads. Passed this function directly as
    // an onClick handler, `confirmed` was the click event: JSON.stringify threw
    // on its cyclic references, and had it survived, its truthiness would have
    // meant "yes, download all of them" without anyone being asked.
    const startDownloads = confirmed === true;
    setSearchingMissing(true);
    setSearchMissingMessage("");
    try {
      const result = await apiRequest("/api/v1/requests/search-missing", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmed: startDownloads }),
      });
      // Asking first is the point: this downloads every missing issue at once,
      // and how many that is only becomes clear once the jobs are reconciled
      // against what is already on disk.
      if (result.status === "confirm") {
        setPendingSearch(result);
        return;
      }
      setPendingSearch(null);
      setSearchMissingMessage(result.detail || "");
      await onRefresh?.();
    } catch (error) {
      setPendingSearch(null);
      setSearchMissingMessage(error.message || "That search could not be started");
    } finally {
      setSearchingMissing(false);
    }
  }
  const requests = catalog?.requests || [];
  const replacements = catalog?.replacementRequests || [];
  const [progress, setProgress] = useState({});
  // Declared after the lists it reads: `requests` and `replacements` are const,
  // so reaching them from above is a ReferenceError that blanks the page.
  const anyDownloading = [...requests, ...replacements]
    .flatMap((request) => request.jobs || [])
    .some((job) => ["queued", "downloading"].includes(job.downloadStatus));
  useEffect(() => {
    if (!anyDownloading) { setProgress({}); return undefined; }
    let cancelled = false;
    async function poll() {
      try {
        const result = await apiRequest("/api/v1/acquisition/progress");
        if (!cancelled) setProgress(result?.downloads || {});
      } catch (error) { /* a download client that cannot answer shows no bar */ }
    }
    poll();
    const timer = window.setInterval(poll, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [anyDownloading]);
  const activeReplacements = replacements.filter((request) => !["fulfilled", "cancelled"].includes(request.status));
  const completedReplacements = replacements.filter((request) => request.status === "fulfilled");
  // Wanted is what can be acted on now. A followed run whose only remaining
  // issues are unpublished has nothing to look for, so it belongs under
  // Following rather than sitting in Wanted marked "Waiting for release".
  const hasSomethingToFind = (request) => Boolean(
    (request.wantedIssueCount || 0)
    || (request.queuedJobCount || 0)
    || (request.jobs || []).some((job) => !["fulfilled", "cancelled"].includes(job.status))
  );
  const wantedRuns = requests.filter(
    (request) => request.status === "open" && hasSomethingToFind(request)
  );
  const followedRuns = requests.filter(
    (request) => request.status === "fulfilled"
      || (request.status === "open" && !hasSomethingToFind(request))
  );
  const seriesEntries = tab === "wanted" ? wantedRuns : tab === "following" ? followedRuns : [];
  const replacementEntries = tab === "wanted" ? activeReplacements : tab === "acquired" ? completedReplacements : [];
  const wantedCount = wantedRuns.length + activeReplacements.length;
  const acquiredCount = completedReplacements.length;
  const followingCount = followedRuns.length;
  const hasEntries = seriesEntries.length || replacementEntries.length;
  const tabCopy = {
    wanted: {
      description: "Missing issues and replacements Flipparr is actively looking for.",
      emptyTitle: "Nothing on your wanted list",
      emptyDetail: "Follow a run or request a replacement from Library health.",
    },
    acquired: {
      description: "Replacement issues and volumes that were downloaded, verified, and added to your library.",
      emptyTitle: "No acquired comics yet",
      emptyDetail: "Finished replacements appear here after Flipparr validates and imports them.",
    },
    following: {
      description: "Runs Flipparr is monitoring that have no currently released issues on Wanted.",
      emptyTitle: "No caught-up runs are being followed",
      emptyDetail: "Follow a run and Flipparr will keep checking it for newly released issues.",
    },
  }[tab];
  return <><PageHeader title="Requests" description="Follow runs, find missing comics, and replace files that are damaged, incorrect, or poor quality."><button className="secondary-button" onClick={() => searchMissing(false)} disabled={searchingMissing} aria-busy={searchingMissing}>{searchingMissing ? <LoadingSpinner size={18} /> : <MagnifyingGlass size={18} />} Search for missing</button><button className="primary-button" onClick={() => setRequestOpen(true)}><Plus size={19} /> Follow a run</button></PageHeader><div className="request-tabs"><button className={tab === "wanted" ? "active" : ""} onClick={() => setTab("wanted")}>Wanted <b>{wantedCount}</b></button><button className={tab === "acquired" ? "active" : ""} onClick={() => setTab("acquired")}>Acquired <b>{acquiredCount}</b></button><button className={tab === "following" ? "active" : ""} onClick={() => setTab("following")}>Following <b>{followingCount}</b></button></div><p className="request-tab-description">{tabCopy.description}</p>{pendingSearch ? <div className="request-search-confirm" role="alertdialog"><div><strong>{pendingSearch.detail}</strong><small>Downloads start immediately, one for every issue listed.</small></div><span><button type="button" className="ghost-button" onClick={() => setPendingSearch(null)}>Cancel</button><button type="button" className="primary-button" disabled={searchingMissing} onClick={() => searchMissing(true)}>{searchingMissing ? <LoadingSpinner size={17} /> : <CloudArrowDown size={17} />} Start downloads</button></span></div> : null}{searchMissingMessage ? <p className="request-search-result" role="status">{searchMissingMessage}</p> : null}<section className="request-list">{hasEntries ? <>{replacementEntries.map((request) => <ReplacementRequestRow request={request} progress={progress} onCancel={onCancelReplacement} onFindRelease={setReleaseJob} onRefresh={onRefresh} key={`replacement-${request.id}`} />)}{seriesEntries.map((request) => <RequestRow request={request} progress={progress} onFindRelease={setReleaseJob} onRefresh={onRefresh} key={`series-${request.id}`} />)}</> : <div className="empty-state request-empty"><CheckCircle size={34} weight="duotone" /><strong>{tabCopy.emptyTitle}</strong><span>{tabCopy.emptyDetail}</span></div>}</section>{requestOpen ? <RequestModal catalog={catalog} onCreate={async (target) => { const result = await onCreateRequest(target); if (result?.ok) setRequestOpen(false); return result; }} onClose={() => setRequestOpen(false)} /> : null}{releaseJob ? <ReleaseSearchModal job={releaseJob} onClose={() => setReleaseJob(null)} onGrabbed={async () => { await onRefresh?.(); setReleaseJob(null); }} /> : null}</>;
}

function acquisitionFailureDetails(job) {
  const technical = String(job?.downloadError || "").trim();
  const normalized = technical.toLowerCase();
  if (job?.downloadFailureStage === "import") return {
    label: "Import failed",
    message: "The download finished, but Flipparr could not validate or add the comic to your library. The original file is still active.",
    technical,
  };
  if (normalized.includes("not-complete") || normalized.includes("aborted, cannot be completed")) return {
    label: "Incomplete release",
    message: "SABnzbd could not retrieve enough Usenet articles to finish this release. Choose another release to continue.",
    technical,
  };
  return {
    label: job?.downloadFailureStage === "download" ? "Download failed" : "Request failed",
    message: job?.downloadFailureStage === "download"
      ? "SABnzbd could not complete this release. Choose another release or review the technical details."
      : "Flipparr could not complete this request. Review the technical details before trying again.",
    technical,
  };
}

function requestFailureStatus(jobs) {
  const failedJobs = jobs.filter((job) => job.status === "failed" || job.downloadStatus === "failed");
  if (!failedJobs.length) return null;
  if (failedJobs.length === 1) return acquisitionFailureDetails(failedJobs[0]).label;
  const allIncomplete = failedJobs.every((job) => acquisitionFailureDetails(job).label === "Incomplete release");
  return allIncomplete ? `${failedJobs.length} incomplete releases` : `${failedJobs.length} downloads failed`;
}

function JobProgress({ entry }) {
  // Only while SABnzbd is actually holding this one. A row with nothing moving
  // shows nothing rather than an empty bar sitting at zero.
  if (!entry) return null;
  const percent = Math.max(0, Math.min(100, Number(entry.percent) || 0));
  const detail = [
    entry.timeLeft && entry.timeLeft !== "0:00:00" ? `${entry.timeLeft} left` : "",
    entry.sizeLeft ? `${entry.sizeLeft} to go` : "",
  ].filter(Boolean).join(" · ");
  return <div className="job-progress">
    <div className="job-progress-track" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} aria-label="Download progress"><i style={{ width: `${percent}%` }} /></div>
    <small>{percent}%{detail ? ` · ${detail}` : ""}</small>
  </div>;
}

function ReplacementRequestRow({ request, progress = {}, onCancel, onFindRelease, onRefresh }) {
  const [expanded, setExpanded] = useState(false);
  const [retryingJobId, setRetryingJobId] = useState(null);
  const [retryError, setRetryError] = useState(null);
  const jobs = request.jobs || [];
  const failed = jobs.filter((job) => job.status === "failed" || job.downloadStatus === "failed").length;
  const importing = jobs.filter((job) => ["completed", "importing", "waiting_for_files"].includes(job.downloadStatus)).length;
  const downloading = jobs.filter((job) => ["queued", "downloading"].includes(job.downloadStatus)).length;
  const searching = jobs.filter((job) => job.status === "searching").length;
  const display = { id: `replacement-${request.id}`, title: request.seriesTitle || request.targetTitle, cover: request.cover };
  const statusLabels = request.targetType === "issue" ? { wanted: "Fix issue", searching: "Finding issue", grabbed: "Issue found", failed: "Needs attention", fulfilled: "Issue fixed" } : { wanted: "Fix run", searching: "Finding comics", grabbed: "Run fix found", failed: "Needs attention", fulfilled: "Run fixed" };
  const statusLabel = request.status === "fulfilled" ? statusLabels.fulfilled : failed ? requestFailureStatus(jobs) : importing ? "Adding to library" : downloading ? "Downloading" : searching ? "Searching" : statusLabels[request.status] || request.status;
  const tone = request.status === "fulfilled" ? "green" : request.status === "failed" || failed ? "red" : "violet";
  const preference = ACQUISITION_LABELS[request.acquisitionPreference] || ACQUISITION_LABELS.either;
  const title = request.seriesTitle || request.targetTitle;
  const scope = [preference, request.coverageTarget, request.reason, request.desiredLanguage ? `Wanted language: ${request.desiredLanguage}` : null].filter(Boolean).join(" · ");
  async function retryJob(job) {
    setRetryingJobId(job.id); setRetryError(null);
    try {
      const result = await apiRequest(`/api/v1/acquisition-jobs/${job.id}/retry`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await onRefresh?.();
      if (result.action === "research") onFindRelease(job);
    } catch (error) {
      setRetryError({ jobId: job.id, message: error.message || "This replacement could not be retried" });
    } finally { setRetryingJobId(null); }
  }
  return <article className={`request-card replacement-request-card ${expanded ? "expanded" : ""}`}><div className="request-row"><span className="request-cover"><SeriesCover series={display} decorative /></span><div><h3>{title}</h3><p>{scope}</p><span>{request.targetTitle !== title ? `${request.targetTitle} · ` : ""}{request.filename} · Added {request.requestedDate} {request.requestedTime}</span></div><StatusBadge tone={tone}>{statusLabel}</StatusBadge><button className="request-expand" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}><span>{expanded ? "Hide issue details" : "View issue details"}</span><CaretDown size={17} /></button></div>{expanded ? <div className="request-job-panel"><header><div><strong>{request.status === "fulfilled" ? "Replacement complete" : "Comics needed for this replacement"}</strong><span>{request.status === "fulfilled" ? "The verified replacement is active and the original is held in recoverable quarantine." : "Flipparr searches and grabs the best match for each issue. The original stays active until every replacement passes validation."}</span></div>{!["fulfilled", "cancelled"].includes(request.status) ? <button className="ghost-button" onClick={() => onCancel(request)}>Cancel request</button> : null}</header>{jobs.length ? <div className="request-jobs">{jobs.map((job) => { const displayStatus = job.downloadStatus || job.status; const imported = job.downloadStatus === "imported"; const failedJob = job.status === "failed" || job.downloadStatus === "failed"; const canSearch = !job.downloadStatus && !["grabbed", "fulfilled", "cancelled"].includes(job.status); const retryMessage = retryError?.jobId === job.id ? retryError.message : null; const failure = failedJob ? acquisitionFailureDetails(job) : null; const detail = retryMessage || (!failedJob ? job.downloadTitle : null); return <div className="request-job" key={job.id}><b>#{job.issueNumber}</b><div><strong>{job.issueTitle || `Issue ${job.issueNumber}`}</strong><span>{job.reason}</span>{failure ? <div className="job-failure-copy"><strong>{failure.label}</strong><small>{failure.message}</small>{failure.technical ? <details><summary>Technical details</summary><code>{failure.technical}</code></details> : null}</div> : detail ? <span className={retryMessage ? "job-error" : ""}>{detail}</span> : null}<JobProgress entry={progress[String(job.id)]} /></div><span className="request-job-actions"><span className={`job-state ${displayStatus}`}>{DOWNLOAD_STATUS_LABELS[job.downloadStatus] || JOB_STATUS_LABELS[job.status] || displayStatus}</span>{failedJob ? <><button type="button" disabled={retryingJobId === job.id} onClick={() => retryJob(job)}>{retryingJobId === job.id ? <LoadingSpinner size={14} /> : <ArrowsClockwise size={14} />} {job.downloadFailureStage === "import" ? "Retry import" : "Try next release"}</button><button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button></> : !imported && canSearch ? <button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button> : null}</span></div>; })}</div> : <div className="request-job-empty"><WarningCircle size={20} /><div><strong>No safe issue targets are available</strong><span>Confirm the comic’s issue contents before replacing it.</span></div></div>}</div> : <footer className="replacement-safety-note"><ShieldCheck size={16} weight="fill" /> The current comic stays in your library until all mapped replacements are downloaded and verified.</footer>}</article>;
}

function RequestRow({ request, progress = {}, onFindRelease, onRefresh }) {
  const [expanded, setExpanded] = useState(false);
  const [retryingJobId, setRetryingJobId] = useState(null);
  const [retryError, setRetryError] = useState(null);
  const jobs = request.jobs || [];
  const ready = request.wantedIssueCount || 0;
  const upcoming = request.upcomingIssueCount || 0;
  const unknown = request.unknownReleaseIssueCount || 0;
  const queued = request.queuedJobCount || 0;
  const searching = jobs.filter((job) => job.status === "searching").length;
  const downloading = jobs.filter((job) => ["queued", "downloading"].includes(job.downloadStatus)).length;
  const importing = jobs.filter((job) => ["completed", "importing", "waiting_for_files"].includes(job.downloadStatus)).length;
  const failed = jobs.filter((job) => job.status === "failed" || job.downloadStatus === "failed").length;
  const scope = [
    ready ? `${ready} missing issue${ready === 1 ? "" : "s"}` : null,
    upcoming ? `${upcoming} upcoming` : null,
    unknown ? `${unknown} release date${unknown === 1 ? "" : "s"} unknown` : null,
    `${request.ownedIssueCount} of ${request.targetIssueCount} owned`,
  ].filter(Boolean).join(" · ");
  const status = request.status === "fulfilled" ? "Up to date" : failed ? requestFailureStatus(jobs) : importing ? `${importing} adding to library` : downloading ? `${downloading} downloading` : searching ? "Searching" : queued ? `${queued} wanted` : upcoming ? "Waiting for release" : "Checking release dates";
  const tone = request.status === "fulfilled" ? "green" : failed ? "red" : queued || searching || downloading || importing ? "violet" : upcoming ? "green" : "muted";
  const display = { id: `request-${request.id}`, title: request.title, cover: request.cover };
  // An issue already in the library is not a missing issue. Listing its job
  // under "Missing issues" put "Added to library" in the middle of a list of
  // things still being looked for.
  const outstandingJobs = jobs.filter(
    (job) => !["fulfilled", "cancelled"].includes(job.status)
  );
  const jobGroups = outstandingJobs.reduce((groups, job) => {
    const key = job.seriesId || "series";
    if (!groups[key]) groups[key] = { id: key, title: job.seriesTitle || request.title, jobs: [] };
    groups[key].jobs.push(job);
    return groups;
  }, {});
  async function retryJob(job) {
    setRetryingJobId(job.id);
    setRetryError(null);
    try {
      const result = await apiRequest(`/api/v1/acquisition-jobs/${job.id}/retry`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      await onRefresh?.();
      if (result.action === "research") onFindRelease(job);
    } catch (error) {
      setRetryError({ jobId: job.id, message: error.message || "This issue could not be retried" });
    } finally {
      setRetryingJobId(null);
    }
  }
  return <article className={`request-card ${expanded ? "expanded" : ""}`}>
    <div className="request-row">
      <span className="request-cover"><SeriesCover series={display} decorative /></span>
      <div><h3>{request.title}</h3><p>{scope}</p><span>Following · {request.publicationStatus === "ongoing" ? "checks daily for newly listed issues" : "completed run"} · {ACQUISITION_LABELS[request.acquisitionPreference] || ACQUISITION_LABELS.either}</span></div>
      <StatusBadge tone={tone}>{status}</StatusBadge>
      <button className="request-expand" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}><span>{expanded ? "Hide issue details" : "View issue details"}</span><CaretDown size={17} /></button>
    </div>
    {expanded ? <div className="request-job-panel">
      <header><div><strong>{request.status === "fulfilled" ? "Run is up to date" : "Missing issues"}</strong><span>{request.status === "fulfilled" ? (request.publicationStatus === "ongoing" ? "Flipparr will keep checking this run and add newly released issues to Wanted." : "Every issue in this completed run is in your library.") : "Flipparr searches for each missing issue and grabs the best match. Anything it cannot decide waits here for you."}</span></div><b>Following</b></header>
      {outstandingJobs.length ? <div className="request-jobs">{Object.values(jobGroups).map((group) => <section className="request-job-group" key={group.id}>
        <header><span>Series run</span><strong>{group.title}</strong><b>{group.jobs.length} issue{group.jobs.length === 1 ? "" : "s"}</b></header>
        {group.jobs.map((job) => { const displayStatus = job.downloadStatus || job.status; const imported = job.downloadStatus === "imported"; const failedJob = job.status === "failed" || job.downloadStatus === "failed"; const relativeDestination = job.downloadDestination?.split("/comics/").pop(); const retryMessage = retryError?.jobId === job.id ? retryError.message : null; const failure = failedJob ? acquisitionFailureDetails(job) : null; const displayDetail = retryMessage || (!failedJob ? (relativeDestination ? `Library: ${relativeDestination}` : job.downloadTitle) : null); const canSearch = !job.downloadStatus && !["grabbed", "fulfilled", "cancelled"].includes(job.status); const retryLabel = job.downloadFailureStage === "import" ? "Retry import" : "Try next release"; return <div className="request-job" key={job.id}>
          <b>#{job.issueNumber}</b>
          <div><strong>{job.issueTitle || `Issue ${job.issueNumber}`}</strong><span>{job.reason}</span>{failure ? <div className="job-failure-copy"><strong>{failure.label}</strong><small>{failure.message}</small>{failure.technical ? <details><summary>Technical details</summary><code>{failure.technical}</code></details> : null}</div> : displayDetail ? <span className={retryMessage ? "job-error" : ""}>{displayDetail}</span> : null}<JobProgress entry={progress[String(job.id)]} /></div>
          <span className="request-job-actions"><span className={`job-state ${displayStatus}`}>{DOWNLOAD_STATUS_LABELS[job.downloadStatus] || JOB_STATUS_LABELS[job.status] || displayStatus}</span>{failedJob ? <><button type="button" disabled={retryingJobId === job.id} onClick={() => retryJob(job)}>{retryingJobId === job.id ? <LoadingSpinner size={14} /> : <ArrowsClockwise size={14} />} {retryingJobId === job.id ? "Retrying…" : retryLabel}</button><button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button></> : !imported && canSearch ? <button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button> : null}</span>
        </div>; })}
      </section>)}</div> : <div className="request-job-empty"><ArrowsClockwise size={20} /><div><strong>Nothing is ready to search yet</strong><span>No action is required. Flipparr will keep checking release dates automatically.</span></div></div>}
      {upcoming || unknown ? <footer>{upcoming ? `${upcoming} upcoming issue${upcoming === 1 ? " is" : "s are"} being followed` : null}{upcoming && unknown ? " · " : null}{unknown ? `${unknown} issue${unknown === 1 ? " has" : "s have"} an unknown release date` : null}</footer> : null}
    </div> : null}
  </article>;
}

function formatReleaseSize(bytes) {
  const value = Number(bytes || 0);
  if (!value) return "Size unavailable";
  if (value >= 1024 ** 3) return `${(value / (1024 ** 3)).toFixed(2)} GB`;
  return `${Math.max(1, Math.round(value / (1024 ** 2)))} MB`;
}

function ReleaseSearchModal({ job, onClose, onGrabbed }) {
  const dialogRef = useDialog(onClose);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [grabbingId, setGrabbingId] = useState(null);
  // The query is editable. It looked like a field and was not one, which is
  // exactly when you want it: nothing was found, and the wording is the thing
  // worth changing.
  const [query, setQuery] = useState("");
  const [edited, setEdited] = useState(false);
  async function search(custom) {
    setLoading(true); setError("");
    const asked = typeof custom === "string" ? custom.trim() : "";
    try {
      const found = await apiRequest(`/api/v1/acquisition-jobs/${job.id}/search`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(asked ? { query: asked } : {}),
      });
      setResult(found);
      // Show the query that ran, which is not always the one first tried.
      if (!asked) setQuery(found?.query || "");
    } catch (searchError) {
      setError(searchError.message || "Prowlarr search failed");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { setEdited(false); setQuery(""); search(); }, [job.id]);
  async function grab(candidate) {
    setGrabbingId(candidate.id); setError("");
    try {
      await apiRequest(`/api/v1/acquisition-jobs/${job.id}/grab`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidateId: candidate.id }),
      });
      await onGrabbed?.();
    } catch (grabError) {
      setError(grabError.message || "SABnzbd could not accept this release");
      setGrabbingId(null);
    }
  }
  const candidates = result?.candidates || [];
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal release-search-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="release-search-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close release search"><X size={20} /></button><span className="eyebrow">Find one missing issue</span><h2 id="release-search-title">{job.seriesTitle} #{job.issueNumber}</h2><p className="workbench-intro">Compare Prowlarr results below. Nothing is sent to SABnzbd until you choose a release.</p><form className="release-query" onSubmit={(event) => { event.preventDefault(); search(query); }}><MagnifyingGlass size={16} /><input value={query} onChange={(event) => { setQuery(event.target.value); setEdited(true); }} aria-label="Search terms" placeholder="Series and issue to search for…" disabled={loading} /><button type="submit" className="secondary-button" disabled={loading || query.trim().length < 2}>{loading ? <LoadingSpinner size={16} /> : null} Search</button></form>{edited ? null : <p className="release-query-note">Flipparr widens this automatically when a narrower wording finds nothing. Edit it to search for something else.</p>}{loading ? <div className="release-loading"><LoadingSpinner size={24} /><div><strong>Searching your indexers…</strong><span>This can take a few seconds.</span></div></div> : null}{error ? <div className="release-error"><WarningCircle size={19} weight="fill" /><span><strong>Release search needs attention</strong>{error}</span><button type="button" onClick={() => search(query)}>Try again</button></div> : null}{!loading && !error && !candidates.length ? <div className="release-empty"><MagnifyingGlass size={28} /><strong>No credible releases found</strong><span>Prowlarr returned no Usenet results that matched both this series and issue number.</span><button type="button" className="secondary-button" onClick={() => search(query)}>Search again</button></div> : null}{candidates.length ? <div className="release-candidates"><header><div><strong>{candidates.length} candidate{candidates.length === 1 ? "" : "s"}</strong><span>Best matches appear first. Confirm the title, issue, language, and format.</span></div></header>{candidates.map((candidate) => { const isGrabbing = grabbingId === candidate.id; return <article className="release-candidate" key={candidate.id}><div className="release-candidate-main"><StatusBadge tone={candidate.matchScore >= 85 ? "green" : "amber"}>{candidate.matchStrength}</StatusBadge><h3>{candidate.title}</h3><p>{candidate.indexer} · {candidate.protocol} · {formatReleaseSize(candidate.sizeBytes)}{candidate.publishDate ? ` · ${new Date(candidate.publishDate).toLocaleDateString()}` : ""}</p>{candidate.formatTags?.length ? <div className="release-tags">{candidate.formatTags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}</div><div className="release-match"><strong>{candidate.matchScore}</strong><span>match score</span></div><ul>{candidate.matchReasons.map((reason) => <li key={reason}><CheckCircle size={14} weight="fill" />{reason}</li>)}</ul><button type="button" className={`primary-button ${isGrabbing ? "loading" : ""}`} aria-busy={isGrabbing} disabled={Boolean(grabbingId)} onClick={() => grab(candidate)}>{isGrabbing ? <LoadingSpinner size={17} /> : <CloudArrowDown size={17} />}{isGrabbing ? "Sending…" : "Send to SABnzbd"}</button></article>; })}</div> : null}<footer className="release-modal-footer"><ShieldCheck size={17} weight="fill" /> Prowlarr download links stay on the server and are never exposed in this page.</footer></section></div>;
}

function RequestModal({ catalog, onClose, onCreate }) {
  const dialogRef = useDialog(onClose);
  const [term, setTerm] = useState("");
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState("");
  const targets = logicalCatalogSeries(catalog).filter((item) => `${item.title} ${item.publisher}`.toLowerCase().includes(term.toLowerCase()));
  async function choose(target) {
    setBusyId(target.id); setError("");
    const result = await onCreate(target);
    if (!result?.ok) setError(result?.error || "Request could not be created");
    setBusyId(null);
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}><section className="modal request-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="request-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close"><X size={20} /></button><span className="eyebrow">Run monitoring</span><h2 id="request-title">Choose a run to follow</h2><p className="workbench-intro">Flipparr will find released issues you do not own and check ongoing runs daily for newly listed issues.</p><SearchBar value={term} onChange={setTerm} placeholder="Search series…" /><div className="request-results">{targets.map((target) => { const summary = target.releaseSummary || {}; const unavailable = !target.total || !target.catalogKnown; const scopeType = target.isCollectionSeries ? "collection" : "series"; const scopeId = String(target.isCollectionSeries ? target.collection.id : target.id); const existing = (catalog?.requests || []).some((request) => request.storedStatus === "open" && request.scopeType === scopeType && request.scopeId === scopeId); return <div className="request-result" key={target.id}><span className="request-result-cover"><SeriesCover series={target} decorative /></span><div><strong>{target.title}</strong><span>{target.publisher} · {target.year}</span><small>{target.catalogKnown ? `${target.owned} of ${target.total} owned · ${summary.releasedMissing || 0} missing issue${summary.releasedMissing === 1 ? "" : "s"}` : "Issue list unavailable"}</small></div><button disabled={unavailable || busyId === target.id || existing} onClick={() => choose(target)}>{busyId === target.id ? "Following…" : existing ? "Following" : "Follow run"}</button></div>; })}</div>{!targets.length ? <div className="drawer-empty"><MagnifyingGlass size={27} /><strong>No matching series</strong></div> : null}{error ? <p className="workbench-error" role="alert">{error}</p> : null}<p className="modal-hint">A completed run stays followed until every issue is owned. An ongoing run moves new released issues to Wanted automatically.</p></section></div>;
}

function MetadataView({ items, backendStatus, onResolve, onReplace }) {
  const entries = items.length ? items : (backendStatus === "offline" ? DEMO_META_ITEMS : []);
  const [selected, setSelected] = useState(0);
  const [resolved, setResolved] = useState([]);
  const [mobileBrowseOpen, setMobileBrowseOpen] = useState(false);
  const selectedIndex = Math.min(selected, Math.max(entries.length - 1, 0));
  const item = entries[selectedIndex];
  const isResolved = resolved.includes(selectedIndex);
  if (!item) return <><PageHeader title="Library health" description="Replace damaged or incorrect comics and review uncertain metadata." /><div className="empty-state"><CheckCircle size={35} weight="duotone" /><strong>Your library looks healthy</strong><span>Damaged files and uncertain matches will appear here after a scan.</span></div></>;
  async function resolveCurrent() {
    if (item.path && item.code && item.fingerprint) await onResolve(item);
    else setResolved((current) => [...current, selectedIndex]);
  }
  const fileProblem = item.category === "file" || ["empty_archive", "no_image_pages", "corrupt_archive", "file_health"].includes(item.code);
  function selectProblem(index) {
    setSelected(index);
    setMobileBrowseOpen(false);
  }
  return <><PageHeader title="Library health" description="Replace damaged or incorrect comics and review uncertain metadata." /><div className="metadata-layout"><nav className="mobile-inbox-nav" aria-label="Navigate library problems"><button type="button" disabled={selectedIndex === 0} onClick={() => selectProblem(selectedIndex - 1)} aria-label="Previous problem"><ArrowLeft size={18} /></button><button type="button" className="mobile-inbox-browser" aria-expanded={mobileBrowseOpen} onClick={() => setMobileBrowseOpen((open) => !open)}><ListBullets size={18} /><span><strong>Problem {selectedIndex + 1} of {entries.length}</strong><small>{mobileBrowseOpen ? "Close problem list" : "Browse all problems"}</small></span></button><button type="button" disabled={selectedIndex === entries.length - 1} onClick={() => selectProblem(selectedIndex + 1)} aria-label="Next problem"><ArrowRight size={18} /></button></nav><aside className={`inbox-list ${mobileBrowseOpen ? "mobile-browse-open" : ""}`}><div className="inbox-label">Needs attention <b>{entries.length - resolved.length}</b></div>{entries.map((entry, index) => { const replacementOpen = entry.replacementStatus && !["fulfilled", "cancelled"].includes(entry.replacementStatus); return <button key={entry.id ?? `${entry.file}-${entry.issue}`} className={`${selectedIndex === index ? "active" : ""} ${resolved.includes(index) ? "resolved" : ""} ${replacementOpen ? "replacement-triaged" : ""}`} onClick={() => selectProblem(index)}>{replacementOpen ? <CheckCircle size={19} weight="fill" /> : <WarningCircle size={19} weight={entry.severity === "error" ? "fill" : "regular"} />}<span><strong>{entry.file}</strong><small>{resolved.includes(index) ? "Fixed" : replacementOpen ? "Replacement requested · original retained" : `${entry.category === "file" ? "Comic file" : "Metadata"} · ${entry.issue}`}</small></span></button>; })}</aside><section className="review-panel"><div className="review-heading"><StatusBadge tone={item.severity === "error" ? "red" : "amber"}>{fileProblem ? "Comic file problem" : "Metadata review"}</StatusBadge><h2>{item.file}</h2><p>{item.detail}</p></div>{fileProblem ? <FileProblem item={item} onReplace={onReplace} /> : item.code === "metadata_conflict" && item.comparison ? <MetadataComparison comparison={item.comparison} /> : <MetadataProblem item={item} />}<div className="review-actions"><button className={fileProblem ? "ghost-button" : "primary-button"} disabled={isResolved} onClick={resolveCurrent}><CheckCircle size={19} /> {isResolved ? "Fixed" : "Mark fixed"}</button>{!fileProblem ? <button className="ghost-button"><PencilSimple size={18} /> Edit metadata</button> : null}<button className="ghost-button">Dismiss</button></div></section></div></>;
}

function FileProblem({ item, onReplace }) {
  const [revealState, setRevealState] = useState({ status: "idle", message: "" });
  useEffect(() => setRevealState({ status: "idle", message: "" }), [item.path]);
  async function revealFile() {
    if (!item.path) {
      setRevealState({ status: "error", message: "The file path is unavailable." });
      return;
    }
    setRevealState({ status: "loading", message: "Opening Finder…" });
    try {
      const result = await apiRequest("/api/v1/reveal-file", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: item.path }),
      });
      setRevealState({ status: "success", message: result.message || "Shown in Finder." });
    } catch (error) {
      setRevealState({ status: "error", message: error.message || "Finder could not show this file." });
    }
  }
  const replacementOpen = item.replacementStatus && !["fulfilled", "cancelled"].includes(item.replacementStatus);
  const replacementLabel = item.replacementStatus === "failed" ? "Replacement needs attention" : item.replacementStatus === "grabbed" ? "Replacement in progress" : "Replacement requested";
  return <div className="file-problem"><HardDrive size={35} weight="duotone" /><div><strong>{item.issue}</strong><div className="file-problem-actions">{replacementOpen ? <span className={`replacement-requested-inline ${item.replacementStatus === "failed" ? "failed" : ""}`}><CheckCircle size={18} weight="fill" /> {replacementLabel}</span> : <button className="primary-button" onClick={() => onReplace(item)}><CloudArrowDown size={18} /> Replace comic</button>}<button onClick={revealFile} disabled={revealState.status === "loading"}>{revealState.status === "loading" ? <LoadingSpinner size={18} /> : <FolderOpen size={18} />} {revealState.status === "loading" ? "Opening Finder…" : "Show comic file"}</button></div><small className="replacement-help">{replacementOpen ? "The original remains active until every replacement is downloaded, validated, and added to your library. Progress and failures appear under Requests." : "Add the mapped issues to your wanted list without deleting this file. It stays in place until every replacement is downloaded and verified."}</small>{revealState.message ? <small className={`file-reveal-status ${revealState.status}`}>{revealState.message}</small> : null}</div></div>;
}

function MetadataProblem({ item }) {
  return <div className="metadata-problem"><Database size={32} weight="duotone" /><div><strong>{item.issue}</strong><small>This changes the library record only; the comic file is not modified.</small></div></div>;
}

function ReplacementModal({ file, busy, error, onClose, onSubmit }) {
  const dialogRef = useDialog(onClose);
  const automaticReason = file.code === "empty_archive" ? "empty" : file.code === "no_image_pages" ? "no_pages" : "corrupt";
  const [reason, setReason] = useState(automaticReason);
  const [language, setLanguage] = useState("English");
  const [acquisitionPreference, setAcquisitionPreference] = useState("issues");
  function submit(event) {
    event.preventDefault();
    onSubmit({ reason, desiredLanguage: reason === "wrong_language" ? language : null, acquisitionPreference });
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal replacement-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="replacement-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close replacement request"><X size={20} /></button><span className="eyebrow">Fix comic run</span><h2 id="replacement-title">{file.file || file.filename}</h2><p className="workbench-intro">Add this comic’s mapped issues to the wanted list. Flipparr will keep the current file active until every replacement passes validation.</p><form onSubmit={submit}><label className="form-field"><span>Replacement format</span><select value={acquisitionPreference} onChange={(event) => setAcquisitionPreference(event.target.value)}><option value="issues">Mapped single issues</option></select><small>Whole-volume acquisition will be added after volume release matching is reliable.</small></label><label className="form-field"><span>Why replace it?</span><select value={reason} onChange={(event) => setReason(event.target.value)}><option value="corrupt">Corrupt or unreadable file</option><option value="no_pages">No readable comic pages</option><option value="empty">Empty archive</option><option value="wrong_language">Wrong language</option><option value="wrong_release">Wrong edition or release</option><option value="poor_quality">Poor scan or image quality</option></select></label>{reason === "wrong_language" ? <label className="form-field"><span>Language wanted</span><input value={language} onChange={(event) => setLanguage(event.target.value)} placeholder="English" required /></label> : null}<div className="replacement-summary"><ShieldCheck size={19} weight="fill" /><span><strong>Recoverable replacement</strong><small>The original is moved to hidden quarantine only after all mapped issues pass identity, archive, copy, and hash checks.</small></span></div>{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="metadata-edit-actions"><button type="button" className="ghost-button" onClick={onClose}>Cancel</button><button type="submit" className="primary-button" aria-busy={busy} disabled={busy || (reason === "wrong_language" && !language.trim())}>{busy ? <LoadingSpinner size={18} /> : <CloudArrowDown size={18} />} {busy ? "Adding…" : "Add to wanted"}</button></div></form></section></div>;
}

function MetadataComparison({ comparison }) {
  return <div className="comparison"><div className="comparison-head"><span>Field</span><span>{comparison.catalogLabel}</span><span>{comparison.fileLabel}</span></div>{comparison.rows.map((row) => <div className="comparison-row" key={row.field}><strong>{row.field}</strong><span className={row.catalog ? "" : "missing"}>{row.catalog ? <CheckCircle size={16} weight="fill" /> : <WarningCircle size={16} />} {row.catalog || "Not available"}</span><span className={row.status}>{row.status === "match" ? <CheckCircle size={16} weight="fill" /> : <WarningCircle size={16} weight={row.status === "conflict" ? "fill" : "regular"} />} {row.file || "Not available"}</span></div>)}{comparison.catalogUrl ? <div className="comparison-source"><a href={comparison.catalogUrl} target="_blank" rel="noreferrer">Open metadata source</a></div> : null}</div>;
}


const SETTINGS_SECTIONS = [
  { id: "library", label: "Library folders" },
  { id: "matching", label: "Matching and fixes" },
  { id: "security", label: "Security" },
  { id: "acquisition", label: "Acquisition services" },
  { id: "metadata", label: "Metadata sources" },
];

// The documented Docker mount, so most installs need no typing at all.
const DEFAULT_LIBRARY_FOLDER = "/comics";

const SETUP_STEPS = [
  { id: "welcome", label: "Welcome", required: false },
  {
    id: "library", label: "Folder path", required: true,
    title: "Select Folder Path",
    lead: "Files are read where they are. Nothing is renamed or moved.",
  },
  {
    id: "acquisition", label: "Download services", required: false,
    title: "Setup Download Services (Optional)",
    lead: "Finds and downloads issues you are missing.",
  },
  {
    id: "metadata", label: "Data sources", required: false,
    title: "Setup Data Sources",
    lead: "Where issue details, covers and dates come from.",
  },
];

function SetupStepper({ stepIndex }) {
  const steps = SETUP_STEPS.slice(1);
  const position = stepIndex - 1;
  return <ol className="setup-stepper" aria-label="Setup progress">
    {steps.map((step, index) => {
      const state = index < position ? "done" : index === position ? "current" : "upcoming";
      return <li className={state} key={step.id} aria-current={state === "current" ? "step" : undefined}>
        <span className="setup-step-marker">{state === "done" ? <Check size={14} weight="bold" /> : index + 1}</span>
        <span className="setup-step-label">{step.label}</span>
      </li>;
    })}
  </ol>;
}

function SetupLibraryStep({ folder, onFolderChange, recursive, onRecursiveChange, onCheckedChange, existingRoots }) {
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const notify = useRef(onCheckedChange);
  notify.current = onCheckedChange;

  // Re-check when the tab is returned to. Setting up a library usually means
  // going away to tidy the folder and coming back, and a count from before
  // that is worse than no count: it looks current and is not.
  const [recheck, setRecheck] = useState(0);
  useEffect(() => {
    const again = () => setRecheck((count) => count + 1);
    // Two separate signals rather than one guarded by visibility: a window that
    // regains focus is reason enough on its own, and an embedded or backgrounded
    // view can report itself hidden while still being looked at.
    const onVisibility = () => { if (document.visibilityState === "visible") again(); };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", again);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", again);
    };
  }, []);

  // Checked as you type rather than behind a separate button press. The folder
  // is pre-filled with the documented mount, so the common case is that the
  // count is already on screen before the user has done anything at all.
  useEffect(() => {
    const value = String(folder || "").trim();
    setResult(null);
    setError("");
    notify.current(null);
    if (!value) { setChecking(false); return undefined; }
    setChecking(true);
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const found = await apiRequest("/api/v1/library-folder-check", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ folder: value, recursive }),
        });
        if (cancelled) return;
        setResult(found);
        notify.current(found);
      } catch (caught) {
        if (!cancelled) setError(caught.message);
      } finally {
        if (!cancelled) setChecking(false);
      }
    }, 450);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [folder, recursive, recheck]);

  return <div className="setup-step-body">
    {existingRoots.length ? <div className="setup-existing-roots">
      <strong>Already added</strong>
      {existingRoots.map((root) => <span key={root.id}><FolderOpen size={16} /> {root.path}</span>)}
    </div> : null}
    <label className="setup-field">
      <span>Folder path</span>
      <input value={folder} onChange={(event) => onFolderChange(event.target.value)}
        placeholder={DEFAULT_LIBRARY_FOLDER} spellCheck={false} autoCapitalize="none" autoCorrect="off" />
    </label>
    <label className="setup-checkbox">
      <input type="checkbox" checked={recursive} onChange={(event) => onRecursiveChange(event.target.checked)} />
      <span>Include subfolders</span>
    </label>
    <p className={`setup-check ${checking ? "busy" : result ? "ok" : error ? "bad" : "idle"}`} role="status" aria-live="polite">
      {checking ? <><LoadingSpinner size={17} /> Looking…</>
        : result ? <><CheckCircle size={18} weight="fill" /> <span><strong>{result.comicCount}{result.countTruncatedAt ? "+" : ""}</strong> {result.comicCount === 1 ? "comic" : "comics"} ready to import.</span></>
        : error ? <><WarningCircle size={18} weight="fill" /> <span>{error}</span></>
        : <span>The folder you gave Flipparr access to.</span>}
    </p>
  </div>;
}

function SetupView({ catalog, onFinish }) {
  const [stepIndex, setStepIndex] = useState(0);
  const [folder, setFolder] = useState(DEFAULT_LIBRARY_FOLDER);
  const [folderChecked, setFolderChecked] = useState(null);
  const [recursive, setRecursive] = useState(true);
  const [services, setServices] = useState([]);
  const [editingService, setEditingService] = useState(null);
  const [providers, setProviders] = useState([]);
  const [editingProvider, setEditingProvider] = useState(null);
  const [finishing, setFinishing] = useState(false);
  const [error, setError] = useState("");
  const existingRoots = catalog?.roots || [];

  async function loadServices() {
    try { setServices((await apiRequest("/api/v1/acquisition-services")).services || []); } catch (caught) { setError(caught.message); }
  }
  async function loadProviders() {
    try { setProviders((await apiRequest("/api/v1/providers")).providers || []); } catch (caught) { setError(caught.message); }
  }
  useEffect(() => { loadServices(); loadProviders(); }, []);

  const step = SETUP_STEPS[stepIndex];
  // A folder is the one thing setup cannot invent: there is nothing to scan
  // without it. Everything after this is genuinely optional.
  const canLeaveLibraryStep = Boolean(folderChecked) || existingRoots.length > 0;

  async function finish() {
    setFinishing(true); setError("");
    try {
      await apiRequest("/api/v1/settings", {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ setupCompleted: true }),
      });
      await onFinish(folderChecked ? folderChecked.path : "", recursive);
    } catch (caught) {
      setError(caught.message);
      setFinishing(false);
    }
  }

  return <div className="setup-shell">
    <div className={`setup-card${step.id === "welcome" ? " welcome" : ""}`}>
      <header className="setup-header">
        {/* The welcome screen has the mark as its subject and no stepper to
            label, so the small wordmark above it was the name twice over. */}
        {step.id === "welcome" ? null : <div className="setup-topline">
          <span className="setup-brand"><FlipparrWordmark height={22} /></span>
          <SetupStepper stepIndex={stepIndex} />
        </div>}
        {step.id === "welcome" ? null : <>
          <div className="setup-step-heading">
            <h1>{step.title}</h1>
            <p>{step.lead}</p>
          </div>
        </>}
      </header>

      {step.id === "welcome" ? <div className="setup-welcome">
        <FlipparrMark size={176} decorative />
        {/* The action belongs to the copy: beside the artwork it follows the
            sentence it answers, and on a phone it lands on the card floor,
            where Back and Continue sit on every step after this one. */}
        <div className="setup-welcome-copy">
          <h1>Set up your comic library</h1>
          <p>Flipparr reads the comics on your disk, identifies them, and shows what is missing.</p>
          <div className="setup-welcome-actions">
            <button type="button" className="setup-pill primary" onClick={() => setStepIndex(1)}>Quick setup</button>
          </div>
        </div>
      </div> : null}

      {step.id === "library" ? <SetupLibraryStep
        folder={folder} recursive={recursive} existingRoots={existingRoots}
        onFolderChange={setFolder} onCheckedChange={setFolderChecked}
        onRecursiveChange={setRecursive} /> : null}

      {step.id === "acquisition" ? <div className="setup-step-body">
        {services.map((service) => <AcquisitionService service={service} concise onConfigure={() => setEditingService(service)} key={service.id} />)}
      </div> : null}

      {step.id === "metadata" ? <div className="setup-step-body">
        {/* Sources you can act on come first. The built-in ones have no button
            and nothing to decide, so they are one informational line at the
            bottom rather than two cards competing with the real choices. */}
        {providers.filter((provider) => !provider.builtIn).map((provider) => <Provider provider={provider} concise onConfigure={() => setEditingProvider(provider)} key={provider.id} />)}
        {providers.some((provider) => provider.builtIn) ? <p className="setup-builtin">
          <CheckCircle size={16} weight="fill" />
          <span>Already on, no account needed: {providers.filter((provider) => provider.builtIn).map((provider) => provider.name).join(" and ")}.</span>
        </p> : null}
        <aside className="setup-aside">
          <ClockCounterClockwise size={18} weight="fill" />
          <span>On a large library, a second source is the difference between a few minutes and most of an afternoon.</span>
        </aside>
      </div> : null}

      {error ? <p className="setup-check-error" role="alert"><WarningCircle size={18} weight="fill" /> {error}</p> : null}

      {step.id === "welcome" ? null : <footer className="setup-actions">
        <button type="button" className="setup-back" onClick={() => setStepIndex(stepIndex - 1)} disabled={finishing} aria-label="Back"><ArrowLeft size={18} /></button>
        {stepIndex < SETUP_STEPS.length - 1
          ? <button type="button" className="setup-pill primary setup-continue" disabled={step.id === "library" && !canLeaveLibraryStep} onClick={() => setStepIndex(stepIndex + 1)}>Continue</button>
          : <button type="button" className="setup-pill primary setup-continue" onClick={finish} disabled={finishing} aria-busy={finishing}>{finishing ? <LoadingSpinner size={18} /> : <CheckCircle size={18} weight="fill" />} Finish and scan</button>}
      </footer>}
    </div>
    {editingService ? <AcquisitionServiceSettingsModal service={editingService} onClose={() => setEditingService(null)} onSaved={async () => { await loadServices(); setEditingService(null); }} /> : null}
    {editingProvider ? <ProviderSettingsModal provider={editingProvider} onClose={() => setEditingProvider(null)} onSaved={async () => { await loadProviders(); setEditingProvider(null); }} /> : null}
  </div>;
}

function SettingsView({ catalog, onNavigate, onAuthChanged, onSignOut, section, onSectionChange }) {
  const [collectedEditions, setCollectedEditions] = useState(false);
  const [savingCollectedEditions, setSavingCollectedEditions] = useState(false);
  const [language, setLanguage] = useState("en");
  const [savingLanguage, setSavingLanguage] = useState(false);
  const [providers, setProviders] = useState([]);
  const [providerError, setProviderError] = useState("");
  const [editingProvider, setEditingProvider] = useState(null);
  const [services, setServices] = useState([]);
  const [serviceError, setServiceError] = useState("");
  const [editingService, setEditingService] = useState(null);
  async function loadProviders() {
    try {
      const result = await apiRequest("/api/v1/providers");
      setProviders(result.providers || []);
      setProviderError("");
    } catch (error) {
      setProviderError(error.message);
    }
  }
  async function loadServices() {
    try {
      const result = await apiRequest("/api/v1/acquisition-services");
      setServices(result.services || []);
      setServiceError("");
    } catch (error) {
      setServiceError(error.message);
    }
  }
  async function loadAppSettings() {
    try {
      const result = await apiRequest("/api/v1/settings");
      setCollectedEditions(Boolean(result?.collectedEditionsEnabled));
      setLanguage(result?.preferredLanguage ?? "en");
    } catch (error) {
      /* settings fall back to defaults */
    }
  }
  async function toggleCollectedEditions(next) {
    setSavingCollectedEditions(true);
    setCollectedEditions(next);
    try {
      const result = await apiRequest("/api/v1/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ collectedEditionsEnabled: next }),
      });
      setCollectedEditions(Boolean(result?.collectedEditionsEnabled));
    } catch (error) {
      setCollectedEditions(!next);
    } finally {
      setSavingCollectedEditions(false);
    }
  }
  async function changeLanguage(next) {
    const previous = language;
    setSavingLanguage(true);
    setLanguage(next);
    try {
      const result = await apiRequest("/api/v1/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preferredLanguage: next }),
      });
      setLanguage(result?.preferredLanguage ?? next);
    } catch (error) {
      setLanguage(previous);
    } finally {
      setSavingLanguage(false);
    }
  }
  useEffect(() => { loadProviders(); loadServices(); loadAppSettings(); }, []);
  const roots = catalog?.roots || [];
  return <><PageHeader title="Settings" description="Configure your library folders, matching, metadata sources, indexer search, and download client." /><div className="settings-switcher"><label className="settings-switcher-select"><span className="sr-only">Settings section</span><select value={section} onChange={(event) => onSectionChange(event.target.value)} aria-label="Settings section">{SETTINGS_SECTIONS.map((item) => <option value={item.id} key={item.id}>{item.label}</option>)}</select></label><div className="settings-tabs" role="tablist" aria-label="Settings section">{SETTINGS_SECTIONS.map((item) => <button type="button" role="tab" aria-selected={section === item.id} className={section === item.id ? "active" : ""} onClick={() => onSectionChange(item.id)} key={item.id}>{item.label}</button>)}</div></div><div className="settings-layout">{section === "library" ? <section className="settings-library-folders"><header><div><h2>Library folders</h2><p>{roots.length ? `${roots.length} folder${roots.length === 1 ? "" : "s"} currently scanned for comics.` : "No library folders are configured yet."}</p></div><button className="secondary-button" onClick={() => onNavigate("import")}><FolderOpen size={18} /> Manage folders</button></header>{roots.length ? <div className="settings-root-list">{roots.map((root) => <span key={root.id}><FolderOpen size={17} /><strong>{root.path}</strong><small>{root.recursive ? "Includes subfolders" : "Top level only"}</small></span>)}</div> : null}</section> : null}{section === "matching" ? <section><h2>Matching and fixes</h2><aside className="provider-policy-note"><ShieldCheck size={19} weight="fill" /><span><strong>Matches are accepted automatically when the evidence is strong</strong><small>Flipparr scores every match from corroborating and conflicting evidence (filename, embedded metadata, provider agreement). Confident matches are applied without review; anything below that threshold, or with conflicting evidence, waits under Library health for you to confirm or fix.</small></span></aside><Toggle checked={collectedEditions} onChange={savingCollectedEditions ? () => {} : toggleCollectedEditions} title="Collected editions (trades, hardcovers, omnibuses)" description="Off by default. Turn on to browse and manage collected editions alongside Issues. Their metadata and file availability are less complete than Issues, and they are never used to fulfill Issue ownership or acquisition." /></section> : null}{section === "security" ? <SecuritySettings onChanged={onAuthChanged} onSignOut={onSignOut} /> : null}{section === "acquisition" ? <section className="metadata-source-settings acquisition-source-settings"><header><div><h2>Acquisition services</h2><p>Connect Prowlarr to find releases and SABnzbd to download the one you choose.</p></div></header><label className="form-field settings-language"><span>Language wanted</span><select value={language} onChange={(event) => changeLanguage(event.target.value)} disabled={savingLanguage}><option value="en">English</option><option value="fr">French</option><option value="es">Spanish</option><option value="de">German</option><option value="it">Italian</option><option value="pt">Portuguese</option><option value="ru">Russian</option><option value="ja">Japanese</option><option value="ko">Korean</option><option value="zh">Chinese</option><option value="pl">Polish</option><option value="nl">Dutch</option><option value="">No preference</option></select><small>A release that says it is another language is never grabbed, and one that says so only once downloaded is refused instead of filed under the issue it claims to be. Releases that say nothing are judged on the rest of the evidence.</small></label>{services.map((service) => <AcquisitionService service={service} onConfigure={() => setEditingService(service)} key={service.id} />)}{serviceError ? <p className="workbench-error" role="alert">{serviceError}</p> : null}</section> : null}{section === "metadata" ? <section className="metadata-source-settings"><header><div><h2>Metadata sources</h2><p>Built-in sources work immediately. Add API credentials for more issue titles, dates, covers, and matches.</p></div></header>{providers.map((provider) => <Provider provider={provider} onConfigure={() => setEditingProvider(provider)} key={provider.id} />)}{providerError ? <p className="workbench-error" role="alert">{providerError}</p> : null}<aside className="provider-policy-note"><ShieldCheck size={19} weight="fill" /><span><strong>Your API credentials stay on this device</strong><small>Keys are hidden after saving and sent only to the service you configure.</small></span></aside></section> : null}</div>{editingProvider ? <ProviderSettingsModal provider={editingProvider} onClose={() => setEditingProvider(null)} onSaved={async () => { await loadProviders(); setEditingProvider(null); }} /> : null}{editingService ? <AcquisitionServiceSettingsModal service={editingService} onClose={() => setEditingService(null)} onSaved={async () => { await loadServices(); setEditingService(null); }} /> : null}</>;
}

function SecuritySettings({ onChanged, onSignOut }) {
  const [config, setConfig] = useState(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  async function load() {
    try {
      const result = await apiRequest("/api/v1/auth");
      setConfig(result);
      setUsername(result.username || "");
    } catch (err) {
      setError(err.message);
    }
  }
  useEffect(() => { load(); }, []);
  async function save(patch) {
    setBusy(true);
    setError("");
    setSaved("");
    try {
      const result = await apiRequest("/api/v1/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      setConfig(result);
      setPassword("");
      setSaved("Saved");
      // Keep the shell's auth gate in step with what was just saved.
      if (onChanged) await onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  if (!config) return null;
  const on = config.method === "forms";
  return <section className="metadata-source-settings">
    <header><div><h2>Security</h2><p>This app stores your metadata and download-client API keys and can start downloads, so require a sign-in if anything other than you can reach it.</p></div></header>
    <Toggle
      checked={on}
      onChange={(next) => next
        ? (config.configured || password
            ? save({ method: "forms", username, ...(password ? { password } : {}) })
            : setError("Set a username and password below first."))
        : save({ method: "none" })}
      title="Require sign-in"
      description="Off by default. When on, the API and library views need a session; the sign-in page itself and the container health check stay reachable."
    />
    <Toggle
      checked={Boolean(config.localBypass)}
      onChange={(next) => save({ localBypass: next })}
      title="Skip sign-in on local addresses"
      description="Off by default. Anything reaching this app through a tunnel, reverse proxy or container bridge arrives from a private address and would be let straight in — so turn this on only if you know the traffic is genuinely local, and set FLIPPARR_TRUSTED_PROXIES when a proxy is in front."
    />
    <div className="auth-credentials">
      <label className="form-field"><span>Username</span>
        <input value={username} autoComplete="username" onChange={(event) => setUsername(event.target.value)} />
      </label>
      <label className="form-field"><span>{config.configured ? "New password" : "Password"}</span>
        <input type="password" value={password} autoComplete="new-password" placeholder={config.configured ? "Leave blank to keep the current password" : "At least 8 characters"} onChange={(event) => setPassword(event.target.value)} />
      </label>
      <button className="secondary-button" disabled={busy || !username || (!config.configured && !password)}
        onClick={() => save({ method: config.method, username, ...(password ? { password } : {}) })} aria-busy={busy}>
        {busy ? <LoadingSpinner size={17} /> : <ShieldCheck size={17} />} Save credentials
      </button>
      {error ? <p className="workbench-error" role="alert">{error}</p> : null}
      {saved ? <small className="auth-saved">{saved}</small> : null}
    </div>
    <aside className="provider-policy-note"><ShieldCheck size={19} weight="fill" /><span><strong>Your password is stored as a scrypt hash</strong><small>It is never returned by the API, and signing in sets an HttpOnly cookie rather than exposing a token to page scripts.</small></span></aside>
    {on && onSignOut ? <button type="button" className="secondary-button" onClick={onSignOut}><SignOut size={17} /> Sign out of this device</button> : null}
  </section>;
}

function Toggle({ checked, onChange, title, description }) {
  return <label className="toggle-row"><span><strong>{title}</strong><small>{description}</small></span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
}

function Provider({ provider, onConfigure, concise = false }) {
  const status = provider.builtIn ? "Available without an account" : provider.enabled ? "Enabled" : provider.configured ? "Configured but disabled" : "Not configured";
  // In setup nothing is set up yet, so "Not configured" reads identically on
  // every card while the Configure button beside it already says as much. Show
  // the pill only once it reports something the button does not.
  const showState = !concise || provider.builtIn || provider.configured || provider.enabled;
  return <div className={`provider-row ${provider.enabled ? "enabled" : ""}`}><Database size={23} weight="duotone" /><span><span className="provider-title-line"><strong>{provider.name}</strong>{showState ? <b className={`provider-state ${provider.enabled ? "connected" : provider.configured ? "paused" : "optional"}`}>{status}</b> : null}</span><small>{concise ? provider.setupSummary || provider.description : provider.description}</small><em>{provider.capabilities.join(" · ")}</em></span>{provider.builtIn ? <b className="provider-priority">Priority {provider.priority}</b> : <button onClick={onConfigure}>{provider.configured ? "Manage" : "Configure"}</button>}</div>;
}

function AcquisitionService({ service, onConfigure, concise = false }) {
  const status = service.enabled ? "Ready" : service.configured ? "Configured but disabled" : "Not connected";
  const Icon = service.id === "prowlarr" ? MagnifyingGlass : CloudArrowDown;
  const showState = !concise || service.configured || service.enabled;
  return <div className={`provider-row ${service.enabled ? "enabled" : ""}`}><Icon size={23} weight="duotone" /><span><span className="provider-title-line"><strong>{service.name}</strong>{showState ? <b className={`provider-state ${service.enabled ? "connected" : service.configured ? "paused" : "optional"}`}>{status}</b> : null}</span><small>{concise ? service.setupSummary || service.description : service.description}</small><em>{service.kind} · {service.capabilities.join(" · ")}</em></span><button onClick={onConfigure}>{service.configured ? "Manage" : "Connect"}</button></div>;
}

function AcquisitionServiceSettingsModal({ service, onClose, onSaved }) {
  const dialogRef = useDialog(onClose);
  const [url, setUrl] = useState(service.url || "");
  const [apiKey, setApiKey] = useState("");
  const [category, setCategory] = useState(service.category || "comics");
  const [enabled, setEnabled] = useState(service.enabled || !service.configured);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const payload = () => ({ url, ...(apiKey ? { apiKey } : {}), ...(service.id === "sabnzbd" ? { category } : {}) });
  async function test() {
    setBusy("test"); setError(""); setResult("");
    try {
      const response = await apiRequest(`/api/v1/acquisition-services/${service.id}/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload()) });
      setResult(response.detail);
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(""); }
  }
  async function save(event) {
    event.preventDefault(); setBusy("save"); setError("");
    try {
      await apiRequest(`/api/v1/acquisition-services/${service.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...payload(), enabled }) });
      await onSaved();
    } catch (requestError) { setError(requestError.message); setBusy(""); }
  }
  async function disconnect() {
    setBusy("remove"); setError("");
    try {
      await apiRequest(`/api/v1/acquisition-services/${service.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ clearCredentials: true, enabled: false }) });
      await onSaved();
    } catch (requestError) { setError(requestError.message); setBusy(""); }
  }
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal provider-settings-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="acquisition-service-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close acquisition service settings"><X size={20} /></button><span className="eyebrow">{service.kind}</span><h2 id="acquisition-service-title">Connect {service.name}</h2><p className="workbench-intro">{service.description}</p><form onSubmit={save}><label className="form-field"><span>Server URL</span><input value={url} onChange={(event) => setUrl(event.target.value)} placeholder={service.id === "prowlarr" ? "http://nas:9696" : "http://nas:8080"} required /></label><label className="form-field"><span>API key</span><input type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={service.configured ? "Saved locally · enter a new key to replace it" : `Enter your ${service.name} API key…`} /></label>{service.id === "sabnzbd" ? <label className="form-field"><span>SABnzbd category</span><input value={category} onChange={(event) => setCategory(event.target.value)} placeholder="comics" required /><small>Flipparr will use this category to identify and monitor its downloads.</small></label> : null}<Toggle checked={enabled} onChange={setEnabled} title={`Use ${service.name}`} description={service.id === "prowlarr" ? "Search configured Usenet indexers for wanted comics." : "Send selected NZBs to SABnzbd and monitor their progress."} />{result ? <p className="provider-test-result"><CheckCircle size={17} weight="fill" /> {result}</p> : null}{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="provider-modal-actions"><button type="button" className="secondary-button" onClick={test} disabled={Boolean(busy) || !url.trim() || (!apiKey && !service.configured)}>{busy === "test" ? <><LoadingSpinner size={17} /> Testing…</> : "Test"}</button><span />{service.configured ? <button type="button" className="danger-button" onClick={disconnect} disabled={Boolean(busy)}>Disconnect</button> : null}<button className="primary-button" disabled={Boolean(busy) || !url.trim() || (!apiKey && !service.configured)}>{busy === "save" ? <><LoadingSpinner size={17} /> Saving…</> : "Save"}</button></div><small className="provider-credential-help">The API key is stored locally and is never returned to the browser after saving.</small></form></section></div>;
}

function ProviderSettingsModal({ provider, onClose, onSaved }) {
  const dialogRef = useDialog(onClose);
  const field = provider.credentialField;
  const [credential, setCredential] = useState("");
  const [enabled, setEnabled] = useState(provider.enabled || !provider.configured);
  const [priority, setPriority] = useState(provider.priority || 20);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const label = provider.id === "metron" ? "API token" : "API key";
  async function test() {
    setBusy("test"); setError(""); setResult("");
    try {
      const payload = credential ? { [field]: credential } : {};
      const response = await apiRequest(`/api/v1/providers/${provider.id}/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      setResult(response.detail);
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(""); }
  }
  async function save(event) {
    event.preventDefault();
    setBusy("save"); setError("");
    try {
      const payload = { enabled, priority, ...(credential ? { [field]: credential } : {}) };
      await apiRequest(`/api/v1/providers/${provider.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      await onSaved();
    } catch (requestError) { setError(requestError.message); setBusy(""); }
  }
  async function removeCredentials() {
    setBusy("remove"); setError("");
    try {
      await apiRequest(`/api/v1/providers/${provider.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ clearCredentials: true, enabled: false, priority }) });
      await onSaved();
    } catch (requestError) { setError(requestError.message); setBusy(""); }
  }
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal provider-settings-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="provider-settings-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close provider settings"><X size={20} /></button><span className="eyebrow">Metadata provider</span><h2 id="provider-settings-title">Connect {provider.name}</h2><p className="workbench-intro">{provider.description}</p><form onSubmit={save}><label className="form-field"><span>{label}</span><input type="password" autoComplete="off" value={credential} onChange={(event) => setCredential(event.target.value)} placeholder={provider.configured ? "Saved locally · enter a new value to replace it" : `Enter your ${provider.name} ${label.toLowerCase()}…`} /></label><small className="provider-credential-help">{provider.credentialHelp || "Your key is stored locally and is never returned to the browser after saving."}{provider.credentialUrl ? <> <a className="provider-credential-link" href={provider.credentialUrl} target="_blank" rel="noreferrer noopener">Get a key<ArrowUpRight size={13} weight="bold" /></a></> : null}</small><Toggle checked={enabled} onChange={setEnabled} title={`Use ${provider.name} for enrichment`} description="Fill missing fields automatically while preserving locked local corrections and higher-priority source data." /><label className="form-field provider-priority-field"><span>Provider priority</span><select value={priority} onChange={(event) => setPriority(Number(event.target.value))}><option value="15">Before other optional providers</option><option value="20">Normal priority</option><option value="30">Fallback priority</option></select><small>Built-in GCD structure remains first. Optional providers fill fields that are still missing.</small></label>{result ? <p className="provider-test-result"><CheckCircle size={17} weight="fill" /> {result}</p> : null}{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="provider-modal-actions"><button type="button" className="secondary-button" onClick={test} disabled={Boolean(busy) || (!credential && !provider.configured)}>{busy === "test" ? <><LoadingSpinner size={17} /> Testing…</> : "Test"}</button><span />{provider.configured ? <button type="button" className="danger-button" onClick={removeCredentials} disabled={Boolean(busy)}>Remove</button> : null}<button className="primary-button" disabled={Boolean(busy) || (!credential && !provider.configured)}>{busy === "save" ? <><LoadingSpinner size={17} /> Saving…</> : "Save"}</button></div></form></section></div>;
}

function GroupedIssueInventory({ issues, onEditIssue }) {
  if (!issues?.length) return <div className="drawer-empty"><BookOpen size={26} weight="duotone" /><strong>No issue list linked yet</strong><span>Flipparr will add series runs and their issues automatically when a match is found.</span></div>;
  const groups = [];
  const byRun = new Map();
  for (const issue of issues) {
    const key = String(issue.contextRunId || issue.contextLabel || "unknown");
    if (!byRun.has(key)) {
      const group = {
        key,
        title: issue.contextGroupName || issue.contextLabel || "Unknown run",
        runTitle: issue.contextLabel,
        year: issue.contextYear,
        type: issue.contextType || "main",
        issues: [],
      };
      byRun.set(key, group);
      groups.push(group);
    }
    byRun.get(key).issues.push(issue);
  }
  return <div className="grouped-issue-inventory">{groups.map((group) => {
    const owned = group.issues.filter((issue) => issue.ownership !== "unowned").length;
    return <section className="issue-run-group" key={group.key}>
      <header><div><span>{group.type === "specials" ? "Special / one-shot" : "Series run"}</span><h3>{group.title}</h3>{group.runTitle !== group.title || group.year ? <small>{[group.runTitle !== group.title ? group.runTitle : null, group.year].filter(Boolean).join(" · ")}</small> : null}</div><strong>{owned} of {group.issues.length} owned</strong></header>
      <div>{group.issues.map((issue) => {
        const genericTitle = !issue.title || identityKey(issue.title) === identityKey(`Issue ${issue.number}`);
        const releaseLabel = issue.publicationDate
          ? new Date(`${issue.publicationDate}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
          : issue.publicationYear ? `Published ${issue.publicationYear}` : "Release date unknown";
        const stateLabel = issue.ownership === "both" ? "Single + volume"
          : issue.ownership === "direct" ? "Single issue"
          : issue.ownership === "collection" ? "In volume"
          : issue.releaseState === "upcoming" ? "Upcoming"
          : issue.releaseState === "unknown" ? "Date needed" : "Missing";
        return <article key={issue.id}><span className={`grouped-issue-cover ${issue.fileCover ? "from-file" : ""}`}><CoverArt id={`issue-${issue.id}`} title={`${issue.contextLabel || "Issue"} #${issue.number}`} cover={issue.fileCover || issue.cover} decorative placeholderSize={16} /></span><span className="grouped-issue-number">#{issue.number}</span><div>{!genericTitle ? <strong>{issue.title}{issue.metadataLocked ? <ShieldCheck className="issue-local-lock" size={13} weight="fill" aria-label="Local metadata correction locked" /> : null}</strong> : null}<small>{releaseLabel}</small></div><div className="issue-row-actions"><span className={`ownership-source ${issue.ownership} ${issue.acquisitionState || ""}`}>{stateLabel}</span>{onEditIssue ? <button type="button" onClick={() => onEditIssue(issue)} aria-label={`Edit metadata for ${issue.contextLabel || "issue"} issue ${issue.number}`} title="Edit issue metadata"><PencilSimple size={14} /></button> : null}</div></article>;
      })}</div>
    </section>;
  })}</div>;
}

function VolumeInventory({ editions }) {
  if (!editions?.length) return <div className="drawer-empty"><Books size={26} weight="duotone" /><strong>No volumes linked yet</strong><span>Collected files will appear here after inventory.</span></div>;
  return <div className="edition-inventory">{editions.map((edition) => { const title = edition.subtitle ? `${edition.title}: ${edition.subtitle}` : edition.title; return <article key={edition.logicalVolumeKey || edition.id}><span className="edition-cover"><CoverArt id={`edition-${edition.id}`} title={title} cover={edition.cover} decorative placeholderSize={20} /></span><div className="edition-card-content"><header><div><strong>{title}</strong><small>{[edition.publisher, edition.publicationYear, edition.format].filter(Boolean).join(" · ") || "Volume details incomplete"}</small></div><span><b>{editionKindLabel(edition.editionKind)}{edition.volume ? ` · Vol. ${edition.volume}` : ""}</b><small>{edition.copyCount > 1 ? `${edition.copyCount} library files` : edition.source || "Local metadata"}</small>{edition.coverageOverrideCount ? <small>{edition.coverageOverrideCount} local contents correction{edition.coverageOverrideCount === 1 ? "" : "s"}</small> : null}</span></header>{edition.isbns?.length ? <p><b>ISBN</b> {edition.isbns.join(", ")}</p> : null}<div className="coverage-groups">{edition.coverageGroups?.length ? edition.coverageGroups.map((coverage) => <div className={coverage.resolved ? "resolved" : "unresolved"} key={`${coverage.seriesLabel}-${coverage.issueLabel}-${coverage.source}`}><span><strong>{coverage.seriesLabel} #{coverage.issueLabel}</strong><small>{coverage.resolved ? "Counts toward canonical ownership" : "Visible claim; canonical numbering unresolved"}</small></span><b>{coverage.confidence}</b><p>{coverage.source}{coverage.evidence ? ` · ${volumeTerminology(coverage.evidence)}` : ""}</p></div>) : <div className="no-coverage"><WarningCircle size={18} /><span><strong>Contents not established</strong><small>{volumeTerminology(edition.coverageStatus) || "No structured issue coverage was returned by the current sources."}</small></span></div>}</div></div></article>; })}</div>;
}

function FileActionButtons({ file, onOpenWorkbench, onOpenCover, onOpenContents, onChangeRun, onReplace }) {
  const editionsOn = useCollectedEditions();
  // Editing a collected edition's issue contents is an edition-management
  // surface; the file itself stays visible and fixable either way.
  return <>{file.identityKind === "edition" && editionsOn ? <button onClick={() => onOpenContents(file)}><ListBullets size={14} /> Issues</button> : null}<button onClick={() => onReplace(file)}><CloudArrowDown size={14} /> Replace</button><button onClick={() => onChangeRun(file)}><Books size={14} /> Change run</button><button onClick={() => onOpenCover(file)}><BookOpen size={14} /> Cover</button><button onClick={() => onOpenWorkbench(file, "match")}><ArrowsClockwise size={14} /> Fix match</button><button onClick={() => onOpenWorkbench(file, "edit")}><PencilSimple size={14} /> Metadata</button></>;
}

function FileInventory({ files, onOpenWorkbench, onOpenCover, onOpenContents, onChangeRun, onReplace }) {
  if (!files?.length) return <div className="drawer-empty"><HardDrive size={26} weight="duotone" /><strong>No local files linked</strong></div>;
  return <div className="file-inventory">{files.map((file) => <article key={file.path}><HardDrive size={20} weight="duotone" /><div><strong>{file.filename}</strong><small>{file.identityKind === "issue" ? "Single issue" : editionKindLabel(file.editionKind)} · {(file.sizeBytes / 1024 / 1024).toFixed(1)} MB</small>{file.metadataLocked ? <small className="metadata-lock"><ShieldCheck size={13} weight="fill" /> Local corrections locked</small> : null}</div><span className="file-row-actions"><FileActionButtons file={file} onOpenWorkbench={onOpenWorkbench} onOpenCover={onOpenCover} onOpenContents={onOpenContents} onChangeRun={onChangeRun} onReplace={onReplace} /></span><details className="file-actions-menu"><summary><DotsThree size={17} weight="bold" /> Actions <CaretDown size={13} /></summary><div><FileActionButtons file={file} onOpenWorkbench={onOpenWorkbench} onOpenCover={onOpenCover} onOpenContents={onOpenContents} onChangeRun={onChangeRun} onReplace={onReplace} /></div></details></article>)}</div>;
}

function IssueCatalogCard({ series, catalogKnown, syncing, error, lastResult, onSync, onReviewFiles, onFindRun }) {
  const catalog = series.issueCatalog || { status: "unknown", syncReady: false };
  const collectionIssueCount = series.issues?.filter((issue) => issue.collectionOwned).length || 0;
  const directFiles = series.inventory?.directIssueFiles || 0;
  const editionCount = series.inventory?.editionCount || 0;
  const issues = series.issues || [];
  const numberedOnly = catalog.titlePolicy === "numbered_only";
  const missingTitleIssues = numberedOnly ? [] : issues.filter((issue) => !String(issue.title || "").trim());
  const upcomingUntitled = missingTitleIssues.filter((issue) => issue.releaseState === "upcoming");
  const repairableTitles = missingTitleIssues.filter((issue) => issue.releaseState !== "upcoming");
  const missingDateIssues = issues.filter((issue) => issue.publicationYear == null);
  const repairableFields = repairableTitles.length + missingDateIssues.length;
  const resultMetadata = lastResult?.metadata;
  const repairedFields = (resultMetadata?.repairedTitleCount || 0) + (resultMetadata?.repairedDateCount || 0);
  const providerErrors = lastResult?.providerErrors || [];
  const checkedAt = catalog.lastSyncedAt ? new Date(catalog.lastSyncedAt).toLocaleString() : null;
  if (catalogKnown) {
    let tone = repairableFields ? "repairing" : "";
    let heading = "Issue details complete";
    let outcome = `No missing issue titles or release dates across ${catalog.issueCount || issues.length} issues.`;
    if (syncing) {
      heading = "Checking metadata sources";
      outcome = "Looking for newly available issue titles and release dates.";
    } else if (error || catalog.error) {
      tone = "error";
      heading = "Metadata check couldn’t finish";
      outcome = "Existing issue information is unchanged. You can try again without losing local corrections.";
    } else if (repairableFields) {
      heading = `${repairableFields} issue detail${repairableFields === 1 ? " is" : "s are"} still unavailable`;
      const parts = [];
      if (repairableTitles.length) parts.push(`${repairableTitles.length} missing title${repairableTitles.length === 1 ? "" : "s"}`);
      if (missingDateIssues.length) parts.push(`${missingDateIssues.length} missing release date${missingDateIssues.length === 1 ? "" : "s"}`);
      outcome = `${parts.join(" and ")}. Enabled providers were checked but do not currently supply ${repairableFields === 1 ? "this field" : "these fields"}.`;
    } else if (numberedOnly) {
      heading = "Issue metadata current";
      outcome = "This run is listed by issue number. The matched sources do not publish separate issue titles.";
    } else if (upcomingUntitled.length) {
      heading = "Metadata current";
      const labels = upcomingUntitled.map((issue) => `#${issue.number}`).join(", ");
      outcome = `${labels} ${upcomingUntitled.length === 1 ? "is" : "are"} upcoming; ${upcomingUntitled.length === 1 ? "its title has" : "their titles have"} not been announced yet.`;
    }
    return <section className={`issue-catalog-card synced ${tone}`}><div className="issue-catalog-outcome"><strong>{heading}</strong><small>{outcome}</small>{repairedFields ? <em><CheckCircle size={14} weight="fill" /> {repairedFields} field{repairedFields === 1 ? "" : "s"} filled during the latest check.</em> : resultMetadata && !syncing ? <em>No additional fields were available during the latest check.</em> : null}<details><summary>View metadata details</summary><div className="issue-catalog-details"><p>{catalog.detail || `The issue list is linked through ${catalog.provider?.toUpperCase() || "the metadata provider"}.`}</p>{checkedAt ? <time>Last checked {checkedAt}</time> : null}{providerErrors.length ? <ul>{providerErrors.map((item) => <li key={item.provider}><b>{item.provider}</b>: {item.error}</li>)}</ul> : null}<p>Local corrections remain locked and are never replaced by provider refreshes.</p></div></details></div><button onClick={onSync} disabled={syncing}>{syncing ? <LoadingSpinner size={17} /> : <ArrowsClockwise size={17} />} {syncing ? "Checking…" : "Refresh details"}</button>{error || catalog.error ? <p>{error || catalog.error}</p> : null}</section>;
  }
  if (catalog.syncReady) {
    const providerNames = (catalog.anchorProviders || []).map((provider) => ({ gcd: "GCD", metron: "Metron", comic_vine: "Comic Vine" }[provider] || provider));
    const providerLabel = providerNames.length ? providerNames.join(" and ") : "a metadata service";
    return <section className="issue-catalog-card ready"><div><strong>Complete issue list is ready to load</strong><small>Your confirmed issue match is linked through {providerLabel}. Loading the run will add complete issue numbering and improve ownership totals.</small></div><button onClick={onSync} disabled={syncing} aria-busy={syncing}>{syncing ? <LoadingSpinner size={17} /> : <CloudArrowDown size={17} />} Load issue list</button>{error || catalog.error ? <p>{error || catalog.error}</p> : null}</section>;
  }
  if (editionCount > 0 && directFiles === 0) return <section className="issue-catalog-card pending"><div><strong>Find the full series</strong><small>We can see {collectionIssueCount} issue{collectionIssueCount === 1 ? "" : "s"} covered by your volumes. Flipparr can find the complete issue list, related miniseries, and specials automatically.</small><em>You choose how to acquire gaps; provider-run maintenance stays automatic.</em></div><button onClick={() => onFindRun(series)}><MagnifyingGlass size={17} /> Find full series</button></section>;
  if (directFiles > 0) return <section className="issue-catalog-card pending"><div><strong>Confirm one issue match to load the full series</strong><small>Your issue files are in the library, but none is linked to a verified external issue yet. Review one file and use Fix Match to choose the correct result.</small></div><button onClick={onReviewFiles}><Eye size={17} /> Review issue files</button></section>;
  return <section className="issue-catalog-card pending"><div><strong>Full issue list not available</strong><small>No verified issue-run evidence is available yet. Your existing library records are still safe and usable.</small><em>No action is required.</em></div></section>;
}

function CollectionManagement({ series, families, allSeries, onCreateFamily, onSetFamily }) {
  const [name, setName] = useState("");
  const [targetFamily, setTargetFamily] = useState("");
  const [candidateRun, setCandidateRun] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const family = families.find((item) => item.id === series.family?.id);
  const familyRunIds = new Set(family?.runIds || []);
  const otherFamilies = families.filter((item) => item.id !== family?.id);
  const availableRuns = allSeries.filter((item) => !familyRunIds.has(item.id));
  async function create(event) {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true); setError("");
    const result = await onCreateFamily(name.trim(), [series.id]);
    if (result.ok) setName(""); else setError(result.error);
    setBusy(false);
  }
  async function assign(runId, familyId) {
    setBusy(true); setError("");
    const result = await onSetFamily(runId, familyId || null);
    if (!result.ok) setError(result.error);
    setBusy(false);
  }
  return <div className="family-management">
    <section className={`current-family ${family ? "linked" : ""}`}><Books size={24} weight="duotone" /><div><span>{family ? "Collection" : "Independent publication run"}</span><strong>{family?.name || series.title}</strong><small>{family ? `${family.runCount} runs are grouped in this collection. Each run keeps its own numbering and metadata.` : "This run is currently shown independently. It can be added to a collection without merging its identity."}</small></div></section>
    {family ? <section className="family-members"><header><strong>Publication runs</strong><small>Remove any run to show it independently again.</small></header>{family.runs.map((run) => <article key={run.id}><div><strong>{run.title}</strong><small>{run.year} · {run.total} issue{run.total === 1 ? "" : "s"}</small></div><button disabled={busy} onClick={() => assign(run.id, null)}>Remove</button></article>)}</section> : null}
    {family && availableRuns.length ? <section className="family-assignment"><h3>Add or move a run into this collection</h3><div><select value={candidateRun} onChange={(event) => setCandidateRun(event.target.value)}><option value="">Choose a run…</option>{availableRuns.map((run) => <option value={run.id} key={run.id}>{run.title} ({run.year}){run.family ? ` · from ${run.family.name}` : ""}</option>)}</select><button disabled={busy || !candidateRun} onClick={() => assign(candidateRun, family.id)}><Plus size={17} /> Add run</button></div><small>Moving a run changes only its collection membership.</small></section> : null}
    {otherFamilies.length ? <section className="family-assignment"><h3>{family ? "Move this run to another collection" : "Add this run to an existing collection"}</h3><div><select value={targetFamily} onChange={(event) => setTargetFamily(event.target.value)}><option value="">Choose a collection…</option>{otherFamilies.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.runCount} run{item.runCount === 1 ? "" : "s"}</option>)}</select><button disabled={busy || !targetFamily} onClick={() => assign(series.id, targetFamily)}>{family ? "Move run" : "Join collection"}</button></div></section> : null}
    <form className="family-create-form" onSubmit={create}><h3>Create a new collection from this run</h3><p>The current run will move into the new collection. Files, issues, volumes, aliases, and provider matches stay on the run.</p><div><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Collection name…" /><button disabled={busy || !name.trim()}><Plus size={17} /> Create collection</button></div></form>
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
  </div>;
}

function SeriesDrawer({ series, families, allSeries, parentCollection, dismissSignal, onBack, onClose, onRequest, onViewRequests, requestBusy, onAddAlias, onSyncIssues, onFindRun, onMergeRun, onRebuildRun, rebuilding = false, rebuildResult = "", onCreateFamily, onSetFamily, onOpenWorkbench, onOpenCover, onOpenContents, onChangeRun, onEditIssue, onReplace }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  const editionsOn = useCollectedEditions();
  useSwipeToDismiss(dialogRef, requestClose);
  useExternalDismiss(dismissSignal, requestClose);
  const [alias, setAlias] = useState("");
  const [savingAlias, setSavingAlias] = useState(false);
  const [aliasError, setAliasError] = useState("");
  const [syncingIssues, setSyncingIssues] = useState(false);
  const [syncError, setSyncError] = useState("");
  const [lastSyncResult, setLastSyncResult] = useState(null);
  const autoRepairKey = useRef(null);
  const [tab, setTab] = useState("overview");
  const issueCatalog = series?.issueCatalog || { status: "unknown" };
  const catalogKnown = ["complete", "complete_to_date"].includes(issueCatalog.status);
  const repairableIssueDetails = (series?.issues || []).filter((issue) => (
    (issueCatalog.titlePolicy !== "numbered_only" && !String(issue.title || "").trim() && issue.releaseState !== "upcoming")
    || issue.publicationYear == null
  )).length;
  const lastCheckedAge = issueCatalog.lastSyncedAt ? Date.now() - Date.parse(issueCatalog.lastSyncedAt) : Infinity;
  const currentRepairKey = `${series?.id || "none"}:${issueCatalog.lastSyncedAt || "never"}`;
  useEffect(() => {
    if (!series || !catalogKnown || !repairableIssueDetails || lastCheckedAge < 6 * 60 * 60 * 1000 || autoRepairKey.current === currentRepairKey) return;
    autoRepairKey.current = currentRepairKey;
    syncIssues(true);
  }, [series?.id, catalogKnown, repairableIssueDetails, currentRepairKey]);
  useEffect(() => {
    // The Volumes tab disappears when collected editions are turned off.
    if (!editionsOn && tab === "editions") setTab("overview");
  }, [editionsOn, tab]);
  if (!series) return null;
  const identityStrength = series.identityConfidence == null ? "Unknown" : series.identityConfidence >= 85 ? "High" : series.identityConfidence >= 65 ? "Medium" : "Low";
  async function saveAlias(event) {
    event.preventDefault();
    if (!alias.trim()) return;
    setSavingAlias(true);
    setAliasError("");
    const result = await onAddAlias(series.id, alias.trim());
    if (result.ok) setAlias("");
    else setAliasError(result.error);
    setSavingAlias(false);
  }
  async function syncIssues(automatic = false) {
    setSyncingIssues(true);
    setSyncError("");
    const result = await onSyncIssues(series.id, automatic);
    if (!result.ok) setSyncError(result.error);
    else setLastSyncResult(result.result);
    setSyncingIssues(false);
  }
  const groupedIssues = (series.issues || []).map((issue) => ({
    ...issue,
    contextRunId: series.id,
    contextLabel: series.title,
    contextYear: series.year,
    contextGroupName: series.title,
    contextType: "main",
  }));
  const isFollowing = series.monitoringStatus === "monitored";
  const wantedIssueCount = Math.max(0, Number(series.releaseSummary?.releasedMissing ?? series.unowned ?? Math.max(0, (series.total || 0) - (series.owned || 0))));
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}><aside className={`series-drawer ${closing ? "closing" : ""}`} ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="series-drawer-title" onMouseDown={(event) => event.stopPropagation()}>{parentCollection ? <button className="drawer-back-link" onClick={onBack}><ArrowLeft size={17} /><span>Back to <strong>{parentCollection.name}</strong></span></button> : null}<button className="modal-close" onClick={requestClose} aria-label="Close series details"><X size={20} /></button><div className="drawer-identity"><div className="drawer-cover"><SeriesCover series={series} /></div><div><h2 id="series-drawer-title">{series.title} <em>({series.year})</em></h2><p>{series.publisher}</p><div className="drawer-statuses"><PublicationStatus series={series} />{isFollowing ? <span className="status-chip green"><CheckCircle size={13} weight="fill" /> Following</span> : null}{series.family ? <button className="family-link-chip" onClick={() => setTab("family")}><Books size={14} /> {series.family.name}</button> : null}</div></div></div><div className="drawer-facts"><span><strong>{series.fileDetails?.length ?? series.owned}</strong>Comic files</span><span><strong>{series.inventory?.directIssueFiles ?? 0}</strong>Single issues</span>{editionsOn ? <span><strong>{series.inventory?.editionCount ?? series.editions?.length ?? 0}</strong>Volumes</span> : null}<span><strong>{identityStrength}</strong>Match confidence</span></div><nav className="drawer-tabs" aria-label="Series details">{[["overview", "Overview"], ["issues", `Issues (${series.issues?.length ?? 0})`], ...(editionsOn ? [["editions", `Volumes (${series.editions?.length ?? 0})`]] : []), ["files", `Files (${series.fileDetails?.length ?? 0})`], ["aliases", "Aliases"], ["family", "Collection"]].map(([id, label]) => <button className={tab === id ? "active" : ""} onClick={() => setTab(id)} key={id}>{label}</button>)}</nav><div className="drawer-tab-content">{tab === "overview" ? <><h3>Your collection</h3><Ownership series={series} /><CollectionCoverage series={series} editionsOn={editionsOn} /><IssueCatalogCard series={series} catalogKnown={catalogKnown} syncing={syncingIssues} error={syncError} lastResult={lastSyncResult} onSync={syncIssues} onReviewFiles={() => setTab("files")} onFindRun={onFindRun} /><details className="advanced-collection-tools"><summary><Gear size={15} /> Advanced tools</summary><p>If one publication run was accidentally split into two entries, preview and combine them without changing files on disk.</p><button onClick={() => onMergeRun(series)}><Books size={16} /> Combine duplicate run</button><p>If this run picked up details from a comic that turned out to be something else, rebuild it: the titles, dates and covers Flipparr worked out are cleared and worked out again from the files here now. Your own corrections are kept, and no file is changed.</p><button onClick={() => onRebuildRun(series)} disabled={rebuilding}>{rebuilding ? <LoadingSpinner size={16} /> : <ArrowsClockwise size={16} />} {rebuilding ? "Rebuilding…" : "Rebuild this run"}</button>{rebuildResult ? <small className="rebuild-result">{rebuildResult}</small> : null}</details></> : null}{tab === "issues" ? <GroupedIssueInventory issues={groupedIssues} onEditIssue={onEditIssue} /> : null}{tab === "editions" && editionsOn ? <VolumeInventory editions={series.editions} /> : null}{tab === "files" ? <FileInventory files={series.fileDetails} onOpenWorkbench={onOpenWorkbench} onOpenCover={onOpenCover} onOpenContents={onOpenContents} onChangeRun={onChangeRun} onReplace={onReplace} /> : null}{tab === "aliases" ? <><div className="alias-list">{series.aliases?.length ? series.aliases.map((item) => <span className={item.confirmed ? "confirmed" : ""} key={`${item.name}-${item.source}`}><strong>{item.name}</strong><small>{item.confirmed ? "Manually confirmed" : item.source}</small></span>) : <p>No alternate titles recorded.</p>}</div><form className="alias-form" onSubmit={saveAlias}><label><span>Add a title alias</span><div><input value={alias} onChange={(event) => setAlias(event.target.value)} placeholder="Alternate series title…" /><button disabled={savingAlias || !alias.trim()} aria-busy={savingAlias}>{savingAlias ? <LoadingSpinner size={18} /> : <Plus size={18} />} Add</button></div></label>{aliasError ? <small className="form-error" role="alert">{aliasError}</small> : <small>Confirmed aliases are used during future scans and searches.</small>}</form></> : null}{tab === "family" ? <CollectionManagement series={series} families={families} allSeries={allSeries} onCreateFamily={onCreateFamily} onSetFamily={onSetFamily} /> : null}</div><div className="drawer-actions"><button className={isFollowing ? "ghost-button" : "primary-button"} disabled={requestBusy || (isFollowing && wantedIssueCount === 0)} onClick={isFollowing ? onViewRequests : onRequest}>{requestBusy ? <LoadingSpinner size={19} /> : isFollowing ? <CheckCircle size={19} weight="fill" /> : <BookmarkSimple size={19} />} {requestBusy ? "Following…" : isFollowing ? wantedIssueCount ? `View ${wantedIssueCount} wanted issue${wantedIssueCount === 1 ? "" : "s"}` : "Following · up to date" : "Follow run"}</button><button className="ghost-button" onClick={() => setTab("files")}><Eye size={18} /> View files</button></div></aside></div>;
}

function SeriesMergeWorkbench({ data, busy, error, onClose, onTargetChange, onConfirm }) {
  const dialogRef = useDialog(onClose);
  const [allowProviderConflicts, setAllowProviderConflicts] = useState(false);
  const preview = data.preview;
  useEffect(() => setAllowProviderConflicts(false), [data.targetId]);
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal series-merge-workbench" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="series-merge-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close duplicate run recovery"><X size={20} /></button><span className="eyebrow">Combine duplicate run</span><h2 id="series-merge-title">Repair {data.source.title}</h2><p className="workbench-intro">Choose the canonical run to keep. Flipparr will move catalog relationships, issue and volume coverage, provider evidence, wanted items, and download history. Comic files are not renamed or moved.</p><label className="form-field"><span>Run to keep</span><select value={data.targetId || ""} onChange={(event) => onTargetChange(event.target.value)}><option value="">Choose the correct run…</option>{data.candidates.map((candidate) => <option value={candidate.id} key={candidate.id}>{candidate.title}{candidate.year ? ` (${candidate.year})` : ""} · {candidate.publisher || "Publisher unknown"}</option>)}</select></label>{busy ? <div className="merge-loading"><LoadingSpinner size={28} label="Checking both runs" /><span>Checking files, issues, volumes, downloads, and provider identities…</span></div> : null}{preview && !busy ? <><div className="merge-direction"><article><small>Combine</small><strong>{preview.source.title}</strong><span>{preview.source.year || "Year unknown"} · {preview.source.counts.files} files</span></article><ArrowRight size={22} /><article className="keep"><small>Keep</small><strong>{preview.target.title}</strong><span>{preview.target.year || "Year unknown"} · {preview.target.counts.files} files</span></article></div><section className="merge-impact"><strong>After combining</strong><span>{preview.source.counts.files + preview.target.counts.files} comic files</span><span>{preview.source.counts.issues + preview.target.counts.issues} issue records before duplicate numbers are collapsed</span><span>{preview.source.counts.volumes + preview.target.counts.volumes} volumes</span></section>{preview.blockers?.length ? <div className="merge-warning blocked"><WarningCircle size={21} weight="fill" /><span><strong>These runs cannot be combined yet</strong>{preview.blockers.map((blocker) => <small key={blocker}>{blocker}</small>)}</span></div> : null}{preview.providerConflicts?.length ? <div className="merge-warning"><WarningCircle size={21} weight="fill" /><span><strong>Provider identities disagree</strong><small>This can indicate a real reboot or an incorrect match. Confirm only if these entries represent the same publication run.</small>{preview.providerConflicts.map((conflict) => <small key={conflict.provider}>{conflict.provider}: {conflict.sourceId} → {conflict.targetId}</small>)}<label><input type="checkbox" checked={allowProviderConflicts} onChange={(event) => setAllowProviderConflicts(event.target.checked)} /> I reviewed these provider IDs and want to combine the runs</label></span></div> : null}<div className="metadata-edit-actions"><button className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || preview.blockers?.length || (preview.providerConflicts?.length && !allowProviderConflicts)} onClick={() => onConfirm(allowProviderConflicts)}><ShieldCheck size={18} /> Combine runs</button></div></> : null}{error ? <p className="workbench-error" role="alert">{error}</p> : null}</section></div>;
}

function StoryArcList({ arcs, emptyTitle, onOpenSeries }) {
  if (!arcs.length) return <div className="drawer-empty"><Books size={27} weight="duotone" /><strong>{emptyTitle}</strong><span>Runs are discovered automatically. Manual grouping is available under Advanced tools.</span></div>;
  return <div className="story-arc-list">{arcs.map((arc) => <article key={arc.id}><header><div><span>{arc.type === "specials" ? "Specials / one-shots" : "Series run"}</span><strong>{arc.name}</strong></div><b className={arc.status}>{arc.status === "complete" ? "Complete" : arc.status === "partial" ? "Partially owned" : arc.status === "cataloged" ? "Not owned" : "Missing"}</b></header><div className="arc-progress-copy"><strong>{arc.ownedIssueCount} of {arc.issueCount || "unknown"} issues owned</strong><span>{arc.volumeCount} volume{arc.volumeCount === 1 ? "" : "s"} · {arc.fileCount} file{arc.fileCount === 1 ? "" : "s"}</span></div><div className="arc-run-links">{arc.runs.map((run) => <button onClick={() => onOpenSeries(run)} key={run.id}><span>{run.title} ({run.year})</span><ArrowRight size={15} /></button>)}</div></article>)}</div>;
}

function CollectionDrawer({ collection, tab, onTabChange, onClose, onFindStructure, onOpenSeries, onOpenContents, onRequest, onViewRequests, requestBusy, onEditIssue }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  useSwipeToDismiss(dialogRef, requestClose);
  if (!collection) return null;
  const arcs = collection.storyArcs || [];
  const files = collection.runs.flatMap((run) => (run.fileDetails || []).map((file) => ({ ...file, run })));
  const arcByRunId = new Map(arcs.flatMap((arc) => (arc.runIds || []).map((runId) => [String(runId), arc])));
  const volumes = collection.runs.flatMap((run) => (run.editions || []).map((volume) => {
    const storyGroup = arcByRunId.get(String(run.id));
    const hasResolvedCoverage = (volume.coverageGroups || []).some((group) => group.resolved);
    const hasUnresolvedCoverage = (volume.coverageGroups || []).some((group) => !group.resolved);
    const linkedFile = files.find((file) => (volume.files || []).includes(file.filename));
    return {
      ...volume,
      run,
      linkedFile,
      storyGroup,
      coverageStatus: volume.contentsStatus || (hasResolvedCoverage && hasUnresolvedCoverage ? "partial" : hasResolvedCoverage ? "verified" : hasUnresolvedCoverage ? "unresolved" : "unknown"),
    };
  }));
  const unverifiedVolumes = volumes.filter((volume) => volume.coverageStatus !== "verified");
  const unverifiedVolumeFileCount = unverifiedVolumes.reduce(
    (count, volume) => count + (volume.copyCount || 1), 0,
  );
  const issueCoveragePending = unverifiedVolumes.length > 0;
  const displayArcs = arcs.map((arc) => {
    const runIds = new Set((arc.runIds || []).map(String));
    const arcVolumes = volumes.filter((volume) => runIds.has(String(volume.run.id)));
    const linkedFileNames = new Set(arcVolumes.flatMap((volume) => volume.files || []));
    const directFiles = files.filter((file) => runIds.has(String(file.run.id)) && file.identityKind === "issue");
    return { ...arc, volumeCount: arcVolumes.length, fileCount: linkedFileNames.size + directFiles.length };
  });
  const mainArcs = displayArcs.filter((arc) => arc.type === "main");
  const specials = displayArcs.filter((arc) => arc.type === "specials");
  const issues = collection.runs.flatMap((run) => {
    const storyGroup = arcByRunId.get(String(run.id));
    return (run.issues || []).map((issue) => ({
      ...issue,
      contextRunId: run.id,
      contextLabel: run.title,
      contextYear: run.year,
      contextGroupName: storyGroup?.name || run.title,
      contextType: storyGroup?.type || "main",
    }));
  });
  const ownedIssueCount = issues.filter((issue) => issue.ownership !== "unowned").length;
  const missingIssueCount = Math.max(0, issues.length - ownedIssueCount);
  const coverage = {
    ...collection,
    owned: ownedIssueCount,
    total: issues.length,
    unowned: issueCoveragePending ? 0 : missingIssueCount,
    catalogKnown: issues.length > 0 && !issueCoveragePending,
    status: issueCoveragePending ? "cataloged" : issues.length ? (missingIssueCount ? "partial" : "complete") : collection.status,
    ownership: issueCoveragePending ? "Volume issue coverage is still being identified" : issues.length ? (missingIssueCount ? `${missingIssueCount} issues missing` : "All issues owned") : collection.ownership,
  };
  const display = { ...collection, id: `collection-${collection.id}`, title: collection.name };
  const preferenceLabel = ACQUISITION_LABELS[collection.acquisitionPreference] || ACQUISITION_LABELS.either;
  return <div className="drawer-backdrop" onMouseDown={onClose}>
    <aside className="series-drawer collection-drawer" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="collection-drawer-title" onMouseDown={(event) => event.stopPropagation()}>
      <button className="modal-close" onClick={onClose} aria-label="Close collection details"><X size={20} /></button>
      <div className="drawer-identity"><div className="drawer-cover"><SeriesCover series={display} /></div><div><span className={`status-chip ${collection.structureStatus === "unmapped" || issueCoveragePending ? "amber" : "green"}`}>{collection.monitoringStatus === "monitored" ? "Following" : "In your library"}</span><h2 id="collection-drawer-title">{collection.name}</h2><p>{collection.publisher} · {collection.year} · {mainArcs.length} runs{specials.length ? ` + ${specials.length} specials` : ""}</p></div></div>
      <div className="drawer-facts collection-coverage-facts">{issueCoveragePending ? <><span><strong>{volumes.length}</strong>Volumes identified</span><span><strong>{unverifiedVolumes.length}</strong>Need issue ranges</span><span><strong>{ownedIssueCount}</strong>Issues confirmed</span><span><strong>{files.length}</strong>Comic files</span></> : <><span><strong>{ownedIssueCount} <em>of {issues.length}</em></strong>Issues confirmed</span><span><strong>{missingIssueCount}</strong>Issues missing</span><span><strong>{volumes.length}</strong>Volumes owned</span><span><strong>{files.length}</strong>Comic files</span></>}</div>
      <nav className="drawer-tabs" aria-label="Collection details">{[["overview", "Overview"], ["issues", `Issues (${issues.length})`], ["volumes", `Volumes (${volumes.length})`], ["specials", `Specials (${specials.length})`], ["arcs", `Runs (${mainArcs.length})`], ["files", `Files (${files.length})`]].map(([id, label]) => <button className={tab === id ? "active" : ""} onClick={() => onTabChange(id)} key={id}>{label}</button>)}</nav>
      <div className="drawer-tab-content">
        {tab === "overview" ? <><h3>Your collection</h3><Ownership series={coverage} /><section className="monitoring-summary"><div><strong>{preferenceLabel}</strong><small>{collection.includeSpecials === false ? "Main series only" : "Main series + specials"}</small></div><span><CheckCircle size={18} weight="fill" /> Series connections updated automatically</span></section>{unverifiedVolumes.length ? <section className="placement-callout"><WarningCircle size={21} weight="fill" /><div><strong>Issue contents are still being identified</strong><small>All {unverifiedVolumes.length} logical volume{unverifiedVolumes.length === 1 ? " is" : "s are"} already matched to this series across {unverifiedVolumeFileCount} comic file{unverifiedVolumeFileCount === 1 ? "" : "s"}. Flipparr will keep looking for the issue ranges in the background.</small></div><button onClick={() => onTabChange("volumes")}>View volumes</button></section> : null}{collection.structureStatus === "unmapped" ? <section className="structure-callout"><div><strong>Complete-series details are still being found</strong><small>Flipparr will keep your current files safe while it looks for a confident series structure.</small></div><button onClick={() => onFindStructure(collection)}><MagnifyingGlass size={17} /> Review details</button></section> : null}<details className="advanced-collection-tools"><summary><Gear size={16} /> Advanced tools</summary><p>Inspect provider runs, split or combine groups, and correct unusual series structures.</p><button onClick={() => onFindStructure(collection)}><PencilSimple size={16} /> Review series structure</button></details></> : null}
        {tab === "issues" ? <GroupedIssueInventory issues={issues} onEditIssue={onEditIssue} /> : null}
        {tab === "arcs" ? <StoryArcList arcs={mainArcs} emptyTitle="No runs found" onOpenSeries={onOpenSeries} /> : null}
        {tab === "specials" ? <StoryArcList arcs={specials} emptyTitle="No specials found" onOpenSeries={onOpenSeries} /> : null}
        {tab === "volumes" ? <div className="collection-volume-list">{volumes.length ? volumes.map((volume) => { const title = volume.subtitle ? `${volume.title}: ${volume.subtitle}` : volume.title; const needsContents = volume.coverageStatus !== "verified"; const canReviewContents = needsContents && volume.linkedFile; return <button className={needsContents ? "unplaced" : "placed"} onClick={() => canReviewContents ? onOpenContents(volume.linkedFile) : onOpenSeries(volume.run)} key={`${volume.run.id}-${volume.logicalVolumeKey || volume.id}`}><span className="collection-volume-cover"><CoverArt id={`collection-volume-${volume.id}`} title={title} cover={volume.cover} decorative placeholderSize={17} /></span><span><strong>{title}</strong><small>{volume.run.title} · {editionKindLabel(volume.editionKind)}{volume.volume ? ` · Vol. ${volume.volume}` : ""}{volume.copyCount > 1 ? ` · ${volume.copyCount} files` : ""}</small>{needsContents ? <b>{volume.coverageStatus === "partial" ? `${volume.contentsIssueCount || 0} issues confirmed · more may be included` : "Issue contents not confirmed"}</b> : <b>{volume.contentsIssueCount || 0} issues confirmed</b>}</span><ArrowRight size={16} /></button>; }) : <div className="drawer-empty"><Books size={27} /><strong>No volumes cataloged</strong></div>}</div> : null}
        {tab === "files" ? <div className="collection-volume-list">{files.map((file) => <button onClick={() => onOpenSeries(file.run)} key={file.id}><HardDrive size={20} weight="duotone" /><span><strong>{file.filename}</strong><small>{file.run.title} · {file.identityKind === "issue" ? "Single issue" : editionKindLabel(file.editionKind)}</small></span><ArrowRight size={16} /></button>)}</div> : null}
      </div>
      <div className="drawer-actions"><button className={collection.monitoringStatus === "monitored" ? "ghost-button" : "primary-button"} disabled={requestBusy || (collection.monitoringStatus === "monitored" && !issueCoveragePending && missingIssueCount === 0)} onClick={issueCoveragePending ? () => onTabChange("volumes") : collection.monitoringStatus === "monitored" ? onViewRequests : onRequest}>{requestBusy ? <LoadingSpinner size={18} /> : issueCoveragePending ? <Books size={18} /> : collection.monitoringStatus === "monitored" ? <CheckCircle size={18} weight="fill" /> : <BookmarkSimple size={18} />} {requestBusy ? "Following…" : issueCoveragePending ? "Review volume contents" : collection.monitoringStatus === "monitored" ? missingIssueCount ? `View ${missingIssueCount} wanted issue${missingIssueCount === 1 ? "" : "s"}` : "Following · up to date" : "Follow collection"}</button><button className="ghost-button" onClick={() => onTabChange("files")}><Eye size={18} /> View files</button></div>
    </aside>
  </div>;
}

function StoryStructureWorkbench({ data, busy, error, onClose, onSave }) {
  const dialogRef = useDialog(onClose);
  const [arcs, setArcs] = useState(() => (data.arcs || []).map((arc) => ({ ...arc, runIds: [...arc.runIds], runLabels: [...arc.runLabels] })));
  function update(index, fields) { setArcs((items) => items.map((arc, position) => position === index ? { ...arc, ...fields } : arc)); }
  function move(index, direction) { setArcs((items) => { const target = index + direction; if (target < 0 || target >= items.length) return items; const next = [...items]; [next[index], next[target]] = [next[target], next[index]]; return next; }); }
  function addGroup() { setArcs((items) => [...items, { id: null, name: "New story group", type: "main", runIds: [], runLabels: [], issueCount: 0, volumeCount: 0, confidence: "manual", reason: "Created for manual organization" }]); }
  function moveRun(fromIndex, runIndex, toIndex) { setArcs((items) => { const next = items.map((arc) => ({ ...arc, runIds: [...arc.runIds], runLabels: [...arc.runLabels] })); const [runId] = next[fromIndex].runIds.splice(runIndex, 1); const [runLabel] = next[fromIndex].runLabels.splice(runIndex, 1); next[toIndex].runIds.push(runId); next[toIndex].runLabels.push(runLabel); return next; }); }
  function submit(event) { event.preventDefault(); onSave(arcs.filter((arc) => arc.runIds.length).map((arc) => ({ name: arc.name.trim(), type: arc.type, runIds: arc.runIds }))); }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal structure-workbench" role="dialog" aria-modal="true" aria-labelledby="structure-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close series structure"><X size={20} /></button><span className="eyebrow">Find series structure</span><h2 id="structure-workbench-title">{data.collection.name}</h2><p className="workbench-intro">Review the proposed hierarchy before saving. Runs can be grouped into one story arc or split into separate arcs and specials; files and metadata are not changed.</p><form onSubmit={submit}><div className="structure-groups">{arcs.map((arc, index) => <article className={!arc.runIds.length ? "empty" : ""} key={`${arc.id || "new"}-${index}`}><header><span>{index + 1}</span><input value={arc.name} onChange={(event) => update(index, { name: event.target.value })} aria-label={`Story group ${index + 1} name`} /><select value={arc.type} onChange={(event) => update(index, { type: event.target.value })}><option value="main">Story arc</option><option value="specials">Specials / one-shots</option></select><button type="button" onClick={() => move(index, -1)} disabled={index === 0} aria-label="Move group up">↑</button><button type="button" onClick={() => move(index, 1)} disabled={index === arcs.length - 1} aria-label="Move group down">↓</button></header><small>{arc.reason} · {arc.confidence} confidence</small><div className="structure-runs">{arc.runIds.length ? arc.runIds.map((runId, runIndex) => <div key={runId}><span>{arc.runLabels[runIndex]}</span>{arcs.length > 1 ? <select value={index} onChange={(event) => moveRun(index, runIndex, Number(event.target.value))} aria-label={`Move ${arc.runLabels[runIndex]} to another group`}>{arcs.map((target, targetIndex) => <option value={targetIndex} key={targetIndex}>Move to {target.name || `group ${targetIndex + 1}`}</option>)}</select> : null}</div>) : <em>Empty group — move a run here or it will not be saved.</em>}</div></article>)}</div><button type="button" className="ghost-button add-structure-group" onClick={addGroup}><Plus size={17} /> Add story group</button>{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="metadata-edit-actions"><button type="button" className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || !arcs.some((arc) => arc.runIds.length)} aria-busy={busy}>{busy ? <LoadingSpinner size={18} /> : <ShieldCheck size={18} />} Save series structure</button></div></form></section></div>;
}

function candidateValue(value) {
  if (value === null || value === undefined || value === "") return "Not set";
  if (value === "issue") return "Single issue";
  if (value === "edition") return "Volume";
  return String(value);
}

function MatchCandidateCard({ candidate, current, selectedKey, busy, onMatch }) {
  const preview = candidate.selectionPreview || { changes: [], associations: [], fields: {} };
  // Identical visible fields do not prove that this provider identity was selected.
  // Fix Match results remain actionable until their retained candidate key is applied.
  const isSelected = Boolean(selectedKey) && selectedKey === candidate.key;
  const edition = candidate.matched_edition || {};
  const creators = candidate.creators || [];
  const rank = candidate.identity_confidence?.score ?? candidate.match_score;
  const facts = [
    ["Record", candidateValue(preview.fields.recordType)],
    ["Publisher", candidate.publisher || "Not supplied"],
    ["Year", candidate.publication_year || candidate.published_date || "Not supplied"],
    ["Format", candidate.format || "Not supplied"],
    ["ISBN", (candidate.isbns || []).join(", ") || "Not supplied"],
    ["Creators", creators.join(", ") || "Not supplied"],
    ["Pages", edition.number_of_pages || candidate.page_count || "Not supplied"],
    ["Cover", preview.coverEffect],
  ];
  return <article className={`match-candidate-card ${isSelected ? "selected" : ""}`}><div className="candidate-cover">{candidate.cover ? <img src={candidate.cover} alt={`${candidate.title || "Candidate"} cover`} /> : <span><Books size={28} weight="duotone" />No cover</span>}</div><div className="candidate-body"><header><div><span>{candidate.source || "Catalog candidate"}</span><h3>{candidate.title || "Untitled candidate"}</h3>{candidate.subtitle ? <strong>{candidate.subtitle}</strong> : null}</div><div className="candidate-badges">{isSelected ? <b className="selected-badge"><CheckCircle size={14} weight="fill" /> Current</b> : null}{rank != null ? <b>{candidate.identity_confidence ? `Identity ${rank}%` : `Rank ${rank}`}</b> : null}{candidate.cover ? <b>Cover</b> : null}</div></header>{candidate.verification_status ? <p className="candidate-verification">{volumeTerminology(candidate.verification_status)}</p> : null}<dl className="candidate-facts">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><section className="candidate-associations"><h4>Issue association</h4>{preview.associations.length ? preview.associations.map((association, index) => <div key={`${association.series}-${association.issueLabel}-${index}`}><strong>{association.series} #{association.issueLabel}</strong><small>{association.source || "Catalog evidence"} · {association.confidence}</small></div>) : <p>No issue or volume coverage association supplied by this match.</p>}</section><section className="candidate-impact"><h4>What selecting this will change</h4>{preview.changes.length ? <div>{preview.changes.map((change) => <span key={change.field}><b>{change.label}</b><del>{candidateValue(change.from)}</del><ArrowRight size={13} /><ins>{candidateValue(change.to)}</ins></span>)}</div> : <p>This candidate matches the currently applied fields.</p>}<small>{candidate.cover ? "Its provider image will also become available in the Cover picker; automatic cover priority will not be changed." : "No provider cover will be added by this candidate."}</small></section>{candidate.match_reasons?.length || candidate.description ? <details className="candidate-evidence"><summary>Evidence and description</summary>{candidate.match_reasons?.length ? <ul>{candidate.match_reasons.map((reason) => <li key={reason}>{volumeTerminology(reason)}</li>)}</ul> : null}{candidate.description ? <p>{candidate.description}</p> : null}</details> : null}<button className="candidate-select" disabled={busy || isSelected} onClick={() => onMatch(candidate.key)}>{isSelected ? "Currently selected" : `Use this match · ${preview.changes.length} change${preview.changes.length === 1 ? "" : "s"}`}</button></div></article>;
}

function MetadataWorkbench({ data, mode, busy, error, onClose, onSave, onMatch, onSearch, onReset }) {
  const dialogRef = useDialog(onClose);
  const [fields, setFields] = useState(() => ({ ...data.current, ...data.override }));
  const [matchQuery, setMatchQuery] = useState(data.suggestedSearchQuery || data.current?.seriesTitle || data.current?.title || "");
  const set = (key) => (event) => setFields({ ...fields, [key]: event.target.value });
  const isIssue = fields.recordType === "issue";
  function submit(event) {
    event.preventDefault();
    const lockedFields = Object.keys(fields).filter((key) => fields[key] !== "" && fields[key] != null);
    onSave(fields, lockedFields);
  }
  function searchMatches(event) {
    event.preventDefault();
    const query = matchQuery.trim();
    if (query) onSearch(query);
  }
  const searchInfo = data.candidateSearch || {};
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className={`modal metadata-workbench ${mode === "match" ? "match-workbench" : ""}`} role="dialog" aria-modal="true" aria-labelledby="metadata-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close metadata workbench"><X size={20} /></button><span className="eyebrow">{mode === "match" ? "Fix match" : "Edit metadata"}</span><h2 id="metadata-workbench-title">{data.file.filename}</h2>{mode === "match" ? <><p className="workbench-intro">Search your enabled metadata services, then compare the complete metadata and ownership impact before selecting a match.</p><form className="match-search-form" onSubmit={searchMatches}><label htmlFor="fix-match-query"><span>Search metadata services</span><small>Starts with your latest saved title and year. You can broaden or replace the query.</small></label><div><input id="fix-match-query" value={matchQuery} onChange={(event) => setMatchQuery(event.target.value)} placeholder="Series, volume, year, or ISBN…" autoComplete="off" /><button type="submit" className="primary-button" disabled={busy || matchQuery.trim().length < 2}>{busy ? <LoadingSpinner size={18} /> : <MagnifyingGlass size={18} />}{busy ? "Searching…" : "Search"}</button></div>{searchInfo.query ? <p className="match-search-summary"><strong>{searchInfo.resultCount || 0} result{searchInfo.resultCount === 1 ? "" : "s"}</strong> for “{searchInfo.query}” · {(searchInfo.providersChecked || []).join(", ") || "metadata services"}{searchInfo.errors?.length ? <span>{searchInfo.errors.length} service{searchInfo.errors.length === 1 ? "" : "s"} could not respond</span> : null}</p> : null}</form><div className="match-candidates">{data.candidates.length ? data.candidates.map((candidate) => <MatchCandidateCard candidate={candidate} current={data.current} selectedKey={data.selectedCandidateKey} busy={busy} onMatch={onMatch} key={candidate.key} />) : <div className="drawer-empty"><MagnifyingGlass size={26} /><strong>{searchInfo.query ? `No matches found for “${searchInfo.query}”` : "No alternate candidates were retained"}</strong><span>Try a cleaner series title, publication year, volume number, or ISBN. Saved metadata corrections automatically become the next suggested search.</span></div>}</div></> : <form className="metadata-edit-form" onSubmit={submit}><p className="workbench-intro">Saved values are stored locally and locked against future provider refreshes. Correcting the series also heals other files connected to the same local run; volume-specific details stay separate. The comic file itself is not modified.</p><label><span>Series</span><input value={fields.seriesTitle || ""} onChange={set("seriesTitle")} required /></label><label><span>Display title</span><input value={fields.title || ""} onChange={set("title")} required /></label><label><span>Subtitle</span><input value={fields.subtitle || ""} onChange={set("subtitle")} /></label><label><span>Record type</span><select value={fields.recordType || "edition"} onChange={set("recordType")}><option value="issue">Single issue</option><option value="edition">Volume</option></select></label>{isIssue ? <label><span>Issue number</span><input value={fields.issueNumber || ""} onChange={set("issueNumber")} /></label> : <><label><span>Volume number</span><input type="number" min="0" value={fields.volumeNumber ?? ""} onChange={set("volumeNumber")} /></label><label><span>Volume type</span><select value={fields.editionKind || "edition"} onChange={set("editionKind")}>{Object.entries(EDITION_KIND_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label></>}<label><span>Publisher</span><input value={fields.publisher || ""} onChange={set("publisher")} /></label><label><span>Publication year</span><input type="number" min="1800" max="2200" value={fields.publicationYear ?? ""} onChange={set("publicationYear")} /></label><label><span>ISBN / GTIN</span><input value={fields.isbn || ""} onChange={set("isbn")} /></label><label><span>Format</span><input value={fields.format || ""} onChange={set("format")} /></label><div className="metadata-edit-actions">{Object.keys(data.override).length ? <button type="button" className="danger-button" onClick={onReset} disabled={busy}>Restore provider metadata</button> : <span />}<button className="primary-button" disabled={busy} aria-busy={busy}>{busy ? <LoadingSpinner size={18} /> : <ShieldCheck size={18} />} Save and lock</button></div></form>}{error ? <p className="workbench-error" role="alert">{error}</p> : null}</section></div>;
}

function IssueMetadataWorkbench({ issue, busy, error, onClose, onSave, onReset }) {
  const dialogRef = useDialog(onClose);
  const [title, setTitle] = useState(issue.title || "");
  const [publicationYear, setPublicationYear] = useState(issue.publicationYear || "");
  const providerTitle = issue.providerTitle || "Not supplied by provider";
  const providerYear = issue.providerPublicationYear || "Not supplied by provider";
  function submit(event) {
    event.preventDefault();
    onSave({ title: title.trim(), publicationYear: publicationYear || null });
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal issue-metadata-workbench" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="issue-metadata-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close issue metadata editor"><X size={20} /></button><span className="eyebrow">Edit issue metadata</span><h2 id="issue-metadata-title">{issue.contextLabel || "Issue"} #{issue.number}</h2><p className="workbench-intro">Correct the catalog after ingestion without modifying the comic file. Saved values remain locked when provider metadata is refreshed.</p><form className="issue-metadata-form" onSubmit={submit}><label className="form-field"><span>Issue title</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder={`Issue ${issue.number} title…`} /></label><label className="form-field"><span>Publication year</span><input type="number" min="1800" max="2200" value={publicationYear} onChange={(event) => setPublicationYear(event.target.value)} /></label><section className="provider-issue-evidence"><header><Database size={18} /><span><strong>Provider metadata retained</strong><small>You can restore these values at any time.</small></span></header><dl><div><dt>Title</dt><dd>{providerTitle}</dd></div><div><dt>Year</dt><dd>{providerYear}</dd></div></dl></section>{issue.metadataLocked ? <p className="issue-lock-note"><ShieldCheck size={17} weight="fill" /> A local correction is currently locked for this issue.</p> : null}{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="metadata-edit-actions">{issue.metadataLocked ? <button type="button" className="danger-button" onClick={onReset} disabled={busy}>Restore provider metadata</button> : <button type="button" className="ghost-button" onClick={onClose}>Cancel</button>}<button className="primary-button" disabled={busy || (!title.trim() && !publicationYear)} aria-busy={busy}>{busy ? <LoadingSpinner size={18} /> : <ShieldCheck size={18} />} Save and lock</button></div></form></section></div>;
}

function CoverWorkbench({ data, busy, error, onClose, onSelect, onUpload }) {
  const dialogRef = useDialog(onClose);
  const selectedSource = data.covers.selectedSource;
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal cover-workbench" role="dialog" aria-modal="true" aria-labelledby="cover-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close cover picker"><X size={20} /></button><span className="eyebrow">Change cover</span><h2 id="cover-workbench-title">{data.file.filename}</h2><p className="workbench-intro">Choose art from the comic, a metadata provider, or upload your own image. This does not alter the original comic file.</p><div className="cover-option-grid">{data.covers.options.map((option) => <article className={selectedSource === option.source && (!data.covers.selectedUrl || data.covers.selectedUrl === option.url) ? "selected" : ""} key={`${option.source}-${option.url}`}><img src={option.url} alt={option.label} /><div><strong>{option.label}</strong><small>{option.detail}</small><button disabled={busy} onClick={() => onSelect(option.source, option.url)}>{selectedSource === option.source && (!data.covers.selectedUrl || data.covers.selectedUrl === option.url) ? "Selected" : "Use cover"}</button></div></article>)}</div><div className="cover-picker-actions"><button className="ghost-button" disabled={busy} onClick={() => onSelect("auto", null)}><ArrowsClockwise size={17} /> Use automatic cover</button><label className="primary-button upload-cover-button"><UploadSimple size={18} /> Upload image<input type="file" accept="image/jpeg,image/png,image/webp,image/gif,image/heic,image/heif" disabled={busy} onChange={(event) => event.target.files?.[0] && onUpload(event.target.files[0])} /></label></div>{error ? <p className="workbench-error" role="alert">{error}</p> : null}</section></div>;
}

function VolumeContentsWorkbench({ data, busy, error, onClose, onChange, onReset }) {
  const dialogRef = useDialog(onClose);
  const contents = data.collectionContents;
  const [seriesId, setSeriesId] = useState(contents.seriesId);
  const [issueInput, setIssueInput] = useState("");
  const [note, setNote] = useState("");
  const [inputError, setInputError] = useState("");
  function addIssues(event) {
    event.preventDefault();
    try {
      const issueNumbers = parseIssueInput(issueInput);
      if (!issueNumbers.length) throw new Error("Enter at least one issue number");
      setInputError("");
      onChange({ seriesId, issueNumbers, included: true, note });
    } catch (parseError) { setInputError(parseError.message); }
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal contents-workbench" role="dialog" aria-modal="true" aria-labelledby="contents-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close volume contents"><X size={20} /></button><span className="eyebrow">Volume contents</span><h2 id="contents-workbench-title">{data.file.filename}</h2><p className="workbench-intro">Review what this volume contains. Provider evidence remains visible; local corrections control which canonical issues count as owned.</p><div className="contents-summary"><strong>{contents.includedCount}</strong><span>canonical issues currently included</span>{contents.hasOverrides ? <b><ShieldCheck size={14} weight="fill" /> Local corrections applied</b> : null}</div><div className="contents-list">{contents.items.length ? contents.items.map((item) => <article className={!item.included ? "excluded" : item.resolved ? "resolved" : "unresolved"} key={`${item.seriesId}-${item.seriesLabel}-${item.issueNumber}-${item.source}`}><div><strong>{item.seriesLabel} #{item.issueNumber}</strong><small>{item.source} · {item.confidence}{item.evidence ? ` · ${item.evidence}` : ""}</small></div><span>{!item.included ? "Excluded" : item.resolved ? "Counts as owned" : "Needs series match"}</span>{item.seriesId ? <button disabled={busy} onClick={() => onChange({ seriesId: item.seriesId, issueNumbers: [item.issueNumber], included: !item.included, note: item.overrideNote || "Adjusted in volume contents" })}>{item.included ? "Exclude" : "Restore"}</button> : null}</article>) : <div className="drawer-empty"><ListBullets size={26} weight="duotone" /><strong>No issue contents established</strong><span>Add the known issues below; they will be stored as a local correction.</span></div>}</div><form className="contents-add-form" onSubmit={addIssues}><h3>Add included issues</h3><label><span>Canonical series</span><select value={seriesId} onChange={(event) => setSeriesId(event.target.value)}>{data.seriesOptions.map((series) => <option value={series.id} key={series.id}>{series.title}{series.year ? ` (${series.year})` : ""}</option>)}</select></label><label><span>Issue numbers</span><input value={issueInput} onChange={(event) => setIssueInput(event.target.value)} placeholder="1-6, 8, Annual 1" /></label><label><span>Correction note</span><input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Publisher contents page, checked manually…" /></label><button className="primary-button" disabled={busy}><Plus size={18} /> Add issues</button></form>{contents.hasOverrides ? <button className="danger-button contents-reset" disabled={busy} onClick={onReset}>Restore provider contents</button> : null}{inputError || error ? <p className="workbench-error" role="alert">{inputError || error}</p> : null}</section></div>;
}

function SeriesRunWorkbench({ data, loading, busy, error, onClose, onConfirm, onBuildCollection }) {
  const dialogRef = useDialog(onClose);
  const series = data?.series || {};
  const candidates = data?.candidates;
  const [selected, setSelected] = useState([]);
  const [collectionName, setCollectionName] = useState(series.title || "");
  const [preference, setPreference] = useState("volumes");
  const [includeSpecials, setIncludeSpecials] = useState(true);
  useEffect(() => {
    setCollectionName(series.title || "");
    setSelected((data?.candidates || []).map((candidate) => candidate.providerSeriesId));
    setPreference("volumes");
    setIncludeSpecials(true);
  }, [series.id, candidates?.length]);
  const specialIds = new Set((candidates || []).filter((candidate) => /one[- ]shot|special/i.test(candidate.publishingFormat || "")).map((candidate) => candidate.providerSeriesId));
  const automaticSelection = selected.filter((id) => includeSpecials || !specialIds.has(id));
  const selectedCandidates = (candidates || []).filter((candidate) => automaticSelection.includes(candidate.providerSeriesId));
  const issueCount = selectedCandidates.reduce((count, candidate) => count + candidate.issueCount, 0);
  const specialCount = selectedCandidates.filter((candidate) => specialIds.has(candidate.providerSeriesId)).length;
  const mainCount = selectedCandidates.length - specialCount;
  function toggle(providerSeriesId) {
    setSelected((items) => items.includes(providerSeriesId) ? items.filter((id) => id !== providerSeriesId) : [...items, providerSeriesId]);
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}>
    <section className="modal series-run-workbench simple-series-plan" role="dialog" aria-modal="true" aria-labelledby="series-run-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}>
      <button className="modal-close" onClick={onClose} aria-label="Close full-series search"><X size={20} /></button>
      <span className="eyebrow">Find full series</span>
      <h2 id="series-run-workbench-title">{series.title}{series.year ? ` (${series.year})` : ""}</h2>
      <p className="workbench-intro">Add the whole series once. Flipparr will maintain its publication runs, issue coverage, volumes, and specials in the background.</p>
      {loading ? <div className="run-loading"><LoadingSpinner size={28} /><strong>Finding the full series…</strong><span>Checking related runs, issue lists, and specials.</span></div> : null}
      {!loading && candidates?.length ? <>
        <section className="automatic-series-plan">
          <div className="plan-heading"><span className="plan-icon"><CheckCircle size={23} weight="fill" /></span><div><strong>Complete series found</strong><small>{mainCount} main publication run{mainCount === 1 ? "" : "s"}{specialCount ? ` + ${specialCount} special${specialCount === 1 ? "" : "s"}` : ""} · {issueCount} canonical issues</small></div></div>
          <div className="plan-outcomes"><span><CheckCircle size={16} /> One series in your library</span><span><CheckCircle size={16} /> Volumes count toward their underlying issues</span><span><CheckCircle size={16} /> New releases and gaps stay monitored</span></div>
        </section>
        <section className="acquisition-choice">
          <div><strong>How should missing comics be acquired?</strong><small>You can change this later. Flipparr still recognizes every format you already own.</small></div>
          <div className="preference-grid">{[
            ["volumes", "Volumes first", "Prefer trades, hardcovers, and omnibuses that cover several issues."],
            ["issues", "Single issues first", "Prefer the original individual issue releases."],
            ["either", "Best available", "Use the fewest available releases to fill each gap."],
          ].map(([value, label, detail]) => <button type="button" className={preference === value ? "selected" : ""} onClick={() => setPreference(value)} key={value}><span>{preference === value ? <CheckCircle size={18} weight="fill" /> : <BookOpen size={18} />}</span><strong>{label}</strong><small>{detail}</small></button>)}</div>
          <Toggle checked={includeSpecials} onChange={setIncludeSpecials} title="Include specials and crossovers" description="One-shots stay visible in Specials and count toward complete-series coverage." />
        </section>
        <div className="full-series-actions"><button className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || automaticSelection.length < 1 || !collectionName.trim()} onClick={() => onBuildCollection(automaticSelection, collectionName.trim(), { acquisitionPreference: preference, includeSpecials })}>{busy ? <LoadingSpinner size={18} /> : <Plus size={18} />} Add &amp; monitor {issueCount} issues</button></div>
        <details className="advanced-run-details"><summary><span><Gear size={17} /> Advanced: review provider runs</span><small>Optional · automatic selection includes {automaticSelection.length} records</small></summary><div className="advanced-run-toolbar"><label><span>Series name</span><input value={collectionName} onChange={(event) => setCollectionName(event.target.value)} /></label><button type="button" onClick={() => setSelected(selected.length === candidates.length ? [] : candidates.map((candidate) => candidate.providerSeriesId))}>{selected.length === candidates.length ? "Clear all" : "Select all"}</button></div><div className="advanced-run-list">{candidates.map((candidate) => {
          const included = selected.includes(candidate.providerSeriesId) && (includeSpecials || !specialIds.has(candidate.providerSeriesId));
          return <article className={included ? "included" : ""} key={candidate.providerSeriesId}><label><input type="checkbox" checked={selected.includes(candidate.providerSeriesId)} onChange={() => toggle(candidate.providerSeriesId)} /><span><strong>{candidate.title}</strong><small>{candidate.yearLabel} · {candidate.publishingFormat || "Publication run"} · {candidate.issueCount} issues</small></span></label><div><a href={`https://www.comics.org/series/${candidate.providerSeriesId}/`} target="_blank" rel="noreferrer">Source</a><button disabled={busy} onClick={() => onConfirm(candidate.providerSeriesId)}>Use only this run</button></div></article>;
        })}</div></details>
      </> : null}
      {!loading && candidates && !candidates.length ? <div className="drawer-empty"><MagnifyingGlass size={28} /><strong>No confident full-series match found</strong><span>The library was not changed. This exception can be reviewed later in Metadata.</span></div> : null}
      {error ? <p className="workbench-error" role="alert">{error}</p> : null}
    </section>
  </div>;
}

function FileRunWorkbench({ data, busy, error, onClose, onMove }) {
  const dialogRef = useDialog(onClose);
  const current = data.current || {};
  const existingRuns = (data.seriesOptions || []).filter((run) => run.title !== current.seriesTitle);
  const suggestedTitle = current.title && current.title !== current.seriesTitle ? current.title : "";
  const [mode, setMode] = useState("new");
  const [seriesId, setSeriesId] = useState(existingRuns[0]?.id || "");
  const [title, setTitle] = useState(suggestedTitle);
  const [year, setYear] = useState(current.publicationYear || "");
  const [publisher, setPublisher] = useState(current.publisher || "");
  function submit(event) {
    event.preventDefault();
    if (mode === "existing") onMove({ seriesId });
    else onMove({ title: title.trim(), year: year || null, publisher: publisher.trim() });
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal file-run-workbench" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="file-run-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close run assignment"><X size={20} /></button><span className="eyebrow">Change publication run</span><h2 id="file-run-title">{data.file.filename}</h2><p className="workbench-intro">This file is currently attached to <strong>{current.seriesTitle}</strong>. Changing its run updates only the catalog relationship—the comic file will not be renamed, moved, or modified.</p><form onSubmit={submit} className="run-assignment-form"><div className="run-assignment-choice"><button type="button" className={mode === "new" ? "active" : ""} onClick={() => setMode("new")}><Plus size={18} /><span><strong>Separate into a new run</strong><small>Use this when the file represents a distinct series, miniseries, or group of one-shots.</small></span></button><button type="button" className={mode === "existing" ? "active" : ""} onClick={() => setMode("existing")}><Books size={18} /><span><strong>Move to an existing run</strong><small>Attach this file to another canonical run already in the library.</small></span></button></div>{mode === "existing" ? <label className="form-field"><span>Publication run</span><select value={seriesId} onChange={(event) => setSeriesId(event.target.value)}><option value="">Choose a run…</option>{existingRuns.map((run) => <option value={run.id} key={run.id}>{run.title}{run.year ? ` (${run.year})` : ""}</option>)}</select></label> : <div className="run-assignment-fields"><label className="form-field"><span>New run title</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Publication run title…" required /></label><label className="form-field"><span>Start year</span><input type="number" min="1800" max="2200" value={year} onChange={(event) => setYear(event.target.value)} /></label><label className="form-field"><span>Publisher</span><input value={publisher} onChange={(event) => setPublisher(event.target.value)} /></label></div>}<div className="run-assignment-note"><ShieldCheck size={20} weight="fill" /><span><strong>Reversible catalog change</strong><small>You can use Change run again later. Covers, volume metadata, and issue-content evidence remain attached to this file.</small></span></div>{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="metadata-edit-actions"><button type="button" className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || (mode === "existing" ? !seriesId : !title.trim())}>{busy ? <LoadingSpinner size={18} /> : <ArrowRight size={18} />} {mode === "existing" ? "Move file" : "Create run and move file"}</button></div></form></section></div>;
}

function LoginView({ onSignedIn }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await apiRequest("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      onSignedIn();
    } catch (err) {
      setError(err.message || "Could not sign in");
    } finally {
      setBusy(false);
    }
  }
  return <div className="login-shell"><form className="login-card" onSubmit={submit}>
    {/* Its own brand block rather than the sidebar's: centred over a
        left-aligned form is the usual shape of a sign-in, and the sidebar's
        rules are tuned for a row in a nav. */}
    <div className="login-brand">
      <FlipparrWordmark height={64} />
    </div>
    <label>
      <span>Username</span>
      {/* name= matters as much as autoComplete=: password managers read both
          when deciding what to offer and what to save. */}
      <input name="username" value={username} autoFocus autoComplete="username"
        autoCapitalize="none" autoCorrect="off" spellCheck={false}
        onChange={(event) => setUsername(event.target.value)} />
    </label>
    <label>
      <span>Password</span>
      <input name="password" type="password" value={password} autoComplete="current-password"
        onChange={(event) => setPassword(event.target.value)} />
    </label>
    {error ? <p className="login-error" role="alert">{error}</p> : null}
    <button className="primary-button login-submit" disabled={busy || !username || !password} aria-busy={busy}>
      {busy ? <LoadingSpinner size={18} /> : <ShieldCheck size={18} />} {busy ? "Signing in…" : "Sign in"}
    </button>
  </form></div>;
}

// URL <-> view. The server already serves the app shell for any path that is
// not under /api and not a real asset, and the auth gate lets those through so
// a login form can render, so these can be real paths rather than hash
// fragments. Hand-rolled because seven routes do not justify a router.
const ROUTE_BY_VIEW = {
  library: "/library",
  discover: "/discover",
  requests: "/requests",
  metadata: "/health",
  settings: "/settings",
  import: "/import",
  search: "/search",
};
const VIEW_BY_ROUTE = Object.fromEntries(
  Object.entries(ROUTE_BY_VIEW).map(([view, path]) => [path, view])
);

// The drawer rides as a query parameter rather than a path segment: it can be
// open over the library, a search or a collection, so it is orthogonal to which
// view is showing.
function locationForState({ active, settingsSection, searchQuery, seriesId }) {
  let path = ROUTE_BY_VIEW[active] || ROUTE_BY_VIEW.library;
  if (active === "settings" && settingsSection) path += `/${settingsSection}`;
  const params = new URLSearchParams();
  if (active === "search" && searchQuery) params.set("q", searchQuery);
  if (seriesId) params.set("series", String(seriesId));
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

function stateFromLocation(pathname, search) {
  const params = new URLSearchParams(search || "");
  const segments = String(pathname || "").split("/").filter(Boolean);
  const active = VIEW_BY_ROUTE[`/${segments[0] || ""}`] || "library";
  const requestedSection = segments[1];
  return {
    active,
    settingsSection: active === "settings" && SETTINGS_SECTIONS.some((item) => item.id === requestedSection)
      ? requestedSection
      : "library",
    searchQuery: active === "search" ? params.get("q") || "" : "",
    seriesId: params.get("series") || "",
  };
}

const BOOT_ROUTE = stateFromLocation(window.location.pathname, window.location.search);

export function App() {
  const [active, setActive] = useState(BOOT_ROUTE.active);
  const [searchQuery, setSearchQuery] = useState(BOOT_ROUTE.searchQuery);
  // A ?series= link cannot be honoured until the catalog it refers to exists.
  const [pendingSeriesId, setPendingSeriesId] = useState(BOOT_ROUTE.seriesId);
  // Bumped when the address no longer names an open drawer, so the drawer can
  // play its exit animation rather than being removed from the tree outright.
  const [drawerDismissSignal, setDrawerDismissSignal] = useState(0);
  const [selectedSeries, setSelectedSeries] = useState(null);
  const [selectedCollection, setSelectedCollection] = useState(null);
  const [seriesParentCollection, setSeriesParentCollection] = useState(null);
  const [collectionTab, setCollectionTab] = useState("overview");
  const [scanState, setScanState] = useState("idle");
  const [scanProgress, setScanProgress] = useState(null);
  const [toast, setToast] = useState("");
  const [catalog, setCatalog] = useState(null);
  const [backendStatus, setBackendStatus] = useState("loading");
  const [authStatus, setAuthStatus] = useState(null);
  const [settingsSection, setSettingsSection] = useState(BOOT_ROUTE.settingsSection);
  const [workbench, setWorkbench] = useState(null);
  const [workbenchBusy, setWorkbenchBusy] = useState(false);
  const [workbenchError, setWorkbenchError] = useState("");
  const [issueWorkbench, setIssueWorkbench] = useState(null);
  const [issueBusy, setIssueBusy] = useState(false);
  const [issueError, setIssueError] = useState("");
  const [coverWorkbench, setCoverWorkbench] = useState(null);
  const [coverBusy, setCoverBusy] = useState(false);
  const [coverError, setCoverError] = useState("");
  const [contentsWorkbench, setContentsWorkbench] = useState(null);
  const [contentsBusy, setContentsBusy] = useState(false);
  const [contentsError, setContentsError] = useState("");
  const [runWorkbench, setRunWorkbench] = useState(null);
  const [runLoading, setRunLoading] = useState(false);
  const [runBusy, setRunBusy] = useState(false);
  const [runError, setRunError] = useState("");
  const [fileRunWorkbench, setFileRunWorkbench] = useState(null);
  const [fileRunBusy, setFileRunBusy] = useState(false);
  const [fileRunError, setFileRunError] = useState("");
  const [structureWorkbench, setStructureWorkbench] = useState(null);
  const [structureBusy, setStructureBusy] = useState(false);
  const [structureError, setStructureError] = useState("");
  const [replacementFile, setReplacementFile] = useState(null);
  const [replacementBusy, setReplacementBusy] = useState(false);
  const [replacementError, setReplacementError] = useState("");
  const [requestBusyKey, setRequestBusyKey] = useState("");
  const [setupCompleted, setSetupCompleted] = useState(null);
  // Remembered per browser: someone who works in the icon rail should not have
  // to collapse it again on every visit. Storage can throw, and an unreadable
  // preference is simply the default.
  const [navCollapsed, setNavCollapsed] = useState(() => {
    try { return window.localStorage.getItem("flipparr.navCollapsed") === "1"; }
    catch { return false; }
  });
  function toggleNav() {
    setNavCollapsed((value) => {
      const next = !value;
      try { window.localStorage.setItem("flipparr.navCollapsed", next ? "1" : "0"); }
      catch { /* a preference that cannot be stored is not worth failing over */ }
      return next;
    });
  }
  const [mergeWorkbench, setMergeWorkbench] = useState(null);
  const [mergeBusy, setMergeBusy] = useState(false);
  const [mergeError, setMergeError] = useState("");
  async function loadSetupState() {
    try {
      const settings = await apiRequest("/api/v1/settings");
      setSetupCompleted(Boolean(settings?.setupCompleted));
    } catch (error) {
      // An unreachable or unauthenticated backend is not evidence that setup is
      // outstanding, and guessing wrong puts the wizard over a working library.
      setSetupCompleted(true);
    }
  }
  async function finishSetup(folder, recursive) {
    setSetupCompleted(true);
    navigate("library");
    if (folder) await scanLibrary(folder, recursive);
    else await loadCatalog();
  }
  function navigate(id, sectionId) {
    setActive(id); setSelectedSeries(null); setSelectedCollection(null); setSeriesParentCollection(null);
    // Settings shows one section at a time, so a deep link selects the section
    // rather than scrolling to it.
    if (id === "settings" && sectionId) setSettingsSection(sectionId);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  function openSearch(value) {
    const cleaned = String(value || "").trim();
    if (cleaned.length < 2) return;
    setSearchQuery(cleaned);
    navigate("search");
  }
  function openSeries(series) { setSelectedCollection(null); setSeriesParentCollection(null); setSelectedSeries(series); }
  function openCollection(collection) { if (!catalog?.collectedEditionsEnabled) return; setSelectedSeries(null); setSeriesParentCollection(null); setCollectionTab("overview"); setSelectedCollection(collection); }
  function openCollectionRun(series) { setSeriesParentCollection(selectedCollection); setSelectedCollection(null); setSelectedSeries(series); }
  function returnToCollection() {
    if (!seriesParentCollection) return;
    const refreshed = catalog?.families?.find((collection) => collection.id === seriesParentCollection.id) || seriesParentCollection;
    setSelectedSeries(null);
    setSeriesParentCollection(null);
    setSelectedCollection(refreshed);
  }
  function showToast(message) { setToast(message); window.setTimeout(() => setToast(""), 3200); }
  async function loadAuthStatus() {
    try {
      const status = await apiRequest("/api/v1/auth/status");
      setAuthStatus(status);
      return status;
    } catch {
      // Treat an unreachable status endpoint as "no gate", so a backend problem
      // surfaces as the existing offline banner rather than a stuck login form.
      setAuthStatus({ method: "none", authenticated: true });
      return null;
    }
  }
  async function signOut() {
    try {
      await apiRequest("/api/v1/auth/logout", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
    } catch {
      // Even if the call fails the session may already be gone; re-checking
      // below decides what the user actually sees.
    }
    setCatalog(null);
    await loadAuthStatus();
  }
  async function loadCatalog() {
    try {
      const data = await apiRequest("/api/v1/catalog");
      setCatalog(data);
      setBackendStatus("live");
      return data;
    } catch (error) {
      // A session can expire, or sign-in can be switched on, while this tab is
      // open. Re-check before deciding the backend is down, or the tab sits on
      // a stale library behind an offline banner with no way to sign in.
      if (error.status === 401 || error.status === 503) {
        await loadAuthStatus();
        return null;
      }
      setBackendStatus("offline");
      return null;
    }
  }
  async function pollScan(scanId, context = {}) {
    for (;;) {
      const scan = await apiRequest(`/api/v1/scans/${scanId}`);
      setScanProgress({ ...scan, ...context });
      if (scan.status === "complete") return scan;
      if (scan.status === "failed") throw new Error(scan.error || "Library scan failed");
      await new Promise((resolve) => window.setTimeout(resolve, 650));
    }
  }
  async function scanLibrary(folder, recursive = true) {
    const targets = folder
      ? [{ path: folder, recursive }]
      : (catalog?.roots || []).map((root) => ({ path: root.path, recursive: Boolean(root.recursive) }));
    // "add" was never a rendered view, so this navigated to a blank screen.
    if (!targets.length) { navigate("import"); return; }
    setScanState("scanning");
    setScanProgress(null);
    try {
      let changed = 0;
      let reused = 0;
      for (let index = 0; index < targets.length; index += 1) {
        const target = targets[index];
        const queued = await apiRequest("/api/v1/scans", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ folder: target.path, recursive: target.recursive, metadataMode: "local" }),
        });
        const scan = await pollScan(queued.id, {
          rootIndex: index + 1, rootTotal: targets.length, rootPath: target.path,
        });
        changed += Number(scan.changed_files || 0);
        reused += Number(scan.reused_files || 0);
      }
      await loadCatalog();
      setScanState("done");
      showToast(`Library inventory complete · ${changed} changed, ${reused} unchanged`);
    } catch (error) {
      setScanState("idle");
      showToast(error.message);
    }
  }
  async function updateLibraryRoot(root, recursive) {
    try {
      await apiRequest(`/api/v1/library-roots/${root.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recursive }),
      });
      await loadCatalog();
      showToast("Library folder setting saved");
    } catch (error) {
      showToast(error.message);
      throw error;
    }
  }
  async function removeLibraryRoot(root) {
    try {
      await apiRequest(`/api/v1/library-roots/${root.id}`, { method: "DELETE" });
      await loadCatalog();
      showToast("Folder removed from Flipparr · comic files were untouched");
    } catch (error) {
      showToast(error.message);
      throw error;
    }
  }
  async function resolveReview(item) {
    try {
      await apiRequest("/api/v1/reviews/resolve", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: item.path, code: item.code, fingerprint: item.fingerprint }),
      });
      await loadCatalog();
      showToast("Review item resolved");
    } catch (error) {
      showToast(error.message);
    }
  }
  async function addSeriesAlias(seriesId, alias) {
    try {
      await apiRequest(`/api/v1/series/${seriesId}/aliases`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alias }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === String(seriesId));
      if (refreshed) setSelectedSeries(refreshed);
      showToast("Series alias confirmed");
      return { ok: true };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  async function createSeriesFamily(name, runIds) {
    try {
      await apiRequest("/api/v1/families", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, runIds }),
      });
      const selectedId = selectedSeries?.id;
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === selectedId);
      if (refreshed) setSelectedSeries(refreshed);
      showToast(`Collection “${name}” created`);
      return { ok: true };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  async function setSeriesFamily(seriesId, familyId) {
    try {
      await apiRequest(`/api/v1/series/${seriesId}/family`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ familyId }),
      });
      const selectedId = selectedSeries?.id;
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === selectedId);
      if (refreshed) setSelectedSeries(refreshed);
      showToast(familyId ? "Run moved into collection" : "Run removed from collection");
      return { ok: true };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  async function openStoryStructure(collection) {
    setStructureError("");
    try {
      const data = await apiRequest(`/api/v1/collections/${collection.id}/structure`);
      setStructureWorkbench(data);
    } catch (error) { showToast(error.message); }
  }
  async function saveStoryStructure(arcs) {
    setStructureBusy(true); setStructureError("");
    try {
      const result = await apiRequest(`/api/v1/collections/${structureWorkbench.collection.id}/structure`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ arcs }),
      });
      const data = await loadCatalog();
      const refreshed = data?.families?.find((item) => item.id === result.collection.id);
      if (refreshed) setSelectedCollection(refreshed);
      setStructureWorkbench(null);
      showToast(`${result.arcCount} story group${result.arcCount === 1 ? "" : "s"} saved`);
    } catch (error) { setStructureError(error.message); }
    setStructureBusy(false);
  }
  const [rebuildingRun, setRebuildingRun] = useState(false);
  const [rebuildResult, setRebuildResult] = useState("");
  async function rebuildSeriesRun(series) {
    setRebuildingRun(true);
    setRebuildResult("");
    try {
      const result = await apiRequest(`/api/v1/series/${series.id}/rebuild`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      const data = await loadCatalog();
      const updated = data?.series?.find((item) => item.id === String(series.id));
      if (updated) setSelectedSeries(updated);
      const providerNote = result?.refresh?.status === "unavailable"
        ? " The provider could not be reached, so run Refresh details later."
        : "";
      setRebuildResult(
        `Rebuilt ${result?.clearedIssues ?? 0} issue${result?.clearedIssues === 1 ? "" : "s"}`
        + ` from ${result?.rederivedFromFiles ?? 0} file${result?.rederivedFromFiles === 1 ? "" : "s"}.${providerNote}`
      );
    } catch (error) {
      setRebuildResult(error.message || "This run could not be rebuilt");
    } finally {
      setRebuildingRun(false);
    }
  }
  async function syncSeriesIssues(seriesId, automatic = false) {
    try {
      const result = await apiRequest(`/api/v1/series/${seriesId}/issues/sync`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === String(seriesId));
      if (refreshed) setSelectedSeries(refreshed);
      const metadata = result.metadata || {};
      const repaired = (metadata.repairedTitleCount || 0) + (metadata.repairedDateCount || 0);
      const remaining = (metadata.missingTitleCount || 0) + (metadata.missingDateCount || 0);
      if (repaired) {
        showToast(`${repaired} missing field${repaired === 1 ? "" : "s"} filled${remaining ? ` · ${remaining} still unavailable` : " · metadata complete"}`);
      } else if (remaining) {
        showToast(`Metadata checked · ${remaining} field${remaining === 1 ? "" : "s"} still unavailable from providers`);
      } else {
        showToast(`${result.issueCount} issues checked · metadata complete`);
      }
      return { ok: true, result };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  async function openSeriesRunWorkbench(series) {
    setRunError("");
    setRunLoading(true);
    setRunWorkbench({ series: { id: series.id, title: series.title, year: series.year, publisher: series.publisher, ownedIssueNumbers: series.issues?.filter((issue) => issue.directOwned || issue.collectionOwned).map((issue) => issue.number) || [] }, candidates: null });
    try {
      const data = await apiRequest(`/api/v1/series/${series.id}/issue-runs`);
      setRunWorkbench(data);
    } catch (error) {
      setRunError(error.message);
    }
    setRunLoading(false);
  }
  async function confirmSeriesRun(providerSeriesId) {
    setRunBusy(true); setRunError("");
    try {
      const result = await apiRequest(`/api/v1/series/${runWorkbench.series.id}/issue-run`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ providerSeriesId }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === runWorkbench.series.id);
      if (refreshed) setSelectedSeries(refreshed);
      setRunWorkbench(null);
      showToast(`${result.issueCount} canonical issues loaded from the confirmed run`);
    } catch (error) {
      setRunError(error.message);
    }
    setRunBusy(false);
  }
  function mergeCandidateScore(source, candidate) {
    const compact = (value) => String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
    let score = 0;
    if (compact(source.title) === compact(candidate.title)) score += 1000;
    if (compact(source.publisher) && compact(source.publisher) === compact(candidate.publisher)) score += 200;
    if (source.year && candidate.year) score -= Math.min(100, Math.abs(Number(source.year) - Number(candidate.year)));
    return score;
  }
  async function previewSeriesMerge(source, targetId, candidates) {
    setMergeBusy(true); setMergeError("");
    setMergeWorkbench({ source, targetId, candidates, preview: null });
    try {
      const preview = await apiRequest("/api/v1/series/merge/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sourceId: source.id, targetId }),
      });
      setMergeWorkbench({ source, targetId, candidates, preview });
    } catch (error) { setMergeError(error.message); }
    setMergeBusy(false);
  }
  function openSeriesMergeWorkbench(source) {
    const candidates = (catalog?.series || [])
      .filter((candidate) => String(candidate.id) !== String(source.id))
      .sort((left, right) => mergeCandidateScore(source, right) - mergeCandidateScore(source, left));
    const likely = candidates.find((candidate) => mergeCandidateScore(source, candidate) >= 1000);
    setMergeError("");
    setMergeWorkbench({ source, targetId: likely?.id || "", candidates, preview: null });
    if (likely) previewSeriesMerge(source, likely.id, candidates);
  }
  async function confirmSeriesMerge(allowProviderConflicts) {
    if (!mergeWorkbench?.targetId) return;
    setMergeBusy(true); setMergeError("");
    try {
      const result = await apiRequest("/api/v1/series/merge", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sourceId: mergeWorkbench.source.id, targetId: mergeWorkbench.targetId, allowProviderConflicts }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((series) => String(series.id) === String(result.seriesId));
      setSelectedSeries(refreshed || null);
      setMergeWorkbench(null);
      showToast(`${result.title} is now one publication run`);
    } catch (error) { setMergeError(error.message); }
    setMergeBusy(false);
  }
  async function buildSeriesCollection(providerSeriesIds, name, options = {}) {
    setRunBusy(true); setRunError("");
    try {
      const result = await apiRequest(`/api/v1/series/${runWorkbench.series.id}/collection-structure`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ providerSeriesIds, name, ...options }),
      });
      const data = await loadCatalog();
      const collection = data?.families?.find((item) => item.id === result.collection.id);
      setRunWorkbench(null);
      setSelectedSeries(null);
      if (collection) setSelectedCollection(collection);
      showToast(`${name} added · ${result.issueCount} issues monitored · ${ACQUISITION_LABELS[options.acquisitionPreference] || ACQUISITION_LABELS.either}`);
    } catch (error) { setRunError(error.message); }
    setRunBusy(false);
  }
  async function openFileWorkbench(file, mode) {
    setWorkbenchError("");
    try {
      const data = await apiRequest(`/api/v1/files/${file.id}`);
      setWorkbench({ data, mode });
    } catch (error) {
      showToast(error.message);
    }
  }
  async function openFileRunWorkbench(file) {
    setFileRunError("");
    try {
      const data = await apiRequest(`/api/v1/files/${file.id}`);
      setFileRunWorkbench(data);
    } catch (error) {
      showToast(error.message);
    }
  }
  async function moveFileToRun(payload) {
    setFileRunBusy(true); setFileRunError("");
    try {
      const result = await apiRequest(`/api/v1/files/${fileRunWorkbench.file.id}/series-run`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      const data = await loadCatalog();
      const movedRun = data?.series?.find((series) => series.id === result.seriesId);
      setSelectedSeries(movedRun || null);
      setFileRunWorkbench(null);
      showToast(`File moved to “${result.title}”`);
    } catch (error) {
      setFileRunError(error.message);
    }
    setFileRunBusy(false);
  }
  async function finishMetadataChange(message, result) {
    const healing = result?.seriesHealing;
    const selectedId = healing?.seriesId || selectedSeries?.id;
    const data = await loadCatalog();
    const refreshed = data?.series?.find((item) => item.id === selectedId);
    if (refreshed) setSelectedSeries(refreshed);
    else if (selectedSeries) setSelectedSeries(null);
    setWorkbench(null);
    if (healing?.status === "healed" && healing.healedFileCount > 1) {
      showToast(`${message} · ${healing.healedFileCount} connected files updated`);
    } else if (healing?.status === "review_required") {
      showToast(`${message} · connected files kept separate for review`);
    } else {
      showToast(message);
    }
  }
  async function saveFileMetadata(fields, lockedFields) {
    setWorkbenchBusy(true); setWorkbenchError("");
    try {
      const result = await apiRequest(`/api/v1/files/${workbench.data.file.id}/metadata`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fields, lockedFields }) });
      await finishMetadataChange("Metadata corrected and locked", result);
    } catch (error) { setWorkbenchError(error.message); }
    setWorkbenchBusy(false);
  }
  async function applyFileMatch(candidateKey) {
    setWorkbenchBusy(true); setWorkbenchError("");
    try {
      const result = await apiRequest(`/api/v1/files/${workbench.data.file.id}/match`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ candidateKey }) });
      await finishMetadataChange("Match replaced and locked", result);
    } catch (error) { setWorkbenchError(error.message); }
    setWorkbenchBusy(false);
  }
  async function searchFileMatches(query) {
    setWorkbenchBusy(true); setWorkbenchError("");
    try {
      const data = await apiRequest(`/api/v1/files/${workbench.data.file.id}/candidates/search`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query }),
      });
      setWorkbench((current) => current ? { ...current, data } : current);
    } catch (error) { setWorkbenchError(error.message); }
    setWorkbenchBusy(false);
  }
  async function resetFileMetadata() {
    setWorkbenchBusy(true); setWorkbenchError("");
    try {
      await apiRequest(`/api/v1/files/${workbench.data.file.id}/metadata/reset`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await finishMetadataChange("Original provider metadata restored");
    } catch (error) { setWorkbenchError(error.message); }
    setWorkbenchBusy(false);
  }
  function openIssueWorkbench(issue) {
    setIssueError("");
    setIssueWorkbench(issue);
  }
  async function finishIssueMetadataChange(message) {
    const selectedSeriesId = selectedSeries?.id;
    const selectedCollectionId = selectedCollection?.id;
    const data = await loadCatalog();
    if (selectedSeriesId) {
      const refreshedSeries = data?.series?.find((item) => item.id === selectedSeriesId);
      if (refreshedSeries) setSelectedSeries(refreshedSeries);
    }
    if (selectedCollectionId) {
      const refreshedCollection = data?.families?.find((item) => item.id === selectedCollectionId);
      if (refreshedCollection) setSelectedCollection(refreshedCollection);
    }
    setIssueWorkbench(null);
    showToast(message);
  }
  async function saveIssueMetadata(fields) {
    setIssueBusy(true); setIssueError("");
    try {
      await apiRequest(`/api/v1/issues/${issueWorkbench.id}/metadata`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fields),
      });
      await finishIssueMetadataChange("Issue metadata corrected and locked");
    } catch (error) { setIssueError(error.message); }
    setIssueBusy(false);
  }
  async function resetIssueMetadata() {
    setIssueBusy(true); setIssueError("");
    try {
      await apiRequest(`/api/v1/issues/${issueWorkbench.id}/metadata/reset`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      await finishIssueMetadataChange("Provider issue metadata restored");
    } catch (error) { setIssueError(error.message); }
    setIssueBusy(false);
  }
  async function createAcquisitionRequest(target) {
    const collection = target?.isCollectionSeries ? target.collection : target;
    const isCollection = Boolean(collection?.runs);
    const requestKey = `${isCollection ? "collection" : "series"}:${collection.id}`;
    setRequestBusyKey(requestKey);
    try {
      const request = await apiRequest("/api/v1/requests", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scopeType: isCollection ? "collection" : "series",
          scopeId: collection.id,
          acquisitionPreference: collection.acquisitionPreference || "either",
          includeSpecials: collection.includeSpecials ?? true,
        }),
      });
      const data = await loadCatalog();
      if (isCollection) {
        const refreshedCollection = data?.families?.find((item) => String(item.id) === String(collection.id));
        if (refreshedCollection && selectedCollection) setSelectedCollection(refreshedCollection);
      } else {
        const refreshedSeries = data?.series?.find((item) => String(item.id) === String(collection.id));
        if (refreshedSeries && selectedSeries) setSelectedSeries(refreshedSeries);
      }
      const wantedCount = Number(request.wantedIssueCount || 0);
      showToast(wantedCount
        ? `Following ${request.title} · ${wantedCount} missing issue${wantedCount === 1 ? "" : "s"} added to Wanted`
        : `Following ${request.title} · you’re up to date`);
      return { ok: true, request };
    } catch (error) {
      showToast(error.message);
      return { ok: false, error: error.message };
    } finally {
      setRequestBusyKey("");
    }
  }
  async function requestDiscoveredSeries(target) {
    try {
      const result = await apiRequest("/api/v1/discover", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: target.query,
          provider: target.provider,
          providerSeriesId: target.providerSeriesId,
          acquisitionPreference: "either",
        }),
      });
      await loadCatalog();
      navigate("requests");
      showToast(`${result.series.title} added · ${result.issueCount} issues followed`);
      return { ok: true, result };
    } catch (error) {
      showToast(error.message);
      return { ok: false, error: error.message };
    }
  }
  function openReplacementRequest(file) {
    setReplacementError("");
    setReplacementFile({ ...file, id: file.fileId || file.id, filename: file.filename || file.file });
  }
  async function createFileReplacement(options) {
    setReplacementBusy(true); setReplacementError("");
    try {
      const selectedSeriesId = selectedSeries?.id;
      const request = await apiRequest(`/api/v1/files/${replacementFile.id}/replacement`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(options),
      });
      const data = await loadCatalog();
      if (selectedSeriesId) {
        const refreshed = data?.series?.find((item) => item.id === selectedSeriesId);
        if (refreshed) setSelectedSeries(refreshed);
      }
      setReplacementFile(null);
      showToast(`Replacement requested for ${request.targetTitle}`);
    } catch (error) { setReplacementError(error.message); }
    setReplacementBusy(false);
  }
  async function cancelFileReplacement(request) {
    try {
      await apiRequest(`/api/v1/replacements/${request.id}/status`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "cancelled" }),
      });
      await loadCatalog();
      showToast(`Replacement cancelled for ${request.targetTitle}`);
    } catch (error) { showToast(error.message); }
  }
  async function openCoverWorkbench(file) {
    setCoverError("");
    try {
      const data = await apiRequest(`/api/v1/files/${file.id}`);
      setCoverWorkbench(data);
    } catch (error) { showToast(error.message); }
  }
  async function finishCoverChange(message) {
    const selectedId = selectedSeries?.id;
    const data = await loadCatalog();
    const refreshed = data?.series?.find((item) => item.id === selectedId);
    if (refreshed) setSelectedSeries(refreshed);
    setCoverWorkbench(null);
    showToast(message);
  }
  async function selectFileCover(source, url) {
    setCoverBusy(true); setCoverError("");
    try {
      await apiRequest(`/api/v1/files/${coverWorkbench.file.id}/cover`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source, url }) });
      await finishCoverChange(source === "auto" ? "Automatic cover selection restored" : "Preferred cover updated");
    } catch (error) { setCoverError(error.message); }
    setCoverBusy(false);
  }
  async function uploadFileCover(file) {
    setCoverBusy(true); setCoverError("");
    try {
      await apiRequest(`/api/v1/files/${coverWorkbench.file.id}/cover/upload`, { method: "POST", headers: { "Content-Type": file.type }, body: file });
      await finishCoverChange("Uploaded cover saved");
    } catch (error) { setCoverError(error.message); }
    setCoverBusy(false);
  }
  async function openContentsWorkbench(file) {
    setContentsError("");
    try {
      const data = await apiRequest(`/api/v1/files/${file.id}`);
      if (!data.collectionContents) throw new Error("This file is not linked to a volume");
      setContentsWorkbench(data);
    } catch (error) { showToast(error.message); }
  }
  async function finishContentsChange(data, message) {
    const selectedId = selectedSeries?.id;
    const selectedCollectionId = selectedCollection?.id;
    const catalogData = await loadCatalog();
    const refreshed = catalogData?.series?.find((item) => item.id === selectedId);
    if (refreshed) setSelectedSeries(refreshed);
    const refreshedCollection = catalogData?.families?.find((item) => item.id === selectedCollectionId);
    if (refreshedCollection) setSelectedCollection(refreshedCollection);
    setContentsWorkbench(data);
    showToast(message);
  }
  async function changeCollectionContents(change) {
    setContentsBusy(true); setContentsError("");
    try {
      const data = await apiRequest(`/api/v1/files/${contentsWorkbench.file.id}/contents`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(change) });
      await finishContentsChange(data, change.included ? "Volume contents updated" : "Issue excluded from volume");
    } catch (error) { setContentsError(error.message); }
    setContentsBusy(false);
  }
  async function resetCollectionContents() {
    setContentsBusy(true); setContentsError("");
    try {
      const data = await apiRequest(`/api/v1/files/${contentsWorkbench.file.id}/contents/reset`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await finishContentsChange(data, "Provider volume contents restored");
    } catch (error) { setContentsError(error.message); }
    setContentsBusy(false);
  }
  useEffect(() => { loadCatalog(); }, []);
  useEffect(() => { loadSetupState(); }, []);
  // Write the current view into the address bar so a refresh, a bookmark or a
  // shared link lands where the user actually was. Held back while a ?series=
  // link is still waiting on the catalog, or this would strip the parameter
  // before it could be resolved.
  useEffect(() => {
    if (pendingSeriesId) return;
    const target = locationForState({
      active, settingsSection, searchQuery, seriesId: selectedSeries?.id,
    });
    if (target !== window.location.pathname + window.location.search) {
      window.history.pushState(null, "", target);
    }
  }, [active, settingsSection, searchQuery, selectedSeries?.id, pendingSeriesId]);
  // Back and forward move between views, and close the drawer when the entry
  // being returned to did not have it open.
  useEffect(() => {
    function applyLocation() {
      const next = stateFromLocation(window.location.pathname, window.location.search);
      setActive(next.active);
      setSettingsSection(next.settingsSection);
      setSearchQuery(next.searchQuery);
      if (next.seriesId) setPendingSeriesId(next.seriesId);
      else { setPendingSeriesId(""); setDrawerDismissSignal((count) => count + 1); }
      canonicaliseLocation(next);
    }
    // An address that parses to a view but is not how that view is spelled --
    // an unknown path, or a settings section that does not exist -- renders
    // correctly but would otherwise leave the wrong text in the address bar,
    // so a bookmark of a typo stays a typo. Replace rather than push: this is
    // a correction, not somewhere the user navigated to.
    function canonicaliseLocation(next) {
      const canonical = locationForState(next);
      if (canonical !== window.location.pathname + window.location.search) {
        window.history.replaceState(null, "", canonical);
      }
    }
    canonicaliseLocation(BOOT_ROUTE);
    window.addEventListener("popstate", applyLocation);
    return () => window.removeEventListener("popstate", applyLocation);
  }, []);
  // Resolve a ?series= link once the catalog is in. An id that no longer exists
  // simply clears, and the effect above then tidies it out of the URL.
  useEffect(() => {
    if (!pendingSeriesId || !catalog) return;
    const match = (catalog.series || []).find((item) => String(item.id) === String(pendingSeriesId));
    if (match) setSelectedSeries(match);
    setPendingSeriesId("");
  }, [pendingSeriesId, catalog]);
  useEffect(() => {
    const acquisitionEntries = [...(catalog?.requests || []), ...(catalog?.replacementRequests || [])];
    const activeDownload = acquisitionEntries.some((request) =>
      (request.jobs || []).some((job) => ["queued", "downloading", "completed", "importing", "waiting_for_files"].includes(job.downloadStatus))
    );
    // A scan counts too. Polling was tied to enrichment and downloads only, so
    // a page opened during a scan sat still until something else woke it.
    const scanning = Boolean(catalog?.activeScan);
    if (!(catalog?.enrichment?.active > 0) && !activeDownload && !scanning) return undefined;
    const timer = window.setInterval(() => loadCatalog(), scanning ? 2000 : 5000);
    return () => window.clearInterval(timer);
  }, [catalog?.enrichment?.active, catalog?.requests, catalog?.replacementRequests, catalog?.activeScan]);
  const visibleSeries = catalog?.series ?? (backendStatus === "offline" ? DEMO_SERIES : []);
  const logicalSeriesCount = logicalCatalogSeries(catalog, visibleSeries).length;
  const navActive = active === "search" || active === "discover" ? "discover" : active === "import" ? "settings" : active;
  useEffect(() => { loadAuthStatus(); }, []);
  if (authStatus && authStatus.method === "forms" && !authStatus.authenticated) {
    return <LoginView onSignedIn={async () => { await loadAuthStatus(); await loadCatalog(); }} />;
  }
  // Setup takes the whole screen: there is nothing useful in the nav until a
  // library folder exists. "Set up later" defers for this session only rather
  // than marking the instance configured, since it is not.
  //
  // An existing install upgrading into this version has setupCompleted false
  // simply because the setting did not exist when it was configured. A library
  // root is proof that the one required step was already done, so wait for the
  // catalog and treat any configured root as setup already finished.
  const setupOutstanding = setupCompleted === false
    && catalog !== null
    && !(catalog?.roots || []).length;
  if (setupOutstanding) {
    return <SetupView catalog={catalog} onFinish={finishSetup} />;
  }
  return <CollectedEditionsContext.Provider value={Boolean(catalog?.collectedEditionsEnabled)}><div className={`app-shell${navCollapsed ? " nav-collapsed" : ""}`}><AppBar query={active === "search" ? searchQuery : ""} collapsed={navCollapsed} settingsActive={navActive === "settings"} inbox={catalog?.inbox ?? []} onToggleNav={toggleNav} onSearch={openSearch} onNavigate={navigate} /><Nav active={navActive} onNavigate={navigate} catalog={catalog} backendStatus={backendStatus} logicalSeriesCount={logicalSeriesCount} authStatus={authStatus} onSignOut={signOut} /><main className="main-content">{catalog?.collectedEditionsEnabled ? <div className="collected-editions-notice"><WarningCircle size={17} weight="fill" /> <span>Collected-edition support is on. Trades, hardcovers and omnibuses have less complete metadata and file availability than Issues, and never fulfill Issue ownership or acquisition.</span></div> : null}{active === "library" ? <LibraryView onNavigate={navigate} onOpenSeries={openSeries} onOpenCollection={openCollection} onSearch={openSearch} catalog={catalog} backendStatus={backendStatus} /> : null}{active === "discover" ? <DiscoverView onSearch={openSearch} /> : null}{active === "search" ? <SearchResultsView query={searchQuery} catalog={catalog} backendStatus={backendStatus} onSearch={openSearch} onOpenSeries={openSeries} onOpenCollection={openCollection} onDiscoverRequest={requestDiscoveredSeries} /> : null}{active === "import" ? <ImportLibraryView onNavigate={navigate} onStartInventory={scanLibrary} onScanLibrary={() => scanLibrary()} onUpdateRoot={updateLibraryRoot} onRemoveRoot={removeLibraryRoot} catalog={catalog} scanState={scanState} scanProgress={scanProgress} /> : null}{active === "requests" ? <RequestsView catalog={catalog} onCreateRequest={createAcquisitionRequest} onCancelReplacement={cancelFileReplacement} onRefresh={loadCatalog} /> : null}{active === "metadata" ? <MetadataView items={catalog?.inbox ?? []} backendStatus={backendStatus} onResolve={resolveReview} onReplace={openReplacementRequest} /> : null}{active === "settings" ? <SettingsView catalog={catalog} onNavigate={navigate} onAuthChanged={loadAuthStatus} onSignOut={signOut} section={settingsSection} onSectionChange={setSettingsSection} /> : null}</main>{selectedSeries ? <SeriesDrawer series={selectedSeries} families={catalog?.families || []} allSeries={visibleSeries} parentCollection={seriesParentCollection} dismissSignal={drawerDismissSignal} onBack={returnToCollection} onClose={() => { setSelectedSeries(null); setSeriesParentCollection(null); }} onRequest={() => createAcquisitionRequest(selectedSeries)} onViewRequests={() => navigate("requests")} requestBusy={requestBusyKey === `series:${selectedSeries.id}`} onAddAlias={addSeriesAlias} onSyncIssues={syncSeriesIssues} onFindRun={openSeriesRunWorkbench} onMergeRun={openSeriesMergeWorkbench} onRebuildRun={rebuildSeriesRun} rebuilding={rebuildingRun} rebuildResult={rebuildResult} onCreateFamily={createSeriesFamily} onSetFamily={setSeriesFamily} onOpenWorkbench={openFileWorkbench} onOpenCover={openCoverWorkbench} onOpenContents={openContentsWorkbench} onChangeRun={openFileRunWorkbench} onEditIssue={openIssueWorkbench} onReplace={openReplacementRequest} /> : null}{selectedCollection ? <CollectionDrawer collection={selectedCollection} tab={collectionTab} onTabChange={setCollectionTab} onClose={() => setSelectedCollection(null)} onFindStructure={openStoryStructure} onOpenSeries={openCollectionRun} onOpenContents={openContentsWorkbench} onRequest={() => createAcquisitionRequest(selectedCollection)} onViewRequests={() => navigate("requests")} requestBusy={requestBusyKey === `collection:${selectedCollection.id}`} onEditIssue={openIssueWorkbench} /> : null}{workbench ? <MetadataWorkbench data={workbench.data} mode={workbench.mode} busy={workbenchBusy} error={workbenchError} onClose={() => setWorkbench(null)} onSave={saveFileMetadata} onMatch={applyFileMatch} onSearch={searchFileMatches} onReset={resetFileMetadata} /> : null}{issueWorkbench ? <IssueMetadataWorkbench issue={issueWorkbench} busy={issueBusy} error={issueError} onClose={() => setIssueWorkbench(null)} onSave={saveIssueMetadata} onReset={resetIssueMetadata} /> : null}{coverWorkbench ? <CoverWorkbench data={coverWorkbench} busy={coverBusy} error={coverError} onClose={() => setCoverWorkbench(null)} onSelect={selectFileCover} onUpload={uploadFileCover} /> : null}{contentsWorkbench ? <VolumeContentsWorkbench data={contentsWorkbench} busy={contentsBusy} error={contentsError} onClose={() => setContentsWorkbench(null)} onChange={changeCollectionContents} onReset={resetCollectionContents} /> : null}{runWorkbench ? <SeriesRunWorkbench data={runWorkbench} loading={runLoading} busy={runBusy} error={runError} onClose={() => setRunWorkbench(null)} onConfirm={confirmSeriesRun} onBuildCollection={buildSeriesCollection} /> : null}{fileRunWorkbench ? <FileRunWorkbench data={fileRunWorkbench} busy={fileRunBusy} error={fileRunError} onClose={() => setFileRunWorkbench(null)} onMove={moveFileToRun} /> : null}{structureWorkbench ? <StoryStructureWorkbench data={structureWorkbench} busy={structureBusy} error={structureError} onClose={() => setStructureWorkbench(null)} onSave={saveStoryStructure} /> : null}{mergeWorkbench ? <SeriesMergeWorkbench data={mergeWorkbench} busy={mergeBusy} error={mergeError} onClose={() => setMergeWorkbench(null)} onTargetChange={(targetId) => targetId ? previewSeriesMerge(mergeWorkbench.source, targetId, mergeWorkbench.candidates) : setMergeWorkbench((current) => ({ ...current, targetId: "", preview: null }))} onConfirm={confirmSeriesMerge} /> : null}{replacementFile ? <ReplacementModal file={replacementFile} busy={replacementBusy} error={replacementError} onClose={() => setReplacementFile(null)} onSubmit={createFileReplacement} /> : null}{toast ? <div className="toast"><CheckCircle size={20} weight="fill" /> {toast}</div> : null}</div></CollectedEditionsContext.Provider>;
}
