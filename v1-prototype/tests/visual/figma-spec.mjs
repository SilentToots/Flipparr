// What the Figma file says, as assertions.
//
// Every value here came from `mcp__Figma__get_design_context` on a node in
// node-id=1-86, and the `node` field records which one. Nothing here was read
// off a screenshot -- a picture cannot tell you a 40% fill from a solid one,
// or 8px radius from 11px, and both of those shipped wrong before this file
// existed.
//
// Run it with tests/visual/figma-check.mjs. It fails on any drift, because
// unlike a colour migration there is no legitimate drift from a specification.

export const spec = [
  // --- top bar, node 1:87 -------------------------------------------------
  { sel: ".appbar", node: "1:87", props: {
    backgroundColor: "rgb(9, 9, 11)", borderBottomColor: "rgb(63, 63, 70)",
    borderBottomWidth: "1px", paddingTop: "8px", paddingLeft: "20px" } },
  { sel: ".appbar-brand-group", node: "1:88", props: { columnGap: "20px" } },
  { sel: ".appbar .search-field", node: "1:108", box: { w: 600 }, props: {
    backgroundColor: "rgb(39, 39, 42)", borderTopColor: "rgb(113, 113, 122)",
    borderTopWidth: "1px", borderRadius: "100px",
    paddingLeft: "8px", paddingRight: "12px", paddingTop: "4px", columnGap: "4px" } },
  { sel: ".appbar-actions", node: "1:95", props: { columnGap: "12px" } },

  // --- sidebar, node 1:122 ------------------------------------------------
  { sel: ".sidebar", node: "1:122", box: { w: 228 }, props: {
    backgroundColor: "rgb(9, 9, 11)", padding: "16px", rowGap: "8px" } },
  { sel: ".nav-item.active", node: "1:152", box: { h: 44 }, props: {
    backgroundColor: "rgba(109, 40, 217, 0.2)", borderLeftWidth: "4px",
    borderLeftColor: "rgb(126, 34, 206)", borderRadius: "0px 8px 8px 0px",
    paddingLeft: "8px", paddingTop: "12px", columnGap: "4px",
    fontSize: "14px", fontWeight: "400" } },
  { sel: ".nav-item.active b", node: "I1:152;1:410", props: {
    backgroundColor: "rgb(0, 0, 0)", borderRadius: "100px",
    fontSize: "12px", fontWeight: "700", color: "rgb(161, 161, 170)" } },

  // --- page header and toolbar, node 1:269 --------------------------------
  { sel: ".page-header h1", node: "1:268", props: {
    fontSize: "20px", fontWeight: "800", color: "rgb(113, 113, 122)" } },
  { sel: ".library-summary", node: "1:363", props: { fontSize: "16px", columnGap: "4px" } },
  { sel: ".sync-indicator", node: "1:378", props: { padding: "4px", borderRadius: "4px" } },
  { sel: ".view-toggle", node: "1:320", props: {
    borderTopColor: "rgb(63, 63, 70)", borderTopWidth: "1px",
    borderRadius: "8px", padding: "4px", columnGap: "8px" } },
  { sel: ".view-toggle button.active", node: "1:319", props: {
    backgroundColor: "rgb(109, 40, 217)", borderRadius: "4px", padding: "4px" } },
  { sel: ".sort-field select", node: "1:331", box: { w: 200 }, props: {
    borderTopColor: "rgb(63, 63, 70)", borderTopWidth: "1px", borderRadius: "8px",
    paddingLeft: "8px", paddingTop: "4px",
    fontSize: "14px", fontWeight: "700", color: "rgb(113, 113, 122)" } },
  { sel: ".filter-button", node: "1:366", props: {
    borderTopColor: "rgb(63, 63, 70)", borderTopWidth: "1px", borderRadius: "8px",
    paddingLeft: "8px", paddingTop: "4px", columnGap: "4px",
    fontSize: "14px", fontWeight: "700", color: "rgb(113, 113, 122)" } },

  // --- comic card, node 1:495 (and 1:523 for the complete variant) --------
  { sel: ".series-card", node: "1:495", props: {
    backgroundColor: "rgb(9, 9, 11)", borderTopColor: "rgb(39, 39, 42)",
    borderRadius: "8px", rowGap: "12px", paddingBottom: "20px" } },
  { sel: ".series-card-identity", node: "I1:495;1:443", props: {
    rowGap: "2px", paddingLeft: "12px" } },
  { sel: ".series-card-identity strong", node: "I1:495;1:429", props: {
    fontSize: "14px", fontWeight: "700", color: "rgb(250, 250, 250)", whiteSpace: "nowrap" } },
  { sel: ".series-card-byline", node: "I1:495;1:442", props: { fontSize: "12px" } },
  { sel: ".series-card-statuses", node: "I1:495;1:444", props: {
    columnGap: "4px", paddingLeft: "12px" } },
  { sel: ".publication-status.ongoing", node: "I1:495;1:447", props: {
    backgroundColor: "rgba(109, 40, 217, 0.4)", borderRadius: "100px",
    padding: "3px 8px", columnGap: "2px",
    fontSize: "10px", fontWeight: "700", color: "rgba(255, 255, 255, 0.8)" } },
  { sel: ".publication-status.completed", node: "I1:523;1:636", props: {
    backgroundColor: "rgba(122, 122, 133, 0.4)" } },
  { sel: ".monitoring-status", node: "I1:495;1:448",
    synth: { parent: ".series-card-statuses", tag: "span", className: "monitoring-status" },
    props: { backgroundColor: "rgb(21, 128, 61)", color: "rgba(255, 255, 255, 0.8)" } },
  { sel: ".ownership.compact", node: "I1:495;1:484", props: {
    rowGap: "8px", paddingLeft: "12px" } },
  { sel: ".ownership.compact .ownership-label", node: "I1:495;1:478", props: {
    fontSize: "12px", color: "rgb(250, 250, 250)" } },
  { sel: ".ownership.compact .progress", node: "I1:495;1:485", props: {
    height: "8px", backgroundColor: "rgb(0, 0, 0)", borderRadius: "100px" } },
];

// The icon each node names, so a swap back to another set is caught. Heroicons
// draw a 24-box outline differently from a 20-box solid, so the variant is
// part of the specification, not an implementation detail.
export const icons = [
  { where: ".appbar-menu svg", node: "1:89", name: "heroicons-mini/bars-3", size: 20 },
  { where: ".appbar .search-field > svg", node: "1:110", name: "heroicons-solid/magnifying-glass", size: 16 },
  { where: ".appbar-notifications button svg", node: "1:96", name: "heroicons-outline/bell", size: 24 },
  { where: '[data-nav="library"] svg', node: "I1:152;1:407", name: "heroicons-mini/book-open", size: 20 },
  { where: '[data-nav="discover"] svg', node: "I1:157;1:149", name: "heroicons-outline/document-plus", size: 20 },
  { where: '[data-nav="requests"] svg', node: "I1:167;1:149", name: "heroicons-outline/wallet", size: 20 },
  { where: '[data-nav="metadata"] svg', node: "I1:174;1:383", name: "heroicons-outline/clipboard-document-list", size: 20 },
  { where: '.view-toggle button[aria-label="Grid view"] svg', node: "1:293", name: "heroicons-mini/squares-2x2", size: 20 },
  { where: '.view-toggle button[aria-label="List view"] svg', node: "1:314", name: "heroicons-solid/list-bullet", size: 20 },
  { where: ".filter-button svg", node: "1:367", name: "heroicons-outline/check-circle", size: 20 },
  { where: ".publication-status svg", node: "I1:495;1:462", name: "heroicons-micro/bolt", size: 12 },
  // Not reachable from data unless a series is followed; the badge's own rule
  // is asserted above by synthesising it.
]

export const iconModules = [
  { name: "heroicons-micro/bookmark", node: "I1:495;1:458", from: "@heroicons/react/16/solid", export: "BookmarkIcon" },
];
