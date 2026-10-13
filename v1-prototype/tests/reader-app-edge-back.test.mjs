// iOS's edge swipe is Back in Flipparr View (src/reader-app/edge-back.js):
// on while a drawer or sheet is in front, never over the reader.
import { test } from "node:test";
import assert from "node:assert/strict";
import { backClosable, closeFrontForBack, dialogClosers, openDialogs } from "../src/dialog-stack.js";

const layer = (classes) => ({ classList: { contains: (name) => classes.includes(name) } });

test("Back closes the drawer or sheet in front, and never the reader", () => {
  const drawer = layer(["series-drawer"]);
  const reader = layer(["reader"]);
  let closed = 0;
  dialogClosers.set(drawer, () => { closed += 1; });
  dialogClosers.set(reader, () => { closed += 100; });
  openDialogs.push(reader);
  assert.equal(backClosable(), false, "the reader's left edge turns pages");
  assert.equal(closeFrontForBack(), false);
  openDialogs.push(drawer);
  assert.equal(backClosable(), true, "a drawer over the reader closes");
  assert.equal(closeFrontForBack(), true);
  assert.equal(closed, 1);
  openDialogs.length = 0;
  assert.equal(backClosable(), false, "nothing in front: nothing to go back from");
});
