// A settings card, and the switches used in one. Shared by Settings and the
// reader's settings drawer.

export function SettingsCard({ title, action, className = "", children }) {
  return <section className={`settings-card ${className}`.trim()}>
    <header><h3>{title}</h3>{action}</header>
    {children}
  </section>;
}

export function Toggle({ checked, onChange, title, description, disabled = false }) {
  return <label className={`toggle-row${disabled ? " toggle-row--off" : ""}`}><span><strong>{title}</strong>{description ? <small>{description}</small> : null}</span><input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
}

/** A switch in a card's header, for the setting the card is about: the rest of the card is what it opens. */
export function HeaderToggle({ checked, onChange, label }) {
  return <label className="toggle-row toggle-row--header"><span className="sr-only">{label}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
}
