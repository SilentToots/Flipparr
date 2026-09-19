import test from "node:test";
import assert from "node:assert/strict";
import { countUpDuration, countUpValue } from "../src/count-up.js";

test("a bigger jump takes longer, up to a cap", () => {
  assert.equal(countUpDuration(10, 11), 232);
  assert.equal(countUpDuration(1511, 1544), 616);
  assert.ok(countUpDuration(0, 5000) === 900, "a huge jump still lands within 900ms");
  assert.equal(countUpDuration(42, 12), countUpDuration(12, 42), "counting down takes as long");
});

test("it starts where it was, lands on the value, and never overshoots", () => {
  const d = countUpDuration(1511, 1544);
  assert.equal(countUpValue(1511, 1544, 0, d), 1511);
  assert.equal(countUpValue(1511, 1544, d, d), 1544);
  assert.equal(countUpValue(1511, 1544, d * 2, d), 1544, "past the end it holds");
  const mid = countUpValue(1511, 1544, d / 2, d);
  assert.ok(mid > 1511 && mid < 1544, `halfway is between: ${mid}`);
  assert.ok(mid > 1511 + 33 / 2, "and past halfway, because it starts fast");
});

test("counting down works the same way", () => {
  const d = countUpDuration(42, 12);
  assert.equal(countUpValue(42, 12, d, d), 12);
  const mid = countUpValue(42, 12, d / 2, d);
  assert.ok(mid < 42 && mid > 12, `halfway is between: ${mid}`);
});

test("a zero duration lands immediately rather than dividing by zero", () => {
  assert.equal(countUpValue(0, 9, 0, 0), 9);
});
