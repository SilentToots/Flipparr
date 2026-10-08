// The reader: a comic, full screen, its settings drawer and the panel editor.
// A module of its own so the web app and Flipparr Reader (the iPad and iPhone
// app, docs/OFFLINE_READING_PLAN.md) draw the same reader from one source. It
// reaches the server and learns what the viewer may do through its host
// (./host.js), never through the web app's own state.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowCounterClockwise, ArrowLeft, ArrowRight, ArrowsClockwise, DotsThree, Gear, ImageSquare, ListBullets,
  PencilSimple, Plus, Trash, WarningCircle, X,
} from "@phosphor-icons/react";
import { GridViewIcon } from "../design-icons.jsx";
import { LoadingIndicator as LoadingSpinner } from "../components/LoadingIndicator";
import { HeaderToggle, SettingsCard, Toggle } from "../components/SettingsControls.jsx";
import { DialogCloseButton, isTopDialog, useDialog, useDrawerExit } from "../dialogs.jsx";
import {
  FLICK_WINDOW_MS, READING_DIRECTIONS, actionForKey, clampPan, clampZoom, fittedSize, isFlick, isSpread, isSwipe, loadReaderPrefs, pageFilter, pageForAction, pageWindow, pagesLeft, panelFocus, panelMask, panelStep, pinchLeavesPanel, pinchPan, pinchZoom, pointerDistance, pointerMidpoint, readablePanels, readingDirection, releasedSwipe, saveReaderPrefs, stepAt, stepCount, stepOf, swipeAction, tapAction, verticalClose, zoomAt,
} from "../reader.js";
import {
  HANDLES, hitTest, dragRect, drawnRect, isDrawn, newPanel, nudge, removeAt, tapOrder, applyOrder, toPayload, fromReading, editorKeyIntent,
} from "../panel-editor.js";
import { useReaderHost } from "./host.js";

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
  const admin = useReaderHost().canEditPanels;
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
// A finger is wider than a pointer: a handle is grabbed from this far with a
// touch, and the layer catches touches this far outside the page, where the
// outer half of an edge handle sits -- a touch there used to land on the
// stage, outside the layer, and go nowhere (owner, 2026-10-07).
const EDITOR_TOUCH_PX = 40;
const EDITOR_NUDGE = 0.005;

function PanelEditor({ fileId, count, pages, startPage, readings, direction, onSaved, onClose }) {
  const { api: apiRequest } = useReaderHost();
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
  const fieldRef = useRef(null);
  const drag = useRef(null);
  // A pointer's moves are applied once a frame, not once an event: a finger
  // reports at up to 120Hz, and a render per report lagged it on a phone.
  const pending = useRef(null);
  const frame = useRef(0);
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

  // Positions are read against the page itself (the field), not the layer
  // that catches the pointer, which reaches a handle's width past the page.
  const pointAt = (event) => {
    const field = (fieldRef.current || event.currentTarget).getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (event.clientX - field.left) / field.width)),
      y: Math.min(1, Math.max(0, (event.clientY - field.top) / field.height)),
    };
  };
  const reachFor = (event) => {
    const px = event.pointerType === "mouse" ? EDITOR_HANDLE_PX : EDITOR_TOUCH_PX;
    return box ? { x: px / box.width, y: px / box.height } : { x: 0.05, y: 0.03 };
  };

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
    const hit = hitTest(panels, point, reachFor(event), selected);
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* a pointer the browser does not know: a test's */ }
    if (hit) {
      setSelected(hit.index);
      drag.current = { pointerId: event.pointerId, index: hit.index, part: hit.part, start: panels[hit.index], origin: point };
    } else {
      setSelected(-1);
      drag.current = { pointerId: event.pointerId, drawing: true, origin: point };
    }
  }
  function applyMove() {
    frame.current = 0;
    const current = drag.current;
    const point = pending.current;
    pending.current = null;
    if (!current || !point) return;
    if (current.drawing) { setDraft(drawnRect(current.origin, point)); return; }
    const dx = point.x - current.origin.x;
    const dy = point.y - current.origin.y;
    if (Math.abs(dx) < 0.002 && Math.abs(dy) < 0.002) return;
    current.moved = true;
    setPanels((list) => list.map((rect, at) => at === current.index ? dragRect(current.start, current.part, dx, dy) : rect));
  }
  function onPointerMove(event) {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    pending.current = pointAt(event);
    if (!frame.current) frame.current = requestAnimationFrame(applyMove);
  }
  function onPointerUp(event) {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    // The last move lands before the lift is judged.
    if (frame.current) { cancelAnimationFrame(frame.current); applyMove(); }
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
  // A splash, a pin-up, a cover: one image, read whole -- said outright and
  // saved at once. The hint used to say to leave such a page with no panels,
  // but a page that started with none had nothing to save, and the reader
  // kept its four quadrants (2026-10-07).
  async function asOneImage() {
    setSaving("save");
    setError("");
    try {
      const data = await apiRequest(`/api/v1/files/${fileId}/pages/${pageRef.current}/panels`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ panels: [] }),
      });
      onSaved(pageRef.current, data);
      setReading(data);
      setPanels(fromReading(data));
      setSelected(-1);
      setOrdering(null);
      setDirty(false);
    } catch (problem) {
      setError(problem.message || "The page could not be saved");
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
        : "Drag on the page to draw the first panel, or mark it as a single image.";
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
        style={{ left: box.left - EDITOR_TOUCH_PX, top: box.top - EDITOR_TOUCH_PX, width: box.width + 2 * EDITOR_TOUCH_PX, height: box.height + 2 * EDITOR_TOUCH_PX }}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
        <div className="panel-editor-field" ref={fieldRef} style={{ left: EDITOR_TOUCH_PX, top: EDITOR_TOUCH_PX, width: box.width, height: box.height }}>
        {panels.map((rect, at) => {
          const number = ordering ? ordering.indexOf(at) + 1 : at + 1;
          return <div key={at} className={`panel-editor-box${at === selected && !ordering ? " selected" : ""}${ordering && number ? " numbered" : ""}`}
            style={{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%` }}>
            <b className="panel-editor-number">{number || "·"}</b>
            {at === selected && !ordering ? HANDLES.map((handle) => <i key={handle} className={`panel-editor-handle panel-editor-handle--${handle}`} aria-hidden="true" />) : null}
          </div>;
        })}
        {draft ? <div className="panel-editor-box drawing" style={{ left: `${draft.x * 100}%`, top: `${draft.y * 100}%`, width: `${draft.w * 100}%`, height: `${draft.h * 100}%` }} /> : null}
        </div>
      </div> : null}
    </div>
    <footer className="reader-bar reader-bar--bottom panel-editor-tools">
      <p className={`panel-editor-hint${error ? " panel-editor-hint--error" : ""}`} role="status">{error || hint}</p>
      <div className="panel-editor-actions">
        {ordering ? <button type="button" className="glass-button" onClick={() => setOrdering(null)}>Cancel ordering</button> : <>
          <button type="button" className="glass-button" onClick={add} disabled={busy}><Plus size={16} /> Add panel</button>
          <button type="button" className="glass-button" onClick={() => { setOrdering([]); setSelected(-1); }} disabled={busy || panels.length < 2}><ListBullets size={16} /> Set order</button>
          <button type="button" className="glass-button" onClick={remove} disabled={busy || selected < 0}><Trash size={16} /> Delete</button>
          <button type="button" className="glass-button" onClick={asOneImage} disabled={busy}><ImageSquare size={16} /> Single image</button>
          {manual ? <button type="button" className="glass-button" onClick={letGo} disabled={Boolean(saving)} aria-busy={saving === "reset"}>
            {saving === "reset" ? <LoadingSpinner size={16} /> : <ArrowCounterClockwise size={16} />} Back to automatic
          </button> : null}
        </>}
      </div>
    </footer>
  </div>;
}

// A panel request that got no answer is asked again this many times, after
// this long and then twice and three times as long.
const PANEL_RETRIES = 3;
const PANEL_RETRY_MS = 5000;

// How long a page may take to have its panels found before the reader says
// it is finding them: an answer within it is not worth a word.
const FINDING_PANELS_DELAY_MS = 400;

// True once `on` has held for `delay` ms, so a status for an answer that
// comes at once never flashes up and away.
function useShownAfter(on, delay) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!on) { setShown(false); return undefined; }
    const timer = setTimeout(() => setShown(true), delay);
    return () => clearTimeout(timer);
  }, [on, delay]);
  return on && shown;
}

export function ReaderView({
  fileId, title, medium, directionOverride, startPage = null, behind = false,
  onFinish, onOpenRun, onProgressSaved, onClose,
}) {
  const { api: apiRequest } = useReaderHost();
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
  // Pages whose panel request failed for want of an answer -- the network,
  // a restart -- and how often. Each is asked again after a pause; one
  // hiccup used to leave a page in quadrants until the comic was reopened.
  const panelRetries = useRef({});
  const [panelRetryTick, setPanelRetryTick] = useState(0);
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
  // Pages whose image did not load, and how many times it has been asked
  // for: a broken image used to sit there, never framed (2026-10-07).
  const [pageFailures, setPageFailures] = useState({});
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
  // What a page's panels are for stepping: the whole page while they are
  // being found, then the server's, or four quadrants when it had none --
  // so there is never a page that cannot be stepped through.
  const pagePanels = useCallback((number) => readablePanels(panelsRef.current[number], direction), [direction]);
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
  // The page in view is having its panels found: it reads whole meanwhile
  // (`readablePanels`), and says so once that takes long enough to notice.
  const findingPanels = panelMode && !overview && pages.state === "done" && count > 0 && !panels[index];
  const showFinding = useShownAfter(findingPanels, FINDING_PANELS_DELAY_MS);
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
    panelRetries.current = {};
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
            const rects = readablePanels(data ?? { segmented: false, panels: [] }, direction);
            setPanel(stepOf(resuming.panel, rects.length, stepPrefsRef.current));
          }
        })
        .catch((problem) => {
          if (fileIdRef.current !== asked) return;
          const tries = (panelRetries.current[number] || 0) + 1;
          panelRetries.current[number] = tries;
          // An answer -- "not a page of this comic" -- is final; no answer
          // is asked again, three times, a little later each time. The
          // page reads whole meanwhile, as it does while panels are found.
          const answered = problem?.status && problem.status < 500;
          if (answered || tries >= PANEL_RETRIES) {
            setPanels((current) => ({ ...current, [number]: { segmented: false, panels: [] } }));
            return;
          }
          window.setTimeout(() => {
            if (fileIdRef.current !== asked) return;
            asking.current.delete(number);
            setPanelRetryTick((tick) => tick + 1);
          }, PANEL_RETRY_MS * tries);
        });
    }
  }, [panelMode, pages.state, count, index, fileId, panels, direction, panelRetryTick]);

  // The framing for one panel of one page, from the shown image's natural
  // size fitted to the surface -- its 1x layout size, whatever zoom it is at.
  // Null until an image is on screen to measure.
  const focusFor = useCallback((number, position) => {
    const image = pageRef.current;
    const surface = surfaceRef.current;
    if (!image || !surface || !image.naturalWidth) return null;
    const viewport = surface.getBoundingClientRect();
    const base = fittedSize({ width: image.naturalWidth, height: image.naturalHeight }, viewport);
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
  const keepPlace = useCallback((page, step = 0, leaving = false) => {
    const panelAt = placePanel(page, step);
    const key = `${page}:${panelAt}`;
    if (!open.current || key === saved.current) return null;
    saved.current = key;
    return apiRequest(`/api/v1/files/${fileId}/progress`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page, panel: panelAt }),
      // A save made as the page goes away -- a closed tab, an app swiped
      // off, a phone locked -- is let finish after it has gone.
      ...(leaving ? { keepalive: true } : {}),
    }).catch(() => { saved.current = null; });
  }, [fileId, placePanel]);

  // The reader is sized from the visual viewport, not left to `inset: 0`.
  // A fixed element fills iOS Safari's layout viewport, which lags the
  // screen after a rotation while the toolbar retracts, and the Discover
  // shelves showed through a strip at the bottom of the comic until Safari
  // caught up (owner, 2026-10-07). The visual viewport is right at every
  // moment; a browser without it keeps the stylesheet's 100dvh.
  useEffect(() => {
    const dialog = dialogRef.current;
    const viewport = window.visualViewport;
    if (!dialog || !viewport) return undefined;
    function fit() {
      dialog.style.setProperty("--reader-top", `${Math.round(viewport.offsetTop)}px`);
      dialog.style.setProperty("--reader-height", `${Math.round(viewport.height)}px`);
    }
    fit();
    viewport.addEventListener("resize", fit);
    viewport.addEventListener("scroll", fit);
    window.addEventListener("orientationchange", fit);
    return () => {
      viewport.removeEventListener("resize", fit);
      viewport.removeEventListener("scroll", fit);
      window.removeEventListener("orientationchange", fit);
    };
  }, [dialogRef]);

  // The place is saved 900ms after a page settles; a tab closed, an app
  // swiped away or a phone locked inside that moment lost it. Leaving the
  // page saves at once (2026-10-07).
  useEffect(() => {
    function leave() { keepPlace(at.current, panelRef.current, true); }
    function hidden() { if (document.visibilityState === "hidden") leave(); }
    window.addEventListener("pagehide", leave);
    document.addEventListener("visibilitychange", hidden);
    return () => {
      window.removeEventListener("pagehide", leave);
      document.removeEventListener("visibilitychange", hidden);
    };
  }, [keepPlace]);

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
    const saving = keepPlace(at.current, panelRef.current, true);
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
      if (verticalClose(dx, dy)) { onClose(); return; }
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
      gesture = { zoom0: zoomRef.current, dist0: pointerDistance(a, b), zoom: zoomRef.current, pan: panRef.current, frame: 0,
                  mid: pointerMidpoint(a, b) };
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
      // The hand moving as it pinches drags the page with it; the zoom is
      // then about where the fingers are now.
      const dragged = pinchPan(gesture.pan, gesture.mid, mid);
      gesture.mid = mid;
      gesture.pan = clampPan(zoomAt(about, gesture.zoom, next, dragged), next, viewport, page, panelModeRef.current);
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
    // Where the touch began, kept apart from `start`, which a pan moves to
    // where the look around began.
    const touched = { x: event.clientX, y: event.clientY, at: start.at };
    const { viewport, page } = panBounds();
    let frame = 0;
    let latest = start.pan;
    let mode = panelModeRef.current && event.pointerType !== "mouse" ? "pending" : "pan";
    if (mode === "pan") setPanning(true);
    function paintTo(to) {
      if (image) image.style.translate = `${to.x}px ${to.y}px`;
    }
    function paint() {
      frame = 0;
      paintTo(latest);
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
      // The click a lift fires is the drag's, not a tap; but a lift that
      // fires no click -- the finger left the surface -- must not eat the
      // next tap either, so the flag is let go of after it.
      if (dragged.current) window.setTimeout(() => { dragged.current = false; }, 0);
      if (mode === "pending" && up.type !== "pointercancel") {
        // A touch that lifted before it read as anything is a tap, and the
        // click that follows it turns the page or wakes the chrome.
        return;
      }
      if (mode !== "pan") return;
      // A swipe at an ordinary speed, in panel view: a step, not a look
      // around that leaves the panel wherever the finger stopped.
      const dx = up.clientX - touched.x;
      const dy = up.clientY - touched.y;
      if (panelModeRef.current && up.pointerType !== "mouse" && releasedSwipe(dx, dy, Date.now() - touched.at)) {
        dragged.current = true;
        window.setTimeout(() => { dragged.current = false; }, 0);
        // The page goes back to where the step began; the step frames the next panel.
        paintTo(start.pan);
        panRef.current = start.pan;
        setPanning(false);
        const action = swipeAction(dx, direction);
        if (action) go(action);
        return;
      }
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
      {/* Present always, so the words are announced when they arrive. */}
      <div className="reader-finding" role="status">{showFinding ? <><LoadingSpinner size={16} /><span>Finding panels…</span></> : null}</div>
      {pageFailures[index]?.failed ? <div className="reader-status reader-status--error reader-page-error" role="alert"
        onClick={(event) => event.stopPropagation()} onPointerDown={(event) => event.stopPropagation()}>
        <WarningCircle size={22} /><span>Page {index + 1} could not be loaded.</span>
        <button type="button" className="glass-button" onClick={() => setPageFailures((current) => ({
          ...current, [index]: { failed: false, tries: (current[index]?.tries || 0) + 1 },
        }))}><ArrowsClockwise size={16} /> Try again</button>
      </div> : null}
      {window_.map((number) => {
        const item = pages.list[number];
        const shown = number === index;
        // The page dims around the panel in view; the whole page, asked for
        // with a double-tap, is shown undimmed.
        const scrim = shown && panelMode && panelScrim && !overview && !wholePageStep && Boolean(panels[number]);
        const hole = scrim ? panelMask(pagePanels(number)[shownStep.panel]) : null;
        const tries = pageFailures[number]?.tries || 0;
        return <img key={`${number}-${tries}`} src={tries ? `${item.readUrl}&retry=${tries}` : item.readUrl} alt={shown ? `Page ${number + 1} of ${count}` : ""}
          className={`reader-page${shown ? " shown" : ""}${spreads[number] ? " spread" : ""}${shown && panning ? " panning" : ""}${shown && panelMode ? " panel-view" : ""}${scrim ? " scrim" : ""}${pageFailures[number]?.failed ? " failed" : ""}`}
          onError={() => setPageFailures((current) => ({ ...current, [number]: { failed: true, tries: current[number]?.tries || 0 } }))}
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
            if (pageFailures[number]) setPageFailures((current) => { const next = { ...current }; delete next[number]; return next; });
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
          ? `${findingPanels ? "Finding panels" : wholePageStep ? "Whole page" : `Panel ${shownStep.panel + 1} of ${pagePanels(index).length}`} · ${index + 1} of ${count}`
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

