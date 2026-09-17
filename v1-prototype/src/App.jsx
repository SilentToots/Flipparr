import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { FlipparrMark, FlipparrWordmark } from "./brand.jsx";
import {
  ArrowsClockwise,
  ArrowUpRight,
  ArrowLeft,
  ArrowRight,
  BookmarkSimple,
  BookOpen,
  ImageSquare,
  Books,
  CaretDown,
  Trash,
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
  ListBullets,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  ShieldCheck,
  SignOut,
  UploadSimple,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { LoadingIndicator } from "./components/LoadingIndicator";
import { Button } from "./components/Button";
import { StatusBadge } from "./components/StatusBadge";
import { jobsNeedingAttention } from "./nav-counts.js";
import { arrivalAt, canDeleteJob, canDeletePull, classifyRequest, groupPullList, isWorking, jobsForTab, releaseSearchSummary, tabCount, waitingIssues, RECENT_ARRIVAL_DAYS } from "./pull-list.js";
import {
  buildNotifications, pruneDismissed, readDismissed, writeDismissed,
  readSeenUntil, writeSeenUntil,
} from "./notifications.js";
import { creatorRoleLabel, orderedCreators, relatedRuns } from "./run-details.js";
import { nextTabBarState } from "./tab-bar.js";
import {
  MenuIcon, SearchIcon, MobileSearchIcon, ViewOptionsIcon, NotificationsIcon,
  ComicsIcon, DiscoverIcon, PullListIcon,
  GridViewIcon, ListViewIcon, FollowingIcon, ChevronDown,
  ActiveRunIcon, FollowedIcon, SettingsNavIcon,
  PullIcon, ShelfBackIcon, ShelfNextIcon, ClearSearchIcon, DrawerCloseIcon,
} from "./design-icons.jsx";
import {
  pullState, issueKey, PULL_STATES, PULL_LABELS, shelfState, splitSearchResults,
  countLabel, providerProgress, libraryMatchState,
  selectableIssue, releasedToPull, runPullSummary, runPreviewIds,
  runModes, completeRunToPull, issueLabel, libraryRunMatches,
} from "./discover.js";

const NAV_ITEMS = [
  { id: "library", label: "Comics", icon: ComicsIcon },
  { id: "discover", label: "Discover", icon: DiscoverIcon },
  { id: "requests", label: "Pull List", icon: PullListIcon },
  // Library health lives under Settings now, so its count travels with it --
  // the rail still says when something needs a decision, one level up.
  { id: "settings", label: "Settings", icon: SettingsNavIcon },
];

// What a rail badge counts, said in words for the tooltip and screen readers.
const NAV_COUNT_LABELS = {
  requests: (n) => `${n} download${n === 1 ? "" : "s"} stopped and waiting for you`,
  settings: (n) => `${n} item${n === 1 ? "" : "s"} in Library health need${n === 1 ? "s" : ""} a decision`,
};

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

// The bell, for the phone's page headers: above 640px it lives in the app bar,
// but a phone has no app bar, so every page's own header carries it.
const NotificationsContext = createContext(null);

function PhoneBell() {
  const bell = useContext(NotificationsContext);
  if (!bell) return null;
  return <NotificationsBell notifications={bell.notifications} onOpen={bell.onOpen} onDismiss={bell.onDismiss} />;
}

const DIALOG_FOCUSABLE =
  'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

// Dialogs stack: Fix match opens on top of the series drawer. Escape and the
// focus trap must apply only to the topmost one. Listener order alone can't do
// this — every dialog listens on document, and capture order favours the one
// that mounted first, which is the one underneath.
const openDialogs = [];

// While any dialog or drawer is open the page behind it does not scroll: only
// the dialog does, and there is one scroll bar. The gutter stays reserved so
// the page does not shift sideways when its scroll bar goes.
function lockPageScroll() {
  document.documentElement.classList.add("page-scroll-locked");
}
function unlockPageScroll() {
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
function exitDurationMs() {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue("--motion-duration-exit").trim();
  const value = parseFloat(raw);
  const ms = !Number.isFinite(value) ? 300 : raw.endsWith("ms") ? value : value * 1000;
  // Two frames past the end, so the last frame of the slide is painted before
  // the drawer leaves the tree rather than racing it.
  return ms + 34;
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
    lockPageScroll();
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
      unlockPageScroll();
      // Return focus to whatever opened the dialog, not the top of the page.
      if (previouslyFocused instanceof HTMLElement && document.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
    };
  }, []);
  return ref;
}

function publicationState(series) {
  const state = series?.publicationStatus || "unknown";
  return state === "completed"
    ? { label: "Run Complete", tone: "completed", known: true }
    : state === "ongoing"
      ? { label: "Active Run", tone: "ongoing", known: true }
      // "Unknown" read as a fault on a freshly scanned library, where almost
      // every row is waiting on provider metadata that will arrive.
      : { label: "Status pending", tone: "pending", known: false };
}

function PublicationStatus({ series }) {
  const state = publicationState(series);
  // A badge on 94% of rows is not a status, it is background texture -- and it
  // buried the handful of rows that do carry one. Say nothing until there is
  // something to say, so a real Active run or Run complete stands out.
  if (!state.known) return null;
  return <span className={`publication-status ${state.tone}`}>
    {state.tone === "ongoing" ? <ActiveRunIcon size={null} /> : null}
    {state.label}
  </span>;
}

function MonitoringStatus({ series }) {
  if (series.monitoringStatus !== "monitored") return null;
  return <span className="monitoring-status"><FollowedIcon size={null} /> Following</span>;
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
const GLASS_SPRING_DURATION = 460;

function springEasing(response = 0.3, damping = 0.66, samples = 40) {
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

const GLASS_TRAVEL_EASING = springEasing();

// Something with weight, thrown: it stretches along the way it is going,
// arrives compressed, and springs back. The stretch follows the distance
// travelled, so a neighbouring tab barely deforms and a jump across the bar
// does it visibly.
function animateGlassIndicator(glass, from, to) {
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

// The pages' one tab control (Pull List, Settings), after iOS 26's segmented
// control: a pill track with a glass thumb that slides to the open tab. The
// run drawer's tabs are the same glass on its violet bar. A row wider than
// the screen scrolls sideways, and opening a tab brings it into view.
function SegmentedTabs({ label, items, value, onChange }) {
  const [trackRef, glass] = useGlassIndicator(".segmented-tab.active", [value, items.map((item) => item.id).join()]);
  useEffect(() => {
    const track = trackRef.current;
    const active = track?.querySelector(".segmented-tab.active");
    if (!active || track.scrollWidth <= track.clientWidth) return;
    const left = active.offsetLeft - (track.clientWidth - active.offsetWidth) / 2;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    track.scrollTo?.({ left: Math.max(0, left), behavior: still ? "auto" : "smooth" });
  }, [value, trackRef]);
  return <div className="segmented-tabs" role="tablist" aria-label={label} ref={trackRef}>
    <span className="glass-indicator" aria-hidden="true" style={glass || { opacity: 0 }} />
    {items.map((item) => <button
      type="button" role="tab" aria-selected={value === item.id}
      className={`segmented-tab${value === item.id ? " active" : ""}${item.className ? ` ${item.className}` : ""}`}
      onClick={() => onChange(item.id)} key={item.id}
    >{item.label}{item.count}</button>)}
  </div>;
}

function useGlassIndicator(selector, deps) {
  const containerRef = useRef(null);
  const [style, setStyle] = useState(null);
  // Where the pill was last placed, so a move knows where it is coming from.
  const placedRef = useRef(null);
  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    function place() {
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
      if (container.dataset.glassPlaced && previous) {
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

// The phone's tab bar gets out of the way while you read, as iOS apps do:
// scrolling down tucks it into the current tab, and scrolling back up,
// reaching the top of the page, changing screen, or tapping it brings it back.
// The styling only applies at phone widths, so a desktop never sees the state.
// The decision, including the iOS rubber-band case, is nextTabBarState.
function useCollapsingTabBar(resetKey) {
  const [collapsed, setCollapsed] = useState(false);
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
const SIDEBAR_COUNT_SKELETON = { display: "inline-block", width: 24, verticalAlign: "middle" };

function Nav({ active, onNavigate, catalog, backendStatus, logicalSeriesCount, authStatus, onSignOut, scanning, onScanLibrary }) {
  const [collapsed, setCollapsed] = useCollapsingTabBar(active);
  const [navRef, navGlass] = useGlassIndicator(".nav-item.active", [active, collapsed]);
  // A tap on the tucked bar opens it rather than going anywhere, so the
  // reader can see the other tabs before choosing one.
  function expandInsteadOfNavigating(event) {
    if (!collapsed || !window.matchMedia("(max-width: 640px)").matches) return;
    event.preventDefault();
    event.stopPropagation();
    setCollapsed(false);
  }
  const counts = {
    requests: jobsNeedingAttention(catalog),
    settings: catalog?.stats?.needAttention ?? 0,
  };
  return (
    <aside className={`sidebar${collapsed ? " tab-bar-collapsed" : ""}`}>
      <nav aria-label="Primary navigation" ref={navRef} onClickCapture={expandInsteadOfNavigating}
        onFocusCapture={(event) => { if (event.target.matches?.(":focus-visible")) setCollapsed(false); }}>
        {/* Drawn only in the phone's tab bar; the desktop rail marks its item itself. */}
        <span className="nav-glass glass-indicator" aria-hidden="true" style={navGlass || { opacity: 0 }} />
        {NAV_ITEMS.map(({ id, label, icon: Icon, count }) => (
          <button className={`nav-item ${active === id ? "active" : ""}`} data-nav={id} key={id} onClick={() => onNavigate(id)} aria-label={(counts[id] ?? count) ? `${label}. ${NAV_COUNT_LABELS[id]?.(counts[id] ?? count) ?? `${counts[id] ?? count}`}` : label} aria-current={active === id ? "page" : undefined}>
            <Icon size={null} /><span>{label}</span>{(counts[id] ?? count) ? <b className={(counts[id] ?? count) > 9 ? "wide" : ""} title={NAV_COUNT_LABELS[id]?.(counts[id] ?? count)}>{counts[id] ?? count}</b> : null}
          </button>
        ))}
      </nav>
      {/* Frame 8:284: the library's size sits at the rail's foot now that the
          page header is gone. The mark beside it is decoration in the file, not
          a second route home, so it is not a button. */}
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
              <span><strong>{catalog?.stats?.files ?? 0}</strong> Files</span>
              <i aria-hidden="true">|</i>
              <span><strong>{logicalSeriesCount ?? 0}</strong> Series</span>
            </span>}
          {/* A quick scan, for comics added outside Flipparr -- they stay
              invisible until one runs, and the only other way in was two
              clicks deep under Import library. It turns while any scan runs:
              started here, from the folders page, or on the server. */}
          <button type="button" className={`sidebar-scan${scanning ? " scanning" : ""}`}
            onClick={onScanLibrary} disabled={scanning} aria-busy={scanning}
            aria-label={scanning ? "Scanning your library" : "Scan library"}
            title={scanning ? "Scanning your library…"
              : catalog?.lastScan?.iso ? `Scan library · last scanned ${catalog.lastScan.date}, ${catalog.lastScan.time}`
              : "Scan library"}>
            <ArrowsClockwise size={16} />
          </button>
        </span>
      </div>
      <div className="sidebar-session">
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
const NOTIFICATION_KINDS = { download: "Download", source: "Metadata source", file: "Comic file", metadata: "Metadata", acquired: "Added" };

function NotificationsMenu({ items, onClose, onOpen, onDismiss }) {
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
  const blocking = items.filter((item) => item.severity === "error").length;
  const actionable = items.filter((item) => item.kind !== "acquired").length;
  const heading = actionable ? "Needs attention" : "Recently added";
  const shown = items.slice(0, 5);
  return <div className="notifications-menu" ref={dialogRef} role="dialog" aria-modal="false" aria-labelledby="notifications-title">
    <header><strong id="notifications-title">{heading}</strong>{items.length ? <b>{items.length}</b> : null}</header>
    {blocking ? <p className="notifications-summary">
      <WarningCircle size={15} weight="fill" className="severity-error" />
      {blocking} {blocking === 1 ? "item is" : "items are"} blocked until you act
    </p> : null}
    {shown.length ? <div className="notifications-list">{shown.map((item) =>
      // The row opens the thing; dismiss is its own control rather than a
      // gesture on the row, so acting on a notification and clearing one are
      // never the same click.
      <div className="notifications-item" key={item.id}>
        <button type="button" className="notifications-open" onClick={() => { onClose(); onOpen(item); }}>
          {item.kind === "acquired"
            ? <CheckCircle size={17} weight="fill" className="severity-done" />
            : <WarningCircle size={17} weight={item.severity === "error" ? "fill" : "regular"} className={item.severity === "error" ? "severity-error" : "severity-warning"} />}
          <span><strong>{item.title}</strong><small>{NOTIFICATION_KINDS[item.kind]} · {item.detail}</small></span>
        </button>
        <button type="button" className="notifications-dismiss" onClick={() => onDismiss(item)} aria-label={`Dismiss: ${item.title}`} title="Dismiss">
          <X size={14} />
        </button>
      </div>)}
    </div> : <p className="notifications-empty"><CheckCircle size={19} weight="fill" /> Nothing needs attention.</p>}
  </div>;
}

// The bell and its menu. The app bar carries it on a desktop and the Comics
// header on a phone -- one control in two places, so one component.
function NotificationsBell({ notifications, onOpen, onDismiss }) {
  const [open, setOpen] = useState(false);
  const items = notifications || [];
  return <div className="appbar-notifications">
    <button
      type="button" className={`appbar-action appbar-bell ${open ? "active" : ""}`}
      onClick={() => setOpen((value) => !value)}
      aria-label={items.length ? `Notifications: ${items.length}` : "Notifications"}
      aria-expanded={open}
    >
      {items.length ? <b className="appbar-badge" aria-hidden="true" /> : null}
      <NotificationsIcon />
    </button>
    {open ? <NotificationsMenu
      items={items}
      onClose={() => setOpen(false)}
      onOpen={onOpen}
      onDismiss={onDismiss}
    /> : null}
  </div>;
}

function AppBar({ query, collapsed, notifications, onToggleNav, onSearch, onNavigate, onOpenNotification, onDismissNotification }) {
  const [draft, setDraft] = useState(query || "");
  // Reloading /search?q=… must not leave the field empty under its own results.
  useEffect(() => { setDraft(query || ""); }, [query]);
  return <header className="appbar">
    <div className="appbar-brand-group">
      <button
        type="button" className="appbar-menu" onClick={onToggleNav}
        aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
        aria-expanded={!collapsed}
      ><MenuIcon /></button>
    </div>
    <div className="appbar-search">
      <SearchBar
        embedded value={draft} onChange={setDraft} onSubmit={() => onSearch(draft)}
        label="Search your library"
        placeholder="Search your library or add a series…"
      />
    </div>
    <div className="appbar-actions">
      <NotificationsBell notifications={notifications} onOpen={onOpenNotification} onDismiss={onDismissNotification} />
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
  const field = <div className="search-field">{embedded ? <SearchIcon /> : null}<input aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} onKeyDown={(event) => {
    if (event.key === "Enter" && onSubmit) {
      event.preventDefault();
      onSubmit();
    }
  }} placeholder={placeholder} /></div>;
  if (!onSubmit || embedded) return field;
  return <div className="search-row">{field}<button type="button" className="search-submit" aria-label={busy ? "Searching…" : actionLabel} title={actionLabel} disabled={busy || value.trim().length < 2} aria-busy={busy} onClick={onSubmit}>{busy ? <LoadingSpinner size={18} /> : <MagnifyingGlass size={19} />}</button></div>;
}

const LoadingSpinner = LoadingIndicator;

// Above 640px the design's header is the toolbar and nothing else: the rail
// and the app bar already say where you are. A phone has neither, so there
// every page gets the Comics screen's top row -- its name, then its actions
// and the bell -- and a page with no actions still gets its name.
function PageHeader({ title, children }) {
  return <header className={`page-header phone-header${children ? "" : " page-header-bare"}`}>
    <div className="phone-header-title"><h1>{title}</h1></div>
    {children ? <div className="page-actions">{children}</div> : null}
    <span className="phone-header-bell"><PhoneBell /></span>
  </header>;
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
  if (compact) {
    return <span className={`ownership ${series.status} compact`}>
      <span className="ownership-label">{catalogUnknown || series.status === "warning"
        ? label
        : <><strong>{series.owned}</strong> of {series.total} Owned</>}</span>
      {series.status !== "warning" && !catalogUnknown ? <span className="progress"><i style={{ width: `${percent}%` }} /></span> : null}
    </span>;
  }
  return <div className={`ownership ${series.status}`}><div className="ownership-label">{series.status === "warning" ? <WarningCircle size={19} weight="fill" /> : catalogUnknown ? <ClockCounterClockwise size={19} weight="fill" /> : <CheckCircle size={19} weight="fill" />}<strong>{label}</strong></div>{series.status !== "warning" && !catalogUnknown ? <div className="progress"><i style={{ width: `${percent}%` }} /></div> : null}{visibleDetail && visibleDetail !== label ? <span>{visibleDetail}</span> : null}</div>;
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
  if (view === "grid") return <div className="series-grid">{series.map((item) => <button className="series-card" onClick={() => onOpen(item)} key={item.id}>
    <SeriesCover series={item} />
    <span className="series-card-identity"><strong>{item.title}</strong><span className="series-card-byline">{item.publisher} • {item.year}</span></span>
    <span className="series-card-statuses"><PublicationStatus series={item} /><MonitoringStatus series={item} /></span>
    <span className="series-card-rule" />
    <Ownership series={item} compact />
  </button>)}</div>;
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

// A token rather than prose: RunStatusChip renders the label, and a function
// that returned "Completed run" while the chip tested for "completed" simply
// drew nothing at all.
function discoveryRunStatus(item) {
  const status = String(item?.status || "").toLowerCase();
  if (item?.yearEnded || ["completed", "complete", "cancelled"].includes(status)) return "completed";
  if (["ongoing", "continuing"].includes(status)) return "ongoing";
  return "unknown";
}

function searchQueryParts(value) {
  const cleaned = String(value || "").trim().replace(/\s+/g, " ");
  const match = cleaned.match(/^(.+?)(?:\s+|\s*\()((?:19|20)\d{2})\)?$/);
  return match ? { title: match[1].trim(), year: match[2] } : { title: cleaned, year: "" };
}

// A cover that 404s must not leave a broken-image glyph in a row of art.
function DiscoverCover({ src, alt, className = "", glyph = 22 }) {
  const [failed, setFailed] = useState(false);
  return <span className={`discover-cover ${className}`.trim()}>
    {src && !failed
      ? <img src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} />
      : <BookOpen size={glyph} weight="duotone" aria-hidden="true" />}
  </span>;
}

// The design's own control, not one of the app's button variants: a filled
// purple pill with a light border and an inner highlight.
function PullButton({ state, idleLabel, onClick, size = "sm" }) {
  const settled = state === PULL_STATES.queued || state === PULL_STATES.owned;
  return <button type="button" className={`pull-button pull-button-${size} pull-button-${state}`}
    onClick={onClick} disabled={state !== PULL_STATES.idle}
    aria-busy={state === PULL_STATES.pending || undefined}>
    <span>{state === PULL_STATES.idle ? idleLabel : PULL_LABELS[state]}</span>
    {state === PULL_STATES.pending ? <LoadingIndicator size={16} />
      : settled ? <FollowingIcon size={16} /> : <PullIcon />}
  </button>;
}

function RunStatusChip({ status }) {
  if (status === "completed") return <span className="run-status complete">Run Complete</span>;
  if (status === "ongoing") return <span className="run-status active"><ActiveRunIcon /> Active Run</span>;
  return null;
}

function PullCard({ issue, state, onPull, onOpen }) {
  return <article className="pull-card">
    <button type="button" className="discover-open" onClick={() => onOpen(issue)}
      aria-label={`Details for ${issue.title}`}>
      <DiscoverCover src={issue.cover} alt="" glyph={30} />
    </button>
    <div className="pull-card-body">
      <h3 title={issue.title}>{issue.title}</h3>
      <div className="pull-card-action">
        <PullButton state={state} idleLabel="Pull Issue" onClick={() => onPull(issue)} />
      </div>
    </div>
  </article>;
}

// The skeleton occupies the loaded card's box exactly. A shelf that grows when
// its comics arrive moves everything under it, which is the moment the reader
// is most likely to be reaching for something.
function PullCardSkeleton() {
  return <div className="pull-card pull-card-skeleton" aria-hidden="true">
    <span className="discover-cover" />
    <div className="pull-card-body"><h3><i /></h3><div className="pull-card-action"><i /></div></div>
  </div>;
}

const SHELF_COPY = {
  empty: { title: "Nothing listed for this week", detail: "Metron has no shipping dates for these days yet." },
  unavailable: { title: "Release dates need Metron", detail: "Connect Metron in Settings. It is the only source with comic shop shipping dates." },
};

/**
 * One week of comics, scrolled sideways.
 *
 * The chevrons page by the visible width rather than by a card, because a row
 * that moves 120px on a click reads as a twitch rather than as navigation.
 */
function ReleaseShelf({ title, date, state, issues = [], error, pulled, onPull, onOpen, onRetry }) {
  const { scroller, atStart, atEnd, measure, page } = useShelfPaging([issues.length, state]);
  const heading = date ? `${title} - ${date}` : title;
  return <section className="release-shelf" aria-label={heading}>
    <header>
      <h2>{heading}</h2>
      {state === "ready" ? <div className="shelf-scroll">
        <button type="button" onClick={() => page(-1)} disabled={atStart} aria-label={`Scroll ${title} back`}><ShelfBackIcon /></button>
        <button type="button" onClick={() => page(1)} disabled={atEnd} aria-label={`Scroll ${title} forward`}><ShelfNextIcon /></button>
      </div> : null}
    </header>
    {state === "loading" ? <div className="shelf-row" role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">Getting this week&rsquo;s releases from Metron</span>
      {[0, 1, 2, 3, 4, 5, 6, 7].map((item) => <PullCardSkeleton key={item} />)}
    </div> : null}
    {state === "ready" ? <div className="shelf-row" ref={scroller} onScroll={measure}>
      {issues.map((issue) => <PullCard issue={issue} state={pullState(issue, pulled)}
        onPull={onPull} onOpen={onOpen} key={issueKey(issue)} />)}
    </div> : null}
    {state === "error" ? <div className="shelf-message" role="status">
      <WarningCircle size={18} />
      <span><strong>This week could not be fetched</strong><small>{error}</small></span>
      <button type="button" onClick={onRetry}>Try again</button>
    </div> : null}
    {state === "empty" || state === "unavailable" ? <div className="shelf-message">
      <MagnifyingGlass size={18} />
      <span><strong>{SHELF_COPY[state].title}</strong><small>{SHELF_COPY[state].detail}</small></span>
    </div> : null}
  </section>;
}

function LibraryMatchCard({ series, onOpen }) {
  return <button type="button" className="library-match" onClick={() => onOpen(series)}>
    <span className="library-match-art">
      <DiscoverCover src={series.cover} alt={`${series.title} cover`} />
      {series.year ? <b>{series.year}</b> : null}
    </span>
    <h3 title={series.title}>{series.title}</h3>
  </button>;
}

function LibraryMatchSkeleton() {
  return <div className="library-match-skeleton" aria-hidden="true">
    <span className="discover-cover" />
    <h3><i /></h3>
  </div>;
}

function NewRunCard({ item, state, onPull, onOpen }) {
  // How long the run is, beside when it ran: "13 issues" is the difference
  // between a one-shot and a decade, and deciding to pull turns on it.
  const issues = Number(item.issueCount) || 0;
  const byline = [
    item.publisher || "Publisher unknown",
    item.yearLabel || item.yearBegan,
    issues ? `${issues} issue${issues === 1 ? "" : "s"}` : null,
  ].filter(Boolean).join(" • ");
  return <article className="new-run-card">
    <button type="button" className="discover-open" onClick={() => onOpen(item)}
      aria-label={`Issues in ${item.title}`}>
      <DiscoverCover src={item.cover} alt="" className="new-run-art" glyph={44} />
    </button>
    <div className="new-run-copy">
      <h3 title={item.title}>{item.title}</h3>
      <p title={byline}>{byline}</p>
    </div>
    <div className="new-run-status"><RunStatusChip status={discoveryRunStatus(item)} />{item.medium === "manga" ? <span className="discover-chip">Manga</span> : null}
      {/* Why a run whose title looks nothing like the search is here. */}
      {item.matchedBy ? <span className="discover-chip matched-by" title={item.matchedBy}>{item.matchedBy}</span> : null}</div>
    <hr />
    <div className="new-run-action">
      <PullButton state={state} idleLabel="Pull Run" size="md" onClick={() => onPull(item)} />
    </div>
  </article>;
}

function NewRunCardSkeleton() {
  return <div className="new-run-card new-run-skeleton" aria-hidden="true">
    <span className="discover-cover new-run-art" />
    <div className="new-run-copy"><h3><i /></h3><p><i /></p></div>
    <div className="new-run-status"><i /></div>
    <hr />
    <div className="new-run-action"><i /></div>
  </div>;
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

function LibraryLoadingSkeleton({ scan = null }) {
  const { total, processed } = scanCounts(scan);
  return <div className="library-loading" role="status" aria-live="polite" aria-busy="true">
    <section className="library-loading-message">
      <span className="library-loading-icon"><LoadingSpinner size={21} /></span>
      {scan
        ? <span><strong>Scanning your library…</strong><small>{total ? `Comic ${Math.min(processed + 1, total)} of ${total} · your runs appear as they are found.` : "Counting your comic files. Nothing is renamed or moved."}</small></span>
        : <span><strong>Loading your library…</strong><small>Bringing in your comic runs, covers, and collection status.</small></span>}
    </section>
    <div className="library-loading-tools" aria-hidden="true"><i /><i /><i /></div>
    <section className="library-loading-table" aria-hidden="true">
      <header><i /><i /><i /><i /></header>
      {[0, 1, 2, 3, 4].map((item) => <article key={item}><span className="library-loading-cover" /><span className="library-loading-copy"><i /><i /><i /></span><span className="library-loading-progress"><i /><i /></span><span className="library-loading-cell" /><span className="library-loading-cell short" /></article>)}
    </section>
  </div>;
}

// The catalog arrives in one response, so until it does every list read from
// it is empty for the wrong reason. Screens other than the library show this
// instead of their empty state, which would otherwise flash "nothing here".
function CatalogLoading({ title, detail }) {
  return <div className="library-loading" role="status" aria-live="polite" aria-busy="true">
    <section className="library-loading-message">
      <span className="library-loading-icon"><LoadingSpinner size={21} /></span>
      <span><strong>{title}</strong><small>{detail}</small></span>
    </section>
  </div>;
}

// A catalog that has not arrived yet, as opposed to a backend that could not
// supply one: offline keeps its own fallback rather than a spinner that never ends.
function catalogPending(catalog, backendStatus) {
  return !catalog && backendStatus !== "offline";
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

// The four states a comic can be in on its way to the library, in the order
// it moves through them. Following is not among them: monitoring is a property
// of the run, shown on its card, not a place a run sits.
const PULL_LIST_TABS = [
  { id: "wanted", label: "Wanted" },
  { id: "downloading", label: "Downloading" },
  { id: "acquired", label: "Acquired" },
  { id: "failed", label: "Failed" },
];

const PULL_LIST_COPY = {
  wanted: {
    description: "Issues Flipparr is looking for but has not started downloading yet.",
    emptyTitle: "Nothing on your wanted list",
    emptyDetail: "Follow a run from Discover or a series, or request a replacement from Library health.",
  },
  downloading: {
    description: "Downloads in progress right now, and issues out looking for a release.",
    emptyTitle: "Nothing is downloading",
    emptyDetail: "Issues appear here while SABnzbd is fetching them and while Flipparr is importing them.",
  },
  acquired: {
    description: `Comics added to your library in the last ${RECENT_ARRIVAL_DAYS} days, newest first. Older ones live on Comics.`,
    emptyTitle: "Nothing has arrived recently",
    emptyDetail: "Issues appear here once they are downloaded, validated and added to your library.",
  },
  failed: {
    description: "Downloads and imports that stopped and need a decision. They clear themselves once a retry succeeds or the comic arrives another way.",
    emptyTitle: "Nothing has failed",
    emptyDetail: "A download that cannot finish on its own waits here for you to retry it or pick another release.",
  },
};

const SORT_OPTIONS = [
  { value: "title", label: "Sort: Title A-Z" },
  { value: "added", label: "Sort: Recently added" },
  { value: "attention", label: "Sort: Needs attention" },
];

// Everything the phone's toolbar used to hold, in one sheet from the bottom of
// the screen: grid or list, the sort, the Following filter, and Runs or
// Collections when collected editions are on. Changes apply as they are made;
// Done, the backdrop and Escape all close it.
function LibraryViewSheet({ view, onView, sort, onSort, followingOnly, onFollowingOnly, editionsOn, scope, onScope, onClose }) {
  const dialogRef = useDialog(onClose);
  return <div className="modal-backdrop library-sheet-backdrop" onMouseDown={onClose}>
    <section className="library-sheet" role="dialog" aria-modal="true" aria-labelledby="library-sheet-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}>
      <header>
        <h2 id="library-sheet-title">View &amp; sort</h2>
        <button type="button" className="library-sheet-close" onClick={onClose} aria-label="Close"><X size={20} /></button>
      </header>
      {editionsOn ? <fieldset>
        <legend>Show</legend>
        <div className="library-sheet-segments">
          {[["runs", "Runs"], ["collections", "Collections"]].map(([id, label]) => <button type="button" aria-pressed={scope === id} onClick={() => onScope(id)} key={id}>{label}</button>)}
        </div>
      </fieldset> : null}
      {scope === "runs" ? <fieldset>
        <legend>View</legend>
        <div className="library-sheet-segments">
          <button type="button" aria-pressed={view === "grid"} onClick={() => onView("grid")}><GridViewIcon /> Grid</button>
          <button type="button" aria-pressed={view === "list"} onClick={() => onView("list")}><ListViewIcon /> List</button>
        </div>
      </fieldset> : null}
      <fieldset>
        <legend>Sort by</legend>
        <div className="library-sheet-options" role="radiogroup" aria-label="Sort by">
          {SORT_OPTIONS.map((option) => <button type="button" role="radio" aria-checked={option.value === sort} onClick={() => onSort(option.value)} key={option.value}>
            {option.label.replace(/^Sort:\s*/, "")}
            {option.value === sort ? <CheckCircle size={20} weight="fill" aria-hidden="true" /> : null}
          </button>)}
        </div>
      </fieldset>
      {scope === "runs" ? <FollowSwitch following={followingOnly} label="Following only" onChange={onFollowingOnly} /> : null}
      <button type="button" className="library-sheet-done" onClick={onClose}>Done</button>
    </section>
  </div>;
}

function SortMenu({ value, onChange }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const current = SORT_OPTIONS.find((option) => option.value === value) || SORT_OPTIONS[0];
  useEffect(() => {
    if (!open) return undefined;
    function onPointerDown(event) {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    }
    function onKeyDown(event) {
      if (event.key === "Escape") { setOpen(false); rootRef.current?.querySelector("button")?.focus(); }
    }
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);
  function choose(option) {
    onChange(option.value);
    setOpen(false);
    rootRef.current?.querySelector("button")?.focus();
  }
  // Arrow keys move through the options once the list is open, which a native
  // select gave for free and a button does not.
  function onListKeyDown(event) {
    const items = [...(rootRef.current?.querySelectorAll('[role="option"]') || [])];
    const index = items.indexOf(document.activeElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next = event.key === "ArrowDown" ? index + 1 : index - 1;
      items[(next + items.length) % items.length]?.focus();
    }
  }
  return <div className="sort-field" ref={rootRef}>
    <button
      type="button" className="sort-trigger" aria-haspopup="listbox" aria-expanded={open}
      aria-label={`Sort by. ${current.label}`} onClick={() => setOpen((value) => !value)}
    >{current.label}<ChevronDown /></button>
    {open ? <div className="sort-menu">
      <ul className="sort-menu-options" role="listbox" aria-label="Sort by" onKeyDown={onListKeyDown}>
        {SORT_OPTIONS.map((option) => (
          <li key={option.value} role="none">
            <button
              type="button" role="option" aria-selected={option.value === value}
              className={option.value === value ? "selected" : ""}
              onClick={() => choose(option)}
            >{option.label}</button>
          </li>
        ))}
      </ul>
    </div> : null}
  </div>;
}

// The phone's search: a full-screen flyout over Comics instead of a field
// dropped into the page. It uses Discover's own search field, and shows the
// library's matching runs as you type with Discover's Library Matches cards.
// Search (Enter) looks through the comic catalogs on Discover, as the desktop
// bar does.
function LibrarySearchFlyout({ series, onSearch, onOpenSeries, onClose }) {
  const dialogRef = useDialog(onClose);
  const inputRef = useRef(null);
  const [draft, setDraft] = useState("");
  useEffect(() => { inputRef.current?.focus(); }, []);
  const trimmed = draft.trim();
  const ready = trimmed.length >= 2;
  const matches = useMemo(() => {
    if (!ready) return [];
    const parts = searchQueryParts(trimmed);
    return series.filter((item) => libraryRunMatches(item, parts)).slice(0, 12);
  }, [series, trimmed, ready]);
  function searchCatalogs() {
    if (!ready) return;
    onClose();
    onSearch(trimmed);
  }
  return <div className="library-search-flyout" role="dialog" aria-modal="true" aria-label="Search" ref={dialogRef}>
    <div className="library-search-flyout-bar">
      <form className="discover-search" role="search" onSubmit={(event) => { event.preventDefault(); searchCatalogs(); }}>
        <SearchIcon />
        <input
          ref={inputRef} type="search" enterKeyHint="search" value={draft}
          onChange={(event) => setDraft(event.target.value)}
          aria-label="Search your library and comic catalogs"
          placeholder="Title, creator or publisher…"
        />
        {draft ? <button type="button" className="discover-search-clear" onClick={() => { setDraft(""); inputRef.current?.focus(); }}
          aria-label="Clear search"><ClearSearchIcon /></button> : null}
        <button type="submit" className="sr-only">Search</button>
      </form>
      <button type="button" className="library-search-flyout-cancel" onClick={onClose}>Cancel</button>
    </div>
    <div className="library-search-flyout-body">
      {ready ? <>
        <section className="discover-results" aria-live="polite">
          <header><h2>{matches.length ? <>{matches.length} <span>{matches.length === 1 ? "Library Match" : "Library Matches"}</span></> : <span>No runs in your library match</span>}</h2></header>
          {matches.length ? <div className="library-match-row">{matches.map((item) => <LibraryMatchCard series={item} onOpen={(run) => { onClose(); onOpenSeries(run); }} key={item.id} />)}</div> : null}
        </section>
        <button type="button" className="library-search-flyout-go" onClick={searchCatalogs}>Search comic catalogs for &ldquo;{trimmed}&rdquo;</button>
      </> : <p className="library-search-flyout-hint">Your runs appear as you type. Press Search to look through the comic catalogs for something new.</p>}
    </div>
  </div>;
}

function LibraryView({ onNavigate, onOpenSeries, onOpenCollection, onSearch, catalog, backendStatus, logicalSeriesCount }) {
  // A phone searches in a full-screen flyout from the magnifier in its header;
  // closing it hands focus back to the magnifier.
  const [searchOpen, setSearchOpen] = useState(false);
  const [viewSheetOpen, setViewSheetOpen] = useState(false);
  const searchToggleRef = useRef(null);
  function closeSearch() {
    setSearchOpen(false);
    searchToggleRef.current?.focus();
  }
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
  // The View & sort button marks when the library isn't showing its default.
  const viewCustomized = view !== "grid" || sort !== "title" || followingOnly || effectiveScope !== "runs";
  const sortedFamilies = useMemo(() => sortLibrary(families, sort), [families, sort]);
  return (
    <>
      <div className="dashboard-header">
      {/* The phone's whole top in one row: the title with the library's size
          under it, then search, View & sort and the bell as 44px buttons.
          Hidden above 640px, where the rail's foot, the app bar and the
          toolbar carry the same things. */}
      <div className="phone-header library-phone-header">
        <div className="phone-header-title library-phone-title">
          <h1>Comics</h1>
          <small><strong>{Number(catalog?.stats?.files ?? 0).toLocaleString()}</strong> files · <strong>{Number(logicalSeriesCount ?? 0).toLocaleString()}</strong> series</small>
        </div>
        <button
          type="button" ref={searchToggleRef} className="library-phone-action library-phone-search"
          aria-label="Search" aria-haspopup="dialog" aria-expanded={searchOpen}
          onClick={() => setSearchOpen(true)}
        ><MobileSearchIcon /></button>
        <button
          type="button" className="library-phone-action library-phone-view"
          aria-label={viewCustomized ? "View and sort, changed from the default" : "View and sort"}
          aria-haspopup="dialog" aria-expanded={viewSheetOpen} onClick={() => setViewSheetOpen(true)}
        ><ViewOptionsIcon />{viewCustomized ? <span className="library-view-dot" aria-hidden="true" /> : null}</button>
        <PhoneBell />
      </div>
      {viewSheetOpen ? <LibraryViewSheet
        view={view} onView={setView} sort={sort} onSort={setSort}
        followingOnly={followingOnly} onFollowingOnly={setFollowingOnly}
        editionsOn={editionsOn} scope={effectiveScope} onScope={setScope}
        onClose={() => setViewSheetOpen(false)}
      /> : null}
      {searchOpen ? <LibrarySearchFlyout series={series} onSearch={onSearch} onOpenSeries={onOpenSeries} onClose={closeSearch} /> : null}
      <div className="library-tools">{editionsOn ? <div className="scope-toggle" aria-label="Choose catalog grouping"><button className={effectiveScope === "runs" ? "active" : ""} onClick={() => setScope("runs")}><ListBullets size={17} /> Runs</button><button className={effectiveScope === "collections" ? "active" : ""} onClick={() => setScope("collections")}><Books size={17} /> Collections</button></div> : null}{effectiveScope === "runs" ? <div className="view-toggle" aria-label="Choose library view"><button className={view === "grid" ? "active" : ""} onClick={() => setView("grid")} aria-label="Grid view"><GridViewIcon /></button><button className={view === "list" ? "active" : ""} onClick={() => setView("list")} aria-label="List view"><ListViewIcon /></button></div> : null}<SortMenu value={sort} onChange={setSort} />{effectiveScope === "runs" ? <button className={`filter-button ${followingOnly ? "active" : ""}`} aria-pressed={followingOnly} onClick={() => setFollowingOnly((value) => !value)}><FollowingIcon /> Following</button> : null}</div>
      </div>
      <div className="dashboard-body">
      {initialLoading ? <LibraryLoadingSkeleton /> : null}
      {!initialLoading && activeScan && !series.length ? <LibraryLoadingSkeleton scan={activeScan} /> : null}
      {!initialLoading && !(activeScan && !series.length) ? <>
      {backendStatus === "offline" ? <div className="backend-banner"><WarningCircle size={19} weight="fill" /> Showing sample comics because your library is unavailable.</div> : null}
      {effectiveScope === "collections" ? (sortedFamilies.length ? <CollectionGroups families={sortedFamilies} onOpenCollection={onOpenCollection} /> : <CollectionEmpty query="" />) : displayedSeries.length ? <SeriesList series={displayedSeries} onOpen={(item) => item.isCollectionSeries && editionsOn ? onOpenCollection(item.collection) : onOpenSeries(item)} view={view} /> : followingOnly ? <div className="empty-state"><CheckCircle size={35} weight="duotone" /><strong>No followed runs</strong><span>Open any run and choose Follow run to monitor future issues.</span><button className="ghost-button" onClick={() => setFollowingOnly(false)}>Show all runs</button></div> : <CatalogEmpty onAdd={() => onNavigate("import")} />}
      </> : null}
      </div>
    </>
  );
}

/**
 * Discover: browse this week, or search every catalog.
 *
 * One screen with two states rather than two routes. The old split put the
 * search box on Discover and its results on a route of their own, so the
 * screen a reader landed on could only be used by people who already knew what
 * they wanted.
 */
function DiscoverView({
  query, catalog, backendStatus, onSearch, onClearSearch,
  onOpenSeries, onOpenCollection, onDiscoverRequest, onPullIssue, onPullIssues,
}) {
  const [draft, setDraft] = useState(query || "");
  const [releases, setReleases] = useState({ state: "loading", data: null });
  const [discovery, setDiscovery] = useState(
    { state: "idle", results: [], error: "", providersChecked: [], providersAnswered: [], fallbacks: [] });
  const [pulled, setPulled] = useState({});
  const [drawer, setDrawer] = useState(null);
  const searching = Boolean(query);

  const allSeries = useMemo(
    () => logicalCatalogSeries(catalog, backendStatus === "offline" ? DEMO_SERIES : []),
    [catalog, backendStatus]);
  const libraryMatches = useMemo(() => {
    if (!query) return [];
    const parts = searchQueryParts(query);
    return allSeries.filter((item) => libraryRunMatches(item, parts));
  }, [allSeries, query]);

  async function loadReleases() {
    setReleases({ state: "loading", data: null });
    try {
      setReleases({ state: "done", data: await apiRequest("/api/v1/discover/releases") });
    } catch (error) {
      setReleases({ state: "done", data: { available: true, error: error.message } });
    }
  }
  useEffect(() => {
    if (searching || backendStatus === "offline") return;
    loadReleases();
  }, [searching, backendStatus]);

  async function searchProviders(value = query) {
    const cleaned = String(value || "").trim();
    if (cleaned.length < 2 || backendStatus === "offline") {
      setDiscovery({ state: "done", results: [], providersChecked: [], providersAnswered: [], fallbacks: [],
        error: backendStatus === "offline" ? "Library service is unavailable" : "" });
      return;
    }
    setDiscovery({ state: "loading", results: [], error: "", providersChecked: [], providersAnswered: [], fallbacks: [] });
    try {
      // One answer: titles, a creator's runs and a publisher's, ranked together.
      const result = await apiRequest(`/api/v1/discover?query=${encodeURIComponent(cleaned)}`);
      setDiscovery({ state: "done", results: result.results || [], error: "",
        providersChecked: result.providersChecked || [], providersAnswered: result.providersAnswered || [],
        fallbacks: result.fallbacks || [], didYouMean: result.didYouMean || [],
        stillLooking: Boolean(result.stillLooking) });
    } catch (error) {
      setDiscovery({ state: "done", results: [], error: error.message,
        providersChecked: [], providersAnswered: [], fallbacks: [] });
    }
  }
  useEffect(() => {
    setDraft(query || "");
    if (searching) searchProviders(query);
  }, [query, backendStatus]);

  function mark(key, state) { setPulled((current) => ({ ...current, [key]: state })); }
  async function pullIssue(issue) {
    const key = issueKey(issue);
    mark(key, PULL_STATES.pending);
    const result = await onPullIssue(issue);
    mark(key, result?.ok ? PULL_STATES.queued : PULL_STATES.idle);
  }
  async function pullRun(item) {
    const key = `run:${item.provider}-${item.providerSeriesId}`;
    mark(key, PULL_STATES.pending);
    // A run that has ended is pulled once, whole; following it would watch
    // for issues that will never come.
    const result = discoveryRunStatus(item) === "completed"
      ? await onPullIssues({ provider: item.provider, providerSeriesId: item.providerSeriesId,
          released: true, title: item.title, query })
      : await onDiscoverRequest({ ...item, query });
    mark(key, result?.ok ? PULL_STATES.queued : PULL_STATES.idle);
  }
  // From the run drawer. Following, or taking every released issue or the
  // whole of an ended run, settles the card behind it; a handful of chosen
  // issues does not, because the card's own button is still on offer.
  const runKey = (item) => `run:${item?.provider}-${item?.providerSeriesId}`;
  async function followFromDrawer(target) {
    const result = await onDiscoverRequest(target);
    if (result?.ok && drawer?.item) mark(runKey(drawer.item), PULL_STATES.queued);
    return result;
  }
  async function pullFromDrawer(target) {
    const result = await onPullIssues(target);
    if (result?.ok && (target.released || target.complete) && drawer?.item) mark(runKey(drawer.item), PULL_STATES.queued);
    return result;
  }

  const libraryState = libraryMatchState(catalog, backendStatus, libraryMatches);
  const data = releases.data || {};
  const { fresh, ownedCount } = splitSearchResults(discovery.results);
  const progress = providerProgress(discovery);
  const outstanding = progress.filter((item) => item.status === "searching");

  return <>
    <PageHeader title="Discover" />
    <section className={`discover-hero${searching ? " searching" : ""}`}>
      {searching ? null : <h2>Pull a new issue or search your library</h2>}
      <form className="discover-search" onSubmit={(event) => { event.preventDefault(); onSearch(draft); }}>
        <SearchIcon />
        <input value={draft} onChange={(event) => setDraft(event.target.value)}
          aria-label="Search comic catalogs"
          placeholder="Try a title, creator or publisher, such as Wolverine 2026…" />
        {searching ? <button type="button" className="discover-search-clear" onClick={onClearSearch}
          aria-label="Clear search"><ClearSearchIcon /></button> : null}
        {/* The design draws no submit control. Implicit submission covers the
            pointer path, but only reliably with a real submit button in the
            form -- and a keyboard user gets something to land on. */}
        <button type="submit" className="sr-only">Search</button>
      </form>
    </section>

    {searching ? <>
      <section className="discover-results" aria-label="Library matches">
        {/* No count until there is one: "0" before the library has arrived is
            a claim, not a placeholder. */}
        <header><h2>{libraryState === "loading" ? null : <>{libraryMatches.length} </>}<span>{libraryMatches.length === 1 ? "Library Match" : "Library Matches"}</span></h2></header>
        {libraryState === "loading" ? <div className="library-match-row" role="status" aria-busy="true">
          <span className="sr-only">Reading your library</span>
          {[0, 1, 2].map((item) => <LibraryMatchSkeleton key={item} />)}
        </div> : libraryState === "ready" ? <div className="library-match-row">
          {libraryMatches.map((series) => <LibraryMatchCard series={series}
            onOpen={(item) => item.isCollectionSeries ? onOpenCollection(item.collection) : onOpenSeries(item)}
            key={series.id} />)}
        </div> : <p className="discover-note">Nothing in your library matches “{query}”.</p>}
      </section>

      <section className="discover-results" aria-label="New matches">
        <header>
          <h2>{discovery.state === "loading" ? null : <>{fresh.length} </>}<span>{fresh.length === 1 ? "New Match" : "New Matches"}</span></h2>
          {discovery.state === "loading" || discovery.fallbacks?.length ? <p className="provider-progress" role="status" aria-live="polite">
            {/* The providers asked are only known once the answer arrives. */}
            {discovery.state === "loading" && !progress.length ? <span>Searching titles, creators and publishers…</span> : null}
            {progress.map((item) => <span className={`provider-${item.status}`} key={item.name}
              title={item.error || undefined}>
              {item.name}{item.status === "searching" ? " searching…" : item.status === "failed" ? " unavailable" : ""}
            </span>)}
          </p> : null}
        </header>
        {discovery.state === "loading" ? <div className="new-run-grid" role="status" aria-busy="true">
          <span className="sr-only">Searching comic catalogs</span>
          {[0, 1, 2, 3, 4].map((item) => <NewRunCardSkeleton key={item} />)}
        </div> : null}
        {discovery.state === "done" && fresh.length ? <div className="new-run-grid">
          {fresh.map((item) => <NewRunCard item={item}
            state={pulled[`run:${item.provider}-${item.providerSeriesId}`] || PULL_STATES.idle}
            onPull={pullRun} onOpen={(run) => setDrawer({ kind: "run", item: run })}
            key={`${item.provider}-${item.providerSeriesId}`} />)}
        </div> : null}
        {discovery.state === "done" && !fresh.length ? <div className="shelf-message">
          <MagnifyingGlass size={18} />
          <span>
            <strong>{discovery.error ? "Online search needs another try"
              : ownedCount ? `All ${ownedCount} matching run${ownedCount === 1 ? "" : "s"} are already in your library`
              : `No new runs match “${query}”`}</strong>
            <small>{discovery.error || "Try the exact title, a creator's full name or a publisher, and add a four-digit year to narrow it."}</small>
          </span>
          {discovery.error ? <button type="button" onClick={() => searchProviders(query)}>Try again</button> : null}
        </div> : null}
        {outstanding.length && fresh.length ? <p className="discover-note">
          Still hearing from {outstanding.map((item) => item.name).join(" and ")}.
        </p> : null}
        {discovery.state === "done" && discovery.stillLooking ? <p className="discover-people-note">
          Still looking up creators and publishers.{" "}
          <button type="button" className="discover-text-button" onClick={() => searchProviders(query)}>Look again</button>
        </p> : null}
        {discovery.state === "done" && discovery.didYouMean?.length ? <p className="discover-people-note did-you-mean">
          <span>Did you mean</span>
          {discovery.didYouMean.map((name) => <button type="button" key={name} onClick={() => onSearch(name)}>{name}</button>)}
        </p> : null}
      </section>
    </> : <>
      <ReleaseShelf title="Latest Releases" date={formatShelfDate(data.latest?.date)}
        state={shelfState(data.latest, data.available, releases.state === "loading")}
        issues={data.latest?.issues} error={data.latest?.error || data.error}
        pulled={pulled} onPull={pullIssue} onOpen={(issue) => setDrawer({ kind: "issue", issue })}
        onRetry={loadReleases} />
      <ReleaseShelf title="Upcoming Releases" date={formatShelfDate(data.upcoming?.date)}
        state={shelfState(data.upcoming, data.available, releases.state === "loading")}
        issues={data.upcoming?.issues} error={data.upcoming?.error || data.error}
        pulled={pulled} onPull={pullIssue} onOpen={(issue) => setDrawer({ kind: "issue", issue })}
        onRetry={loadReleases} />
      {/* The week before last: a comic is easy to miss by a few days, and by
          the time you look the shelf it was on has moved up. */}
      <ReleaseShelf title="Previous Releases" date={formatShelfDate(data.previous?.date)}
        state={shelfState(data.previous, data.available, releases.state === "loading")}
        issues={data.previous?.issues} error={data.previous?.error || data.error}
        pulled={pulled} onPull={pullIssue} onOpen={(issue) => setDrawer({ kind: "issue", issue })}
        onRetry={loadReleases} />
    </>}
    {drawer?.kind === "issue" ? <DiscoverIssueDrawer issue={drawer.issue}
      state={pullState(drawer.issue, pulled)} onPull={pullIssue}
      onOpenRun={(item) => setDrawer({ kind: "run", item })} onClose={() => setDrawer(null)} /> : null}
    {drawer?.kind === "run" ? <DiscoverRunDrawer item={drawer.item} query={query}
      settled={pulled[runKey(drawer.item)]} onFollow={followFromDrawer} onPullIssues={pullFromDrawer}
      onClose={() => setDrawer(null)} /> : null}
  </>;
}

function formatLongDate(iso) {
  if (!iso) return "";
  const date = new Date(`${String(iso).slice(0, 10)}T12:00:00`);
  return Number.isNaN(date.getTime())
    ? "" : date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

const PREVIEW_PROVIDER_NAMES = { metron: "Metron", comic_vine: "Comic Vine", gcd: "Grand Comics Database" };

// The run drawer's header, for the Discover drawers: the cover as art behind a
// violet-to-black scrim, the cover itself, the title, a byline and chips. The
// same classes as the Comics drawer, so the two read as one kind of panel.
function DiscoverDrawerHero({ art, cover, title, titleId, byline, onClose, closeLabel, children }) {
  return <header className="comic-drawer-hero">
    {art ? <>
      <img className="comic-drawer-backdrop" src={art} alt="" aria-hidden="true" key={art} />
      <img className="comic-drawer-backdrop blurred" src={art} alt="" aria-hidden="true" key={`${art}-blurred`} />
    </> : null}
    <span className="comic-drawer-scrim" aria-hidden="true" />
    <button type="button" className="comic-drawer-close" onClick={onClose} aria-label={closeLabel}><DrawerCloseIcon size={null} /></button>
    <div className="comic-drawer-identity">
      <div className="comic-drawer-cover">{cover}</div>
      <div className="comic-drawer-copy">
        <div className="comic-drawer-titles">
          <h2 id={titleId}>{title}</h2>
          {byline ? <p>{byline}</p> : null}
        </div>
        {children ? <div className="comic-drawer-statuses">{children}</div> : null}
      </div>
    </div>
  </header>;
}

/**
 * One comic from a release shelf.
 *
 * What the shelf already knows -- cover, title, dates -- is drawn at once;
 * the description and credits come from Metron after the drawer opens, behind
 * placeholder lines rather than a spinner in an empty panel.
 */
function DiscoverIssueDrawer({ issue, state, onPull, onOpenRun, onClose }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  const [detail, setDetail] = useState({ state: "loading", data: null });
  useEffect(() => {
    let live = true;
    if (!issue?.providerIssueId) { setDetail({ state: "done", data: null }); return undefined; }
    setDetail({ state: "loading", data: null });
    apiRequest(`/api/v1/discover/issue?id=${encodeURIComponent(issue.providerIssueId)}`)
      .then((data) => { if (live) setDetail({ state: "done", data }); })
      .catch(() => { if (live) setDetail({ state: "error", data: null }); });
    return () => { live = false; };
  }, [issue?.providerIssueId]);
  const data = detail.data || {};
  const facts = [
    ["Ships", formatLongDate(data.storeDate || issue.storeDate)],
    ["Cover date", formatLongDate(data.coverDate || issue.coverDate)],
    ["Pages", data.pageCount],
    ["Price", data.price ? `$${data.price}` : null],
  ].filter(([, value]) => value);
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer comic-drawer discover-drawer ${closing ? "closing" : ""}`} ref={dialogRef}
      role="dialog" aria-modal="true" aria-labelledby="discover-issue-title"
      onMouseDown={(event) => event.stopPropagation()}>
      <DiscoverDrawerHero art={issue.cover} titleId="discover-issue-title" title={issue.title}
        cover={<DiscoverCover src={issue.cover} alt={`${issue.title} cover`} glyph={30} />}
        byline={[issue.publisher, issue.seriesTitle].filter(Boolean).join(" • ")}
        onClose={requestClose} closeLabel="Close issue details" />
      <div className="comic-drawer-body">
      {facts.length ? <div className="drawer-facts">
        {facts.map(([label, value]) => <span key={label}><strong>{value}</strong>{label}</span>)}
      </div> : null}
      <section className="discover-drawer-section">
        <h3>About this issue</h3>
        {detail.state === "loading" ? <div className="discover-drawer-lines" role="status" aria-busy="true">
          <span className="sr-only">Getting this issue&rsquo;s details from Metron</span><i /><i /><i />
        </div> : null}
        {detail.state === "error" ? <p className="discover-note">Metron&rsquo;s details for this issue are unavailable right now.</p> : null}
        {detail.state === "done" ? <>
          {data.storyTitles?.length ? <p className="discover-story-titles">{data.storyTitles.join(" · ")}</p> : null}
          {data.description ? <p>{data.description}</p> : <p className="discover-note">Metron has no description for this issue.</p>}
          {data.creators?.length ? <dl className="discover-credits">
            {data.creators.map((creator) => <div key={creator.name}><dt>{creator.roles.join(", ")}</dt><dd>{creator.name}</dd></div>)}
          </dl> : null}
        </> : null}
      </section>
      </div>
      <div className="discover-drawer-actions">
        {issue.providerSeriesId ? <button type="button" className="ghost-button"
          onClick={() => onOpenRun({
            provider: "metron", providerSeriesId: issue.providerSeriesId,
            providerIds: { metron: issue.providerSeriesId },
            title: issue.seriesTitle, yearBegan: issue.seriesYear,
            publisher: issue.publisher, cover: issue.cover,
          })}>See the whole run</button> : <span />}
        <PullButton state={state} idleLabel="Pull Issue" size="md" onClick={() => onPull(issue)} />
      </div>
    </aside>
  </div>;
}

/**
 * A run's issues, and how much of the run to take.
 *
 * Three ends, said before the button is pressed: following keeps watching for
 * new issues; the other two take what is chosen, once. Ticking an issue is
 * choosing, so it switches the mode itself rather than asking first.
 */
// What a run is about, in its catalog's own words. Clamped: Comic Vine's lead
// paragraph can run long, and in both drawers the issues are the point.
function RunSynopsis({ text, source, sourcePrefix = "From", loading = false }) {
  const [open, setOpen] = useState(false);
  if (loading) return <section className="run-synopsis" role="status" aria-busy="true" aria-label="Loading the story">
    <span className="discover-drawer-lines" aria-hidden="true"><i /><i /><i /></span>
  </section>;
  if (!text) return null;
  const long = text.length > 220;
  return <section className="run-synopsis" aria-label="Story">
    <h3>Story</h3>
    <p className={long && !open ? "clamped" : ""}>{text}</p>
    <footer>
      {long ? <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>{open ? "Show less" : "Read more"}</button> : null}
      {source ? <small>{sourcePrefix} {source}</small> : null}
    </footer>
  </section>;
}

function DiscoverRunDrawer({ item, query, settled, onFollow, onPullIssues, onClose }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  const [preview, setPreview] = useState({ state: "loading", data: null, error: "" });
  const [mode, setMode] = useState("choose");
  const [selected, setSelected] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(settled === PULL_STATES.queued ? "Already on your Pull List." : "");
  const ids = runPreviewIds(item);
  const idsKey = JSON.stringify(ids);
  const source = ["metron", "comic_vine", "gcd"].find((provider) => ids[provider]);
  async function load() {
    setPreview({ state: "loading", data: null, error: "" });
    const params = new URLSearchParams({ ...ids, query: query || item.title || "" });
    try {
      setPreview({ state: "done", data: await apiRequest(`/api/v1/discover/run?${params}`), error: "" });
    } catch (error) {
      setPreview({ state: "error", data: null, error: error.message });
    }
  }
  useEffect(() => { load(); }, [idsKey]);
  const run = preview.data;
  const issues = run?.issues || [];
  // The preview's own reading outranks the search row's, which is often
  // silent about whether a run has ended.
  const status = run?.publicationStatus || discoveryRunStatus(item);
  const completed = status === "completed";
  // A mode the run no longer offers -- picked before the preview said it had
  // ended -- falls back to taking the whole run.
  const activeMode = completed && (mode === "follow" || mode === "released") ? "complete"
    : !completed && mode === "complete" ? "released" : mode;
  const summary = runPullSummary(activeMode, issues, [...selected], { following: Boolean(run?.following) });
  const [modesRef, modeGlass] = useGlassIndicator("button.active", [activeMode, preview.state]);
  function toggle(number) {
    setMode("choose");
    setDone("");
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(number)) next.delete(number); else next.add(number);
      return next;
    });
  }
  async function commit() {
    if (!run || summary.disabled || busy) return;
    setBusy(true);
    const target = {
      provider: run.provider, providerSeriesId: run.providerSeriesId,
      title: run.title, query: query || run.title,
    };
    const result = activeMode === "follow"
      ? await onFollow(target)
      : await onPullIssues(activeMode === "released" ? { ...target, released: true }
        : activeMode === "complete"
          ? { ...target, complete: true, numbers: completeRunToPull(issues).map((issue) => issue.number) }
          : { ...target, numbers: [...selected] });
    setBusy(false);
    if (result?.ok) {
      setDone(activeMode === "follow" ? "Following this run." : "On your Pull List.");
      setSelected(new Set());
      load();
    }
  }
  const yearLabel = item.yearLabel || run?.year || item.yearBegan;
  const art = run?.cover || item.cover;
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer comic-drawer discover-drawer ${closing ? "closing" : ""}`} ref={dialogRef}
      role="dialog" aria-modal="true" aria-labelledby="discover-run-title"
      onMouseDown={(event) => event.stopPropagation()}>
      <DiscoverDrawerHero art={art} titleId="discover-run-title" title={run?.title || item.title}
        cover={<DiscoverCover src={art} alt={`${item.title} cover`} glyph={30} />}
        byline={[run?.publisher || item.publisher, yearLabel].filter(Boolean).join(" • ")}
        onClose={requestClose} closeLabel="Close run details">
        <RunStatusChip status={run?.publicationStatus || discoveryRunStatus(item)} />
        {(run?.medium || item.medium) === "manga" ? <StatusBadge tone="muted">Manga</StatusBadge> : null}
        {run ? <StatusBadge tone="muted">{issues.length} issue{issues.length === 1 ? "" : "s"}</StatusBadge> : null}
      </DiscoverDrawerHero>
      {/* How much to take, as the run drawer's tab bar: the same violet bar and
          sliding glass, choosing one of three rather than a page. */}
      <div className="drawer-tabs comic-drawer-tabs discover-mode-tabs" role="radiogroup" aria-label="How much of this run to pull" ref={modesRef}>
        <span className="comic-drawer-tab-glass glass-indicator" aria-hidden="true" style={modeGlass || { opacity: 0 }} />
        {runModes(completed ? "completed" : status).map(([id, label]) =>
          <button type="button" role="radio" aria-checked={activeMode === id} className={activeMode === id ? "active" : ""}
            disabled={!run || busy} onClick={() => { setMode(id); setDone(""); }} key={id}>{label}</button>)}
      </div>
      <div className="comic-drawer-body">
      {preview.state === "loading" ? <RunSynopsis loading /> : <RunSynopsis text={run?.synopsis} source={run?.providerName} key={idsKey} />}
      {run?.detailsLimited ? <p className="discover-note">The Grand Comics Database lists this run&rsquo;s issue numbers without titles, dates or covers.</p> : null}
      <section className="discover-issue-list" aria-label="Issues in this run">
        <header>
          <h3>Issues</h3>
          {run && mode === "choose" ? <span>
            <button type="button" onClick={() => { setSelected(new Set(releasedToPull(issues).map((issue) => issue.number))); setDone(""); }}>Select all released</button>
            <button type="button" onClick={() => { setSelected(new Set()); setDone(""); }} disabled={!selected.size}>Clear</button>
          </span> : null}
        </header>
        {preview.state === "loading" ? <div role="status" aria-busy="true">
          <p className="discover-note">Getting the issue list from {PREVIEW_PROVIDER_NAMES[source] || "the catalogs"}&hellip; a long run takes a little while the first time.</p>
          {[0, 1, 2, 3, 4, 5].map((row) => <div className="discover-issue-row discover-issue-skeleton" aria-hidden="true" key={row}>
            <i /><span className="discover-cover" /><span><i /><i /></span>
          </div>)}
        </div> : null}
        {preview.state === "error" ? <div className="shelf-message">
          <WarningCircle size={18} />
          <span><strong>This run&rsquo;s issues could not be listed</strong><small>{preview.error}</small></span>
          <button type="button" onClick={load}>Try again</button>
        </div> : null}
        {preview.state === "done" ? issues.map((issue, index) => {
          const available = selectableIssue(issue);
          const note = issue.owned ? "In library" : issue.queued ? "On Pull List"
            : issue.releaseState === "upcoming" ? "Not out yet"
            : issue.releaseState === "unknown" ? "Release date unknown" : null;
          const date = formatLongDate(issue.publicationDate) || issue.publicationYear;
          return <label className={`discover-issue-row${available ? "" : " unavailable"}`} key={`${issue.number}-${index}`}>
            <input type="checkbox" checked={selected.has(issue.number)} disabled={!available || busy}
              onChange={() => toggle(issue.number)} aria-label={`Issue ${issue.number}`} />
            <DiscoverCover src={issue.cover} alt="" glyph={16} />
            <span>
              <strong>{issueLabel(issue.number, run?.medium || item.medium)}{issue.title ? ` · ${issue.title}` : ""}</strong>
              <small>{[date, note].filter(Boolean).join(" · ") || "\u00a0"}</small>
            </span>
          </label>;
        }) : null}
      </section>
      </div>
      <div className="discover-drawer-actions">
        <small role="status" aria-live="polite">{done || summary.detail}</small>
        <button type="button" className="pull-button pull-button-md pull-button-idle"
          disabled={!run || summary.disabled || busy} onClick={commit} aria-busy={busy || undefined}>
          <span>{busy ? "Working…" : summary.label}</span>
          {busy ? <LoadingIndicator size={16} /> : <PullIcon />}
        </button>
      </div>
    </aside>
  </div>;
}

// The design labels a shelf by its shipping Wednesday, in the reader's own
// notation rather than the ISO date the endpoint speaks.
function formatShelfDate(iso) {
  if (!iso) return "";
  const [year, month, day] = String(iso).split("-");
  return year && month && day ? `${month}/${day}/${year}` : "";
}

function CatalogEmpty({ onAdd }) {
  return <div className="empty-state"><Books size={35} weight="duotone" /><strong>Your library is ready for its first comics</strong><span>Choose a folder to scan without changing your files.</span><button className="primary-button" onClick={onAdd}><FolderOpen size={18} /> Import library</button></div>;
}

// A library scan started from the app, in progress or just finished: on the
// folders page and in Settings. The rail's button shows it as a turning icon.
function ScanProgress({ scanState, scanProgress }) {
  const scanTotal = Number(scanProgress?.total_files || 0);
  const scanProcessed = Number(scanProgress?.processed_files || 0);
  const scanPercent = scanTotal ? Math.min(100, Math.round((scanProcessed / scanTotal) * 100)) : 0;
  if (scanState === "done") return <div className="success-banner"><CheckCircle size={19} weight="fill" /> Scan complete. Your library is up to date.</div>;
  if (scanState !== "scanning") return null;
  return <div className={`scan-progress ${scanTotal ? "determinate" : ""}`} aria-live="polite"><span><strong>{scanTotal ? `Scanning comic ${Math.min(scanProcessed + 1, scanTotal)} of ${scanTotal}` : "Finding comic files…"}</strong><small>{scanProgress?.rootTotal > 1 ? `Folder ${scanProgress.rootIndex} of ${scanProgress.rootTotal} · ${scanProgress.rootPath}` : scanTotal ? `${scanProcessed} complete · reading embedded details, cover art, and file health` : "Counting files before the library scan begins"}</small></span><b>{scanTotal ? `${scanPercent}%` : "Starting"}</b><i style={scanTotal ? { width: `${scanPercent}%` } : undefined} /></div>;
}

function ImportLibraryView({ onNavigate, onStartInventory, onScanLibrary, onUpdateRoot, onRemoveRoot, catalog, backendStatus, scanState, scanProgress }) {
  const [path, setPath] = useState("");
  const [recursive, setRecursive] = useState(true);
  const [pickerState, setPickerState] = useState("");
  const [editingRoot, setEditingRoot] = useState(null);
  const [rootRecursive, setRootRecursive] = useState(true);
  const busy = scanState === "scanning";
  const roots = catalog?.roots || [];
  const lastScan = catalog?.lastScan?.iso ? `${catalog.lastScan.date} ${catalog.lastScan.time}` : "Not scanned yet";
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
  return <><PageHeader title="Import library"><button className="ghost-button page-back-action" onClick={() => onNavigate("settings")}><ArrowLeft size={18} /> Back to Settings</button></PageHeader>
    <ScanProgress scanState={scanState} scanProgress={scanProgress} />
    {catalogPending(catalog, backendStatus) ? <CatalogLoading title="Loading your library folders…" detail="Checking which folders Flipparr already scans." /> : null}
    {roots.length ? <section className="library-sources-panel"><header><div><span className="eyebrow">Library folders</span><h2>Your comic sources</h2><p>Each folder is scanned independently. Removing one from Flipparr never deletes or moves its files.</p></div><div className="library-scan-action"><span>Last library scan</span><strong>{lastScan}</strong><button className={`primary-button ${busy ? "loading" : ""}`} onClick={onScanLibrary} disabled={busy}>{busy ? <LoadingSpinner size={19} /> : <ArrowsClockwise size={19} />}{busy ? "Scanning…" : "Scan all folders"}</button></div></header><div className="library-source-list">{roots.map((root) => <article className="library-source" key={root.id}><span className="library-source-icon"><FolderOpen size={22} weight="duotone" /></span><div className="library-source-copy"><strong>{root.path}</strong><span>{root.recursive ? "Includes subfolders" : "Top-level comics only"} · {rootScanLabel(root.last_scan_at)}</span></div><div className="library-source-actions"><button className="ghost-button" disabled={busy} onClick={() => onStartInventory(root.path, Boolean(root.recursive))}><ArrowsClockwise size={16} /> Scan</button><button className="ghost-button" disabled={busy} onClick={() => { setEditingRoot(editingRoot === root.id ? null : root.id); setRootRecursive(Boolean(root.recursive)); }}><PencilSimple size={16} /> Manage</button></div>{editingRoot === root.id ? <div className="library-source-editor"><label className="check-row"><input type="checkbox" checked={rootRecursive} onChange={(event) => setRootRecursive(event.target.checked)} /><span><strong>Include subfolders</strong><small>Apply this setting on future scans</small></span></label><div><button className="secondary-button" onClick={async () => { await onUpdateRoot(root, rootRecursive); setEditingRoot(null); }}>Save setting</button><button className="danger-button" onClick={() => removeRoot(root)}>Remove from Flipparr</button></div><small>To change the folder path, add the new folder below, then remove this source.</small></div> : null}</article>)}</div></section> : null}
    <section className="focused-panel add-panel"><div className="panel-icon"><FolderOpen size={30} weight="duotone" /></div><h2>Add another library folder</h2><p>We’ll inventory the issues and volumes already in this folder, use covers and metadata from the files, and check for damaged archives. Online details can be refreshed after the library is visible.</p><label className="form-field"><span>Library folder</span><div className="path-input"><input value={path} placeholder="/comics-archive" onChange={(event) => setPath(event.target.value)} /><button type="button" onClick={chooseFolder}>Choose folder</button></div>{pickerState ? <small>{pickerState}</small> : null}</label><label className="check-row"><input type="checkbox" checked={recursive} onChange={(event) => setRecursive(event.target.checked)} /><span><strong>Include subfolders</strong><small>Useful when each series has its own folder</small></span></label><div className="safety-note"><ShieldCheck size={22} weight="fill" /><span><strong>Your files stay untouched</strong><small>No files will be renamed, moved, or modified during this scan.</small></span></div><div className="docker-path-note"><HardDrive size={20} /><span><strong>Using Docker?</strong><small>Mount each NAS share into the Flipparr container first, then enter its container path here. Avoid adding a folder inside an existing source.</small></span></div><div className="panel-actions"><button className={`primary-button ${busy ? "loading" : ""}`} disabled={busy || !path.trim()} onClick={() => onStartInventory(path, recursive)}>{busy ? <LoadingSpinner size={19} /> : <UploadSimple size={19} />} {busy ? "Scanning…" : "Import and scan folder"}</button><button className="ghost-button" onClick={() => onNavigate("settings")}>Cancel</button></div></section></>;
}

function RequestsView({ catalog, backendStatus, focus, onCancelReplacement, onDeletePull, onRefresh }) {
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
  // Four tabs for the four states, and every request in exactly one of them.
  // The classifier lives in src/pull-list.js because this screen cannot be
  // exercised on a library with no requests, which is how two bugs reached
  // production; there it is testable without a browser.
  const buckets = useMemo(() => groupPullList(catalog), [catalog]);
  // A notification about a failed download lands here, so the screen has to
  // put the reader in front of that request rather than on whichever tab they
  // last used. A failure is always on a live request, which is Wanted.
  const aimedAt = useRef(null);
  useEffect(() => {
    if (!focus || aimedAt.current === focus) return;
    aimedAt.current = focus;
    // Ask which tab actually holds the row rather than assuming: a failure or
    // an arrival is exactly the case that is not on Wanted.
    const all = [...(catalog?.requests || []), ...(catalog?.replacementRequests || [])];
    const target = all.find((request) => String(request.id) === String(focus.requestId));
    setTab(classifyRequest(target) || "wanted");
    // After the tab renders, not before: the row does not exist until then.
    const key = `${focus.kind === "replacement" ? "replacement" : "series"}-${focus.requestId}`;
    const timer = setTimeout(() => {
      const row = document.querySelector(`[data-request="${key}"]`);
      if (!row) return;
      row.scrollIntoView({ block: "center", behavior: "smooth" });
      row.querySelector(".request-expand")?.click();
      row.classList.add("request-aimed");
      setTimeout(() => row.classList.remove("request-aimed"), 2200);
    }, 60);
    return () => clearTimeout(timer);
  }, [focus]);
  const entries = buckets[tab] ?? [];
  const tabCopy = PULL_LIST_COPY[tab];
  const loading = catalogPending(catalog, backendStatus);
  return <><PageHeader title="Pull List"><button type="button" className="primary-button search-missing-button" onClick={() => searchMissing(false)} disabled={searchingMissing} aria-busy={searchingMissing}>{searchingMissing ? <LoadingSpinner size={16} /> : <MagnifyingGlass size={16} weight="bold" />} Search for missing</button></PageHeader><SegmentedTabs label="Pull List" value={tab} onChange={setTab} items={PULL_LIST_TABS.filter(({ id }) => id !== "failed" || tabCount(buckets.failed)).map(({ id, label }) => ({ id, label, className: id === "failed" ? "request-tab-failed" : "", count: loading ? null : <b>{tabCount(buckets[id])}</b> }))} /><p className="request-tab-description">{tabCopy.description}</p>{pendingSearch ? <div className="request-search-confirm" role="alertdialog"><div><strong>{pendingSearch.detail}</strong><small>Downloads start immediately, one for every issue listed.</small></div><span><button type="button" className="ghost-button" onClick={() => setPendingSearch(null)}>Cancel</button><button type="button" className="primary-button" disabled={searchingMissing} onClick={() => searchMissing(true)}>{searchingMissing ? <LoadingSpinner size={17} /> : <CloudArrowDown size={17} />} Start downloads</button></span></div> : null}{searchMissingMessage ? <p className="request-search-result" role="status">{searchMissingMessage}</p> : null}<section className="request-list">{loading ? <CatalogLoading title="Loading your pull list…" detail="Bringing in followed runs, wanted issues, and downloads." /> : entries.length ? entries.map(({ kind, request }) => kind === "replacement"
    ? <ReplacementRequestRow request={request} progress={progress} openByDefault={tab === "failed" || tab === "downloading"} onCancel={onCancelReplacement} onFindRelease={setReleaseJob} onRefresh={onRefresh} key={`replacement-${request.id}`} />
    : <RequestRow request={request} tab={tab} progress={progress} openByDefault={tab === "failed" || tab === "downloading"} onFindRelease={setReleaseJob} onRefresh={onRefresh} onDelete={onDeletePull} key={`series-${request.id}`} />) : <div className="empty-state request-empty"><CheckCircle size={34} weight="duotone" /><strong>{tabCopy.emptyTitle}</strong><span>{tabCopy.emptyDetail}</span></div>}</section>{releaseJob ? <ReleaseSearchModal job={releaseJob} onClose={() => setReleaseJob(null)} onGrabbed={async () => { await onRefresh?.(); setReleaseJob(null); }} /> : null}</>;
}

function acquisitionFailureDetails(job) {
  const technical = String(job?.downloadError || "").trim();
  const normalized = technical.toLowerCase();
  if (job?.downloadFailureStage === "import") return {
    label: "Import failed",
    message: "The download finished, but Flipparr could not validate or add the comic to your library. The original file is still active.",
    technical,
  };
  // The file said it was something else, so the release is not used again.
  if (job?.downloadFailureStage === "content") return {
    label: "Wrong comic",
    message: "The release held a different comic than this issue, so it will not be used again. Its files are kept for a week; the technical details say what was found.",
    technical,
  };
  // Parts of the posted release are missing, so downloading it again gives
  // the same holes; it is not grabbed again.
  if (job?.downloadFailureStage === "damaged") return {
    label: "Incomplete release",
    message: "Parts of this release are missing from the Usenet server, so it cannot be used and will not be grabbed again. Another release, or another indexer, is needed; the technical details say how much was missing.",
    technical,
  };
  // Nothing proved it wrong -- it could not be read or identified -- so the
  // release is only set aside for a day.
  if (job?.downloadFailureStage === "unidentified") return {
    label: "Couldn't confirm the issue",
    message: "Flipparr could not confirm this download is the issue, so it was set aside rather than filed. Its files are kept for a week and the release is tried again tomorrow; the technical details say what was read.",
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

function ReplacementRequestRow({ request, progress = {}, openByDefault = false, onCancel, onFindRelease, onRefresh }) {
  const [expanded, setExpanded] = useState(openByDefault);
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
  return <article data-request={`replacement-${request.id}`} className={`request-card replacement-request-card ${expanded ? "expanded" : ""}`}><button type="button" className="request-row" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}><span className="request-cover"><SeriesCover series={display} decorative /></span><span className="request-identity"><strong>{title}</strong><small>{scope}</small><small>{request.targetTitle !== title ? `${request.targetTitle} · ` : ""}{request.filename} · Added {request.requestedDate} {request.requestedTime}</small><span className="tag-line"><StatusBadge tone={tone}>{statusLabel}</StatusBadge></span></span><CaretDown className="request-caret" size={20} aria-hidden="true" /></button>{expanded ? <div className="request-job-panel"><header><div><strong>{request.status === "fulfilled" ? "Replacement complete" : "Comics needed for this replacement"}</strong><span>{request.status === "fulfilled" ? "The verified replacement is active and the original is held in recoverable quarantine." : "Flipparr searches and grabs the best match for each issue. The original stays active until every replacement passes validation."}</span></div>{!["fulfilled", "cancelled"].includes(request.status) ? <button className="ghost-button" onClick={() => onCancel(request)}>Cancel request</button> : null}</header>{jobs.length ? <div className="request-jobs">{jobs.map((job) => { const displayStatus = job.downloadStatus || job.status; const imported = job.downloadStatus === "imported"; const failedJob = job.status === "failed" || job.downloadStatus === "failed"; const canSearch = !job.downloadStatus && !["grabbed", "fulfilled", "cancelled"].includes(job.status); const retryMessage = retryError?.jobId === job.id ? retryError.message : null; const failure = failedJob ? acquisitionFailureDetails(job) : null; const detail = retryMessage || (!failedJob ? job.downloadTitle : null); return <div className="request-job" key={job.id}><b>{issueLabel(job.issueNumber, request.medium)}</b><div><strong>{job.issueTitle || `Issue ${job.issueNumber}`}</strong>{imported ? null : <span>{job.reason}</span>}{failure ? <div className="job-failure-copy"><strong>{failure.label}</strong><small>{failure.message}</small>{failure.technical ? <details><summary>Technical details</summary><code>{failure.technical}</code></details> : null}</div> : detail ? <span className={retryMessage ? "job-error" : ""}>{detail}</span> : null}<JobProgress entry={progress[String(job.id)]} /></div><span className="request-job-actions"><span className={`job-state ${displayStatus}`}>{DOWNLOAD_STATUS_LABELS[job.downloadStatus] || JOB_STATUS_LABELS[job.status] || displayStatus}</span>{failedJob ? <><button type="button" disabled={retryingJobId === job.id} onClick={() => retryJob(job)}>{retryingJobId === job.id ? <LoadingSpinner size={14} /> : <ArrowsClockwise size={14} />} {job.downloadFailureStage === "import" ? "Retry import" : "Try next release"}</button><button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button></> : !imported && canSearch ? <button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button> : null}</span></div>; })}</div> : <div className="request-job-empty"><WarningCircle size={20} /><div><strong>No safe issue targets are available</strong><span>Confirm the comic’s issue contents before replacing it.</span></div></div>}</div> : <footer className="replacement-safety-note"><ShieldCheck size={16} weight="fill" /> The current comic stays in your library until all mapped replacements are downloaded and verified.</footer>}</article>;
}

function RequestRow({ request, tab, progress = {}, openByDefault = false, onFindRelease, onRefresh, onDelete }) {
  const [expanded, setExpanded] = useState(openByDefault);
  const [deleting, setDeleting] = useState(null);
  const [retryingJobId, setRetryingJobId] = useState(null);
  const [retryError, setRetryError] = useState(null);
  const [uploadingJobId, setUploadingJobId] = useState(null);
  const [uploadError, setUploadError] = useState(null);
  const jobs = request.jobs || [];
  // A comic found by hand had nowhere to go: it had to be dropped in the
  // library folder and waited for, with nothing tying it to the wanted issue.
  async function uploadForJob(job, file) {
    if (!file) return;
    setUploadingJobId(job.id);
    setUploadError(null);
    try {
      await apiRequest(`/api/v1/acquisition-jobs/${job.id}/import`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-Filename": file.name },
        body: file,
      });
      onRefresh?.();
    } catch (error) {
      setUploadError({ jobId: job.id, message: error.message });
    }
    setUploadingJobId(null);
  }
  // A run is on every tab it has issues for. On one, the row speaks for the
  // issues listed there, so its badge does not say Failed on Wanted.
  const tabJobs = jobsForTab(request, tab);
  const counted = ["failed", "downloading", "wanted"].includes(tab) ? tabJobs.jobs : jobs;
  const ready = request.wantedIssueCount || 0;
  const upcoming = request.upcomingIssueCount || 0;
  const unknown = request.unknownReleaseIssueCount || 0;
  const queued = counted === jobs ? request.queuedJobCount || 0
    : counted.filter((job) => job.status === "queued").length;
  const searching = counted.filter((job) => job.status === "searching").length;
  const downloading = counted.filter((job) => ["queued", "downloading"].includes(job.downloadStatus)).length;
  const importing = counted.filter((job) => ["completed", "importing", "waiting_for_files"].includes(job.downloadStatus)).length;
  const failed = counted.filter((job) => job.status === "failed" || job.downloadStatus === "failed").length;
  // A run reaches Acquired after it was unfollowed, so the row can no longer
  // assume it is being watched. Cancelled is the only status that means that;
  // a fulfilled request is still followed, which is what the Comics grid says.
  // A request for named issues never followed anything -- pulling one comic
  // from Discover is not taking on its run.
  const pulled = request.coverage === "issues";
  const following = !pulled && request.status !== "cancelled";
  const scope = [
    pulled ? `${request.targetIssueCount === 1 ? "One issue" : `${request.targetIssueCount} issues`} pulled from Discover · not following this run` : following ? `Following · ${request.publicationStatus === "ongoing" ? "checks daily for newly listed issues" : request.publicationStatus === "completed" ? "completed run" : "checks daily in case it continues"}` : request.publicationStatus === "completed" ? "Completed run" : "Nothing is being looked for",
    ACQUISITION_LABELS[request.acquisitionPreference] || ACQUISITION_LABELS.either,
    unknown ? `${unknown} release date${unknown === 1 ? "" : "s"} unknown` : null,
  ].filter(Boolean).join(" · ");
  // A comic pulled before it ships has no job yet, so it is listed from the
  // request's own issues -- otherwise there is nothing to see or delete.
  const waiting = waitingIssues(request);
  // With one issue, deleting it and deleting the pull are the same click.
  const perIssueDelete = pulled && request.targetIssueCount > 1;
  const status = pulled ? (request.status === "fulfilled" ? "Arrived" : upcoming && !jobs.length ? "Waiting for release" : "Pulled issue")
    : !following ? "Not following" : request.status === "fulfilled" ? "Up to date" : failed ? requestFailureStatus(jobs) : importing ? `${importing} adding to library` : downloading ? `${downloading} downloading` : searching ? "Searching" : queued ? `${queued} wanted` : upcoming ? "Waiting for release" : "Checking release dates";
  const tone = pulled ? (request.status === "fulfilled" ? "green" : "violet")
    : !following ? "muted" : request.status === "fulfilled" ? "green" : failed ? "red" : queued || searching || downloading || importing ? "violet" : upcoming ? "green" : "muted";
  const display = { id: `request-${request.id}`, title: request.title, cover: request.cover };
  // The bar is the Comics card's: owned out of the issues this pull covers.
  const ownership = {
    owned: request.ownedIssueCount || 0,
    total: Math.max(request.targetIssueCount || 0, 1),
    status: request.ownedIssueCount >= request.targetIssueCount ? "complete" : "partial",
    releaseSummary: { upcoming, releasedMissing: ready },
  };
  const scopeNote = pulled ? "Pulled from Discover" : [ready ? `${ready} missing` : null, upcoming ? `${upcoming} upcoming` : null].filter(Boolean).join(" · ");
  // An issue already in the library is not a missing issue. Listing its job
  // under "Missing issues" put "Added to library" in the middle of a list of
  // things still being looked for.
  const outstandingJobs = tabJobs.jobs;
  // ...but on a row that has nothing outstanding, those same jobs are the only
  // thing there is to show, and on Acquired they are the reason the row is
  // there at all. So the panel falls back to what arrived rather than to an
  // empty state claiming Flipparr is still checking release dates.
  const panelJobs = outstandingJobs.length ? outstandingJobs : jobs.filter(arrivalAt);
  const jobGroups = panelJobs.reduce((groups, job) => {
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
  async function remove(issue) {
    setDeleting(issue ? issue.id : "all");
    try { await onDelete?.(request, issue); } finally { setDeleting(null); }
  }
  const deleteButton = (issue) => <button type="button" className="request-job-delete" disabled={deleting !== null} onClick={() => remove(issue)} aria-label={`Delete #${issue.number} from the Pull List`}>{deleting === issue.id ? <LoadingSpinner size={14} /> : <Trash size={14} />} {deleting === issue.id ? "Deleting…" : "Delete"}</button>;
  return <article data-request={`series-${request.id}`} className={`request-card ${expanded ? "expanded" : ""}`}>
    {/* The Comics list's card: cover, title and year, publisher, the run's
        badges and its ownership bar. What the row is doing and why sits in
        the panel it opens. */}
    <button type="button" className="request-row" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
      <span className="request-cover"><SeriesCover series={display} decorative /></span>
      <span className="request-identity">
        <strong>{request.title}{request.year ? <> <em>({request.year})</em></> : null}</strong>
        <small>{[request.publisher, scopeNote].filter(Boolean).join(" · ")}</small>
        <span className="tag-line"><PublicationStatus series={request} />{following ? <span className="monitoring-status"><FollowedIcon size={null} /> Following</span> : null}<StatusBadge tone={tone}>{status}</StatusBadge></span>
        <span className="request-ownership"><Ownership series={ownership} compact /></span>
      </span>
      <CaretDown className="request-caret" size={20} aria-hidden="true" />
    </button>
    {expanded ? <div className="request-job-panel">
      <header><div><strong>{pulled ? "Pulled from Discover" : !following ? "What arrived" : request.status === "fulfilled" ? "Run is up to date" : "Missing issues"}</strong><span>{pulled ? "You asked for these issues by name. The rest of the run is not being looked for." : !following ? "You stopped following this run. Everything already in your library stays there; nothing new is looked for." : request.status === "fulfilled" ? (request.publicationStatus === "completed" ? "Every issue in this completed run is in your library." : "Flipparr will keep checking this run and add newly released issues to Wanted.") : "Flipparr searches for each missing issue and grabs the best match. Anything it cannot decide waits here for you."}</span><small>{scope}</small></div>{pulled && canDeletePull(request) ? <button type="button" className="ghost-button" disabled={deleting !== null} onClick={() => remove(null)}>{deleting === "all" ? "Deleting…" : "Delete request"}</button> : null}</header>
      {Object.keys(tabJobs.elsewhere).length ? <p className="request-jobs-note">
        Also in this run: {Object.entries(tabJobs.elsewhere).map(([where, count]) => `${count} ${where}`).join(" · ")}.
      </p> : null}
      {panelJobs.length || waiting.length ? <div className="request-jobs">{Object.values(jobGroups).map((group) => <section className="request-job-group" key={group.id}>
        <header><span>Series run</span><strong>{group.title}</strong><b>{group.jobs.length} issue{group.jobs.length === 1 ? "" : "s"}</b></header>
        {group.jobs.map((job) => { const displayStatus = job.downloadStatus || job.status; const imported = job.downloadStatus === "imported"; const failedJob = job.status === "failed" || job.downloadStatus === "failed"; const relativeDestination = job.downloadDestination?.split("/comics/").pop(); const retryMessage = retryError?.jobId === job.id ? retryError.message : null; const failure = failedJob ? acquisitionFailureDetails(job) : null; const displayDetail = retryMessage || (!failedJob ? (relativeDestination ? `Library: ${relativeDestination}` : job.downloadTitle) : null); const canSearch = !job.downloadStatus && !["grabbed", "fulfilled", "cancelled"].includes(job.status); const retryLabel = job.downloadFailureStage === "import" ? "Retry import" : "Try next release"; return <div className="request-job" key={job.id}>
          <b>{issueLabel(job.issueNumber, request.medium)}</b>
          <div><strong>{job.issueTitle || `Issue ${job.issueNumber}`}</strong>{imported ? null : <span>{job.reason}</span>}{job.deliveredWithCount > 0 ? <span className="job-pack-note">{job.deliveredWithCount} more issue{job.deliveredWithCount === 1 ? "" : "s"} came from this download</span> : null}{uploadError?.jobId === job.id ? <span className="job-error" role="alert">{uploadError.message}</span> : null}{failure ? <div className="job-failure-copy"><strong>{failure.label}</strong><small>{failure.message}</small>{failure.technical ? <details><summary>Technical details</summary><code>{failure.technical}</code></details> : null}</div> : displayDetail ? <span className={retryMessage ? "job-error" : ""}>{displayDetail}</span> : null}<JobProgress entry={progress[String(job.id)]} /></div>
          <span className="request-job-actions"><span className={`job-state ${displayStatus}`}>{DOWNLOAD_STATUS_LABELS[job.downloadStatus] || JOB_STATUS_LABELS[job.status] || displayStatus}</span>{failedJob ? <><button type="button" disabled={retryingJobId === job.id} onClick={() => retryJob(job)}>{retryingJobId === job.id ? <LoadingSpinner size={14} /> : <ArrowsClockwise size={14} />} {retryingJobId === job.id ? "Retrying…" : retryLabel}</button><button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button></> : !imported && canSearch ? <button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button> : null}{!imported ? <label className={`job-upload ${uploadingJobId === job.id ? "busy" : ""}`}><input type="file" accept=".cbz,.cbr,.cbt,.cb7,.pdf,.epub" disabled={uploadingJobId === job.id} onChange={(event) => { const [file] = event.target.files || []; event.target.value = ""; uploadForJob(job, file); }} />{uploadingJobId === job.id ? <LoadingSpinner size={14} /> : <UploadSimple size={14} />} {uploadingJobId === job.id ? "Adding…" : "Upload a file"}</label> : null}{perIssueDelete && canDeleteJob(job) ? deleteButton({ id: job.issueId, number: job.issueNumber }) : null}</span>
        </div>; })}
      </section>)}{waiting.length ? <section className="request-job-group">
        <header><span>Not out yet</span><strong>{request.title}</strong><b>{waiting.length} issue{waiting.length === 1 ? "" : "s"}</b></header>
        {waiting.map((issue) => <div className="request-job" key={`waiting-${issue.id}`}>
          <b>{issueLabel(issue.number, request.medium)}</b>
          <div><strong>{issue.title || `Issue ${issue.number}`}</strong><span>{issue.publicationDate ? `Out ${formatLongDate(issue.publicationDate)} · searched for once it ships` : "Release date not known yet · searched for once it ships"}</span></div>
          <span className="request-job-actions"><span className="job-state waiting">Waiting for release</span>{perIssueDelete ? deleteButton(issue) : null}</span>
        </div>)}
      </section> : null}</div> : <div className="request-job-empty"><ArrowsClockwise size={20} /><div><strong>{following ? "Nothing is ready to search yet" : "Nothing is being looked for"}</strong><span>{following ? "No action is required. Flipparr will keep checking release dates automatically." : "Follow the run again from Comics to have Flipparr check it."}</span></div></div>}
      {!pulled && (upcoming || unknown) ? <footer>{upcoming ? `${upcoming} upcoming issue${upcoming === 1 ? " is" : "s are"} being followed` : null}{upcoming && unknown ? " · " : null}{unknown ? `${unknown} issue${unknown === 1 ? " has" : "s have"} an unknown release date` : null}</footer> : null}
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
  // Nothing offered is not the same as nothing found: say which, and why.
  const summary = releaseSearchSummary(result, `#${job.issueNumber}`);
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal release-search-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="release-search-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close release search"><X size={20} /></button><span className="eyebrow">Find one missing issue</span><h2 id="release-search-title">{job.seriesTitle} #{job.issueNumber}</h2><p className="workbench-intro">Compare the results below. Nothing is downloaded until you choose a release.</p><form className="release-query" onSubmit={(event) => { event.preventDefault(); search(query); }}><MagnifyingGlass size={16} /><input value={query} onChange={(event) => { setQuery(event.target.value); setEdited(true); }} aria-label="Search terms" placeholder="Series and issue to search for…" disabled={loading} /><button type="submit" className="secondary-button" disabled={loading || query.trim().length < 2}>{loading ? <LoadingSpinner size={16} /> : null} Search</button></form>{edited ? null : <p className="release-query-note">Flipparr widens this automatically when a narrower wording finds nothing. Edit it to search for something else.</p>}{loading ? <div className="release-loading"><LoadingSpinner size={24} /><div><strong>Searching your indexers…</strong><span>This can take a few seconds.</span></div></div> : null}{error ? <div className="release-error"><WarningCircle size={19} weight="fill" /><span><strong>Release search needs attention</strong>{error}</span><button type="button" onClick={() => search(query)}>Try again</button></div> : null}{!loading && !error && !candidates.length ? <div className="release-empty"><MagnifyingGlass size={28} /><strong>{summary.headline}</strong><span>{summary.detail}</span>{summary.setAside.length ? <ul className="release-set-aside">{summary.setAside.map((item) => <li key={item.title}><b title={item.title}>{item.title}</b><small>{item.reason}{item.copies > 1 ? ` · listed by ${item.copies} indexers` : ""}</small></li>)}</ul> : null}<button type="button" className="secondary-button" onClick={() => search(query)}>Search again</button></div> : null}{candidates.length ? <div className="release-candidates"><header><div><strong>{candidates.length} candidate{candidates.length === 1 ? "" : "s"}</strong><span>Best matches appear first. Confirm the title, issue, language, and format.</span></div></header>{candidates.map((candidate) => { const isGrabbing = grabbingId === candidate.id; return <article className="release-candidate" key={candidate.id}><div className="release-candidate-main"><StatusBadge tone={candidate.matchScore >= 85 ? "green" : "amber"}>{candidate.matchStrength}</StatusBadge><h3>{candidate.title}</h3><p>{candidate.indexer} · {candidate.protocol} · {formatReleaseSize(candidate.sizeBytes)}{candidate.publishDate ? ` · ${new Date(candidate.publishDate).toLocaleDateString()}` : ""}</p>{candidate.formatTags?.length ? <div className="release-tags">{candidate.formatTags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}</div><div className="release-match"><strong>{candidate.matchScore}</strong><span>match score</span></div><ul>{candidate.matchReasons.map((reason) => <li key={reason}><CheckCircle size={14} weight="fill" />{reason}</li>)}{candidate.grabbable === false && candidate.grabHint ? <li className="release-candidate-hint"><WarningCircle size={14} />{candidate.grabHint}</li> : null}</ul><button type="button" className={`primary-button ${isGrabbing ? "loading" : ""}`} aria-busy={isGrabbing} disabled={Boolean(grabbingId) || candidate.grabbable === false} title={candidate.grabbable === false ? candidate.grabHint : undefined} onClick={() => grab(candidate)}>{isGrabbing ? <LoadingSpinner size={17} /> : <CloudArrowDown size={17} />}{isGrabbing ? "Sending…" : candidate.grabbable === false ? "Not fetchable yet" : "Send to SABnzbd"}</button></article>; })}</div> : null}<footer className="release-modal-footer"><ShieldCheck size={17} weight="fill" /> Prowlarr download links stay on the server and are never exposed in this page.</footer></section></div>;
}

function MetadataView({ items, loading = false, focus, backendStatus, onResolve, onReplace }) {
  const entries = items.length ? items : (backendStatus === "offline" ? DEMO_META_ITEMS : []);
  const [selected, setSelected] = useState(0);
  // Aim once per bell click, not once per catalog poll: the list refreshes
  // every few seconds and would otherwise keep yanking the selection back to
  // the notification the user has already moved on from. Re-runs on `entries`
  // so a focus set before the inbox loaded still lands.
  const aimedAt = useRef(null);
  useEffect(() => {
    if (!focus || aimedAt.current === focus) return;
    const index = entries.findIndex((entry) => entry.id === focus.id);
    if (index < 0) return;
    aimedAt.current = focus;
    setSelected(index);
  }, [focus, entries]);
  const [resolved, setResolved] = useState([]);
  const [mobileBrowseOpen, setMobileBrowseOpen] = useState(false);
  const selectedIndex = Math.min(selected, Math.max(entries.length - 1, 0));
  const item = entries[selectedIndex];
  const isResolved = resolved.includes(selectedIndex);
  if (loading) return <CatalogLoading title="Checking your library health…" detail="Bringing in damaged files and matches waiting for review." />;
  if (!item) return <><div className="empty-state"><CheckCircle size={35} weight="duotone" /><strong>Your library looks healthy</strong><span>Damaged files and uncertain matches will appear here after a scan.</span></div></>;
  async function resolveCurrent() {
    if (item.path && item.code && item.fingerprint) await onResolve(item);
    else setResolved((current) => [...current, selectedIndex]);
  }
  const fileProblem = item.category === "file" || ["empty_archive", "no_image_pages", "corrupt_archive", "file_health"].includes(item.code);
  function selectProblem(index) {
    setSelected(index);
    setMobileBrowseOpen(false);
  }
  return <><div className="metadata-layout"><nav className="mobile-inbox-nav" aria-label="Navigate library problems"><button type="button" disabled={selectedIndex === 0} onClick={() => selectProblem(selectedIndex - 1)} aria-label="Previous problem"><ArrowLeft size={18} /></button><button type="button" className="mobile-inbox-browser" aria-expanded={mobileBrowseOpen} onClick={() => setMobileBrowseOpen((open) => !open)}><ListBullets size={18} /><span><strong>Problem {selectedIndex + 1} of {entries.length}</strong><small>{mobileBrowseOpen ? "Close problem list" : "Browse all problems"}</small></span></button><button type="button" disabled={selectedIndex === entries.length - 1} onClick={() => selectProblem(selectedIndex + 1)} aria-label="Next problem"><ArrowRight size={18} /></button></nav><aside className={`inbox-list ${mobileBrowseOpen ? "mobile-browse-open" : ""}`}><div className="inbox-label">Needs attention <b>{entries.length - resolved.length}</b></div>{entries.map((entry, index) => { const replacementOpen = entry.replacementStatus && !["fulfilled", "cancelled"].includes(entry.replacementStatus); return <button key={entry.id ?? `${entry.file}-${entry.issue}`} className={`${selectedIndex === index ? "active" : ""} ${resolved.includes(index) ? "resolved" : ""} ${replacementOpen ? "replacement-triaged" : ""}`} onClick={() => selectProblem(index)}>{replacementOpen ? <CheckCircle size={19} weight="fill" /> : <WarningCircle size={19} weight={entry.severity === "error" ? "fill" : "regular"} />}<span><strong>{entry.file}</strong><small>{resolved.includes(index) ? "Fixed" : replacementOpen ? "Replacement requested · original retained" : `${entry.category === "file" ? "Comic file" : "Metadata"} · ${entry.issue}`}</small></span></button>; })}</aside><section className="review-panel"><div className="review-heading"><StatusBadge tone={item.severity === "error" ? "red" : "amber"}>{fileProblem ? "Comic file problem" : "Metadata review"}</StatusBadge><h2>{item.file}</h2><p>{item.detail}</p></div>{fileProblem ? <FileProblem item={item} onReplace={onReplace} /> : item.code === "metadata_conflict" && item.comparison ? <MetadataComparison comparison={item.comparison} /> : <MetadataProblem item={item} />}<div className="review-actions"><button className={fileProblem ? "ghost-button" : "primary-button"} disabled={isResolved} onClick={resolveCurrent}><CheckCircle size={19} /> {isResolved ? "Fixed" : "Mark fixed"}</button>{!fileProblem ? <button className="ghost-button"><PencilSimple size={18} /> Edit metadata</button> : null}<button className="ghost-button">Dismiss</button></div></section></div></>;
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
  return <div className="file-problem"><HardDrive size={35} weight="duotone" /><div><strong>{item.issue}</strong><div className="file-problem-actions">{replacementOpen ? <span className={`replacement-requested-inline ${item.replacementStatus === "failed" ? "failed" : ""}`}><CheckCircle size={18} weight="fill" /> {replacementLabel}</span> : <button className="primary-button" onClick={() => onReplace(item)}><CloudArrowDown size={18} /> Replace comic</button>}<button onClick={revealFile} disabled={revealState.status === "loading"}>{revealState.status === "loading" ? <LoadingSpinner size={18} /> : <FolderOpen size={18} />} {revealState.status === "loading" ? "Opening Finder…" : "Show comic file"}</button></div><small className="replacement-help">{replacementOpen ? "The original remains active until every replacement is downloaded, validated, and added to your library. Progress and failures appear under Pull List." : "Add the mapped issues to your wanted list without deleting this file. It stays in place until every replacement is downloaded and verified."}</small>{revealState.message ? <small className={`file-reveal-status ${revealState.status}`}>{revealState.message}</small> : null}</div></div>;
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
  { id: "health", label: "Library health" },
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
        <BuiltInSources providers={providers} />
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

function SettingsView({ catalog, backendStatus, onNavigate, onAuthChanged, onSignOut, section, onSectionChange, health, onScanLibrary, scanState, scanProgress }) {
  const [collectedEditions, setCollectedEditions] = useState(false);
  const [savingCollectedEditions, setSavingCollectedEditions] = useState(false);
  const [language, setLanguage] = useState("en");
  const [savingLanguage, setSavingLanguage] = useState(false);
  const [autoScan, setAutoScan] = useState(true);
  const [autoScanInterval, setAutoScanInterval] = useState(60);
  const [savingAutoScan, setSavingAutoScan] = useState(false);
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
      setAutoScan(result?.autoScanEnabled ?? true);
      setAutoScanInterval(Number(result?.autoScanIntervalMinutes) || 60);
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
  // Background scans: whether, and how often. Shown at once, put back if the
  // save fails.
  async function saveAutoScan(patch) {
    const previous = { enabled: autoScan, interval: autoScanInterval };
    setSavingAutoScan(true);
    if ("autoScanEnabled" in patch) setAutoScan(patch.autoScanEnabled);
    if ("autoScanIntervalMinutes" in patch) setAutoScanInterval(patch.autoScanIntervalMinutes);
    try {
      const result = await apiRequest("/api/v1/settings", {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
      });
      setAutoScan(result?.autoScanEnabled ?? true);
      setAutoScanInterval(Number(result?.autoScanIntervalMinutes) || 60);
    } catch {
      setAutoScan(previous.enabled);
      setAutoScanInterval(previous.interval);
    } finally {
      setSavingAutoScan(false);
    }
  }
  useEffect(() => { loadProviders(); loadServices(); loadAppSettings(); }, []);
  const roots = catalog?.roots || [];
  // A scan started here, from the rail or folders page, or in the background.
  const scanning = scanState === "scanning" || Boolean(catalog?.activeScan);
  return <><PageHeader title="Settings" /><div className="settings-switcher"><SegmentedTabs label="Settings section" value={section} onChange={onSectionChange} items={SETTINGS_SECTIONS} /></div><div className={`settings-layout${section === "health" ? " settings-layout-wide" : ""}`}>{section === "health" ? <MetadataView {...health} /> : null}{section === "library" ? catalogPending(catalog, backendStatus) ? <CatalogLoading title="Loading your library folders…" detail="Checking which folders Flipparr already scans." /> : <section className="settings-library-folders"><header><div><h2>Library folders</h2><p>{roots.length ? `${roots.length} folder${roots.length === 1 ? "" : "s"} currently scanned for comics.` : "No library folders are configured yet."}</p>{roots.length ? <p className="settings-last-scan">{scanning ? "Scanning now" : catalog?.lastScan?.iso ? `Last scanned ${catalog.lastScan.date} at ${catalog.lastScan.time}` : "Not scanned yet"}</p> : null}</div><div className="settings-library-actions"><button className="secondary-button" onClick={() => onNavigate("import")}><FolderOpen size={18} /> Manage folders</button>{/* The rail's scan button is hidden on a phone; this is the one there. */}{roots.length ? <button className={`primary-button ${scanning ? "loading" : ""}`} onClick={onScanLibrary} disabled={scanning} aria-busy={scanning}>{scanning ? <LoadingSpinner size={18} /> : <ArrowsClockwise size={18} />}{scanning ? "Scanning…" : "Scan library"}</button> : null}</div></header><ScanProgress scanState={scanState} scanProgress={scanProgress} />{roots.length ? <div className="settings-root-list">{roots.map((root) => <span key={root.id}><FolderOpen size={17} /><strong>{root.path}</strong><small>{root.recursive ? "Includes subfolders" : "Top level only"}</small></span>)}</div> : null}{roots.length ? <div className="settings-auto-scan"><Toggle checked={autoScan} onChange={savingAutoScan ? () => {} : (next) => saveAutoScan({ autoScanEnabled: next })} title="Scan automatically" description="Checks your library folders in the background, so comics added outside Flipparr appear without a manual scan." />{autoScan ? <label className="form-field settings-language settings-scan-interval"><span>How often</span><select value={autoScanInterval} onChange={(event) => saveAutoScan({ autoScanIntervalMinutes: Number(event.target.value) })} disabled={savingAutoScan}><option value={15}>Every 15 minutes</option><option value={60}>Every hour</option><option value={360}>Every 6 hours</option><option value={1440}>Once a day</option></select></label> : null}</div> : null}</section> : null}{section === "matching" ? <section><h2>Matching and fixes</h2><aside className="provider-policy-note"><ShieldCheck size={19} weight="fill" /><span><strong>Matches are accepted automatically when the evidence is strong</strong><small>Flipparr scores every match from corroborating and conflicting evidence (filename, embedded metadata, provider agreement). Confident matches are applied without review; anything below that threshold, or with conflicting evidence, waits under Library health for you to confirm or fix.</small></span></aside><Toggle checked={collectedEditions} onChange={savingCollectedEditions ? () => {} : toggleCollectedEditions} title="Collected editions (trades, hardcovers, omnibuses)" description="Off by default. Turn on to browse and manage collected editions alongside Issues. Their metadata and file availability are less complete than Issues, and they are never used to fulfill Issue ownership or acquisition." /></section> : null}{section === "security" ? <SecuritySettings onChanged={onAuthChanged} onSignOut={onSignOut} /> : null}{section === "acquisition" ? <section className="metadata-source-settings acquisition-source-settings"><header><div><h2>Acquisition services</h2><p>Connect Prowlarr to find releases and SABnzbd to download the one you choose.</p></div></header><label className="form-field settings-language"><span>Language wanted</span><select value={language} onChange={(event) => changeLanguage(event.target.value)} disabled={savingLanguage}><option value="en">English</option><option value="fr">French</option><option value="es">Spanish</option><option value="de">German</option><option value="it">Italian</option><option value="pt">Portuguese</option><option value="ru">Russian</option><option value="ja">Japanese</option><option value="ko">Korean</option><option value="zh">Chinese</option><option value="pl">Polish</option><option value="nl">Dutch</option><option value="">No preference</option></select><small>A release that says it is another language is never grabbed, and one that says so only once downloaded is refused instead of filed under the issue it claims to be. Releases that say nothing are judged on the rest of the evidence.</small></label>{services.map((service) => <AcquisitionService service={service} onConfigure={() => setEditingService(service)} key={service.id} />)}{serviceError ? <p className="workbench-error" role="alert">{serviceError}</p> : null}</section> : null}{section === "metadata" ? <section className="metadata-source-settings"><header><div><h2>Metadata sources</h2><p>Built-in sources work immediately. Add API credentials for more issue titles, dates, covers, and matches.</p></div></header>{providers.filter((provider) => !provider.builtIn).map((provider) => <Provider provider={provider} onConfigure={() => setEditingProvider(provider)} key={provider.id} />)}<BuiltInSources providers={providers} />{providerError ? <p className="workbench-error" role="alert">{providerError}</p> : null}<aside className="provider-policy-note"><ShieldCheck size={19} weight="fill" /><span><strong>Your API credentials stay on this device</strong><small>Keys are hidden after saving and sent only to the service you configure.</small></span></aside></section> : null}</div>{editingProvider ? <ProviderSettingsModal provider={editingProvider} onClose={() => setEditingProvider(null)} onSaved={async () => { await loadProviders(); setEditingProvider(null); }} /> : null}{editingService ? <AcquisitionServiceSettingsModal service={editingService} onClose={() => setEditingService(null)} onSaved={async () => { await loadServices(); setEditingService(null); }} /> : null}</>;
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

// The sources that need no account, as one line after the ones that do: they
// have nothing to configure, so they sit below the real choices rather than
// as cards competing with them. Setup and Settings both use it.
function BuiltInSources({ providers }) {
  const builtIn = providers.filter((provider) => provider.builtIn);
  if (!builtIn.length) return null;
  return <p className="builtin-sources">
    <CheckCircle size={16} weight="fill" />
    <span>Already on, no account needed: {builtIn.map((provider) => provider.name).join(" and ")}.</span>
  </p>;
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

function GroupedIssueInventory({ issues, onEditIssue, medium }) {
  // Covers are why the tab exists, so posters lead. A long run is also less
  // scrolling this way than as tall rows: four across beats one down.
  const [view, setView] = useState("grid");
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
  // The same toggle and icons as the Comics toolbar, in the first run's
  // header rather than on a row of its own above it.
  const viewToggle = <div className="view-toggle" aria-label="Choose issue view">
    <button type="button" className={view === "grid" ? "active" : ""} onClick={() => setView("grid")} aria-label="Grid view" aria-pressed={view === "grid"}><GridViewIcon /></button>
    <button type="button" className={view === "list" ? "active" : ""} onClick={() => setView("list")} aria-label="List view" aria-pressed={view === "list"}><ListViewIcon /></button>
  </div>;
  return <div className={`grouped-issue-inventory ${view}`}>
    {groups.map((group, groupIndex) => {
    const owned = group.issues.filter((issue) => issue.ownership !== "unowned").length;
    return <section className="issue-run-group" key={group.key}>
      <header><div><span>{group.type === "specials" ? "Special / one-shot" : "Series run"}</span><h3>{group.title}</h3>{group.runTitle !== group.title || group.year ? <small>{[group.runTitle !== group.title ? group.runTitle : null, group.year].filter(Boolean).join(" · ")}</small> : null}</div><div className="issue-run-group-aside">{groupIndex === 0 ? viewToggle : null}<strong>{owned} of {group.issues.length} owned</strong></div></header>
      <div className={view === "grid" ? "issue-tile-grid" : ""}>{group.issues.map((issue) => {
        // "Volume 5" under "Vol. 5" says it twice.
        const genericTitle = !issue.title || identityKey(issue.title) === identityKey(`Issue ${issue.number}`)
          || (medium === "manga" && identityKey(issue.title) === identityKey(`Volume ${issue.number}`));
        const releaseLabel = issue.publicationDate
          ? new Date(`${issue.publicationDate}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
          : issue.publicationYear ? `Published ${issue.publicationYear}` : "Release date unknown";
        // Owned as a single issue is what the Issues tab already says, so it
        // carries no badge; the other states are news and keep theirs.
        const stateLabel = issue.ownership === "both" ? "Single + volume"
          : issue.ownership === "direct" ? null
          : issue.ownership === "collection" ? "In volume"
          : issue.releaseState === "upcoming" ? "Upcoming"
          : issue.releaseState === "unknown" ? "Date needed" : "Missing";
        const owned = issue.ownership !== "unowned";
        if (view === "grid") {
          // A missing issue keeps its tile, dimmed, so a gap in a run is
          // visible rather than silently absent from the grid.
          return <article className={`issue-tile ${owned ? "" : "unowned"}`} key={issue.id}>
            {/* The badge sits over the cover, beside it rather than inside it,
                so a missing issue's dimmed cover does not dim its badge too. */}
            <span className="issue-tile-art">
              <span className="issue-tile-cover"><CoverArt id={`issue-${issue.id}`} title={`${issue.contextLabel || "Issue"} #${issue.number}`} cover={issue.fileCover || issue.cover} decorative placeholderSize={22} /></span>
              {stateLabel ? <span className={`ownership-source ${issue.ownership} ${issue.acquisitionState || ""}`}>{stateLabel}</span> : null}
            </span>
            {onEditIssue ? <button type="button" className="issue-tile-edit" onClick={() => onEditIssue(issue)} aria-label={`Edit metadata for ${issue.contextLabel || "issue"} issue ${issue.number}`} title="Edit issue metadata"><PencilSimple size={14} /></button> : null}
            <strong>{issueLabel(issue.number, medium)}{issue.metadataLocked ? <ShieldCheck className="issue-local-lock" size={12} weight="fill" aria-label="Local metadata correction locked" /> : null}</strong>
            {!genericTitle ? <small className="issue-tile-title">{issue.title}</small> : null}
          </article>;
        }
        return <article key={issue.id}><span className={`grouped-issue-cover ${issue.fileCover ? "from-file" : ""}`}><CoverArt id={`issue-${issue.id}`} title={`${issue.contextLabel || "Issue"} #${issue.number}`} cover={issue.fileCover || issue.cover} decorative placeholderSize={16} /></span><span className="grouped-issue-number">{issueLabel(issue.number, medium)}</span><div>{!genericTitle ? <strong>{issue.title}{issue.metadataLocked ? <ShieldCheck className="issue-local-lock" size={13} weight="fill" aria-label="Local metadata correction locked" /> : null}</strong> : null}<small>{releaseLabel}</small></div><div className="issue-row-actions">{stateLabel ? <span className={`ownership-source ${issue.ownership} ${issue.acquisitionState || ""}`}>{stateLabel}</span> : null}{onEditIssue ? <button type="button" onClick={() => onEditIssue(issue)} aria-label={`Edit metadata for ${issue.contextLabel || "issue"} issue ${issue.number}`} title="Edit issue metadata"><PencilSimple size={14} /></button> : null}</div></article>;
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

// Following is on or off, so it is a switch -- not a button that changes into
// "View wanted issues" once pressed, beside a second button that undoes it.
function FollowSwitch({ following, busy = false, label, onChange }) {
  return <label className={`follow-switch${busy ? " busy" : ""}`}>
    <input type="checkbox" role="switch" checked={following} disabled={busy}
      aria-checked={following} onChange={(event) => onChange(event.target.checked)} />
    <i aria-hidden="true" />
    <span>{busy ? <LoadingSpinner size={14} /> : null}{label}</span>
  </label>;
}

// The comic drawer's pieces (node-id=72-1306). A row pages by its own width,
// the way Discover's release shelves do, and says when there is nowhere left
// to go.
function useShelfPaging(deps = []) {
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
    if (node) node.scrollBy({ left: direction * node.clientWidth, behavior: "smooth" });
  }
  return { scroller, atStart, atEnd, measure, page };
}

// "#1 · The Dark Knight" -- the run's own title would say the same thing on
// every card, so the number leads and the issue's title follows when it has
// one worth showing.
function issueCardLabel(issue, medium) {
  const number = issueLabel(issue.number, medium);
  const generic = !issue.title || identityKey(issue.title) === identityKey(`Issue ${issue.number}`)
    || (medium === "manga" && identityKey(issue.title) === identityKey(`Volume ${issue.number}`));
  return generic ? number : `${number} · ${issue.title}`;
}

function ComicDrawerRow({ title, count, children }) {
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
function ComicDrawerIssueCard({ issue, medium, onOpen }) {
  const owned = issue.ownership !== "unowned";
  const state = issue.ownership === "collection" ? "In volume"
    : owned ? null
    : issue.releaseState === "upcoming" ? "Upcoming"
    : issue.releaseState === "unknown" ? "Date needed" : "Missing";
  const label = issueCardLabel(issue, medium);
  return <article className={`pull-card comic-drawer-card${owned ? "" : " unowned"}`}>
    <button type="button" className="discover-open" onClick={onOpen} aria-label={`${label}${state ? `, ${state.toLowerCase()}` : ""}. Show all issues`}>
      <span className="comic-drawer-card-art">
        <DiscoverCover src={issue.fileCover || issue.cover} alt="" glyph={30} />
        {state ? <span className={`ownership-source ${issue.ownership} ${issue.acquisitionState || ""}`}>{state}</span> : null}
      </span>
    </button>
    <div className="pull-card-body"><h3 title={label}>{label}</h3></div>
  </article>;
}

function ComicDrawerRunCard({ run, onOpen }) {
  return <article className="pull-card comic-drawer-card">
    <button type="button" className="discover-open" onClick={() => onOpen(run)} aria-label={`Open ${run.title}${run.year ? ` (${run.year})` : ""}`}>
      <span className="comic-drawer-card-art"><span className="discover-cover"><SeriesCover series={run} decorative /></span></span>
    </button>
    <div className="pull-card-body"><h3 title={run.title}>{run.title}</h3></div>
  </article>;
}

// Everyone credited, as the file writes it: one run of names. Past two lines
// it folds, and opening it lists each person with what they did.
function ComicDrawerCreators({ creators }) {
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

function SeriesDrawer({ series, families, allSeries, parentCollection, dismissSignal, onBack, onClose, onRequest, onViewRequests, requestBusy, onAddAlias, onSyncIssues, onFindRun, onMergeRun, onRebuildRun, rebuilding = false, rebuildResult = "", onCreateFamily, onSetFamily, onOpenWorkbench, onOpenCover, onChangeSeriesCover, onFixSeriesMatch, onOpenContents, onChangeRun, onEditIssue, onReplace, onUnfollow, unfollowBusy = false, onSetFormat, onRemove, onOpenSeries, onChangeBackdrop, backdropVersion = 0 }) {
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
  // Asked for when the drawer opens: the blurb is not in the catalog, and
  // most runs are never opened.
  const [synopsis, setSynopsis] = useState({ state: "loading", text: null, source: null });
  useEffect(() => {
    if (!series?.id) return undefined;
    let live = true;
    setSynopsis({ state: "loading", text: null, source: null });
    apiRequest(`/api/v1/series/${series.id}/synopsis`)
      .then((data) => { if (live) setSynopsis({ state: "done", text: data.synopsis, source: data.providerName }); })
      .catch(() => { if (live) setSynopsis({ state: "done", text: null, source: null }); });
    return () => { live = false; };
  }, [series?.id]);
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
    // Volumes and Collection disappear when collected editions are turned off.
    if (!editionsOn && (tab === "editions" || tab === "family")) setTab("overview");
  }, [editionsOn, tab]);
  // The comic drawer (node-id=72-1306): a page from the comic behind the
  // header -- the one the reader chose, or a spread found for them -- and the
  // runs here that share a maker or a publisher. The cover stands in when
  // there is no page to use, or the page will not load.
  const [backdrop, setBackdrop] = useState({ forId: null, url: null, source: null, fileId: null, page: null });
  const [backdropFailed, setBackdropFailed] = useState(false);
  useEffect(() => {
    if (!series?.id) return undefined;
    let live = true;
    setBackdropFailed(false);
    apiRequest(`/api/v1/series/${series.id}/backdrop`)
      .then((data) => { if (live) setBackdrop({ ...data, forId: series.id }); })
      .catch(() => { if (live) setBackdrop({ forId: series.id, url: null, source: "none", fileId: null, page: null }); });
    return () => { live = false; };
  }, [series?.id, backdropVersion]);
  const shownBackdrop = backdrop.forId === series?.id ? backdrop : null;
  const coverArt = series?.coverCandidates?.[0] || series?.cover || null;
  const pageArt = shownBackdrop?.url && !backdropFailed ? shownBackdrop.url : null;
  // Nothing until this run's answer arrives, so the last run's page never
  // flashes behind a new title.
  const heroArt = shownBackdrop ? pageArt || coverArt : null;
  const related = useMemo(() => (series ? relatedRuns(series, allSeries || []) : null), [series, allSeries]);
  const creators = useMemo(() => orderedCreators(series?.creators), [series?.creators]);
  const [tabsRef, tabGlass] = useGlassIndicator("button.active", [tab, series?.id, editionsOn]);
  useEffect(() => { dialogRef.current?.scrollTo?.({ top: 0 }); }, [series?.id]);
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
  // Volumes and Collection belong to collected-edition support; the counts
  // live on the tabs, so the header does not repeat them.
  const tabs = [
    ["overview", "Overview"],
    ["issues", `Issues (${series.issues?.length ?? 0})`],
    ...(editionsOn ? [["editions", `Volumes (${series.editions?.length ?? 0})`]] : []),
    ["files", `Files (${series.fileDetails?.length ?? 0})`],
    ...(editionsOn ? [["family", "Collection"]] : []),
    ["advanced", "Advanced"],
  ];
  const alternateTitles = (series.aliases || []).filter((item) => identityKey(item.name) !== identityKey(series.title));
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}><aside className={`series-drawer comic-drawer ${closing ? "closing" : ""}`} ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="series-drawer-title" onMouseDown={(event) => event.stopPropagation()}>
    <header className="comic-drawer-hero">
      {heroArt ? <><img className="comic-drawer-backdrop" src={heroArt} alt="" aria-hidden="true" key={heroArt} onError={pageArt ? () => setBackdropFailed(true) : undefined} /><img className="comic-drawer-backdrop blurred" src={heroArt} alt="" aria-hidden="true" key={`${heroArt}-blurred`} /></> : null}
      <span className="comic-drawer-scrim" aria-hidden="true" />
      <button type="button" className="comic-drawer-close" onClick={requestClose} aria-label="Close series details"><DrawerCloseIcon size={null} /></button>
      {onChangeBackdrop ? <button type="button" className="comic-drawer-backdrop-button" onClick={() => onChangeBackdrop(series, shownBackdrop)} aria-label="Choose the header background page" title="Choose background page"><ImageSquare size={20} /></button> : null}
      {parentCollection ? <button type="button" className="drawer-back-link" onClick={onBack}><ArrowLeft size={17} /><span>Back to <strong>{parentCollection.name}</strong></span></button> : null}
      <div className="comic-drawer-identity">
        <div className="comic-drawer-cover">{onChangeSeriesCover ? <button type="button" className="drawer-cover-button" onClick={() => onChangeSeriesCover(series)} aria-label={`Change the cover for ${series.title}`}><SeriesCover series={series} /><span className="drawer-cover-hint"><ImageSquare size={15} /> Change cover</span></button> : <SeriesCover series={series} />}</div>
        <div className="comic-drawer-copy">
          <div className="comic-drawer-titles">
            <h2 id="series-drawer-title">{series.title}</h2>
            <p>{[series.publisher, series.year].filter(Boolean).join(" • ")}</p>
          </div>
          <div className="comic-drawer-statuses"><PublicationStatus series={series} /><MonitoringStatus series={series} />{editionsOn && series.family ? <button type="button" className="family-link-chip" onClick={() => setTab("family")}><Books size={14} /> {series.family.name}</button> : null}</div>
          <Ownership series={series} compact />
        </div>
      </div>
    </header>
    <nav className="drawer-tabs comic-drawer-tabs" aria-label="Series details" ref={tabsRef}><span className="comic-drawer-tab-glass glass-indicator" aria-hidden="true" style={tabGlass || { opacity: 0 }} />{tabs.map(([id, label]) => <button type="button" className={tab === id ? "active" : ""} aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)} key={id}>{label}</button>)}</nav>
    <div className="comic-drawer-body">
      {tab === "overview" ? <>
        <div className="comic-drawer-follow">
          <FollowSwitch following={isFollowing} busy={requestBusy || unfollowBusy} label={requestBusy ? "Following…" : unfollowBusy ? "Stopping…" : isFollowing ? "Following Run" : "Follow Run"} onChange={(on) => (on ? onRequest() : onUnfollow(series))} />
          {isFollowing && wantedIssueCount ? <button type="button" className="comic-drawer-link" onClick={onViewRequests}>View {wantedIssueCount} wanted issue{wantedIssueCount === 1 ? "" : "s"}</button> : null}
        </div>
        <RunSynopsis loading={synopsis.state === "loading"} text={synopsis.text} source={synopsis.source} sourcePrefix="Source:" key={series.id} />
        {series.issues?.length ? <ComicDrawerRow title="Issues" count={series.issues.length}>{series.issues.map((issue) => <ComicDrawerIssueCard issue={issue} medium={series.medium} onOpen={() => setTab("issues")} key={issue.id || issue.number} />)}</ComicDrawerRow> : null}
        {creators.length ? <ComicDrawerCreators creators={creators} key={`creators-${series.id}`} /> : null}
        {related?.moreBy ? <ComicDrawerRow title={`More From ${related.moreBy.name}`} count={related.moreBy.runs.length}>{related.moreBy.runs.map((run) => <ComicDrawerRunCard run={run} onOpen={(item) => onOpenSeries?.(item)} key={run.id} />)}</ComicDrawerRow> : null}
        {related?.publisher ? <ComicDrawerRow title={`More From ${related.publisher.name}`} count={related.publisher.runs.length}>{related.publisher.runs.map((run) => <ComicDrawerRunCard run={run} onOpen={(item) => onOpenSeries?.(item)} key={run.id} />)}</ComicDrawerRow> : null}
      </> : null}
      {tab === "issues" ? <GroupedIssueInventory issues={groupedIssues} onEditIssue={onEditIssue} medium={series.medium} /> : null}
      {tab === "editions" && editionsOn ? <VolumeInventory editions={series.editions} /> : null}
      {tab === "files" ? <FileInventory files={series.fileDetails} onOpenWorkbench={onOpenWorkbench} onOpenCover={onOpenCover} onOpenContents={onOpenContents} onChangeRun={onChangeRun} onReplace={onReplace} /> : null}
      {tab === "family" && editionsOn ? <CollectionManagement series={series} families={families} allSeries={allSeries} onCreateFamily={onCreateFamily} onSetFamily={onSetFamily} /> : null}
      {tab === "advanced" ? <div className="advanced-tools">
        {/* Moved off the header: useful when repairing a run, noise when reading one. */}
        <div className="drawer-facts"><span><strong>{series.fileDetails?.length ?? series.owned}</strong>Comic files</span><span><strong>{series.inventory?.directIssueFiles ?? 0}</strong>Single issues</span>{editionsOn ? <span><strong>{series.inventory?.editionCount ?? series.editions?.length ?? 0}</strong>Volumes</span> : null}<span><strong>{identityStrength}</strong>Match confidence</span></div>
        {/* Single issues are already a fact above; the volume split only means something with editions on. */}
        {editionsOn ? <CollectionCoverage series={series} editionsOn={editionsOn} /> : null}
        <IssueCatalogCard series={series} catalogKnown={catalogKnown} syncing={syncingIssues} error={syncError} lastResult={lastSyncResult} onSync={syncIssues} onReviewFiles={() => setTab("files")} onFindRun={onFindRun} />
        <section className="advanced-card advanced-aliases">
          <div>
            <strong>Alternate titles</strong>
            <p>Other names this run&rsquo;s comics are filed under. Scans use them automatically; add one only if a scan keeps missing a file.</p>
            {alternateTitles.length ? <div className="alias-list">{alternateTitles.map((item) => <span className={item.confirmed ? "confirmed" : ""} key={`${item.name}-${item.source}`}><strong>{item.name}</strong><small>{item.confirmed ? "Manually confirmed" : item.source}</small></span>)}</div> : null}
            <form className="alias-form" onSubmit={saveAlias}><label><span>Add a title alias</span><div><input value={alias} onChange={(event) => setAlias(event.target.value)} placeholder="Alternate series title…" /><button disabled={savingAlias || !alias.trim()} aria-busy={savingAlias}>{savingAlias ? <LoadingSpinner size={18} /> : <Plus size={18} />} Add</button></div></label>{aliasError ? <small className="form-error" role="alert">{aliasError}</small> : null}</form>
          </div>
        </section>
        <section className="advanced-card">
          <div><strong>Header background</strong><p>{shownBackdrop?.source === "chosen" ? "A page you chose from this run." : "A page picked automatically: the first double-page spread in the run's first issue."}</p></div>
          <button type="button" onClick={() => onChangeBackdrop?.(series, shownBackdrop)}><ImageSquare size={16} /> Choose page</button>
        </section>
        <section className="advanced-card">
          <div><strong>Combine duplicate run</strong><p>One run split into two entries? Merge them. Files on disk aren&rsquo;t changed.</p></div>
          <button type="button" onClick={() => onMergeRun(series)}><Books size={16} /> Combine</button>
        </section>
        <section className="advanced-card">
          <div><strong>Series cover</strong><p>Use another issue&rsquo;s art, a provider&rsquo;s cover, or your own image.</p></div>
          <button type="button" onClick={() => onChangeSeriesCover(series)}><ImageSquare size={16} /> Change cover</button>
        </section>
        <section className="advanced-card">
          <div><strong>Fix series match</strong><p>Matched to the wrong comic? Pick the right run. Its issue list replaces this one; files aren&rsquo;t touched.</p></div>
          <button type="button" onClick={() => onFixSeriesMatch(series)}><MagnifyingGlass size={16} /> Fix match</button>
        </section>
        <section className="advanced-card">
          <div><strong>{series.medium === "manga" ? "Filed as manga" : "Filed as a comic"}</strong><p>{series.medium === "manga" ? "Searched and filed by volume, in the Manga folder." : "Manga is searched and filed by volume, in the Manga folder."} Change it if the publisher misled Flipparr.</p></div>
          <button type="button" onClick={() => onSetFormat?.(series, series.medium === "manga" ? "comic" : "manga")}><Books size={16} /> {series.medium === "manga" ? "File as comic" : "File as manga"}</button>
        </section>
        <section className="advanced-card">
          <div><strong>Rebuild this run</strong><p>Clear the titles, dates and covers Flipparr worked out, and work them out again from these files. Your corrections are kept.</p>{rebuildResult ? <small className="rebuild-result">{rebuildResult}</small> : null}</div>
          <button type="button" onClick={() => onRebuildRun(series)} disabled={rebuilding}>{rebuilding ? <LoadingSpinner size={16} /> : <ArrowsClockwise size={16} />} {rebuilding ? "Rebuilding…" : "Rebuild"}</button>
        </section>
        <section className="advanced-card danger">
          <div><strong>Remove from library</strong><p>Removes the run and deletes its comic files from disk. You&rsquo;ll see what goes first. This can&rsquo;t be undone.</p></div>
          <button type="button" onClick={() => onRemove?.(series)}><Trash size={16} /> Remove</button>
        </section>
      </div> : null}
    </div>
  </aside></div>;
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

function CollectionDrawer({ collection, tab, onTabChange, onClose, onFindStructure, onOpenSeries, onOpenContents, onRequest, onViewRequests, requestBusy, onEditIssue, onUnfollow, unfollowBusy = false }) {
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
      {tab === "overview" ? <div className="drawer-actions"><FollowSwitch following={collection.monitoringStatus === "monitored"} busy={requestBusy || unfollowBusy} label={requestBusy ? "Following…" : unfollowBusy ? "Stopping…" : collection.monitoringStatus === "monitored" ? "Following" : "Follow collection"} onChange={(on) => (on ? onRequest() : onUnfollow(collection))} />{issueCoveragePending ? <button className="ghost-button" onClick={() => onTabChange("volumes")}><Books size={18} /> Review volume contents</button> : null}{collection.monitoringStatus === "monitored" && !issueCoveragePending && missingIssueCount ? <button className="ghost-button" onClick={onViewRequests}><CheckCircle size={18} weight="fill" /> View {missingIssueCount} wanted issue{missingIssueCount === 1 ? "" : "s"}</button> : null}<button className="ghost-button" onClick={() => onTabChange("files")}><Eye size={18} /> View files</button></div> : null}
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

function SeriesMatchWorkbench({ data, loading, busy, error, onClose, onSearch, onConfirm }) {
  const dialogRef = useDialog(onClose);
  const [draft, setDraft] = useState(data.query || data.series?.title || "");
  const candidates = data.candidates || [];
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}>
    <section className="modal series-match-workbench" role="dialog" aria-modal="true" aria-labelledby="series-match-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}>
      <button className="modal-close" onClick={onClose} aria-label="Close series match"><X size={20} /></button>
      <span className="eyebrow">Fix series match</span>
      <h2 id="series-match-title">{data.series?.title} <em>({data.series?.year})</em></h2>
      <p className="workbench-intro">Pick the publication run this series really is. Its issue list is replaced with the one you choose; your own corrections and your comic files are untouched.</p>
      <form className="series-match-search" onSubmit={(event) => { event.preventDefault(); onSearch(draft); }}>
        <input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Search publication runs…" aria-label="Search for the right publication run" />
        <button className="secondary-button" disabled={loading || !draft.trim()}>{loading ? <LoadingSpinner size={17} /> : <MagnifyingGlass size={17} />} Search</button>
      </form>
      {data.providersChecked?.length ? <small className="series-match-providers">Searched {data.providersChecked.join(", ")}</small> : null}
      {loading ? <div className="series-match-empty"><LoadingSpinner size={20} label="Searching metadata services" /></div>
        : candidates.length ? <div className="discovery-results">{candidates.map((item) => {
            const itemId = `${item.provider || data.provider}-${item.providerSeriesId}`;
            return <article className="discovery-result" key={itemId}>
              <DiscoverCover src={item.cover} alt={`${item.title} cover`} />
              <div className="discovery-copy">
                <div className="discovery-title-row"><strong>{item.title}</strong><b>{item.yearLabel}</b></div>
                <span>{item.publisher || "Publisher unknown"} · {item.issueCount} known issue{item.issueCount === 1 ? "" : "s"}</span>
                <small>{item.providerName || data.providerName || data.provider}</small>
              </div>
              <Button busy={busy === itemId} busyLabel="Matching…" icon={<CheckCircle size={16} />} onClick={() => onConfirm(item)}>Use this match</Button>
            </article>;
          })}</div>
        : <div className="series-match-empty"><MagnifyingGlass size={22} weight="duotone" /><strong>No publication runs found</strong><span>Try the exact title, or add the publication year.</span></div>}
      {error ? <p className="workbench-error" role="alert">{error}</p> : null}
    </section>
  </div>;
}

function CoverWorkbench({ data, title, busy, error, onClose, onSelect, onUpload }) {
  const dialogRef = useDialog(onClose);
  const selectedSource = data.covers.selectedSource;
  // A run has many options from the same source, so identity is the option
  // id where the payload carries one, not the source alone.
  const isSelected = (option) => (
    data.covers.selectedOptionId
      ? data.covers.selectedOptionId === option.optionId
      : selectedSource === option.source && (!data.covers.selectedUrl || data.covers.selectedUrl === option.url)
  );
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal cover-workbench" role="dialog" aria-modal="true" aria-labelledby="cover-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close cover picker"><X size={20} /></button><span className="eyebrow">Change cover</span><h2 id="cover-workbench-title">{title || data.file?.filename}</h2><p className="workbench-intro">Choose art from the comic, a metadata provider, or upload your own image. This does not alter the original comic file.</p><div className="cover-option-grid">{data.covers.options.map((option) => <article className={isSelected(option) ? "selected" : ""} key={option.optionId || `${option.source}-${option.url}`}><img src={option.url} alt={option.label} /><div><strong>{option.label}</strong><small>{option.detail}</small><button disabled={busy} onClick={() => onSelect(option.source, option.url, option.fileId)}>{isSelected(option) ? "Selected" : "Use cover"}</button></div></article>)}</div><div className="cover-picker-actions"><button className="ghost-button" disabled={busy} onClick={() => onSelect("auto", null)}><ArrowsClockwise size={17} /> Use automatic cover</button><label className="primary-button upload-cover-button"><UploadSimple size={18} /> Upload image<input type="file" accept="image/jpeg,image/png,image/webp,image/gif,image/heic,image/heif" disabled={busy} onChange={(event) => event.target.files?.[0] && onUpload(event.target.files[0])} /></label></div>{error ? <p className="workbench-error" role="alert">{error}</p> : null}</section></div>;
}

// The page behind a run's drawer header: pick an issue, then one of its pages.
// Pages load one issue at a time, as thumbnails the server renders from the
// file, so a long run costs nothing until an issue is opened.
const PAGE_ARCHIVE_EXTENSIONS = new Set(["cbz", "cbr", "cb7", "cbt", "zip", "rar"]);

function BackdropWorkbench({ series, current, busy, error, onClose, onChoose, onAutomatic }) {
  const dialogRef = useDialog(onClose);
  const files = useMemo(() => [...(series.fileDetails || [])]
    .filter((file) => PAGE_ARCHIVE_EXTENSIONS.has(String(file.extension || "").replace(".", "").toLowerCase()))
    .sort((a, b) => String(a.filename).localeCompare(String(b.filename), undefined, { numeric: true, sensitivity: "base" })), [series.fileDetails]);
  const [fileId, setFileId] = useState(() => (files.some((file) => file.id === current?.fileId) ? current.fileId : files[0]?.id) || "");
  const [pages, setPages] = useState({ state: "loading", list: [], error: "" });
  useEffect(() => {
    if (!fileId) { setPages({ state: "done", list: [], error: "" }); return undefined; }
    let live = true;
    setPages({ state: "loading", list: [], error: "" });
    apiRequest(`/api/v1/files/${fileId}/pages`)
      .then((data) => { if (live) setPages({ state: "done", list: data.pages || [], error: "" }); })
      .catch((loadError) => { if (live) setPages({ state: "done", list: [], error: loadError.message }); });
    return () => { live = false; };
  }, [fileId]);
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal backdrop-workbench" role="dialog" aria-modal="true" aria-labelledby="backdrop-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}>
    <button type="button" className="modal-close" onClick={onClose} aria-label="Close"><X size={20} /></button>
    <h2 id="backdrop-workbench-title">Header background</h2>
    <p className="backdrop-workbench-intro">Choose a page from {series.title} to show behind the drawer&rsquo;s header.</p>
    <div className="backdrop-workbench-tools">
      <label><span>Issue</span><select value={fileId} onChange={(event) => setFileId(event.target.value)}>{files.map((file) => <option value={file.id} key={file.id}>{String(file.filename).replace(/\.[^.]+$/, "")}</option>)}</select></label>
      <button type="button" className="secondary-button" onClick={onAutomatic} disabled={busy || current?.source !== "chosen"}>Use automatic page</button>
    </div>
    {error ? <p className="form-error" role="alert">{error}</p> : null}
    {!files.length ? <p className="backdrop-workbench-status">This run has no comic archives to take a page from.</p> : null}
    {pages.state === "loading" && files.length ? <div className="backdrop-workbench-status" role="status"><LoadingSpinner size={18} /> Reading pages…</div> : null}
    {pages.error ? <p className="form-error" role="alert">{pages.error}</p> : null}
    {pages.state === "done" && !pages.error && pages.list.length ? <div className="backdrop-page-grid">{pages.list.map((page) => {
      const selected = current?.fileId === fileId && current?.page === page.index;
      return <button type="button" className={`backdrop-page${selected ? " selected" : ""}`} aria-pressed={selected} disabled={busy} onClick={() => onChoose(fileId, page.index)} key={page.index}>
        <img src={page.url} alt="" loading="lazy" />
        <span>Page {page.index + 1}</span>
      </button>;
    })}</div> : null}
  </section></div>;
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
  requests: "/pull-list",
  settings: "/settings",
  import: "/import",
};
// Paths this app used to answer on. A bookmark to one lands where it meant
// to rather than silently on the library. /search was its own view until
// Discover grew the results it was showing.
const RETIRED_ROUTES = { "/requests": "requests", "/search": "discover", "/health": "settings" };
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
  if (active === "discover" && searchQuery) params.set("q", searchQuery);
  if (seriesId) params.set("series", String(seriesId));
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

function stateFromLocation(pathname, search) {
  const params = new URLSearchParams(search || "");
  const segments = String(pathname || "").split("/").filter(Boolean);
  const first = `/${segments[0] || ""}`;
  const active = VIEW_BY_ROUTE[first] || RETIRED_ROUTES[first] || "library";
  const requestedSection = segments[1];
  return {
    active,
    settingsSection: active === "settings" && SETTINGS_SECTIONS.some((item) => item.id === requestedSection)
      ? requestedSection
      : "health",
    searchQuery: active === "discover" ? params.get("q") || "" : "",
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
  const [reviewFocus, setReviewFocus] = useState(null);
  const [requestFocus, setRequestFocus] = useState(null);
  const [dismissedNotifications, setDismissedNotifications] = useState(
    () => readDismissed(typeof window === "undefined" ? null : window.localStorage));
  // Where this client had got to last time it looked. Set on first run so a
  // browser that has never seen the app does not open onto every comic ever
  // imported; from then on it only moves when an arrival is acknowledged.
  const [seenUntil] = useState(() => {
    if (typeof window === "undefined") return null;
    const stored = readSeenUntil(window.localStorage);
    if (stored) return stored;
    const now = new Date().toISOString();
    writeSeenUntil(window.localStorage, now);
    return now;
  });
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
  const [seriesCoverWorkbench, setSeriesCoverWorkbench] = useState(null);
  const [backdropWorkbench, setBackdropWorkbench] = useState(null);
  const [backdropBusy, setBackdropBusy] = useState(false);
  const [backdropError, setBackdropError] = useState("");
  const [backdropVersion, setBackdropVersion] = useState(0);
  const [unfollowBusy, setUnfollowBusy] = useState(false);
  const [matchWorkbench, setMatchWorkbench] = useState(null);
  const [matchLoading, setMatchLoading] = useState(false);
  const [matchBusy, setMatchBusy] = useState("");
  const [matchError, setMatchError] = useState("");
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
  // A notification names one problem, so clicking it opens that problem
  // rather than whichever happens to be first. A fresh object every time, so
  // clicking the same row again after browsing elsewhere re-aims.
  function reviewProblem(item) {
    setReviewFocus(item?.id ? { id: item.id } : null);
    navigate("settings", "health");
  }

  // The bell's rows each carry their destination, so this is the one place
  // that has to know how to land on each screen. Called with no argument by
  // "Review all", which opens the screen without aiming at anything.
  function openNotification(item) {
    if (!item) { navigate("settings", "health"); return; }
    if (item.view === "requests") {
      setRequestFocus({ ...item.focus });
      navigate("requests");
      return;
    }
    if (item.view === "settings") {
      navigate("settings", item.focus?.section);
      return;
    }
    reviewProblem(item.focus);
  }

  function dismissNotification(item) {
    setDismissedNotifications((current) => {
      const next = current.includes(item.id) ? current : [...current, item.id];
      writeDismissed(window.localStorage, next);
      return next;
    });
  }

  const notifications = useMemo(
    () => buildNotifications(catalog, dismissedNotifications, seenUntil),
    [catalog, dismissedNotifications, seenUntil]);
  const notificationsBell = { notifications, onOpen: openNotification, onDismiss: dismissNotification };
  useEffect(() => {
    if (!catalog || !dismissedNotifications.length) return;
    const pruned = pruneDismissed(catalog, dismissedNotifications);
    if (pruned.length === dismissedNotifications.length) return;
    setDismissedNotifications(pruned);
    writeDismissed(window.localStorage, pruned);
  }, [catalog, dismissedNotifications]);

  function navigate(id, sectionId) {
    setActive(id); setSelectedSeries(null); setSelectedCollection(null); setSeriesParentCollection(null);
    // Settings shows one section at a time, so a deep link selects the section
    // rather than scrolling to it.
    if (id === "settings" && sectionId) setSettingsSection(sectionId);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  function openSearch(value) {
    const cleaned = String(value || "").trim();
    // An empty value is the clear button, not a rejected search.
    if (cleaned && cleaned.length < 2) return;
    setSearchQuery(cleaned);
    navigate("discover");
    if (!cleaned) return;
    // The two halves of this screen read different libraries otherwise. The
    // "in your library" list filters the catalog this client last fetched,
    // while Discover's "already in your library" is decided server-side
    // against the current one -- and the poll below only runs while a scan,
    // a download or enrichment is active, so on an idle library the client's
    // copy is whatever it loaded on mount and never changes. Add a series and
    // search for it and the page contradicts itself, indefinitely.
    loadCatalog();
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
  async function pullDiscoveredIssue(issue) {
    try {
      const result = await apiRequest("/api/v1/discover/pull-issue", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: "metron",
          providerSeriesId: issue.providerSeriesId,
          providerIssueId: issue.providerIssueId,
          number: issue.number,
          seriesTitle: issue.seriesTitle,
        }),
      });
      // A pull starts real work, and the catalog poll only runs while
      // something is active -- so without this the Pull List does not know
      // about it until something else happens to refresh.
      await loadCatalog();
      showToast(`${issue.title} added to your Pull List`);
      return { ok: true, result };
    } catch (error) {
      showToast(error.message);
      return { ok: false, error: error.message };
    }
  }

  async function pullDiscoveredIssues({ provider, providerSeriesId, numbers, released, title, query }) {
    try {
      const result = await apiRequest("/api/v1/discover/pull-issue", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider, providerSeriesId, numbers, released: Boolean(released),
          seriesTitle: query || title,
        }),
      });
      await loadCatalog();
      const count = (result.numbers || []).length;
      showToast(`${title} · ${count} issue${count === 1 ? "" : "s"} added to your Pull List`);
      return { ok: true, result };
    } catch (error) {
      showToast(error.message);
      return { ok: false, error: error.message };
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
      // Staying put is the point: the card settles into "On your Pull List" so
      // the reader can keep browsing the results they were reading.
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
  // Deleted, not cancelled: a pull is one click to make again, and a cancelled
  // row would sit on the Pull List as a comic nobody wants.
  async function deletePull(request, issue) {
    try {
      await apiRequest(issue
        ? `/api/v1/requests/${request.id}/issues/${issue.id}`
        : `/api/v1/requests/${request.id}`, { method: "DELETE" });
      await loadCatalog();
      showToast(issue
        ? `Deleted #${issue.number} from your Pull List`
        : `Deleted ${request.title} from your Pull List`);
    } catch (error) { showToast(error.message); }
  }
  async function setSeriesFormat(series, format) {
    try {
      await apiRequest(`/api/v1/series/${series.id}/format`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ format }),
      });
      await loadCatalog();
      showToast(format === "manga" ? `${series.title} is filed as manga` : `${series.title} is filed as a comic`);
    } catch (error) { showToast(error.message); }
  }
  // Irreversible, so it says exactly what goes -- files and space -- before
  // it asks, and the server checks every path again before deleting any.
  async function removeSeries(series) {
    try {
      const plan = await apiRequest(`/api/v1/series/${series.id}/removal`);
      if (plan.activeDownloads) {
        showToast("Something for this run is still downloading. Remove it once that has finished.");
        return;
      }
      const size = plan.sizeBytes >= 1e9 ? `${(plan.sizeBytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(plan.sizeBytes / 1e6))} MB`;
      const files = plan.fileCount
        ? `This permanently deletes ${plan.fileCount} comic file${plan.fileCount === 1 ? "" : "s"} (${size}) from disk.`
        : "It has no comic files on disk.";
      if (!window.confirm(`Remove ${plan.title} from your library?\n\n${files} Its issue list and requests go too. This cannot be undone.`)) return;
      const result = await apiRequest(`/api/v1/series/${series.id}`, { method: "DELETE" });
      setSelectedSeries(null);
      await loadCatalog();
      showToast(result.filesDeleted
        ? `Removed ${plan.title} and deleted ${result.filesDeleted} file${result.filesDeleted === 1 ? "" : "s"}`
        : `Removed ${plan.title}`);
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
  async function saveSeriesBackdrop(body, message) {
    const runId = backdropWorkbench?.series?.id;
    if (!runId) return;
    setBackdropBusy(true); setBackdropError("");
    try {
      await apiRequest(`/api/v1/series/${runId}/backdrop`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setBackdropVersion((value) => value + 1);
      setBackdropWorkbench(null);
      showToast(message);
    } catch (error) { setBackdropError(error.message); }
    setBackdropBusy(false);
  }
  async function openSeriesCoverWorkbench(series) {
    setCoverError("");
    try {
      setSeriesCoverWorkbench(await apiRequest(`/api/v1/series/${series.id}/cover`));
    } catch (error) { showToast(error.message); }
  }
  async function finishSeriesCoverChange(message) {
    const selectedId = selectedSeries?.id;
    const data = await loadCatalog();
    const refreshed = data?.series?.find((item) => item.id === selectedId);
    if (refreshed) setSelectedSeries(refreshed);
    setSeriesCoverWorkbench(null);
    showToast(message);
  }
  async function selectSeriesCover(source, url, fileId) {
    setCoverBusy(true); setCoverError("");
    try {
      await apiRequest(`/api/v1/series/${seriesCoverWorkbench.series.id}/cover`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source, url, fileId }) });
      await finishSeriesCoverChange(source === "auto" ? "Automatic cover selection restored" : "Series cover updated");
    } catch (error) { setCoverError(error.message); }
    setCoverBusy(false);
  }
  async function uploadSeriesCover(file) {
    setCoverBusy(true); setCoverError("");
    try {
      await apiRequest(`/api/v1/series/${seriesCoverWorkbench.series.id}/cover/upload`, { method: "POST", headers: { "Content-Type": file.type }, body: file });
      await finishSeriesCoverChange("Uploaded cover saved");
    } catch (error) { setCoverError(error.message); }
    setCoverBusy(false);
  }
  async function unfollowSeries(series) {
    setUnfollowBusy(true);
    try {
      await apiRequest(`/api/v1/series/${series.id}/monitoring`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ monitored: false }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === series.id);
      if (refreshed) setSelectedSeries(refreshed);
      showToast(`No longer following ${series.title}`);
    } catch (error) { showToast(error.message); }
    setUnfollowBusy(false);
  }
  async function unfollowCollection(collection) {
    setUnfollowBusy(true);
    try {
      await apiRequest(`/api/v1/collections/${collection.id}/monitoring`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ monitored: false }),
      });
      const data = await loadCatalog();
      const refreshed = data?.families?.find((item) => item.id === collection.id);
      if (refreshed) setSelectedCollection(refreshed);
      showToast(`No longer following ${collection.name}`);
    } catch (error) { showToast(error.message); }
    setUnfollowBusy(false);
  }
  async function openSeriesMatchWorkbench(series) {
    setMatchError(""); setMatchBusy("");
    setMatchWorkbench({ series, candidates: [], query: series.title });
    setMatchLoading(true);
    try {
      setMatchWorkbench(await apiRequest(`/api/v1/series/${series.id}/match-candidates`));
    } catch (error) { setMatchError(error.message); }
    setMatchLoading(false);
  }
  async function searchSeriesMatches(query) {
    setMatchError(""); setMatchLoading(true);
    try {
      const data = await apiRequest(
        `/api/v1/series/${matchWorkbench.series.id}/match-candidates?` +
        new URLSearchParams({ query }).toString()
      );
      setMatchWorkbench(data);
    } catch (error) { setMatchError(error.message); }
    setMatchLoading(false);
  }
  async function confirmSeriesMatch(item) {
    const seriesId = matchWorkbench.series.id;
    setMatchBusy(`${item.provider || matchWorkbench.provider}-${item.providerSeriesId}`);
    setMatchError("");
    try {
      const result = await apiRequest(`/api/v1/series/${seriesId}/match`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: item.provider || matchWorkbench.provider,
          providerSeriesId: item.providerSeriesId,
          query: matchWorkbench.query,
        }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((entry) => entry.id === seriesId);
      if (refreshed) setSelectedSeries(refreshed);
      setMatchWorkbench(null);
      showToast(`Matched to ${result.matchedTitle || item.title}`);
    } catch (error) { setMatchError(error.message); }
    setMatchBusy("");
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
    // `isWorking` rather than a fourth copy of the downloadStatus list: a job
    // that is queued or out searching has no downloadStatus yet, so the list
    // was blind to precisely the stretch a reader watches.
    const activeDownload = acquisitionEntries.some(isWorking);
    // A scan counts too. Polling was tied to enrichment and downloads only, so
    // a page opened during a scan sat still until something else woke it.
    const scanning = Boolean(catalog?.activeScan);
    if (!(catalog?.enrichment?.active > 0) && !activeDownload && !scanning) return undefined;
    const timer = window.setInterval(() => loadCatalog(), scanning ? 2000 : 5000);
    return () => window.clearInterval(timer);
  }, [catalog?.enrichment?.active, catalog?.requests, catalog?.replacementRequests, catalog?.activeScan]);
  const visibleSeries = catalog?.series ?? (backendStatus === "offline" ? DEMO_SERIES : []);
  const logicalSeriesCount = logicalCatalogSeries(catalog, visibleSeries).length;
  const navActive = active === "discover" ? "discover" : active === "import" ? "settings" : active;
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
  return <CollectedEditionsContext.Provider value={Boolean(catalog?.collectedEditionsEnabled)}><NotificationsContext.Provider value={notificationsBell}><div className={`app-shell${navCollapsed ? " nav-collapsed" : ""}`}><AppBar query={searchQuery} collapsed={navCollapsed} notifications={notifications} onToggleNav={toggleNav} onSearch={openSearch} onNavigate={navigate} onOpenNotification={openNotification} onDismissNotification={dismissNotification} /><Nav active={navActive} onNavigate={navigate} catalog={catalog} backendStatus={backendStatus} logicalSeriesCount={logicalSeriesCount} authStatus={authStatus} onSignOut={signOut} scanning={scanState === "scanning" || Boolean(catalog?.activeScan)} onScanLibrary={() => scanLibrary()} /><main className={`main-content${active === "library" ? " main-content-flush" : ""}`}>{catalog?.collectedEditionsEnabled ? <div className="collected-editions-notice"><WarningCircle size={17} weight="fill" /> <span>Collected-edition support is on. Trades, hardcovers and omnibuses have less complete metadata and file availability than Issues, and never fulfill Issue ownership or acquisition.</span></div> : null}{active === "library" ? <LibraryView onNavigate={navigate} onOpenSeries={openSeries} onOpenCollection={openCollection} onSearch={openSearch} catalog={catalog} backendStatus={backendStatus} logicalSeriesCount={logicalSeriesCount} /> : null}{active === "discover" ? <DiscoverView query={searchQuery} catalog={catalog} backendStatus={backendStatus} onSearch={openSearch} onClearSearch={() => openSearch("")} onOpenSeries={openSeries} onOpenCollection={openCollection} onDiscoverRequest={requestDiscoveredSeries} onPullIssue={pullDiscoveredIssue} onPullIssues={pullDiscoveredIssues} /> : null}{active === "import" ? <ImportLibraryView onNavigate={navigate} onStartInventory={scanLibrary} onScanLibrary={() => scanLibrary()} onUpdateRoot={updateLibraryRoot} onRemoveRoot={removeLibraryRoot} catalog={catalog} backendStatus={backendStatus} scanState={scanState} scanProgress={scanProgress} /> : null}{active === "requests" ? <RequestsView catalog={catalog} backendStatus={backendStatus} focus={requestFocus} onCancelReplacement={cancelFileReplacement} onDeletePull={deletePull} onRefresh={loadCatalog} /> : null}{active === "settings" ? <SettingsView catalog={catalog} backendStatus={backendStatus} onNavigate={navigate} onAuthChanged={loadAuthStatus} onSignOut={signOut} section={settingsSection} onSectionChange={setSettingsSection} health={{ items: catalog?.inbox ?? [], loading: catalogPending(catalog, backendStatus), focus: reviewFocus, backendStatus, onResolve: resolveReview, onReplace: openReplacementRequest }} onScanLibrary={() => scanLibrary()} scanState={scanState} scanProgress={scanProgress} /> : null}</main>{selectedSeries ? <SeriesDrawer series={selectedSeries} families={catalog?.families || []} allSeries={visibleSeries} parentCollection={seriesParentCollection} dismissSignal={drawerDismissSignal} onBack={returnToCollection} onClose={() => { setSelectedSeries(null); setSeriesParentCollection(null); }} onRequest={() => createAcquisitionRequest(selectedSeries)} onViewRequests={() => navigate("requests")} requestBusy={requestBusyKey === `series:${selectedSeries.id}`} onAddAlias={addSeriesAlias} onSyncIssues={syncSeriesIssues} onFindRun={openSeriesRunWorkbench} onMergeRun={openSeriesMergeWorkbench} onRebuildRun={rebuildSeriesRun} rebuilding={rebuildingRun} rebuildResult={rebuildResult} onCreateFamily={createSeriesFamily} onSetFamily={setSeriesFamily} onOpenWorkbench={openFileWorkbench} onOpenCover={openCoverWorkbench} onChangeSeriesCover={openSeriesCoverWorkbench} onFixSeriesMatch={openSeriesMatchWorkbench} onSetFormat={setSeriesFormat} onRemove={removeSeries} onUnfollow={unfollowSeries} unfollowBusy={unfollowBusy} onOpenContents={openContentsWorkbench} onChangeRun={openFileRunWorkbench} onEditIssue={openIssueWorkbench} onReplace={openReplacementRequest} onOpenSeries={openSeries} onChangeBackdrop={(item, current) => { setBackdropError(""); setBackdropWorkbench({ series: item, current }); }} backdropVersion={backdropVersion} /> : null}{selectedCollection ? <CollectionDrawer collection={selectedCollection} tab={collectionTab} onTabChange={setCollectionTab} onClose={() => setSelectedCollection(null)} onFindStructure={openStoryStructure} onOpenSeries={openCollectionRun} onOpenContents={openContentsWorkbench} onRequest={() => createAcquisitionRequest(selectedCollection)} onViewRequests={() => navigate("requests")} requestBusy={requestBusyKey === `collection:${selectedCollection.id}`} onEditIssue={openIssueWorkbench} onUnfollow={unfollowCollection} unfollowBusy={unfollowBusy} /> : null}{workbench ? <MetadataWorkbench data={workbench.data} mode={workbench.mode} busy={workbenchBusy} error={workbenchError} onClose={() => setWorkbench(null)} onSave={saveFileMetadata} onMatch={applyFileMatch} onSearch={searchFileMatches} onReset={resetFileMetadata} /> : null}{issueWorkbench ? <IssueMetadataWorkbench issue={issueWorkbench} busy={issueBusy} error={issueError} onClose={() => setIssueWorkbench(null)} onSave={saveIssueMetadata} onReset={resetIssueMetadata} /> : null}{coverWorkbench ? <CoverWorkbench data={coverWorkbench} busy={coverBusy} error={coverError} onClose={() => setCoverWorkbench(null)} onSelect={selectFileCover} onUpload={uploadFileCover} /> : null}{matchWorkbench ? <SeriesMatchWorkbench data={matchWorkbench} loading={matchLoading} busy={matchBusy} error={matchError} onClose={() => setMatchWorkbench(null)} onSearch={searchSeriesMatches} onConfirm={confirmSeriesMatch} /> : null}{seriesCoverWorkbench ? <CoverWorkbench data={seriesCoverWorkbench} title={seriesCoverWorkbench.series.title} busy={coverBusy} error={coverError} onClose={() => setSeriesCoverWorkbench(null)} onSelect={selectSeriesCover} onUpload={uploadSeriesCover} /> : null}{backdropWorkbench ? <BackdropWorkbench series={backdropWorkbench.series} current={backdropWorkbench.current} busy={backdropBusy} error={backdropError} onClose={() => setBackdropWorkbench(null)} onChoose={(fileId, page) => saveSeriesBackdrop({ fileId, page }, "Header background updated")} onAutomatic={() => saveSeriesBackdrop({ source: "auto" }, "Automatic background restored")} /> : null}{contentsWorkbench ? <VolumeContentsWorkbench data={contentsWorkbench} busy={contentsBusy} error={contentsError} onClose={() => setContentsWorkbench(null)} onChange={changeCollectionContents} onReset={resetCollectionContents} /> : null}{runWorkbench ? <SeriesRunWorkbench data={runWorkbench} loading={runLoading} busy={runBusy} error={runError} onClose={() => setRunWorkbench(null)} onConfirm={confirmSeriesRun} onBuildCollection={buildSeriesCollection} /> : null}{fileRunWorkbench ? <FileRunWorkbench data={fileRunWorkbench} busy={fileRunBusy} error={fileRunError} onClose={() => setFileRunWorkbench(null)} onMove={moveFileToRun} /> : null}{structureWorkbench ? <StoryStructureWorkbench data={structureWorkbench} busy={structureBusy} error={structureError} onClose={() => setStructureWorkbench(null)} onSave={saveStoryStructure} /> : null}{mergeWorkbench ? <SeriesMergeWorkbench data={mergeWorkbench} busy={mergeBusy} error={mergeError} onClose={() => setMergeWorkbench(null)} onTargetChange={(targetId) => targetId ? previewSeriesMerge(mergeWorkbench.source, targetId, mergeWorkbench.candidates) : setMergeWorkbench((current) => ({ ...current, targetId: "", preview: null }))} onConfirm={confirmSeriesMerge} /> : null}{replacementFile ? <ReplacementModal file={replacementFile} busy={replacementBusy} error={replacementError} onClose={() => setReplacementFile(null)} onSubmit={createFileReplacement} /> : null}{toast ? <div className="toast"><CheckCircle size={20} weight="fill" /> {toast}</div> : null}</div></NotificationsContext.Provider></CollectedEditionsContext.Provider>;
}
