const VALID_TONES = new Set(["amber", "violet", "red", "green", "muted"]);

export function StatusBadge({ tone = "muted", icon = null, className = "", children }) {
  const toneName = VALID_TONES.has(tone) ? tone : "muted";
  const classes = ["status-badge", `status-badge--${toneName}`, className].filter(Boolean).join(" ");

  return <span className={classes}>{icon}{children}</span>;
}
