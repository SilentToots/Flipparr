# Accessibility audit

The same screens the visual harness photographs (`../visual/states.mjs`),
at 1440, 768, 375 and 320px, through axe-core and four probes of our own.
What it checks and the rules that follow from it are in
`../../DESIGN_SYSTEM.md`, "Accessibility".

## Running it

Against the scratch stack described in `../visual/README.md` (the audit
needs the same library: a followed run, one in progress, one with two
readable issues):

```
VISUAL_APP_ORIGIN=http://localhost:8802 npm run a11y:audit -- --out /tmp/a11y
```

`--only name,name` runs a few states. The run prints one line per screen
and width, lists every axe rule violated with the nodes it hit, and writes
`a11y-report.json` to `--out`. It exits 1 on a serious or critical axe
violation, a page that scrolls sideways at 320px, or a keyboard stop with
no visible focus indicator. Small targets and moderate axe findings are
printed but do not fail the run; read them.

It is not in CI because it needs a library, as the visual harness does. Run
it for a change to a control, a focus style, a colour pair, or anything that
moves at 320px, and record the result in the commit.

## What the probes do

- **Reflow**: `scrollWidth` against `clientWidth` on the document, plus the
  first few elements past the right edge, ignoring shelves that scroll
  sideways on purpose.
- **Targets**: every button, link, input and ARIA control under 24px in
  either direction. A control inside its `<label>` is measured by the
  label; `.sr-only` helpers, 1px hidden inputs and inline text links are
  not targets.
- **Keyboard**: Tab until the ring wraps, at most 60 stops. Each stop's
  outline, box-shadow, border and background are compared with the same
  element blurred, and with its parent's (a field that draws its ring with
  `:focus-within`), so a ring on either counts. A stop on an invisible
  element is a failure.
- **Motion**: under `prefers-reduced-motion: reduce`, the longest computed
  `animation-duration` or `transition-duration` on the page; over 20ms
  fails.

## First pass

2026-10-06, 45 states x 4 widths: no overflow at 320px, no motion under
reduced motion, every focus stop indicated. One serious finding -- the Read
button's issue number at 3.67:1 on the violet, faded with `opacity` -- and
two kinds of small target (13px native checkboxes; the 20px *See all*
button), all fixed in the shared rules; re-run clean -- axe reports no
violation of any level on any screen.
