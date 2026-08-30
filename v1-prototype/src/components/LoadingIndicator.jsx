const SIZE_ALIASES = {
  14: "xs",
  15: "xs",
  16: "sm",
  17: "sm",
  18: "md",
  19: "md",
  20: "md",
  21: "lg",
  22: "lg",
  24: "xl",
  25: "xl",
  28: "xl",
};

const VALID_SIZES = new Set(["xs", "sm", "md", "lg", "xl"]);

function normalizeSize(size) {
  if (typeof size === "number") return SIZE_ALIASES[size] || "md";
  return VALID_SIZES.has(size) ? size : "md";
}

export function LoadingIndicator({ size = "md", className = "", label = "" }) {
  const sizeName = normalizeSize(size);
  const classes = ["loading-indicator", `loading-indicator--${sizeName}`, className].filter(Boolean).join(" ");

  return (
    <span
      className={classes}
      role={label ? "status" : undefined}
      aria-label={label || undefined}
      aria-hidden={label ? undefined : "true"}
    >
      {Array.from({ length: 8 }, (_, index) => (
        <i className="loading-indicator__mark" key={index} />
      ))}
    </span>
  );
}
