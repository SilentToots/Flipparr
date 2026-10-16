// A settings card, and the controls used in one. Shared by Settings, the
// reader's settings drawer, the profile editor and Flipparr View.
//
// A card reads top to bottom as: its title, one sentence saying what it is
// for, the controls, what is set or happened (SettingsFacts), the buttons,
// and last a closed "More about this" for everything a person may want to
// know but does not need to scan (the owner, 2026-10-16; DESIGN_SYSTEM.md,
// "Settings copy").
import { useId } from "react";
import { CaretRight } from "@phosphor-icons/react";

export function SettingsCard({ title, description, more, action, className = "", children, ...rest }) {
  return <section className={`settings-card ${className}`.trim()} {...rest}>
    <header><h3>{title}</h3>{action}</header>
    {description ? <p className="settings-card-lead">{description}</p> : null}
    {children}
    {more ? <SettingsMore>{more}</SettingsMore> : null}
  </section>;
}

/** The card's footnote: closed until asked for, so the card stays one screen of controls. */
export function SettingsMore({ label = "More about this", children }) {
  return <details className="settings-more">
    <summary><CaretRight size={14} weight="bold" aria-hidden="true" />{label}</summary>
    <div className="settings-more-body">{children}</div>
  </details>;
}

/**
 * A switch with its name and one line on what it changes. The name alone is
 * the switch's accessible name; the line is its description, so a screen
 * reader does not read a paragraph as a label.
 */
export function Toggle({ checked, onChange, title, description, disabled = false }) {
  const id = useId();
  return <label className={`toggle-row${disabled ? " toggle-row--off" : ""}`}>
    <span><strong id={`${id}-name`}>{title}</strong>{description ? <small id={`${id}-hint`}>{description}</small> : null}</span>
    <input type="checkbox" checked={checked} disabled={disabled} aria-labelledby={`${id}-name`}
      aria-describedby={description ? `${id}-hint` : undefined} onChange={(event) => onChange(event.target.checked)} /><i />
  </label>;
}

/** A switch in a card's header, for the setting the card is about: the rest of the card is what it opens. */
export function HeaderToggle({ checked, onChange, label }) {
  return <label className="toggle-row toggle-row--header"><span className="sr-only">{label}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
}

/**
 * A labelled control that is not a switch -- a GlassSelect, an input -- with
 * an optional one-line hint. The label is always on screen; a GlassSelect is
 * a button, so this is a group, not a <label>.
 */
export function SettingsField({ label, hint, className = "", children }) {
  const id = useId();
  return <div className={`form-field settings-field ${className}`.trim()} role="group" aria-labelledby={`${id}-label`}>
    <span id={`${id}-label`}>{label}</span>
    {children}
    {hint ? <small>{hint}</small> : null}
  </div>;
}

/** What is set or what happened, as short label/value pairs instead of a sentence. */
export function SettingsFacts({ facts, className = "" }) {
  const shown = (facts || []).filter((fact) => fact && fact[1] !== null && fact[1] !== undefined && fact[1] !== "");
  if (!shown.length) return null;
  return <dl className={`system-facts settings-facts ${className}`.trim()}>
    {shown.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
  </dl>;
}
