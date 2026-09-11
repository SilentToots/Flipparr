import test from "node:test";
import assert from "node:assert/strict";
import { canDeleteJob, canDeletePull, waitingIssues } from "../src/pull-list.js";

test("an issue SABnzbd is still working on cannot be deleted", () => {
  // Its job is what imports the file when the download finishes.
  for (const downloadStatus of ["queued", "downloading", "completed", "importing", "waiting_for_files"]) {
    assert.equal(canDeleteJob({ status: "grabbed", downloadStatus }), false, downloadStatus);
  }
  assert.equal(canDeleteJob({ status: "searching" }), false);
});

test("anything not in flight can be deleted", () => {
  assert.equal(canDeleteJob({ status: "queued" }), true);
  assert.equal(canDeleteJob({ status: "failed" }), true);
  assert.equal(canDeleteJob({ status: "failed", downloadStatus: "failed" }), true);
  assert.equal(canDeleteJob({ status: "fulfilled", downloadStatus: "imported" }), true);
  assert.equal(canDeleteJob(null), false);
});

test("only a pull by name is deleted, and only when nothing is in flight", () => {
  assert.equal(canDeletePull({ coverage: "issues", jobs: [{ status: "queued" }] }), true);
  assert.equal(canDeletePull({ coverage: "issues", jobs: [] }), true);
  assert.equal(canDeletePull({ coverage: "run", jobs: [] }), false, "a followed run is unfollowed");
  assert.equal(canDeletePull({
    coverage: "issues", jobs: [{ status: "queued" }, { status: "grabbed", downloadStatus: "downloading" }],
  }), false);
});

test("an issue pulled before it ships is listed, though it has no job", () => {
  const request = {
    coverage: "issues",
    jobs: [{ issueId: "2", status: "queued" }],
    issues: [
      { id: "2", number: "2", ownership: "unowned" },
      { id: "3", number: "3", ownership: "unowned" },
      { id: "4", number: "4", ownership: "direct" },
    ],
  };
  assert.deepEqual(waitingIssues(request).map((issue) => issue.number), ["3"]);
  assert.deepEqual(waitingIssues({ ...request, coverage: "run" }), []);
  assert.deepEqual(waitingIssues(null), []);
});
