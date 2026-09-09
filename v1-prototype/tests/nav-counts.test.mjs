import test from "node:test";
import assert from "node:assert/strict";
import { jobsNeedingAttention } from "../src/nav-counts.js";

const req = (...jobs) => ({ jobs });

test("a followed run with nothing wrong badges nothing", () => {
  const catalog = { requests: [req({ status: "queued" }, { status: "grabbed" })] };
  assert.equal(jobsNeedingAttention(catalog), 0);
});

test("openRequests is not the count -- eight healthy requests still badge nothing", () => {
  // The bug this replaced: eight followed runs wore a permanent "8".
  const catalog = { requests: Array.from({ length: 8 }, () => req({ status: "queued" })) };
  assert.equal(jobsNeedingAttention(catalog), 0);
});

test("a job that failed counts, whichever half of it failed", () => {
  const catalog = {
    requests: [req({ status: "failed" }, { status: "queued" })],
    replacementRequests: [req({ status: "grabbed", downloadStatus: "failed" })],
  };
  assert.equal(jobsNeedingAttention(catalog), 2);
});

test("a job counts once even when both halves report failure", () => {
  const catalog = { requests: [req({ status: "failed", downloadStatus: "failed" })] };
  assert.equal(jobsNeedingAttention(catalog), 1);
});

test("an imported download is finished, not waiting", () => {
  const catalog = { requests: [req({ status: "fulfilled", downloadStatus: "imported" })] };
  assert.equal(jobsNeedingAttention(catalog), 0);
});

test("a catalog that has not loaded badges nothing rather than throwing", () => {
  assert.equal(jobsNeedingAttention(null), 0);
  assert.equal(jobsNeedingAttention({}), 0);
  assert.equal(jobsNeedingAttention({ requests: [{}], replacementRequests: [null] }), 0);
});

test("an unfollowed run stops badging the rail", () => {
  // openRequests was replaced because it counted inventory; this is the other
  // half of the same mistake -- a cancelled run counting as work outstanding.
  const catalog = { requests: [{ id: 1, status: "cancelled", jobs: [{ id: 3, status: "failed" }] }] };
  assert.equal(jobsNeedingAttention(catalog), 0);
});

test("the same failure on a live run still counts", () => {
  const catalog = { requests: [{ id: 1, status: "open", jobs: [{ id: 3, status: "failed" }] }] };
  assert.equal(jobsNeedingAttention(catalog), 1);
});
