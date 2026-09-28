import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FlipparrMark } from "./brand.jsx";
import {
  Users,
  UserCircle,
  DeviceMobile,
  Hourglass,
  Backspace,
  ArrowsClockwise,
  ArrowCounterClockwise,
  CaretRight,
  CaretUpDown,
  Heartbeat,
  LockSimple,
  Sparkle,
  ArrowUpRight,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  ImageSquare,
  Books,
  CaretDown,
  Trash,
  Check,
  CheckCircle,
  Copy,
  ClockCounterClockwise,
  CloudArrowDown,
  Database,
  DotsThree,
  Eye,
  FolderOpen,
  Gear,
  Star,
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
import { artTone } from "./art-tone.js";
import { arrivalAt, canDeleteJob, canDeletePull, classifyRequest, groupPullList, isWorking, jobsForTab, releaseSearchSummary, tabCount, waitingIssues, RECENT_ARRIVAL_DAYS } from "./pull-list.js";
import {
  needsAttention, staleDismissals, readLegacyDismissed, forgetLegacyState, bellCount, timeAgo,
} from "./notifications.js";
import { creatorRoleLabel, orderedCreators, relatedRuns } from "./run-details.js";
import { nextTabBarState, canTuck } from "./tab-bar.js";
import { sheetPullDecision } from "./sheet.js";
import { countUpDuration, countUpValue } from "./count-up.js";
import { readingDirection, actionForKey, tapAction, pageForAction, pageWindow, isSpread, clampZoom, clampPan, pagesLeft, pageFilter, swipeAction, zoomAt, panelFocus, panelStep, stepCount, stepAt, stepOf, quadrantPanels, pointerDistance, pointerMidpoint, pinchZoom, pinchLeavesPanel, panelMask, isSwipe, isFlick, FLICK_WINDOW_MS, isEdgeTouch, isStolenBack, PULL_REFRESH_PX, loadReaderPrefs, saveReaderPrefs } from "./reader.js";
import { readRecent, recentEntry, rememberRecent, writeRecent } from "./recent-searches.js";
import {
  SearchIcon, MobileSearchIcon, ViewOptionsIcon, NotificationsIcon,
  ComicsIcon, DiscoverIcon, PullListIcon,
  GridViewIcon, ListViewIcon, FollowingIcon, ChevronDown,
  ActiveRunIcon, FollowedIcon, SettingsNavIcon,
  PullIcon, ShelfBackIcon, ShelfNextIcon, ClearSearchIcon, DrawerCloseIcon,
} from "./design-icons.jsx";
import { readingVerb, readingNoun, readingAriaLabel, READING_STATES } from "./reading-target.js";
import { SORT_OPTIONS, LIBRARY_DEFAULTS, sortLibrary, inProgress, loadLibraryPrefs, saveLibraryPrefs } from "./library.js";
import { setStorageProfile, storageProfile, profileStorage, migrateLegacyKeys, can, isAdmin, initials, profileColour, nextProfileColour, pinInput, VIEWER_CACHE_KEY, RATINGS, RATING_LABELS, ratingSource, limitLabel, isLocked, lockChoices, lockPatch, LOCK_LABELS } from "./profiles.js";
import { READING_DIRECTIONS } from "./reader.js";
import {
  HANDLES, hitTest, dragRect, drawnRect, isDrawn, newPanel, nudge, removeAt, tapOrder, applyOrder, toPayload, fromReading, editorKeyIntent,
} from "./panel-editor.js";
import { RATING_SOURCES, runRating, filledStars, ratingForPress, ratingLabel } from "./ratings.js";
import {
  pullState, issueKey, PULL_STATES, PULL_LABELS, READER_PULL_LABELS, shelfState, splitSearchResults,
  providerProgress, libraryMatchState,
  selectableIssue, releasedToPull, runPullSummary, runPreviewIds, weeklyPicks,
  runModes, completeRunToPull, issueLabel, libraryRunMatches,
} from "./discover.js";
import { requestKey, waitingKeys, runRequested, requestScope, adminQueue, isFollow, swipeDecision, ratingTone, UNDO_MS, REQUEST_STATE_LABELS, ADMIN_STATE_LABELS } from "./member-requests.js";

const NAV_ITEMS = [
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
const NAV_COUNT_LABELS = {
  // Stopped downloads and readers' requests: both wait on the admin.
  requests: (n) => `${n} waiting for you on the Pull List`,
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

// Reader profiles. The profile reading this page, as the server resolved it;
// null before it answers, and for the admin the whole app is theirs anyway.
// The server refuses what a reader may not do -- this only keeps a reader
// from being shown controls that would be refused.
const ViewerContext = createContext(null);
function useViewer() {
  return useContext(ViewerContext);
}

// Browser storage is the profile's own (profiles.js). Which profile was last
// reading on this device is known before anything renders, so the first
// paint already reads that profile's sort, view and settings.
function cachedViewerId() {
  try { return Number(window.localStorage.getItem(VIEWER_CACHE_KEY)) || null; } catch { return null; }
}
setStorageProfile(typeof window === "undefined" ? null : cachedViewerId());

function storable() {
  try {
    window.localStorage.setItem("flipparr.probe", "1");
    window.localStorage.removeItem("flipparr.probe");
    return true;
  } catch {
    return false;
  }
}

// A profile's reader settings live on the server, so they follow the person to
// any device; the browser keeps a copy for the first paint. The server's word
// wins for what it has.
async function syncReaderPrefs() {
  try {
    const me = await apiRequest("/api/v1/me");
    const prefs = me?.prefs || {};
    if (!Object.keys(prefs).length) return;
    const storage = profileStorage();
    let local = {};
    try { local = JSON.parse(storage.getItem("flipparr.reader") || "{}") || {}; } catch { local = {}; }
    storage.setItem("flipparr.reader", JSON.stringify({ ...local, ...prefs }));
  } catch {
    // Offline or refused: the browser's copy stands.
  }
}

// Enter a profile: remember it on this device, hand the browser's settings
// from before profiles to the admin, and start the page afresh. A reload is
// the one sure way that nothing -- a cached answer, an open drawer, a
// component's state -- carries one profile's library into another's.
function enterProfile(viewerId) {
  LAST_ANSWERS.clear();
  try {
    if (viewerId) {
      window.localStorage.setItem(VIEWER_CACHE_KEY, String(viewerId));
      migrateLegacyKeys(window.localStorage, viewerId);
    } else {
      window.localStorage.removeItem(VIEWER_CACHE_KEY);
    }
  } catch {
    // A browser that keeps nothing starts every profile fresh.
  }
  window.location.reload();
}

function useCollectedEditions() {
  return useContext(CollectedEditionsContext);
}

// What every page header needs from the app and no page owns: the bell's
// notifications, and the app's search -- the current query and where a
// search goes.
const HeaderContext = createContext(null);

function HeaderBell() {
  const header = useContext(HeaderContext);
  if (!header) return null;
  return <NotificationsBell bell={header.bell} />;
}

const DIALOG_FOCUSABLE =
  'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

// Dialogs stack: Fix match opens on top of the series drawer. Escape and the
// focus trap must apply only to the topmost one. Listener order alone can't do
// this — every dialog listens on document, and capture order favours the one
// that mounted first, which is the one underneath.
const openDialogs = [];
// Whether a dialog is the one in front. Keys belong to the front layer only:
// Escape closes one layer at a time, and the reader's page turns must not
// reach it while a drawer is open over it.
const isTopDialog = (node) => Boolean(node) && openDialogs[openDialogs.length - 1] === node;
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
const dialogClosers = new WeakMap();
const dialogMarks = new WeakMap();
let dialogMarkCount = 0;
function closeTopDialog() {
  const node = openDialogs[openDialogs.length - 1];
  if (!node || node.hasAttribute("data-in-address")) return false;
  const close = dialogClosers.get(node);
  if (!close) return false;
  close();
  return true;
}
/** The dialog in front, when Back has just taken its entry: it should close. */
function dialogLeftByBack() {
  const node = openDialogs[openDialogs.length - 1];
  if (!node || node.hasAttribute("data-in-address")) return false;
  return window.history.state?.dialog !== dialogMarks.get(node);
}

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

// What each GET last answered, for a view coming back: a tab switch unmounts
// the view it leaves, and a view mounting with nothing showed its skeleton
// while it asked again for what it had a moment ago -- the Comics grid, the
// release shelves, the settings toggles. Seeded from here, it draws at once
// with the last answer and refreshes behind it.
const LAST_ANSWERS = new Map();
function lastAnswer(path) { return LAST_ANSWERS.get(path) ?? null; }

async function apiRequest(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (response.ok && (!options?.method || options.method === "GET")) LAST_ANSWERS.set(path, payload);
  if (!response.ok) {
    const error = new Error(payload.error || `Request failed (${response.status})`);
    // Carry the status alongside the message. Callers need to tell "you are
    // signed out" apart from "the backend is down", and the message alone
    // cannot do that -- the server sends readable prose, not a status code.
    error.status = response.status;
    // Why, when the server says: "profile_required" asks for the picker.
    error.reason = payload.reason || "";
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

// A choice of one, as SegmentedTabs draws it but choosing a setting rather
// than a page: grid or list, runs or collections. An item with only an icon
// names itself with `title`.
function GlassSegmented({ label, items, value, onChange, className = "" }) {
  const [trackRef, glass] = useGlassIndicator(".segmented-tab.active", [value, items.map((item) => item.id).join()]);
  return <div className={`segmented-tabs ${className}`.trim()} role="radiogroup" aria-label={label} ref={trackRef}>
    <span className="glass-indicator" aria-hidden="true" style={glass || { opacity: 0 }} />
    {items.map((item) => <button type="button" role="radio" aria-checked={value === item.id}
      className={`segmented-tab${item.label ? "" : " segmented-tab--icon"}${value === item.id ? " active" : ""}`}
      aria-label={item.label ? undefined : item.title} title={item.label ? undefined : item.title}
      disabled={item.disabled} onClick={() => onChange(item.id)} key={item.id}>{item.icon}{item.label}</button>)}
  </div>;
}

// A number that counts to its new value rather than jumping, for the counts
// a scan or a pull changes under the reader. It starts at the first number it
// is given (a library's size should not run up from 0 on every page load) and
// animates every change after that.
function useCountUp(value) {
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

function CountUp({ value }) {
  return <>{useCountUp(value).toLocaleString()}</>;
}

function useGlassIndicator(selector, deps, quietRef = null) {
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
function useCollapsingTabBar(resetKey, tuckable = true) {
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
const SIDEBAR_COUNT_SKELETON = { display: "inline-block", width: 24, verticalAlign: "middle" };

function Nav({ active, onNavigate, catalog, backendStatus, logicalSeriesCount, authStatus, onSignOut, scanning, onScanLibrary }) {
  const viewer = useViewer();
  const admin = isAdmin(viewer);
  // A reader's Pull List is what they have asked for.
  const items = NAV_ITEMS.filter(({ id }) => can(viewer, `nav.${id}`))
    .map((item) => (item.id === "requests" && !admin ? { ...item, label: "Requests" } : item));
  const [collapsed, setCollapsed] = useCollapsingTabBar(active, canTuck(active, items));
  // While the bar folds or opens, the glass pill sits out: its blur was
  // being re-placed and re-animated on every frame of the width animation,
  // and a phone could not keep that smooth. It pops in, in place, once the
  // bar has finished moving -- the owner's suggestion.
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
        {/* Who is reading, once there is more than one person it could be: a
            tap opens their profile, where switching and signing out live. */}
        {viewer && (authStatus?.household || authStatus?.method === "forms")
          ? <button type="button" className="sidebar-profile" onClick={() => onNavigate("profile")}>
            <ProfileAvatar profile={viewer} /><span>{viewer.name}</span>
          </button>
          : null}
        {authStatus?.method === "forms" && authStatus?.authenticated && !authStatus?.household
          ? <button type="button" className="sign-out-button" onClick={() => onSignOut()}><SignOut size={16} /> Sign out</button>
          : null}
      </div>
    </aside>
  );
}

// A choice from a list, as iOS 26's pop-up button: a capsule showing the
// current value, which opens a glass menu of the options with a check on the
// chosen one. It replaces the native select, whose open list the browser
// draws in its own style. The menu is drawn over everything (a portal, placed
// from the button's position) so a dialog's scrolling body cannot clip it,
// and it joins the dialog stack, so Escape closes the menu and not the dialog
// under it. `label` names the control for assistive technology.
function GlassSelect({ value, options, onChange, label, placeholder = "", disabled = false, className = "" }) {
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

function GlassSelectMenu({ options, value, label, anchor, onChoose, onClose, onDismiss }) {
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

// The bell (rebuilt 2026-09-27). Two halves: what needs you, worked out from
// the library as it stands (notifications.js), which goes away when it is dealt
// with; and what happened, kept per profile on the server, which is read and
// cleared there and so is the same on every device. On a phone it opens as a
// sheet from the bottom the height of the screen -- iOS presents a popover
// that way at compact width -- and elsewhere as a panel under the bell, placed
// from the bell's own position so it stays inside the window whatever sits
// beside the bell.
const NOTIFICATION_KINDS = { download: "Download", request: "Request", source: "Metadata source", file: "Comic file", metadata: "Metadata" };

function NotificationRow({ item, news, fresh, onOpen, onDismiss, onRetry }) {
  const [busy, setBusy] = useState(false);
  const warning = news ? item.tone === "warning" : item.severity !== "error";
  const icon = news && item.cover
    ? <img className="notification-cover" src={item.cover} alt="" loading="lazy" />
    : <span className={`notification-icon ${news && !warning ? "severity-done" : warning ? "severity-warning" : "severity-error"}`}>
      {news && !warning ? <CheckCircle size={20} weight="fill" /> : <WarningCircle size={20} weight={warning ? "regular" : "fill"} />}
    </span>;
  async function retry() {
    setBusy(true);
    try { await onRetry(item); } finally { setBusy(false); }
  }
  // The row opens the thing; clearing is its own control, so acting on a
  // notification and getting rid of it are never the same tap.
  return <li className={`notification-row${fresh ? " notification-row--new" : ""}`} data-id={item.id}>
    <button type="button" className="notification-open" onClick={() => onOpen(item)}>
      {icon}
      <span className="notification-text">
        <strong>{item.title}</strong>
        <small>{news ? item.detail : `${NOTIFICATION_KINDS[item.kind] || "Needs you"} · ${item.detail}`}</small>
        {news ? <time dateTime={item.at}>{fresh ? <span className="sr-only">New, </span> : null}{timeAgo(item.at)}</time> : null}
      </span>
    </button>
    <span className="notification-actions">
      {item.retryJobId ? <button type="button" className="secondary-button notification-retry" onClick={retry} disabled={busy} aria-busy={busy}
        aria-label={`Retry: ${item.title}`}>
        {busy ? <LoadingSpinner size={14} /> : <ArrowCounterClockwise size={14} />} Retry
      </button> : null}
      <button type="button" className="notification-dismiss" onClick={() => onDismiss(item)}
        aria-label={`${news ? "Clear" : "Dismiss"}: ${item.title}`} title={news ? "Clear" : "Dismiss"} data-notification-dismiss>
        <X size={16} />
      </button>
    </span>
  </li>;
}

function NotificationCenter({ bell, anchorRef, onClose }) {
  const phone = usePhoneWidth();
  const dialogRef = useDialog(onClose);
  const headingRef = useRef(null);
  const [place, setPlace] = useState(null);
  // What is new is decided from a fresh answer, not from whatever the bell
  // held when it was tapped: those rows stay marked while the panel is open,
  // and the server is told they have been seen, so the badge does not count
  // them again. Until the answer comes, unread rows stand in.
  const [freshIds, setFreshIds] = useState(null);
  useEffect(() => {
    let live = true;
    bell.onRefresh().then((data) => {
      if (!live) return;
      const ids = new Set((data?.items || bell.activity.items || []).filter((item) => !item.read).map((item) => item.id));
      setFreshIds(ids);
      if (ids.size) bell.onSeen();
    });
    return () => { live = false; };
  }, []);
  // Focus starts on the heading rather than the first control (Clear all),
  // so a stray Enter clears nothing; and when the focused row goes, focus
  // moves to the next row or back to the heading rather than to the page.
  // Once the panel is placed: until then it is hidden, and focusing anything
  // inside a hidden element does nothing (the phone's sheet is never hidden).
  useEffect(() => { if (phone || place) headingRef.current?.focus(); }, [phone, Boolean(place)]);
  const focusNext = useRef(null);
  useEffect(() => {
    if (!focusNext.current) return;
    const wanted = focusNext.current;
    focusNext.current = null;
    const rows = [...(dialogRef.current?.querySelectorAll(".notification-row") || [])];
    const next = rows.find((row) => row.dataset.id === wanted) || rows[0];
    (next?.querySelector("[data-notification-dismiss]") || headingRef.current)?.focus();
  });
  const isFresh = (item) => (freshIds ? freshIds.has(item.id) : !item.read);
  function removing(items, item) {
    const index = items.findIndex((row) => row.id === item.id);
    focusNext.current = items[index + 1]?.id ?? items[index - 1]?.id ?? null;
  }
  useLayoutEffect(() => {
    if (phone) return undefined;
    function measure() {
      const button = anchorRef.current?.getBoundingClientRect();
      if (!button) return;
      const margin = 16;
      const width = Math.min(400, window.innerWidth - margin * 2);
      const top = button.bottom + 8;
      setPlace({
        top, width, maxHeight: window.innerHeight - top - margin,
        // Its right edge under the bell's, pulled in from either side of the window.
        left: Math.max(margin, Math.min(button.right - width, window.innerWidth - margin - width)),
      });
    }
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [phone]);
  useEffect(() => {
    if (phone) return undefined;
    function outside(event) {
      if (dialogRef.current?.contains(event.target) || anchorRef.current?.contains(event.target)) return;
      onClose();
    }
    document.addEventListener("pointerdown", outside, true);
    return () => document.removeEventListener("pointerdown", outside, true);
  }, [phone, onClose]);
  const attention = bell.attention || [];
  const activity = bell.activity.items || [];
  const fresh = activity.filter(isFresh);
  const earlier = activity.filter((item) => !isFresh(item));
  const empty = !attention.length && !activity.length;
  const open = (item) => { onClose(); bell.onOpen(item); };
  const all = [...attention, ...fresh, ...earlier];
  const rows = (items, news) => <ul className="notification-list">{items.map((item) => <NotificationRow key={item.id} item={item} news={news}
    fresh={news && isFresh(item)} onOpen={open}
    onDismiss={(row) => { removing(all, row); (news ? bell.onClear : bell.onDismiss)(row); }} onRetry={bell.onRetry} />)}</ul>;
  const body = <div className="notifications-body">
    {bell.error ? <p className="workbench-error" role="alert">{bell.error}</p> : null}
    {attention.length ? <section aria-labelledby="notifications-attention">
      <h3 id="notifications-attention">Needs you</h3>{rows(attention, false)}
    </section> : null}
    {fresh.length ? <section aria-labelledby="notifications-new"><h3 id="notifications-new">New</h3>{rows(fresh, true)}</section> : null}
    {earlier.length ? <section aria-labelledby="notifications-earlier">
      <h3 id="notifications-earlier">{fresh.length || attention.length ? "Earlier" : "Recent"}</h3>{rows(earlier, true)}
    </section> : null}
    {empty ? <p className="notifications-empty"><CheckCircle size={22} weight="fill" /> You're all caught up</p> : null}
  </div>;
  const head = <header className="notifications-head">
    <h2 id="notifications-title" ref={headingRef} tabIndex={-1}>Notifications</h2>
    {activity.length ? <button type="button" className="notifications-clear" onClick={bell.onClearAll}>Clear all</button> : null}
    {phone ? <button type="button" className="glass-button glass-button--icon library-sheet-close"
      onClick={() => slideSheetAway(dialogRef.current, onClose)} aria-label="Close"><X size={20} /></button> : null}
  </header>;
  if (phone) {
    return createPortal(<div className="modal-backdrop notifications-sheet-backdrop" onMouseDown={onClose}>
      <section className="modal notifications-sheet" role="dialog" aria-modal="true" aria-labelledby="notifications-title"
        ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}>
        <SheetGrabber onClose={onClose} pullAnywhere />
        {head}{body}
      </section>
    </div>, document.body);
  }
  return createPortal(<section className="notifications-popover glass-menu" role="dialog" aria-modal="false" aria-labelledby="notifications-title"
    ref={dialogRef} style={place ? { ...place } : { visibility: "hidden", top: 0, left: 0 }}>
    {head}{body}
  </section>, document.body);
}

// The bell. The app bar carries it on a desktop and every page's header on a
// phone -- one control in several places, so one component.
function NotificationsBell({ bell }) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef(null);
  const count = bellCount(bell.attention, bell.activity);
  const close = useCallback(() => setOpen(false), []);
  return <div className="appbar-notifications">
    <button
      type="button" ref={buttonRef}
      className={`glass-button glass-button--icon appbar-bell${count ? " unread" : ""}${open ? " active" : ""}`}
      onClick={() => setOpen((value) => !value)}
      aria-label={count ? `Notifications: ${count}` : "Notifications"}
      aria-expanded={open} aria-haspopup="dialog"
    >
      <NotificationsIcon />
    </button>
    {open ? <NotificationCenter bell={bell} anchorRef={buttonRef} onClose={close} /> : null}
  </div>;
}

const LoadingSpinner = LoadingIndicator;

// Every page's top, at every width (chosen 2026-09-17, replacing the violet
// app bar): the page's name alone -- the same on every page, so nothing sits
// under it -- then search, its actions, one primary action and
// the bell as glass controls on the right, and an optional row of tools
// (tabs, view and sort) under them. It sticks to the top; once the page moves
// it condenses -- the title shrinks -- and the content
// passing under it blurs away, the way iOS 26 treats its bars. Its height does
// not change as it condenses, so the page never jumps under a scroll.
//
// search: "page" is the page's own field at every width, on a row of its own
// on a phone (the Search page); "phone" is a field on a phone only (Comics
// filters your comics, Discover searches the catalogs -- above 640px the
// sidebar's Search does both); "none" leaves it out.
function PageHeader({ title, leading, actions, primary, search = "none", field, tools, toolsClassName = "", narrow = false }) {
  const headerRef = useRef(null);
  const [condensed, setCondensed] = useState(false);
  // Condensed is simply "the window has moved", read from the window: a
  // sentinel watched by an IntersectionObserver missed the move on an
  // iPhone when a view change left the page scrolled, and the header sat
  // transparent over the content with its band off. Not the rubber band,
  // though: pushed past its end a page bounces, scrollY reads past the
  // range for a moment, and the bounce's return does not always send a last
  // scroll event -- so a page with no range to scroll never condenses, and
  // the finger lifting checks again once the bounce has settled. A long
  // page bounced at its bottom stays condensed, as it should.
  useEffect(() => {
    // Nothing a scroll frame has to lay out: whether the page has room to
    // scroll only changes when its size does, so a ResizeObserver keeps it
    // (they run after layout, for free), and a frame reads scrollY alone.
    // Reading scrollHeight there was a forced layout under the tab bar's
    // own animation, every frame, and the bar stuttered.
    let range = document.documentElement.scrollHeight - window.innerHeight;
    // Past the noise: during a phone's momentum scrollY wanders by fractions
    // of a pixel, and Safari's collapsing toolbar sends scroll events of its
    // own; 8px is a move, not a tremor.
    const sync = () => setCondensed(window.scrollY > 8 && range > 0);
    let frame = 0;
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; sync(); }); };
    // Measured a frame after the size changes, not inside the observer's
    // own delivery: the header's height is set from another observer in
    // the same pass, and answering in the pass is a loop it warns about.
    const measure = () => { range = document.documentElement.scrollHeight - window.innerHeight; onScroll(); };
    const sized = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    sized?.observe(document.body);
    let settle = 0;
    const later = () => { window.clearTimeout(settle); settle = window.setTimeout(sync, 400); };
    sync();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("scrollend", onScroll, { passive: true });
    window.addEventListener("resize", measure);
    window.addEventListener("touchend", later, { passive: true });
    return () => {
      sized?.disconnect();
      cancelAnimationFrame(frame);
      window.clearTimeout(settle);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("scrollend", onScroll);
      window.removeEventListener("resize", measure);
      window.removeEventListener("touchend", later);
    };
  }, []);
  // Sticky things further down the page sit under the header, not behind it.
  useEffect(() => {
    const header = headerRef.current;
    if (!header || typeof ResizeObserver === "undefined") return undefined;
    const root = document.documentElement;
    // Written a frame later, not inside the observer's delivery: on a phone
    // the page's own padding follows this height, so writing it in the pass
    // resized the page mid-pass, which the observer warns about.
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => root.style.setProperty("--page-header-height", `${header.offsetHeight}px`));
    });
    observer.observe(header);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      root.style.removeProperty("--page-header-height");
    };
  }, []);
  return <>
    <header ref={headerRef} className={`page-header page-header--search-${search}${narrow ? " page-header--narrow" : ""}${condensed ? " page-header--condensed" : ""}`}>
      <div className="page-header-heading">
        {leading}
        <div className="page-header-title"><h1>{title}</h1></div>
      </div>
      {search === "none" ? null : <HeaderSearchField field={field} />}
      <div className="page-header-actions">
        {actions}
        {primary}
        <HeaderBell />
        <HeaderProfile />
      </div>
      {tools ? <div className={`page-header-tools ${toolsClassName}`}>{tools}</div> : null}
    </header>
  </>;
}

// The header's search field. Without `field` it keeps its own draft, starts
// from the app's current query, and Enter runs the app's search; a page whose
// search it is passes `field` to own the value instead.
function HeaderSearchField({ field, className = "page-header-search" }) {
  const header = useContext(HeaderContext);
  const query = header?.query || "";
  const [ownDraft, setOwnDraft] = useState(query);
  useEffect(() => { setOwnDraft(query); }, [query]);
  const inputRef = useRef(null);
  const value = field ? field.value : ownDraft;
  const change = field ? field.onChange : setOwnDraft;
  function submit() {
    if (field) field.onSubmit();
    else header?.onSearch(ownDraft);
  }
  function clear() {
    if (field) field.onClear();
    else {
      setOwnDraft("");
      // Showing results, clearing is leaving them, not only emptying the box.
      if (query) header?.onClearSearch?.();
    }
    inputRef.current?.focus();
  }
  return <form className={`glass-field ${className}`} role="search" onSubmit={(event) => { event.preventDefault(); submit(); }}>
    <SearchIcon />
    <input
      ref={inputRef} type="search" enterKeyHint="search" value={value} autoFocus={field?.autoFocus}
      onChange={(event) => change(event.target.value)}
      // Enter runs the search itself rather than leaning on the form's
      // implicit submission, which not every browser raises for every Enter.
      // Not while an input method is composing: there Enter picks a character.
      onKeyDown={(event) => {
        if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
        event.preventDefault();
        submit();
      }}
      aria-label={field?.label || "Search your library and comic catalogs"}
      placeholder={field?.placeholder || "Search comics…"}
    />
    {value ? <button type="button" className="glass-field-clear" onClick={clear} aria-label="Clear search"><ClearSearchIcon /></button> : null}
    {/* Implicit submission needs a real submit control to be reliable, and a
        keyboard user gets something to land on. */}
    <button type="submit" className="sr-only">Search</button>
  </form>;
}

function Ownership({ series, compact = false }) {
  const percent = Math.max(5, Math.round((series.owned / series.total) * 100));
  // "Match needs attention" is the admin's to act on. A reader sees what is
  // owned -- as a count alone, since a run waiting on its match has no total
  // to trust yet.
  const admin = isAdmin(useViewer());
  const reviewing = series.status === "warning" && admin;
  const catalogUnknown = series.catalogKnown === false || series.status === "unknown" || (series.status === "warning" && !admin);
  const volumeCount = series.inventory?.editionCount || 0;
  const collectedOnly = volumeCount > 0 && !series.inventory?.directIssueFiles;
  const coveredIssues = series.issues?.filter((issue) => issue.ownership !== "unowned").length || 0;
  const arcCoverageKnown = collectedOnly && coveredIssues > 0;
  const ownedLabel = arcCoverageKnown ? `${coveredIssues} issue${coveredIssues === 1 ? "" : "s"} owned` : collectedOnly ? `${volumeCount} volume${volumeCount === 1 ? "" : "s"} owned` : `${series.owned} owned`;
  const label = reviewing ? seriesAttentionLabel(series) : catalogUnknown ? ownedLabel : compact ? `${series.owned} of ${series.total} owned` : `${series.owned} of ${series.total}`;
  const coverageDetail = arcCoverageKnown ? `${coveredIssues} issue${coveredIssues === 1 ? "" : "s"} collected in ${volumeCount} volume${volumeCount === 1 ? "" : "s"}${series.family ? " · complete-series progress is tracked separately" : ""}` : "";
  const detail = reviewing ? [coverageDetail, seriesAttentionDetail(series)].filter(Boolean).join(" · ") : series.isCollectionSeries ? series.ownership : coverageDetail || series.ownership;
  // An issue that is not out yet is not missing. Counting the two together
  // read as a gap to close on a run that is simply still being published.
  const summary = series.releaseSummary || {};
  const shortfall = Math.max(0, series.total - series.owned);
  const upcoming = Math.max(0, Number(summary.upcoming ?? 0));
  const missing = summary.releasedMissing == null
    ? Math.max(0, shortfall - upcoming)
    : Math.max(0, Number(summary.releasedMissing));
  const plural = (count) => (count === 1 ? "" : "s");
  const compactDetail = reviewing
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
  const showsConciseDetail = compact || (catalogUnknown && !reviewing);
  const visibleDetail = showsConciseDetail ? compactDetail : detail;
  if (compact) {
    return <span className={`ownership ${series.status} compact`}>
      <span className="ownership-label">{catalogUnknown || reviewing
        ? label
        : <><strong>{series.owned}</strong> of {series.total} Owned</>}</span>
      {!reviewing && !catalogUnknown ? <span className="progress"><i style={{ width: `${percent}%` }} /></span> : null}
    </span>;
  }
  return <div className={`ownership ${reviewing || series.status !== "warning" ? series.status : ""}`}><div className="ownership-label">{reviewing ? <WarningCircle size={19} weight="fill" /> : catalogUnknown ? <ClockCounterClockwise size={19} weight="fill" /> : <CheckCircle size={19} weight="fill" />}<strong>{label}</strong></div>{!reviewing && !catalogUnknown ? <div className="progress"><i style={{ width: `${percent}%` }} /></div> : null}{visibleDetail && visibleDetail !== label ? <span>{visibleDetail}</span> : null}</div>;
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

function SeriesList({ series, onOpen, onRead, reading, view }) {
  // --card-index staggers the arrival; past the first screenful there is
  // nothing to see, so the delay stops climbing.
  //
  // The card is an article wrapping a full-bleed button rather than a button
  // itself: Read has to be its own target, and a button inside a button is
  // not a thing a browser will render. `.pull-card` has always been built
  // this way, so the hover and press rules are shared with it.
  if (view === "grid") return <div className="series-grid">{series.map((item, index) => <article className="series-card" style={{ "--card-index": Math.min(index, 11) }} key={item.id}>
    <span className="series-card-art">
      <SeriesCover series={item} />
      <ReadRunOverlay run={item} reading={reading} onRead={onRead} />
    </span>
    <span className="series-card-identity"><strong>{item.title}</strong><span className="series-card-byline">{item.publisher} • {item.year}</span></span>
    <span className="series-card-statuses"><PublicationStatus series={item} /><MonitoringStatus series={item} /></span>
    <span className="series-card-rule" />
    <Ownership series={item} compact />
    {/* Last, and over the whole card: the card opens the run as it always
        did, and Read sits above it with a target of its own. The card cannot
        be the button any more -- a button inside a button is not a thing. */}
    <button type="button" className="discover-open series-card-open" onClick={() => onOpen(item)} aria-label={`${item.title}. Show details`} />
  </article>)}</div>;
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
function PullButton({ state, idleLabel, readerLabel = "Request", requestedLabel, onClick, size = "sm" }) {
  // A reader asks rather than pulls: "Request", then "Requested" until the
  // admin decides.
  const reader = !isAdmin(useViewer());
  const labels = reader ? READER_PULL_LABELS : PULL_LABELS;
  const settled = state === PULL_STATES.queued || state === PULL_STATES.owned;
  // Requested reads as settled, like On Pull List: the fill that says "done".
  const look = state === PULL_STATES.requested ? PULL_STATES.queued : state;
  return <button type="button" className={`pull-button pull-button-${size} pull-button-${look}`}
    onClick={onClick} disabled={state !== PULL_STATES.idle}
    aria-busy={state === PULL_STATES.pending || undefined}>
    <span>{state === PULL_STATES.idle ? (reader ? readerLabel : idleLabel)
      : state === PULL_STATES.requested && requestedLabel ? requestedLabel : labels[state]}</span>
    {state === PULL_STATES.pending ? <LoadingIndicator size={16} />
      : state === PULL_STATES.requested ? <Hourglass size={16} />
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
  // A reader cannot connect anything: it is the admin's to set up.
  readerUnavailable: { title: "Release dates aren't set up", detail: "The admin can connect Metron, the source of comic shop shipping dates." },
};

/**
 * One week of comics, scrolled sideways.
 *
 * The chevrons page by the visible width rather than by a card, because a row
 * that moves 120px on a click reads as a twitch rather than as navigation.
 */
function ReleaseShelf({ title, date, subtitle, state, issues = [], error, pulled, waiting, onPull, onOpen, onRetry }) {
  const copyKey = state === "unavailable" && !isAdmin(useViewer()) ? "readerUnavailable" : state;
  const { scroller, atStart, atEnd, measure, page } = useShelfPaging([issues.length, state]);
  const heading = date ? `${title} - ${date}` : title;
  return <section className="release-shelf" aria-label={heading}>
    <header>
      <div className="release-shelf-heading"><h2>{heading}</h2>{subtitle ? <p>{subtitle}</p> : null}</div>
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
      {issues.map((issue) => <PullCard issue={issue} state={pullState(issue, pulled, waiting)}
        onPull={onPull} onOpen={onOpen} key={issueKey(issue)} />)}
    </div> : null}
    {state === "error" ? <div className="shelf-message" role="status">
      <WarningCircle size={18} />
      <span><strong>This week could not be fetched</strong><small>{error}</small></span>
      <button type="button" onClick={onRetry}>Try again</button>
    </div> : null}
    {state === "empty" || state === "unavailable" ? <div className="shelf-message">
      <MagnifyingGlass size={18} />
      <span><strong>{SHELF_COPY[copyKey].title}</strong><small>{SHELF_COPY[copyKey].detail}</small></span>
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

// The Search page's "Recently Searched", after the Apple TV app's: what was
// opened from results, as cards of a cover, a title and a line under it. A
// library run no longer in the library drops out.
function RecentSearches({ entries, allSeries, onClear, onSearch, onOpenSeries, onOpenRun }) {
  const cards = entries.map((entry) => {
    if (entry.kind === "query") {
      return { key: entry.key, title: entry.text, detail: "Search", query: true, open: () => onSearch(entry.text) };
    }
    if (entry.kind === "series") {
      const series = allSeries.find((item) => String(item.id) === String(entry.id));
      return series && { key: entry.key, title: series.title, cover: series.cover,
        detail: [series.publisher, series.year].filter(Boolean).join(" · ") || "In your library",
        open: () => onOpenSeries(series) };
    }
    const run = entry.item;
    return { key: entry.key, title: run.title, cover: run.cover,
      detail: [run.publisher, run.yearLabel || run.yearBegan].filter(Boolean).join(" · ") || "Comic catalog",
      open: () => onOpenRun(run) };
  }).filter(Boolean);
  if (!cards.length) return null;
  return <section className="recent-searches" aria-labelledby="recent-searches-title">
    <header>
      <h2 id="recent-searches-title">Recently Searched</h2>
      <button type="button" className="recent-searches-clear" onClick={onClear}>Clear</button>
    </header>
    <div className="recent-search-grid">
      {cards.map((card) => <button type="button" className="recent-search" key={card.key} onClick={card.open}>
        {card.query
          ? <span className="recent-search-art recent-search-query" aria-hidden="true"><MagnifyingGlass size={20} /></span>
          : <DiscoverCover src={card.cover} alt="" className="recent-search-art" glyph={18} />}
        <span className="recent-search-copy">
          <strong title={card.title}>{card.title}</strong>
          <small>{card.detail}</small>
        </span>
      </button>)}
    </div>
  </section>;
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

function LibraryLoadingSkeleton({ scan = null, view = "grid" }) {
  const { total, processed } = scanCounts(scan);
  return <div className="library-loading" role="status" aria-live="polite" aria-busy="true">
    <section className="library-loading-message">
      <span className="library-loading-icon"><LoadingSpinner size={21} /></span>
      {scan
        ? <span><strong>Scanning your library…</strong><small>{total ? `Comic ${Math.min(processed + 1, total)} of ${total} · your runs appear as they are found.` : "Counting your comic files. Nothing is renamed or moved."}</small></span>
        : <span><strong>Loading your library…</strong><small>Bringing in your comic runs, covers, and collection status.</small></span>}
    </section>
    <div className="library-loading-tools" aria-hidden="true"><i /><i /><i /></div>
    {/* The shape the comics will arrive in. It drew a table whichever view
        was chosen, left over from when the list was the default, so a grid
        library rearranged itself the moment it loaded. */}
    {view === "grid"
      ? <section className="library-loading-grid series-grid" aria-hidden="true">
        {[0, 1, 2, 3, 4, 5, 6, 7].map((item) => <span className="library-loading-card" key={item}>
          <span className="library-loading-cover" />
          <span className="library-loading-copy"><i /><i /></span>
          <span className="library-loading-progress"><i /><i /></span>
        </span>)}
      </section>
      : <section className="library-loading-table" aria-hidden="true">
        <header><i /><i /><i /><i /></header>
        {[0, 1, 2, 3, 4].map((item) => <article key={item}><span className="library-loading-cover" /><span className="library-loading-copy"><i /><i /><i /></span><span className="library-loading-progress"><i /><i /></span><span className="library-loading-cell" /><span className="library-loading-cell short" /></article>)}
      </section>}
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
    emptyTitle: "Nothing on your wanted list",
    emptyDetail: "Follow a run from Discover or a series, or request a replacement from Library health.",
  },
  downloading: {
    emptyTitle: "Nothing is downloading",
    emptyDetail: "Issues appear here while SABnzbd is fetching them and while Flipparr is importing them.",
  },
  acquired: {
    emptyTitle: "Nothing has arrived recently",
    emptyDetail: `Issues appear here for ${RECENT_ARRIVAL_DAYS} days once they are downloaded, validated and added to your library.`,
  },
  failed: {
    emptyTitle: "Nothing has failed",
    emptyDetail: "A download that cannot finish on its own waits here for you to retry it or pick another release.",
  },
};

// Everything the phone's toolbar used to hold, in one sheet from the bottom of
// the screen: grid or list, the sort, the Following filter, and Runs or
// Collections when collected editions are on. Changes apply as they are made;
// Done, the backdrop and Escape all close it.
function LibraryViewSheet({ view, onView, sort, onSort, followingOnly, onFollowingOnly, inProgressOnly, onInProgressOnly, editionsOn, scope, onScope, onClose }) {
  const dialogRef = useDialog(onClose);
  return <div className="modal-backdrop library-sheet-backdrop" onMouseDown={onClose}>
    <section className="library-sheet" role="dialog" aria-modal="true" aria-labelledby="library-sheet-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}>
      <SheetGrabber onClose={onClose} detents pullAnywhere />
      <header>
        <h2 id="library-sheet-title">View &amp; sort</h2>
        <button type="button" className="glass-button glass-button--icon library-sheet-close"
          onClick={(event) => slideSheetAway(event.currentTarget.closest(".library-sheet"), onClose)} aria-label="Close"><X size={20} /></button>
      </header>
      {/* The desktop tools row's controls: segmented, with the sliding thumb. */}
      {editionsOn ? <fieldset>
        <legend>Show</legend>
        <GlassSegmented label="Show" value={scope} onChange={onScope} className="library-sheet-segmented"
          items={[{ id: "runs", label: "Runs", icon: <ListBullets size={17} /> }, { id: "collections", label: "Collections", icon: <Books size={17} /> }]} />
      </fieldset> : null}
      {scope === "runs" ? <fieldset>
        <legend>View</legend>
        <GlassSegmented label="View" value={view} onChange={onView} className="library-sheet-segmented"
          items={[{ id: "grid", label: "Grid", icon: <GridViewIcon /> }, { id: "list", label: "List", icon: <ListViewIcon /> }]} />
      </fieldset> : null}
      <fieldset>
        <legend>Sort by</legend>
        <div className="library-sheet-options" role="radiogroup" aria-label="Sort by">
          {SORT_OPTIONS.map((option) => <button type="button" role="radio" aria-checked={option.value === sort} onClick={() => onSort(option.value)} key={option.value}>
            {option.label}
            {option.value === sort ? <Check size={18} weight="bold" aria-hidden="true" /> : null}
          </button>)}
        </div>
      </fieldset>
      {scope === "runs" ? <>
        <FollowSwitch following={followingOnly} label="Following only" onChange={onFollowingOnly} />
        <FollowSwitch following={inProgressOnly} label="In progress only" onChange={onInProgressOnly} />
      </> : null}
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
  // The trigger is a segment of the Sort and Following capsule, and the menu
  // is glass that grows out of it, as iOS 26's pull-down menus do.
  return <div className="sort-field" ref={rootRef}>
    <button
      type="button" className="sort-trigger glass-capsule-segment" aria-haspopup="listbox" aria-expanded={open}
      aria-label={`Sort by. ${current.label}`} onClick={() => setOpen((value) => !value)}
    >{current.label}<ChevronDown /></button>
    {open ? <div className="sort-menu glass-menu">
      <ul className="sort-menu-options" role="listbox" aria-label="Sort by" onKeyDown={onListKeyDown}>
        {SORT_OPTIONS.map((option) => (
          <li key={option.value} role="none">
            <button
              type="button" role="option" aria-selected={option.value === value}
              className={option.value === value ? "selected" : ""}
              onClick={() => choose(option)}
            ><span className="sort-menu-check" aria-hidden="true">{option.value === value ? <Check size={16} weight="bold" /> : null}</span>{option.label}</button>
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
// Null until the first answer: the grid's Recent order needs it, and a grid
// drawn before it came was drawn in added order and then re-sorted under
// the eye, the comics read last jumping to the front. A refresh keeps the
// last map until the next one lands, so nothing flickers.
function useRunReading(version) {
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

function LibraryView({ onNavigate, onOpenSeries, onOpenCollection, onSearch, onRead, readingVersion, catalog, backendStatus }) {
  const libraryViewer = useViewer();
  const libraryAdmin = isAdmin(libraryViewer);
  const [viewSheetOpen, setViewSheetOpen] = useState(false);
  // The page's own search: it narrows what you already have, as you type.
  const [query, setQuery] = useState("");
  const queryParts = useMemo(() => searchQueryParts(query), [query]);
  const searching = query.trim().length > 0;
  // How the grid was left last time, in this browser. Recent and covers by
  // default: covers are the point of a comic library, and Recent is Kindle's
  // answer to "where was I" -- one grid, with what you are reading on top.
  const [prefs] = useState(loadLibraryPrefs);
  const [view, setView] = useState(prefs.view);
  const [scope, setScope] = useState(prefs.scope);
  const [sort, setSort] = useState(prefs.sort);
  const [followingOnly, setFollowingOnly] = useState(prefs.followingOnly);
  const [inProgressOnly, setInProgressOnly] = useState(prefs.inProgressOnly);
  useEffect(() => {
    saveLibraryPrefs({ view, scope, sort, followingOnly, inProgressOnly });
  }, [view, scope, sort, followingOnly, inProgressOnly]);
  const fallbackSeries = backendStatus === "offline" ? DEMO_SERIES : [];
  const series = useMemo(() => logicalCatalogSeries(catalog, fallbackSeries), [catalog, backendStatus]);
  const families = useMemo(() => (catalog?.families || []).map((family) => ({ ...family, runCount: family.runs?.length || family.runCount || 0 })), [catalog?.families]);
  const runReading = useRunReading(readingVersion);
  // Nothing is drawn until what decides its order is in: the catalog, and
  // for Recent and In progress the reading map as well.
  const initialLoading = (backendStatus === "loading" && !catalog) || (runReading === null && (sort === "recent" || inProgressOnly));
  // The server has always reported this; nothing read it, so arriving during a
  // scan showed the "no comics yet" empty state on a library that was filling.
  const activeScan = catalog?.activeScan || null;
  const editionsOn = Boolean(catalog?.collectedEditionsEnabled);
  const effectiveScope = editionsOn ? scope : "runs";
  const scopedSeries = useMemo(() => editionsOn ? series : series.filter((item) => !item.isCollectionSeries), [editionsOn, series]);
  // Fetched before the filters and the sort need it: In progress and Recent
  // both read from where each run was left, which is this map's to know.
  const followedSeries = useMemo(() => followingOnly ? scopedSeries.filter((item) => item.monitoringStatus === "monitored") : scopedSeries, [followingOnly, scopedSeries]);
  const readingSeries = useMemo(() => inProgressOnly ? followedSeries.filter((item) => inProgress(runReading?.[String(item.id)])) : followedSeries, [inProgressOnly, followedSeries, runReading]);
  const filteredSeries = useMemo(() => searching ? readingSeries.filter((item) => libraryRunMatches(item, queryParts)) : readingSeries, [readingSeries, searching, queryParts]);
  const displayedSeries = useMemo(() => sortLibrary(filteredSeries, sort, runReading), [filteredSeries, sort, runReading]);
  // The View & sort button marks when the library isn't showing its default.
  const viewCustomized = view !== LIBRARY_DEFAULTS.view || sort !== LIBRARY_DEFAULTS.sort || followingOnly || inProgressOnly || effectiveScope !== "runs";
  const sortedFamilies = useMemo(() => {
    const needle = queryParts.title.toLowerCase();
    const matching = searching ? families.filter((family) => String(family.name || "").toLowerCase().includes(needle)) : families;
    return sortLibrary(matching, sort);
  }, [families, sort, searching, queryParts]);
  return (
    <>
      <PageHeader
        title="Comics"
        search="phone"
        field={{
          value: query,
          onChange: setQuery,
          // It filters as you type, so Search only puts the keyboard away.
          onSubmit: () => document.activeElement?.blur?.(),
          onClear: () => setQuery(""),
          label: "Search your comics",
          placeholder: "Search your comics…",
        }}
        actions={<button
          type="button" className="glass-button glass-button--icon library-phone-view"
          aria-label={viewCustomized ? "View and sort, changed from the default" : "View and sort"}
          aria-haspopup="dialog" aria-expanded={viewSheetOpen} onClick={() => setViewSheetOpen(true)}
        ><ViewOptionsIcon />{viewCustomized ? <span className="library-view-dot" aria-hidden="true" /> : null}</button>}
        toolsClassName="library-tools-row"
        tools={<div className="library-tools">
          {/* The layout choices on the left as segmented controls; how the
              grid is ordered and filtered on the right, in one glass
              capsule, Following turning violet while it filters. */}
          {editionsOn ? <GlassSegmented label="Choose catalog grouping" value={effectiveScope} onChange={setScope} className="library-scope-toggle"
            items={[{ id: "runs", label: "Runs", icon: <ListBullets size={17} /> }, { id: "collections", label: "Collections", icon: <Books size={17} /> }]} /> : null}
          {effectiveScope === "runs" ? <GlassSegmented label="Choose library view" value={view} onChange={setView} className="library-view-toggle"
            items={[{ id: "grid", title: "Grid view", icon: <GridViewIcon /> }, { id: "list", title: "List view", icon: <ListViewIcon /> }]} /> : null}
          <div className="glass-capsule library-refine">
            <SortMenu value={sort} onChange={setSort} />
            {effectiveScope === "runs" ? <>
              <span className="glass-capsule-divider" aria-hidden="true" />
              <button type="button" className={`glass-capsule-segment filter-button${followingOnly ? " active" : ""}`} aria-pressed={followingOnly} onClick={() => setFollowingOnly((value) => !value)}><FollowingIcon /> Following</button>
              <span className="glass-capsule-divider" aria-hidden="true" />
              <button type="button" className={`glass-capsule-segment filter-button${inProgressOnly ? " active" : ""}`} aria-pressed={inProgressOnly} onClick={() => setInProgressOnly((value) => !value)}><BookOpen size={17} weight="fill" /> In progress</button>
            </> : null}
          </div>
        </div>}
      />
      {/* View, sort and the filters are a sheet on a phone. */}
      {viewSheetOpen ? <LibraryViewSheet
        view={view} onView={setView} sort={sort} onSort={setSort}
        followingOnly={followingOnly} onFollowingOnly={setFollowingOnly}
        inProgressOnly={inProgressOnly} onInProgressOnly={setInProgressOnly}
        editionsOn={editionsOn} scope={effectiveScope} onScope={setScope}
        onClose={() => setViewSheetOpen(false)}
      /> : null}
      <div className="dashboard-body">
      {initialLoading ? <LibraryLoadingSkeleton view={view} /> : null}
      {!initialLoading && activeScan && !series.length ? <LibraryLoadingSkeleton scan={activeScan} view={view} /> : null}
      {!initialLoading && !(activeScan && !series.length) ? <>
      {backendStatus === "offline" ? <div className="backend-banner"><WarningCircle size={19} weight="fill" /> Showing sample comics because your library is unavailable.</div> : null}
      {effectiveScope === "collections" ? (sortedFamilies.length ? <CollectionGroups families={sortedFamilies} onOpenCollection={onOpenCollection} /> : <CollectionEmpty query={query.trim()} />) : displayedSeries.length ? <SeriesList series={displayedSeries} onOpen={(item) => item.isCollectionSeries && editionsOn ? onOpenCollection(item.collection) : onOpenSeries(item)} onRead={onRead} reading={runReading} view={view} /> : searching ? <div className="empty-state"><MagnifyingGlass size={35} weight="duotone" /><strong>No comics match “{query.trim()}”</strong><span>{can(libraryViewer, "discover.search") ? "The comic catalogs may have it." : "Try another title or creator."}</span>{can(libraryViewer, "discover.search") ? <button className="ghost-button" onClick={() => onSearch(query)}>Search the catalogs</button> : null}</div> : inProgressOnly ? <div className="empty-state"><BookOpen size={35} weight="duotone" /><strong>Nothing in progress</strong><span>Start a run and it shows up here.</span><button className="ghost-button" onClick={() => setInProgressOnly(false)}>Show all runs</button></div> : followingOnly ? <div className="empty-state"><CheckCircle size={35} weight="duotone" /><strong>No followed runs</strong><span>{libraryAdmin ? "Open any run and choose Follow run to monitor future issues." : "Open any run and choose Request follow to ask for its new issues."}</span><button className="ghost-button" onClick={() => setFollowingOnly(false)}>Show all runs</button></div> : <CatalogEmpty onAdd={() => onNavigate("import")} />}
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
// Two pages in one. mode "discover" is the week's releases, with a phone's
// search over them; mode "search" is the Search page above 640px -- the same
// results, over your library and the catalogs, and nothing else.
function DiscoverView({
  mode = "discover", query, catalog, backendStatus, onSearch, onClearSearch,
  onOpenSeries, onOpenCollection, onDiscoverRequest, onUnfollowRun, onPullIssue, onPullIssues, onPullArc,
}) {
  const [draft, setDraft] = useState(query || "");
  const [releases, setReleases] = useState(() => {
    const last = lastAnswer("/api/v1/discover/releases");
    return last ? { state: "done", data: last } : { state: "loading", data: null };
  });
  const [discovery, setDiscovery] = useState(
    { state: "idle", results: [], error: "", providersChecked: [], providersAnswered: [], fallbacks: [] });
  const [pulled, setPulled] = useState({});
  const [drawer, setDrawer] = useState(null);
  // Story arcs matching the search, asked beside it so runs are not held up.
  const [arcs, setArcs] = useState([]);
  const latestQuery = useRef(query);
  latestQuery.current = query;
  const searching = Boolean(query);
  const [recent, setRecent] = useState(() => readRecent(profileStorage()));
  function remember(kind, item) {
    setRecent((current) => {
      const next = rememberRecent(current, recentEntry(kind, item));
      writeRecent(profileStorage(), next);
      return next;
    });
  }
  function clearRecent() {
    setRecent([]);
    writeRecent(profileStorage(), []);
  }
  const openLibraryRun = (item) => item.isCollectionSeries ? onOpenCollection(item.collection) : onOpenSeries(item);
  const openCatalogRun = (run) => setDrawer({ kind: "run", item: run });

  const allSeries = useMemo(
    () => logicalCatalogSeries(catalog, backendStatus === "offline" ? DEMO_SERIES : []),
    [catalog, backendStatus]);
  const libraryMatches = useMemo(() => {
    if (!query) return [];
    const parts = searchQueryParts(query);
    return allSeries.filter((item) => libraryRunMatches(item, parts));
  }, [allSeries, query]);

  async function loadReleases() {
    // The skeleton only when there is nothing to show yet; a refresh of
    // shelves already up lands behind them.
    setReleases((current) => (current.data ? current : { state: "loading", data: null }));
    try {
      setReleases({ state: "done", data: await apiRequest("/api/v1/discover/releases") });
    } catch (error) {
      setReleases({ state: "done", data: { available: true, error: error.message } });
    }
  }
  useEffect(() => {
    if (searching || mode === "search" || backendStatus === "offline") return;
    loadReleases();
  }, [searching, mode, backendStatus]);

  async function searchProviders(value = query) {
    const cleaned = String(value || "").trim();
    if (cleaned.length < 2 || backendStatus === "offline") {
      setDiscovery({ state: "done", results: [], providersChecked: [], providersAnswered: [], fallbacks: [],
        error: backendStatus === "offline" ? "Library service is unavailable" : "" });
      return;
    }
    setDiscovery({ state: "loading", results: [], error: "", providersChecked: [], providersAnswered: [], fallbacks: [] });
    setArcs([]);
    apiRequest(`/api/v1/discover/arcs?query=${encodeURIComponent(cleaned)}`)
      .then((result) => { if (cleaned === String(latestQuery.current || "").trim()) setArcs(result.arcs || []); })
      .catch(() => setArcs([]));
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
  // Every search run is remembered, however it was started: the field, a
  // "Did you mean", or Comics sending a search on.
  useEffect(() => { if (query) remember("query", query); }, [query]);

  function mark(key, state) { setPulled((current) => ({ ...current, [key]: state })); }
  // A reader's pull is a request: its card says so until the admin decides.
  const settledBy = (result) => (result?.requested ? PULL_STATES.requested : PULL_STATES.queued);
  // What this profile has asked for and is still waiting on, from any device.
  const waiting = useMemo(() => waitingKeys(catalog?.memberRequests), [catalog]);
  async function pullIssue(issue) {
    const key = issueKey(issue);
    mark(key, PULL_STATES.pending);
    const result = await onPullIssue(issue);
    mark(key, result?.ok ? settledBy(result) : PULL_STATES.idle);
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
    mark(key, result?.ok ? settledBy(result) : PULL_STATES.idle);
  }
  // From the run drawer. Following, or taking every released issue or the
  // whole of an ended run, settles the card behind it; a handful of chosen
  // issues does not, because the card's own button is still on offer.
  const runKey = (item) => `run:${item?.provider}-${item?.providerSeriesId}`;
  async function followFromDrawer(target) {
    const result = await onDiscoverRequest(target);
    if (result?.ok && drawer?.item) mark(runKey(drawer.item), settledBy(result));
    return result;
  }
  // Unfollowing frees the card to offer the run again.
  async function unfollowFromDrawer(target) {
    const result = await onUnfollowRun(target);
    if (result?.ok && drawer?.item) mark(runKey(drawer.item), PULL_STATES.idle);
    return result;
  }
  async function pullFromDrawer(target) {
    const result = await onPullIssues(target);
    if (result?.ok && (target.released || target.complete) && drawer?.item) mark(runKey(drawer.item), settledBy(result));
    return result;
  }

  const libraryState = libraryMatchState(catalog, backendStatus, libraryMatches);
  const data = releases.data || {};
  const picks = useMemo(() => weeklyPicks(data), [data]);
  const { fresh, ownedCount } = splitSearchResults(discovery.results);
  const progress = providerProgress(discovery);
  const outstanding = progress.filter((item) => item.status === "searching");
  const searchField = {
    value: draft,
    onChange: setDraft,
    onSubmit: () => onSearch(draft),
    onClear: () => { setDraft(""); if (searching) onClearSearch(); },
    label: mode === "search" ? "Search your library and comic catalogs" : "Search comic catalogs",
    placeholder: "Title, creator or publisher…",
  };
  // The Search page before a search is only its field, in the middle of the
  // page, ready to type into; once there are results it moves to the header.
  const searchPrompt = mode === "search" && !searching;

  return <>
    <PageHeader
      title={mode === "search" ? "Search" : "Discover"}
      search={searchPrompt ? "none" : mode === "search" ? "page" : "phone"}
      field={searchField}
    />

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
            onOpen={(item) => { remember("series", item); openLibraryRun(item); }}
            key={series.id} />)}
        </div> : <p className="discover-note">Nothing in your library matches “{query}”.</p>}
      </section>

      {arcs.length ? <section className="discover-results" aria-label="Story arcs">
        <header><h2>{arcs.length} <span>{arcs.length === 1 ? "Story Arc" : "Story Arcs"}</span></h2></header>
        <div className="arc-results">
          {arcs.map((arc) => <button type="button" className="arc-row" onClick={() => setDrawer({ kind: "arc", arc })} key={arc.providerArcId}>
            <span className="arc-row-icon" aria-hidden="true"><Books size={20} /></span>
            <span><strong>{arc.name}</strong><small>Story arc</small></span>
            <CaretRight size={16} aria-hidden="true" />
          </button>)}
        </div>
      </section> : null}

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
            state={pulled[`run:${item.provider}-${item.providerSeriesId}`]
              || (runRequested(waiting, item.provider, item.providerSeriesId) ? PULL_STATES.requested : PULL_STATES.idle)}
            onPull={pullRun} onOpen={(run) => { remember("run", run); openCatalogRun(run); }}
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
    </> : mode === "search" ? <>
      <div className="empty-state search-empty">
        <strong>Search your library and the comic catalogs</strong>
        <HeaderSearchField field={{ ...searchField, autoFocus: true }} className="search-empty-field" />
        <span>Try a title, a creator's full name or a publisher, and add a four-digit year to narrow it.</span>
      </div>
      <RecentSearches entries={recent} allSeries={allSeries} onClear={clearRecent} onSearch={onSearch}
        onOpenSeries={(item) => { remember("series", item); openLibraryRun(item); }}
        onOpenRun={(run) => { remember("run", run); openCatalogRun(run); }} />
    </> : <>
      {/* What this week is worth looking at, ranked against the library. It is
          only drawn when there is something to say: when Metron is not
          connected, or a week failed, the weeks below already say so, and
          saying it twice on one screen is noise. */}
      {picks.issues.length ? <ReleaseShelf title="Worth a look this week"
        subtitle={picks.state === "partial"
          ? `Runs you follow, then new #1s — from ${picks.weeksUsed} of ${picks.weeksTotal} weeks, one could not be fetched.`
          : "New issues of runs you follow, then this week's #1s."}
        state="ready" issues={picks.issues}
        pulled={pulled} waiting={waiting} onPull={pullIssue} onOpen={(issue) => setDrawer({ kind: "issue", issue })} /> : null}
      <ReleaseShelf title="Latest Releases" date={formatShelfDate(data.latest?.date)}
        state={shelfState(data.latest, data.available, releases.state === "loading")}
        issues={data.latest?.issues} error={data.latest?.error || data.error}
        pulled={pulled} waiting={waiting} onPull={pullIssue} onOpen={(issue) => setDrawer({ kind: "issue", issue })}
        onRetry={loadReleases} />
      <ReleaseShelf title="Upcoming Releases" date={formatShelfDate(data.upcoming?.date)}
        state={shelfState(data.upcoming, data.available, releases.state === "loading")}
        issues={data.upcoming?.issues} error={data.upcoming?.error || data.error}
        pulled={pulled} waiting={waiting} onPull={pullIssue} onOpen={(issue) => setDrawer({ kind: "issue", issue })}
        onRetry={loadReleases} />
      {/* The week before last: a comic is easy to miss by a few days, and by
          the time you look the shelf it was on has moved up. */}
      <ReleaseShelf title="Previous Releases" date={formatShelfDate(data.previous?.date)}
        state={shelfState(data.previous, data.available, releases.state === "loading")}
        issues={data.previous?.issues} error={data.previous?.error || data.error}
        pulled={pulled} waiting={waiting} onPull={pullIssue} onOpen={(issue) => setDrawer({ kind: "issue", issue })}
        onRetry={loadReleases} />
    </>}
    {drawer?.kind === "issue" ? <DiscoverIssueDrawer issue={drawer.issue}
      state={pullState(drawer.issue, pulled, waiting)} onPull={pullIssue}
      onOpenRun={(item) => setDrawer({ kind: "run", item })} onClose={() => setDrawer(null)} /> : null}
    {drawer?.kind === "arc" ? <StoryArcDrawer arc={drawer.arc} waiting={waiting} onPull={onPullArc}
      onClose={() => setDrawer(null)} /> : null}
    {drawer?.kind === "run" ? <DiscoverRunDrawer item={drawer.item} query={query}
      settled={pulled[runKey(drawer.item)]
        || (runRequested(waiting, drawer.item.provider, drawer.item.providerSeriesId) ? PULL_STATES.requested : undefined)}
      followRequested={waiting.has(requestKey("discover_run", drawer.item))}
      onFollow={followFromDrawer} onUnfollow={unfollowFromDrawer} onPullIssues={pullFromDrawer}
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

// A drawer's colour, from its art (see art-tone.js). The page can only read
// the pixels of an image from this origin, so a remote cover is read through
// the server's small swatch of it. Tones are kept per image for the session.
const ART_TONES = new Map();

function useArtTone(src) {
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
    image.src = local ? src : `/api/v1/art-swatch?src=${encodeURIComponent(src)}`;
    return () => { live = false; };
  }, [src]);
  return tone;
}

// The drawer's class and style for a tone, or nothing for art without one.
function toneProps(tone) {
  return tone
    ? { className: " toned", style: { "--art-tone": tone.tone, "--art-tone-deep": tone.deep } }
    : { className: "", style: undefined };
}

// A drawer's top bar, after Plex's detail page. It stays pinned: at the top
// it is clear, with the close control as a glass circle over the art; once
// the title has scrolled under it, it frosts and takes the title, and its
// controls lose their circles (no glass on glass). While the drawer moves,
// it also tells the header how far it has gone -- the art darkens and drifts
// at a slower pace than the page, and stretches when pulled down at the top.
// A dialog's close control: a glass circle in its corner. A drawer's (`drawer`)
// is a back arrow on a phone, for the reason DrawerTopBar gives.
function DialogCloseButton({ onClose, label, drawer = false }) {
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
const SHEET_QUERY = "(max-width: 640px)";
// A motion token's duration in milliseconds, as the stylesheet has it now
// (reduced motion collapses them all to 1ms).
function motionMs(token) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  const ms = value.endsWith("ms") ? parseFloat(value) : value.endsWith("s") ? parseFloat(value) * 1000 : NaN;
  return Number.isFinite(ms) ? ms : 0;
}

// A sheet leaving slides down and its backdrop fades, however it is closed --
// Done, the backdrop, Escape, the back gesture -- as an iOS sheet does,
// rather than vanishing. `ms` is how long it takes: the token's, or less when
// a flick has already given it speed. Off a phone, or with reduced motion, it
// just closes.
function slideSheetAway(sheet, then, ms = null) {
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
function SheetGrabber({ onClose, detents = false, pullAnywhere = false }) {
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

// On a phone a drawer is a page pushed over the one it came from, so it
// leaves with the back arrow, as an iPhone page does; above 640px it is a
// panel beside the page and closes with the X. `onBack`, when the drawer was
// opened from another (a run from its collection), is where back goes.
function DrawerTopBar({ title, onClose, onBack, closeLabel, stacked = false, children }) {
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
function DiscoverDrawerHero({ art, cover, title, titleId, byline, onClose, closeLabel, children }) {
  return <><DrawerTopBar title={title} onClose={onClose} closeLabel={closeLabel} /><header className="comic-drawer-hero">
    {art ? <>
      <img className="comic-drawer-backdrop" src={art} alt="" aria-hidden="true" key={art} />
      <img className="comic-drawer-backdrop blurred" src={art} alt="" aria-hidden="true" key={`${art}-blurred`} />
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
      </div>
    </div>
  </header></>;
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
  const toned = toneProps(useArtTone(issue.cover));
  const facts = [
    ["Ships", formatLongDate(data.storeDate || issue.storeDate)],
    ["Cover date", formatLongDate(data.coverDate || issue.coverDate)],
    ["Pages", data.pageCount],
    ["Price", data.price ? `$${data.price}` : null],
  ].filter(([, value]) => value);
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer comic-drawer discover-drawer${toned.className} ${closing ? "closing" : ""}`} style={toned.style} ref={dialogRef}
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
// `heading` is null where the text sits under a heading of its own already,
// as the finish drawer's next-issue card does.
function RunSynopsis({ text, source, sourcePrefix = "From", loading = false, heading = "Story" }) {
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
      {source ? <small>{sourcePrefix} {source}</small> : null}
    </footer>
  </section>;
}

// A catalog run, as the comics drawer shows a library one: the art, then --
// on one page, no tabs -- how to take it, stacked. Follow is the comics
// drawer's band (a run that has ended has nothing to follow, so it has none);
// under it one card pulls every released issue (or the whole of an ended
// run), and one opens into the issue list to pick from. The story follows.
// A story arc: its issues in reading order across every series it runs
// through, what the library holds of each, and one button for the rest --
// Pull arc, or for a reader, Request arc.
function StoryArcDrawer({ arc, waiting, onPull, onClose }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  const reader = !isAdmin(useViewer());
  const [detail, setDetail] = useState({ state: "loading", data: null, error: "" });
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(waiting?.has(requestKey("discover_arc", { arcId: arc.providerArcId })) ? "Requested. Waiting for approval." : "");
  async function load() {
    setDetail({ state: "loading", data: null, error: "" });
    try {
      setDetail({ state: "done", data: await apiRequest(`/api/v1/discover/arc?id=${encodeURIComponent(arc.providerArcId)}`), error: "" });
    } catch (error) {
      setDetail({ state: "error", data: null, error: error.message });
    }
  }
  useEffect(() => { load(); }, [arc.providerArcId]);
  const data = detail.data;
  const issues = data?.issues || [];
  const seriesNames = [...new Set((data?.series || []).map((series) => `${series.title}${series.year ? ` (${series.year})` : ""}`))];
  async function pull() {
    if (!data || busy) return;
    setBusy(true);
    const result = await onPull({ arcId: arc.providerArcId, name: data.name || arc.name, cover: data.cover, issueCount: issues.length });
    setBusy(false);
    if (result?.ok) {
      setDone(result.requested ? "Requested. Waiting for approval." : reader ? "On the way." : "On your Pull List.");
      load();
    }
  }
  const verb = reader ? "Request" : "Pull";
  const label = !data ? `${verb} arc` : data.missing === issues.length ? `${verb} all ${issues.length} issues`
    : `${verb} ${data.missing} missing issue${data.missing === 1 ? "" : "s"}`;
  const nothingLeft = Boolean(data) && !data.missing;
  const toned = toneProps(useArtTone(data?.cover));
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer comic-drawer discover-drawer${toned.className} ${closing ? "closing" : ""}`} style={toned.style} ref={dialogRef}
      role="dialog" aria-modal="true" aria-labelledby="story-arc-title" onMouseDown={(event) => event.stopPropagation()}>
      <DiscoverDrawerHero art={data?.cover} titleId="story-arc-title" title={data?.name || arc.name}
        cover={<DiscoverCover src={data?.cover} alt={`${arc.name} cover`} glyph={30} />}
        byline={["Story arc", data ? `${issues.length} issue${issues.length === 1 ? "" : "s"}` : null, seriesNames.slice(0, 2).join(", ")].filter(Boolean).join(" • ")}
        onClose={requestClose} closeLabel="Close story arc">
        {data ? <StatusBadge tone="muted">{data.owned} in library</StatusBadge> : null}
      </DiscoverDrawerHero>
      <div className="comic-drawer-body">
        {detail.state === "error" ? <div className="shelf-message">
          <WarningCircle size={18} />
          <span><strong>This arc&rsquo;s issues could not be listed</strong><small>{detail.error}</small></span>
          <button type="button" onClick={load}>Try again</button>
        </div> : <section className="run-pull-options" aria-label="Pull the arc">
          <div className="run-pull-option">
            <span className="run-pull-option-copy">
              <strong>{nothingLeft ? "Every issue is here or on the way" : label}</strong>
              <small role="status" aria-live="polite">{done || (detail.state === "loading" ? "Getting the arc’s issues…"
                : seriesNames.length > 1 ? `Across ${seriesNames.length} series, in reading order.` : "In reading order.")}</small>
            </span>
            <button type="button" className="pull-button pull-button-md pull-button-idle"
              disabled={!data || nothingLeft || busy || Boolean(done)} onClick={pull} aria-busy={busy || undefined}>
              <span>{busy ? `${reader ? "Requesting" : "Pulling"}…` : verb}</span>
              {busy ? <LoadingIndicator size={16} /> : <PullIcon />}
            </button>
          </div>
        </section>}
        {detail.state === "loading" ? <div role="status" aria-busy="true">
          {[0, 1, 2, 3].map((row) => <div className="discover-issue-row discover-issue-skeleton" aria-hidden="true" key={row}><i /><span className="discover-cover" /><span><i /><i /></span></div>)}
        </div> : <div className="discover-issue-list arc-issue-list">
          {issues.map((issue) => <div className="discover-issue-row" key={issue.providerIssueId}>
            <DiscoverCover src={issue.cover} alt="" glyph={16} />
            <span>
              <strong>{issue.seriesTitle} #{issue.number}</strong>
              <small>{[seriesNames.length > 1 && issue.seriesYear ? `${issue.seriesTitle} (${issue.seriesYear})` : null, formatLongDate(issue.coverDate),
                issue.owned ? "In library" : issue.queued ? (reader ? "On the way" : "On Pull List") : null].filter(Boolean).join(" · ") || " "}</small>
            </span>
          </div>)}
        </div>}
        {data?.description ? <RunSynopsis text={data.description} source="Metron" /> : null}
      </div>
    </aside>
  </div>;
}

function DiscoverRunDrawer({ item, query, settled, followRequested = false, onFollow, onUnfollow, onPullIssues, onClose }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  // A reader asks: Follow becomes "Request follow", Pull becomes "Request".
  const reader = !isAdmin(useViewer());
  const verb = reader ? "Request" : "Pull";
  const [preview, setPreview] = useState({ state: "loading", data: null, error: "" });
  const [choosing, setChoosing] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  // Which action is running, and what the last one said, by action.
  const [busy, setBusy] = useState("");
  const [done, setDone] = useState(settled === PULL_STATES.queued ? { whole: reader ? "Already on the way." : "Already on your Pull List." }
    : followRequested ? { follow: "Follow requested. Waiting for approval." } : {});
  // A follow asked for, as opposed to some of the run's issues.
  const [asked, setAsked] = useState(followRequested);
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
  const modes = runModes(status).map(([id]) => id);
  const canFollow = modes.includes("follow");
  const completed = modes.includes("complete");
  // What the switch last did outranks the preview until the preview catches up.
  const [followed, setFollowed] = useState(null);
  const following = followed ?? Boolean(run?.following);
  const followSummary = runPullSummary("follow", issues, [], { following });
  const wholeMode = completed ? "complete" : "released";
  const wholeSummary = runPullSummary(wholeMode, issues);
  const chooseSummary = runPullSummary("choose", issues, [...selected]);
  function toggle(number) {
    setDone((current) => ({ ...current, choose: "" }));
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(number)) next.delete(number); else next.add(number);
      return next;
    });
  }
  async function commit(action) {
    // Only a run the library holds can be unfollowed, and it is known by its id.
    if (!run || busy || (action === "unfollow" && !run.runId)) return;
    setBusy(action);
    const target = {
      provider: run.provider, providerSeriesId: run.providerSeriesId,
      title: run.title, query: query || run.title,
    };
    const result = action === "follow" ? await onFollow(target)
      : action === "unfollow" ? await onUnfollow({ runId: run.runId, title: run.title })
      : await onPullIssues(action === "choose" ? { ...target, numbers: [...selected] }
        : completed
          ? { ...target, complete: true, numbers: completeRunToPull(issues).map((issue) => issue.number) }
          : { ...target, released: true });
    setBusy("");
    if (result?.ok) {
      if (result.requested) {
        // Asked, not done: nothing is followed or pulled until the admin says so.
        if (action === "follow") setAsked(true);
        setDone((current) => ({ ...current, [action === "follow" ? "follow" : action]:
          action === "follow" ? "Follow requested. Waiting for approval." : "Requested. Waiting for approval." }));
      } else if (action === "follow" || action === "unfollow") {
        setFollowed(action === "follow");
        setDone((current) => ({ ...current, follow: action === "follow" ? "Following this run." : "No longer following this run." }));
      } else {
        setDone((current) => ({ ...current, [action]: reader ? "On the way." : "On your Pull List." }));
      }
      if (action === "choose") setSelected(new Set());
      load();
    }
  }
  const yearLabel = item.yearLabel || run?.year || item.yearBegan;
  const art = run?.cover || item.cover;
  const toned = toneProps(useArtTone(art));
  const waiting = preview.state === "loading" ? "Getting the issue list…" : "";
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer comic-drawer discover-drawer${toned.className} ${closing ? "closing" : ""}`} style={toned.style} ref={dialogRef}
      role="dialog" aria-modal="true" aria-labelledby="discover-run-title"
      onMouseDown={(event) => event.stopPropagation()}>
      <DiscoverDrawerHero art={art} titleId="discover-run-title" title={run?.title || item.title}
        cover={<DiscoverCover src={art} alt={`${item.title} cover`} glyph={30} />}
        byline={[run?.publisher || item.publisher, yearLabel].filter(Boolean).join(" • ")}
        onClose={requestClose} closeLabel="Close run details">
        <RunStatusChip status={status} />
        {(run?.medium || item.medium) === "manga" ? <StatusBadge tone="muted">Manga</StatusBadge> : null}
        {run ? <StatusBadge tone="muted">{issues.length} issue{issues.length === 1 ? "" : "s"}</StatusBadge> : null}
      </DiscoverDrawerHero>
      <div className="comic-drawer-body">
        {canFollow ? <div className="comic-drawer-follow">
          {reader ? <PullButton size="md" readerLabel="Request follow" requestedLabel="Follow requested" onClick={() => commit("follow")}
            state={busy === "follow" ? PULL_STATES.pending : following ? PULL_STATES.queued
              : asked ? PULL_STATES.requested : run ? PULL_STATES.idle : PULL_STATES.pending} />
          : <FollowSwitch following={following} busy={busy === "follow" || busy === "unfollow" || !run}
            label={busy === "follow" ? "Following…" : busy === "unfollow" ? "Stopping…" : following ? "Following Run" : "Follow Run"}
            onChange={(on) => commit(on ? "follow" : "unfollow")} />}
          <small className="comic-drawer-follow-note" role="status" aria-live="polite">{done.follow || waiting || followSummary.detail}</small>
        </div> : null}
        {preview.state === "error" ? <div className="shelf-message">
          <WarningCircle size={18} />
          <span><strong>This run&rsquo;s issues could not be listed</strong><small>{preview.error}</small></span>
          <button type="button" onClick={load}>Try again</button>
        </div> : <section className="run-pull-options" aria-label="Pull issues">
          <div className="run-pull-option">
            <span className="run-pull-option-copy">
              <strong>{(run ? wholeSummary.label : completed ? "Pull complete run" : "Pull all released").replace(/^Pull/, verb)}</strong>
              <small role="status" aria-live="polite">{done.whole || waiting || wholeSummary.detail}</small>
            </span>
            <button type="button" className="pull-button pull-button-md pull-button-idle"
              disabled={!run || wholeSummary.disabled || Boolean(busy)} onClick={() => commit("whole")} aria-busy={busy === "whole" || undefined}>
              <span>{busy === "whole" ? `${verb === "Pull" ? "Pulling" : "Requesting"}…` : verb}</span>
              {busy === "whole" ? <LoadingIndicator size={16} /> : <PullIcon />}
            </button>
          </div>
          <div className={`run-pull-option run-pull-choose${choosing ? " open" : ""}`}>
            <button type="button" className="run-pull-choose-toggle" aria-expanded={choosing} aria-controls="run-issue-picker"
              onClick={() => setChoosing((open) => !open)}>
              <span className="run-pull-option-copy">
                <strong>Choose issues</strong>
                <small>{choosing ? `Tick the issues you want to ${verb.toLowerCase()}.` : "Pick single issues from this run."}</small>
              </span>
              <ChevronDown />
            </button>
            {choosing ? <div className="discover-issue-list" id="run-issue-picker">
              <header>
                <h3>Issues</h3>
                {run ? <span>
                  <button type="button" onClick={() => { setSelected(new Set(releasedToPull(issues).map((issue) => issue.number))); setDone((current) => ({ ...current, choose: "" })); }}>Select all released</button>
                  <button type="button" onClick={() => setSelected(new Set())} disabled={!selected.size}>Clear</button>
                </span> : null}
              </header>
              {preview.state === "loading" ? <div role="status" aria-busy="true">
                <p className="discover-note">Getting the issue list from {PREVIEW_PROVIDER_NAMES[source] || "the catalogs"}&hellip; a long run takes a little while the first time.</p>
                {[0, 1, 2, 3].map((row) => <div className="discover-issue-row discover-issue-skeleton" aria-hidden="true" key={row}>
                  <i /><span className="discover-cover" /><span><i /><i /></span>
                </div>)}
              </div> : issues.map((issue, index) => {
                const available = selectableIssue(issue);
                const note = issue.owned ? "In library" : issue.queued ? (reader ? "On the way" : "On Pull List")
                  : issue.releaseState === "upcoming" ? "Not out yet"
                  : issue.releaseState === "unknown" ? "Release date unknown" : null;
                const date = formatLongDate(issue.publicationDate) || issue.publicationYear;
                return <label className={`discover-issue-row${available ? "" : " unavailable"}`} key={`${issue.number}-${index}`}>
                  <input type="checkbox" checked={selected.has(issue.number)} disabled={!available || Boolean(busy)}
                    onChange={() => toggle(issue.number)} aria-label={`Issue ${issue.number}`} />
                  <DiscoverCover src={issue.cover} alt="" glyph={16} />
                  <span>
                    <strong>{issueLabel(issue.number, run?.medium || item.medium)}{issue.title ? ` · ${issue.title}` : ""}</strong>
                    <small>{[date, note].filter(Boolean).join(" · ") || " "}</small>
                  </span>
                </label>;
              })}
              <footer className="run-pull-choose-actions">
                <small role="status" aria-live="polite">{done.choose || chooseSummary.detail}</small>
                <button type="button" className="pull-button pull-button-md pull-button-idle"
                  disabled={!run || chooseSummary.disabled || Boolean(busy)} onClick={() => commit("choose")} aria-busy={busy === "choose" || undefined}>
                  <span>{busy === "choose" ? `${verb === "Pull" ? "Pulling" : "Requesting"}…` : (chooseSummary.disabled ? "Pull issues" : chooseSummary.label).replace(/^Pull/, verb)}</span>
                  {busy === "choose" ? <LoadingIndicator size={16} /> : <PullIcon />}
                </button>
              </footer>
            </div> : null}
          </div>
        </section>}
        {preview.state === "loading" ? <RunSynopsis loading /> : <RunSynopsis text={run?.synopsis} source={run?.providerName} key={idsKey} />}
        {run?.detailsLimited ? <p className="discover-note">The Grand Comics Database lists this run&rsquo;s issue numbers without titles, dates or covers.</p> : null}
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
  // Adding folders is the admin's; a reader is told what to expect instead.
  if (!isAdmin(useViewer())) {
    return <div className="empty-state"><Books size={35} weight="duotone" /><strong>No comics yet</strong><span>Comics appear here once the library&rsquo;s admin adds them.</span></div>;
  }
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
  return <><PageHeader title="Import library" narrow leading={<button type="button" className="glass-button glass-button--icon" onClick={() => onNavigate("settings", "library")} aria-label="Back to Library folders"><ArrowLeft size={20} /></button>} />
    <ScanProgress scanState={scanState} scanProgress={scanProgress} />
    {catalogPending(catalog, backendStatus) ? <CatalogLoading title="Loading your library folders…" detail="Checking which folders Flipparr already scans." /> : null}
    {roots.length ? <section className="library-sources-panel"><header><div><span className="eyebrow">Library folders</span><h2>Your comic sources</h2><p>Each folder is scanned independently. Removing one from Flipparr never deletes or moves its files.</p></div><div className="library-scan-action"><span>Last library scan</span><strong>{lastScan}</strong><button className={`primary-button ${busy ? "loading" : ""}`} onClick={onScanLibrary} disabled={busy}>{busy ? <LoadingSpinner size={19} /> : <ArrowsClockwise size={19} />}{busy ? "Scanning…" : "Scan all folders"}</button></div></header><div className="library-source-list">{roots.map((root) => <article className="library-source" key={root.id}><span className="library-source-icon"><FolderOpen size={22} weight="duotone" /></span><div className="library-source-copy"><strong>{root.path}</strong><span>{root.recursive ? "Includes subfolders" : "Top-level comics only"} · {rootScanLabel(root.last_scan_at)}</span></div><div className="library-source-actions"><button className="ghost-button" disabled={busy} onClick={() => onStartInventory(root.path, Boolean(root.recursive))}><ArrowsClockwise size={16} /> Scan</button><button className="ghost-button" disabled={busy} onClick={() => { setEditingRoot(editingRoot === root.id ? null : root.id); setRootRecursive(Boolean(root.recursive)); }}><PencilSimple size={16} /> Manage</button></div>{editingRoot === root.id ? <div className="library-source-editor"><label className="check-row"><input type="checkbox" checked={rootRecursive} onChange={(event) => setRootRecursive(event.target.checked)} /><span><strong>Include subfolders</strong><small>Apply this setting on future scans</small></span></label><div><button className="secondary-button" onClick={async () => { await onUpdateRoot(root, rootRecursive); setEditingRoot(null); }}>Save setting</button><button className="danger-button" onClick={() => removeRoot(root)}>Remove from Flipparr</button></div><small>To change the folder path, add the new folder below, then remove this source.</small></div> : null}</article>)}</div></section> : null}
    <section className="focused-panel add-panel"><div className="panel-icon"><FolderOpen size={30} weight="duotone" /></div><h2>Add another library folder</h2><p>We’ll inventory the issues and volumes already in this folder, use covers and metadata from the files, and check for damaged archives. Online details can be refreshed after the library is visible.</p><label className="form-field"><span>Library folder</span><div className="path-input"><input value={path} placeholder="/comics-archive" onChange={(event) => setPath(event.target.value)} /><button type="button" onClick={chooseFolder}>Choose folder</button></div>{pickerState ? <small>{pickerState}</small> : null}</label><label className="check-row"><input type="checkbox" checked={recursive} onChange={(event) => setRecursive(event.target.checked)} /><span><strong>Include subfolders</strong><small>Useful when each series has its own folder</small></span></label><div className="safety-note"><ShieldCheck size={22} weight="fill" /><span><strong>Your files stay untouched</strong><small>No files will be renamed, moved, or modified during this scan.</small></span></div><div className="docker-path-note"><HardDrive size={20} /><span><strong>Using Docker?</strong><small>Mount each NAS share into the Flipparr container first, then enter its container path here. Avoid adding a folder inside an existing source.</small></span></div><div className="panel-actions"><button className={`primary-button ${busy ? "loading" : ""}`} disabled={busy || !path.trim()} onClick={() => onStartInventory(path, recursive)}>{busy ? <LoadingSpinner size={19} /> : <UploadSimple size={19} />} {busy ? "Scanning…" : "Import and scan folder"}</button><button className="ghost-button" onClick={() => onNavigate("settings", "library")}>Cancel</button></div></section></>;
}

// ---- Readers' requests ----------------------------------------------------------

const REQUEST_TONES = { pending: "amber", failed: "red", approved: "violet", available: "green", declined: "muted", cancelled: "muted" };

function requestedOn(iso) {
  const date = iso ? new Date(iso) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
}

// What a request is, from Metron: its age rating, the run's genres and what
// it is about. "Not rated" once looked up and there is none -- for a parent,
// that is worth saying too.
function RequestFacts({ detail, story = false }) {
  const [open, setOpen] = useState(false);
  if (!detail || !("genres" in detail)) return null;
  const genres = detail.genres || [];
  return <>
    <span className="tag-line request-facts">
      <StatusBadge tone={ratingTone(detail.rating)}>{detail.rating ? `Rated ${detail.rating}` : "Not rated"}</StatusBadge>
      {genres.map((genre) => <StatusBadge tone="muted" key={genre}>{genre}</StatusBadge>)}
    </span>
    {detail.rating && detail.ratingFrom ? <small className="request-facts-note">Rating from {detail.ratingFrom.toLowerCase()}</small> : null}
    {story && detail.synopsis ? <p className={`request-story${open ? " open" : ""}`}>{detail.synopsis}</p> : null}
    {story && detail.synopsis?.length > 180 ? <button type="button" className="request-story-more" onClick={() => setOpen((value) => !value)}>{open ? "Less" : "More"}</button> : null}
  </>;
}

// One request: what was asked for, by whom, where it stands, and -- for the
// admin while it waits, or its reader while it is pending -- what to do.
function MemberRequestCard({ request, admin, onDecide, aimed = false }) {
  const [busy, setBusy] = useState("");
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const waiting = request.status === "pending" || request.status === "failed";
  const labels = admin ? ADMIN_STATE_LABELS : REQUEST_STATE_LABELS;
  async function decide(action, body) {
    setBusy(action);
    setError("");
    const failure = await onDecide(request, action, body);
    if (failure) setError(failure);
    setBusy("");
    if (!failure) setDeclining(false);
  }
  const byline = [request.detail?.publisher, request.detail?.year].filter(Boolean).join(" · ");
  return <article className={`request-card member-request-card${aimed ? " request-aimed" : ""}`} data-member-request={request.id}>
    <div className="member-request">
      <span className="request-cover">{request.detail?.cover
        ? <img src={request.detail.cover} alt="" loading="lazy" />
        : <span className="cover-placeholder" aria-hidden="true"><BookOpen size={24} weight="duotone" /></span>}</span>
      <span className="request-identity">
        <strong>{request.title}</strong>
        <small>{[requestScope(request), byline].filter(Boolean).join(" · ")}</small>
        {admin ? <span className="member-request-who"><ProfileAvatar profile={request.requestedBy} size="sm" />
          <small>{request.requestedBy?.name} · {requestedOn(request.createdAt)}</small></span>
          : <small>Asked {requestedOn(request.createdAt)}</small>}
        <span className="tag-line"><StatusBadge tone={REQUEST_TONES[request.state] || "muted"}>{labels[request.state] || request.state}</StatusBadge></span>
        {admin ? <RequestFacts detail={request.detail} /> : null}
        {request.state === "declined" && request.declineReason ? <small className="member-request-reason">&ldquo;{request.declineReason}&rdquo;</small> : null}
        {admin && request.status === "failed" && request.failure ? <small className="member-request-reason">{request.failure}</small> : null}
      </span>
    </div>
    {admin && waiting ? <div className="member-request-actions">
      {declining ? <form className="member-request-decline" onSubmit={(event) => { event.preventDefault(); decide("decline", { reason }); }}>
        <label className="form-field"><span>Reason</span>
          <input value={reason} maxLength={300} autoFocus onChange={(event) => setReason(event.target.value)} placeholder="Optional, shown to them" />
        </label>
        <div className="settings-card-actions">
          <button type="button" className="secondary-button" onClick={() => setDeclining(false)} disabled={Boolean(busy)}>Back</button>
          <button className="danger-button" disabled={Boolean(busy)} aria-busy={busy === "decline"}>{busy === "decline" ? <LoadingSpinner size={16} /> : null} Decline</button>
        </div>
      </form> : <div className="settings-card-actions">
        <button type="button" className="secondary-button" onClick={() => setDeclining(true)} disabled={Boolean(busy)}>Decline</button>
        <button type="button" className="primary-button" onClick={() => decide("approve")} disabled={Boolean(busy)} aria-busy={busy === "approve"}>
          {busy === "approve" ? <LoadingSpinner size={16} /> : <Check size={16} weight="bold" />} {request.status === "failed" ? "Try again" : "Approve"}
        </button>
      </div>}
    </div> : null}
    {!admin && request.status === "pending" ? <div className="member-request-actions"><div className="settings-card-actions">
      <button type="button" className="secondary-button" onClick={() => decide("cancel")} disabled={Boolean(busy)} aria-busy={busy === "cancel"}>
        {busy === "cancel" ? <LoadingSpinner size={16} /> : null} Cancel request
      </button>
    </div></div> : null}
    {error ? <p className="workbench-error member-request-error" role="alert">{error}</p> : null}
  </article>;
}

// One waiting request as a card to swipe: right approves, left declines, the
// buttons under it do the same. The card follows the finger; let go short of
// a decision and it springs back. A vertical drag is left to the page's scroll.
function SwipeCard({ request, onSwipe, peek = false }) {
  const cardRef = useRef(null);
  const drag = useRef(null);
  const [dx, setDx] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [leaving, setLeaving] = useState("");
  const still = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  function fly(action) {
    if (leaving) return;
    if (still) { onSwipe(request, action); return; }
    setLeaving(action);
    window.setTimeout(() => onSwipe(request, action), 220);
  }
  function down(event) {
    if (leaving || (event.pointerType === "mouse" && event.button !== 0) || event.target.closest("button, a, input")) return;
    drag.current = { x: event.clientX, y: event.clientY, id: event.pointerId, horizontal: null, at: performance.now() };
  }
  function move(event) {
    const current = drag.current;
    if (!current || current.id !== event.pointerId) return;
    const mx = event.clientX - current.x;
    const my = event.clientY - current.y;
    if (current.horizontal === null) {
      if (Math.abs(mx) < 8 && Math.abs(my) < 8) return;
      current.horizontal = Math.abs(mx) > Math.abs(my);
      if (!current.horizontal) { drag.current = null; return; }
      cardRef.current?.setPointerCapture?.(event.pointerId);
      setDragging(true);
    }
    setDx(mx);
  }
  function up(event) {
    const current = drag.current;
    drag.current = null;
    setDragging(false);
    if (!current?.horizontal) return;
    const moved = event.clientX - current.x;
    const action = swipeDecision(moved, cardRef.current?.offsetWidth || 320, performance.now() - current.at);
    if (action) fly(action); else setDx(0);
  }
  const offset = leaving ? (leaving === "approve" ? "130%" : "-130%") : `${dx}px`;
  const tilt = leaving ? (leaving === "approve" ? 12 : -12) : dx / 24;
  const lean = Math.max(-1, Math.min(1, dx / 120));
  const byline = [request.detail?.publisher, request.detail?.year].filter(Boolean).join(" · ");
  return <div className="request-swipe">
    <div className="request-swipe-pile">
    {peek ? <div className="request-card request-swipe-peek" aria-hidden="true" /> : null}
    <article ref={cardRef} className={`request-card request-swipe-card${dragging ? " dragging" : ""}${leaving ? " leaving" : ""}`}
      style={{ transform: `translateX(${offset}) rotate(${tilt}deg)` }} tabIndex={0}
      aria-label={`${request.title}, asked for by ${request.requestedBy?.name}. Swipe right to approve, left to decline.`}
      onKeyDown={(event) => { if (event.key === "ArrowRight") fly("approve"); if (event.key === "ArrowLeft") fly("decline"); }}
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
      <span className="request-swipe-stamp approve" style={{ opacity: Math.max(0, lean) }} aria-hidden="true">Approve</span>
      <span className="request-swipe-stamp decline" style={{ opacity: Math.max(0, -lean) }} aria-hidden="true">Decline</span>
      <div className="request-swipe-head">
        <span className="request-cover request-swipe-cover">{request.detail?.cover
          ? <img src={request.detail.cover} alt="" draggable="false" />
          : <span className="cover-placeholder" aria-hidden="true"><BookOpen size={28} weight="duotone" /></span>}</span>
        <span className="request-identity">
          <strong>{request.title}</strong>
          <small>{[requestScope(request), byline].filter(Boolean).join(" · ")}</small>
          <span className="member-request-who"><ProfileAvatar profile={request.requestedBy} size="sm" />
            <small>{request.requestedBy?.name} · {requestedOn(request.createdAt)}</small></span>
          {request.status === "failed" ? <small className="member-request-reason">Couldn&rsquo;t be done: {request.failure}</small> : null}
        </span>
      </div>
      <RequestFacts detail={request.detail} story />
    </article>
    </div>
    <div className="request-swipe-actions">
      <button type="button" className="secondary-button" onClick={() => fly("decline")} disabled={Boolean(leaving)}><X size={16} weight="bold" /> Decline</button>
      <button type="button" className="primary-button" onClick={() => fly("approve")} disabled={Boolean(leaving)}>
        <Check size={16} weight="bold" /> {request.status === "failed" ? "Try again" : "Approve"}</button>
    </div>
  </div>;
}

// Whether the device points precisely and hovers -- a mouse or trackpad. It
// decides the request queue's shape by how it is used, not by how wide the
// screen is: an iPad without a trackpad still swipes.
const FINE_POINTER = "(hover: hover) and (pointer: fine)";

function useFinePointer() {
  const [fine, setFine] = useState(() => typeof window !== "undefined" && Boolean(window.matchMedia?.(FINE_POINTER).matches));
  useEffect(() => {
    const query = window.matchMedia?.(FINE_POINTER);
    if (!query) return undefined;
    const change = () => setFine(query.matches);
    query.addEventListener?.("change", change);
    return () => query.removeEventListener?.("change", change);
  }, []);
  return fine;
}

// One waiting request as a row, for a mouse and keyboard: everything about it
// on show, Decline and Approve at its end -- Seerr's requests list.
function QueueRow({ request, onDecide }) {
  const byline = [request.detail?.publisher, request.detail?.year].filter(Boolean).join(" · ");
  return <article className="request-card request-queue-row" tabIndex={0} data-queue-request={request.id}
    aria-label={`${request.title}, asked for by ${request.requestedBy?.name}`}>
    <span className="request-cover request-swipe-cover">{request.detail?.cover
      ? <img src={request.detail.cover} alt="" />
      : <span className="cover-placeholder" aria-hidden="true"><BookOpen size={28} weight="duotone" /></span>}</span>
    <span className="request-identity">
      <strong>{request.title}</strong>
      <small>{[requestScope(request), byline].filter(Boolean).join(" · ")}</small>
      <span className="member-request-who"><ProfileAvatar profile={request.requestedBy} size="sm" />
        <small>{request.requestedBy?.name} · {requestedOn(request.createdAt)}</small></span>
      {request.status === "failed" ? <small className="member-request-reason">Couldn&rsquo;t be done: {request.failure}</small> : null}
      <RequestFacts detail={request.detail} story />
    </span>
    <span className="request-queue-actions">
      <button type="button" className="secondary-button" onClick={() => onDecide(request, "decline")} title="Decline (D)"><X size={16} weight="bold" /> Decline</button>
      <button type="button" className="primary-button" onClick={() => onDecide(request, "approve")} title="Approve (A)">
        <Check size={16} weight="bold" /> {request.status === "failed" ? "Try again" : "Approve"}</button>
    </span>
  </article>;
}

// The admin's waiting requests as a deck: one card at a time, the next behind
// it. A decision is held for a few seconds with Undo before it is sent --
// approving starts downloads, and a swipe is easy to make by accident. Leaving
// the page sends it; a request never sent simply stays waiting.
function RequestDeck({ requests, onDecide }) {
  const fine = useFinePointer();
  const listRef = useRef(null);
  const focusNext = useRef(null);
  const [held, setHeld] = useState(null);
  const [gone, setGone] = useState(() => new Set());
  const [error, setError] = useState("");
  const heldRef = useRef(null);
  const timer = useRef(null);
  const hide = (id, hidden) => setGone((current) => {
    const next = new Set(current);
    if (hidden) next.add(id); else next.delete(id);
    return next;
  });
  async function send(entry) {
    window.clearTimeout(timer.current);
    if (heldRef.current === entry) { heldRef.current = null; setHeld(null); }
    const failure = await onDecide(entry.request, entry.action, entry.action === "decline" ? { reason: entry.reason || "" } : undefined);
    if (failure) setError(failure);
    // Sent, and the queue reloaded: a decided request has left it; one whose
    // approval failed is back, offering Try again.
    hide(entry.request.id, false);
  }
  function decide(request, action) {
    // Focus moves on to the next request once the list has re-rendered
    // without this one, so a keyboard can keep going.
    const rows = [...(listRef.current?.querySelectorAll("[data-queue-request]") || [])];
    const at = rows.findIndex((row) => row.dataset.queueRequest === String(request.id));
    focusNext.current = (rows[at + 1] || rows[at - 1])?.dataset.queueRequest || null;
    if (heldRef.current) send(heldRef.current);
    const entry = { request, action, reason: "" };
    heldRef.current = entry;
    setHeld(entry);
    setError("");
    hide(request.id, true);
    timer.current = window.setTimeout(() => send(entry), UNDO_MS);
  }
  function undo() {
    const entry = heldRef.current;
    if (!entry) return;
    window.clearTimeout(timer.current);
    heldRef.current = null;
    setHeld(null);
    hide(entry.request.id, false);
  }
  function askReason() {
    window.clearTimeout(timer.current);
    setHeld({ ...heldRef.current, reasoning: true });
  }
  useEffect(() => {
    if (!focusNext.current) return;
    listRef.current?.querySelector(`[data-queue-request="${focusNext.current}"]`)?.focus();
    focusNext.current = null;
  }, [gone]);
  // While a decision waits, Z takes it back from anywhere on the page -- the
  // row that had focus is gone.
  useEffect(() => {
    if (!held || held.reasoning) return undefined;
    function onKey(event) {
      if (event.key.toLowerCase() !== "z" || event.metaKey || event.ctrlKey || event.altKey || event.target.closest?.("input, textarea")) return;
      event.preventDefault();
      undo();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [held]);
  // Leaving the page sends what was decided rather than dropping it.
  useEffect(() => () => {
    window.clearTimeout(timer.current);
    if (heldRef.current) onDecide(heldRef.current.request, heldRef.current.action,
      heldRef.current.action === "decline" ? { reason: heldRef.current.reason || "" } : undefined);
  }, []);
  const deck = requests.filter((request) => !gone.has(request.id));
  const [top, next] = deck;
  // A mouse and keyboard: move with the arrows or J/K, decide with A or D,
  // take it back with Z -- a moderation queue's keys, Gmail's Undo.
  function onKeys(event) {
    if (event.metaKey || event.ctrlKey || event.altKey || event.target.closest("input, textarea")) return;
    const key = event.key.toLowerCase();
    if (key === "z") return;
    const row = event.target.closest?.("[data-queue-request]");
    const rows = [...(listRef.current?.querySelectorAll("[data-queue-request]") || [])];
    if (["arrowdown", "j", "arrowup", "k"].includes(key)) {
      event.preventDefault();
      const at = rows.indexOf(row);
      const step = key === "arrowdown" || key === "j" ? 1 : -1;
      rows[Math.max(0, Math.min(rows.length - 1, at + step))]?.focus();
      return;
    }
    const request = row && deck.find((item) => String(item.id) === row.dataset.queueRequest);
    if (!request) return;
    if (key === "a") { event.preventDefault(); decide(request, "approve"); }
    if (key === "d") { event.preventDefault(); decide(request, "decline"); }
  }
  return <section className="request-deck" aria-label="Requests waiting">
    {held ? <div className="request-undo" role="status">
      {held.reasoning ? <form className="request-undo-reason" onSubmit={(event) => {
        event.preventDefault();
        const reason = new FormData(event.currentTarget).get("reason");
        send({ ...heldRef.current, reason: String(reason || "") });
      }}>
        <input name="reason" autoFocus maxLength={300} placeholder="Why not? Shown to them" aria-label="Reason" />
        <button className="primary-button">Decline</button>
      </form> : <>
        <span>{held.action === "approve" ? "Approved" : "Declined"} <strong>{held.request.title}</strong></span>
        {held.action === "decline" ? <button type="button" onClick={askReason}>Add a reason</button> : null}
        <button type="button" onClick={undo}>Undo</button>
      </>}
    </div> : null}
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
    {top && fine ? <>
      <p className="request-deck-count">{deck.length === 1 ? "1 request waiting" : `${deck.length} requests waiting`}
        <span className="request-keys" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd> move · <kbd>A</kbd> approve · <kbd>D</kbd> decline · <kbd>Z</kbd> undo</span></p>
      <div className="request-queue" ref={listRef} onKeyDown={onKeys}>
        {deck.map((request) => <QueueRow request={request} onDecide={decide} key={request.id} />)}
      </div>
    </> : top ? <>
      <p className="request-deck-count">{deck.length === 1 ? "1 request waiting" : `${deck.length} requests waiting`}</p>
      <SwipeCard request={top} onSwipe={decide} peek={Boolean(next)} key={top.id} />
    </> : held ? null : <div className="empty-state request-empty"><CheckCircle size={34} weight="duotone" /><strong>All caught up</strong><span>Nothing is waiting for you.</span></div>}
  </section>;
}

// The admin's queue, as the Pull List's first tab: what waits, then what was decided.
function MemberRequestList({ requests, admin, onDecide, focus }) {
  // The admin answers the oldest first; a reader looks for what they just asked.
  const queue = adminQueue(requests);
  const waiting = admin ? queue.waiting : [...queue.waiting].reverse();
  // A request its reader took back is theirs; the admin's history leaves it out.
  const decided = admin ? queue.decided.filter((request) => request.status !== "cancelled") : queue.decided;
  const aimed = focus?.requestId;
  if (!requests.length) {
    return <div className="empty-state request-empty"><CheckCircle size={34} weight="duotone" />
      <strong>{admin ? "No requests" : "Nothing requested yet"}</strong>
      <span>{admin ? "When a reader asks for a run or an issue, it waits here for you."
        : "Find a run or an issue in Discover and tap Request. The admin says yes or no."}</span></div>;
  }
  return <>
    {admin ? <RequestDeck requests={waiting} onDecide={onDecide} />
      : waiting.length ? <section className="request-list">{waiting.map((request) => <MemberRequestCard request={request} admin={admin}
      onDecide={onDecide} aimed={String(aimed) === String(request.id)} key={request.id} />)}</section> : null}
    {decided.length ? <section className="request-list member-request-history" aria-label="Decided">
      {admin || waiting.length ? <h2 className="member-request-heading">Earlier</h2> : null}
      {decided.map((request) => <MemberRequestCard request={request} admin={admin} onDecide={onDecide}
        aimed={String(aimed) === String(request.id)} key={request.id} />)}
    </section> : null}
  </>;
}

// A reader's page: what they asked for and what became of it.
function MyRequestsView({ catalog, backendStatus, focus, onDecide }) {
  const loading = catalogPending(catalog, backendStatus);
  return <><PageHeader title="Requests" />
    {loading ? <CatalogLoading title="Loading your requests…" detail="" />
      : <MemberRequestList requests={catalog?.memberRequests || []} admin={false} onDecide={onDecide} focus={focus} />}
  </>;
}

function RequestsView({ catalog, backendStatus, focus, onCancelReplacement, onDeletePull, onRefresh, onDecide }) {
  const [releaseJob, setReleaseJob] = useState(null);
  // Readers' requests come first while any wait: they are the admin's to answer.
  const memberRequests = catalog?.memberRequests || [];
  const pendingAsks = memberRequests.filter((request) => request.status === "pending" || request.status === "failed").length;
  const [tab, setTabState] = useState(() => (pendingAsks ? "asks" : "wanted"));
  // The catalog often arrives after the page: open on Requests then, unless
  // a tab has already been chosen.
  const tabChosen = useRef(false);
  const setTab = (next) => { tabChosen.current = true; setTabState(next); };
  useEffect(() => {
    if (pendingAsks && !tabChosen.current) setTabState("asks");
  }, [pendingAsks]);
  const [searchingMissing, setSearchingMissing] = useState(false);
  const [searchMissingMessage, setSearchMissingMessage] = useState("");
  // The Wanted list's own action, the way Radarr and Sonarr put one there:
  // everything still missing, searched on demand. It covers issues nothing
  // good enough was found for last time, and requests made before Flipparr
  // searched on its own.
  //
  // It runs on the tap, without a confirmation (2026-09-19, at the user's
  // request): the line it sits on says how many issues it is for, downloading
  // them is what a wanted list is for, and nothing here is destructive -- a
  // download that was not wanted is removed from this same page.
  async function searchMissing() {
    setSearchingMissing(true);
    setSearchMissingMessage("");
    try {
      const result = await apiRequest("/api/v1/requests/search-missing", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmed: true }),
      });
      setSearchMissingMessage(result.detail || "");
      await onRefresh?.();
    } catch (error) {
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
      } catch { /* a download client that cannot answer shows no bar */ }
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
    if (focus.tab) { setTab(focus.tab); return undefined; }
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
      const toggle = row.querySelector(".request-row");
      if (toggle?.getAttribute("aria-expanded") === "false") toggle.click();
      row.classList.add("request-aimed");
      setTimeout(() => row.classList.remove("request-aimed"), 2200);
    }, 60);
    return () => clearTimeout(timer);
  }, [focus]);
  const entries = tab === "asks" ? [] : buckets[tab] ?? [];
  const tabCopy = PULL_LIST_COPY[tab] ?? PULL_LIST_COPY.wanted;
  const loading = catalogPending(catalog, backendStatus);
  const tabs = [
    ...(memberRequests.length ? [{ id: "asks", label: "Requests", count: loading ? null : <b>{pendingAsks}</b> }] : []),
    ...PULL_LIST_TABS.filter(({ id }) => id !== "failed" || tabCount(buckets.failed)).map(({ id, label }) => ({ id, label, className: id === "failed" ? "request-tab-failed" : "", count: loading ? null : <b>{tabCount(buckets[id])}</b> })),
  ];
  // Searching for everything missing is Wanted's own action, so it sits at
  // the head of that list, beside how much there is to find, rather than in
  // the page header.
  const missingIssues = buckets.wanted.reduce((sum, { kind, request }) => sum + (kind === "replacement"
    ? (request.jobs || []).filter((job) => !["fulfilled", "cancelled"].includes(job.status)).length
    : request.wantedIssueCount || 0), 0);
  // The count and what to do about it, as one line: "3 missing issues | Find".
  const wantedBar = tab === "wanted" && !loading && entries.length ? <div className="request-list-bar">
    <span><strong>{missingIssues}</strong> missing issue{missingIssues === 1 ? "" : "s"}</span>
    <i className="request-list-bar-rule" aria-hidden="true" />
    <button type="button" className="search-missing-button" onClick={() => searchMissing()} disabled={searchingMissing} aria-busy={searchingMissing}
      aria-label={`Find the ${missingIssues} missing issue${missingIssues === 1 ? "" : "s"}`}>
      {/* No magnifier: that icon means typing a search, and this is not one. */}
      {searchingMissing ? <><LoadingSpinner size={14} /> Finding…</> : "Find"}
    </button>
  </div> : null;
  return <><PageHeader title="Pull List" tools={<SegmentedTabs label="Pull List" value={tab} onChange={setTab} items={tabs} />} />{wantedBar}{tab === "wanted" && searchMissingMessage ? <p className="request-search-result" role="status">{searchMissingMessage}</p> : null}{tab === "asks" && !loading ? <MemberRequestList requests={memberRequests} admin onDecide={onDecide} focus={focus} /> : <section className="request-list">{loading ? <CatalogLoading title="Loading your pull list…" detail="Bringing in followed runs, wanted issues, and downloads." /> : entries.length ? entries.map(({ kind, request }) => kind === "replacement"
    ? <ReplacementRequestRow request={request} progress={progress} openByDefault={tab === "failed" || tab === "downloading"} onCancel={onCancelReplacement} onFindRelease={setReleaseJob} onRefresh={onRefresh} key={`replacement-${request.id}`} />
    : <RequestRow request={request} tab={tab} progress={progress} openByDefault={tab === "failed" || tab === "downloading"} onFindRelease={setReleaseJob} onRefresh={onRefresh} onDelete={onDeletePull} key={`series-${request.id}`} />) : <div className="empty-state request-empty"><CheckCircle size={34} weight="duotone" /><strong>{tabCopy.emptyTitle}</strong><span>{tabCopy.emptyDetail}</span></div>}</section>}{releaseJob ? <ReleaseSearchModal job={releaseJob} onClose={() => setReleaseJob(null)} onGrabbed={async () => { await onRefresh?.(); setReleaseJob(null); }} /> : null}</>;
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
  // Stopping is the answer to a download that is not going anywhere: the
  // issue goes back to wanting a release and the next pass reaches for another.
  async function stopJob(job) {
    setRetryingJobId(job.id); setRetryError(null);
    try {
      await apiRequest(`/api/v1/acquisition-jobs/${job.id}/stop`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await onRefresh?.();
    } catch (error) {
      setRetryError({ jobId: job.id, message: error.message || "This download could not be stopped" });
    } finally { setRetryingJobId(null); }
  }
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
  return <article data-request={`replacement-${request.id}`} className={`request-card replacement-request-card ${expanded ? "expanded" : ""}`}><button type="button" className="request-row" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}><span className="request-cover"><SeriesCover series={display} decorative /></span><span className="request-identity"><strong>{title}</strong><small>{scope}</small><small>{request.targetTitle !== title ? `${request.targetTitle} · ` : ""}{request.filename} · Added {request.requestedDate} {request.requestedTime}</small><span className="tag-line"><StatusBadge tone={tone}>{statusLabel}</StatusBadge></span></span><CaretDown className="request-caret" size={20} aria-hidden="true" /></button>{expanded ? <div className="request-job-panel"><header><div><strong>{request.status === "fulfilled" ? "Replacement complete" : "Comics needed for this replacement"}</strong><span>{request.status === "fulfilled" ? "The verified replacement is active and the original is held in recoverable quarantine." : "Flipparr searches and grabs the best match for each issue. The original stays active until every replacement passes validation."}</span></div>{!["fulfilled", "cancelled"].includes(request.status) ? <button className="ghost-button" onClick={() => onCancel(request)}>Cancel request</button> : null}</header>{jobs.length ? <div className="request-jobs">{jobs.map((job) => { const displayStatus = job.downloadStatus || job.status; const imported = job.downloadStatus === "imported"; const failedJob = job.status === "failed" || job.downloadStatus === "failed"; const canSearch = !job.downloadStatus && !["grabbed", "fulfilled", "cancelled"].includes(job.status); const retryMessage = retryError?.jobId === job.id ? retryError.message : null; const failure = failedJob ? acquisitionFailureDetails(job) : null; const detail = retryMessage || (!failedJob ? job.downloadTitle : null); return <div className="request-job" key={job.id}><b>{issueLabel(job.issueNumber, request.medium)}</b><div><strong>{job.issueTitle || `Issue ${job.issueNumber}`}</strong>{imported ? null : <span>{job.reason}</span>}{failure ? <div className="job-failure-copy"><strong>{failure.label}</strong><small>{failure.message}</small>{failure.technical ? <details><summary>Technical details</summary><code>{failure.technical}</code></details> : null}</div> : detail ? <span className={retryMessage ? "job-error" : ""}>{detail}</span> : null}<JobProgress entry={progress[String(job.id)]} /></div><span className="request-job-actions"><span className={`job-state ${displayStatus}`}>{DOWNLOAD_STATUS_LABELS[job.downloadStatus] || JOB_STATUS_LABELS[job.status] || displayStatus}</span>{failedJob ? <><button type="button" disabled={retryingJobId === job.id} onClick={() => retryJob(job)}>{retryingJobId === job.id ? <LoadingSpinner size={14} /> : <ArrowsClockwise size={14} />} {job.downloadFailureStage === "import" ? "Retry import" : "Try next release"}</button><button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button></> : !imported && canSearch ? <button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button> : null}{!imported && !failedJob && job.status === "grabbed" ? <button type="button" disabled={retryingJobId === job.id} onClick={() => stopJob(job)}>{retryingJobId === job.id ? <LoadingSpinner size={14} /> : <X size={14} />} Stop download</button> : null}</span></div>; })}</div> : <div className="request-job-empty"><WarningCircle size={20} /><div><strong>No safe issue targets are available</strong><span>Confirm the comic’s issue contents before replacing it.</span></div></div>}</div> : <footer className="replacement-safety-note"><ShieldCheck size={16} weight="fill" /> The current comic stays in your library until all mapped replacements are downloaded and verified.</footer>}</article>;
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
        // A header holds only ASCII; a comic's name often has an en dash or a
        // curly apostrophe, and fetch refuses the header outright otherwise.
        headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) },
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
  // Stopping is the answer to a download that is not going anywhere: the
  // issue goes back to wanting a release and the next pass reaches for another.
  async function stopJob(job) {
    setRetryingJobId(job.id); setRetryError(null);
    try {
      await apiRequest(`/api/v1/acquisition-jobs/${job.id}/stop`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await onRefresh?.();
    } catch (error) {
      setRetryError({ jobId: job.id, message: error.message || "This download could not be stopped" });
    } finally { setRetryingJobId(null); }
  }
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
          <span className="request-job-actions"><span className={`job-state ${displayStatus}`}>{DOWNLOAD_STATUS_LABELS[job.downloadStatus] || JOB_STATUS_LABELS[job.status] || displayStatus}</span>{failedJob ? <><button type="button" disabled={retryingJobId === job.id} onClick={() => retryJob(job)}>{retryingJobId === job.id ? <LoadingSpinner size={14} /> : <ArrowsClockwise size={14} />} {retryingJobId === job.id ? "Retrying…" : retryLabel}</button><button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button></> : !imported && canSearch ? <button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button> : null}{!imported && !failedJob && job.status === "grabbed" ? <button type="button" disabled={retryingJobId === job.id} onClick={() => stopJob(job)}>{retryingJobId === job.id ? <LoadingSpinner size={14} /> : <X size={14} />} Stop download</button> : null}{!imported ? <label className={`job-upload ${uploadingJobId === job.id ? "busy" : ""}`}><input type="file" accept=".cbz,.cbr,.cbt,.cb7,.pdf,.epub" disabled={uploadingJobId === job.id} onChange={(event) => { const [file] = event.target.files || []; event.target.value = ""; uploadForJob(job, file); }} />{uploadingJobId === job.id ? <LoadingSpinner size={14} /> : <UploadSimple size={14} />} {uploadingJobId === job.id ? "Adding…" : "Upload a file"}</label> : null}{perIssueDelete && canDeleteJob(job) ? deleteButton({ id: job.issueId, number: job.issueNumber }) : null}</span>
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
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal release-search-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="release-search-title" onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close release search" /><span className="eyebrow">Find one missing issue</span><h2 id="release-search-title">{job.seriesTitle} #{job.issueNumber}</h2><p className="workbench-intro">Compare the results below. Nothing is downloaded until you choose a release.</p><form className="release-query" onSubmit={(event) => { event.preventDefault(); search(query); }}><MagnifyingGlass size={16} /><input value={query} onChange={(event) => { setQuery(event.target.value); setEdited(true); }} aria-label="Search terms" placeholder="Series and issue to search for…" disabled={loading} /><button type="submit" className="secondary-button" disabled={loading || query.trim().length < 2}>{loading ? <LoadingSpinner size={16} /> : null} Search</button></form>{edited ? null : <p className="release-query-note">Flipparr widens this automatically when a narrower wording finds nothing. Edit it to search for something else.</p>}{loading ? <div className="release-loading"><LoadingSpinner size={24} /><div><strong>Searching your indexers…</strong><span>This can take a few seconds.</span></div></div> : null}{error ? <div className="release-error"><WarningCircle size={19} weight="fill" /><span><strong>Release search needs attention</strong>{error}</span><button type="button" onClick={() => search(query)}>Try again</button></div> : null}{!loading && !error && !candidates.length ? <div className="release-empty"><MagnifyingGlass size={28} /><strong>{summary.headline}</strong><span>{summary.detail}</span>{summary.setAside.length ? <ul className="release-set-aside">{summary.setAside.map((item) => <li key={item.title}><b title={item.title}>{item.title}</b><small>{item.reason}{item.copies > 1 ? ` · listed by ${item.copies} indexers` : ""}</small></li>)}</ul> : null}<button type="button" className="secondary-button" onClick={() => search(query)}>Search again</button></div> : null}{candidates.length ? <div className="release-candidates"><header><div><strong>{candidates.length} candidate{candidates.length === 1 ? "" : "s"}</strong><span>Best matches appear first. Confirm the title, issue, language, and format.</span></div></header>{candidates.map((candidate) => { const isGrabbing = grabbingId === candidate.id; return <article className="release-candidate" key={candidate.id}><div className="release-candidate-main"><StatusBadge tone={candidate.matchScore >= 85 ? "green" : "amber"}>{candidate.matchStrength}</StatusBadge><h3>{candidate.title}</h3><p>{candidate.indexer} · {candidate.protocol} · {formatReleaseSize(candidate.sizeBytes)}{candidate.publishDate ? ` · ${new Date(candidate.publishDate).toLocaleDateString()}` : ""}</p>{candidate.formatTags?.length ? <div className="release-tags">{candidate.formatTags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}</div><div className="release-match"><strong>{candidate.matchScore}</strong><span>match score</span></div><ul>{candidate.matchReasons.map((reason) => <li key={reason}><CheckCircle size={14} weight="fill" />{reason}</li>)}{candidate.grabbable === false && candidate.grabHint ? <li className="release-candidate-hint"><WarningCircle size={14} />{candidate.grabHint}</li> : null}</ul><button type="button" className={`primary-button ${isGrabbing ? "loading" : ""}`} aria-busy={isGrabbing} disabled={Boolean(grabbingId) || candidate.grabbable === false} title={candidate.grabbable === false ? candidate.grabHint : undefined} onClick={() => grab(candidate)}>{isGrabbing ? <LoadingSpinner size={17} /> : <CloudArrowDown size={17} />}{isGrabbing ? "Sending…" : candidate.grabbable === false ? "Not fetchable yet" : "Send to SABnzbd"}</button></article>; })}</div> : null}<footer className="release-modal-footer"><ShieldCheck size={17} weight="fill" /> Prowlarr download links stay on the server and are never exposed in this page.</footer></section></div>;
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
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal replacement-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="replacement-title" onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close replacement request" /><span className="eyebrow">Fix comic run</span><h2 id="replacement-title">{file.file || file.filename}</h2><p className="workbench-intro">Add this comic’s mapped issues to the wanted list. Flipparr will keep the current file active until every replacement passes validation.</p><form onSubmit={submit}><label className="form-field"><span>Replacement format</span><GlassSelect label="Replacement format" value={acquisitionPreference} onChange={setAcquisitionPreference} className="glass-select--fill" options={[{ value: "issues", label: "Mapped single issues" }]} /><small>Whole-volume acquisition will be added after volume release matching is reliable.</small></label><label className="form-field"><span>Why replace it?</span><GlassSelect label="Why replace it?" value={reason} onChange={setReason} className="glass-select--fill" options={[{ value: "corrupt", label: "Corrupt or unreadable file" }, { value: "no_pages", label: "No readable comic pages" }, { value: "empty", label: "Empty archive" }, { value: "wrong_language", label: "Wrong language" }, { value: "wrong_release", label: "Wrong edition or release" }, { value: "poor_quality", label: "Poor scan or image quality" }]} /></label>{reason === "wrong_language" ? <label className="form-field"><span>Language wanted</span><input value={language} onChange={(event) => setLanguage(event.target.value)} placeholder="English" required /></label> : null}<div className="replacement-summary"><ShieldCheck size={19} weight="fill" /><span><strong>Recoverable replacement</strong><small>The original is moved to hidden quarantine only after all mapped issues pass identity, archive, copy, and hash checks.</small></span></div>{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="metadata-edit-actions"><button type="button" className="ghost-button" onClick={onClose}>Cancel</button><button type="submit" className="primary-button" aria-busy={busy} disabled={busy || (reason === "wrong_language" && !language.trim())}>{busy ? <LoadingSpinner size={18} /> : <CloudArrowDown size={18} />} {busy ? "Adding…" : "Add to wanted"}</button></div></form></section></div>;
}

function MetadataComparison({ comparison }) {
  return <div className="comparison"><div className="comparison-head"><span>Field</span><span>{comparison.catalogLabel}</span><span>{comparison.fileLabel}</span></div>{comparison.rows.map((row) => <div className="comparison-row" key={row.field}><strong>{row.field}</strong><span className={row.catalog ? "" : "missing"}>{row.catalog ? <CheckCircle size={16} weight="fill" /> : <WarningCircle size={16} />} {row.catalog || "Not available"}</span><span className={row.status}>{row.status === "match" ? <CheckCircle size={16} weight="fill" /> : <WarningCircle size={16} weight={row.status === "conflict" ? "fill" : "regular"} />} {row.file || "Not available"}</span></div>)}{comparison.catalogUrl ? <div className="comparison-source"><a href={comparison.catalogUrl} target="_blank" rel="noreferrer">Open metadata source</a></div> : null}</div>;
}

// Each section's name, the line under its title, and -- for a phone's list of
// them, grouped as iOS Settings groups its rows -- its icon and group.
const SETTINGS_SECTIONS = [
  { id: "profile", label: "Your profile", icon: UserCircle, group: "you",
    detail: "Your name and picture, what switching to you asks for, and this device." },
  { id: "health", label: "Library health", icon: Heartbeat, group: "library",
    detail: "Damaged files and matches waiting for you to confirm or fix." },
  { id: "library", label: "Library folders", icon: FolderOpen, group: "library",
    detail: "The folders Flipparr reads your comics from, and how often it checks them." },
  { id: "matching", label: "Matching and fixes", icon: Sparkle, group: "library",
    detail: "How Flipparr decides what a file is, and which formats it manages." },
  { id: "acquisition", label: "Acquisition services", icon: CloudArrowDown, group: "sources",
    detail: "Where releases are found and downloaded, and in which language." },
  { id: "metadata", label: "Metadata sources", icon: Database, group: "sources",
    detail: "Where issue titles, dates, covers and matches come from." },
  { id: "reader", label: "Reader", icon: BookOpen, group: "reading",
    detail: "Panel view, and the models that can help it with the pages it cannot read." },
  { id: "profiles", label: "Profiles", icon: Users, group: "app",
    detail: "Who reads this library. Each profile keeps its own place, history and ratings." },
  { id: "security", label: "Security", icon: LockSimple, group: "app",
    detail: "Who can reach this app, and how they sign in." },
];

// The sections a profile is shown: everything for the admin; a reader's own
// profile and reader settings.
// A reader's sections say only what is theirs: the vision models behind
// panel view are the admin's.
const READER_SECTION_DETAIL = { reader: "How panel view reads a page, one panel at a time." };

function settingsSectionsFor(viewer) {
  return SETTINGS_SECTIONS.filter((item) => can(viewer, `settings.${item.id}`))
    .map((item) => (!isAdmin(viewer) && READER_SECTION_DETAIL[item.id] ? { ...item, detail: READER_SECTION_DETAIL[item.id] } : item));
}

// One block of a settings section, as a titled card; `action` sits at the
// title's end.
function SettingsCard({ title, action, className = "", children }) {
  return <section className={`settings-card ${className}`.trim()}>
    <header><h3>{title}</h3>{action}</header>
    {children}
  </section>;
}

// A phone's first level of Settings, after iPhone Settings: grouped rows of
// an icon and a name, each opening its section as a page. A count rides
// beside the name rather than in a column of its own, which would set every
// row's columns differently and stagger the chevrons.
// Which build the app is: the server's, from /healthz, so a phone that has
// held on to an old page can be told apart from a bug in the new one.
function useBuild() {
  const [build, setBuild] = useState(() => lastAnswer("/healthz")?.build || "");
  useEffect(() => {
    let live = true;
    apiRequest("/healthz").then((data) => { if (live && data?.build) setBuild(data.build); }).catch(() => {});
    return () => { live = false; };
  }, []);
  return build;
}

function SettingsIndex({ counts = {}, onOpen, sections = SETTINGS_SECTIONS }) {
  const build = useBuild();
  const groups = [...new Set(sections.map((item) => item.group))];
  return <nav className="settings-index" aria-label="Settings sections">
    {groups.map((group) => <ul key={group}>
      {sections.filter((item) => item.group === group).map(({ id, label, icon: Icon }) => <li key={id}>
        <button type="button" onClick={() => onOpen(id)}
          aria-label={counts[id] ? `${label}. ${counts[id]} need${counts[id] === 1 ? "s" : ""} a decision` : undefined}>
          <span className={`settings-index-icon settings-index-icon--${id}`} aria-hidden="true"><Icon size={18} weight="fill" /></span>
          <span className="settings-index-label">
            <span>{label}</span>
            {counts[id] ? <b className="settings-index-badge">{counts[id]}</b> : null}
          </span>
          <CaretRight size={16} aria-hidden="true" />
        </button>
      </li>)}
    </ul>)}
    {build ? <p className="settings-build">Flipparr build {build}</p> : null}
  </nav>;
}

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
            label, so the small mark above it would be the mark twice over. */}
        {step.id === "welcome" ? null : <div className="setup-topline">
          <span className="setup-brand"><FlipparrMark size={24} /></span>
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
        {providers.filter((provider) => !provider.builtIn && provider.kind !== "reading").map((provider) => <Provider provider={provider} concise onConfigure={() => setEditingProvider(provider)} key={provider.id} />)}
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

// ---- Reader profiles ----------------------------------------------------------
//
// A profile is drawn as a coloured disc with its initials, the way a Plex Home
// or Netflix profile is; the colours are the design tokens' profile palette.
function ProfileAvatar({ profile, size = "md" }) {
  return <span className={`profile-avatar profile-avatar--${size} profile-avatar--${profileColour(profile)}`} aria-hidden="true">
    {profile?.avatar ? <img src={profile.avatar} alt="" draggable="false" /> : initials(profile?.name)}
  </span>;
}

// Profiles to choose from, as Plex and Netflix show them everywhere a profile
// is chosen -- the picker, the header's menu, its sheet: a large disc, the name
// under it, and a word for the one reading now or one that asks for a PIN.
function ProfileTiles({ profiles, current, onChoose, onAdd = null, busy = false }) {
  return <ul className="profile-picker-list">
    {profiles.map((profile) => <li key={profile.id}>
      <button type="button" onClick={() => onChoose(profile)} disabled={busy} aria-current={profile.id === current ? "true" : undefined}>
        <ProfileAvatar profile={profile} size="lg" />
        <span className="profile-tile-name">{profile.name}</span>
        {profile.id === current ? <small>Reading now</small>
          : isLocked(profile) ? <small><LockSimple size={12} /> Locked</small> : null}
      </button>
    </li>)}
    {onAdd ? <li>
      <button type="button" onClick={onAdd} disabled={busy}>
        <span className="profile-avatar profile-avatar--lg profile-avatar--add" aria-hidden="true"><Plus size={32} weight="light" /></span>
        <span className="profile-tile-name">Add profile</span>
      </button>
    </li> : null}
  </ul>;
}

// Your picture in every page's header, where Plex keeps it: a tap opens a
// menu of who else can read here, adding a profile, your own page, and
// signing out. The header is the one place every page has.
function HeaderProfile() {
  const header = useContext(HeaderContext);
  const viewer = useViewer();
  const [open, setOpen] = useState(false);
  if (!header?.profile || !viewer) return null;
  return <div className="appbar-profile">
    <button type="button" className={`glass-button glass-button--icon appbar-profile-button${open ? " active" : ""}`}
      onClick={() => setOpen((value) => !value)} aria-expanded={open} aria-label={`${viewer.name}: profiles`}>
      <ProfileAvatar profile={viewer} size="fill" />
    </button>
    {open ? <ProfileMenu onClose={() => setOpen(false)} /> : null}
  </div>;
}

// On a phone the menu is a sheet from the bottom, as the bell is, with rows a
// finger can hit; elsewhere a glass menu under the avatar.
function ProfileMenu({ onClose }) {
  const header = useContext(HeaderContext);
  const viewer = useViewer();
  const actions = header.profile;
  const phone = usePhoneWidth();
  const dialogRef = useDialog(onClose);
  const [others, setOthers] = useState(() => (actions.household ? null : []));
  useEffect(() => {
    if (phone) return undefined;
    function handlePointerDown(event) {
      const node = dialogRef.current;
      if (!node || node.contains(event.target) || event.target.closest?.(".appbar-profile")) return;
      onClose();
    }
    document.addEventListener("pointerdown", handlePointerDown, true);
    return () => document.removeEventListener("pointerdown", handlePointerDown, true);
  }, [onClose, phone]);
  useEffect(() => {
    if (!actions.household) return;
    apiRequest("/api/v1/profiles")
      .then((data) => setOthers((data.profiles || []).filter((profile) => profile.id !== viewer.id)))
      .catch(() => setOthers([]));
  }, []);
  const go = (action) => () => { onClose(); action(); };
  // You first, as Plex puts the one signed in first: your tile opens your
  // page, anyone else's switches to them.
  const rows = <>
    <ProfileTiles profiles={[viewer, ...(others || [])]} current={viewer.id}
      onChoose={(profile) => go(profile.id === viewer.id ? actions.onOpenProfile : () => actions.onSwitchTo(profile))()}
      onAdd={isAdmin(viewer) && others !== null ? go(actions.onAdd) : null} />
    {others === null ? <p className="profile-menu-loading"><LoadingSpinner size={16} /></p> : null}
    {actions.method === "forms" ? <div className="profile-menu-actions">
      <button type="button" className="profile-menu-item profile-menu-quiet" onClick={go(actions.onSignOut)}>
        <SignOut size={18} /><span>Sign out</span>
      </button>
    </div> : null}
  </>;
  if (phone) {
    return createPortal(<div className="modal-backdrop" onMouseDown={onClose}>
      <section className="modal profile-sheet" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="profile-sheet-title"
        onMouseDown={(event) => event.stopPropagation()}>
        <SheetGrabber onClose={onClose} pullAnywhere />
        <header>
          <h2 id="profile-sheet-title">Who's reading?</h2>
          <button type="button" className="glass-button glass-button--icon library-sheet-close"
            onClick={() => slideSheetAway(dialogRef.current, onClose)} aria-label="Close"><X size={20} /></button>
        </header>
        <div className="profile-menu">{rows}</div>
      </section>
    </div>, document.body);
  }
  return <div className="profile-menu glass-menu" ref={dialogRef} role="dialog" aria-modal="false" aria-label="Who's reading?">{rows}</div>;
}

// Adding a profile the way Plex adds one: a name. Its colour is given to it
// (one nobody else has); a picture, a PIN, a sign-in of its own come later,
// from Edit.
function AddProfileSheet({ onClose, onAdded }) {
  const dialogRef = useDialog(onClose);
  const [name, setName] = useState("");
  const [colour, setColour] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Your own profile needs a PIN (or a password) before anyone else can
  // join, or anyone at a shared device could open it; asked here, once,
  // rather than refused.
  const [guarded, setGuarded] = useState(null);
  const [ownPin, setOwnPin] = useState("");
  useEffect(() => {
    Promise.all([apiRequest("/api/v1/me"), apiRequest("/api/v1/users")])
      .then(([me, list]) => { setGuarded(isLocked(me?.profile)); setColour(nextProfileColour(list?.users)); })
      .catch(() => setGuarded(true));
  }, []);
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (guarded === false) {
        await apiRequest("/api/v1/me", {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pin: ownPin }),
        });
        setGuarded(true);
      }
      const created = await apiRequest("/api/v1/users", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
      });
      onAdded(created);
    } catch (failure) {
      setError(failure.message);
      setBusy(false);
    }
  }
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal add-profile-sheet" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="add-profile-title" onMouseDown={(event) => event.stopPropagation()}>
    <DialogCloseButton onClose={onClose} label="Close" />
    <h2 id="add-profile-title">Add a profile</h2>
    <p className="workbench-intro">Everyone who reads here gets their own place, history and ratings. You can give them a picture, a PIN or a sign-in of their own afterwards.</p>
    <form onSubmit={submit}>
      <div className="add-profile-preview"><ProfileAvatar profile={{ name: name || "?", colour }} size="lg" /></div>
      <label className="form-field"><span>Name</span>
        <input value={name} maxLength={40} autoFocus onChange={(event) => setName(event.target.value)} placeholder="Their name" />
      </label>
      {guarded === false ? <label className="form-field"><span>A PIN for your own profile</span>
        <input type="password" inputMode="numeric" autoComplete="off" value={ownPin}
          onChange={(event) => setOwnPin(event.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="Four to six digits" />
        <small>Asked when someone switches to you, so nobody else can open your profile.</small>
      </label> : null}
      {error ? <p className="workbench-error" role="alert">{error}</p> : null}
      <button className="primary-button add-profile-submit" disabled={busy || !name.trim() || guarded === null || (guarded === false && ownPin.length < 4)} aria-busy={busy}>
        {busy ? <LoadingSpinner size={17} /> : <Plus size={17} />} Add profile
      </button>
    </form>
  </section></div>;
}

// A profile's picture: a photo, or a cover from the library, or none.
function ProfilePictureEditor({ profile, catalog, onChanged }) {
  const fileRef = useRef(null);
  const [choosing, setChoosing] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  async function run(kind, request) {
    setBusy(kind);
    setError("");
    try {
      onChanged(await request());
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy("");
    }
  }
  const upload = (file) => run("upload", () => apiRequest(`/api/v1/profiles/${profile.id}/avatar/upload`, {
    method: "POST", headers: { "Content-Type": file.type || "image/jpeg" }, body: file,
  }));
  const choose = (fileId) => run("library", () => apiRequest(`/api/v1/profiles/${profile.id}/avatar`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fileId, page: 0 }),
  }));
  const remove = () => run("remove", () => apiRequest(`/api/v1/profiles/${profile.id}/avatar`, { method: "DELETE" }));
  return <div className="profile-picture-editor">
    <ProfileAvatar profile={profile} size="lg" />
    <div className="profile-picture-actions">
      <input ref={fileRef} type="file" accept="image/*" hidden onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) upload(file); }} />
      <button type="button" className="secondary-button" onClick={() => fileRef.current?.click()} disabled={Boolean(busy)}>
        {busy === "upload" ? <LoadingSpinner size={16} /> : <UploadSimple size={16} />} Upload a photo
      </button>
      <button type="button" className="secondary-button" onClick={() => setChoosing(true)} disabled={Boolean(busy) || !catalog?.series?.length}>
        {busy === "library" ? <LoadingSpinner size={16} /> : <Books size={16} />} Choose a cover
      </button>
      {profile.avatar ? <button type="button" className="secondary-button" onClick={remove} disabled={Boolean(busy)}>Remove picture</button> : null}
    </div>
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
    {choosing ? <LibraryPicturePicker catalog={catalog} onClose={() => setChoosing(false)} onChoose={(fileId) => { setChoosing(false); choose(fileId); }} /> : null}
  </div>;
}

// Any cover in the library as a picture: the comic reader's own answer to a
// profile photo.
function LibraryPicturePicker({ catalog, onClose, onChoose }) {
  const dialogRef = useDialog(onClose);
  const [filter, setFilter] = useState("");
  const runs = (catalog?.series || []).filter((series) => series.fileDetails?.length
    && (!filter || String(series.title || "").toLowerCase().includes(filter.toLowerCase())));
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal library-picture-picker" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="library-picture-title" onMouseDown={(event) => event.stopPropagation()}>
    <DialogCloseButton onClose={onClose} label="Close" />
    <h2 id="library-picture-title">Choose a cover</h2>
    <label className="form-field"><span>Find a run</span>
      <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Title" />
    </label>
    <div className="library-picture-grid">
      {runs.slice(0, 120).map((series) => <button type="button" key={series.id} onClick={() => onChoose(series.fileDetails[0].id)} aria-label={`Use the cover of ${series.title}`}>
        <SeriesCover series={series} decorative />
        <span>{series.title}</span>
      </button>)}
    </div>
  </section></div>;
}

// A profile's own page, as Plex has one: the picture large, the name, what
// they have read, and their reading history. On a phone it is a sheet over
// whatever page it was opened from (ProfileSheet) -- reached from the header,
// it has no tab, and as a page it left the tab bar nothing to show -- and
// `onLeave` closes the sheet before anything in it takes you elsewhere.
function ProfileView({ onRead, onNavigate, authStatus, onSwitch, sheet = false, onLeave = null }) {
  const viewer = useViewer();
  const [profile, setProfile] = useState(() => lastAnswer("/api/v1/me")?.profile ?? null);
  const [activity, setActivity] = useState(() => lastAnswer("/api/v1/me/activity"));
  useEffect(() => {
    apiRequest("/api/v1/me").then((data) => setProfile(data.profile)).catch(() => {});
    apiRequest("/api/v1/me/activity").then(setActivity).catch(() => {});
  }, []);
  const shown = profile || viewer;
  const stats = activity?.stats;
  const since = String(profile?.createdAt || "").slice(0, 4);
  const figures = stats ? [
    [stats.issuesRead, stats.issuesRead === 1 ? "issue read" : "issues read"],
    [stats.inProgress, "in progress"],
    [stats.runs, stats.runs === 1 ? "run" : "runs"],
    [stats.pagesRead, stats.pagesRead === 1 ? "page" : "pages"],
    [stats.finishedThisYear, `in ${stats.year}`],
  ] : [];
  const history = activity?.history || [];
  const leaving = (action) => (...args) => { onLeave?.(); action(...args); };
  return <>
    {sheet ? null : <PageHeader title="Profile" />}
    <section className="profile-hero">
      {shown?.avatar ? <img className="profile-hero-backdrop" src={shown.avatar} alt="" aria-hidden="true" /> : null}
      <ProfileAvatar profile={shown} size="xl" />
      <h2>{shown?.name}</h2>
      {since ? <p className="profile-hero-since">Reading here since {since}</p> : null}
      {figures.length ? <dl className="profile-stats">{figures.map(([value, label]) => <div key={label}>
        <dd>{Number(value || 0).toLocaleString()}</dd><dt>{label}</dt>
      </div>)}</dl> : <p className="profile-stats-loading"><LoadingSpinner size={18} /></p>}
      <div className="profile-hero-actions">
        <button type="button" className="secondary-button" onClick={leaving(() => onNavigate("settings", "profile"))}><PencilSimple size={18} /> Edit profile</button>
        {authStatus?.household ? <button type="button" className="secondary-button" onClick={leaving(onSwitch)}><UserCircle size={18} /> Switch profile</button> : null}
      </div>
    </section>
    <section className="profile-history" aria-labelledby="profile-history-title">
      <h2 id="profile-history-title">Reading history</h2>
      {activity && !history.length ? <p className="profile-history-empty">Comics you read appear here, newest first.</p> : null}
      <div className="profile-history-shelf">{history.map((item) => {
        const fraction = item.finishedAt ? 1 : item.pageCount ? Math.min(1, (item.page + 1) / item.pageCount) : 0;
        return <button type="button" className="profile-history-card" key={item.fileId} onClick={leaving(() => onRead({ id: item.fileId }))}>
          <span className="profile-history-cover">
            {/* A comic's first page is its cover, and a page a reader may fetch. */}
            <img src={`/api/v1/files/${item.fileId}/pages/0`} alt="" loading="lazy" />
            {item.finishedAt ? <b className="profile-history-done" aria-label="Finished"><Check size={14} weight="bold" /></b> : null}
            {!item.finishedAt && fraction ? <i className="profile-history-progress" style={{ "--progress": `${Math.round(fraction * 100)}%` }} aria-hidden="true" /> : null}
          </span>
          <strong>{item.seriesTitle || item.filename}</strong>
          <small>{[item.issueNumber ? `#${item.issueNumber}` : "", item.finishedAt ? "Finished" : item.pageCount ? `Page ${item.page + 1} of ${item.pageCount}` : ""].filter(Boolean).join(" · ")}</small>
        </button>;
      })}</div>
    </section>
  </>;
}

// Your profile on a phone: a sheet the height of the screen over the page you
// were on, as an iOS app's account sheet is, with the picture's blur filling
// its top. It pulls down and slides away like every sheet.
function ProfileSheet({ onClose, ...props }) {
  const dialogRef = useDialog(onClose);
  return createPortal(<div className="modal-backdrop" onMouseDown={onClose}>
    <section className="modal profile-page-sheet" ref={dialogRef} role="dialog" aria-modal="true" aria-label="Your profile"
      onMouseDown={(event) => event.stopPropagation()}>
      <SheetGrabber onClose={onClose} pullAnywhere />
      <button type="button" className="glass-button glass-button--icon profile-page-sheet-close"
        onClick={() => slideSheetAway(dialogRef.current, onClose)} aria-label="Close"><X size={20} /></button>
      <ProfileView {...props} sheet onLeave={onClose} />
    </section>
  </div>, document.body);
}

// "Who's reading?" -- a shared device with more than one profile asks before
// showing anyone's library. A profile with a PIN asks for it; the admin
// without a PIN asks for their password; a reader with neither just opens.
// A PIN the way a phone's lock screen asks for one: a dot for each digit and
// a pad of keys -- no field, no Continue. The last digit opens the profile.
// Bottom right is Cancel until there is a digit to delete. A PIN whose length
// is not known yet (set before lengths were kept) gets a ✓ key, once.
const PIN_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];

function PinPad({ length = null, busy = false, error = "", onSubmit, onCancel, onInput }) {
  const [pin, setPin] = useState("");
  const [shakes, setShakes] = useState(0);
  const pinRef = useRef("");
  const submitting = useRef(false);
  async function submit(value) {
    if (submitting.current) return;
    submitting.current = true;
    const ok = await onSubmit(value);
    submitting.current = false;
    // Wrong: the dots shake and empty, ready for another go.
    if (!ok) { pinRef.current = ""; setPin(""); setShakes((count) => count + 1); }
  }
  function press(key) {
    if (busy || submitting.current) return;
    if (key === "Enter") {
      if (!length && pinRef.current.length >= 4) submit(pinRef.current);
      return;
    }
    const next = pinInput(pinRef.current, key);
    if (next === pinRef.current) return;
    onInput?.();
    pinRef.current = next;
    setPin(next);
    if (length && next.length === length) submit(next);
  }
  // A keyboard types on the pad too. Escape inside an overlay is the dialog's,
  // which takes it back to the profiles.
  useEffect(() => {
    function onKey(event) {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (/^\d$/.test(event.key) || event.key === "Backspace" || event.key === "Enter") {
        event.preventDefault();
        press(event.key);
      } else if (event.key === "Escape") {
        onCancel();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  const dots = Math.max(length || 4, pin.length);
  return <div className="pin-pad" role="group" aria-label="PIN">
    <div className={`pin-dots${shakes ? " pin-dots--wrong" : ""}`} key={shakes} aria-hidden="true">
      {Array.from({ length: dots }, (_, index) => <i className={index < pin.length ? "filled" : ""} key={index} />)}
    </div>
    <p className="sr-only" aria-live="polite">{length ? `${pin.length} of ${length} digits` : `${pin.length} digits`}</p>
    <p className="pin-pad-message" role={error ? "alert" : undefined}>{busy ? <LoadingSpinner size={16} /> : error || " "}</p>
    <div className="pin-keys">
      {PIN_KEYS.map((key) => <button type="button" className="pin-key" onClick={() => press(key)} disabled={busy} key={key}>{key}</button>)}
      {length ? <span aria-hidden="true" /> : <button type="button" className="pin-key pin-key--quiet" onClick={() => press("Enter")}
        disabled={busy || pin.length < 4} aria-label="Enter PIN"><Check size={24} weight="bold" /></button>}
      <button type="button" className="pin-key" onClick={() => press("0")} disabled={busy}>0</button>
      {pin ? <button type="button" className="pin-key pin-key--quiet" onClick={() => press("Backspace")} disabled={busy} aria-label="Delete"><Backspace size={24} /></button>
        : <button type="button" className="pin-key pin-key--quiet pin-key--text" onClick={onCancel} disabled={busy}>Cancel</button>}
    </div>
  </div>;
}

function WhoIsReadingView({ signInAvailable, overlay = false, current = null, ask = null, onClose, canAdd = false, onAdd }) {
  const [profiles, setProfiles] = useState(null);
  const [asking, setAsking] = useState(ask);
  // Escape while a PIN or password is asked goes back to the profiles, not out.
  const overlayRef = useDialog(overlay ? () => (asking ? back() : onClose?.()) : null);
  function back() { setAsking(null); setError(""); }
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    apiRequest("/api/v1/profiles").then((data) => setProfiles(data.profiles || [])).catch((failure) => setError(failure.message));
  }, []);
  async function enter(profile, proof = {}) {
    setBusy(true);
    setError("");
    try {
      const result = await apiRequest("/api/v1/profiles/switch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ userId: profile.id, ...proof }),
      });
      enterProfile(result.viewer.id);
      return true;
    } catch (failure) {
      setError(failure.message);
      setBusy(false);
      return false;
    }
  }
  function choose(profile) {
    // Choosing who is already reading is choosing to carry on.
    if (profile.id === current) { onClose?.(); return; }
    if (isLocked(profile)) {
      setAsking(profile);
      setSecret("");
      setError("");
    } else {
      enter(profile);
    }
  }
  return <div className={`login-shell profile-picker${overlay ? " profile-picker--overlay" : ""}`}
    ref={overlay ? overlayRef : undefined} role={overlay ? "dialog" : undefined} aria-modal={overlay ? "true" : undefined} aria-label={overlay ? "Who's reading?" : undefined}>
    {overlay ? <button type="button" className="glass-button glass-button--icon profile-picker-close" onClick={() => onClose?.()} aria-label="Close"><X size={20} /></button> : null}
    <div className="profile-picker-card">
      <FlipparrMark size={48} />
      <h1>{asking ? asking.name : "Who’s reading?"}</h1>
      {asking?.lock === "pin" ? <div className="profile-picker-secret">
        <ProfileAvatar profile={asking} size="lg" />
        <PinPad length={asking.pinLength} busy={busy} error={error} onCancel={back} onInput={() => setError("")}
          onSubmit={(pin) => enter(asking, { pin })} />
      </div> : asking ? <form className="profile-picker-secret" onSubmit={(event) => {
        event.preventDefault();
        enter(asking, asking.lock === "pin" ? { pin: secret } : { password: secret });
      }}>
        <ProfileAvatar profile={asking} size="lg" />
        <label className="form-field"><span>{asking.lock === "pin" ? "PIN" : "Password"}</span>
          <input type="password" autoFocus value={secret} name={asking.lock === "pin" ? "pin" : "password"}
            inputMode={asking.lock === "pin" ? "numeric" : undefined}
            autoComplete={asking.lock === "pin" ? "off" : "current-password"}
            onChange={(event) => setSecret(asking.lock === "pin" ? event.target.value.replace(/\D/g, "").slice(0, 6) : event.target.value)} />
        </label>
        {error ? <p className="login-error" role="alert">{error}</p> : null}
        <div className="profile-picker-actions">
          <button type="button" className="secondary-button" onClick={() => { setAsking(null); setError(""); }}>Back</button>
          <button className="primary-button" disabled={busy || !secret} aria-busy={busy}>
            {busy ? <LoadingSpinner size={18} /> : null} Continue
          </button>
        </div>
      </form> : <>
        {profiles === null && !error ? <LoadingSpinner size={22} /> : null}
        <ProfileTiles profiles={profiles || []} current={current} onChoose={choose} busy={busy}
          onAdd={canAdd && profiles ? () => onAdd?.() : null} />
        {error ? <p className="login-error" role="alert">{error}</p> : null}
        {signInAvailable ? <button type="button" className="profile-picker-other" onClick={() => enterProfile(null)}>
          Sign in with a password instead
        </button> : null}
      </>}
    </div>
  </div>;
}

// The admin's list of profiles. Readers read and rate; the library, what is
// downloaded and every setting stay the admin's.
function ProfilesSettings({ catalog }) {
  const header = useContext(HeaderContext);
  const [users, setUsers] = useState(() => lastAnswer("/api/v1/users")?.users ?? null);
  const [security, setSecurity] = useState(() => lastAnswer("/api/v1/auth") ?? null);
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState("");
  async function load() {
    try {
      const [list, auth] = await Promise.all([apiRequest("/api/v1/users"), apiRequest("/api/v1/auth")]);
      setUsers(list.users || []);
      setSecurity(auth);
      setError("");
    } catch (failure) {
      setError(failure.message);
    }
  }
  // Reloaded when a profile is added from anywhere -- the header's menu, the picker.
  useEffect(() => { load(); }, [header?.profile?.version]);
  const owner = users?.find((user) => user.id === 1);
  const guarded = isLocked(owner);
  return <>
    <SettingsCard title="Profiles" action={<button type="button" className="secondary-button" onClick={() => header?.profile?.onAdd()} disabled={!guarded}><Plus size={18} /> Add profile</button>}>
      <p className="settings-card-lead">Readers can read and rate; the library, downloads and settings stay yours.</p>
      {users && !guarded ? <p className="settings-card-note">First give your own profile a PIN (in Your profile) or a password (in Security), so nobody at a shared device can open yours.</p> : null}
      {users === null && !error ? <CatalogLoading title="Loading profiles…" detail="" /> : null}
      {error ? <p className="workbench-error" role="alert">{error}</p> : null}
      <div className="profile-rows">
        {(users || []).map((user) => <button type="button" className="profile-row" key={user.id} onClick={() => setEditing(user)}>
          <ProfileAvatar profile={user} />
          <span><strong>{user.name}</strong><small>{[
            user.role === "admin" ? "Admin" : "Reader",
            user.disabled ? "Off" : "",
            LOCK_LABELS[user.lock] ?? "",
            limitLabel(user),
            user.loginName ? `Signs in as ${user.loginName}` : "",
          ].filter(Boolean).join(" · ")}</small></span>
          <CaretRight size={16} aria-hidden="true" />
        </button>)}
      </div>
    </SettingsCard>
    <RatingsCard />
    <SettingsCard title="Shared devices">
      <p className="settings-card-lead">A device you sign in on with your password asks &ldquo;Who&rsquo;s reading?&rdquo; from then on, like a Plex Home. A reader who signs in with their own password on their own phone gets only their profile.</p>
    </SettingsCard>
    {editing ? <ProfileEditorModal user={editing} catalog={catalog} adminPassword={Boolean(security?.configured)} onClose={() => setEditing(null)}
      onPictureChanged={async (updated) => { setEditing(updated); await load(); }}
      onSaved={async () => { setEditing(null); await load(); }} /> : null}
  </>;
}

// What switching to a profile asks for, as the profile chooses: a tap, its
// PIN or its password (the admin's is the one in Security). Your profile and
// the admin's editor both draw it; the form around it saves.
function SwitchLockFields({ profile, self = false, adminPassword, lock, onLock, pin, onPin, password, onPassword }) {
  const owner = profile?.id === 1;
  const choices = lockChoices(profile, { adminPassword });
  const reasons = choices.filter((choice) => choice.disabled && choice.reason).map((choice) => choice.reason);
  const whom = self ? "you" : profile?.name || "them";
  const detail = {
    open: `Anyone at a shared device can switch to ${whom} with a tap.`,
    pin: `Switching to ${whom} asks for a PIN.`,
    password: owner ? `Switching to ${whom} asks for the password in Security.` : `Switching to ${whom} asks for ${self ? "your" : "their"} password.`,
  }[lock];
  return <div className="switch-lock">
    <GlassSegmented label="When switching, ask for" items={choices} value={lock} onChange={onLock} />
    <p className="settings-card-note">{[detail, ...reasons].join(" ")}</p>
    {lock === "pin" ? <label className="form-field"><span>{profile?.hasPin ? "New PIN" : "PIN"}</span>
      <input type="password" inputMode="numeric" autoComplete="off" value={pin}
        onChange={(event) => onPin(event.target.value.replace(/\D/g, "").slice(0, 6))}
        placeholder={profile?.hasPin ? "Leave blank to keep it" : "Four to six digits"} />
    </label> : null}
    {lock === "password" && !owner && !profile?.hasPassword ? <label className="form-field"><span>Password</span>
      <input type="password" autoComplete="new-password" value={password} onChange={(event) => onPassword(event.target.value)}
        placeholder="At least 8 characters" />
    </label> : null}
  </div>;
}

// How many runs have a rating and where it came from, and the switch that
// lets the vision connector read the ratings printed on covers.
function RatingsCard() {
  const [summary, setSummary] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  async function load() {
    try { setSummary(await apiRequest("/api/v1/ratings")); } catch (failure) { setError(failure.message); }
  }
  useEffect(() => { load(); }, []);
  async function run(kind, action) {
    setBusy(kind);
    setError("");
    try { await action(); await load(); } catch (failure) { setError(failure.message); }
    setBusy("");
  }
  const toggleCovers = (on) => run("covers", () => apiRequest("/api/v1/settings", {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ratingsFromCovers: on }),
  }));
  const lookAgain = () => run("again", () => apiRequest("/api/v1/ratings/check", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  }));
  const parts = summary ? [
    summary.by_metron ? `${summary.by_metron} from Metron` : null,
    summary.by_cover ? `${summary.by_cover} from covers` : null,
    summary.by_admin ? `${summary.by_admin} set by you` : null,
  ].filter(Boolean) : [];
  return <SettingsCard title="Ratings" action={summary ? <button type="button" className="secondary-button" onClick={lookAgain} disabled={Boolean(busy)} aria-busy={busy === "again"}>
    {busy === "again" ? <LoadingSpinner size={16} /> : <ArrowsClockwise size={16} />} Look again</button> : null}>
    {summary ? <>
      <p className="settings-card-lead">{summary.rated} of {summary.runs} run{summary.runs === 1 ? " has" : "s have"} a rating{parts.length ? `: ${parts.join(", ")}` : ""}.{summary.waiting ? ` ${summary.waiting} still to look at.` : ""} A profile&rsquo;s limit is set in its profile, and a run&rsquo;s rating in its Edit sheet.</p>
      <Toggle checked={summary.fromCovers} onChange={busy || !summary.visionConnected ? () => {} : toggleCovers} disabled={!summary.visionConnected}
        title="Read ratings printed on covers"
        description={summary.visionConnected
          ? "For runs Metron has no rating for, the newest cover is sent to your vision connector to read the rating printed there, such as DC's 13+ TEEN or Marvel's RATED T+. One cover per run, on your key."
          : "Connect Claude or ChatGPT in Settings, Reader first."} />
    </> : error ? null : <LoadingSpinner size={18} />}
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
  </SettingsCard>;
}

function ProfileEditorModal({ user, catalog, adminPassword, onClose, onSaved, onPictureChanged }) {
  const dialogRef = useDialog(onClose);
  const creating = !user.id;
  const owner = user.id === 1;
  const [name, setName] = useState(user.name || "");
  const [lock, setLock] = useState(user.lock || "open");
  const [pin, setPin] = useState("");
  const [loginName, setLoginName] = useState(user.loginName || "");
  const [password, setPassword] = useState("");
  const [showOnPicker, setShowOnPicker] = useState(user.showOnPicker ?? true);
  const [maxRating, setMaxRating] = useState(user.maxRating || "");
  const [allowUnrated, setAllowUnrated] = useState(Boolean(user.allowUnrated));
  const [canDiscover, setCanDiscover] = useState(user.canDiscover ?? true);
  // A first limit keeps them out of Discover too, as the server does; the
  // switch is there to say otherwise.
  function chooseLimit(next) {
    if (next && !maxRating) setCanDiscover(false);
    setMaxRating(next);
  }
  const lockEdit = creating ? {} : lockPatch(user, lock, { pin, password });
  // The lock's own password field stands in for the sign-in one while there is none.
  const lockAsksPassword = !creating && !owner && lock === "password" && !user.hasPassword;
  const [enabled, setEnabled] = useState(!user.disabled);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  async function call(path, method, body) {
    return apiRequest(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  }
  async function save(event) {
    event.preventDefault();
    setBusy("save");
    setError("");
    const payload = { name, showOnPicker, ...lockEdit };
    if (!owner) Object.assign(payload, { disabled: !enabled, maxRating: maxRating || null, allowUnrated, canDiscover });
    if (creating && pin) payload.pin = pin;
    if (!owner && (loginName || user.loginName) && loginName !== (user.loginName || "")) payload.loginName = loginName || null;
    if (!owner && password) payload.password = password;
    try {
      if (creating) await call("/api/v1/users", "POST", payload);
      else await call(`/api/v1/users/${user.id}`, "PATCH", payload);
      await onSaved();
    } catch (failure) {
      setError(failure.message);
      setBusy("");
    }
  }
  async function act(kind) {
    setBusy(kind);
    setError("");
    try {
      if (kind === "signout") await call(`/api/v1/users/${user.id}/sign-out`, "POST", {});
      if (kind === "remove") await call(`/api/v1/users/${user.id}`, "DELETE");
      await onSaved();
    } catch (failure) {
      setError(failure.message);
      setBusy("");
    }
  }
  // Grouped as Settings groups a section: a card for each thing the admin
  // decides about the profile, and the profile's actions at the foot.
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal profile-editor" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="profile-editor-title" onMouseDown={(event) => event.stopPropagation()}>
    <DialogCloseButton onClose={onClose} label="Close profile" />
    <span className="eyebrow">{creating ? "New profile" : owner ? "Admin" : "Reader"}</span>
    <h2 id="profile-editor-title">{creating ? "Add a profile" : user.name}</h2>
    <div className="profile-editor-cards">
      {/* Outside the form, the name joined to it by id: the cover picker's
          search must not submit it. */}
      <SettingsCard title="Profile">
        <div className="profile-form">
          {creating ? null : <ProfilePictureEditor profile={user} catalog={catalog} onChanged={(updated) => onPictureChanged?.(updated)} />}
          <label className="form-field"><span>Name</span>
            <input form="profile-editor-form" value={name} maxLength={40} autoFocus={creating} onChange={(event) => setName(event.target.value)} placeholder="Their name" />
          </label>
        </div>
      </SettingsCard>
      <form id="profile-editor-form" className="profile-editor-cards" onSubmit={save}>
        {creating ? null : <SettingsCard title="When switching">
          <SwitchLockFields profile={user} adminPassword={adminPassword} lock={lock} onLock={setLock}
            pin={pin} onPin={setPin} password={password} onPassword={setPassword} />
        </SettingsCard>}
        <SettingsCard title={owner ? "Sign-in" : "Their own sign-in"}>
          {owner ? <p className="settings-card-note">Your sign-in name and password are in Security.</p> : <div className="profile-form">
            <p className="settings-card-lead">A name and password let them sign in on their own phone, without a shared device.</p>
            <label className="form-field"><span>Sign-in name</span>
              <input value={loginName} autoCapitalize="none" autoCorrect="off" spellCheck={false}
                onChange={(event) => setLoginName(event.target.value.trim())} placeholder="Optional" />
            </label>
            {lockAsksPassword ? <p className="settings-card-note">They sign in with the password set under When switching.</p>
              : <label className="form-field"><span>{user.hasPassword ? "New password" : "Password"}</span>
                <input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)}
                  placeholder={user.hasPassword ? "Leave blank to keep it" : "At least 8 characters"} />
              </label>}
          </div>}
        </SettingsCard>
        {owner || creating ? null : <SettingsCard title="What they can read">
          <div className="profile-form">
            <div className="form-field"><span>Highest rating</span>
              <GlassSelect label="Highest rating" value={maxRating || "any"} onChange={(next) => chooseLimit(next === "any" ? "" : next)}
                className="glass-select--fill"
                options={[{ value: "any", label: "No limit" }, ...RATINGS.filter((id) => id !== "mature").map((id) => ({ value: id, label: `Up to ${RATING_LABELS[id]}` }))]} /></div>
            <p className="settings-card-note">{maxRating
              ? `Runs rated above ${RATING_LABELS[maxRating]} are hidden from ${name || "them"}, with everything in them.`
              : `${name || "They"} can read everything in the library.`}</p>
            {maxRating ? <>
              <Toggle checked={allowUnrated} onChange={setAllowUnrated} title="Unrated comics"
                description="Many runs have no rating yet, and Image and Boom print none. Off, they are hidden too." />
              <Toggle checked={canDiscover} onChange={setCanDiscover} title="Discover"
                description="New releases and online search carry no rating to filter by, so they show every cover." />
            </> : null}
          </div>
        </SettingsCard>}
        <SettingsCard title="Access">
          <Toggle checked={showOnPicker} onChange={setShowOnPicker} title="Show on shared devices"
            description="Listed when a shared device asks who is reading." />
          {owner || creating ? null : <Toggle checked={enabled} onChange={setEnabled} title="Profile on"
            description="Off, this profile cannot be opened or signed in to. Its history is kept." />}
          {creating ? null : <div className="settings-card-actions">
            <button type="button" className="secondary-button" onClick={() => act("signout")} disabled={Boolean(busy)}>Sign out everywhere</button>
          </div>}
        </SettingsCard>
        {error ? <p className="workbench-error" role="alert">{error}</p> : null}
        <div className="provider-modal-actions">
          {!creating && !owner ? <button type="button" className="danger-button" onClick={() => act("remove")} disabled={Boolean(busy)}>Remove</button> : null}
          <span />
          <button className="primary-button" disabled={Boolean(busy) || !name.trim() || !lockEdit}>
            {busy === "save" ? <><LoadingSpinner size={17} /> Saving…</> : creating ? "Add profile" : "Save"}
          </button>
        </div>
      </form>
    </div>
  </section></div>;
}

// A profile about itself: name, picture, what switching to it asks for; a
// reader's own sign-in; and who is reading on this device.
function YourProfileSettings({ authStatus, onSignOut, catalog, onViewerChanged }) {
  const viewer = useViewer();
  const header = useContext(HeaderContext);
  const [profile, setProfile] = useState(() => lastAnswer("/api/v1/me")?.profile ?? null);
  const [name, setName] = useState(profile?.name ?? viewer?.name ?? "");
  const [lock, setLock] = useState(profile?.lock ?? "open");
  const [pin, setPin] = useState("");
  const [lockPassword, setLockPassword] = useState("");
  const [adminPassword, setAdminPassword] = useState(false);
  const [loginName, setLoginName] = useState(profile?.loginName ?? "");
  const [password, setPassword] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState({ card: "", text: "" });
  const admin = isAdmin(viewer);
  useEffect(() => {
    apiRequest("/api/v1/me").then((data) => {
      setProfile(data.profile);
      setName(data.profile?.name ?? "");
      setLock(data.profile?.lock ?? "open");
      setLoginName(data.profile?.loginName ?? "");
    }).catch(() => {});
    // Whether the admin has a password in Security to be asked for.
    if (admin) apiRequest("/api/v1/auth").then((auth) => setAdminPassword(Boolean(auth?.configured))).catch(() => {});
  }, []);
  async function patch(body, done) {
    setBusy(done);
    setError({ card: "", text: "" });
    setMessage("");
    try {
      const updated = await apiRequest("/api/v1/me", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setProfile(updated);
      setLock(updated.lock ?? "open");
      setPin("");
      setLockPassword("");
      setPassword("");
      setCurrentPassword("");
      setMessage(done);
      onViewerChanged?.();
    } catch (failure) {
      setError({ card: done, text: failure.message });
    } finally {
      setBusy("");
    }
  }
  function saveProfile(event) {
    event.preventDefault();
    patch({ name }, "profile");
  }
  const lockEdit = profile && (lock !== profile.lock || pin || lockPassword)
    ? lockPatch(profile, lock, { pin, password: lockPassword }) : null;
  function saveLock(event) {
    event.preventDefault();
    if (lockEdit) patch(lockEdit, "lock");
  }
  function saveSignIn(event) {
    event.preventDefault();
    const body = { loginName: loginName || null };
    if (password) Object.assign(body, { password, currentPassword });
    patch(body, "signin");
  }
  const household = Boolean(authStatus?.household);
  return <>
    <SettingsCard title="Profile">
      <div className="profile-form">
        {/* The picture saves itself; the form is the name's alone, so the
            cover picker's search cannot submit it. */}
        {profile ? <ProfilePictureEditor profile={profile} catalog={catalog} onChanged={(updated) => { setProfile(updated); onViewerChanged?.(); }} /> : <LoadingSpinner size={18} />}
        <form className="profile-form" onSubmit={saveProfile}>
          <label className="form-field"><span>Name</span><input value={name} maxLength={40} onChange={(event) => setName(event.target.value)} /></label>
          {error.card === "profile" ? <p className="workbench-error" role="alert">{error.text}</p> : null}
          <div className="settings-card-actions">
            <button className="primary-button" disabled={Boolean(busy) || !name.trim()} aria-busy={busy === "profile"}>
              {busy === "profile" ? <LoadingSpinner size={18} /> : null} Save
            </button>
            {message === "profile" && !busy ? <small role="status">Saved</small> : null}
          </div>
        </form>
      </div>
    </SettingsCard>
    <SettingsCard title="Switching to you">
      {profile ? <form className="profile-form" onSubmit={saveLock}>
        <SwitchLockFields profile={profile} self adminPassword={adminPassword} lock={lock} onLock={setLock}
          pin={pin} onPin={setPin} password={lockPassword} onPassword={setLockPassword} />
        {error.card === "lock" ? <p className="workbench-error" role="alert">{error.text}</p> : null}
        <div className="settings-card-actions">
          <button className="primary-button" disabled={Boolean(busy) || !lockEdit} aria-busy={busy === "lock"}>
            {busy === "lock" ? <LoadingSpinner size={18} /> : null} Save
          </button>
          {message === "lock" && !busy ? <small role="status">Saved</small> : null}
        </div>
      </form> : <LoadingSpinner size={18} />}
    </SettingsCard>
    {admin ? null : <SettingsCard title="Your own sign-in">
      <form className="profile-form" onSubmit={saveSignIn}>
        <p className="settings-card-lead">A name and password let you sign in to your profile on your own phone, without a shared device.</p>
        <label className="form-field"><span>Sign-in name</span>
          <input value={loginName} autoCapitalize="none" autoCorrect="off" spellCheck={false} autoComplete="username"
            onChange={(event) => setLoginName(event.target.value.trim())} />
        </label>
        <label className="form-field"><span>{profile?.hasPassword ? "New password" : "Password"}</span>
          <input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="At least 8 characters" />
        </label>
        {profile?.hasPassword && password ? <label className="form-field"><span>Current password</span>
          <input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
        </label> : null}
        {error.card === "signin" ? <p className="workbench-error" role="alert">{error.text}</p> : null}
        <div className="settings-card-actions">
          <button className="primary-button" disabled={Boolean(busy)} aria-busy={busy === "signin"}>{busy === "signin" ? <LoadingSpinner size={18} /> : null} Save sign-in</button>
        </div>
      </form>
    </SettingsCard>}
    <SettingsCard title="This device">
      <p className="settings-card-lead">{household
        ? "This is a shared device: switching asks who is reading."
        : "Signed in on this device as you."}</p>
      <div className="settings-card-actions">
        {household ? <button type="button" className="secondary-button" onClick={() => header?.profile?.onPicker()}><UserCircle size={18} /> Switch profile</button> : null}
        {authStatus?.method === "forms" ? <button type="button" className="secondary-button" onClick={() => onSignOut({ forgetDevice: true })}>
          <SignOut size={18} /> {household ? "Sign out and forget this device" : "Sign out"}
        </button> : null}
      </div>
    </SettingsCard>
  </>;
}

function SettingsView({ catalog, backendStatus, logicalSeriesCount, onNavigate, onAuthChanged, onSignOut, section, onSectionChange, health, onScanLibrary, scanState, scanProgress, authStatus }) {
  const viewer = useViewer();
  const admin = isAdmin(viewer);
  const sections = settingsSectionsFor(viewer);
  const settingsLast = lastAnswer("/api/v1/settings");
  const [collectedEditions, setCollectedEditions] = useState(Boolean(settingsLast?.collectedEditionsEnabled));
  const [savingCollectedEditions, setSavingCollectedEditions] = useState(false);
  const [language, setLanguage] = useState(settingsLast?.preferredLanguage ?? "en");
  const [savingLanguage, setSavingLanguage] = useState(false);
  const [autoScan, setAutoScan] = useState(settingsLast?.autoScanEnabled ?? true);
  const [autoScanInterval, setAutoScanInterval] = useState(Number(settingsLast?.autoScanIntervalMinutes) || 60);
  const [savingAutoScan, setSavingAutoScan] = useState(false);
  const [everyPage, setEveryPage] = useState(settingsLast?.visionReadsEveryPage ?? true);
  const [forReaders, setForReaders] = useState(Boolean(settingsLast?.visionForReaders));
  const [savingEveryPage, setSavingEveryPage] = useState(false);
  const [providers, setProviders] = useState(() => lastAnswer("/api/v1/providers")?.providers ?? []);
  const [providerError, setProviderError] = useState("");
  const [editingProvider, setEditingProvider] = useState(null);
  const [services, setServices] = useState(() => lastAnswer("/api/v1/acquisition-services")?.services ?? []);
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
      setEveryPage(result?.visionReadsEveryPage ?? true);
      setForReaders(Boolean(result?.visionForReaders));
    } catch {
      /* settings fall back to defaults */
    }
  }
  async function toggleForReaders(next) {
    setSavingEveryPage(true);
    setForReaders(next);
    try {
      const result = await apiRequest("/api/v1/settings", {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ visionForReaders: next }),
      });
      setForReaders(Boolean(result?.visionForReaders));
    } catch {
      setForReaders(!next);
    } finally {
      setSavingEveryPage(false);
    }
  }
  async function toggleEveryPage(next) {
    setSavingEveryPage(true);
    setEveryPage(next);
    try {
      const result = await apiRequest("/api/v1/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ visionReadsEveryPage: next }),
      });
      setEveryPage(Boolean(result?.visionReadsEveryPage));
    } catch {
      setEveryPage(!next);
    } finally {
      setSavingEveryPage(false);
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
    } catch {
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
    } catch {
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
  // The library's settings are the admin's; a reader's page asks for none of them.
  useEffect(() => { if (admin) { loadProviders(); loadServices(); loadAppSettings(); } }, [admin]);
  const roots = catalog?.roots || [];
  // A scan started here, from the rail or folders page, or in the background.
  const scanning = scanState === "scanning" || Boolean(catalog?.activeScan);
  // Above 640px, the sections are listed beside the one open, and one is
  // always open; on a phone, the list is a page of its own and each section
  // opens over it, as iPhone Settings does.
  const phone = usePhoneWidth();
  const current = section || (phone ? "" : admin ? "health" : "profile");
  const open = sections.find((item) => item.id === current);
  const needAttention = Number(catalog?.stats?.needAttention ?? 0);
  const modals = <>
    {editingProvider ? <ProviderSettingsModal provider={editingProvider} onClose={() => setEditingProvider(null)} onSaved={async () => { await loadProviders(); setEditingProvider(null); }} /> : null}
    {editingService ? <AcquisitionServiceSettingsModal service={editingService} onClose={() => setEditingService(null)} onSaved={async () => { await loadServices(); setEditingService(null); }} /> : null}
  </>;
  if (!open) {
    return <><PageHeader title="Settings" />
      <SettingsIndex onOpen={onSectionChange} counts={{ health: needAttention }} sections={sections} />
      {modals}
    </>;
  }
  const languageOptions = [["en", "English"], ["fr", "French"], ["es", "Spanish"], ["de", "German"], ["it", "Italian"], ["pt", "Portuguese"], ["ru", "Russian"], ["ja", "Japanese"], ["ko", "Korean"], ["zh", "Chinese"], ["pl", "Polish"], ["nl", "Dutch"], ["", "No preference"]];
  return <>
    <PageHeader title={phone ? open.label : "Settings"}
      leading={phone ? <button type="button" className="glass-button glass-button--icon" onClick={() => onSectionChange("")} aria-label="Back to Settings"><ArrowLeft size={20} /></button> : null} />
    <div className={`settings-shell${current === "health" ? " settings-shell--wide" : ""}`}>
      {/* The column runs the panel's full height, for its rule; the list in
          it stays in view as a long section scrolls. */}
      {phone ? null : <nav className="settings-nav" aria-label="Settings sections"><div>
        {sections.map(({ id, label }) => <button type="button" key={id}
          className={id === current ? "active" : ""} aria-current={id === current ? "page" : undefined}
          onClick={() => onSectionChange(id)}>
          <span>{label}</span>{id === "health" && needAttention ? <b>{needAttention}</b> : null}
        </button>)}
      </div></nav>}
      <div className="settings-pane">
        <header className="settings-pane-header">
          {phone ? null : <h2>{open.label}</h2>}
          <p>{open.detail}</p>
        </header>
        {current === "profile" ? <YourProfileSettings authStatus={authStatus} onSignOut={onSignOut} catalog={catalog} onViewerChanged={onAuthChanged} /> : null}
        {current === "profiles" ? <ProfilesSettings catalog={catalog} /> : null}
        {current === "health" ? <MetadataView {...health} /> : null}
        {current === "library" ? catalogPending(catalog, backendStatus)
          ? <CatalogLoading title="Loading your library folders…" detail="Checking which folders Flipparr already scans." />
          : <>
            <SettingsCard title="Folders" className="settings-library-folders"
              action={<button type="button" className="secondary-button" onClick={() => onNavigate("import")}><FolderOpen size={18} /> Manage folders</button>}>
              <p className="settings-card-lead">{roots.length ? `${roots.length} folder${roots.length === 1 ? "" : "s"} scanned for comics.` : "No library folders are configured yet."}</p>
              {roots.length ? <p className="settings-library-size"><strong>{Number(catalog?.stats?.files ?? 0).toLocaleString()}</strong> files · <strong>{Number(logicalSeriesCount ?? 0).toLocaleString()}</strong> series</p> : null}
              {roots.length ? <div className="settings-root-list">{roots.map((root) => <span key={root.id}><FolderOpen size={17} /><strong>{root.path}</strong><small>{root.recursive ? "Includes subfolders" : "Top level only"}</small></span>)}</div> : null}
              <ScanProgress scanState={scanState} scanProgress={scanProgress} />
              {/* The rail's scan button is hidden on a phone; this is the one there. */}
              {roots.length ? <div className="settings-card-actions">
                <button type="button" className={`primary-button ${scanning ? "loading" : ""}`} onClick={onScanLibrary} disabled={scanning} aria-busy={scanning}>{scanning ? <LoadingSpinner size={18} /> : <ArrowsClockwise size={18} />}{scanning ? "Scanning…" : "Scan library"}</button>
                <small>{scanning ? "Scanning now" : catalog?.lastScan?.iso ? `Last scanned ${catalog.lastScan.date} at ${catalog.lastScan.time}` : "Not scanned yet"}</small>
              </div> : null}
            </SettingsCard>
            {roots.length ? <SettingsCard title="Automatic scans">
              <Toggle checked={autoScan} onChange={savingAutoScan ? () => {} : (next) => saveAutoScan({ autoScanEnabled: next })} title="Scan automatically" description="Checks your library folders in the background, so comics added outside Flipparr appear without a manual scan." />
              {autoScan ? <div className="form-field settings-language settings-scan-interval"><span>How often</span><GlassSelect label="How often" value={autoScanInterval} disabled={savingAutoScan}
                onChange={(next) => saveAutoScan({ autoScanIntervalMinutes: Number(next) })}
                options={[{ value: 15, label: "Every 15 minutes" }, { value: 60, label: "Every hour" }, { value: 360, label: "Every 6 hours" }, { value: 1440, label: "Once a day" }]} /></div> : null}
            </SettingsCard> : null}
          </> : null}
        {current === "matching" ? <>
          <SettingsCard title="How matches are accepted">
            <p className="settings-card-lead">Matches are accepted automatically when the evidence is strong. Flipparr scores every match from corroborating and conflicting evidence (filename, embedded metadata, provider agreement). Confident matches are applied without review; anything below that threshold, or with conflicting evidence, waits under Library health for you to confirm or fix.</p>
          </SettingsCard>
          <SettingsCard title="Formats">
            <Toggle checked={collectedEditions} onChange={savingCollectedEditions ? () => {} : toggleCollectedEditions} title="Collected editions (trades, hardcovers, omnibuses)" description="Off by default. Turn on to browse and manage collected editions alongside Issues. Their metadata and file availability are less complete than Issues, and they are never used to fulfill Issue ownership or acquisition." />
          </SettingsCard>
        </> : null}
        {current === "security" ? <SecuritySettings onChanged={onAuthChanged} onSignOut={onSignOut} /> : null}
        {current === "acquisition" ? <>
          <SettingsCard title="Services" className="metadata-source-settings">
            <p className="settings-card-lead">Connect Prowlarr to find releases and SABnzbd to download the one you choose.</p>
            {services.map((service) => <AcquisitionService service={service} onConfigure={() => setEditingService(service)} key={service.id} />)}
            {serviceError ? <p className="workbench-error" role="alert">{serviceError}</p> : null}
          </SettingsCard>
          <SettingsCard title="Language">
            <div className="form-field settings-language"><span>Language wanted</span><GlassSelect label="Language wanted" value={language} disabled={savingLanguage} onChange={changeLanguage}
              options={languageOptions.map(([value, label]) => ({ value, label }))} /><small>A release that says it is another language is never grabbed, and one that says so only once downloaded is refused instead of filed under the issue it claims to be. Releases that say nothing are judged on the rest of the evidence.</small></div>
          </SettingsCard>
        </> : null}
        {current === "reader" ? <>
          <SettingsCard title="Panel view">
            <p className="settings-card-lead">Reads a page one panel at a time, zoomed to fit, in reading order. Turn it on from the reader&rsquo;s settings or with the P key; the choice follows your profile to any device. The rest of the page dims around the panel being read, which the same settings can turn off.{admin ? <> Flipparr finds the panels itself, and a page it gets wrong can be fixed by hand from the same settings &mdash; drawn, moved and numbered over the page &mdash; and stays as you left it.</> : null}</p>
          </SettingsCard>
          {/* Claude and ChatGPT are filed here, not under Metadata sources:
              they contribute nothing to what a comic is, only to how a hard
              page is read. They spend the admin's key, so they are the admin's. */}
          {admin ? <><SettingsCard title="Help with hard pages" className="metadata-source-settings">
            <p className="settings-card-lead">Optional. With an API key, one of these is asked about the pages you read in panel view &mdash; each page once, sent as an image &mdash; and the page itself corrects the answer&rsquo;s edges. Nothing is sent while they are off.</p>
            {providers.filter((provider) => provider.kind === "reading").map((provider) => <Provider provider={provider} onConfigure={() => setEditingProvider(provider)} key={provider.id} />)}
            {providerError ? <p className="workbench-error" role="alert">{providerError}</p> : null}
            <Toggle checked={forReaders} onChange={savingEveryPage ? () => {} : toggleForReaders} title="Readers' pages too"
              description="On, pages other profiles open are asked about as well, on your key. Off, they read what Flipparr found itself and every page you have already had read or fixed." />
            <Toggle checked={everyPage} onChange={savingEveryPage ? () => {} : toggleEveryPage} title="Ask about every page" description="On, the model reads every page you open in panel view and its answer replaces what Flipparr found itself, which can be wrong while sure. Off, it is asked only about the pages Flipparr could not read. About half a cent a page with Sonnet." />
            <p className="settings-card-note">Measured on real pages, September 2026: Claude (Sonnet 5) read every layout tried, the hardest to within a few percent. ChatGPT (gpt-4.1-mini) is cheaper and cautious &mdash; it says &ldquo;no panels&rdquo; when unsure and merges panels rather than inventing them. Either way, Flipparr fits a model&rsquo;s boxes to the gutters it finds itself and refuses boxes that cross the art.</p>
          </SettingsCard>
          <aside className="provider-policy-note"><ShieldCheck size={19} weight="fill" /><span><strong>Your API credentials stay on this device</strong><small>Keys are hidden after saving and sent only to the service you configure.</small></span></aside></> : null}
        </> : null}
        {current === "metadata" ? <>
          <SettingsCard title="Sources with an account" className="metadata-source-settings">
            <p className="settings-card-lead">Built-in sources work immediately. Add API credentials for more issue titles, dates, covers, and matches.</p>
            {providers.filter((provider) => !provider.builtIn && provider.kind !== "reading").map((provider) => <Provider provider={provider} onConfigure={() => setEditingProvider(provider)} key={provider.id} />)}
            <BuiltInSources providers={providers} />
            {providerError ? <p className="workbench-error" role="alert">{providerError}</p> : null}
          </SettingsCard>
          <aside className="provider-policy-note"><ShieldCheck size={19} weight="fill" /><span><strong>Your API credentials stay on this device</strong><small>Keys are hidden after saving and sent only to the service you configure.</small></span></aside>
        </> : null}
      </div>
    </div>
    {modals}
  </>;
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
  return <>
    <SettingsCard title="Sign-in"
      action={on && onSignOut ? <button type="button" className="secondary-button" onClick={onSignOut}><SignOut size={17} /> Sign out</button> : null}>
    <p className="settings-card-lead">This app stores your metadata and download-client API keys and can start downloads, so require a sign-in if anything other than you can reach it.</p>
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
      title="Treat local addresses as a shared device"
      description="Off by default. On, a device on the local network is a shared one: with one profile it opens as that profile without signing in; with readers it asks who is reading, and the admin needs their PIN or password. Anything reaching this app through a tunnel, reverse proxy or container bridge arrives from a private address and would be let straight in — so turn this on only if you know the traffic is genuinely local, and set FLIPPARR_TRUSTED_PROXIES when a proxy is in front."
    />
    </SettingsCard>
    <SettingsCard title="Credentials">
    <div className="auth-credentials">
      <div className="field-group">
      <label className="form-field"><span>Username</span>
        <input value={username} autoComplete="username" onChange={(event) => setUsername(event.target.value)} />
      </label>
      <label className="form-field"><span>{config.configured ? "New password" : "Password"}</span>
        <input type="password" value={password} autoComplete="new-password" placeholder={config.configured ? "Leave blank to keep it" : "At least 8 characters"} onChange={(event) => setPassword(event.target.value)} />
      </label>
      </div>
      <button className="secondary-button" disabled={busy || !username || (!config.configured && !password)}
        onClick={() => save({ method: config.method, username, ...(password ? { password } : {}) })} aria-busy={busy}>
        {busy ? <LoadingSpinner size={17} /> : <ShieldCheck size={17} />} Save credentials
      </button>
      {error ? <p className="workbench-error" role="alert">{error}</p> : null}
      {saved ? <small className="auth-saved">{saved}</small> : null}
    </div>
    </SettingsCard>
    {on ? <SharedDevicesCard /> : null}
    <aside className="provider-policy-note"><ShieldCheck size={19} weight="fill" /><span><strong>Your password is stored as a scrypt hash</strong><small>It is never returned by the API, and signing in sets an HttpOnly cookie rather than exposing a token to page scripts.</small></span></aside>
  </>;
}

/**
 * Forgetting every shared device: for a tablet that is lost or given away.
 * Each needs the admin's password again before it shows the picker, and
 * every profile opened on one is signed out. This device, and a reader's
 * sign-in on their own phone, are kept.
 */
function SharedDevicesCard() {
  const [state, setState] = useState("idle");
  const [error, setError] = useState("");
  async function forget() {
    setState("busy");
    setError("");
    try {
      await apiRequest("/api/v1/devices/forget", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      setState("done");
    } catch (err) {
      setError(err.message);
      setState("idle");
    }
  }
  return <SettingsCard title="Shared devices">
    <p className="settings-card-lead">A device you sign in on with your password becomes a shared one: it asks who is reading. If one is lost or given away, forget them all. Each will need your password again, and every profile open on one is signed out. This device stays as it is, and so do readers signed in on their own phones.</p>
    <div className="settings-card-actions">
      {state === "confirm"
        ? <>
          <button type="button" className="danger-button" onClick={forget}>Forget them</button>
          <button type="button" className="secondary-button" onClick={() => setState("idle")}>Keep them</button>
        </>
        : <button type="button" className="secondary-button" onClick={() => setState("confirm")} disabled={state === "busy"} aria-busy={state === "busy"}>
          {state === "busy" ? <LoadingSpinner size={17} /> : <DeviceMobile size={17} />} Forget other shared devices
        </button>}
      {state === "done" ? <small role="status">Forgotten. Other devices need your password again.</small> : null}
    </div>
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
  </SettingsCard>;
}

function Toggle({ checked, onChange, title, description, disabled = false }) {
  return <label className={`toggle-row${disabled ? " toggle-row--off" : ""}`}><span><strong>{title}</strong>{description ? <small>{description}</small> : null}</span><input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
}

/** A switch in a card's header, for the setting the card is about: the rest of the card is what it opens. */
function HeaderToggle({ checked, onChange, label }) {
  return <label className="toggle-row toggle-row--header"><span className="sr-only">{label}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
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
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal provider-settings-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="acquisition-service-title" onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close acquisition service settings" /><span className="eyebrow">{service.kind}</span><h2 id="acquisition-service-title">Connect {service.name}</h2><p className="workbench-intro">{service.description}</p><form onSubmit={save}><label className="form-field"><span>Server URL</span><input value={url} onChange={(event) => setUrl(event.target.value)} placeholder={service.id === "prowlarr" ? "http://nas:9696" : "http://nas:8080"} required /></label><label className="form-field"><span>API key</span><input type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={service.configured ? "Saved · type to replace" : `Enter your ${service.name} API key…`} /></label>{service.id === "sabnzbd" ? <label className="form-field"><span>SABnzbd category</span><input value={category} onChange={(event) => setCategory(event.target.value)} placeholder="comics" required /><small>Flipparr will use this category to identify and monitor its downloads.</small></label> : null}<Toggle checked={enabled} onChange={setEnabled} title={`Use ${service.name}`} description={service.id === "prowlarr" ? "Search configured Usenet indexers for wanted comics." : "Send selected NZBs to SABnzbd and monitor their progress."} />{result ? <p className="provider-test-result"><CheckCircle size={17} weight="fill" /> {result}</p> : null}{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="provider-modal-actions"><button type="button" className="secondary-button" onClick={test} disabled={Boolean(busy) || !url.trim() || (!apiKey && !service.configured)}>{busy === "test" ? <><LoadingSpinner size={17} /> Testing…</> : "Test"}</button><span />{service.configured ? <button type="button" className="danger-button" onClick={disconnect} disabled={Boolean(busy)}>Disconnect</button> : null}<button className="primary-button" disabled={Boolean(busy) || !url.trim() || (!apiKey && !service.configured)}>{busy === "save" ? <><LoadingSpinner size={17} /> Saving…</> : "Save"}</button></div><small className="provider-credential-help">The API key is stored locally and is never returned to the browser after saving.</small></form></section></div>;
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
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal provider-settings-modal" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="provider-settings-title" onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close provider settings" /><span className="eyebrow">Metadata provider</span><h2 id="provider-settings-title">Connect {provider.name}</h2><p className="workbench-intro">{provider.description}</p><form onSubmit={save}><label className="form-field"><span>{label}</span><input type="password" autoComplete="off" value={credential} onChange={(event) => setCredential(event.target.value)} placeholder={provider.configured ? "Saved · type to replace" : `Enter your ${provider.name} ${label.toLowerCase()}…`} /></label><small className="provider-credential-help">{provider.credentialHelp || "Your key is stored locally and is never returned to the browser after saving."}{provider.credentialUrl ? <> <a className="provider-credential-link" href={provider.credentialUrl} target="_blank" rel="noreferrer noopener">Get a key<ArrowUpRight size={13} weight="bold" /></a></> : null}</small><Toggle checked={enabled} onChange={setEnabled} title={`Use ${provider.name} for enrichment`} description="Fill missing fields automatically while preserving locked local corrections and higher-priority source data." /><div className="form-field provider-priority-field"><span>Provider priority</span><GlassSelect label="Provider priority" value={priority} onChange={(next) => setPriority(Number(next))} options={[{ value: 15, label: "Before other optional providers" }, { value: 20, label: "Normal priority" }, { value: 30, label: "Fallback priority" }]} /><small>Built-in GCD structure remains first. Optional providers fill fields that are still missing.</small></div>{result ? <p className="provider-test-result"><CheckCircle size={17} weight="fill" /> {result}</p> : null}{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="provider-modal-actions"><button type="button" className="secondary-button" onClick={test} disabled={Boolean(busy) || (!credential && !provider.configured)}>{busy === "test" ? <><LoadingSpinner size={17} /> Testing…</> : "Test"}</button><span />{provider.configured ? <button type="button" className="danger-button" onClick={removeCredentials} disabled={Boolean(busy)}>Remove</button> : null}<button className="primary-button" disabled={Boolean(busy) || (!credential && !provider.configured)}>{busy === "save" ? <><LoadingSpinner size={17} /> Saving…</> : "Save"}</button></div></form></section></div>;
}

/**
 * What an issue's Read control says, from the file behind it.
 *
 * Simpler than a run's: an issue is one comic, so it is unread, part-read or
 * finished. Nothing is offered for an issue with no readable file -- missing,
 * upcoming, a PDF, or owned only inside a volume, which the row already says.
 */
function issueReadState(issue, readingFiles) {
  const file = issue?.fileId ? readingFiles?.[String(issue.fileId)] : null;
  if (!file?.readable) return null;
  // The same rule the server's resolver uses for a run: a record that is not
  // finished is a place to continue from, whatever page it is on. Two rules
  // for one question is exactly what the resolver exists to prevent.
  const place = file.stale ? null : file;
  if (!place?.pageCount) return { label: "Read", fraction: 0, detail: "", finished: false };
  if (place.finishedAt) return { label: "Read again", fraction: 0, detail: "", finished: true };
  return {
    label: "Continue", fraction: Math.min(1, (place.page + 1) / place.pageCount),
    detail: `page ${place.page + 1} of ${place.pageCount}`, finished: false,
  };
}

// A small menu of actions on one thing, drawn over everything from its
// button's position (as GlassSelect's list is), right edge under the button's.
// It joins the dialog stack, so Escape closes it and not what is under it.
function ActionMenu({ anchor, label, items, onClose }) {
  const menuRef = useDialog(onClose);
  const [place, setPlace] = useState(null);
  useLayoutEffect(() => {
    const button = anchor.current?.getBoundingClientRect();
    const menu = menuRef.current;
    if (!button || !menu) return;
    const gap = 6;
    const margin = 12;
    const width = Math.min(240, window.innerWidth - margin * 2);
    const below = window.innerHeight - button.bottom - gap - margin;
    const up = menu.scrollHeight > below && button.top - gap - margin > below;
    setPlace({
      width, left: Math.min(Math.max(margin, button.right - width), window.innerWidth - margin - width),
      ...(up ? { bottom: window.innerHeight - button.top + gap } : { top: button.bottom + gap }),
      transformOrigin: up ? "bottom right" : "top right",
    });
  }, []);
  useEffect(() => {
    function outside(event) {
      if (menuRef.current?.contains(event.target) || anchor.current?.contains(event.target)) return;
      onClose();
    }
    document.addEventListener("pointerdown", outside, true);
    window.addEventListener("scroll", onClose, true);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("scroll", onClose, true);
    };
  }, [onClose]);
  return createPortal(<div className="glass-menu glass-select-menu action-menu" ref={menuRef} role="menu" aria-label={label}
    style={place ? { ...place } : { visibility: "hidden", top: 0, left: 0 }} onMouseDown={(event) => event.stopPropagation()}>
    {items.map((item) => <button type="button" role="menuitem" key={item.key} onClick={() => { onClose(); item.onSelect(); }}>
      <span className="sort-menu-check" aria-hidden="true">{item.icon}</span>
      <span>{item.label}</span>
    </button>)}
  </div>, document.body);
}

// What can be done to one issue, as every reader offers it: a menu from a
// "..." (shown on hover on a desktop, always to a finger) or a long press on
// the tile -- never a tap on the cover, which opens the comic. Read or
// continue it, mark it read or unread for this profile, and, for the admin,
// edit its metadata.
function IssueMenu({ issue, medium, read, onRead, onMark, onEditIssue, className, longPress = false }) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef(null);
  const close = useCallback(() => setOpen(false), []);
  useEffect(() => {
    if (!longPress) return undefined;
    const tile = buttonRef.current?.closest(".issue-tile");
    if (!tile) return undefined;
    let timer = 0;
    let held = false;
    const cancel = () => { window.clearTimeout(timer); timer = 0; };
    function onDown(event) {
      if (event.pointerType === "mouse" || event.button !== 0) return;
      held = false;
      timer = window.setTimeout(() => { held = true; setOpen(true); }, 500);
    }
    // A press that became the menu must not also open the comic on release.
    function onClick(event) {
      if (held) { event.preventDefault(); event.stopPropagation(); held = false; }
    }
    tile.addEventListener("pointerdown", onDown);
    tile.addEventListener("pointermove", cancel);
    tile.addEventListener("pointerup", cancel);
    tile.addEventListener("pointercancel", cancel);
    tile.addEventListener("click", onClick, true);
    return () => {
      cancel();
      tile.removeEventListener("pointerdown", onDown);
      tile.removeEventListener("pointermove", cancel);
      tile.removeEventListener("pointerup", cancel);
      tile.removeEventListener("pointercancel", cancel);
      tile.removeEventListener("click", onClick, true);
    };
  }, [longPress]);
  const label = issueLabel(issue.number, medium);
  const items = [];
  if (read && onRead) items.push({ key: "read", label: read.label, icon: <BookOpen size={16} weight="fill" />, onSelect: () => onRead({ id: issue.fileId }) });
  // Part-read, it can be finished or forgotten; read, forgotten; untouched,
  // finished. Someone else's place on your profile is cleared the same way.
  if (read && onMark && !read.finished) items.push({ key: "mark-read", label: "Mark as read", icon: <Check size={16} weight="bold" />, onSelect: () => onMark(issue, true) });
  if (read && onMark && (read.finished || read.fraction)) items.push({ key: "mark-unread", label: "Mark as unread", icon: <ArrowCounterClockwise size={16} />, onSelect: () => onMark(issue, false) });
  if (onEditIssue) items.push({ key: "edit", label: "Edit issue metadata", icon: <PencilSimple size={16} />, onSelect: () => onEditIssue(issue) });
  if (!items.length) return null;
  return <>
    <button type="button" ref={buttonRef} className={className} onClick={() => setOpen((value) => !value)}
      aria-haspopup="menu" aria-expanded={open} aria-label={`Options for ${label}`} title="Options">
      <DotsThree size={18} weight="bold" />
    </button>
    {open ? <ActionMenu anchor={buttonRef} label={`Options for ${label}`} items={items} onClose={close} /> : null}
  </>;
}

// What an issue's badge says about reading it, in the ownership badge's spot,
// once the issue is owned: "In progress" over its bar, "Read" when finished.
// Unread is plain, as in every reader looked at (Komga, Plex, Panels).
function readingBadge(read) {
  if (!read) return null;
  if (read.finished) return { label: "Read", tone: "reading-read" };
  if (read.fraction) return { label: "In progress", tone: "reading-progress" };
  return null;
}

// The run's own menu in its group header: mark every issue read, or unread.
function RunMenu({ runRead, runStarted, onMarkRun }) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef(null);
  const close = useCallback(() => setOpen(false), []);
  const items = [
    ...(runRead ? [] : [{ key: "mark-read", label: "Mark run as read", icon: <Check size={16} weight="bold" />, onSelect: () => onMarkRun(true) }]),
    ...(runRead || runStarted ? [{ key: "mark-unread", label: "Mark run as unread", icon: <ArrowCounterClockwise size={16} />, onSelect: () => onMarkRun(false) }] : []),
  ];
  return <>
    <button type="button" ref={buttonRef} className="issue-group-menu" onClick={() => setOpen((value) => !value)}
      aria-haspopup="menu" aria-expanded={open} aria-label="Options for this run" title="Options">
      <DotsThree size={18} weight="bold" />
    </button>
    {open ? <ActionMenu anchor={buttonRef} label="Options for this run" items={items} onClose={close} /> : null}
  </>;
}

function GroupedIssueInventory({ issues, onEditIssue, onRead, onRate, onMark, onMarkRun, runRead = false, runStarted = false, readingFiles, medium }) {
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
  // The Comics tools row's switch, so grid or list reads the same everywhere.
  const viewToggle = <GlassSegmented label="Choose issue view" value={view} onChange={setView} className="issue-view-toggle"
    items={[{ id: "grid", title: "Grid view", icon: <GridViewIcon /> }, { id: "list", title: "List view", icon: <ListViewIcon /> }]} />;
  return <div className={`grouped-issue-inventory ${view}`}>
    {groups.map((group, groupIndex) => {
    const owned = group.issues.filter((issue) => issue.ownership !== "unowned").length;
    return <section className="issue-run-group" key={group.key}>
      <header><div><span>{group.type === "specials" ? "Special / one-shot" : "Series run"}</span><h3>{group.title}</h3>{group.runTitle !== group.title || group.year ? <small>{[group.runTitle !== group.title ? group.runTitle : null, group.year].filter(Boolean).join(" · ")}</small> : null}</div><div className="issue-run-group-aside">{groupIndex === 0 ? viewToggle : null}<strong>{owned} of {group.issues.length} owned</strong>{groupIndex === 0 && onMarkRun && readingFiles ? <RunMenu runRead={runRead} runStarted={runStarted} onMarkRun={onMarkRun} /> : null}</div></header>
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
        const read = issueReadState(issue, readingFiles);
        if (view === "grid") {
          // A missing issue keeps its tile, dimmed, so a gap in a run is
          // visible rather than silently absent from the grid.
          return <article className={`issue-tile ${owned ? "" : "unowned"}`} key={issue.id}>
            {/* The badge sits over the cover, beside it rather than inside it,
                so a missing issue's dimmed cover does not dim its badge too. */}
            <span className="issue-tile-art">
              <span className="issue-tile-cover"><CoverArt id={`issue-${issue.id}`} title={`${issue.contextLabel || "Issue"} #${issue.number}`} cover={issue.fileCover || issue.cover} decorative placeholderSize={22} /></span>
              {stateLabel ? <span className={`ownership-source ${issue.ownership} ${issue.acquisitionState || ""}`}>{stateLabel}</span>
                : readingBadge(read) ? <span className={`ownership-source ${readingBadge(read).tone}`}>{readingBadge(read).label}</span> : null}
              {/* Inside the cover's box, not the tile's: anchored to the tile
                  it would sit under the issue title. */}
              {read && onRead ? <button type="button" className="issue-tile-read" onClick={() => onRead({ id: issue.fileId })}
                aria-label={`${read.label} ${issueLabel(issue.number, medium)}${read.detail ? `, ${read.detail}` : ""}`}
                title={read.label}><BookOpen size={14} weight="fill" /></button> : null}
              {read?.fraction ? <span className="read-progress-bar" aria-hidden="true"><i style={{ width: `${Math.round(read.fraction * 100)}%` }} /></span> : null}
              <IssueMenu issue={issue} medium={medium} read={read} onRead={onRead} onMark={onMark} onEditIssue={onEditIssue} className="issue-tile-menu" longPress />
            </span>
            {onEditIssue ? <button type="button" className="issue-tile-edit" onClick={() => onEditIssue(issue)} aria-label={`Edit metadata for ${issue.contextLabel || "issue"} issue ${issue.number}`} title="Edit issue metadata"><PencilSimple size={14} /></button> : null}
            <strong>{issueLabel(issue.number, medium)}{issue.metadataLocked ? <ShieldCheck className="issue-local-lock" size={12} weight="fill" aria-label="Local metadata correction locked" /> : null}</strong>
            {!genericTitle ? <small className="issue-tile-title">{issue.title}</small> : null}
          </article>;
        }
        return <article key={issue.id}><span className={`grouped-issue-cover ${issue.fileCover ? "from-file" : ""}`}><CoverArt id={`issue-${issue.id}`} title={`${issue.contextLabel || "Issue"} #${issue.number}`} cover={issue.fileCover || issue.cover} decorative placeholderSize={16} /></span><span className="grouped-issue-number">{issueLabel(issue.number, medium)}</span><div>{!genericTitle ? <strong>{issue.title}{issue.metadataLocked ? <ShieldCheck className="issue-local-lock" size={13} weight="fill" aria-label="Local metadata correction locked" /> : null}</strong> : null}<small>{releaseLabel}{read?.detail ? ` · ${read.detail}` : ""}</small></div><div className="issue-row-actions">{!stateLabel && readingBadge(read) ? <span className={`ownership-source ${readingBadge(read).tone}`}>{readingBadge(read).label}</span> : null}{onRate && issue.ownership !== "unowned" ? <StarRating rating={{ value: issue.yourRating || 0, source: issue.yourRating ? RATING_SOURCES.yours : RATING_SOURCES.none, count: 0 }} title={`${issue.contextLabel || "Issue"} ${issueLabel(issue.number, medium)}`} size={15} onRate={(value) => onRate(issue, value)} /> : null}{stateLabel ? <span className={`ownership-source ${issue.ownership} ${issue.acquisitionState || ""}`}>{stateLabel}</span> : null}{read && onRead ? <button type="button" className="issue-row-read" onClick={() => onRead({ id: issue.fileId })} aria-label={`${read.label} ${issueLabel(issue.number, medium)}${read.detail ? `, ${read.detail}` : ""}`} title={read.label}><BookOpen size={14} weight="fill" /></button> : null}{onEditIssue ? <button type="button" className="issue-row-edit" onClick={() => onEditIssue(issue)} aria-label={`Edit metadata for ${issue.contextLabel || "issue"} issue ${issue.number}`} title="Edit issue metadata"><PencilSimple size={14} /></button> : null}<IssueMenu issue={issue} medium={medium} read={read} onRead={onRead} onMark={onMark} onEditIssue={onEditIssue} className="issue-row-menu" /></div></article>;
      })}</div>
    </section>;
  })}</div>;
}

function VolumeInventory({ editions }) {
  if (!editions?.length) return <div className="drawer-empty"><Books size={26} weight="duotone" /><strong>No volumes linked yet</strong><span>Collected files will appear here after inventory.</span></div>;
  return <div className="edition-inventory">{editions.map((edition) => { const title = edition.subtitle ? `${edition.title}: ${edition.subtitle}` : edition.title; return <article key={edition.logicalVolumeKey || edition.id}><span className="edition-cover"><CoverArt id={`edition-${edition.id}`} title={title} cover={edition.cover} decorative placeholderSize={20} /></span><div className="edition-card-content"><header><div><strong>{title}</strong><small>{[edition.publisher, edition.publicationYear, edition.format].filter(Boolean).join(" · ") || "Volume details incomplete"}</small></div><span><b>{editionKindLabel(edition.editionKind)}{edition.volume ? ` · Vol. ${edition.volume}` : ""}</b><small>{edition.copyCount > 1 ? `${edition.copyCount} library files` : edition.source || "Local metadata"}</small>{edition.coverageOverrideCount ? <small>{edition.coverageOverrideCount} local contents correction{edition.coverageOverrideCount === 1 ? "" : "s"}</small> : null}</span></header>{edition.isbns?.length ? <p><b>ISBN</b> {edition.isbns.join(", ")}</p> : null}<div className="coverage-groups">{edition.coverageGroups?.length ? edition.coverageGroups.map((coverage) => <div className={coverage.resolved ? "resolved" : "unresolved"} key={`${coverage.seriesLabel}-${coverage.issueLabel}-${coverage.source}`}><span><strong>{coverage.seriesLabel} #{coverage.issueLabel}</strong><small>{coverage.resolved ? "Counts toward canonical ownership" : "Visible claim; canonical numbering unresolved"}</small></span><b>{coverage.confidence}</b><p>{coverage.source}{coverage.evidence ? ` · ${volumeTerminology(coverage.evidence)}` : ""}</p></div>) : <div className="no-coverage"><WarningCircle size={18} /><span><strong>Contents not established</strong><small>{volumeTerminology(edition.coverageStatus) || "No structured issue coverage was returned by the current sources."}</small></span></div>}</div></div></article>; })}</div>;
}

function FileActionButtons({ file, readable, onRead, onOpenWorkbench, onOpenCover, onOpenContents, onChangeRun, onReplace }) {
  const editionsOn = useCollectedEditions();
  // Editing a collected edition's issue contents is an edition-management
  // surface; the file itself stays visible and fixable either way.
  // Read comes first: it is the only thing here you would do for pleasure.
  // Whether it can be read is the server's answer, from the file's first
  // bytes -- the suffix lies in both directions. A .cb7 needs a tool the
  // container may not carry, and a zip named .bin opens perfectly.
  return <>{readable ? <button onClick={() => onRead(file)}><BookOpen size={14} /> Read</button>
    : <small className="file-not-readable">Not a readable archive</small>}{file.identityKind === "edition" && editionsOn ? <button onClick={() => onOpenContents(file)}><ListBullets size={14} /> Issues</button> : null}<button onClick={() => onReplace(file)}><CloudArrowDown size={14} /> Replace</button><button onClick={() => onChangeRun(file)}><Books size={14} /> Change run</button><button onClick={() => onOpenCover(file)}><BookOpen size={14} /> Cover</button><button onClick={() => onOpenWorkbench(file, "match")}><ArrowsClockwise size={14} /> Fix match</button><button onClick={() => onOpenWorkbench(file, "edit")}><PencilSimple size={14} /> Metadata</button></>;
}

function FileInventory({ files, readingFiles, onRead, onOpenWorkbench, onOpenCover, onOpenContents, onChangeRun, onReplace }) {
  if (!files?.length) return <div className="drawer-empty"><HardDrive size={26} weight="duotone" /><strong>No local files linked</strong></div>;
  return <div className="file-inventory">{files.map((file) => <article key={file.path}><HardDrive size={20} weight="duotone" /><div><strong>{file.filename}</strong><small>{file.identityKind === "issue" ? "Single issue" : editionKindLabel(file.editionKind)} · {(file.sizeBytes / 1024 / 1024).toFixed(1)} MB</small>{file.metadataLocked ? <small className="metadata-lock"><ShieldCheck size={13} weight="fill" /> Local corrections locked</small> : null}</div><span className="file-row-actions"><FileActionButtons file={file} readable={Boolean(readingFiles?.[String(file.id)]?.readable)} onRead={onRead} onOpenWorkbench={onOpenWorkbench} onOpenCover={onOpenCover} onOpenContents={onOpenContents} onChangeRun={onChangeRun} onReplace={onReplace} /></span><details className="file-actions-menu"><summary><DotsThree size={17} weight="bold" /> Actions <CaretDown size={13} /></summary><div><FileActionButtons file={file} readable={Boolean(readingFiles?.[String(file.id)]?.readable)} onRead={onRead} onOpenWorkbench={onOpenWorkbench} onOpenCover={onOpenCover} onOpenContents={onOpenContents} onChangeRun={onChangeRun} onReplace={onReplace} /></div></details></article>)}</div>;
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
    {family && availableRuns.length ? <section className="family-assignment"><h3>Add or move a run into this collection</h3><div><GlassSelect label="Run to add" placeholder="Choose a run…" value={candidateRun} onChange={setCandidateRun} className="glass-select--fill" options={availableRuns.map((run) => ({ value: run.id, label: `${run.title} (${run.year})${run.family ? ` · from ${run.family.name}` : ""}` }))} /><button disabled={busy || !candidateRun} onClick={() => assign(candidateRun, family.id)}><Plus size={17} /> Add run</button></div><small>Moving a run changes only its collection membership.</small></section> : null}
    {otherFamilies.length ? <section className="family-assignment"><h3>{family ? "Move this run to another collection" : "Add this run to an existing collection"}</h3><div><GlassSelect label="Collection" placeholder="Choose a collection…" value={targetFamily} onChange={setTargetFamily} className="glass-select--fill" options={otherFamilies.map((item) => ({ value: item.id, label: `${item.name} · ${item.runCount} run${item.runCount === 1 ? "" : "s"}` }))} /><button disabled={busy || !targetFamily} onClick={() => assign(series.id, targetFamily)}>{family ? "Move run" : "Join collection"}</button></div></section> : null}
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
    if (!node) return;
    // A row that bleeds to its container's edges pads its ends; a page is
    // the width between them, so it still lands on a card's edge.
    const style = getComputedStyle(node);
    const inner = node.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    node.scrollBy({ left: direction * inner, behavior: "smooth" });
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

// What a run's Edit panel can change, in the order it lists them. Assets
// first: they are what people come here to change, and they are the two the
// drawer's header used to spend its only button on.
const EDIT_SECTIONS = [
  ["cover", "Series cover", "Use another issue's art, a provider's cover, or your own image."],
  ["background", "Header background", "The page behind this drawer's header."],
  ["titles", "Alternate titles", "Other names this run's comics are filed under."],
];

/**
 * Edit, the way iOS Settings works: a list, then one section at a time, with
 * Back to where you came from.
 *
 * It is a panel inside the drawer rather than a dialog over it. That is what
 * keeps one scrim on screen -- tapping outside dismisses the drawer, as it
 * always did -- and it means the cover and background pickers are shown in
 * place rather than stacked as a second modal, which this app has never done
 * and whose layering is decided by render order rather than by design.
 *
 * What it holds is what a run can actually change. A run's title, publisher
 * and year come from the provider it is matched to and there is no route to
 * override them, so they are shown as facts with the one action that does
 * change them, rather than as fields that would quietly discard what you type.
 */
function SeriesEditPanel({
  series, section, onSection, shownBackdrop, medium, readingFiles, directionOverride, onSetDirection,
  cover, onSelectCover, onUploadCover, coverBusy, coverError,
  onChooseBackdrop, onAutomaticBackdrop, backdropBusy, backdropError,
  alternateTitles, aliasForm, onFixMatch, onSetFormat, onSetAgeRating,
}) {
  if (section === "cover") {
    return <div className="edit-section">
      <p className="edit-section-intro">Choose art from the comic, a metadata provider, or upload your own image. The comic file is not altered.</p>
      {cover
        ? <CoverPicker data={cover} busy={coverBusy} error={coverError} onSelect={onSelectCover} onUpload={onUploadCover} />
        : <div className="edit-section-status" role="status"><LoadingSpinner size={18} /> Looking for cover art…</div>}
    </div>;
  }
  if (section === "background") {
    return <div className="edit-section">
      <p className="edit-section-intro">Choose a page from {series.title} to show behind this header.</p>
      <BackdropPicker series={series} current={shownBackdrop} busy={backdropBusy} error={backdropError} readable={readingFiles}
        onChoose={onChooseBackdrop} onAutomatic={onAutomaticBackdrop} />
    </div>;
  }
  if (section === "titles") {
    return <div className="edit-section">
      <p className="edit-section-intro">Scans use these automatically. Add one only if a scan keeps missing a file.</p>
      {alternateTitles.length ? <div className="alias-list">{alternateTitles.map((item) => <span className={item.confirmed ? "confirmed" : ""} key={`${item.name}-${item.source}`}><strong>{item.name}</strong><small>{item.confirmed ? "Manually confirmed" : item.source}</small></span>)}</div> : null}
      {aliasForm}
    </div>;
  }
  return <div className="edit-index">
    {/* Facts, not fields. A run's identity is its provider match, and the
        honest way to offer a change is the match, not a text box that would
        be thrown away. */}
    <section className="edit-identity">
      <dl>
        <div><dt>Title</dt><dd>{series.title}</dd></div>
        {series.publisher ? <div><dt>Publisher</dt><dd>{series.publisher}</dd></div> : null}
        {series.year ? <div><dt>Year</dt><dd>{series.year}</dd></div> : null}
      </dl>
      <p>These come from the run this is matched to. Fix the match to change them.</p>
      <button type="button" className="ghost-button" onClick={() => onFixMatch(series)}><MagnifyingGlass size={16} /> Fix series match</button>
    </section>
    <div className="edit-rows">
      {EDIT_SECTIONS.map(([id, label, detail]) => <button type="button" className="edit-row" onClick={() => onSection(id)} key={id}>
        <span><strong>{label}</strong><small>{detail}</small></span>
        <CaretRight size={17} />
      </button>)}
    </div>
    <section className="advanced-card">
      <div><strong>{medium === "manga" ? "Filed as manga" : "Filed as a comic"}</strong><p>{medium === "manga" ? "Searched and filed by volume, in the Manga folder." : "Manga is searched and filed by volume, in the Manga folder."} Change it if the publisher misled Flipparr.</p></div>
      <button type="button" onClick={() => onSetFormat?.(series, medium === "manga" ? "comic" : "manga")}><Books size={16} /> {medium === "manga" ? "File as comic" : "File as manga"}</button>
    </section>
    {/* How it is filed and which way it turns are not the same question: an
        English edition of a manga is printed left to right, and no provider
        records that, so it is the reader's to say. */}
    <section className="advanced-card edit-direction">
      <div>
        <strong>Reading direction</strong>
        <p>{directionOverride
          ? `Set by hand: ${directionOverride === READING_DIRECTIONS.rtl ? "right to left" : "left to right"}.`
          : medium === "manga"
            ? "Right to left, because this is filed as manga."
            : "Left to right, because this is filed as a comic."}</p>
      </div>
      <GlassSegmented label="Choose reading direction" value={directionOverride || "auto"}
        onChange={(next) => onSetDirection?.(series, next === "auto" ? null : next)}
        className="direction-toggle"
        items={[
          { id: "auto", label: "Auto" },
          { id: READING_DIRECTIONS.ltr, title: "Left to right", icon: <ArrowRight size={16} /> },
          { id: READING_DIRECTIONS.rtl, title: "Right to left", icon: <ArrowLeft size={16} /> },
        ]} />
    </section>
    {/* Who may read it. What Metron or the cover said is kept; the admin's
        choice outranks it, and "Auto" goes back to it. */}
    <section className="advanced-card edit-rating">
      <div>
        <strong>Age rating</strong>
        <p>{ratingSource(series)}</p>
      </div>
      {/* A list, not a segmented control: five ratings do not fit across a phone. */}
      <GlassSelect label="Age rating" value={series.ageRatingOverride || "auto"}
        onChange={(next) => onSetAgeRating?.(series, next === "auto" ? null : next)}
        options={[{ value: "auto", label: series.ageRatingFound ? `Auto (${RATING_LABELS[series.ageRatingFound]})` : "Auto" },
          ...RATINGS.map((id) => ({ value: id, label: RATING_LABELS[id] }))]} />
    </section>
  </div>;
}

/**
 * Read, over a run's cover in the Comics grid.
 *
 * Icon only: a label does not fit a card at 320px, so the wording lives in
 * the accessible name. Nothing is drawn for a run with no reading history --
 * working out what is readable would mean opening every archive in the
 * library on a request the grid makes on every visit -- so an unopened run
 * gets a plain Read and resolves when it is clicked.
 */
function ReadRunOverlay({ run, reading, onRead }) {
  if (!onRead) return null;
  const place = reading?.[String(run.id)];
  const started = place?.state === "continue" && place.pageCount;
  // No progress hairline on a run's cover: a line across one issue's pages sat
  // above the bar that means how much of the run is owned, and read as the
  // same kind of thing. The place is still in the button's accessible name.
  // The drawer's verbs, in one word: Continue a comic mid-page, Begin the next
  // one when you are between issues, Restart a run read to its end. A run
  // never opened gets a plain Read and resolves when it is clicked.
  const verb = started ? "Continue" : place?.state === "next" ? "Begin" : place?.state === "finished" ? "Restart" : "Read";
  const label = started
    ? `Continue ${issueLabel(place.issueNumber, run.medium)} of ${run.title}, page ${place.page + 1} of ${place.pageCount}`
    : `${verb} ${run.title}`;
  // Only a comic mid-page is opened by file: between issues the latest file is
  // the one just finished, and the reader would reopen it from page one. The
  // run resolves to the next unread -- or, once every issue is read, to #1.
  // The marker says the same words an issue's badge does: "Read" once every
  // issue is, "In progress" on a run that has been started. A bare count of
  // what was left said nothing on its own (the owner, 2026-09-28).
  return <>
    {place?.state === "finished" ? <span className="series-card-mark done"><Check size={13} weight="bold" aria-hidden="true" /> Read</span>
      : place ? <span className="series-card-mark">In progress</span> : null}
    <button type="button" className="series-card-read" title={verb}
      onClick={() => onRead(started ? { id: place.fileId } : { runId: run.id })} aria-label={label}>
      <BookOpen size={16} weight="fill" />
      <b>{verb}</b>
    </button>
  </>;
}

/**
 * The run's one reading action, worded by what the server resolved.
 *
 * It renders nothing at all when there is nothing readable. A greyed-out Read
 * would say the library holds a comic it does not, and "why is this disabled"
 * is a worse question than "where is the button".
 */
function ReadRunButton({ reading, title, medium, onRead }) {
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
function StarRating({ rating, title, size = 18, onRate }) {
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
function ComicDrawerIssueCard({ issue, medium, onOpen, onRead, readingFiles }) {
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

function SeriesDrawer({ series, families, allSeries, parentCollection, dismissSignal, onBack, onClose, onRead, onMarkIssue, onMarkRun, readingVersion = 0,
  coverBusy = false, coverError = "", onSelectSeriesCover, onUploadSeriesCover,
  backdropBusy = false, backdropError = "", onSaveBackdrop, onSetDirection, onRate, onRateIssue, onRequest, onViewRequests, requestBusy, onAddAlias, onSyncIssues, onFindRun, onMergeRun, onRebuildRun, rebuilding = false, rebuildResult = "", onCreateFamily, onSetFamily, onOpenWorkbench, onOpenCover, onChangeSeriesCover, onFixSeriesMatch, onOpenContents, onChangeRun, onEditIssue, onReplace, onUnfollow, unfollowBusy = false, onSetFormat, onRemove, onOpenSeries, onChangeBackdrop, backdropVersion = 0, requested = false, onSetAgeRating }) {
  const admin = isAdmin(useViewer());
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
  // null when not editing, "" for the list of sections, else the section. It
  // is drawer state, so opening another comic leaves editing behind.
  const [edit, setEdit] = useState(null);
  const [coverOptions, setCoverOptions] = useState(null);
  const loadCoverOptions = useCallback(() => {
    if (!series?.id) return;
    apiRequest(`/api/v1/series/${series.id}/cover`)
      .then(setCoverOptions).catch(() => setCoverOptions(null));
  }, [series?.id]);
  useEffect(() => {
    if (edit !== "cover") return;
    setCoverOptions(null);
    loadCoverOptions();
  }, [edit, loadCoverOptions]);
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
  // What can be read here, and where each comic was left. Its own request
  // rather than the catalog's: reading changes on every page turn, and the
  // catalog is refetched on scans and downloads, so folding one into the
  // other guarantees a progress bar that lies.
  const [reading, setReading] = useState(null);
  useEffect(() => {
    if (!series?.id) return undefined;
    let live = true;
    setReading(null);
    apiRequest(`/api/v1/series/${series.id}/reading`)
      .then((data) => { if (live) setReading(data); })
      .catch(() => { if (live) setReading(null); });
    return () => { live = false; };
  }, [series?.id, readingVersion]);
  const readingFiles = useMemo(() => Object.fromEntries(
    [...(reading?.issues || []), ...(reading?.volumes || [])].map((file) => [String(file.id), file]),
  ), [reading]);
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
  const toned = toneProps(useArtTone(coverArt));
  const related = useMemo(() => (series ? relatedRuns(series, allSeries || []) : null), [series, allSeries]);
  const creators = useMemo(() => orderedCreators(series?.creators), [series?.creators]);
  // `edit` is a dependency because the strip is unmounted while editing: the
  // pill has to be measured again against the node that comes back.
  const [tabsRef, tabGlass] = useGlassIndicator("button.active", [tab, series?.id, editionsOn, edit]);
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
  // Starting over is offered once the run has been started at all; before
  // that there is nothing to go back to.
  const restartFile = reading?.resume
    && [READING_STATES.continue, READING_STATES.next, READING_STATES.finished].includes(reading.resume.state)
    ? reading.issues?.find((file) => file.readable) || null
    : null;
  // Marking the whole run: read when any of it is not, unread once all of it is.
  const readableFiles = [...(reading?.issues || []), ...(reading?.volumes || [])].filter((file) => file.readable);
  const runAllRead = readableFiles.length > 0 && readableFiles.every((file) => file.finishedAt && !file.stale);
  // The run's own status in the hero, in the issue badge's words: "Read", or
  // "In progress" with how many are read, once any of it has been opened.
  const runReadCount = readableFiles.filter((file) => file.finishedAt && !file.stale).length;
  const runStarted = readableFiles.some((file) => file.pageCount && !file.stale);
  const runReadingBadge = runAllRead ? { tone: "green", text: "Read" }
    : runStarted ? { tone: "violet", text: `In progress · ${runReadCount} of ${readableFiles.length} read` } : null;
  const wantedIssueCount = Math.max(0, Number(series.releaseSummary?.releasedMissing ?? series.unowned ?? Math.max(0, (series.total || 0) - (series.owned || 0))));
  // Volumes and Collection belong to collected-edition support; the counts
  // live on the tabs, so the header does not repeat them.
  const tabs = [
    ["overview", "Overview"],
    ["issues", `Issues (${series.issues?.length ?? 0})`],
    ...(editionsOn ? [["editions", `Volumes (${series.editions?.length ?? 0})`]] : []),
    // Files, Collection and Advanced are where a run is repaired: the admin's.
    ...(admin ? [["files", `Files (${series.fileDetails?.length ?? 0})`]] : []),
    ...(editionsOn && admin ? [["family", "Collection"]] : []),
    ...(admin ? [["advanced", "Advanced"]] : []),
  ];
  const alternateTitles = (series.aliases || []).filter((item) => identityKey(item.name) !== identityKey(series.title));
  // A new tab's content slides in from the side it lies on, so moving right
  // reads as moving right. Nothing slides on the drawer's first render.
  const shownTab = useRef(tab);
  const tabMove = useRef("");
  if (shownTab.current !== tab) {
    const order = tabs.map(([id]) => id);
    tabMove.current = order.indexOf(tab) > order.indexOf(shownTab.current) ? "next" : "back";
    shownTab.current = tab;
  }
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}><aside className={`series-drawer comic-drawer${toned.className}${edit !== null ? " comic-drawer--editing" : ""} ${closing ? "closing" : ""}`} style={toned.style} ref={dialogRef} data-in-address="" role="dialog" aria-modal="true" aria-labelledby="series-drawer-title" onMouseDown={(event) => event.stopPropagation()}>
    <DrawerTopBar
      title={edit ? (EDIT_SECTIONS.find(([id]) => id === edit)?.[1] || series.title) : series.title}
      onClose={requestClose}
      onBack={edit !== null ? () => setEdit(edit ? "" : null) : parentCollection ? onBack : undefined}
      stacked={edit !== null}
      closeLabel="Close series details">
      {edit === null && admin ? <>
        <button type="button" className={`glass-button glass-button--icon comic-drawer-follow-button${isFollowing ? " active" : ""}`}
          onClick={() => (isFollowing ? onUnfollow(series) : onRequest())}
          disabled={requestBusy || unfollowBusy} aria-pressed={isFollowing}
          aria-label={isFollowing ? `Stop following ${series.title}` : `Follow ${series.title}`}
          title={isFollowing ? "Following" : "Follow run"}>
          {requestBusy || unfollowBusy ? <LoadingSpinner size={18} /> : <FollowedIcon size={20} />}
          <b>{isFollowing ? "Unfollow" : "Follow"}</b>
        </button>
        <button type="button" className="glass-button glass-button--icon comic-drawer-edit-button" onClick={() => setEdit("")} aria-label={`Edit ${series.title}`} title="Edit"><PencilSimple size={20} /></button>
      </> : null}
      {/* A reader asks for a run to be followed; the admin decides. */}
      {edit === null && !admin && !isFollowing ? <button type="button"
        className={`glass-button glass-button--icon comic-drawer-follow-button${requested ? " active" : ""}`}
        onClick={() => onRequest()} disabled={requestBusy || requested}
        aria-label={requested ? `Following ${series.title} is requested` : `Request a follow of ${series.title}`}
        title={requested ? "Waiting for approval" : "Ask for this run to be followed: new issues as they come out"}>
        {requestBusy ? <LoadingSpinner size={18} /> : requested ? <Hourglass size={20} /> : <FollowedIcon size={20} />}
        <b>{requested ? "Follow requested" : "Request follow"}</b>
      </button> : null}
    </DrawerTopBar>
    <header className="comic-drawer-hero">
      {heroArt ? <><img className="comic-drawer-backdrop" src={heroArt} alt="" aria-hidden="true" key={heroArt} onError={pageArt ? () => setBackdropFailed(true) : undefined} /><img className="comic-drawer-backdrop blurred" src={heroArt} alt="" aria-hidden="true" key={`${heroArt}-blurred`} /></> : null}
      <span className="comic-drawer-scrim" aria-hidden="true" />
      {parentCollection ? <button type="button" className="drawer-back-link" onClick={onBack}><ArrowLeft size={17} /><span>Back to <strong>{parentCollection.name}</strong></span></button> : null}
      <div className="comic-drawer-identity">
        <div className="comic-drawer-cover">{onChangeSeriesCover && admin ? <button type="button" className="drawer-cover-button" onClick={() => onChangeSeriesCover(series)} aria-label={`Change the cover for ${series.title}`}><SeriesCover series={series} /><span className="drawer-cover-hint"><ImageSquare size={15} /> Change cover</span></button> : <SeriesCover series={series} />}</div>
        <div className="comic-drawer-copy">
          <div className="comic-drawer-titles">
            <h2 id="series-drawer-title">{series.title}</h2>
            <p>{[series.publisher, series.year].filter(Boolean).join(" • ")}</p>
          </div>
          <div className="comic-drawer-statuses"><PublicationStatus series={series} /><MonitoringStatus series={series} />{series.ageRating ? <StatusBadge tone={ratingTone(series.ageRating.replace("_", " "))}>Rated {RATING_LABELS[series.ageRating]}</StatusBadge> : null}{runReadingBadge ? <StatusBadge tone={runReadingBadge.tone}>{runReadingBadge.text}</StatusBadge> : null}<StarRating rating={runRating(series)} title={series.title} onRate={(value) => onRate?.(series, value)} />{editionsOn && series.family ? <button type="button" className="family-link-chip" onClick={() => setTab("family")}><Books size={14} /> {series.family.name}</button> : null}</div>
          <Ownership series={series} compact />
          {isFollowing && wantedIssueCount && admin ? <button type="button" className="comic-drawer-link" onClick={onViewRequests}>View {wantedIssueCount} wanted issue{wantedIssueCount === 1 ? "" : "s"}</button> : null}
        </div>
      </div>
    </header>
    {/* Reading and following, together, above the tabs and on every one of
        them. They are what this drawer is for; everything below is detail. */}
    {edit !== null ? <div className="comic-drawer-body comic-drawer-edit">
      <header className="edit-heading">
        <span className="eyebrow">Editing</span>
        <h3>{EDIT_SECTIONS.find(([id]) => id === edit)?.[1] || series.title}</h3>
      </header>
      <SeriesEditPanel
        series={series} section={edit} onSection={setEdit} shownBackdrop={shownBackdrop} medium={series.medium}
        readingFiles={readingFiles}
        cover={coverOptions} coverBusy={coverBusy} coverError={coverError}
        onSelectCover={(source, url, fileId) => onSelectSeriesCover?.(source, url, fileId, series.id).then(loadCoverOptions)}
        onUploadCover={(file) => onUploadSeriesCover?.(file, series.id).then(loadCoverOptions)}
        backdropBusy={backdropBusy} backdropError={backdropError}
        onChooseBackdrop={(fileId, page) => onSaveBackdrop?.({ fileId, page }, "Header background updated", series.id)}
        onAutomaticBackdrop={() => onSaveBackdrop?.({ source: "auto" }, "Automatic background restored", series.id)}
        alternateTitles={alternateTitles} onFixMatch={onFixSeriesMatch} onSetFormat={onSetFormat}
        directionOverride={series.readingDirection} onSetDirection={onSetDirection} onSetAgeRating={onSetAgeRating}
        aliasForm={<form className="alias-form" onSubmit={saveAlias}><label><span>Add a title alias</span><div><input value={alias} onChange={(event) => setAlias(event.target.value)} placeholder="Alternate series title…" /><button disabled={savingAlias || !alias.trim()} aria-busy={savingAlias}>{savingAlias ? <LoadingSpinner size={18} /> : <Plus size={18} />} Add</button></div></label>{aliasError ? <small className="form-error" role="alert">{aliasError}</small> : null}</form>}
      />
    </div> : <>
    <div className="comic-drawer-actions">
      <ReadRunButton reading={reading} title={series.title} medium={series.medium} onRead={onRead} />
    </div>
    <nav className="drawer-tabs comic-drawer-tabs" aria-label="Series details" ref={tabsRef}><span className="comic-drawer-tab-glass glass-indicator" aria-hidden="true" style={tabGlass || { opacity: 0 }} />{tabs.map(([id, label]) => <button type="button" className={tab === id ? "active" : ""} aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)} key={id}>{label}</button>)}</nav>
    <div className="comic-drawer-body" key={tab} data-tab-move={tabMove.current || undefined}>
      {tab === "overview" ? <>
        <RunSynopsis loading={synopsis.state === "loading"} text={synopsis.text} source={synopsis.source} sourcePrefix="Source:" key={series.id} />
        {series.issues?.length ? <ComicDrawerRow title="Issues" count={series.issues.length}>{series.issues.map((issue) => <ComicDrawerIssueCard issue={issue} medium={series.medium} onOpen={() => setTab("issues")} onRead={onRead} readingFiles={readingFiles} key={issue.id || issue.number} />)}</ComicDrawerRow> : null}
        {creators.length ? <ComicDrawerCreators creators={creators} key={`creators-${series.id}`} /> : null}
        {related?.moreBy ? <ComicDrawerRow title={`More From ${related.moreBy.name}`} count={related.moreBy.runs.length}>{related.moreBy.runs.map((run) => <ComicDrawerRunCard run={run} onOpen={(item) => onOpenSeries?.(item)} key={run.id} />)}</ComicDrawerRow> : null}
        {related?.publisher ? <ComicDrawerRow title={`More From ${related.publisher.name}`} count={related.publisher.runs.length}>{related.publisher.runs.map((run) => <ComicDrawerRunCard run={run} onOpen={(item) => onOpenSeries?.(item)} key={run.id} />)}</ComicDrawerRow> : null}
      </> : null}
      {tab === "issues" ? <GroupedIssueInventory issues={groupedIssues} onEditIssue={admin ? onEditIssue : undefined} onRead={onRead} onRate={onRateIssue} onMark={onMarkIssue} onMarkRun={onMarkRun ? (read) => onMarkRun(series, read) : undefined} runRead={runAllRead} runStarted={runStarted} readingFiles={readingFiles} medium={series.medium} /> : null}
      {tab === "editions" && editionsOn ? <VolumeInventory editions={series.editions} /> : null}
      {tab === "files" ? <FileInventory files={series.fileDetails} readingFiles={readingFiles} onRead={onRead} onOpenWorkbench={onOpenWorkbench} onOpenCover={onOpenCover} onOpenContents={onOpenContents} onChangeRun={onChangeRun} onReplace={onReplace} /> : null}
      {tab === "family" && editionsOn ? <CollectionManagement series={series} families={families} allSeries={allSeries} onCreateFamily={onCreateFamily} onSetFamily={onSetFamily} /> : null}
      {tab === "advanced" ? <div className="advanced-tools">
        {/* Moved off the header: useful when repairing a run, noise when reading one. */}
        <div className="drawer-facts"><span><strong>{series.fileDetails?.length ?? series.owned}</strong>Comic files</span><span><strong>{series.inventory?.directIssueFiles ?? 0}</strong>Single issues</span>{editionsOn ? <span><strong>{series.inventory?.editionCount ?? series.editions?.length ?? 0}</strong>Volumes</span> : null}<span><strong>{identityStrength}</strong>Match confidence</span></div>
        {/* Single issues are already a fact above; the volume split only means something with editions on. */}
        {editionsOn ? <CollectionCoverage series={series} editionsOn={editionsOn} /> : null}
        <IssueCatalogCard series={series} catalogKnown={catalogKnown} syncing={syncingIssues} error={syncError} lastResult={lastSyncResult} onSync={syncIssues} onReviewFiles={() => setTab("files")} onFindRun={onFindRun} />
        {/* Covers, titles and the match moved into Edit. Advanced keeps what
            repairs a run rather than what dresses it. */}
        <section className="advanced-card">
          <div><strong>Edit this run</strong><p>Its cover, header background, alternate titles, how it is filed, and the run it is matched to.</p></div>
          <button type="button" onClick={() => setEdit("")}><PencilSimple size={16} /> Edit</button>
        </section>
        {restartFile && onRead ? <section className="advanced-card">
          <div><strong>Start from the beginning</strong><p>Opens the run&rsquo;s first issue from its first page. Your place in the run moves once you turn a page there.</p></div>
          <button type="button" onClick={() => onRead({ id: restartFile.id, fromStart: true })}><ArrowCounterClockwise size={16} /> Restart</button>
        </section> : null}
        {readableFiles.length && onMarkRun ? <section className="advanced-card">
          <div><strong>Reading state</strong><p>{runAllRead
            ? "Every issue is read. Marking the run unread forgets your place in all of them, for your profile only."
            : runStarted
              ? "Some of this run is read or in progress. Mark it all read, or unread to forget every place -- someone else's reading on your profile included."
              : "Marks every issue of this run read, for your profile only. Each issue's menu does the same for one."}</p></div>
          <div className="advanced-card-actions">
            {!runAllRead ? <button type="button" onClick={() => onMarkRun(series, true)}><Check size={16} weight="bold" /> Mark read</button> : null}
            {runAllRead || runStarted ? <button type="button" onClick={() => onMarkRun(series, false)}><ArrowCounterClockwise size={16} /> Mark unread</button> : null}
          </div>
        </section> : null}
        <section className="advanced-card">
          <div><strong>Combine duplicate run</strong><p>One run split into two entries? Merge them. Files on disk aren&rsquo;t changed.</p></div>
          <button type="button" onClick={() => onMergeRun(series)}><Books size={16} /> Combine</button>
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
    </>}
  </aside></div>;
}

/**
 * The end of an issue.
 *
 * It opens over the reader rather than inside it, which is why it is the app's
 * drawer and not a card of its own: the same panel, the same top bar, the same
 * four ways out. Dismissing it puts you back on the last page, because the
 * reader underneath never unmounted -- so there is nothing here called "Keep
 * reading" or "Done". Leaving is leaving.
 *
 * What it says is what a bookshelf says when you close a book: this is what
 * you finished, what did you think of it, here is the next one and what it is
 * about, and here is the rest of the run.
 */
function FinishDrawer({ series, issue, nextIssue, medium, title, readingVersion = 0,
  onRateIssue, onRead, onOpenSeries, onClose }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  useSwipeToDismiss(dialogRef, requestClose);
  const art = issue?.fileCover || issue?.cover || series?.cover || null;
  const toned = toneProps(useArtTone(art));
  // The run's progress has just changed -- by this very comic -- so the shelf
  // asks again rather than showing places that are one issue out of date.
  const [reading, setReading] = useState(null);
  useEffect(() => {
    if (!series?.id) return undefined;
    let live = true;
    apiRequest(`/api/v1/series/${series.id}/reading`)
      .then((data) => { if (live) setReading(data); })
      .catch(() => { if (live) setReading(null); });
    return () => { live = false; };
  }, [series?.id, readingVersion]);
  const readingFiles = useMemo(() => Object.fromEntries(
    [...(reading?.issues || []), ...(reading?.volumes || [])].map((file) => [String(file.id), file]),
  ), [reading]);
  // What the next issue is about, asked for when the drawer opens. It is the
  // next issue's blurb, not this one's: a card offering a comic is where a
  // description earns its place, and one about the comic you have just read
  // is a recap. Nothing is asked when the run is over.
  const [detail, setDetail] = useState({ state: "loading", data: null });
  useEffect(() => {
    if (!nextIssue?.id) return undefined;
    let live = true;
    setDetail({ state: "loading", data: null });
    apiRequest(`/api/v1/issues/${nextIssue.id}/detail`)
      .then((data) => { if (live) setDetail({ state: "done", data }); })
      .catch(() => { if (live) setDetail({ state: "done", data: { status: "unavailable" } }); });
    return () => { live = false; };
  }, [nextIssue?.id]);
  const heading = issue
    ? `${issueLabel(issue.number, medium)}${issue.title ? ` · ${issue.title}` : ""}`
    : title;
  // Everything else in the run, including what is missing from it: "next" skips
  // the issues you do not own, and a shelf that hid them would make that skip
  // look like the run itself.
  const rest = (series?.issues || []).filter((item) => (
    item !== issue && item !== nextIssue
  ));
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer comic-drawer finish-drawer${toned.className} ${closing ? "closing" : ""}`}
      style={toned.style} ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="finish-drawer-title"
      onMouseDown={(event) => event.stopPropagation()}>
      <DiscoverDrawerHero art={art} titleId="finish-drawer-title" title={heading}
        cover={<DiscoverCover src={art} alt="" glyph={30} />}
        byline={issue ? series?.title : null}
        onClose={requestClose} closeLabel="Close">
        <span className="finish-done"><CheckCircle size={15} weight="fill" /> Finished</span>
      </DiscoverDrawerHero>
      <div className="comic-drawer-body">
        {/* The two cards sit close, as one pair of things to do; the shelf
            below keeps the body's own distance. */}
        <div className="finish-cards">
        {issue && onRateIssue ? <section className="finish-card finish-card--rating" aria-label="Your rating">
          <h3>What did you think?</h3>
          <StarRating size={28} title={issue.title || issueLabel(issue.number, medium)}
            rating={{ value: issue.yourRating || 0, source: issue.yourRating ? RATING_SOURCES.yours : RATING_SOURCES.none, count: 0 }}
            onRate={(value) => onRateIssue(issue, value)} />
        </section> : null}

        {nextIssue && onRead ? <section className="finish-next-block" aria-label="Next in this run">
          <h3>Next in this run</h3>
          <div className="finish-card finish-next">
            <DiscoverCover src={nextIssue.fileCover || nextIssue.cover} alt="" glyph={24} />
            <div className="finish-next-copy">
              {/* Named the way a shelf names it: the run and the number, then
                  the story's own title as the thing you are being offered. */}
              <strong>{[series?.title, issueLabel(nextIssue.number, medium)].filter(Boolean).join(" ")}</strong>
              {nextIssue.title ? <span className="finish-next-title">{nextIssue.title}</span> : null}
              {detail.state === "loading" ? <RunSynopsis loading heading={null} />
                : detail.data?.description
                  ? <RunSynopsis heading={null} text={detail.data.description} source={detail.data.providerName} />
                  : <p className="discover-note">{detail.data?.status === "unavailable"
                    ? "This issue’s details are unavailable right now."
                    : "No description on record for this issue."}</p>}
              <button type="button" className="primary-button" onClick={() => onRead({ id: nextIssue.fileId })}>
                {/* "Read Issue #2" for a comic; a volume label already names itself. */}
                Read {issueLabel(nextIssue.number, medium).startsWith("#") ? "Issue " : ""}{issueLabel(nextIssue.number, medium)}
              </button>
            </div>
          </div>
        </section> : <p className="finish-last">That is the last comic this run has.</p>}
        </div>

        {rest.length ? <ComicDrawerRow title="More from this run" count={rest.length}>
          {rest.map((item) => <ComicDrawerIssueCard key={item.id} issue={item} medium={medium}
            readingFiles={readingFiles} onRead={onRead}
            onOpen={() => { requestClose(); onOpenSeries?.(series); }} />)}
        </ComicDrawerRow> : null}
      </div>
    </aside>
  </div>;
}

function SeriesMergeWorkbench({ data, busy, error, onClose, onTargetChange, onConfirm }) {
  const dialogRef = useDialog(onClose);
  const [allowProviderConflicts, setAllowProviderConflicts] = useState(false);
  const preview = data.preview;
  useEffect(() => setAllowProviderConflicts(false), [data.targetId]);
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal series-merge-workbench" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="series-merge-title" onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close duplicate run recovery" /><span className="eyebrow">Combine duplicate run</span><h2 id="series-merge-title">Repair {data.source.title}</h2><p className="workbench-intro">Choose the canonical run to keep. Flipparr will move catalog relationships, issue and volume coverage, provider evidence, wanted items, and download history. Comic files are not renamed or moved.</p><label className="form-field"><span>Run to keep</span><GlassSelect label="Run to keep" placeholder="Choose the correct run…" value={data.targetId || ""} onChange={onTargetChange} className="glass-select--fill" options={data.candidates.map((candidate) => ({ value: candidate.id, label: `${candidate.title}${candidate.year ? ` (${candidate.year})` : ""} · ${candidate.publisher || "Publisher unknown"}` }))} /></label>{busy ? <div className="merge-loading"><LoadingSpinner size={28} label="Checking both runs" /><span>Checking files, issues, volumes, downloads, and provider identities…</span></div> : null}{preview && !busy ? <><div className="merge-direction"><article><small>Combine</small><strong>{preview.source.title}</strong><span>{preview.source.year || "Year unknown"} · {preview.source.counts.files} files</span></article><ArrowRight size={22} /><article className="keep"><small>Keep</small><strong>{preview.target.title}</strong><span>{preview.target.year || "Year unknown"} · {preview.target.counts.files} files</span></article></div><section className="merge-impact"><strong>After combining</strong><span>{preview.source.counts.files + preview.target.counts.files} comic files</span><span>{preview.source.counts.issues + preview.target.counts.issues} issue records before duplicate numbers are collapsed</span><span>{preview.source.counts.volumes + preview.target.counts.volumes} volumes</span></section>{preview.blockers?.length ? <div className="merge-warning blocked"><WarningCircle size={21} weight="fill" /><span><strong>These runs cannot be combined yet</strong>{preview.blockers.map((blocker) => <small key={blocker}>{blocker}</small>)}</span></div> : null}{preview.providerConflicts?.length ? <div className="merge-warning"><WarningCircle size={21} weight="fill" /><span><strong>Provider identities disagree</strong><small>This can indicate a real reboot or an incorrect match. Confirm only if these entries represent the same publication run.</small>{preview.providerConflicts.map((conflict) => <small key={conflict.provider}>{conflict.provider}: {conflict.sourceId} → {conflict.targetId}</small>)}<label><input type="checkbox" checked={allowProviderConflicts} onChange={(event) => setAllowProviderConflicts(event.target.checked)} /> I reviewed these provider IDs and want to combine the runs</label></span></div> : null}<div className="metadata-edit-actions"><button className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || preview.blockers?.length || (preview.providerConflicts?.length && !allowProviderConflicts)} onClick={() => onConfirm(allowProviderConflicts)}><ShieldCheck size={18} /> Combine runs</button></div></> : null}{error ? <p className="workbench-error" role="alert">{error}</p> : null}</section></div>;
}

function StoryArcList({ arcs, emptyTitle, onOpenSeries }) {
  if (!arcs.length) return <div className="drawer-empty"><Books size={27} weight="duotone" /><strong>{emptyTitle}</strong><span>Runs are discovered automatically. Manual grouping is available under Advanced tools.</span></div>;
  return <div className="story-arc-list">{arcs.map((arc) => <article key={arc.id}><header><div><span>{arc.type === "specials" ? "Specials / one-shots" : "Series run"}</span><strong>{arc.name}</strong></div><b className={arc.status}>{arc.status === "complete" ? "Complete" : arc.status === "partial" ? "Partially owned" : arc.status === "cataloged" ? "Not owned" : "Missing"}</b></header><div className="arc-progress-copy"><strong>{arc.ownedIssueCount} of {arc.issueCount || "unknown"} issues owned</strong><span>{arc.volumeCount} volume{arc.volumeCount === 1 ? "" : "s"} · {arc.fileCount} file{arc.fileCount === 1 ? "" : "s"}</span></div><div className="arc-run-links">{arc.runs.map((run) => <button onClick={() => onOpenSeries(run)} key={run.id}><span>{run.title} ({run.year})</span><ArrowRight size={15} /></button>)}</div></article>)}</div>;
}

function CollectionDrawer({ collection, tab, onTabChange, onClose, onFindStructure, onOpenSeries, onOpenContents, onRequest, onViewRequests, requestBusy, onEditIssue, onUnfollow, unfollowBusy = false, requested = false }) {
  const viewer = useViewer();
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
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer collection-drawer ${closing ? "closing" : ""}`} ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="collection-drawer-title" onMouseDown={(event) => event.stopPropagation()}>
      <DialogCloseButton onClose={requestClose} label="Close collection details" drawer />
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
      {tab === "overview" && isAdmin(viewer) ? <div className="drawer-actions"><FollowSwitch following={collection.monitoringStatus === "monitored"} busy={requestBusy || unfollowBusy} label={requestBusy ? "Following…" : unfollowBusy ? "Stopping…" : collection.monitoringStatus === "monitored" ? "Following" : "Follow collection"} onChange={(on) => (on ? onRequest() : onUnfollow(collection))} />{issueCoveragePending ? <button className="ghost-button" onClick={() => onTabChange("volumes")}><Books size={18} /> Review volume contents</button> : null}{collection.monitoringStatus === "monitored" && !issueCoveragePending && missingIssueCount ? <button className="ghost-button" onClick={onViewRequests}><CheckCircle size={18} weight="fill" /> View {missingIssueCount} wanted issue{missingIssueCount === 1 ? "" : "s"}</button> : null}<button className="ghost-button" onClick={() => onTabChange("files")}><Eye size={18} /> View files</button></div> : null}{tab === "overview" && !isAdmin(viewer) && collection.monitoringStatus !== "monitored" ? <div className="drawer-actions"><button type="button" className="ghost-button" onClick={() => onRequest()} disabled={requestBusy || requested}>{requestBusy ? <LoadingSpinner size={18} /> : requested ? <Hourglass size={18} /> : <FollowedIcon size={18} />} {requested ? "Follow requested" : "Request follow"}</button></div> : null}
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
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal structure-workbench" role="dialog" aria-modal="true" aria-labelledby="structure-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close series structure" /><span className="eyebrow">Find series structure</span><h2 id="structure-workbench-title">{data.collection.name}</h2><p className="workbench-intro">Review the proposed hierarchy before saving. Runs can be grouped into one story arc or split into separate arcs and specials; files and metadata are not changed.</p><form onSubmit={submit}><div className="structure-groups">{arcs.map((arc, index) => <article className={!arc.runIds.length ? "empty" : ""} key={`${arc.id || "new"}-${index}`}><header><span>{index + 1}</span><input value={arc.name} onChange={(event) => update(index, { name: event.target.value })} aria-label={`Story group ${index + 1} name`} /><GlassSelect label={`Story group ${index + 1} kind`} value={arc.type} onChange={(next) => update(index, { type: next })} className="glass-select--compact" options={[{ value: "main", label: "Story arc" }, { value: "specials", label: "Specials / one-shots" }]} /><button type="button" onClick={() => move(index, -1)} disabled={index === 0} aria-label="Move group up">↑</button><button type="button" onClick={() => move(index, 1)} disabled={index === arcs.length - 1} aria-label="Move group down">↓</button></header><small>{arc.reason} · {arc.confidence} confidence</small><div className="structure-runs">{arc.runIds.length ? arc.runIds.map((runId, runIndex) => <div key={runId}><span>{arc.runLabels[runIndex]}</span>{arcs.length > 1 ? <GlassSelect label={`Move ${arc.runLabels[runIndex]} to another group`} value={index} onChange={(next) => moveRun(index, runIndex, Number(next))} className="glass-select--compact" options={arcs.map((target, targetIndex) => ({ value: targetIndex, label: `Move to ${target.name || `group ${targetIndex + 1}`}` }))} /> : null}</div>) : <em>Empty group — move a run here or it will not be saved.</em>}</div></article>)}</div><button type="button" className="ghost-button add-structure-group" onClick={addGroup}><Plus size={17} /> Add story group</button>{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="metadata-edit-actions"><button type="button" className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || !arcs.some((arc) => arc.runIds.length)} aria-busy={busy}>{busy ? <LoadingSpinner size={18} /> : <ShieldCheck size={18} />} Save series structure</button></div></form></section></div>;
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
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className={`modal metadata-workbench ${mode === "match" ? "match-workbench" : ""}`} role="dialog" aria-modal="true" aria-labelledby="metadata-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close metadata workbench" /><span className="eyebrow">{mode === "match" ? "Fix match" : "Edit metadata"}</span><h2 id="metadata-workbench-title">{data.file.filename}</h2>{mode === "match" ? <><p className="workbench-intro">Search your enabled metadata services, then compare the complete metadata and ownership impact before selecting a match.</p><form className="match-search-form" onSubmit={searchMatches}><label htmlFor="fix-match-query"><span>Search metadata services</span><small>Starts with your latest saved title and year. You can broaden or replace the query.</small></label><div><input id="fix-match-query" value={matchQuery} onChange={(event) => setMatchQuery(event.target.value)} placeholder="Series, volume, year, or ISBN…" autoComplete="off" /><button type="submit" className="primary-button" disabled={busy || matchQuery.trim().length < 2}>{busy ? <LoadingSpinner size={18} /> : <MagnifyingGlass size={18} />}{busy ? "Searching…" : "Search"}</button></div>{searchInfo.query ? <p className="match-search-summary"><strong>{searchInfo.resultCount || 0} result{searchInfo.resultCount === 1 ? "" : "s"}</strong> for “{searchInfo.query}” · {(searchInfo.providersChecked || []).join(", ") || "metadata services"}{searchInfo.errors?.length ? <span>{searchInfo.errors.length} service{searchInfo.errors.length === 1 ? "" : "s"} could not respond</span> : null}</p> : null}</form><div className="match-candidates">{data.candidates.length ? data.candidates.map((candidate) => <MatchCandidateCard candidate={candidate} current={data.current} selectedKey={data.selectedCandidateKey} busy={busy} onMatch={onMatch} key={candidate.key} />) : <div className="drawer-empty"><MagnifyingGlass size={26} /><strong>{searchInfo.query ? `No matches found for “${searchInfo.query}”` : "No alternate candidates were retained"}</strong><span>Try a cleaner series title, publication year, volume number, or ISBN. Saved metadata corrections automatically become the next suggested search.</span></div>}</div></> : <form className="metadata-edit-form" onSubmit={submit}><p className="workbench-intro">Saved values are stored locally and locked against future provider refreshes. Correcting the series also heals other files connected to the same local run; volume-specific details stay separate. The comic file itself is not modified.</p><label><span>Series</span><input value={fields.seriesTitle || ""} onChange={set("seriesTitle")} required /></label><label><span>Display title</span><input value={fields.title || ""} onChange={set("title")} required /></label><label><span>Subtitle</span><input value={fields.subtitle || ""} onChange={set("subtitle")} /></label><label><span>Record type</span><GlassSelect label="Record type" value={fields.recordType || "edition"} onChange={(next) => setFields({ ...fields, recordType: next })} className="glass-select--fill" options={[{ value: "issue", label: "Single issue" }, { value: "edition", label: "Volume" }]} /></label>{isIssue ? <label><span>Issue number</span><input value={fields.issueNumber || ""} onChange={set("issueNumber")} /></label> : <><label><span>Volume number</span><input type="number" min="0" value={fields.volumeNumber ?? ""} onChange={set("volumeNumber")} /></label><label><span>Volume type</span><GlassSelect label="Volume type" value={fields.editionKind || "edition"} onChange={(next) => setFields({ ...fields, editionKind: next })} className="glass-select--fill" options={Object.entries(EDITION_KIND_LABELS).map(([value, label]) => ({ value, label }))} /></label></>}<label><span>Publisher</span><input value={fields.publisher || ""} onChange={set("publisher")} /></label><label><span>Publication year</span><input type="number" min="1800" max="2200" value={fields.publicationYear ?? ""} onChange={set("publicationYear")} /></label><label><span>ISBN / GTIN</span><input value={fields.isbn || ""} onChange={set("isbn")} /></label><label><span>Format</span><input value={fields.format || ""} onChange={set("format")} /></label><div className="metadata-edit-actions">{Object.keys(data.override).length ? <button type="button" className="danger-button" onClick={onReset} disabled={busy}>Restore provider metadata</button> : <span />}<button className="primary-button" disabled={busy} aria-busy={busy}>{busy ? <LoadingSpinner size={18} /> : <ShieldCheck size={18} />} Save and lock</button></div></form>}{error ? <p className="workbench-error" role="alert">{error}</p> : null}</section></div>;
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
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal issue-metadata-workbench" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="issue-metadata-title" onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close issue metadata editor" /><span className="eyebrow">Edit issue metadata</span><h2 id="issue-metadata-title">{issue.contextLabel || "Issue"} #{issue.number}</h2><p className="workbench-intro">Correct the catalog after ingestion without modifying the comic file. Saved values remain locked when provider metadata is refreshed.</p><form className="issue-metadata-form" onSubmit={submit}><label className="form-field"><span>Issue title</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder={`Issue ${issue.number} title…`} /></label><label className="form-field"><span>Publication year</span><input type="number" min="1800" max="2200" value={publicationYear} onChange={(event) => setPublicationYear(event.target.value)} /></label><section className="provider-issue-evidence"><header><Database size={18} /><span><strong>Provider metadata retained</strong><small>You can restore these values at any time.</small></span></header><dl><div><dt>Title</dt><dd>{providerTitle}</dd></div><div><dt>Year</dt><dd>{providerYear}</dd></div></dl></section>{issue.metadataLocked ? <p className="issue-lock-note"><ShieldCheck size={17} weight="fill" /> A local correction is currently locked for this issue.</p> : null}{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="metadata-edit-actions">{issue.metadataLocked ? <button type="button" className="danger-button" onClick={onReset} disabled={busy}>Restore provider metadata</button> : <button type="button" className="ghost-button" onClick={onClose}>Cancel</button>}<button className="primary-button" disabled={busy || (!title.trim() && !publicationYear)} aria-busy={busy}>{busy ? <LoadingSpinner size={18} /> : <ShieldCheck size={18} />} Save and lock</button></div></form></section></div>;
}

function SeriesMatchWorkbench({ data, loading, busy, error, onClose, onSearch, onConfirm }) {
  const dialogRef = useDialog(onClose);
  const [draft, setDraft] = useState(data.query || data.series?.title || "");
  const candidates = data.candidates || [];
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}>
    <section className="modal series-match-workbench" role="dialog" aria-modal="true" aria-labelledby="series-match-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}>
      <DialogCloseButton onClose={onClose} label="Close series match" />
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

/**
 * The cover picker itself, without a window around it.
 *
 * Two things host this: the modal below, which the Files tab opens for one
 * file's cover, and the run drawer's Edit panel, which shows it in place. One
 * body so the two cannot drift into saying different things about the same
 * choice.
 */
function CoverPicker({ data, busy, error, onSelect, onUpload }) {
  const selectedSource = data.covers.selectedSource;
  // A run has many options from the same source, so identity is the option
  // id where the payload carries one, not the source alone.
  const isSelected = (option) => (
    data.covers.selectedOptionId
      ? data.covers.selectedOptionId === option.optionId
      : selectedSource === option.source && (!data.covers.selectedUrl || data.covers.selectedUrl === option.url)
  );
  return <>
    <div className="cover-option-grid">{data.covers.options.map((option) => <article className={isSelected(option) ? "selected" : ""} key={option.optionId || `${option.source}-${option.url}`}><img src={option.url} alt={option.label} /><div><strong>{option.label}</strong><small>{option.detail}</small><button disabled={busy} onClick={() => onSelect(option.source, option.url, option.fileId)}>{isSelected(option) ? "Selected" : "Use cover"}</button></div></article>)}</div>
    <div className="cover-picker-actions"><button className="ghost-button" disabled={busy} onClick={() => onSelect("auto", null)}><ArrowsClockwise size={17} /> Use automatic cover</button><label className="primary-button upload-cover-button"><UploadSimple size={18} /> Upload image<input type="file" accept="image/jpeg,image/png,image/webp,image/gif,image/heic,image/heif" disabled={busy} onChange={(event) => event.target.files?.[0] && onUpload(event.target.files[0])} /></label></div>
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
  </>;
}

function CoverWorkbench({ data, title, busy, error, onClose, onSelect, onUpload }) {
  const dialogRef = useDialog(onClose);
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal cover-workbench" role="dialog" aria-modal="true" aria-labelledby="cover-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close cover picker" /><span className="eyebrow">Change cover</span><h2 id="cover-workbench-title">{title || data.file?.filename}</h2><p className="workbench-intro">Choose art from the comic, a metadata provider, or upload your own image. This does not alter the original comic file.</p>
    <CoverPicker data={data} busy={busy} error={error} onSelect={onSelect} onUpload={onUpload} />
  </section></div>;
}

// --- The reader ---------------------------------------------------------------
//
// A comic, full screen, over whatever opened it. The page endpoints have been
// in production since the drawer's headers needed them; this is the surface
// that reads them.
//
// What it borrows from a Kindle, deliberately: a scrubber that previews the
// page you are dragging over so skimming back does not lose your place, a way
// to type a page number, and a night setting that dims and warms the page
// rather than sending you to Control Centre.
// The reader's settings as a drawer, the same panel every other detail in
// the app opens in: a page pushed over the reader on a phone, a side panel
// above it elsewhere, Back and Escape closing it. It grew past a popover
// once panel view had its own choices; grouped rows with a line each, as
// Settings has them, carry the explanations a menu had no room for.
function ReaderSettingsDrawer({
  night, onNight, panelMode, onTogglePanelMode, panelScrim, onPanelScrim, panelStartWhole, onPanelStartWhole,
  panelReveal, onPanelReveal, count, rereading, onFixPanels, onReadAgain, onClose,
}) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  // Correcting and re-scanning a page's panels changes them for everyone.
  const admin = isAdmin(useViewer());
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer reader-settings-drawer ${closing ? "closing" : ""}`} ref={dialogRef} role="dialog" aria-modal="true"
      aria-labelledby="reader-settings-title" onMouseDown={(event) => event.stopPropagation()}>
      <DialogCloseButton onClose={requestClose} label="Close reader settings" drawer />
      <h2 id="reader-settings-title">Reader settings</h2>
      <SettingsCard title="Screen">
        <label className="reader-settings-range"><span>Brightness</span>
          <input type="range" min="0.35" max="1" step="0.05" value={night.dim}
            onChange={(event) => onNight({ ...night, dim: Number(event.target.value) })} />
        </label>
        <label className="reader-settings-range"><span>Warmth</span>
          <input type="range" min="0" max="1" step="0.1" value={night.warm}
            onChange={(event) => onNight({ ...night, warm: Number(event.target.value) })} />
        </label>
      </SettingsCard>
      {/* Reading by panel is the card: its switch in the header, and everything
          it opens -- the choices and the two tools -- underneath, only while on. */}
      <SettingsCard title="Read by panel" action={<HeaderToggle checked={panelMode} onChange={() => onTogglePanelMode()} label="Read by panel" />}>
        {panelMode ? <>
          <Toggle checked={panelScrim} onChange={(value) => onPanelScrim(value)} title="Dim around the panel" />
          <Toggle checked={panelStartWhole} onChange={(value) => onPanelStartWhole(value)} title="Start each page on the whole page" />
          <Toggle checked={panelReveal} onChange={(value) => onPanelReveal(value)} title="End each page on the whole page" />
          {count && admin ? <div className="advanced-tools reader-settings-tools">
            <section className="advanced-card">
              <div><strong>Manually fix panels</strong><p>Draw, move, resize or reorder this page&rsquo;s panels by hand.</p></div>
              <button type="button" onClick={onFixPanels}><PencilSimple size={16} /> Fix panels</button>
            </section>
            <section className="advanced-card">
              <div><strong>Scan pages for panels again</strong><p>Finds this issue&rsquo;s panels again as you reach each page, keeping the pages you fixed.</p>
                {rereading === "done" ? <small className="rebuild-result">Pages will be scanned again as you reach them.</small> : null}
                {rereading === "failed" ? <small className="rebuild-result">The pages could not be scanned again.</small> : null}</div>
              <button type="button" onClick={onReadAgain} disabled={rereading === "asking"} aria-busy={rereading === "asking"}>
                {rereading === "asking" ? <LoadingSpinner size={16} /> : <ArrowsClockwise size={16} />} {rereading === "asking" ? "Scanning…" : "Scan again"}
              </button>
            </section>
          </div> : null}
        </> : <p className="settings-card-lead">One panel at a time, in reading order.</p>}
      </SettingsCard>
    </aside>
  </div>;
}

// Correcting a page's panels by hand, over the page itself: the finder's
// rectangles (or none, for a page it could not read), each numbered in reading
// order; drag one to move it, its handles to resize, an empty stretch of page
// to draw a new one; tap them in order to renumber them. The editor pages
// through the issue on its own -- the arrows in its bar, or the keyboard's
// with no panel selected -- and saves a page as it is left, so a whole issue
// is corrected in one sitting. What is saved is the person's: nothing
// automatic touches it again until they let it go. The geometry is
// panel-editor.js; this is the pointer, the paging and the paint.
const EDITOR_HANDLE_PX = 22;
const EDITOR_NUDGE = 0.005;

function PanelEditor({ fileId, count, pages, startPage, readings, direction, onSaved, onClose }) {
  const [page, setPage] = useState(startPage);
  const [panels, setPanels] = useState([]);
  const [reading, setReading] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(-1);
  const [draft, setDraft] = useState(null);
  const [ordering, setOrdering] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState("");
  const [error, setError] = useState("");
  const [box, setBox] = useState(null);
  const stageRef = useRef(null);
  const imageRef = useRef(null);
  const drag = useRef(null);
  const manual = reading?.source === "manual";
  const pageRef = useRef(page);
  pageRef.current = page;
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  const panelsRef = useRef(panels);
  panelsRef.current = panels;

  // The page's reading: the reader's, when it has one, else asked for.
  useEffect(() => {
    let live = true;
    const known = readings?.[page];
    setSelected(-1);
    setOrdering(null);
    setDraft(null);
    setDirty(false);
    setError("");
    if (known) {
      setReading(known);
      setPanels(fromReading(known));
      setLoading(false);
      return undefined;
    }
    setLoading(true);
    apiRequest(`/api/v1/files/${fileId}/pages/${page}/panels`)
      .then((data) => { if (live) { setReading(data); setPanels(fromReading(data)); setLoading(false); } })
      .catch((problem) => { if (live) { setReading(null); setPanels([]); setLoading(false); setError(problem.message || "This page's panels could not be read"); } });
    return () => { live = false; };
  }, [fileId, page]); // the reader's map is read once per page, not re-applied over edits
  // The next page's image is fetched ahead, so a turn does not wait on it.
  useEffect(() => {
    const next = pages[page + 1];
    if (next?.readUrl) { const ahead = new Image(); ahead.src = next.readUrl; }
  }, [pages, page]);

  const finishRef = useRef(null);
  const dialogRef = useDialog(() => (ordering ? setOrdering(null) : finishRef.current?.()));

  // Where the page sits on the stage: the layer of rectangles lies over it
  // exactly, and every pointer position is read against it.
  useEffect(() => {
    const stage = stageRef.current;
    const image = imageRef.current;
    if (!stage || !image) return undefined;
    const measure = () => {
      if (!image.naturalWidth) return;
      const s = stage.getBoundingClientRect();
      const r = image.getBoundingClientRect();
      setBox({ left: r.left - s.left, top: r.top - s.top, width: r.width, height: r.height });
    };
    measure();
    image.addEventListener("load", measure);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(stage);
    return () => { image.removeEventListener("load", measure); observer?.disconnect(); };
  }, []);

  const pointAt = (event) => {
    const layer = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (event.clientX - layer.left) / layer.width)),
      y: Math.min(1, Math.max(0, (event.clientY - layer.top) / layer.height)),
    };
  };
  const reach = box ? { x: EDITOR_HANDLE_PX / box.width, y: EDITOR_HANDLE_PX / box.height } : { x: 0.03, y: 0.02 };

  function onPointerDown(event) {
    if (event.button || loading || saving) return;
    const point = pointAt(event);
    if (ordering) {
      const hit = hitTest(panels, point, { x: 0, y: 0 });
      if (!hit) return;
      const step = tapOrder(ordering, hit.index, panels.length);
      if (step.done) { setPanels(applyOrder(panels, step.sequence)); setOrdering(null); setDirty(true); setSelected(-1); }
      else setOrdering(step.sequence);
      return;
    }
    const hit = hitTest(panels, point, reach, selected);
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* a pointer the browser does not know: a test's */ }
    if (hit) {
      setSelected(hit.index);
      drag.current = { pointerId: event.pointerId, index: hit.index, part: hit.part, start: panels[hit.index], origin: point };
    } else {
      setSelected(-1);
      drag.current = { pointerId: event.pointerId, drawing: true, origin: point };
    }
  }
  function onPointerMove(event) {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const point = pointAt(event);
    if (current.drawing) { setDraft(drawnRect(current.origin, point)); return; }
    const dx = point.x - current.origin.x;
    const dy = point.y - current.origin.y;
    if (Math.abs(dx) < 0.002 && Math.abs(dy) < 0.002) return;
    current.moved = true;
    setPanels((list) => list.map((rect, at) => at === current.index ? dragRect(current.start, current.part, dx, dy) : rect));
  }
  function onPointerUp(event) {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    drag.current = null;
    if (current.drawing) {
      if (draft && isDrawn(draft)) { setPanels((list) => [...list, draft]); setSelected(panels.length); setDirty(true); }
      setDraft(null);
      return;
    }
    if (current.moved) setDirty(true);
  }

  // A page is saved as it is left -- by a turn, or by closing -- and not
  // left if it could not be. `saving` names what is under way, so the bar
  // can say so and a second turn waits its turn.
  async function flush() {
    if (!dirtyRef.current) return true;
    setSaving("save");
    setError("");
    try {
      const data = await apiRequest(`/api/v1/files/${fileId}/pages/${pageRef.current}/panels`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ panels: toPayload(panelsRef.current) }),
      });
      onSaved(pageRef.current, data);
      setReading(data);
      setDirty(false);
      return true;
    } catch (problem) {
      setError(problem.message || "The panels could not be saved");
      return false;
    } finally {
      setSaving("");
    }
  }
  async function turn(delta) {
    const target = Math.min(count - 1, Math.max(0, page + delta));
    if (target === page || saving || loading) return;
    if (!(await flush())) return;
    setPage(target);
  }
  async function finish() {
    if (saving) return;
    if (!(await flush())) return;
    onClose(pageRef.current);
  }
  finishRef.current = finish;
  async function letGo() {
    setSaving("reset");
    setError("");
    try {
      const data = await apiRequest(`/api/v1/files/${fileId}/pages/${page}/panels`, { method: "DELETE" });
      onSaved(page, data);
      setReading(data);
      setPanels(fromReading(data));
      setSelected(-1);
      setDirty(false);
    } catch (problem) {
      setError(problem.message || "The page could not be read again");
    } finally {
      setSaving("");
    }
  }
  function add() {
    const rect = newPanel(panels);
    setPanels([...panels, rect]);
    setSelected(panels.length);
    setDirty(true);
  }
  function remove() {
    const next = removeAt(panels, selected);
    setPanels(next.panels);
    setSelected(next.selected);
    setDirty(true);
  }
  // The keyboard is heard from anywhere in the editor -- focus rests on a
  // button, or nowhere after a tap on the page -- and only while the editor
  // is the dialog in front, so the reader's own keys stay quiet beneath it.
  const keyRef = useRef(null);
  keyRef.current = (event) => {
    if (event.target.closest("input, textarea, select") || ordering) return;
    const intent = editorKeyIntent(event.key, selected, direction);
    if (!intent) return;
    event.preventDefault();
    if (saving) return;
    if (intent === "remove") remove();
    else if (intent === "nudge") {
      setPanels((list) => list.map((rect, at) => at === selected ? nudge(rect, event.key, EDITOR_NUDGE, event.shiftKey) : rect));
      setDirty(true);
    } else turn(intent === "next" ? 1 : -1);
  };
  useEffect(() => {
    const onKey = (event) => { if (isTopDialog(dialogRef.current)) keyRef.current(event); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dialogRef]);

  const status = saving === "save" ? "Saving…" : saving === "reset" ? "Reading the page again…" : error ? "" : dirty ? "Unsaved changes" : manual ? "Saved" : "";
  // Nothing is edited while a save or a re-read is in flight: an answer
  // landing over a change made meanwhile would lose one or the other.
  const busy = loading || Boolean(saving);
  const hint = ordering
    ? `Tap the panels in reading order · ${ordering.length} of ${panels.length}`
    : loading ? "Reading the page…"
      : panels.length
        ? "Drag a panel to move it, its corners to resize. Drag on the page to draw one. Arrows turn the page; pages save as you leave them."
        : "Drag on the page to draw the first panel, or leave it with none for a single image.";
  return <div className="panel-editor" ref={dialogRef} role="dialog" aria-modal="true" aria-label={`Panels on page ${page + 1}`}>
    <header className="reader-bar reader-bar--top panel-editor-bar">
      <button type="button" className="glass-button glass-button--icon" onClick={finish} disabled={Boolean(saving)} aria-label="Done fixing panels"><X size={20} /></button>
      <span className="reader-title">Panels · page {page + 1} of {count}{status ? <span className="panel-editor-status">{status}</span> : null}</span>
      <span className="panel-editor-pager glass-capsule">
        <button type="button" className="glass-button glass-button--icon" onClick={() => turn(direction === READING_DIRECTIONS.rtl ? 1 : -1)}
          disabled={Boolean(saving) || (direction === READING_DIRECTIONS.rtl ? page >= count - 1 : page <= 0)} aria-label={direction === READING_DIRECTIONS.rtl ? "Next page" : "Previous page"}><ArrowLeft size={18} /></button>
        <button type="button" className="glass-button glass-button--icon" onClick={() => turn(direction === READING_DIRECTIONS.rtl ? -1 : 1)}
          disabled={Boolean(saving) || (direction === READING_DIRECTIONS.rtl ? page <= 0 : page >= count - 1)} aria-label={direction === READING_DIRECTIONS.rtl ? "Previous page" : "Next page"}><ArrowRight size={18} /></button>
      </span>
    </header>
    <div className="panel-editor-stage" ref={stageRef} onPointerDown={(event) => { if (event.target === event.currentTarget) setSelected(-1); }}>
      <img ref={imageRef} src={pages[page]?.readUrl} alt="" draggable="false" />
      {box && !loading ? <div className={`panel-editor-layer${ordering ? " ordering" : ""}`}
        style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
        {panels.map((rect, at) => {
          const number = ordering ? ordering.indexOf(at) + 1 : at + 1;
          return <div key={at} className={`panel-editor-box${at === selected && !ordering ? " selected" : ""}${ordering && number ? " numbered" : ""}`}
            style={{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%` }}>
            <b className="panel-editor-number">{number || "·"}</b>
            {at === selected && !ordering ? HANDLES.map((handle) => <i key={handle} className={`panel-editor-handle panel-editor-handle--${handle}`} aria-hidden="true" />) : null}
          </div>;
        })}
        {draft ? <div className="panel-editor-box drawing" style={{ left: `${draft.x * 100}%`, top: `${draft.y * 100}%`, width: `${draft.w * 100}%`, height: `${draft.h * 100}%` }} /> : null}
      </div> : null}
    </div>
    <footer className="reader-bar reader-bar--bottom panel-editor-tools">
      <p className={`panel-editor-hint${error ? " panel-editor-hint--error" : ""}`} role="status">{error || hint}</p>
      <div className="panel-editor-actions">
        {ordering ? <button type="button" className="glass-button" onClick={() => setOrdering(null)}>Cancel ordering</button> : <>
          <button type="button" className="glass-button" onClick={add} disabled={busy}><Plus size={16} /> Add panel</button>
          <button type="button" className="glass-button" onClick={() => { setOrdering([]); setSelected(-1); }} disabled={busy || panels.length < 2}><ListBullets size={16} /> Set order</button>
          <button type="button" className="glass-button" onClick={remove} disabled={busy || selected < 0}><Trash size={16} /> Delete</button>
          {manual ? <button type="button" className="glass-button" onClick={letGo} disabled={Boolean(saving)} aria-busy={saving === "reset"}>
            {saving === "reset" ? <LoadingSpinner size={16} /> : <ArrowCounterClockwise size={16} />} Back to automatic
          </button> : null}
        </>}
      </div>
    </footer>
  </div>;
}

function ReaderView({
  fileId, title, medium, directionOverride, startPage = null, behind = false,
  onFinish, onOpenRun, onProgressSaved, onClose,
}) {
  // Escape closes the page grid first: it is a layer over the comic, and
  // leaving the comic entirely is not what someone looking at its pages
  // meant by pressing it.
  const dialogRef = useDialog(() => (contents ? setContents(false) : onClose()));
  const [pages, setPages] = useState({ state: "loading", list: [], error: "" });
  const [index, setIndex] = useState(0);
  const [chrome, setChrome] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [panning, setPanning] = useState(false);
  // The current zoom and pan, readable from the natively bound wheel handler
  // and a drag in progress without either re-binding on every change.
  const zoomRef = useRef(1);
  const panRef = useRef(pan);
  zoomRef.current = zoom;
  if (!panning) panRef.current = pan;
  const pageRef = useRef(null);
  // The page's size at 1x, taken the moment it is zoomed. A zoomed page is
  // laid out at its real pixel size and only ever translated: scaling a
  // bitmap with a transform had the compositor stretching a 1x raster while
  // it moved and re-rasterising when it stopped, which read as a blur that
  // came and went with the scroll.
  const baseRef = useRef(null);
  useEffect(() => { if (zoom === 1) baseRef.current = null; }, [zoom]);
  const dragged = useRef(false);
  const pinching = useRef(false);
  // Panel view: one panel at a time. The server's reading of each page in the
  // window, which panel is in view, and whether the page is being shown whole
  // for a moment (a double-tap). The mode itself is kept in this browser.
  const [panelMode, setPanelMode] = useState(() => loadReaderPrefs().panelMode);
  // Whether the rest of the page dims around the panel being read.
  const [panelScrim, setPanelScrim] = useState(() => loadReaderPrefs().panelScrim);
  const [panelStartWhole, setPanelStartWhole] = useState(() => loadReaderPrefs().panelStartWhole);
  const [panelReveal, setPanelReveal] = useState(() => loadReaderPrefs().panelReveal);
  const prefsSaved = useRef(false);
  useEffect(() => {
    saveReaderPrefs({ panelMode, panelScrim, panelStartWhole, panelReveal });
    // And to the profile, a moment after the last change: not on opening,
    // which changes nothing.
    if (!prefsSaved.current) { prefsSaved.current = true; return undefined; }
    const timer = window.setTimeout(() => {
      apiRequest("/api/v1/me/prefs", {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ panelMode, panelScrim, panelStartWhole, panelReveal }),
      }).catch(() => {});
    }, 600);
    return () => window.clearTimeout(timer);
  }, [panelMode, panelScrim, panelStartWhole, panelReveal]);
  // The whole-page steps asked for, before and after a page's panels.
  const stepPrefs = { start: panelStartWhole, end: panelReveal };
  const stepPrefsRef = useRef(stepPrefs);
  stepPrefsRef.current = stepPrefs;
  const [panel, setPanel] = useState(0);
  const [panels, setPanels] = useState({});
  const [overview, setOverview] = useState(false);
  // The framing a step is flying from, for the flight after the render.
  const flightRef = useRef(null);
  const panelModeRef = useRef(panelMode);
  const overviewRef = useRef(overview);
  const panelRef = useRef(panel);
  const panelsRef = useRef(panels);
  const fileIdRef = useRef(fileId);
  panelModeRef.current = panelMode;
  overviewRef.current = overview;
  panelRef.current = panel;
  panelsRef.current = panels;
  fileIdRef.current = fileId;
  const asking = useRef(new Set());
  const resumingPanel = useRef(null);
  const [night, setNight] = useState({ dim: 1, warm: 0 });
  const [settingsOpen, setSettingsOpen] = useState(false);
  // The page's panels, being corrected by hand over the page itself.
  const [fixingPanels, setFixingPanels] = useState(false);
  // Reading an issue's panels again: the comic's automatic readings are
  // forgotten on the server (a person's pages stay theirs) and the reader's
  // own map emptied, so each page is read afresh as it is reached -- for a
  // comic read before a connector or a way of reading was there.
  const [rereading, setRereading] = useState("");
  async function readPanelsAgain() {
    if (rereading === "asking") return;
    setRereading("asking");
    try {
      await apiRequest(`/api/v1/files/${fileId}/panels`, { method: "DELETE" });
      asking.current.clear();
      setPanels((current) => Object.fromEntries(Object.entries(current).filter(([, reading]) => reading?.source === "manual")));
      setRereading("done");
    } catch {
      setRereading("failed");
    }
  }
  const [scrub, setScrub] = useState(null);
  const [contents, setContents] = useState(false);
  const [arrows, setArrows] = useState(false);
  const [spreads, setSpreads] = useState({});
  const surfaceRef = useRef(null);
  const chromeTimer = useRef(null);
  const arrowTimer = useRef(null);
  const held = useRef(false);
  const onArrow = useRef(false);
  // Where the place is kept: `open` guards against saving page 0 over a real
  // place before the comic has finished opening, and `saved` against writing
  // the same page twice.
  const open = useRef(false);
  const saved = useRef(null);
  const at = useRef(0);
  at.current = index;
  const direction = readingDirection(medium, directionOverride);
  // What a page's panels are for stepping: the server's, or four quadrants
  // while its answer is on its way or when it had none -- so there is never
  // a page that cannot be stepped through.
  const pagePanels = useCallback((number) => {
    const entry = panelsRef.current[number];
    return entry?.segmented && entry.panels.length ? entry.panels : quadrantPanels(direction);
  }, [direction]);
  // The steps a page takes: its panels, and -- when asked for -- the whole
  // page before and after them, the way Guided View can. `panel` is a step;
  // what it shows is read through stepAt.
  const pageSteps = useCallback((number) => stepCount(pagePanels(number).length, stepPrefsRef.current), [pagePanels]);
  const shownStep = stepAt(panel, pagePanels(index).length, stepPrefs);
  const wholePageStep = panelMode && !overview && shownStep.whole;
  // The panel the place is kept at: a whole-page step counts as its nearest panel.
  const placePanel = useCallback((number, step) => (
    panelModeRef.current ? stepAt(step, pagePanels(number).length, stepPrefsRef.current).panel : 0
  ), [pagePanels]);
  const togglePanelMode = useCallback(() => {
    setPanelMode((value) => !value);
    setOverview(false);
    setPanel(0);
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, []);
  const count = pages.list.length;
  const spread = Boolean(spreads[index]);

  useEffect(() => {
    let live = true;
    open.current = false;
    saved.current = null;
    setPages({ state: "loading", list: [], error: "" });
    setIndex(0);
    Promise.all([
      apiRequest(`/api/v1/files/${fileId}/pages`),
      // A comic never opened has no place yet, and a place the server no
      // longer trusts comes back as page 0; neither is worth an error.
      apiRequest(`/api/v1/files/${fileId}/progress`).catch(() => null),
    ])
      .then(([data, place]) => {
        if (!live) return;
        const list = data.pages || [];
        // A comic already finished opens from its beginning: every Read on it
        // -- the finish drawer's, a card's "Read again", the run's Restart --
        // means reading it again, and its last page is not a place to resume.
        // Nothing is written until a page is turned, so the record stays
        // finished until the re-read has actually begun.
        const asked = startPage !== null ? startPage
          : place?.finishedAt ? 0
          : Number(place?.page) || 0;
        const start = Math.min(Math.max(0, asked), Math.max(0, list.length - 1));
        // The panel the place was left on is taken up once that page's
        // panels are known (the fetch below), since a step is counted
        // against them; a comic opened at an asked-for page starts it whole.
        const resumePanel = startPage === null && !place?.finishedAt ? Number(place?.panel) || 0 : 0;
        resumingPanel.current = resumePanel > 0 ? { page: start, panel: resumePanel } : null;
        setPages({ state: "done", list, error: "" });
        setIndex(start);
        // Both refs move with the resumed page, not with the render that
        // follows: closing the reader in the frame between the two would
        // otherwise save the cover over the place just restored.
        saved.current = `${start}:${resumePanel}`;
        at.current = start;
        open.current = true;
      })
      .catch((error) => { if (live) setPages({ state: "done", list: [], error: error.message }); });
    return () => { live = false; };
  }, [fileId, startPage]);

  // A new comic is a new set of pages to read.
  useEffect(() => {
    setPanels({});
    asking.current.clear();
    setPanel(0);
    setOverview(false);
  }, [fileId]);

  // The server is asked about the pages in the window, once each, only while
  // panel view is on. A page is read the first time anyone asks and kept, so
  // this is a comic being segmented as it is read, never a library sweep.
  useEffect(() => {
    if (!panelMode || pages.state !== "done" || !count) return;
    const asked = fileId;
    for (const number of pageWindow(index, count)) {
      if (panels[number] || asking.current.has(number)) continue;
      asking.current.add(number);
      apiRequest(`/api/v1/files/${asked}/pages/${number}/panels`)
        .then((data) => {
          if (fileIdRef.current !== asked) return;
          setPanels((current) => ({ ...current, [number]: data }));
          // The place's panel, now that the page's panels are known.
          const resuming = resumingPanel.current;
          if (resuming && resuming.page === number) {
            resumingPanel.current = null;
            const rects = data?.segmented && data.panels.length ? data.panels : quadrantPanels(direction);
            setPanel(stepOf(resuming.panel, rects.length, stepPrefsRef.current));
          }
        })
        .catch(() => { if (fileIdRef.current === asked) setPanels((current) => ({ ...current, [number]: { segmented: false, panels: [] } })); });
    }
  }, [panelMode, pages.state, count, index, fileId, panels, direction]);

  // The framing for one panel of one page, from the shown image's natural
  // size fitted to the surface -- its 1x layout size, whatever zoom it is at.
  // Null until an image is on screen to measure.
  const focusFor = useCallback((number, position) => {
    const image = pageRef.current;
    const surface = surfaceRef.current;
    if (!image || !surface || !image.naturalWidth) return null;
    const viewport = surface.getBoundingClientRect();
    const fit = Math.min(1, viewport.width / image.naturalWidth, viewport.height / image.naturalHeight);
    const base = { width: image.naturalWidth * fit, height: image.naturalHeight * fit };
    baseRef.current = base;
    const rects = pagePanels(number);
    const at = stepAt(position, rects.length, stepPrefsRef.current);
    if (at.whole) return { zoom: 1, pan: { x: 0, y: 0 } };
    return panelFocus(rects[at.panel], viewport, base);
  }, [pagePanels]);

  // Where the page sits for the panel in view: once the shown image has
  // loaded (`spreads` records that) and again for every step.
  useEffect(() => {
    if (!panelMode || overview || pages.state !== "done" || spreads[index] === undefined) return;
    const focus = focusFor(index, panel);
    if (!focus) return;
    flightRef.current = { zoom: zoomRef.current, pan: panRef.current };
    setZoom(focus.zoom);
    setPan(focus.pan);
  }, [panelMode, overview, pages.state, spreads, index, panel, panels, focusFor]);

  // Turning the phone changes the surface, and with it the page's 1x size:
  // fitted by width one way, by height the other. Whatever framing was in
  // force was worked out for the old surface. In panel view the panel is
  // framed again for the new one; a zoomed page keeps its zoom, has its 1x
  // size re-derived, and its pan brought back within the new bounds. A snap,
  // not a flight: the flight's geometry belongs to the surface that is gone.
  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface || typeof ResizeObserver === "undefined") return undefined;
    let last = null;
    const observer = new ResizeObserver(() => {
      const box = surface.getBoundingClientRect();
      const size = `${Math.round(box.width)}x${Math.round(box.height)}`;
      if (size === last) return;
      const first = last === null;
      last = size;
      if (first) return;
      const image = pageRef.current;
      if (!image?.naturalWidth) return;
      if (panelModeRef.current && !overviewRef.current) {
        const focus = focusFor(at.current, panelRef.current);
        if (focus) { setZoom(focus.zoom); setPan(focus.pan); }
        return;
      }
      if (zoomRef.current > 1) {
        const fit = Math.min(1, box.width / image.naturalWidth, box.height / image.naturalHeight);
        baseRef.current = { width: image.naturalWidth * fit, height: image.naturalHeight * fit };
        setPan((current) => clampPan(current, zoomRef.current, box, baseRef.current));
        // The size is inline from `baseRef`, which is not state; a render is
        // owed so the page takes its new 1x size.
        setZoom((current) => current);
        setPan((current) => ({ ...current }));
      }
    });
    observer.observe(surface);
    return () => observer.disconnect();
  }, [focusFor]);

  // The flight between framings, after the new one has been laid out: the
  // image is snapped to its new size and place, then a transform carries it
  // from where the old framing was to rest. Animating the layout itself was a
  // reflow of a 2x bitmap every frame, which a phone could not keep smooth,
  // and a page arriving at the old framing then jumping to its own read as
  // the reader losing its place. A transform is composited; the page is
  // crisp again the moment it lands.
  useLayoutEffect(() => {
    const from = flightRef.current;
    if (!from) return;
    flightRef.current = null;
    const image = pageRef.current;
    if (!image || !panelModeRef.current || typeof image.animate !== "function") return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const dx = from.pan.x - pan.x;
    const dy = from.pan.y - pan.y;
    const ratio = from.zoom / zoom;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1 && Math.abs(ratio - 1) < 0.01) return;
    image.animate(
      [{ transform: `translate(${dx}px, ${dy}px) scale(${ratio})` }, { transform: "none" }],
      // Short and decisive: a step should feel like a snap, not a glide.
      { duration: 180, easing: "cubic-bezier(.2, .8, .2, 1)" },
    );
  }, [zoom, pan]);

  // The place is kept as you read: a moment after a page settles, so paging
  // quickly through a recap is one write rather than twenty, and again on the
  // way out, because a turn followed straight away by a close would otherwise
  // be the one page that is forgotten.
  // The place is the page and the panel on it (the step's nearest panel),
  // so panel view resumes where it was left, on any device.
  const keepPlace = useCallback((page, step = 0) => {
    const panelAt = placePanel(page, step);
    const key = `${page}:${panelAt}`;
    if (!open.current || key === saved.current) return null;
    saved.current = key;
    return apiRequest(`/api/v1/files/${fileId}/progress`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page, panel: panelAt }),
    }).catch(() => { saved.current = null; });
  }, [fileId, placePanel]);

  useEffect(() => {
    // Nothing is saved while the comic is still opening. A timer started at
    // page zero and a place arriving a moment later is a race the reader
    // loses: the timer fires with the cover's number and writes it over the
    // page you were on. Waiting for the place to land costs one page turn's
    // delay and cannot lose anything.
    if (!open.current) return undefined;
    const timer = window.setTimeout(() => keepPlace(index, panelRef.current), 900);
    return () => window.clearTimeout(timer);
  }, [index, panel, keepPlace]);

  // Closing saves, and whatever is listening is told once it has landed. The
  // Continue shelf asks the moment the reader goes; without this it asks
  // while the last page is still being written and misses the comic you have
  // just been reading.
  useEffect(() => () => {
    const saving = keepPlace(at.current, panelRef.current);
    if (saving) saving.then(() => onProgressSaved?.());
  }, [keepPlace, onProgressSaved]);

  /**
   * The chrome is asked for, not stumbled into.
   *
   * It shows once when a comic opens, to say what is here, and after that
   * only when someone asks for it by tapping the middle of the page. Moving a
   * pointer and turning a page both get the arrows instead: a hand resting on
   * a trackpad used to keep the bars awake for as long as you read, and
   * bringing them back on every page turn covers the page you just turned to.
   */
  const hideChrome = useCallback(() => setChrome(false), []);
  const wakeChrome = useCallback(() => {
    setChrome(true);
    window.clearTimeout(chromeTimer.current);
    // Nothing counts down while the pointer is resting on the bars or the
    // panel: a control that slides out from under the cursor on its way to
    // being clicked is worse than one that never appeared.
    if (held.current) return;
    chromeTimer.current = window.setTimeout(hideChrome, 1800);
  }, [hideChrome]);
  const wakeArrows = useCallback(() => {
    setArrows(true);
    window.clearTimeout(arrowTimer.current);
    if (onArrow.current) return;
    arrowTimer.current = window.setTimeout(() => setArrows(false), 1400);
  }, []);
  // Held open while a pointer rests on them, released when it leaves.
  const hold = useCallback((ref) => {
    // Waking again is what cancels the countdown already running: setting the
    // flag alone left the timer ticking, so the bar still slid away a moment
    // after the pointer landed on it.
    const wake = () => (ref === onArrow ? wakeArrows : wakeChrome)();
    return {
      onMouseEnter: () => { ref.current = true; wake(); },
      onMouseLeave: () => { ref.current = false; wake(); },
    };
  }, [wakeArrows, wakeChrome]);
  useEffect(() => {
    wakeChrome();
    return () => {
      window.clearTimeout(chromeTimer.current);
      window.clearTimeout(arrowTimer.current);
    };
  }, [wakeChrome]);

  // Turning a page shows the arrows, never the bars. The whole interface
  // coming back on every page turn is the comic being covered up by the thing
  // you turned the page to read.
  const go = useCallback((action) => {
    // In panel view a turn is a step: the next panel, or the next page's
    // first. Stepping past the end is finishing, exactly as paging past it is.
    if (panelModeRef.current && !overviewRef.current && count) {
      const counts = Array.from({ length: count }, (_, number) => pageSteps(number));
      const next = panelStep({ page: at.current, panel: panelRef.current }, action, counts);
      if (next === null) { if (action === "next") onFinish?.(); return; }
      if (next.page !== at.current) {
        // The next page is framed for its panel before it is shown, from this
        // page's size -- pages of a comic match -- so it arrives in place
        // rather than at the old framing and then jumping.
        const focus = focusFor(next.page, next.panel);
        if (focus) {
          flightRef.current = { zoom: zoomRef.current, pan: panRef.current };
          setZoom(focus.zoom);
          setPan(focus.pan);
        }
        setIndex(next.page);
      }
      setPanel(next.panel);
      wakeArrows();
      return;
    }
    // Paging on from the last page is how a reader says they have finished,
    // and the only moment worth asking what they thought of it.
    if (action === "next" && count && at.current >= count - 1) { onFinish?.(); return; }
    setIndex((current) => {
      const next = pageForAction(current, count, action);
      if (next !== current) { setZoom(1); setPan({ x: 0, y: 0 }); }
      return next;
    });
    wakeArrows();
  }, [count, wakeArrows, onFinish, pagePanels, focusFor]);

  useEffect(() => {
    function onKeyDown(event) {
      if (event.target.closest?.("input")) return;
      // Pages turn only while the pages are in front. With the finish drawer
      // or the run drawer open over the reader, Left, Right and Space used to
      // page the comic underneath -- so Left-then-dismiss landed you a page
      // short of where you had been.
      if (!isTopDialog(dialogRef.current)) return;
      if (event.key === "p" || event.key === "P") { event.preventDefault(); togglePanelMode(); return; }
      const action = actionForKey(event.key, direction);
      if (!action) return;
      event.preventDefault();
      go(action);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [direction, go, togglePanelMode]);

  // Two ahead and one behind stay mounted, so a page turn is instant and a
  // page back is too. The window is small on purpose: every page is a request
  // that opens the archive, and this server runs a thread per connection.
  const window_ = pageWindow(index, count);

  function onSurfacePointerDown(event) {
    if (zoom > 1 || event.pointerType === "mouse") return;
    const start = { x: event.clientX, y: event.clientY, at: Date.now() };
    const surface = surfaceRef.current;
    function finish(end) {
      surface.removeEventListener("pointerup", onUp);
      surface.removeEventListener("pointercancel", onCancel);
      // A pinch's fingers lifting are not a swipe, whatever line they make.
      if (pinching.current) return;
      const dx = end.clientX - start.x;
      const dy = end.clientY - start.y;
      if (Math.abs(dy) > 120 && Math.abs(dy) > Math.abs(dx)) { onClose(); return; }
      if (isSwipe(dx, dy, Date.now() - start.at)) {
        // A swipe drags the page with it: left moves the page left, which is
        // forward in a comic and back in manga, the same rule as a tap.
        const action = swipeAction(dx, direction);
        if (action) go(action);
      }
    }
    const onUp = (end) => finish(end);
    const onCancel = () => {
      surface.removeEventListener("pointerup", onUp);
      surface.removeEventListener("pointercancel", onCancel);
    };
    surface.addEventListener("pointerup", onUp);
    surface.addEventListener("pointercancel", onCancel);
  }

  function onSurfaceClick(event) {
    // Releasing a drag fires a click; a page turn on letting go of a pan is
    // not what a hand meant.
    if (dragged.current) { dragged.current = false; return; }
    const box = surfaceRef.current?.getBoundingClientRect();
    if (!box) return;
    const action = tapAction(event.clientX - box.left, box.width, direction);
    if (action === "chrome") {
      if (chrome) hideChrome(); else wakeChrome();
      return;
    }
    go(action);
  }

  // The surface and the page at 1x, for bounding a pan. The page's layout
  // size is read rather than its rect, which the scale would have inflated.
  const panBounds = useCallback(() => {
    const viewport = surfaceRef.current?.getBoundingClientRect() || { width: 0, height: 0 };
    const image = pageRef.current;
    const page = baseRef.current || (image ? { width: image.offsetWidth, height: image.offsetHeight } : viewport);
    return { viewport, page };
  }, []);

  // Zoom about a point on the surface, so what was under the pointer stays
  // under it: double-tapping a panel lands on that panel, not on the middle
  // of the page with the panel somewhere off to the side.
  const zoomTo = useCallback((target, clientX, clientY) => {
    const box = surfaceRef.current?.getBoundingClientRect();
    if (!box) return;
    const point = { x: clientX - (box.left + box.width / 2), y: clientY - (box.top + box.height / 2) };
    if (zoomRef.current === 1 && pageRef.current) {
      baseRef.current = { width: pageRef.current.offsetWidth, height: pageRef.current.offsetHeight };
    }
    const { viewport, page } = panBounds();
    const scale = clampZoom(target);
    setPan(clampPan(zoomAt(point, zoomRef.current, scale, panRef.current), scale, viewport, page));
    setZoom(scale);
  }, [panBounds]);

  function onDoubleClick(event) {
    event.preventDefault();
    // In panel view a double-tap shows the whole page, and again returns to
    // the panel you were on.
    if (panelMode) {
      if (overview) { setOverview(false); return; }
      setOverview(true);
      setZoom(1);
      setPan({ x: 0, y: 0 });
      return;
    }
    zoomTo(zoomRef.current > 1 ? 1 : 2, event.clientX, event.clientY);
  }

  // Wheel is bound natively: React's is passive, and a pinch or a ctrl-wheel
  // that is not prevented zooms the browser instead. With the page zoomed a
  // plain wheel or two-finger scroll moves it, the way a scroll view would.
  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return undefined;
    // A scroll is a drag in ticks, and gets the drag's treatment: written to
    // the image a frame at a time, the transition off for as long as the
    // ticks keep coming, and the state committed once they stop. A render per
    // tick with the transition on was every tick animated against the next.
    let frame = 0;
    let settle = 0;
    let latest = null;
    function paint() {
      frame = 0;
      const image = pageRef.current;
      if (image && latest) image.style.translate = `${latest.x}px ${latest.y}px`;
    }
    function onWheel(event) {
      if (event.ctrlKey || event.metaKey) {
        event.preventDefault();
        zoomTo(zoomRef.current - event.deltaY / 400, event.clientX, event.clientY);
        return;
      }
      if (zoomRef.current <= 1) return;
      event.preventDefault();
      const { viewport, page } = panBounds();
      latest = clampPan(
        { x: panRef.current.x - event.deltaX, y: panRef.current.y - event.deltaY },
        zoomRef.current, viewport, page, panelModeRef.current,
      );
      panRef.current = latest;
      if (!settle) setPanning(true);
      if (!frame) frame = requestAnimationFrame(paint);
      window.clearTimeout(settle);
      settle = window.setTimeout(() => {
        settle = 0;
        setPan(latest);
        setPanning(false);
      }, 150);
    }
    surface.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      surface.removeEventListener("wheel", onWheel);
      cancelAnimationFrame(frame);
      window.clearTimeout(settle);
    };
  }, [zoomTo, panBounds]);

  // Dragging a zoomed page. The drag is written straight to the image, one
  // frame per pointer move, and committed to state when the finger lifts: a
  // render per move was a repaint of a 2x bitmap per move, which is what
  // jittered. The page's transition is off for the drag, or every frame
  // would be animated and trail the finger; the bound is the page's own
  // edges; and only the pointer that started the drag moves it, so a second
  // finger landing does not throw it. `panRef` is kept current throughout, so
  // any render that happens mid-drag writes the same translate, not a stale one.
  // Two fingers on the page: a pinch, bound natively on the surface so it
  // sees every pointer, and written straight to the image a frame at a time
  // like the drag, committed when a finger lifts. The drag and the swipe
  // stand aside while it runs (`pinching`). In panel view a pinch in past
  // the panel's framing shows the whole page, as a double-tap does.
  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return undefined;
    const pointers = new Map();
    let gesture = null;
    const point = (event) => ({ x: event.clientX, y: event.clientY });
    function paint() {
      gesture.frame = 0;
      const image = pageRef.current;
      const base = baseRef.current;
      if (!image || !base) return;
      image.style.width = `${Math.round(base.width * gesture.zoom)}px`;
      image.style.height = `${Math.round(base.height * gesture.zoom)}px`;
      image.style.maxWidth = "none";
      image.style.maxHeight = "none";
      image.style.translate = `${gesture.pan.x}px ${gesture.pan.y}px`;
    }
    function begin() {
      const [a, b] = [...pointers.values()];
      const image = pageRef.current;
      if (zoomRef.current === 1 && image) baseRef.current = { width: image.offsetWidth, height: image.offsetHeight };
      gesture = { zoom0: zoomRef.current, dist0: pointerDistance(a, b), zoom: zoomRef.current, pan: panRef.current, frame: 0 };
      pinching.current = true;
      // The finger that lifts last fires a click; a page turn on letting go
      // of a pinch is not what the hand meant.
      dragged.current = true;
      setPanning(true);
    }
    function end() {
      cancelAnimationFrame(gesture.frame);
      const { zoom: after, pan } = gesture;
      gesture = null;
      const image = pageRef.current;
      const clear = () => {
        if (!image) return;
        for (const property of ["width", "height", "maxWidth", "maxHeight"]) image.style[property] = "";
      };
      const framing = panelModeRef.current && !overviewRef.current ? focusFor(at.current, panelRef.current)?.zoom || 0 : 0;
      if (pinchLeavesPanel(after, framing, panelModeRef.current, overviewRef.current)) {
        clear();
        setOverview(true);
        setZoom(1);
        setPan({ x: 0, y: 0 });
      } else {
        if (after <= 1) clear();
        setZoom(after);
        setPan(after <= 1 ? { x: 0, y: 0 } : pan);
      }
      setPanning(false);
      // The remaining finger's lift, and the click it fires, are still the
      // pinch's; only after them is a touch a touch again.
      window.setTimeout(() => { pinching.current = false; dragged.current = false; }, 0);
    }
    function onDown(event) {
      if (event.pointerType === "mouse") return;
      pointers.set(event.pointerId, point(event));
      if (pointers.size === 2 && !gesture) begin();
    }
    function onMove(event) {
      if (!pointers.has(event.pointerId)) return;
      pointers.set(event.pointerId, point(event));
      if (!gesture || pointers.size < 2) return;
      const [a, b] = [...pointers.values()];
      const box = surface.getBoundingClientRect();
      const mid = pointerMidpoint(a, b);
      const about = { x: mid.x - (box.left + box.width / 2), y: mid.y - (box.top + box.height / 2) };
      const next = pinchZoom(gesture.zoom0, gesture.dist0, pointerDistance(a, b));
      const { viewport, page } = panBounds();
      gesture.pan = clampPan(zoomAt(about, gesture.zoom, next, gesture.pan), next, viewport, page, panelModeRef.current);
      gesture.zoom = next;
      zoomRef.current = next;
      panRef.current = gesture.pan;
      if (!gesture.frame) gesture.frame = requestAnimationFrame(paint);
    }
    function onUp(event) {
      pointers.delete(event.pointerId);
      if (gesture && pointers.size < 2) end();
    }
    surface.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      surface.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      if (gesture) cancelAnimationFrame(gesture.frame);
    };
  }, [panBounds, focusFor]);

  function onPanStart(event) {
    // In panel view the page overflows the screen at any zoom, so a drag is
    // always a pan; otherwise a drag on the whole page is the swipe path's.
    // On a phone in panel view a finger's first moment is ambiguous: a
    // flick or the start of a look around. The page holds still until it
    // is one or the other -- a flick steps the moment it reads as one,
    // without waiting for the finger to lift, and the page never trails it;
    // a slower touch becomes a pan from where the finger is then. A mouse
    // drag is a pan from the start, as before.
    if ((zoom <= 1 && !panelModeRef.current) || event.button) return;
    event.preventDefault();
    const pointerId = event.pointerId;
    const image = pageRef.current;
    const start = { x: event.clientX, y: event.clientY, at: Date.now(), pan: panRef.current };
    const { viewport, page } = panBounds();
    let frame = 0;
    let latest = start.pan;
    let mode = panelModeRef.current && event.pointerType !== "mouse" ? "pending" : "pan";
    if (mode === "pan") setPanning(true);
    function paint() {
      frame = 0;
      if (image) image.style.translate = `${latest.x}px ${latest.y}px`;
    }
    function letGo() {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    }
    function onMove(move) {
      if (move.pointerId !== pointerId) return;
      // A second finger has made this a pinch: the drag stands aside.
      if (pinching.current) { letGo(); cancelAnimationFrame(frame); return; }
      const dx = move.clientX - start.x;
      const dy = move.clientY - start.y;
      if (mode === "pending") {
        const elapsed = Date.now() - start.at;
        if (isFlick(dx, dy, elapsed)) {
          mode = "stepped";
          dragged.current = true;
          letGo();
          const action = swipeAction(dx, direction);
          if (action) go(action);
          return;
        }
        if (elapsed < FLICK_WINDOW_MS && Math.abs(dy) <= Math.abs(dx)) return;
        // A look around, then: from here, not from the touch.
        mode = "pan";
        start.x = move.clientX;
        start.y = move.clientY;
        setPanning(true);
        return;
      }
      if (mode !== "pan") return;
      if (Math.abs(dx) > 6 || Math.abs(dy) > 6) dragged.current = true;
      latest = clampPan({ x: start.pan.x + (move.clientX - start.x), y: start.pan.y + (move.clientY - start.y) }, zoomRef.current, viewport, page, panelModeRef.current);
      panRef.current = latest;
      if (!frame) frame = requestAnimationFrame(paint);
    }
    function onUp(up) {
      if (up.pointerId !== pointerId) return;
      letGo();
      cancelAnimationFrame(frame);
      if (mode !== "pan") return;
      paint();
      setPan(latest);
      setPanning(false);
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }

  function goToPage(target) {
    setIndex(target);
    setContents(false);
    setZoom(1);
    setPan({ x: 0, y: 0 });
    wakeArrows();
  }

  const filter = pageFilter(night);
  return <div className={`reader${chrome ? "" : " reader--reading"}${behind ? " reader--behind" : ""}`} ref={dialogRef} data-in-address="" role="dialog" aria-modal="true" aria-label={`Reading ${title}`}>
    <div className={`reader-surface${zoom > 1 || panelMode ? " zoomed" : ""}`} ref={surfaceRef} onClick={onSurfaceClick} onDoubleClick={onDoubleClick}
      onPointerDown={zoom > 1 || panelMode ? onPanStart : onSurfacePointerDown} onMouseMove={zoom > 1 || panelMode ? undefined : wakeArrows}>
      {pages.state === "loading" ? <div className="reader-status" role="status"><LoadingSpinner size={22} /> Opening…</div> : null}
      {pages.error ? <div className="reader-status reader-status--error" role="alert">
        <WarningCircle size={22} /><span>{pages.error}</span>
      </div> : null}
      {pages.state === "done" && !pages.error && !count ? <div className="reader-status" role="status">
        <WarningCircle size={22} /><span>This file has no pages to read.</span>
      </div> : null}
      {window_.map((number) => {
        const item = pages.list[number];
        const shown = number === index;
        // The page dims around the panel in view; the whole page, asked for
        // with a double-tap, is shown undimmed.
        const scrim = shown && panelMode && panelScrim && !overview && !wholePageStep;
        const hole = scrim ? panelMask(pagePanels(number)[shownStep.panel]) : null;
        return <img key={number} src={item.readUrl} alt={shown ? `Page ${number + 1} of ${count}` : ""}
          className={`reader-page${shown ? " shown" : ""}${spreads[number] ? " spread" : ""}${shown && panning ? " panning" : ""}${shown && panelMode ? " panel-view" : ""}${scrim ? " scrim" : ""}`}
          aria-hidden={shown ? undefined : "true"} decoding="async" draggable="false"
          ref={shown ? pageRef : undefined}
          style={shown ? {
            filter, translate: `${(panning ? panRef.current : pan).x}px ${(panning ? panRef.current : pan).y}px`,
            ...(hole ? { "--hole-w": `${hole.w}%`, "--hole-h": `${hole.h}%`, "--hole-x": `${hole.x}%`, "--hole-y": `${hole.y}%` } : {}),
            ...(zoom > 1 && baseRef.current ? {
              width: `${Math.round(baseRef.current.width * zoom)}px`, height: `${Math.round(baseRef.current.height * zoom)}px`,
              maxWidth: "none", maxHeight: "none",
            } : {}),
          } : undefined}
          onLoad={(event) => {
            const { naturalWidth, naturalHeight } = event.target;
            // A spread is shown across the width instead of squeezed into the
            // height; the rule is the backend's, so both agree what one is.
            setSpreads((current) => current[number] === isSpread(naturalWidth, naturalHeight)
              ? current : { ...current, [number]: isSpread(naturalWidth, naturalHeight) });
          }} />;
      })}
    </div>

    {/* What a pointer gets for moving: the way on, and the way back. The rest
        of the interface waits to be asked for. */}
    {count && !contents ? <>
      <button type="button" className={`reader-arrow reader-arrow--back${arrows || chrome ? " shown" : ""}`}
        onClick={() => go(direction === READING_DIRECTIONS.rtl ? "next" : "previous")}
        onMouseMove={wakeArrows} {...hold(onArrow)} aria-label={direction === READING_DIRECTIONS.rtl ? "Next page" : "Previous page"}>
        <ArrowLeft size={22} />
      </button>
      <button type="button" className={`reader-arrow reader-arrow--on${arrows || chrome ? " shown" : ""}`}
        onClick={() => go(direction === READING_DIRECTIONS.rtl ? "previous" : "next")}
        onMouseMove={wakeArrows} {...hold(onArrow)} aria-label={direction === READING_DIRECTIONS.rtl ? "Previous page" : "Next page"}>
        <ArrowRight size={22} />
      </button>
    </> : null}

    {/* Every page at once, the way a Kindle's Content grid does it: the pages
        themselves, not a box to type a number into. */}
    {contents ? <div className="reader-contents-backdrop" onMouseDown={() => setContents(false)}>
      <aside className="reader-contents" role="dialog" aria-label="All pages"
        onMouseDown={(event) => event.stopPropagation()}>
      <header className="reader-contents-bar">
        <span>All pages</span>
        <button type="button" className="glass-button glass-button--icon" onClick={() => setContents(false)}
          aria-label="Close all pages"><X size={20} /></button>
      </header>
      <div className="reader-contents-grid">
        {pages.list.map((page) => <button type="button" key={page.index}
          className={`reader-contents-page${page.index === index ? " current" : ""}`}
          aria-current={page.index === index ? "true" : undefined}
          // Bring the page being read into view: it is as likely to be page
          // 40 of 47 as page 2, and scrolling to find yourself is the thing
          // this grid exists to save.
          ref={page.index === index ? (node) => node?.scrollIntoView({ block: "center" }) : undefined}
          onClick={() => goToPage(page.index)}>
          <span className="reader-contents-art">
            <img src={page.url} alt="" loading="lazy" decoding="async" />
            {/* On the page, the way an issue tile says Upcoming or Missing on
                its cover rather than underneath it. */}
            {page.index === index ? <span className="ownership-source collection">Reading</span> : null}
          </span>
          <b>{page.index + 1}</b>
        </button>)}
      </div>
      </aside>
    </div> : null}

    <header className="reader-bar reader-bar--top" onPointerDown={wakeChrome} onFocusCapture={wakeChrome} {...hold(held)}>
      <button type="button" className="glass-button glass-button--icon" onClick={onClose} aria-label="Close the reader"><X size={20} /></button>
      {/* A keyboard's way out, said once beside the button. Phones have no
          Escape, so it is not drawn there. */}
      <kbd className="reader-esc-hint" aria-hidden="true">esc</kbd>
      <span className="reader-title">{title}</span>
      {onOpenRun ? <button type="button" className="glass-button glass-button--icon" onClick={onOpenRun}
        aria-label="Open this run" title="This run"><DotsThree size={22} weight="bold" /></button> : null}
      {count ? <button type="button" className={`glass-button glass-button--icon${contents ? " active" : ""}`}
        aria-expanded={contents} aria-label="All pages" title="All pages"
        onClick={() => { setContents((open) => !open); wakeChrome(); }}><GridViewIcon /></button> : null}
      <button type="button" className={`glass-button glass-button--icon${settingsOpen ? " active" : ""}`}
        aria-expanded={settingsOpen} aria-label="Reader settings"
        onClick={() => { setSettingsOpen((open) => !open); wakeChrome(); }}><Gear size={20} /></button>
    </header>

    {settingsOpen ? <ReaderSettingsDrawer night={night} onNight={setNight}
      panelMode={panelMode} onTogglePanelMode={togglePanelMode}
      panelScrim={panelScrim} onPanelScrim={setPanelScrim}
      panelStartWhole={panelStartWhole} onPanelStartWhole={setPanelStartWhole}
      panelReveal={panelReveal} onPanelReveal={setPanelReveal}
      count={count} rereading={rereading}
      onFixPanels={() => { setSettingsOpen(false); setFixingPanels(true); }}
      onReadAgain={readPanelsAgain}
      onClose={() => setSettingsOpen(false)} /> : null}

    {fixingPanels && pages.list[index] ? <PanelEditor fileId={fileId} count={count} pages={pages.list} startPage={index} readings={panels} direction={direction}
      onSaved={(number, data) => setPanels((current) => ({ ...current, [number]: data }))}
      onClose={(number) => {
        // The reader picks up where the editor left off, from the first
        // panel of that page; the focus effect re-frames from the new map.
        setFixingPanels(false);
        if (number !== index) goToPage(number);
        setPanel(0);
      }} /> : null}
    <footer className="reader-bar reader-bar--bottom" onPointerDown={wakeChrome} onFocusCapture={wakeChrome} {...hold(held)}>
      <span className="reader-count">{count
        ? (panelMode && !overview
          ? `${wholePageStep ? "Whole page" : `Panel ${shownStep.panel + 1} of ${pagePanels(index).length}`} · ${index + 1} of ${count}`
          : `${index + 1} of ${count}`)
        : "—"}{spread ? " · spread" : ""}</span>
      {count ? <div className="reader-scrubber">
        {/* The page under the thumb, before letting go: skimming back for the
            page a recap refers to should not cost your place. */}
        {scrub !== null && pages.list[scrub] ? <span className="reader-scrub-preview">
          <img src={pages.list[scrub].url} alt="" />
          <b>{scrub + 1}</b>
        </span> : null}
        <input type="range" min="0" max={count - 1} value={scrub ?? index} aria-label="Page"
          onChange={(event) => { setScrub(Number(event.target.value)); wakeChrome(); }}
          onPointerUp={() => { if (scrub !== null) { setIndex(scrub); setScrub(null); } }}
          onKeyUp={() => { if (scrub !== null) { setIndex(scrub); setScrub(null); } }}
          onBlur={() => setScrub(null)} />
      </div> : null}
      <span className="reader-left">{count ? pagesLeft(index, count) : ""}</span>
    </footer>
  </div>;
}

// The page behind a run's drawer header: pick an issue, then one of its pages.
// Pages load one issue at a time, as thumbnails the server renders from the
// file, so a long run costs nothing until an issue is opened.
const PAGE_ARCHIVE_EXTENSIONS = new Set(["cbz", "cbr", "cb7", "cbt", "zip", "rar"]);

/** The background picker, hosted by the modal below and by the Edit panel. */
function BackdropPicker({ series, current, busy, error, readable, onChoose, onAutomatic }) {
  const files = useMemo(() => [...(series.fileDetails || [])]
    .filter((file) => (readable ? readable[String(file.id)]?.readable
      : PAGE_ARCHIVE_EXTENSIONS.has(String(file.extension || "").replace(".", "").toLowerCase())))
    .sort((a, b) => String(a.filename).localeCompare(String(b.filename), undefined, { numeric: true, sensitivity: "base" })), [series.fileDetails, readable]);
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
  return <>
    <div className="backdrop-workbench-tools">
      <label><span>Issue</span><GlassSelect label="Issue" value={fileId} onChange={setFileId} className="glass-select--fill" options={files.map((file) => ({ value: file.id, label: String(file.filename).replace(/\.[^.]+$/, "") }))} /></label>
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
  </>;
}

function BackdropWorkbench({ series, current, busy, error, onClose, onChoose, onAutomatic }) {
  const dialogRef = useDialog(onClose);
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal backdrop-workbench" role="dialog" aria-modal="true" aria-labelledby="backdrop-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}>
    <DialogCloseButton onClose={onClose} label="Close" />
    <h2 id="backdrop-workbench-title">Header background</h2>
    <p className="backdrop-workbench-intro">Choose a page from {series.title} to show behind the drawer&rsquo;s header.</p>
    <BackdropPicker series={series} current={current} busy={busy} error={error} onChoose={onChoose} onAutomatic={onAutomatic} />
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
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal contents-workbench" role="dialog" aria-modal="true" aria-labelledby="contents-workbench-title" ref={dialogRef} onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close volume contents" /><span className="eyebrow">Volume contents</span><h2 id="contents-workbench-title">{data.file.filename}</h2><p className="workbench-intro">Review what this volume contains. Provider evidence remains visible; local corrections control which canonical issues count as owned.</p><div className="contents-summary"><strong>{contents.includedCount}</strong><span>canonical issues currently included</span>{contents.hasOverrides ? <b><ShieldCheck size={14} weight="fill" /> Local corrections applied</b> : null}</div><div className="contents-list">{contents.items.length ? contents.items.map((item) => <article className={!item.included ? "excluded" : item.resolved ? "resolved" : "unresolved"} key={`${item.seriesId}-${item.seriesLabel}-${item.issueNumber}-${item.source}`}><div><strong>{item.seriesLabel} #{item.issueNumber}</strong><small>{item.source} · {item.confidence}{item.evidence ? ` · ${item.evidence}` : ""}</small></div><span>{!item.included ? "Excluded" : item.resolved ? "Counts as owned" : "Needs series match"}</span>{item.seriesId ? <button disabled={busy} onClick={() => onChange({ seriesId: item.seriesId, issueNumbers: [item.issueNumber], included: !item.included, note: item.overrideNote || "Adjusted in volume contents" })}>{item.included ? "Exclude" : "Restore"}</button> : null}</article>) : <div className="drawer-empty"><ListBullets size={26} weight="duotone" /><strong>No issue contents established</strong><span>Add the known issues below; they will be stored as a local correction.</span></div>}</div><form className="contents-add-form" onSubmit={addIssues}><h3>Add included issues</h3><label><span>Canonical series</span><GlassSelect label="Canonical series" value={seriesId} onChange={setSeriesId} className="glass-select--fill" options={data.seriesOptions.map((series) => ({ value: series.id, label: `${series.title}${series.year ? ` (${series.year})` : ""}` }))} /></label><label><span>Issue numbers</span><input value={issueInput} onChange={(event) => setIssueInput(event.target.value)} placeholder="1-6, 8, Annual 1" /></label><label><span>Correction note</span><input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Publisher contents page, checked manually…" /></label><button className="primary-button" disabled={busy}><Plus size={18} /> Add issues</button></form>{contents.hasOverrides ? <button className="danger-button contents-reset" disabled={busy} onClick={onReset}>Restore provider contents</button> : null}{inputError || error ? <p className="workbench-error" role="alert">{inputError || error}</p> : null}</section></div>;
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
      <DialogCloseButton onClose={onClose} label="Close full-series search" />
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
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal file-run-workbench" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="file-run-title" onMouseDown={(event) => event.stopPropagation()}><DialogCloseButton onClose={onClose} label="Close run assignment" /><span className="eyebrow">Change publication run</span><h2 id="file-run-title">{data.file.filename}</h2><p className="workbench-intro">This file is currently attached to <strong>{current.seriesTitle}</strong>. Changing its run updates only the catalog relationship—the comic file will not be renamed, moved, or modified.</p><form onSubmit={submit} className="run-assignment-form"><div className="run-assignment-choice"><button type="button" className={mode === "new" ? "active" : ""} onClick={() => setMode("new")}><Plus size={18} /><span><strong>Separate into a new run</strong><small>Use this when the file represents a distinct series, miniseries, or group of one-shots.</small></span></button><button type="button" className={mode === "existing" ? "active" : ""} onClick={() => setMode("existing")}><Books size={18} /><span><strong>Move to an existing run</strong><small>Attach this file to another canonical run already in the library.</small></span></button></div>{mode === "existing" ? <label className="form-field"><span>Publication run</span><GlassSelect label="Publication run" placeholder="Choose a run…" value={seriesId} onChange={setSeriesId} className="glass-select--fill" options={existingRuns.map((run) => ({ value: run.id, label: `${run.title}${run.year ? ` (${run.year})` : ""}` }))} /></label> : <div className="run-assignment-fields"><label className="form-field"><span>New run title</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Publication run title…" required /></label><label className="form-field"><span>Start year</span><input type="number" min="1800" max="2200" value={year} onChange={(event) => setYear(event.target.value)} /></label><label className="form-field"><span>Publisher</span><input value={publisher} onChange={(event) => setPublisher(event.target.value)} /></label></div>}<div className="run-assignment-note"><ShieldCheck size={20} weight="fill" /><span><strong>Reversible catalog change</strong><small>You can use Change run again later. Covers, volume metadata, and issue-content evidence remain attached to this file.</small></span></div>{error ? <p className="workbench-error" role="alert">{error}</p> : null}<div className="metadata-edit-actions"><button type="button" className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || (mode === "existing" ? !seriesId : !title.trim())}>{busy ? <LoadingSpinner size={18} /> : <ArrowRight size={18} />} {mode === "existing" ? "Move file" : "Create run and move file"}</button></div></form></section></div>;
}

// The way back in for a forgotten password. There is no email to send a reset
// link to, so recovery is a command on the server (see OPERATING.md, "Forgot
// your password"); this puts it where someone locked out will look.
const RESET_PASSWORD_COMMAND = "docker exec -it flipparr python app.py reset-password";

function ForgotPassword() {
  // "copied", "failed", or "" before either.
  const [copyState, setCopyState] = useState("");
  const commandRef = useRef(null);
  // The clipboard API exists only on HTTPS and localhost. Over plain HTTP the
  // button would do nothing, so it is left out and the command stays
  // selectable instead.
  const canCopy = typeof window !== "undefined" && window.isSecureContext
    && Boolean(navigator.clipboard?.writeText);
  useEffect(() => {
    if (copyState !== "copied") return undefined;
    const timer = setTimeout(() => setCopyState(""), 2000);
    return () => clearTimeout(timer);
  }, [copyState]);
  async function copy() {
    try {
      await navigator.clipboard.writeText(RESET_PASSWORD_COMMAND);
      setCopyState("copied");
    } catch {
      // A browser can still refuse (permissions, an unfocused page). Select
      // the command so copying it by hand is one keystroke, and say so --
      // a button that silently does nothing reads as broken.
      const selection = window.getSelection();
      if (commandRef.current && selection) selection.selectAllChildren(commandRef.current);
      setCopyState("failed");
    }
  }
  const copied = copyState === "copied";
  return <details className="login-help">
    <summary>Forgot your password?</summary>
    {/* Its own box: a <details> lays its children out in an internal slot,
        so layout set on the element itself never reaches them. */}
    <div className="login-help-body">
      <p>Run this where Flipparr is installed:</p>
      <div className="copy-field">
        {/* Each word kept whole: a line break inside "reset-password" reads
            as two words. The spaces stay real, so a hand selection copies
            the command exactly. */}
        <code ref={commandRef}>{RESET_PASSWORD_COMMAND.split(" ").map((word, index) =>
          <span key={index}>{index ? " " : ""}<span className="copy-field-word">{word}</span></span>)}</code>
        {canCopy ? <button type="button" className={copied ? "copied" : ""} onClick={copy}
          aria-label={copied ? "Copied" : "Copy command"} title={copied ? "Copied" : "Copy command"}>
          {copied ? <Check size={18} weight="bold" /> : <Copy size={18} />}
        </button> : null}
      </div>
      {copyState === "failed" ? <p role="status">Couldn’t copy it here. The command is selected, so copy it by hand.</p> : null}
      <p>It signs every device out. If your container isn’t named flipparr, use its name.</p>
      <span className="sr-only" aria-live="polite">{copied ? "Command copied" : ""}</span>
    </div>
  </details>;
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
      const result = await apiRequest("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      onSignedIn(result);
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
      <FlipparrMark size={72} />
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
    <ForgotPassword />
  </form></div>;
}

// URL <-> view. The server already serves the app shell for any path that is
// not under /api and not a real asset, and the auth gate lets those through so
// a login form can render, so these can be real paths rather than hash
// fragments. Hand-rolled because seven routes do not justify a router.
const ROUTE_BY_VIEW = {
  search: "/search",
  library: "/library",
  discover: "/discover",
  requests: "/pull-list",
  settings: "/settings",
  import: "/import",
  profile: "/profile",
};
// Paths this app used to answer on. A bookmark to one lands where it meant
// to rather than silently on the library.
const RETIRED_ROUTES = { "/requests": "requests", "/health": "settings" };
const VIEW_BY_ROUTE = Object.fromEntries(
  Object.entries(ROUTE_BY_VIEW).map(([view, path]) => [path, view])
);

// The drawer rides as a query parameter rather than a path segment: it can be
// open over the library, a search or a collection, so it is orthogonal to which
// view is showing.
// The views that carry a query: the Search page above 640px, and Discover,
// where a phone searches.
const SEARCH_VIEWS = new Set(["search", "discover"]);
// How often the library's own polling may refresh the bell.
const BELL_REFRESH_MIN_MS = 30_000;
const PHONE_QUERY = "(max-width: 640px)";
const isPhoneWidth = () => Boolean(window.matchMedia?.(PHONE_QUERY).matches);
function usePhoneWidth() {
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

function locationForState({ active, settingsSection, searchQuery, seriesId, readFileId }) {
  let path = ROUTE_BY_VIEW[active] || ROUTE_BY_VIEW.library;
  if (active === "settings" && settingsSection) path += `/${settingsSection}`;
  const params = new URLSearchParams();
  if (SEARCH_VIEWS.has(active) && searchQuery) params.set("q", searchQuery);
  if (seriesId) params.set("series", String(seriesId));
  // The reader is a parameter rather than a path, so Back closes it -- which
  // is what a phone's edge swipe is, and what a reader expects of it.
  if (readFileId) params.set("read", String(readFileId));
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

function stateFromLocation(pathname, search) {
  const params = new URLSearchParams(search || "");
  const segments = String(pathname || "").split("/").filter(Boolean);
  const first = `/${segments[0] || ""}`;
  const routed = VIEW_BY_ROUTE[first] || RETIRED_ROUTES[first] || "library";
  // A search is Discover's on a phone and the Search page's above 640px,
  // whichever address it came in on.
  const active = routed === "search" && isPhoneWidth() ? "discover"
    : routed === "discover" && params.get("q") && !isPhoneWidth() ? "search" : routed;
  const requestedSection = segments[1];
  return {
    active,
    // No section is the list of them on a phone; above 640px it opens the first.
    settingsSection: active === "settings" && SETTINGS_SECTIONS.some((item) => item.id === requestedSection)
      ? requestedSection
      : "",
    searchQuery: SEARCH_VIEWS.has(active) ? params.get("q") || "" : "",
    seriesId: params.get("series") || "",
    readFileId: params.get("read") || "",
  };
}

const BOOT_ROUTE = (() => {
  const route = stateFromLocation(window.location.pathname, window.location.search);
  // On a phone your profile is a sheet over the library, not a page. Decided
  // here, before anything reads the address: left to an effect, the boot's
  // own canonicalisation put /profile back into history, and every Back onto
  // that entry opened the sheet again.
  if (route.active === "profile" && isPhoneWidth()) {
    window.history.replaceState(null, "", ROUTE_BY_VIEW.library);
    return { ...route, active: "library", profileSheet: true };
  }
  return route;
})();

export function App() {
  const [active, setActive] = useState(BOOT_ROUTE.active);
  const [profileSheet, setProfileSheet] = useState(Boolean(BOOT_ROUTE.profileSheet));
  const [searchQuery, setSearchQuery] = useState(BOOT_ROUTE.searchQuery);
  // A phone has no Search page, and above 640px Discover shows only the week's
  // releases -- so a search follows the width it is read at: a /search link
  // opens on a phone as Discover's results, and Discover's results widen into
  // the Search page.
  //
  // Only when the width changes: choosing Discover from the sidebar with a
  // search still open goes to the releases, not back to the results.
  const phoneWidth = usePhoneWidth();
  useEffect(() => {
    if (phoneWidth && active === "search") setActive("discover");
    else if (!phoneWidth && active === "discover" && searchQuery) setActive("search");
  }, [phoneWidth]);
  // Arriving at /profile on a phone (a bookmark, a reload, a narrowed window)
  // is the library with your profile's sheet over it.
  // Reached later -- Back onto an old /profile entry, or a window narrowed
  // on the profile page -- the same: the entry becomes the library's and the
  // sheet opens over it, so no entry is left that reopens it on every Back.
  useEffect(() => {
    if (active !== "profile" || !phoneWidth) return;
    window.history.replaceState(null, "", ROUTE_BY_VIEW.library);
    setActive("library");
    setProfileSheet(true);
  }, [active, phoneWidth]);
  // Above 640px Discover is the releases alone; the search stays open on the
  // Search page, and Discover neither shows it nor carries it in its address.
  const viewQuery = active === "discover" && !phoneWidth ? "" : searchQuery;
  // A ?series= link cannot be honoured until the catalog it refers to exists.
  const [pendingSeriesId, setPendingSeriesId] = useState(BOOT_ROUTE.seriesId);
  // What is being read, if anything: a file id, from ?read=.
  const [readFileId, setReadFileId] = useState(BOOT_ROUTE.readFileId);
  // Leaving the reader is the one moment the continue shelf is certainly out
  // of date, so that is when it is asked again -- by the reader closing or by
  // Back, which is why this watches the file rather than the close button.
  // The page to open at, when the caller means a particular one rather than
  // wherever the comic was left.
  const [readFrom, setReadFrom] = useState(null);
  // Which comic was finished, rather than a flag: opening a different one
  // falsifies this in the same render that swaps the reader's file, so there
  // is no clean-up effect to get wrong.
  const [finishedFileId, setFinishedFileId] = useState("");
  const [readingVersion, setReadingVersion] = useState(0);
  const noteReadingChanged = useCallback(() => setReadingVersion((version) => version + 1), []);
  // Marking read or unread: this profile's own place, so it needs no
  // confirmation; the drawer and the grid re-read their state after.
  async function markIssueRead(issue, read) {
    try {
      await apiRequest(`/api/v1/files/${issue.fileId}/progress`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ read }) });
      noteReadingChanged();
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  async function markRunRead(series, read) {
    try {
      await apiRequest(`/api/v1/series/${series.id}/reading`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ read }) });
      noteReadingChanged();
      showToast(read ? `${series.title} marked as read` : `${series.title} marked as unread`);
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  /**
   * Open a comic. Callers know different things: the Continue shelf and the
   * issue rows know a file, a card in the grid may only know a run that has
   * never been opened. The run is resolved here rather than in the grid,
   * which would otherwise need the whole library's archives sniffed on every
   * visit to know what is readable.
   */
  const readComic = useCallback(async (target) => {
    // Opening a comic puts the drawer away. The reader sits *under* an open
    // drawer -- that is what lets the drawer open over a comic you are
    // reading -- so leaving it up would mean pressing Read and watching
    // nothing happen, with the reader hidden behind it.
    // "From the beginning" has to say so. Handing the reader a file id alone
    // gets the place that file was left at -- which, for the first issue of a
    // run you have read, is its last page.
    const open = (fileId) => {
      setReadFrom(target?.fromStart ? 0 : null);
      setReadFileId(String(fileId));
      setSelectedSeries(null);
      // Reading the issue you just finished keeps the same file id, so the
      // drawer would otherwise be left standing over page one.
      setFinishedFileId("");
    };
    const fileId = typeof target === "string" ? target : target?.id;
    if (fileId) { open(fileId); return; }
    if (!target?.runId) return;
    try {
      const data = await apiRequest(`/api/v1/series/${target.runId}/reading`);
      if (data?.resume?.fileId) open(data.resume.fileId);
      else showToast("There is nothing here that can be read yet.", "error");
    } catch (error) { showToast(error.message, "error"); }
  }, []);
  useEffect(() => {
    if (readFileId) return;
    setReadingVersion((version) => version + 1);
  }, [readFileId]);
  // Bumped when the address no longer names an open drawer, so the drawer can
  // play its exit animation rather than being removed from the tree outright.
  const [drawerDismissSignal, setDrawerDismissSignal] = useState(0);
  const [selectedSeries, setSelectedSeries] = useState(null);
  // What the drawer shows right now, for work that finishes later. A check,
  // a save or a sync that ended after the drawer was closed used to set the
  // run again and open the drawer on its own; refreshing is only for a drawer
  // that is still open on that run.
  const selectedSeriesRef = useRef(null);
  useEffect(() => { selectedSeriesRef.current = selectedSeries; }, [selectedSeries]);
  const refreshOpenSeries = useCallback((refreshed) => {
    if (refreshed && selectedSeriesRef.current && String(selectedSeriesRef.current.id) === String(refreshed.id)) {
      setSelectedSeries(refreshed);
    }
  }, []);
  const [selectedCollection, setSelectedCollection] = useState(null);
  const [seriesParentCollection, setSeriesParentCollection] = useState(null);
  const [collectionTab, setCollectionTab] = useState("overview");
  const [scanState, setScanState] = useState("idle");
  const [scanProgress, setScanProgress] = useState(null);
  const [toast, setToast] = useState("");
  const [toastTone, setToastTone] = useState("success");
  const [toastLeaving, setToastLeaving] = useState(false);
  const toastTimers = useRef([]);
  const [reviewFocus, setReviewFocus] = useState(null);
  const [requestFocus, setRequestFocus] = useState(null);
  // The bell's news, kept per profile on the server, with what the profile
  // has dismissed of the things needing attention (notifications.js).
  const [bellNews, setBellNews] = useState(() => lastAnswer("/api/v1/notifications") || { items: [], unread: 0, dismissed: [] });
  const [bellError, setBellError] = useState("");
  const [catalog, setCatalog] = useState(null);
  const [backendStatus, setBackendStatus] = useState("loading");
  const [authStatus, setAuthStatus] = useState(null);
  // The picker, opened over the app (Switch profile), and the Add sheet.
  const [picker, setPicker] = useState(null);
  const [addingProfile, setAddingProfile] = useState(false);
  const [profilesVersion, setProfilesVersion] = useState(0);
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
  const [mergeWorkbench, setMergeWorkbench] = useState(null);
  const [mergeBusy, setMergeBusy] = useState(false);
  const [mergeError, setMergeError] = useState("");
  async function loadSetupState() {
    try {
      const settings = await apiRequest("/api/v1/settings");
      setSetupCompleted(Boolean(settings?.setupCompleted));
    } catch {
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

  // News opens what it is about: the run that comics arrived in, or the
  // request that was decided.
  function openNews(target) {
    if (target?.view === "series") {
      const series = (catalog?.series || []).find((item) => String(item.id) === String(target.seriesId));
      navigate("library");
      if (series) openSeries(series);
      return;
    }
    if (target?.view === "requests") {
      setRequestFocus({ ...target.focus });
      navigate("requests");
    }
  }

  // Dismissals made here and not yet confirmed: an answer to some other
  // request (a refresh, a clear) must not put them back.
  const pendingDismissals = useRef(new Set());
  const withPending = (dismissed) => [...new Set([...(dismissed || []), ...pendingDismissals.current])];
  const bellRefreshedAt = useRef(0);
  const refreshBell = useCallback(async () => {
    try {
      const data = await apiRequest("/api/v1/notifications");
      bellRefreshedAt.current = Date.now();
      setBellNews({ ...data, dismissed: withPending(data.dismissed) });
      setBellError("");
      return data;
    } catch (error) {
      if (error.status !== 401) setBellError("Notifications could not be loaded. They will be tried again.");
      return null;
    }
  }, []);
  async function sendBell(path, body) {
    try {
      const result = await apiRequest(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      // Only the dismissals endpoint speaks for the dismissals.
      if (path.endsWith("/dismissed")) {
        setBellNews((current) => ({ ...current, dismissed: withPending(result.dismissed) }));
      } else if (result.items) {
        setBellNews((current) => ({ ...result, dismissed: current.dismissed }));
      } else {
        setBellNews((current) => ({ ...current, ...result, dismissed: current.dismissed }));
      }
      setBellError("");
      return result;
    } catch (error) {
      setBellError(error.message);
      refreshBell();
      return null;
    }
  }
  function changeDismissals({ add = [], remove = [] }) {
    // At once on screen; the server's answer settles it.
    add.forEach((key) => pendingDismissals.current.add(key));
    setBellNews((current) => ({
      ...current,
      dismissed: [...new Set([...(current.dismissed || []).filter((key) => !remove.includes(key)), ...add])],
    }));
    return sendBell("/api/v1/notifications/dismissed", { add, remove })
      .finally(() => add.forEach((key) => pendingDismissals.current.delete(key)));
  }

  const viewerIsAdmin = isAdmin(authStatus?.viewer);
  const attention = useMemo(
    () => needsAttention(catalog, bellNews.dismissed || [], { admin: viewerIsAdmin }),
    [catalog, bellNews.dismissed, viewerIsAdmin]);
  // The news is asked for when the library is -- an import that brought
  // comics changes both -- but not on every one of the library's 5-second
  // polls while something downloads: once every half minute from here, and
  // at once when the bell is opened or a profile signs in.
  const bellViewer = useRef(null);
  useEffect(() => {
    if (!catalog || !authStatus?.viewer) return;
    const changed = bellViewer.current !== authStatus.viewer.id;
    bellViewer.current = authStatus.viewer.id;
    if (changed || Date.now() - bellRefreshedAt.current >= BELL_REFRESH_MIN_MS) refreshBell();
  }, [catalog, authStatus?.viewer?.id]);
  // A dismissal whose notification is gone is forgotten: a retried job fails
  // again under the same id, and must be able to say so.
  useEffect(() => {
    if (!catalog || !viewerIsAdmin || !bellNews.dismissed?.length) return;
    const stale = staleDismissals(catalog, bellNews.dismissed);
    if (stale.length) changeDismissals({ remove: stale });
  }, [catalog, bellNews.dismissed, viewerIsAdmin]);
  // Dismissals this browser kept before the server did move up once, and
  // are forgotten here only once the server has them.
  useEffect(() => {
    if (!authStatus?.viewer) return;
    const legacy = readLegacyDismissed(profileStorage());
    if (!legacy.length) { forgetLegacyState(profileStorage()); return; }
    changeDismissals({ add: legacy }).then((result) => { if (result) forgetLegacyState(profileStorage()); });
  }, [authStatus?.viewer?.id]);

  const bell = {
    attention, activity: bellNews, error: bellError,
    onRefresh: refreshBell,
    onOpen: (item) => (item.target ? openNews(item.target) : openNotification(item)),
    onDismiss: (item) => changeDismissals({ add: [item.id] }),
    onSeen: () => sendBell("/api/v1/notifications/read", { all: true }),
    onClear: (item) => {
      setBellNews((current) => ({ ...current, items: current.items.filter((row) => row.id !== item.id) }));
      return sendBell("/api/v1/notifications/clear", { ids: [item.id] });
    },
    // Clear all clears the news. What needs someone stays until it is dealt
    // with, or dismissed one by one.
    onClearAll: () => {
      setBellNews((current) => ({ ...current, items: [], unread: 0 }));
      sendBell("/api/v1/notifications/clear", { all: true });
    },
    onRetry: async (item) => {
      try {
        const result = await apiRequest(`/api/v1/acquisition-jobs/${item.retryJobId}/retry`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
        });
        showToast(result.detail || "Trying again");
        await loadCatalog();
      } catch (error) {
        showToast(error.message, "error");
      }
    },
  };

  function navigate(id, sectionId) {
    // A phone opens your profile as a sheet over this page, not as a page.
    if (id === "profile" && isPhoneWidth()) { setProfileSheet(true); return; }
    setActive(id); setSelectedSeries(null); setSelectedCollection(null); setSeriesParentCollection(null);
    // Settings shows one section at a time, so a deep link selects the section
    // rather than scrolling to it.
    if (id === "settings" && sectionId) setSettingsSection(sectionId);
    // Choosing Settings again, from inside a section, goes back to the list,
    // as tapping the tab you are on does on an iPhone.
    else if (id === "settings" && active === "settings") setSettingsSection("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  function openSearch(value) {
    const cleaned = String(value || "").trim();
    // An empty value is the clear button, not a rejected search.
    if (cleaned && cleaned.length < 2) return;
    setSearchQuery(cleaned);
    navigate(isPhoneWidth() ? "discover" : "search");
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
  function openSeries(series) {
    setSelectedCollection(null);
    setSeriesParentCollection(null);
    setSelectedSeries(series);
  }
  function openCollection(collection) { if (!catalog?.collectedEditionsEnabled) return; setSelectedSeries(null); setSeriesParentCollection(null); setCollectionTab("overview"); setSelectedCollection(collection); }
  function openCollectionRun(series) { setSeriesParentCollection(selectedCollection); setSelectedCollection(null); setSelectedSeries(series); }
  function returnToCollection() {
    if (!seriesParentCollection) return;
    const refreshed = catalog?.families?.find((collection) => collection.id === seriesParentCollection.id) || seriesParentCollection;
    setSelectedSeries(null);
    setSeriesParentCollection(null);
    setSelectedCollection(refreshed);
  }
  // A new message replaces the one showing, and restarts its time; the last
  // 300ms play its exit rather than cutting it off.
  // `tone` is "success" (a green check) or "error" (a red warning): a failed
  // action said with a check read as though it had worked.
  function showToast(message, tone = "success") {
    toastTimers.current.forEach((timer) => window.clearTimeout(timer));
    setToast(message);
    setToastTone(tone);
    setToastLeaving(false);
    toastTimers.current = [
      window.setTimeout(() => setToastLeaving(true), 2900),
      window.setTimeout(() => { setToast(""); setToastLeaving(false); }, 3200),
    ];
  }
  async function loadAuthStatus() {
    try {
      const status = await apiRequest("/api/v1/auth/status");
      // A different profile from the one this page was drawn for: start
      // again as that profile, so its storage and nothing else is read.
      const viewerId = status?.viewer?.id ?? null;
      if (viewerId && viewerId !== storageProfile() && storable()) {
        enterProfile(viewerId);
        return status;
      }
      setAuthStatus(status);
      if (viewerId) syncReaderPrefs();
      return status;
    } catch {
      // Treat an unreachable status endpoint as "no gate", so a backend problem
      // surfaces as the existing offline banner rather than a stuck login form.
      setAuthStatus({ method: "none", authenticated: true });
      return null;
    }
  }
  async function signOut({ forgetDevice = false } = {}) {
    try {
      await apiRequest("/api/v1/auth/logout", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ forgetDevice }),
      });
    } catch {
      // Even if the call fails the session may already be gone; the fresh
      // page decides what the user actually sees -- the picker on a shared
      // device, the sign-in form elsewhere.
    }
    enterProfile(null);
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
      showToast(error.message, "error");
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
      showToast(error.message, "error");
      throw error;
    }
  }
  async function removeLibraryRoot(root) {
    try {
      await apiRequest(`/api/v1/library-roots/${root.id}`, { method: "DELETE" });
      await loadCatalog();
      showToast("Folder removed from Flipparr · comic files were untouched");
    } catch (error) {
      showToast(error.message, "error");
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
      showToast(error.message, "error");
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
      refreshOpenSeries(refreshed);
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
      refreshOpenSeries(refreshed);
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
      refreshOpenSeries(refreshed);
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
    } catch (error) { showToast(error.message, "error"); }
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
      refreshOpenSeries(updated);
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
      refreshOpenSeries(refreshed);
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
      refreshOpenSeries(refreshed);
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
      showToast(error.message, "error");
    }
  }
  async function openFileRunWorkbench(file) {
    setFileRunError("");
    try {
      const data = await apiRequest(`/api/v1/files/${file.id}`);
      setFileRunWorkbench(data);
    } catch (error) {
      showToast(error.message, "error");
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
    if (refreshed) refreshOpenSeries(refreshed);
    else if (selectedSeriesRef.current) setSelectedSeries(null);
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
      refreshOpenSeries(refreshedSeries);
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
  // A reader's Follow and Pull ask the admin instead. The answer is a
  // request waiting (or, for a reader the admin trusts, started at once), and
  // the caller's card settles on "Requested".
  async function askAdmin(body) {
    try {
      const result = await apiRequest("/api/v1/member-requests", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      await loadCatalog();
      if (result.status === "already") {
        showToast(result.message);
        return { ok: true, result };
      }
      const started = result.status === "approved";
      const follow = isFollow(result);
      showToast(started ? (follow ? `Following ${result.title}` : `${result.title} · on its way`)
        : follow ? `Asked to follow ${result.title} · waiting for approval` : `${result.title} requested · waiting for approval`);
      return { ok: true, result, requested: !started };
    } catch (error) {
      showToast(error.message, "error");
      return { ok: false, error: error.message };
    }
  }
  // Approve, decline or cancel a reader's request. Returns an error to show on
  // the card, or nothing.
  async function decideMemberRequest(request, action, body) {
    try {
      const result = await apiRequest(`/api/v1/member-requests/${request.id}/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
      });
      await loadCatalog();
      if (action === "approve") {
        showToast(result.status === "failed" ? `${request.title} couldn't be added · ${result.failure || "try again"}`
          : `${request.title} approved · on its way`, result.status === "failed" ? "error" : undefined);
      } else {
        showToast(action === "decline" ? `${request.title} declined` : `${request.title} request cancelled`);
      }
      // A failed approval is on the card itself (status and reason), so it is
      // not said again beneath it.
      return "";
    } catch (error) {
      await loadCatalog();
      return error.message;
    }
  }
  async function createAcquisitionRequest(target) {
    const collection = target?.isCollectionSeries ? target.collection : target;
    const isCollection = Boolean(collection?.runs);
    const requestKey = `${isCollection ? "collection" : "series"}:${collection.id}`;
    if (!viewerIsAdmin) {
      setRequestBusyKey(requestKey);
      const asked = await askAdmin(isCollection ? { kind: "collection", collectionId: collection.id }
        : { kind: "run", seriesId: collection.id });
      setRequestBusyKey("");
      return asked;
    }
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
        refreshOpenSeries(refreshedSeries);
      }
      const wantedCount = Number(request.wantedIssueCount || 0);
      showToast(wantedCount
        ? `Following ${request.title} · ${wantedCount} missing issue${wantedCount === 1 ? "" : "s"} added to Wanted`
        : `Following ${request.title} · you’re up to date`);
      return { ok: true, request };
    } catch (error) {
      showToast(error.message, "error");
      return { ok: false, error: error.message };
    } finally {
      setRequestBusyKey("");
    }
  }
  async function pullDiscoveredIssue(issue) {
    if (!viewerIsAdmin) {
      return askAdmin({ kind: "discover_issues", provider: "metron", providerSeriesId: issue.providerSeriesId,
        providerIssueId: issue.providerIssueId,
        numbers: [issue.number], title: issue.seriesTitle || issue.title, query: issue.seriesTitle,
        publisher: issue.publisher, cover: issue.cover });
    }
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
      showToast(error.message, "error");
      return { ok: false, error: error.message };
    }
  }

  async function pullDiscoveredIssues({ provider, providerSeriesId, numbers, released, title, query, publisher, cover, year }) {
    if (!viewerIsAdmin) {
      return askAdmin({ kind: "discover_issues", provider, providerSeriesId, numbers, released: Boolean(released),
        title, query: query || title, publisher, cover, year });
    }
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
      showToast(error.message, "error");
      return { ok: false, error: error.message };
    }
  }

  async function pullStoryArc({ arcId, name, cover, issueCount }) {
    if (!viewerIsAdmin) {
      return askAdmin({ kind: "discover_arc", provider: "metron", arcId, title: name, cover, issueCount });
    }
    try {
      const result = await apiRequest("/api/v1/discover/pull-arc", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ arcId }),
      });
      await loadCatalog();
      const failed = (result.failed || []).map((item) => item.title).filter(Boolean);
      showToast(`${name} · ${result.count} issue${result.count === 1 ? "" : "s"} added to your Pull List${failed.length ? ` · ${failed.join(", ")} could not be pulled` : ""}`,
        failed.length ? "error" : undefined);
      return { ok: true, result };
    } catch (error) {
      showToast(error.message, "error");
      return { ok: false, error: error.message };
    }
  }
  async function requestDiscoveredSeries(target) {
    if (!viewerIsAdmin) {
      return askAdmin({ kind: "discover_run", provider: target.provider, providerSeriesId: target.providerSeriesId,
        title: target.title, query: target.query || target.title, publisher: target.publisher,
        cover: target.cover, year: Number.isInteger(target.year) ? target.year : undefined });
    }
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
      showToast(error.message, "error");
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
        refreshOpenSeries(refreshed);
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
    } catch (error) { showToast(error.message, "error"); }
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
    } catch (error) { showToast(error.message, "error"); }
  }
  async function setSeriesFormat(series, format) {
    try {
      await apiRequest(`/api/v1/series/${series.id}/format`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ format }),
      });
      await loadCatalog();
      showToast(format === "manga" ? `${series.title} is filed as manga` : `${series.title} is filed as a comic`);
    } catch (error) { showToast(error.message, "error"); }
  }
  /**
   * Which way this run's pages turn, whatever it is filed as.
   *
   * null restores "follow the medium". The format goes along for the ride
   * because the route sets both, and sending the run's current one leaves it
   * where it is.
   */
  /** Your rating for a run or one of its issues; null takes it back. */
  async function rateSeries(series, rating) {
    await saveRating(`/api/v1/series/${series.id}/rating`, rating, series.title);
  }
  async function rateIssue(issue, rating) {
    await saveRating(`/api/v1/issues/${issue.id}/rating`, rating,
      `${issue.contextLabel || "Issue"} #${issue.number}`);
  }
  async function saveRating(path, rating, subject) {
    try {
      await apiRequest(path, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === selectedSeries?.id);
      refreshOpenSeries(refreshed);
      showToast(rating ? `${subject} rated ${rating} star${rating === 1 ? "" : "s"}` : `Rating removed from ${subject}`);
    } catch (error) { showToast(error.message, "error"); }
  }
  async function setSeriesAgeRating(series, rating) {
    try {
      await apiRequest(`/api/v1/series/${series.id}/age-rating`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rating }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === series.id);
      refreshOpenSeries(refreshed);
      showToast(rating ? `${series.title} rated ${RATING_LABELS[rating]}` : `${series.title} uses the rating found for it`);
    } catch (error) { showToast(error.message, "error"); }
  }
  async function setSeriesDirection(series, direction) {
    try {
      const saved = await apiRequest(`/api/v1/series/${series.id}/format`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ format: series.medium, readingDirection: direction }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === series.id);
      refreshOpenSeries(refreshed);
      showToast(saved.readingDirection
        ? `${series.title} reads ${saved.readingDirection === "rtl" ? "right to left" : "left to right"}`
        : `${series.title} follows its medium again`);
    } catch (error) { showToast(error.message, "error"); }
  }
  // Irreversible, so it says exactly what goes -- files and space -- before
  // it asks, and the server checks every path again before deleting any.
  async function removeSeries(series) {
    try {
      const plan = await apiRequest(`/api/v1/series/${series.id}/removal`);
      if (plan.activeDownloads) {
        showToast("Something for this run is still downloading. Remove it once that has finished.", "error");
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
    } catch (error) { showToast(error.message, "error"); }
  }
  async function openCoverWorkbench(file) {
    setCoverError("");
    try {
      const data = await apiRequest(`/api/v1/files/${file.id}`);
      setCoverWorkbench(data);
    } catch (error) { showToast(error.message, "error"); }
  }
  async function finishCoverChange(message) {
    const selectedId = selectedSeries?.id;
    const data = await loadCatalog();
    const refreshed = data?.series?.find((item) => item.id === selectedId);
    refreshOpenSeries(refreshed);
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
  // `runId` is passed by the drawer's Edit panel, which hosts the same picker
  // without opening the workbench these were written for.
  async function saveSeriesBackdrop(body, message, runId = backdropWorkbench?.series?.id) {
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
    } catch (error) { showToast(error.message, "error"); }
  }
  async function finishSeriesCoverChange(message) {
    const selectedId = selectedSeries?.id;
    const data = await loadCatalog();
    const refreshed = data?.series?.find((item) => item.id === selectedId);
    refreshOpenSeries(refreshed);
    setSeriesCoverWorkbench(null);
    showToast(message);
  }
  async function selectSeriesCover(source, url, fileId, runId = seriesCoverWorkbench?.series?.id) {
    setCoverBusy(true); setCoverError("");
    try {
      await apiRequest(`/api/v1/series/${runId}/cover`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source, url, fileId }) });
      await finishSeriesCoverChange(source === "auto" ? "Automatic cover selection restored" : "Series cover updated");
    } catch (error) { setCoverError(error.message); }
    setCoverBusy(false);
  }
  async function uploadSeriesCover(file, runId = seriesCoverWorkbench?.series?.id) {
    setCoverBusy(true); setCoverError("");
    try {
      await apiRequest(`/api/v1/series/${runId}/cover/upload`, { method: "POST", headers: { "Content-Type": file.type }, body: file });
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
      refreshOpenSeries(refreshed);
      showToast(`No longer following ${series.title}`);
    } catch (error) { showToast(error.message, "error"); }
    setUnfollowBusy(false);
  }
  // From a Discover drawer: the run as the library knows it, by its id.
  async function unfollowDiscoveredRun({ runId, title }) {
    if (!runId) return { ok: false };
    try {
      await apiRequest(`/api/v1/series/${runId}/monitoring`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ monitored: false }),
      });
      await loadCatalog();
      showToast(`No longer following ${title}`);
      return { ok: true };
    } catch (error) {
      showToast(error.message, "error");
      return { ok: false, error: error.message };
    }
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
    } catch (error) { showToast(error.message, "error"); }
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
      refreshOpenSeries(refreshed);
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
    } catch (error) { showToast(error.message, "error"); }
  }
  async function finishContentsChange(data, message) {
    const selectedId = selectedSeries?.id;
    const selectedCollectionId = selectedCollection?.id;
    const catalogData = await loadCatalog();
    const refreshed = catalogData?.series?.find((item) => item.id === selectedId);
    refreshOpenSeries(refreshed);
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
  const locationRef = useRef(window.location.pathname + window.location.search);
  useEffect(() => {
    if (pendingSeriesId) return;
    const target = locationForState({
      active, settingsSection, searchQuery: viewQuery, seriesId: selectedSeries?.id, readFileId,
    });
    locationRef.current = target;
    if (target !== window.location.pathname + window.location.search) {
      // Over a dialog's own entry rather than on top of it: the dialog is
      // going with the view, and its entry must not be left to a Back press.
      if (window.history.state?.dialog) window.history.replaceState(null, "", target);
      else window.history.pushState(null, "", target);
    }
  }, [active, settingsSection, viewQuery, selectedSeries?.id, readFileId, pendingSeriesId]);
  // Back and forward move between views, and close the drawer when the entry
  // being returned to did not have it open.
  //
  // Except when Back was not meant: on a phone the reader's "previous" tap
  // zone runs to the screen's left edge, and iOS takes a tap there that
  // drifts as its own Back gesture -- the touch is cancelled, the entry
  // popped, and the reader gone mid-issue. An edge touch the system
  // cancelled a moment before the pop is that gesture; the reader keeps its
  // entry and stays. The close button, Esc, a pull down and a Back with no
  // edge touch behind it still close it.
  const edgeTouch = useRef({ startedAt: 0, cancelledAt: 0 });
  useEffect(() => {
    function onPointerDown(event) {
      if (event.pointerType === "mouse") return;
      edgeTouch.current.startedAt = isEdgeTouch(event.clientX, window.innerWidth) ? Date.now() : 0;
    }
    function onPointerCancel() {
      if (edgeTouch.current.startedAt) edgeTouch.current.cancelledAt = Date.now();
    }
    window.addEventListener("pointerdown", onPointerDown, { passive: true, capture: true });
    window.addEventListener("pointercancel", onPointerCancel, { passive: true, capture: true });
    return () => {
      window.removeEventListener("pointerdown", onPointerDown, { capture: true });
      window.removeEventListener("pointercancel", onPointerCancel, { capture: true });
    };
  }, []);
  useEffect(() => {
    function applyLocation() {
      const next = stateFromLocation(window.location.pathname, window.location.search);
      const reading = new URLSearchParams(locationRef.current.split("?")[1] || "").get("read");
      if (reading && !next.readFileId && isStolenBack(edgeTouch.current.cancelledAt, Date.now())) {
        edgeTouch.current.cancelledAt = 0;
        window.history.pushState(null, "", locationRef.current);
        return;
      }
      // An entry marked for a dialog nobody has open -- development's double
      // mount leaves one, a reload keeps one -- is not a place; Back goes on.
      const mark = window.history.state?.dialog;
      if (mark && !openDialogs.some((node) => dialogMarks.get(node) === mark)) {
        window.history.back();
        return;
      }
      // Back took the dialog in front's own entry: the dialog closes and the
      // page stays, its address untouched. Only if Back went further -- to
      // another page -- does the location apply as well.
      if (dialogLeftByBack()) {
        closeTopDialog();
        if (window.location.pathname + window.location.search === locationRef.current) return;
      }
      setActive(next.active);
      setSettingsSection(next.settingsSection);
      setSearchQuery(next.searchQuery);
      setReadFileId(next.readFileId);
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
  // A settings section opens at its top, and the list is at its top on the
  // way back: a long section left scrolled would otherwise hand its offset
  // to the short list, which then sat under the header.
  useEffect(() => { window.scrollTo(0, 0); }, [settingsSection]);
  // Pull to refresh, for the app on a Home Screen: installed, it has no
  // browser chrome and so none of Safari's reload -- the pull was never the
  // page's. This one rides the phone's own rubber band: while a finger is
  // down at the top, the stretch it reports is the pull's depth, past a
  // threshold the pill says so, and letting go there reloads. Nothing is
  // moved by hand, so nothing repaints per frame. A Safari tab keeps its
  // own, and a page a dialog holds is left alone.
  const [pull, setPull] = useState("idle");
  useEffect(() => {
    const installed = window.matchMedia?.("(display-mode: standalone)").matches || window.navigator.standalone === true;
    if (!installed) return undefined;
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
      window.location.reload();
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
  const logicalSeries = useMemo(() => logicalCatalogSeries(catalog, visibleSeries), [catalog, backendStatus]);
  const logicalSeriesCount = logicalSeries.length;
  async function switchToProfile(profile) {
    // A profile that asks for something asks on the picker; one that does
    // not opens at once.
    if (isLocked(profile)) { setPicker({ ask: profile }); return; }
    try {
      const result = await apiRequest("/api/v1/profiles/switch", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ userId: profile.id }),
      });
      enterProfile(result.viewer.id);
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  const header = {
    bell,
    query: SEARCH_VIEWS.has(active) ? viewQuery : "", onSearch: openSearch, onClearSearch: () => openSearch(""),
    profile: {
      household: Boolean(authStatus?.household), method: authStatus?.method, version: profilesVersion,
      onOpenProfile: () => navigate("profile"), onSwitchTo: switchToProfile, onPicker: () => setPicker({}),
      onAdd: () => setAddingProfile(true), onSignOut: () => signOut({ forgetDevice: true }),
    },
  };
  // What is being read, named: the run it belongs to and the file's own name.
  const readingSeries = readFileId
    ? (catalog?.series || []).find((item) => (item.fileDetails || []).some((file) => String(file.id) === String(readFileId)))
    : null;
  const readingTitle = readFileId
    ? (readingSeries?.fileDetails || []).find((file) => String(file.id) === String(readFileId))?.filename || readingSeries?.title || "Reading"
    : "";
  // What the reader needs when a comic ends: the issue it was, so it can be
  // rated, and the one after it, so finishing is not a dead end.
  const readingIssues = readingSeries?.issues || [];
  const readingIssue = readFileId
    ? readingIssues.find((issue) => String(issue.fileId) === String(readFileId)) || null
    : null;
  const readingNext = readingIssue
    ? readingIssues.slice(readingIssues.indexOf(readingIssue) + 1).find((issue) => issue.fileId) || null
    : null;
  const finished = Boolean(readFileId) && finishedFileId === readFileId;
  const navActive = active === "import" ? "settings" : active;
  useEffect(() => { loadAuthStatus(); }, []);
  // A shared device with more than one profile asks who is reading.
  if (authStatus?.profileRequired) {
    return <WhoIsReadingView signInAvailable={authStatus.method === "forms"} />;
  }
  if (authStatus && authStatus.method === "forms" && !authStatus.authenticated) {
    return <LoginView onSignedIn={(result) => enterProfile(result?.viewer?.id ?? null)} />;
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
  return <ViewerContext.Provider value={authStatus?.viewer || null}><CollectedEditionsContext.Provider value={Boolean(catalog?.collectedEditionsEnabled)}><HeaderContext.Provider value={header}><div className="app-shell"><Nav active={navActive} onNavigate={navigate} catalog={catalog} backendStatus={backendStatus} logicalSeriesCount={logicalSeriesCount} authStatus={authStatus} onSignOut={signOut} scanning={scanState === "scanning" || Boolean(catalog?.activeScan)} onScanLibrary={() => scanLibrary()} /><main className="main-content"><div className="page-view" key={active}>{catalog?.collectedEditionsEnabled ? <div className="collected-editions-notice"><WarningCircle size={17} weight="fill" /> <span>Collected-edition support is on. Trades, hardcovers and omnibuses have less complete metadata and file availability than Issues, and never fulfill Issue ownership or acquisition.</span></div> : null}{active === "profile" ? <ProfileView onRead={readComic} onNavigate={navigate} authStatus={authStatus} onSwitch={() => setPicker({})} /> : null}{active === "library" ? <LibraryView onNavigate={navigate} onOpenSeries={openSeries} onOpenCollection={openCollection} onSearch={openSearch} onRead={readComic} readingVersion={readingVersion} catalog={catalog} backendStatus={backendStatus} /> : null}{SEARCH_VIEWS.has(active) && !can(authStatus?.viewer, "nav.discover") ? <><PageHeader title="Discover" /><div className="empty-state"><MagnifyingGlass size={35} weight="duotone" /><strong>Discover is off for this profile</strong><span>New comics come through the admin.</span></div></> : null}{SEARCH_VIEWS.has(active) && can(authStatus?.viewer, "nav.discover") ? <DiscoverView key={active} mode={active} query={viewQuery} catalog={catalog} backendStatus={backendStatus} onSearch={openSearch} onClearSearch={() => openSearch("")} onOpenSeries={openSeries} onOpenCollection={openCollection} onDiscoverRequest={requestDiscoveredSeries} onUnfollowRun={unfollowDiscoveredRun} onPullIssue={pullDiscoveredIssue} onPullIssues={pullDiscoveredIssues} onPullArc={pullStoryArc} /> : null}{active === "import" ? <ImportLibraryView onNavigate={navigate} onStartInventory={scanLibrary} onScanLibrary={() => scanLibrary()} onUpdateRoot={updateLibraryRoot} onRemoveRoot={removeLibraryRoot} catalog={catalog} backendStatus={backendStatus} scanState={scanState} scanProgress={scanProgress} /> : null}{active === "requests" ? (viewerIsAdmin ? <RequestsView catalog={catalog} backendStatus={backendStatus} focus={requestFocus} onCancelReplacement={cancelFileReplacement} onDeletePull={deletePull} onRefresh={loadCatalog} onDecide={decideMemberRequest} /> : <MyRequestsView catalog={catalog} backendStatus={backendStatus} focus={requestFocus} onDecide={decideMemberRequest} />) : null}{active === "settings" ? <SettingsView catalog={catalog} backendStatus={backendStatus} logicalSeriesCount={logicalSeriesCount} onNavigate={navigate} onAuthChanged={loadAuthStatus} onSignOut={signOut} authStatus={authStatus} section={settingsSection} onSectionChange={setSettingsSection} health={{ items: catalog?.inbox ?? [], loading: catalogPending(catalog, backendStatus), focus: reviewFocus, backendStatus, onResolve: resolveReview, onReplace: openReplacementRequest }} onScanLibrary={() => scanLibrary()} scanState={scanState} scanProgress={scanProgress} /> : null}</div></main>{readFileId ? <ReaderView fileId={readFileId} title={readingTitle} medium={readingSeries?.medium} directionOverride={readingSeries?.readingDirection} startPage={readFrom} behind={Boolean(selectedSeries) || finished} onFinish={() => setFinishedFileId(readFileId)} onProgressSaved={noteReadingChanged} onOpenRun={readingSeries ? () => openSeries(readingSeries) : undefined} onClose={() => { setReadFileId(""); setReadFrom(null); setFinishedFileId(""); }} /> : null}{finished ? <FinishDrawer key={readFileId} series={readingSeries} issue={readingIssue} nextIssue={readingNext} medium={readingSeries?.medium} title={readingTitle} readingVersion={readingVersion} onRateIssue={rateIssue} onRead={readComic} onOpenSeries={openSeries} onClose={() => setFinishedFileId("")} /> : null}{selectedSeries ? <SeriesDrawer key={selectedSeries.id} readingVersion={readingVersion} onMarkIssue={markIssueRead} onMarkRun={markRunRead} coverBusy={coverBusy} coverError={coverError} onSelectSeriesCover={selectSeriesCover} onUploadSeriesCover={uploadSeriesCover} backdropBusy={backdropBusy} backdropError={backdropError} onSaveBackdrop={saveSeriesBackdrop} series={selectedSeries} families={catalog?.families || []} allSeries={visibleSeries} parentCollection={seriesParentCollection} dismissSignal={drawerDismissSignal} onBack={returnToCollection} onClose={() => { setSelectedSeries(null); setSeriesParentCollection(null); }} onRead={readComic} onSetAgeRating={setSeriesAgeRating} onRequest={() => createAcquisitionRequest(selectedSeries)} requested={waitingKeys(catalog?.memberRequests).has(`run:${selectedSeries.id}`)} onViewRequests={() => navigate("requests")} requestBusy={requestBusyKey === `series:${selectedSeries.id}`} onAddAlias={addSeriesAlias} onSyncIssues={syncSeriesIssues} onFindRun={openSeriesRunWorkbench} onMergeRun={openSeriesMergeWorkbench} onRebuildRun={rebuildSeriesRun} rebuilding={rebuildingRun} rebuildResult={rebuildResult} onCreateFamily={createSeriesFamily} onSetFamily={setSeriesFamily} onOpenWorkbench={openFileWorkbench} onOpenCover={openCoverWorkbench} onChangeSeriesCover={openSeriesCoverWorkbench} onFixSeriesMatch={openSeriesMatchWorkbench} onSetFormat={setSeriesFormat} onSetDirection={setSeriesDirection} onRate={rateSeries} onRateIssue={rateIssue} onRemove={removeSeries} onUnfollow={unfollowSeries} unfollowBusy={unfollowBusy} onOpenContents={openContentsWorkbench} onChangeRun={openFileRunWorkbench} onEditIssue={openIssueWorkbench} onReplace={openReplacementRequest} onOpenSeries={openSeries} onChangeBackdrop={(item, current) => { setBackdropError(""); setBackdropWorkbench({ series: item, current }); }} backdropVersion={backdropVersion} /> : null}{selectedCollection ? <CollectionDrawer collection={selectedCollection} tab={collectionTab} onTabChange={setCollectionTab} onClose={() => setSelectedCollection(null)} onFindStructure={openStoryStructure} onOpenSeries={openCollectionRun} onOpenContents={openContentsWorkbench} onRequest={() => createAcquisitionRequest(selectedCollection)} requested={waitingKeys(catalog?.memberRequests).has(`collection:${selectedCollection.id}`)} onViewRequests={() => navigate("requests")} requestBusy={requestBusyKey === `collection:${selectedCollection.id}`} onEditIssue={openIssueWorkbench} onUnfollow={unfollowCollection} unfollowBusy={unfollowBusy} /> : null}{workbench ? <MetadataWorkbench data={workbench.data} mode={workbench.mode} busy={workbenchBusy} error={workbenchError} onClose={() => setWorkbench(null)} onSave={saveFileMetadata} onMatch={applyFileMatch} onSearch={searchFileMatches} onReset={resetFileMetadata} /> : null}{issueWorkbench ? <IssueMetadataWorkbench issue={issueWorkbench} busy={issueBusy} error={issueError} onClose={() => setIssueWorkbench(null)} onSave={saveIssueMetadata} onReset={resetIssueMetadata} /> : null}{coverWorkbench ? <CoverWorkbench data={coverWorkbench} busy={coverBusy} error={coverError} onClose={() => setCoverWorkbench(null)} onSelect={selectFileCover} onUpload={uploadFileCover} /> : null}{matchWorkbench ? <SeriesMatchWorkbench data={matchWorkbench} loading={matchLoading} busy={matchBusy} error={matchError} onClose={() => setMatchWorkbench(null)} onSearch={searchSeriesMatches} onConfirm={confirmSeriesMatch} /> : null}{seriesCoverWorkbench ? <CoverWorkbench data={seriesCoverWorkbench} title={seriesCoverWorkbench.series.title} busy={coverBusy} error={coverError} onClose={() => setSeriesCoverWorkbench(null)} onSelect={selectSeriesCover} onUpload={uploadSeriesCover} /> : null}{backdropWorkbench ? <BackdropWorkbench series={backdropWorkbench.series} current={backdropWorkbench.current} busy={backdropBusy} error={backdropError} onClose={() => setBackdropWorkbench(null)} onChoose={(fileId, page) => saveSeriesBackdrop({ fileId, page }, "Header background updated")} onAutomatic={() => saveSeriesBackdrop({ source: "auto" }, "Automatic background restored")} /> : null}{contentsWorkbench ? <VolumeContentsWorkbench data={contentsWorkbench} busy={contentsBusy} error={contentsError} onClose={() => setContentsWorkbench(null)} onChange={changeCollectionContents} onReset={resetCollectionContents} /> : null}{runWorkbench ? <SeriesRunWorkbench data={runWorkbench} loading={runLoading} busy={runBusy} error={runError} onClose={() => setRunWorkbench(null)} onConfirm={confirmSeriesRun} onBuildCollection={buildSeriesCollection} /> : null}{fileRunWorkbench ? <FileRunWorkbench data={fileRunWorkbench} busy={fileRunBusy} error={fileRunError} onClose={() => setFileRunWorkbench(null)} onMove={moveFileToRun} /> : null}{structureWorkbench ? <StoryStructureWorkbench data={structureWorkbench} busy={structureBusy} error={structureError} onClose={() => setStructureWorkbench(null)} onSave={saveStoryStructure} /> : null}{mergeWorkbench ? <SeriesMergeWorkbench data={mergeWorkbench} busy={mergeBusy} error={mergeError} onClose={() => setMergeWorkbench(null)} onTargetChange={(targetId) => targetId ? previewSeriesMerge(mergeWorkbench.source, targetId, mergeWorkbench.candidates) : setMergeWorkbench((current) => ({ ...current, targetId: "", preview: null }))} onConfirm={confirmSeriesMerge} /> : null}{replacementFile ? <ReplacementModal file={replacementFile} busy={replacementBusy} error={replacementError} onClose={() => setReplacementFile(null)} onSubmit={createFileReplacement} /> : null}{pull !== "idle" ? <div className="pull-refresh" role="status" aria-live="polite">{pull === "refreshing" ? <LoadingSpinner size={16} /> : <ArrowsClockwise size={16} />} {pull === "refreshing" ? "Refreshing…" : "Release to refresh"}</div> : null}{profileSheet ? <ProfileSheet onClose={() => setProfileSheet(false)} onRead={readComic} onNavigate={navigate} authStatus={authStatus} onSwitch={() => setPicker({})} /> : null}{picker ? <WhoIsReadingView key={profilesVersion} overlay current={authStatus?.viewer?.id ?? null} ask={picker.ask || null}
      onClose={() => setPicker(null)} canAdd={isAdmin(authStatus?.viewer)} onAdd={() => setAddingProfile(true)} /> : null}{addingProfile ? <AddProfileSheet onClose={() => setAddingProfile(false)}
      onAdded={() => { setAddingProfile(false); setProfilesVersion((value) => value + 1); showToast("Profile added"); }} /> : null}{toast ? <div className={`toast toast--${toastTone}${toastLeaving ? " leaving" : ""}`} role={toastTone === "error" ? "alert" : "status"} key={toast}>{toastTone === "error" ? <WarningCircle size={20} weight="fill" /> : <CheckCircle size={20} weight="fill" />} {toast}</div> : null}</div></HeaderContext.Provider></CollectedEditionsContext.Provider></ViewerContext.Provider>;
}
