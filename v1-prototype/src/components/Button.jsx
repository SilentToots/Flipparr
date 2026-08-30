import { LoadingIndicator } from "./LoadingIndicator";

const VALID_VARIANTS = new Set(["primary", "secondary", "ghost", "danger"]);
const VALID_SIZES = new Set(["sm", "md"]);

export function Button({
  variant = "primary",
  size = "md",
  busy = false,
  busyLabel = "Working…",
  icon = null,
  className = "",
  disabled = false,
  children,
  ...props
}) {
  const variantName = VALID_VARIANTS.has(variant) ? variant : "primary";
  const sizeName = VALID_SIZES.has(size) ? size : "md";
  const classes = [
    "ui-button",
    `ui-button--${variantName}`,
    `ui-button--${sizeName}`,
    className,
  ].filter(Boolean).join(" ");

  return (
    <button
      {...props}
      className={classes}
      aria-busy={busy || undefined}
      disabled={disabled || busy}
    >
      {busy ? <LoadingIndicator size={sizeName === "sm" ? "xs" : "sm"} /> : icon}
      <span className="ui-button__label">{busy ? busyLabel : children}</span>
    </button>
  );
}
