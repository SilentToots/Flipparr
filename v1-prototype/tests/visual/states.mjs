// The screens a colour change can break, and how to reach each one.
//
// `require` is the guard against a false clean: dev:uiqa proxies to a
// tunnelled backend, and if that tunnel is down every screen renders its
// empty state and a capture "succeeds" having photographed nothing.
// A state whose required selectors are missing fails the run.

const settle = async (page, ms = 400) => page.waitForTimeout(ms);

async function openFirstSeries(page) {
  // A run's card, not a collection's: collections lead the grid (2026-10-01).
  await page.waitForSelector(".series-card:not(.collection-card)", { timeout: 15000 });
  await page.locator(".series-card:not(.collection-card)").first().click();
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
    path: "/library/all",
    require: [".series-card", ".page-header", ".sidebar"],
    // A phone has no app bar; its header is part of the Comics screen, with
    // search a button by the bell (2026-10-01).
    phoneRequire: [".series-card", ".appbar-search", ".sidebar"],
  },
  {
    name: "library-list",
    phone: false,
    path: "/library/all",
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
    path: "/library/all",
    // The catalog takes seconds over the tunnel, and `.filter-button` is in
    // the header before any of it arrives -- so this used to photograph the
    // loading skeleton and pass, which is the false clean `require` exists to
    // stop. A card has to be on screen for the shot to mean anything.
    waitForCatalog: true,
    require: [".library-view-button", ".series-card"],
    // One View & sort button on every width (2026-09-29): the filters live in
    // its drawer, so the state opens it, flips the switch and closes it.
    async setup(page) {
      await page.waitForSelector(".library-view-button", { timeout: 15000 });
      await page.locator(".library-view-button").click();
      await page.getByRole("switch", { name: "Following only" }).click();
      await page.locator(".library-sheet-done").click();
      await settle(page);
    },
  },
  {
    // The runs you are reading. Depends on the shared library having some --
    // the same dependence library-following has on followed runs.
    name: "library-in-progress",
    phone: false,
    path: "/library/all",
    waitForCatalog: true,
    require: [".library-view-button", ".series-card"],
    async setup(page) {
      await page.waitForSelector(".library-view-button", { timeout: 15000 });
      await page.locator(".library-view-button").click();
      await page.getByRole("switch", { name: "In progress only" }).click();
      await page.locator(".library-sheet-done").click();
      await settle(page);
    },
  },
  {
    // Requires the popover, not just the bar: a click that lands before the
    // bell is ready photographs the closed state, and the run reads clean
    // having captured the same page as library-grid. The bell opens a glass
    // popover (`.notifications-popover`) above 640px and a full-screen sheet
    // (`.notifications-sheet`) on a phone; the menu it replaced is gone.
    // Opening it marks the news read on the server, which a scratch library
    // does not mind and the shared one would.
    name: "notifications-open",
    // The phone's bell is a different control in the Comics header.
    phone: false,
    path: "/library/all",
    waitForCatalog: true,
    require: [".page-header", ".notifications-popover", ".series-card"],
    async setup(page) {
      await page.waitForSelector(".appbar-notifications button", { timeout: 15000 });
      await page.locator(".appbar-notifications button").first().click();
      await page.waitForSelector(".notifications-popover", { timeout: 15000 });
      await settle(page);
    },
  },
  { name: "drawer-overview", path: "/library/all", require: [".series-drawer"], setup: openFirstSeries },
  {
    name: "drawer-issues",
    path: "/library/all",
    require: [".series-drawer"],
    setup: (page) => drawerTab(page, "Issues"),
  },
  {
    // Photographed on a phone now: it is the one surface that has had a Read
    // button all along, and it had never been captured at 375px.
    name: "drawer-files",
    path: "/library/all",
    require: [".series-drawer"],
    setup: (page) => drawerTab(page, "Files"),
  },
  // Edit is pushed over the tabs rather than opened as a window, so both the
  // list and a section have to be photographed: a click that lands before the
  // panel renders captures the drawer underneath and the run reads clean.
  {
    name: "drawer-edit",
    path: "/library/all",
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
    path: "/library/all",
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
    path: "/library/all",
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
    path: "/library/all",
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
  { name: "discover", path: "/discover", stub: ["releases"], waitForCatalog: true, require: [".pull-card:not(.pull-card-skeleton)"], phoneRequire: [".page-header .appbar-search", ".pull-card:not(.pull-card-skeleton)"] },
  // Wanted is the first tab -- a request's row or the "All caught up" empty
  // state -- unless a reader's request is waiting, when the page opens on the
  // Requests tab instead: a queue row on desktop, a swipe card on a phone, and
  // no empty state at all. Every one of those is proof of content, but they
  // are different pages, so the baseline is made with no request pending (see
  // README.md, "What the library needs").
  { name: "pull-list", path: "/pull-list", waitForCatalog: true, require: [".segmented-tabs", ".request-row, .request-queue-row, .request-swipe, .request-empty"] },
  // Comics' home (since 2026-10-01): shelves, or the empty library's state.
  // Search: before a query (recents and the hint), and a library search as
  // typed -- no catalogs, which are asked only on Enter (2026-10-01).
  { name: "search-empty", path: "/search", require: [".search-hint"] },
  { name: "search-results", path: "/search?q=saga", stub: ["search"], waitForCatalog: true, require: [".discover-results"] },
  { name: "library-recommended", path: "/library", waitForCatalog: true, require: [".library-shelf, .empty-state"] },
  { name: "library-collections", path: "/library/collections", waitForCatalog: true, require: [".collection-card, .empty-state"] },
  { name: "library-reading", path: "/library/reading", waitForCatalog: true, require: [".series-card, .empty-state"] },
  { name: "library-health", path: "/settings/health", require: [".metadata-layout, .empty-state"] },
  { name: "settings", path: "/settings/library", require: [".settings-shell"] },
  { name: "settings-reader", path: "/settings/reader", require: [".settings-card"] },
  { name: "settings-acquisition", path: "/settings/acquisition", require: [".settings-card"] },
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
