import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { apiRequest } from "../api.js";
import { CoverArt, DiscoverCover } from "../components/CoverArt.jsx";
import { GlassSelect, LoadingSpinner } from "../components/glass.jsx";

import { moveToEdge } from "../reading-list.js";
import { moveRun } from "../run-collections.js";
import { FollowSwitch } from "./frame.jsx";
import { ActionMenu } from "./inventory.jsx";
import { ArrowCounterClockwise, ArrowLineDown, ArrowLineUp, ArrowsClockwise, CaretRight, CheckCircle, DotsSixVertical, DotsThree, ListNumbers, Plus, Trash, UploadSimple, X } from "@phosphor-icons/react";
import { useImageSrc } from "../host.js";

/** A read group's Edit: its name and summary on the screen, one Save, and the pickers a step in. */
export function GroupEditScreen({ name, summary, summaryLabel = "Summary", busy, onSave, rows }) {
  const [nameDraft, setNameDraft] = useState(name);
  const [summaryDraft, setSummaryDraft] = useState(summary || "");
  const changed = nameDraft.trim() !== name || summaryDraft.trim() !== String(summary || "").trim();
  return <div className="edit-index">
    <form className="group-edit-form" onSubmit={(event) => {
      event.preventDefault();
      if (changed && nameDraft.trim().length >= 2) onSave({ name: nameDraft.trim(), summary: summaryDraft.trim() });
    }}>
      <label className="form-field"><span>Name</span><input value={nameDraft} maxLength={80} required onChange={(event) => setNameDraft(event.target.value)} /></label>
      <label className="form-field"><span>{summaryLabel}</span><textarea value={summaryDraft} maxLength={2000} rows={4} onChange={(event) => setSummaryDraft(event.target.value)} placeholder="Optional" /></label>
      <div className="metadata-edit-actions group-edit-actions"><button type="submit" className="primary-button" disabled={Boolean(busy) || !changed || nameDraft.trim().length < 2} aria-busy={busy === "save" || undefined}>Save</button></div>
    </form>
    <div className="edit-rows">
      {rows.map((row) => <button type="button" className="edit-row" key={row.id} disabled={row.disabled} onClick={row.onOpen}>
        <span><strong>{row.label}</strong><small>{row.detail}</small></span>
        <CaretRight size={17} />
      </button>)}
    </div>
  </div>;
}

/** A read group's Cover, a step in from Edit: a picture of your own, or one of its own covers. */
export function GroupCoverPicker({ choices, current, busy, uploadError, onUpload, onChoose, empty }) {
  return <>
    {onUpload ? <div className="arc-cover-upload">
      <label className={`secondary-button upload-cover-button${busy === "upload" ? " busy" : ""}`}>
        {busy === "upload" ? <LoadingSpinner size={18} /> : <UploadSimple size={18} />} Upload an image
        <input type="file" accept="image/jpeg,image/png,image/webp,image/gif,image/heic,image/heif" disabled={busy === "upload"}
          onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) onUpload(file); }} />
      </label>
      {uploadError ? <p className="workbench-error" role="alert">{uploadError}</p> : null}
    </div> : null}
    <div className="arc-cover-choices" role="radiogroup" aria-label="Cover">
      {choices.length ? choices.map((choice) => <button type="button" key={choice.id} role="radio" aria-checked={choice.id === current} className={choice.id === current ? "active" : ""}
        onClick={() => onChoose(choice)} disabled={Boolean(busy)}>
        <DiscoverCover src={choice.url} alt="" glyph={22} /><small>{choice.label}</small>
      </button>) : empty}
    </div>
  </>;
}

/**
 * The page behind a read group's header: chosen from its own comics, or found
 * in the first of them, as a run's is. Nothing until this group's answer is
 * in, so another group's page never flashes behind a new title.
 */
export function useGroupBackdrop(path, id) {
  const [backdrop, setBackdrop] = useState({ forId: null, url: null, source: null, fileId: null, page: null });
  const [failed, setFailed] = useState(false);
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setFailed(false);
    apiRequest(`${path}/backdrop`)
      .then((answer) => { if (live) setBackdrop({ ...answer, forId: id }); })
      .catch(() => { if (live) setBackdrop({ forId: id, url: null, source: "none", fileId: null, page: null }); });
    return () => { live = false; };
  }, [path, id, version]);
  async function save(body) {
    setBusy(true); setError("");
    try {
      await apiRequest(`${path}/backdrop`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setVersion((value) => value + 1);
      return true;
    } catch (failure) { setError(failure.message); return false; } finally { setBusy(false); }
  }
  const shown = backdrop.forId === id ? backdrop : null;
  return { shown, page: shown?.url && !failed ? shown.url : null, onFail: () => setFailed(true), save, busy, error };
}

// One row of an Arrange list: to either end -- the way through a long list,
// where dragging is slow -- or out of the group. A menu rather than three
// buttons, so a phone row keeps room for the item's name.
export function OrderRowMenu({ label, first, last, removeLabel, onMove, onRemove }) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef(null);
  const close = useCallback(() => setOpen(false), []);
  const items = [
    ...(first || !onMove ? [] : [{ key: "top", label: "Move to top", icon: <ArrowLineUp size={16} />, onSelect: () => onMove("top") }]),
    ...(last || !onMove ? [] : [{ key: "bottom", label: "Move to bottom", icon: <ArrowLineDown size={16} />, onSelect: () => onMove("bottom") }]),
    { key: "remove", label: removeLabel, icon: <X size={16} />, onSelect: onRemove },
  ];
  return <>
    <button type="button" ref={buttonRef} className="glass-button glass-button--icon arc-order-menu" onClick={() => setOpen((value) => !value)}
      aria-haspopup="menu" aria-expanded={open} aria-label={`Options for ${label}`} title="Options">
      <DotsThree size={18} weight="bold" />
    </button>
    {open ? <ActionMenu anchor={buttonRef} label={`Options for ${label}`} items={items} onClose={close} /> : null}
  </>;
}

/**
 * A read group's Arrange tab, for whoever may change it: the order it is read
 * in (a saved mode), and in your own order a drag list whose rows' menus move
 * one to an end or take it out. Every change saves at once. Below, Add.
 */
export function GroupArrange({ noun, groupNoun, ids, sortMode, sortOptions, busy, onSortMode, onReorder, onRemove, nameOf, renderRow, rowClassName, onAdd, addLabel, empty }) {
  const custom = sortMode === "custom";
  const aNoun = `${/^[aeiou]/i.test(noun) ? "an" : "a"} ${noun}`;
  const modeLabel = sortOptions.find((item) => item.id === sortMode)?.label?.toLowerCase() || sortMode;
  const row = (id, index) => <>
    {renderRow(id, index)}
    <OrderRowMenu label={nameOf(id)} first={index === 0} last={index === ids.length - 1} removeLabel={`Remove from the ${groupNoun}`}
      onMove={custom ? (edge) => onReorder(moveToEdge(ids, id, edge)) : null} onRemove={() => onRemove(id)} />
  </>;
  if (!ids.length) return empty;
  return <div className="group-arrange">
    <div className="form-field group-order-field"><span>Order</span>
      <GlassSelect label="Order" value={sortMode} onChange={onSortMode} className="glass-select--fill"
        options={sortOptions.map((item) => ({ value: item.id, label: item.label }))} /></div>
    <p className="settings-card-note">{custom
      ? `Drag ${aNoun} by its handle, or use the arrow keys on it. Its menu moves it to the top or bottom, or takes it out of this ${groupNoun} only.`
      : `Read by ${modeLabel}. Choose Your order to arrange them by hand. ${aNoun[0].toUpperCase()}${aNoun.slice(1)}'s menu takes it out of this ${groupNoun} only.`}</p>
    {custom ? <DragOrderList ids={ids} rowClassName={rowClassName}
      label={(id, index, total) => `${nameOf(id)}, ${index + 1} of ${total}`}
      onReorder={onReorder} renderRow={row} />
      : <ol className="source-order-list">{ids.map((id, index) => <li className={`source-order-row ${rowClassName?.(id) || ""}`} key={id}>{row(id, index)}</li>)}</ol>}
    {onAdd ? <button type="button" className="glass-button comic-drawer-add" onClick={onAdd} disabled={Boolean(busy)}><Plus size={18} /> {addLabel}</button> : null}
  </div>;
}

/** Arrange, as one card in a read group's Advanced: how it is read now, and a step into the list. */
export function GroupArrangeCard({ noun, count, sortMode, sortOptions, onOpen }) {
  const mode = sortOptions.find((item) => item.id === sortMode)?.label || "Your order";
  const items = `${count} ${noun}${count === 1 ? "" : "s"}`;
  return <section className="advanced-card">
    <div><strong>Arrange</strong><p>{sortMode === "custom" ? `${items}, in your order.` : `${items}, read by ${mode.toLowerCase()}.`} Change the order, move one to the top or bottom, or take one out.</p></div>
    <button type="button" onClick={onOpen}><ListNumbers size={16} /> Arrange</button>
  </section>;
}

/** Sharing a read group, in Advanced: its maker's switch, or the admin's Stop sharing on someone else's. */
export function GroupShareCard({ group, noun, busy, onShare, onStopSharing }) {
  if (group?.mine) return <section className="advanced-card">
    <div><strong>Share with the household</strong><p>{group.shared
      ? `Everyone in the household sees this ${noun} and reads along. Only you change it.`
      : `Only you see this ${noun}. Shared, everyone in the household can read it; only you change it.`}</p></div>
    <FollowSwitch following={Boolean(group.shared)} busy={busy === "share"} label={group.shared ? "Shared" : "Not shared"} onChange={onShare} />
  </section>;
  if (group?.manageable && group?.shared && group?.ownerName) return <section className="advanced-card">
    <div><strong>Shared by {group.ownerName}</strong><p>Stop sharing takes it out of everyone&rsquo;s comics but {group.ownerName}&rsquo;s. It stays theirs.</p></div>
    <button type="button" onClick={onStopSharing} disabled={busy === "share"}><X size={16} /> Stop sharing</button>
  </section>;
  return null;
}

/** Restart and Reading state, in a read group's Advanced, for every profile. */
export function GroupReadingCards({ noun, firstLabel, onRestart, allRead, anyRead, busy, onMark }) {
  return <>
    {onRestart ? <section className="advanced-card">
      <div><strong>Start from the beginning</strong><p>Opens {firstLabel} from its first page. Your place moves once you turn a page there.</p></div>
      <button type="button" onClick={onRestart}><ArrowCounterClockwise size={16} /> Restart</button>
    </section> : null}
    {onMark ? <section className="advanced-card">
      <div><strong>Reading state</strong><p>{allRead
        ? `Every comic here is read. Marking the ${noun} unread forgets your place in all of them, for your profile only.`
        : anyRead
          ? `Some of this ${noun} is read or in progress. Mark it all read, or unread to forget every place -- someone else's reading on your profile included.`
          : `Marks every comic of this ${noun} read, for your profile only.`}</p></div>
      <div className="advanced-card-actions">
        {!allRead ? <button type="button" disabled={Boolean(busy)} onClick={() => onMark(true)}><CheckCircle size={16} /> Mark read</button> : null}
        {allRead || anyRead ? <button type="button" disabled={Boolean(busy)} onClick={() => onMark(false)}><ArrowCounterClockwise size={16} /> Mark unread</button> : null}
      </div>
    </section> : null}
  </>;
}

/** Deleting a read group, last in Advanced: the comics themselves stay. */
export function GroupDeleteCard({ group, noun, name, onDelete }) {
  const keeps = noun === "collection" ? "Every run in it stays in your library." : "The comics themselves stay, in their runs.";
  return <section className="advanced-card danger">
    <div><strong>Delete this {noun}</strong><p>{group?.mine && group?.shared ? `Removes it from your comics and everyone it is shared with. ${keeps}` : `Removes it from your comics. ${keeps}`}</p></div>
    <button type="button" onClick={() => { if (window.confirm(`Delete ${name}? ${keeps}`)) onDelete(); }}><Trash size={16} /> Delete</button>
  </section>;
}

export function RunCollectionRow({ run }) {
  if (!run) return null;
  return <>
    <span className="rating-review-cover"><CoverArt id={run.id} title={run.title} cover={run.cover} coverCandidates={run.coverCandidates} decorative placeholderSize={16} /></span>
    <span><strong>{run.title}{/^\d{4}$/.test(String(run.year)) ? ` (${run.year})` : ""}</strong><small>{run.publisher}</small></span>
  </>;
}

/**
 * The download order, dragged into shape by each row's handle. The handle
 * alone starts a drag, so a touch on the row still scrolls the page; on it,
 * the arrow keys move the source a place at a time, because dragging must
 * never be the only way (WCAG 2.5.7). The order is saved once, on drop.
 */
/**
 * A list put in order by dragging each row's handle (Download order, and a
 * collection's runs). Only the handle takes the touch, so a swipe on a row
 * still scrolls; on the focused handle the arrow keys move it a place, since
 * dragging must never be the only way (WCAG 2.5.7). The order is reported
 * once, on drop. `label(id, index, total)` names a handle; `renderRow(id,
 * index)` draws the rest of its row.
 */
// How long a finger holds a handle still before the row lifts. Shorter and a
// scroll that starts on the handle picks a run up; much longer and the hold
// feels like nothing is happening.
export const DRAG_HOLD_MS = 250;

// How far a finger may wander during the hold and still be holding.
export const DRAG_HOLD_SLOP = 8;

export function DragOrderList({ ids, label, renderRow, rowClassName, onReorder }) {
  const saved = ids.map(String);
  const savedKey = saved.join();
  const latestSaved = useRef(saved);
  latestSaved.current = saved;
  const onReorderRef = useRef(onReorder);
  onReorderRef.current = onReorder;
  const [order, setOrder] = useState(saved);
  const [drag, setDrag] = useState(null);
  // The handlers read these rather than state: the touch listeners are
  // attached once, and must see the order and the drag as they are now.
  const orderRef = useRef(order);
  orderRef.current = order;
  const dragRef = useRef(null);
  // Saves still on their way. Until they land the list keeps the order it was
  // put in by hand: following the saved order meanwhile snapped a dropped row
  // back, then forward again seconds later once the catalog had reloaded (a
  // collection's runs, 2026-10-01). A save that fails puts it back.
  const pending = useRef(0);
  const listRef = useRef(null);
  const rowRefs = useRef({});
  const handleRefs = useRef({});
  const refocus = useRef(null);
  // Where each row stood before a swap, so the ones that make way glide.
  const before = useRef(null);
  useEffect(() => { if (!drag && !pending.current) setOrder(savedKey ? savedKey.split(",") : []); }, [savedKey, drag]);
  useEffect(() => {
    if (refocus.current) { handleRefs.current[refocus.current]?.focus(); refocus.current = null; }
  });
  useLayoutEffect(() => {
    const tops = before.current;
    before.current = null;
    if (!tops || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    for (const [id, top] of Object.entries(tops)) {
      const node = rowRefs.current[id];
      const delta = node ? top - node.getBoundingClientRect().top : 0;
      if (Math.abs(delta) > 0.5) {
        node.animate?.([{ transform: `translateY(${delta}px)` }, { transform: "translateY(0)" }],
          { duration: 160, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" });
      }
    }
  }, [order]);
  // A row's own content is drawn once per order, not on every move of the
  // pointer: only the dragged row's offset changes while it follows a finger.
  const rows = useMemo(() => Object.fromEntries(order.map((id, index) => [id, renderRow(id, index)])), [order, renderRow]);
  function remember(except) {
    before.current = Object.fromEntries(orderRef.current.filter((id) => id !== except)
      .map((id) => [id, rowRefs.current[id]?.getBoundingClientRect().top ?? 0]));
  }
  function report(next) {
    pending.current += 1;
    let failed = false;
    Promise.resolve().then(() => onReorderRef.current(next))
      .then((result) => { failed = result === false; }, () => { failed = true; })
      .finally(() => {
        pending.current -= 1;
        if (failed && !pending.current) setOrder(latestSaved.current);
      });
  }
  function begin(id, y) {
    const next = { id, startY: y, offset: 0, before: orderRef.current.join() };
    dragRef.current = next;
    setDrag(next);
  }
  function moveTo(y) {
    const current = dragRef.current;
    if (!current) return;
    let { startY } = current;
    let offset = y - startY;
    let next = orderRef.current;
    const index = next.indexOf(current.id);
    const below = rowRefs.current[next[index + 1]];
    const above = rowRefs.current[next[index - 1]];
    // Past half a neighbour, the two trade places and the pointer's origin
    // moves with the row, so it stays under the finger.
    if (below && offset > below.offsetHeight / 2) {
      next = moveRun(next, current.id, index + 1);
      startY += below.offsetHeight; offset -= below.offsetHeight;
    } else if (above && offset < -above.offsetHeight / 2) {
      next = moveRun(next, current.id, index - 1);
      startY -= above.offsetHeight; offset += above.offsetHeight;
    }
    if (next !== orderRef.current) { remember(current.id); orderRef.current = next; setOrder(next); }
    dragRef.current = { ...current, startY, offset };
    setDrag(dragRef.current);
  }
  function end() {
    const current = dragRef.current;
    if (!current) return;
    dragRef.current = null;
    setDrag(null);
    if (orderRef.current.join() !== current.before) report(orderRef.current);
  }
  // A finger lifts a row by holding its handle still (Reminders, Keep): a
  // touch on the handle used to start a drag at once, so scrolling a long
  // list with a thumb on that edge moved runs (owner, 2026-10-01). Until
  // the hold completes the handle scrolls like the rest of the row; once it
  // has, the page holds still and the row follows. A mouse drags at once.
  const startHold = useRef(null);
  startHold.current = { begin, moveTo, end };
  useEffect(() => {
    const list = listRef.current;
    if (!list) return undefined;
    let timer = 0;
    let held = null;
    let startY = 0;
    let lifted = false;
    const reset = () => { window.clearTimeout(timer); timer = 0; held = null; lifted = false; };
    const onStart = (event) => {
      const handle = event.target.closest?.("[data-drag-id]");
      if (!handle || event.touches.length !== 1) return;
      held = handle.dataset.dragId;
      startY = event.touches[0].clientY;
      lifted = false;
      timer = window.setTimeout(() => {
        timer = 0;
        lifted = true;
        startHold.current.begin(held, startY);
      }, DRAG_HOLD_MS);
    };
    const onMove = (event) => {
      if (!held) return;
      const y = event.touches[0].clientY;
      if (!lifted) {
        // Moving before the hold is up is a scroll: let it be one.
        if (Math.abs(y - startY) > DRAG_HOLD_SLOP) reset();
        return;
      }
      event.preventDefault();
      startHold.current.moveTo(y);
    };
    const onEnd = () => {
      const wasLifted = lifted;
      reset();
      if (wasLifted) startHold.current.end();
    };
    list.addEventListener("touchstart", onStart, { passive: true });
    list.addEventListener("touchmove", onMove, { passive: false });
    list.addEventListener("touchend", onEnd);
    list.addEventListener("touchcancel", onEnd);
    return () => {
      reset();
      list.removeEventListener("touchstart", onStart);
      list.removeEventListener("touchmove", onMove);
      list.removeEventListener("touchend", onEnd);
      list.removeEventListener("touchcancel", onEnd);
    };
  }, []);
  function pointerDown(event, id) {
    if (event.pointerType !== "mouse" || event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    begin(id, event.clientY);
  }
  function key(event, id) {
    const delta = event.key === "ArrowUp" ? -1 : event.key === "ArrowDown" ? 1 : 0;
    if (!delta) return;
    event.preventDefault();
    const index = order.indexOf(id);
    if (index + delta < 0 || index + delta >= order.length) return;
    const next = moveRun(order, id, index + delta);
    refocus.current = id;
    remember(null);
    orderRef.current = next;
    setOrder(next);
    report(next);
  }
  return <ol className="source-order-list" ref={listRef}>
    {order.map((id, index) => {
      const dragging = drag?.id === id;
      return <li className={`source-order-row ${rowClassName?.(id) || ""} ${dragging ? "source-order-row--dragging" : ""}`}
        key={id} ref={(node) => { rowRefs.current[id] = node; }} style={dragging ? { transform: `translateY(${drag.offset}px)`, willChange: "transform" } : undefined}>
        <button type="button" className="source-order-handle" data-drag-id={id} ref={(node) => { handleRefs.current[id] = node; }}
          aria-label={`${label(id, index, order.length)}. Drag, or press the up and down arrow keys, to reorder.`}
          title="Hold and drag to reorder"
          onPointerDown={(event) => pointerDown(event, id)}
          onPointerMove={(event) => { if (event.pointerType === "mouse") moveTo(event.clientY); }}
          onPointerUp={(event) => { if (event.pointerType === "mouse") end(); }}
          onPointerCancel={(event) => { if (event.pointerType === "mouse") end(); }}
          onKeyDown={(event) => key(event, id)}><DotsSixVertical size={20} weight="bold" /></button>
        {rows[id]}
      </li>;
    })}
  </ol>;
}

/**
 * The cover picker itself, without a window around it.
 *
 * Two things host this: the modal below, which the Files tab opens for one
 * file's cover, and the run drawer's Edit panel, which shows it in place. One
 * body so the two cannot drift into saying different things about the same
 * choice.
 */
export function CoverPicker({ data, busy, error, onSelect, onUpload }) {
  const src = useImageSrc();
  const selectedSource = data.covers.selectedSource;
  // A run has many options from the same source, so identity is the option
  // id where the payload carries one, not the source alone.
  const isSelected = (option) => (
    data.covers.selectedOptionId
      ? data.covers.selectedOptionId === option.optionId
      : selectedSource === option.source && (!data.covers.selectedUrl || data.covers.selectedUrl === option.url)
  );
  return <>
    <div className="cover-option-grid">{data.covers.options.map((option) => <article className={isSelected(option) ? "selected" : ""} key={option.optionId || `${option.source}-${option.url}`}><img src={src(option.url)} alt={option.label} /><div><strong>{option.label}</strong><small>{option.detail}</small><button disabled={busy} onClick={() => onSelect(option.source, option.url, option.fileId)}>{isSelected(option) ? "Selected" : "Use cover"}</button></div></article>)}</div>
    <div className="cover-picker-actions"><button className="ghost-button" disabled={busy} onClick={() => onSelect("auto", null)}><ArrowsClockwise size={17} /> Use automatic cover</button><label className="primary-button upload-cover-button"><UploadSimple size={18} /> Upload image<input type="file" accept="image/jpeg,image/png,image/webp,image/gif,image/heic,image/heif" disabled={busy} onChange={(event) => event.target.files?.[0] && onUpload(event.target.files[0])} /></label></div>
    {error ? <p className="workbench-error" role="alert">{error}</p> : null}
  </>;
}

// The page behind a run's drawer header: pick an issue, then one of its pages.
// Pages load one issue at a time, as thumbnails the server renders from the
// file, so a long run costs nothing until an issue is opened.
export const PAGE_ARCHIVE_EXTENSIONS = new Set(["cbz", "cbr", "cb7", "cbt", "zip", "rar"]);

/** The background picker, hosted by the modal below and by the Edit panel. */
export function BackdropPicker({ series, current, busy, error, readable, onChoose, onAutomatic }) {
  const src = useImageSrc();
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
        <img src={src(page.url)} alt="" loading="lazy" />
        <span>Page {page.index + 1}</span>
      </button>;
    })}</div> : null}
  </>;
}
