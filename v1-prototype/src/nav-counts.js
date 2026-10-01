// What a badge on the left rail counts.
//
// A rail badge is a count of things waiting on the reader, not an inventory.
// `stats.openRequests` was standing in for it and counts runs being followed,
// so a healthy library that follows eight runs wore a permanent "8" that no
// action could clear.
//
// A failed job is the thing that genuinely stops: it will not progress until
// someone retries it or picks a release by hand. Both request kinds carry
// their jobs, and either the job or its download can be the part that failed.

// A download the person stopped is not a failure: the issue is wanted again,
// and another release is already being looked for.
export const jobHasFailed = (job) =>
  job?.status === "failed" || (job?.downloadStatus === "failed" && !job?.downloadStopped);

export function jobsNeedingAttention(catalog) {
  // A cancelled run is not waiting on anyone; unfollow is the ordinary route
  // into that state and its jobs keep whatever status they last had.
  const jobsOf = (list) => (list || [])
    .filter((request) => request?.status !== "cancelled")
    .flatMap((request) => request?.jobs || []);
  return [...jobsOf(catalog?.requests), ...jobsOf(catalog?.replacementRequests)]
    .filter(jobHasFailed).length;
}
