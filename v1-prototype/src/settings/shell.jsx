// Settings' layout, shared by the web app and Flipparr View (the owner,
// 2026-10-10: the app's Settings was one long page). Above 640px the
// sections are listed beside the one open; on a phone the list is a page of
// its own, grouped as iPhone Settings is, and each section opens over it
// with a Back. The sections and their contents are the host's.
import { ArrowLeft, CaretRight } from "@phosphor-icons/react";
import { PageHeader } from "../frame/header.jsx";
import { useBuild } from "./about.jsx";

// A phone's first level of Settings, after iPhone Settings: grouped rows of
// an icon and a name, each opening its section as a page. A count rides
// beside the name rather than in a column of its own, which would set every
// row's columns differently and stagger the chevrons.
export function SettingsIndex({ counts = {}, onOpen, sections }) {
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

/**
 * One section open, as the web's Settings draws it: the page header (with
 * Back on a phone), the section list beside the pane above 640px, and the
 * pane's own header -- then `children`, the section's cards. `counts`
 * badges a section in the list (Library health's decisions).
 */
export function SettingsFrame({ sections, open, phone, onSectionChange, counts = {}, wide = false, children }) {
  return <>
    <PageHeader title={phone ? open.label : "Settings"}
      leading={phone ? <button type="button" className="glass-button glass-button--icon" onClick={() => onSectionChange("")} aria-label="Back to Settings"><ArrowLeft size={20} /></button> : null} />
    <div className={`settings-shell${wide ? " settings-shell--wide" : ""}`}>
      {/* The column runs the panel's full height, for its rule; the list in
          it stays in view as a long section scrolls. */}
      {phone ? null : <nav className="settings-nav" aria-label="Settings sections"><div>
        {sections.map(({ id, label }) => <button type="button" key={id}
          className={id === open.id ? "active" : ""} aria-current={id === open.id ? "page" : undefined}
          onClick={() => onSectionChange(id)}>
          <span>{label}</span>{counts[id] ? <b>{counts[id]}</b> : null}
        </button>)}
      </div></nav>}
      <div className="settings-pane">
        <header className="settings-pane-header">
          {phone ? null : <h2>{open.label}</h2>}
          <p>{open.detail}</p>
        </header>
        {children}
      </div>
    </div>
  </>;
}
