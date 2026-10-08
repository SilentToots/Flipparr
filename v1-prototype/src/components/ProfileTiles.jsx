// A profile's disc, the profile tiles every picker shows, and the PIN pad:
// shared by the web app and Flipparr Reader, so a household sees the same
// "Who's reading?" on both.
import { useEffect, useRef, useState } from "react";
import { Backspace, Check, LockSimple, Plus } from "@phosphor-icons/react";
import { LoadingIndicator as LoadingSpinner } from "./LoadingIndicator";
import { initials, isLocked, pinInput, profileColour } from "../profiles.js";

// A profile is drawn as a coloured disc with its initials, the way a Plex Home
// or Netflix profile is; the colours are the design tokens' profile palette.
export function ProfileAvatar({ profile, size = "md" }) {
  return <span className={`profile-avatar profile-avatar--${size} profile-avatar--${profileColour(profile)}`} aria-hidden="true">
    {profile?.avatar ? <img src={profile.avatar} alt="" draggable="false" /> : initials(profile?.name)}
  </span>;
}

// Profiles to choose from, as Plex and Netflix show them everywhere a profile
// is chosen -- the picker, the header's menu, its sheet: a large disc, the name
// under it, and a word for the one reading now or one that asks for a PIN.
export function ProfileTiles({ profiles, current, onChoose, onAdd = null, busy = false }) {
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

// A PIN the way a phone's lock screen asks for one: a dot for each digit and
// a pad of keys -- no field, no Continue. The last digit opens the profile.
// Bottom right is Cancel until there is a digit to delete. A PIN whose length
// is not known yet (set before lengths were kept) gets a ✓ key, once.
export const PIN_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];

export function PinPad({ length = null, busy = false, error = "", onSubmit, onCancel, onInput }) {
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
