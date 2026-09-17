import { test } from "node:test";
import assert from "node:assert/strict";
import { artTone } from "../src/art-tone.js";

const fill = (pixels) => {
  const data = new Uint8ClampedArray(pixels.length * 4);
  pixels.forEach(([r, g, b], i) => data.set([r, g, b, 255], i * 4));
  return data;
};
const repeat = (count, rgb) => Array.from({ length: count }, () => rgb);
const hue = (tone) => Number(/hsl\((\d+)/.exec(tone)[1]);
const light = (tone) => Number(/(\d+)%\)$/.exec(tone)[1]);

const relative = (hsl) => {
  const [, h, s, l] = /hsl\((\d+) (\d+)% (\d+)%\)/.exec(hsl).map(Number);
  const c = (1 - Math.abs(2 * (l / 100) - 1)) * (s / 100);
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l / 100 - c / 2;
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  const f = (v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r + m) + 0.7152 * f(g + m) + 0.0722 * f(b + m);
};
const contrast = (a, b) => (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);

test("a red cover gives a dark red tone", () => {
  const tone = artTone(fill(repeat(100, [200, 40, 30])));
  assert.ok(tone);
  assert.ok(hue(tone.tone) <= 10 || hue(tone.tone) >= 350, tone.tone);
  assert.ok(light(tone.deep) < light(tone.tone));
});

test("the art's colour wins over the black and grey most of it is", () => {
  const pixels = [...repeat(80, [10, 10, 10]), ...repeat(10, [128, 128, 128]), ...repeat(10, [30, 90, 210])];
  const tone = artTone(fill(pixels));
  assert.ok(tone);
  assert.ok(hue(tone.tone) > 200 && hue(tone.tone) < 240, tone.tone);
});

test("black-and-white art has no tone", () => {
  const pixels = [...repeat(50, [0, 0, 0]), ...repeat(50, [240, 240, 240])];
  assert.equal(artTone(fill(pixels)), null);
});

test("a speck of colour is an accent, not the art's colour", () => {
  const pixels = [...repeat(99, [20, 20, 20]), [255, 0, 0]];
  assert.equal(artTone(fill(pixels)), null);
});

test("muted and white text clear AA on the tone whatever the hue", () => {
  const muted = relative("hsl(240 6% 72%)");
  for (const rgb of [[240, 220, 20], [40, 220, 60], [20, 200, 220], [230, 40, 40], [140, 60, 220]]) {
    const tone = artTone(fill(repeat(50, rgb)));
    for (const shade of [tone.tone, tone.deep]) {
      assert.ok(contrast(1, relative(shade)) >= 4.5, `white on ${shade}`);
      assert.ok(contrast(muted, relative(shade)) >= 4.5, `muted on ${shade} from ${rgb}`);
    }
  }
});
