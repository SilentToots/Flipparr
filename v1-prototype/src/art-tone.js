// A drawer's background colour, taken from its art the way Plex's detail
// page takes it from a poster.
//
// The art's most vibrant hue family wins, not its average: an average of a
// cover is usually brown, and the grey and black most covers are mostly made
// of say nothing about them. The winner is then set dark enough that white
// text on it clears AA with room to spare, and a deeper version of it carries
// the page below the header. Art with no real colour gets no tone, and the
// drawer keeps its own.

const HUE_BINS = 12;
// A pixel counts once it is this colourful and this bright.
const MIN_SATURATION = 0.22;
const MIN_VALUE = 0.18;
const MAX_VALUE_FOR_GREY = 0.97;
// Below this share of the image, the colour is an accent, not the art's.
const MIN_COLOURFUL_SHARE = 0.04;
// The muted text colour (#b4b4bb, luminance ~0.46) clears 4.5:1 on anything
// at or under 0.06; the deep tone keeps a wide margin under the page's copy.
const TONE_LUMINANCE = 0.06;
const DEEP_LUMINANCE = 0.025;

function toHsv(r, g, b) {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;
  let h = 0;
  if (delta) {
    if (max === r) h = ((g - b) / delta) % 6;
    else if (max === g) h = (b - r) / delta + 2;
    else h = (r - g) / delta + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  return { h, s: max ? delta / max : 0, v: max / 255 };
}

function toHsl(r, g, b) {
  const [rn, gn, bn] = [r / 255, g / 255, b / 255];
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  const d = max - min;
  const s = d ? d / (1 - Math.abs(2 * l - 1)) : 0;
  return { h: toHsv(r, g, b).h, s, l };
}

function hslToRgb(h, s, l) {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x]
    : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  return [r + m, g + m, b + m];
}

// WCAG relative luminance of a colour given as 0-1 channels.
function luminance([r, g, b]) {
  const f = (v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

// The lightest step at or below `start` whose luminance is at most `ceiling`.
// Yellows and greens are far brighter than reds at the same lightness, so a
// fixed lightness would put the muted text colour under AA on some covers.
function darkEnough(h, s, start, ceiling) {
  let l = start;
  while (l > 0.04 && luminance(hslToRgb(h, s, l)) > ceiling) l -= 0.01;
  return l;
}

function hslString(h, s, l) {
  return `hsl(${Math.round(h)} ${Math.round(s * 100)}% ${Math.round(l * 100)}%)`;
}

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

/**
 * Two tones from RGBA pixel data (as a canvas gives it), or null when the
 * art has no colour worth taking: `tone` for the header's edge and the top of
 * the page, `deep` for the rest of it.
 */
export function artTone(pixels) {
  const bins = Array.from({ length: HUE_BINS }, () => ({ weight: 0, r: 0, g: 0, b: 0 }));
  let counted = 0;
  let colourful = 0;
  for (let i = 0; i + 3 < pixels.length; i += 4) {
    if (pixels[i + 3] < 128) continue;
    counted += 1;
    const r = pixels[i];
    const g = pixels[i + 1];
    const b = pixels[i + 2];
    const { h, s, v } = toHsv(r, g, b);
    if (s < MIN_SATURATION || v < MIN_VALUE || (s < 0.3 && v > MAX_VALUE_FOR_GREY)) continue;
    colourful += 1;
    const weight = s * v;
    const bin = bins[Math.floor(h / (360 / HUE_BINS)) % HUE_BINS];
    bin.weight += weight;
    bin.r += r * weight;
    bin.g += g * weight;
    bin.b += b * weight;
  }
  if (!counted || colourful / counted < MIN_COLOURFUL_SHARE) return null;
  // A hue family and its neighbours, so a colour that straddles two bins is
  // not split in half and beaten by a smaller one.
  let best = -1;
  let bestWeight = 0;
  bins.forEach((bin, index) => {
    const around = bin.weight
      + 0.5 * bins[(index + HUE_BINS - 1) % HUE_BINS].weight
      + 0.5 * bins[(index + 1) % HUE_BINS].weight;
    if (bin.weight && around > bestWeight) {
      bestWeight = around;
      best = index;
    }
  });
  if (best < 0) return null;
  const bin = bins[best];
  const { h, s } = toHsl(bin.r / bin.weight, bin.g / bin.weight, bin.b / bin.weight);
  const saturation = clamp(s, 0.35, 0.7);
  const deepSaturation = clamp(saturation * 0.8, 0.25, 0.55);
  return {
    tone: hslString(h, saturation, darkEnough(h, saturation, 0.26, TONE_LUMINANCE)),
    deep: hslString(h, deepSaturation, darkEnough(h, deepSaturation, 0.12, DEEP_LUMINANCE)),
  };
}
