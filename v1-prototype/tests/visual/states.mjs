// The screens a colour change can break, and how to reach each one.
//
// `require` is the guard against a false clean: dev:uiqa proxies to a
// tunnelled backend, and if that tunnel is down every screen renders its
// empty state and a capture "succeeds" having photographed nothing.
// A state whose required selectors are missing fails the run.

const settle = async (page, ms = 400) => page.waitForTimeout(ms);

async function openFirstSeries(page) {
  await page.waitForSelector(".series-card", { timeout: 15000 });
  await page.locator(".series-card").first().click();
  await page.waitForSelector(".series-drawer", { timeout: 15000 });
  await settle(page);
}

async function drawerTab(page, label) {
  await openFirstSeries(page);
  await page.locator(`.drawer-tabs button:has-text("${label}")`).first().click();
  await settle(page);
}

// `phone: false` keeps a state to the desktop pass: the list toggle and the
// Following filter live in a sheet on a phone.
export const states = [
  {
    name: "library-grid",
    path: "/library",
    require: [".series-card", ".page-header", ".sidebar"],
    // A phone has no app bar; its header is part of the Comics screen.
    phoneRequire: [".series-card", ".page-header-search", ".sidebar"],
  },
  {
    name: "library-list",
    phone: false,
    path: "/library",
    require: [".series-row"],
    async setup(page) {
      await page.waitForSelector(".library-view-toggle", { timeout: 15000 });
      await page.locator('.library-view-toggle [aria-label="List view"]').first().click();
      await settle(page);
    },
  },
  {
    name: "library-following",
    phone: false,
    path: "/library",
    // The catalog takes seconds over the tunnel, and `.filter-button` is in
    // the header before any of it arrives -- so this used to photograph the
    // loading skeleton and pass, which is the false clean `require` exists to
    // stop. A card has to be on screen for the shot to mean anything.
    waitForCatalog: true,
    require: [".filter-button", ".series-card"],
    async setup(page) {
      await page.waitForSelector(".filter-button", { timeout: 15000 });
      await page.locator(".filter-button").first().click();
      await settle(page);
    },
  },
  {
    // Requires the menu, not just the bar: a click that lands before the
    // bell has items photographs the closed state, and the run reads clean
    // having captured the same page as library-grid.
    name: "notifications-open",
    // The phone's bell is a different control in the Comics header.
    phone: false,
    path: "/library",
    waitForCatalog: true,
    require: [".page-header", ".notifications-menu", ".series-card"],
    async setup(page) {
      await page.waitForSelector(".appbar-notifications button", { timeout: 15000 });
      await page.locator(".appbar-notifications button").first().click();
      await page.waitForSelector(".notifications-menu", { timeout: 15000 });
      await settle(page);
    },
  },
  { name: "drawer-overview", path: "/library", require: [".series-drawer"], setup: openFirstSeries },
  {
    name: "drawer-issues",
    path: "/library",
    require: [".series-drawer"],
    setup: (page) => drawerTab(page, "Issues"),
  },
  {
    // Photographed on a phone now: it is the one surface that has had a Read
    // button all along, and it had never been captured at 375px.
    name: "drawer-files",
    path: "/library",
    require: [".series-drawer"],
    setup: (page) => drawerTab(page, "Files"),
  },
  // Edit is pushed over the tabs rather than opened as a window, so both the
  // list and a section have to be photographed: a click that lands before the
  // panel renders captures the drawer underneath and the run reads clean.
  {
    name: "drawer-edit",
    path: "/library",
    require: [".edit-rows", ".edit-identity"],
    async setup(page) {
      await openFirstSeries(page);
      await page.locator(".comic-drawer-edit-button").click();
      await page.waitForSelector(".edit-rows", { timeout: 15000 });
      await settle(page);
    },
  },
  {
    name: "drawer-edit-cover",
    path: "/library",
    require: [".cover-option-grid"],
    async setup(page) {
      await openFirstSeries(page);
      await page.locator(".comic-drawer-edit-button").click();
      await page.locator(".edit-row").first().click();
      await page.waitForSelector(".cover-option-grid", { timeout: 15000 });
      await settle(page, 800);
    },
  },
  // The drawer lost its Aliases tab to Advanced, and Collection only exists
  // with collected editions switched on, which the QA library leaves off.
  {
    name: "drawer-advanced",
    path: "/library",
    require: [".series-drawer"],
    setup: (page) => drawerTab(page, "Advanced"),
  },
  // The release shelves come from Metron, which answers differently -- or not
  // at all -- from one run to the next, and a shelf that loads in one capture
  // and not the other buries anything a style change did under two thousand
  // elements. They are stubbed (stubs.mjs).
  {
    // The end of an issue. Reached by seeding the last page, not by paging
    // through a comic, which is thirty-odd requests and flaky. The place is
    // *routed*, not written: this harness runs against the shared library, and
    // a capture that left a finished record on someone's comic would be a
    // capture that changed what it photographs. The POST is swallowed for the
    // same reason.
    name: "reader-finish",
    path: "/library",
    waitForCatalog: true,
    require: [".finish-drawer", ".finish-card", ".finish-next"],
    async setup(page) {
      const origin = new URL(page.url()).origin;
      const catalog = await (await page.request.get(`${origin}/api/v1/catalog`)).json();
      // A run with at least two readable issues, so there is a next one.
      const run = (catalog.series || []).find((item) => (item.issues || []).filter((issue) => issue.fileId).length >= 2);
      if (!run) throw new Error("reader-finish: no run with two readable issues in this library");
      const fileId = run.issues.find((issue) => issue.fileId).fileId;
      const pages = await (await page.request.get(`${origin}/api/v1/files/${fileId}/pages`)).json();
      const last = Number(pages.pageCount) - 1;
      await page.route(`**/api/v1/files/${fileId}/progress`, (route) => route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify(route.request().method() === "GET"
          ? { fileId: String(fileId), page: last, pageCount: pages.pageCount, finishedAt: null }
          : {}),
      }));
      await page.goto(`${origin}/?read=${fileId}`, { waitUntil: "networkidle", timeout: 45000 });
      await page.waitForSelector('.reader-bar--bottom:has-text("Last page")', { timeout: 30000 });
      await page.keyboard.press("ArrowRight");
      await page.waitForSelector(".finish-drawer", { timeout: 15000 });
      await settle(page, 800);
    },
  },
  { name: "discover", path: "/discover", stub: ["releases"], waitForCatalog: true, require: [".pull-card:not(.pull-card-skeleton)"], phoneRequire: [".page-header .glass-field", ".pull-card:not(.pull-card-skeleton)"] },
  { name: "pull-list", path: "/pull-list", waitForCatalog: true, require: [".segmented-tabs", ".request-row, .empty-state"] },
  { name: "library-health", path: "/settings/health", require: [".metadata-layout, .empty-state"] },
  { name: "settings", path: "/settings/library", require: [".settings-shell"] },
  // The folders panel only renders once the catalog has loaded; the header
  // alone let a capture land on "0 Files" and a page 120px shorter.
  { name: "import", path: "/import", require: [".page-header", ".library-sources-panel"], phone: false },
];

// A screen the Vite proxy cannot reach: it only forwards /api, so the intake
// surface has to be visited on the backend origin. It consumes the same
// tokens and would otherwise drift unnoticed.
export const backendStates = [
  { name: "intake", path: "/library/intake", require: ["body"] },
];
