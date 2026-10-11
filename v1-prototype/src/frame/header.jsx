import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiRequest } from "../api.js";
import { ProfileAvatar, ProfileTiles } from "../components/ProfileTiles.jsx";
import { LoadingSpinner, useGlassIndicator } from "../components/glass.jsx";
import { ClearSearchIcon, MobileSearchIcon, NotificationsIcon, SearchIcon } from "../design-icons.jsx";
import { SheetGrabber, slideSheetAway, useDialog, usePhoneWidth } from "../dialogs.jsx";
import { bellCount, timeAgo } from "../notifications.js";
import { isAdmin } from "../profiles.js";
import { useViewer } from "../viewer.js";
import { ArrowCounterClockwise, CheckCircle, SignOut, WarningCircle, X } from "@phosphor-icons/react";
import { useImageSrc } from "../host.js";

// What every page header needs from the app and no page owns: the bell's
// notifications, and the app's search -- the current query and where a
// search goes.
export const HeaderContext = createContext(null);

export function HeaderBell() {
  const header = useContext(HeaderContext);
  // Where the host has notifications: Flipparr View has none yet.
  if (!header?.bell) return null;
  return <NotificationsBell bell={header.bell} />;
}

// The pages' one tab control (Pull List, Settings), after iOS 26's segmented
// control: a pill track with a glass thumb that slides to the open tab. The
// run drawer's tabs are the same glass on its violet bar. A row wider than
// the screen scrolls sideways, and opening a tab brings it into view.
export function SegmentedTabs({ label, items, value, onChange }) {
  const [trackRef, glass] = useGlassIndicator(".segmented-tab.active", [value, items.map((item) => item.id).join()]);
  useEffect(() => {
    const track = trackRef.current;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    function reveal(smooth) {
      const active = track?.querySelector(".segmented-tab.active");
      if (!active || track.scrollWidth <= track.clientWidth) return;
      const left = active.offsetLeft - (track.clientWidth - active.offsetWidth) / 2;
      track.scrollTo?.({ left: Math.max(0, left), behavior: smooth && !still ? "smooth" : "auto" });
    }
    reveal(true);
    // Opened straight onto a tab past the first screenful -- Comics' Reading
    // list on a phone -- the labels are not measured until the font has
    // loaded, so the first look finds nothing to scroll. Look again then.
    let live = true;
    document.fonts?.ready?.then(() => { if (live) reveal(false); });
    return () => { live = false; };
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

// The bell (rebuilt 2026-09-27). Two halves: what needs you, worked out from
// the library as it stands (notifications.js), which goes away when it is dealt
// with; and what happened, kept per profile on the server, which is read and
// cleared there and so is the same on every device. On a phone it opens as a
// sheet from the bottom the height of the screen -- iOS presents a popover
// that way at compact width -- and elsewhere as a panel under the bell, placed
// from the bell's own position so it stays inside the window whatever sits
// beside the bell.
export const NOTIFICATION_KINDS = { download: "Download", request: "Request", source: "Metadata source", file: "Comic file", metadata: "Metadata" };

export function NotificationRow({ item, news, fresh, onOpen, onDismiss, onRetry }) {
  const [busy, setBusy] = useState(false);
  const src = useImageSrc();
  const warning = news ? item.tone === "warning" : item.severity !== "error";
  const icon = news && item.cover
    ? <img className="notification-cover" src={src(item.cover)} alt="" loading="lazy" />
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

export function NotificationCenter({ bell, anchorRef, onClose }) {
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
export function NotificationsBell({ bell }) {
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
export function PageHeader({ title, leading, actions, primary, search = "none", field, tools, toolsClassName = "", narrow = false, searchButton = true }) {
  const headerRef = useRef(null);
  const headerContext = useContext(HeaderContext);
  const phone = usePhoneWidth();
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
        {/* Search on a phone: one button by the bell, on every page, opening
            the same search a desktop types into its sidebar (2026-10-01). */}
        {phone && searchButton && headerContext?.search ? <button type="button" className="glass-button glass-button--icon appbar-search"
          onClick={headerContext.search.onOpen} aria-label="Search" title="Search"><MobileSearchIcon size={20} /></button> : null}
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
export function HeaderSearchField({ field, className = "page-header-search" }) {
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
      ref={inputRef} type="search" enterKeyHint="search" value={value} autoFocus={field?.autoFocus} id={field?.id}
      onChange={(event) => change(event.target.value)}
      onFocus={field?.onFocus}
      // Enter runs the search itself rather than leaning on the form's
      // implicit submission, which not every browser raises for every Enter.
      // Not while an input method is composing: there Enter picks a character.
      onKeyDown={(event) => {
        // Esc clears a search, and a second Esc leaves the field.
        if (event.key === "Escape" && field?.onEscape) {
          event.preventDefault();
          if (value) clear(); else event.currentTarget.blur();
          return;
        }
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

// Your picture in every page's header, where Plex keeps it: a tap opens a
// menu of who else can read here, adding a profile, your own page, and
// signing out. The header is the one place every page has.
export function HeaderProfile() {
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
export function ProfileMenu({ onClose }) {
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
      onAdd={isAdmin(viewer) && others !== null && actions.onAdd ? go(actions.onAdd) : null} />
    {others === null ? <p className="profile-menu-loading"><LoadingSpinner size={16} /></p> : null}
    {/* Signing out means something only with sign-in on, on the web; in
        Flipparr View it always does (`signOutAlways`): with sign-in off it
        leaves the server. */}
    {actions.method === "forms" || actions.signOutAlways ? <div className="profile-menu-actions">
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
