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
// Following filter live in a sheet on a phone, and the drawer's Files tab is a
// table built for width.
export const states = [
  {
    name: "library-grid",
    path: "/library",
    require: [".series-card", ".appbar", ".sidebar"],
    // A phone has no app bar; its header is part of the Comics screen.
    phoneRequire: [".series-card", ".library-phone-header", ".sidebar"],
  },
  {
    name: "library-list",
    phone: false,
    path: "/library",
    require: [".series-row"],
    async setup(page) {
      await page.waitForSelector(".view-toggle", { timeout: 15000 });
      await page.locator('.view-toggle button[aria-label="List view"]').first().click();
      await settle(page);
    },
  },
  {
    name: "library-following",
    phone: false,
    path: "/library",
    require: [".filter-button"],
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
    require: [".appbar", ".notifications-menu"],
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
    name: "drawer-files",
    phone: false,
    path: "/library",
    require: [".series-drawer"],
    setup: (page) => drawerTab(page, "Files"),
  },
  // The drawer lost its Aliases tab to Advanced, and Collection only exists
  // with collected editions switched on, which the QA library leaves off.
  {
    name: "drawer-advanced",
    path: "/library",
    require: [".series-drawer"],
    setup: (page) => drawerTab(page, "Advanced"),
  },
  // Wait for real release cards: the shelves load from Metron after the page,
  // and a capture that lands before them differs from one that lands after by
  // two thousand elements, which buries anything a style change did.
  { name: "discover", path: "/discover", require: [".discover-hero", ".pull-card:not(.pull-card-skeleton)"] },
  { name: "pull-list", path: "/pull-list", require: [".request-tabs"] },
  { name: "library-health", path: "/settings/health", require: [".metadata-layout, .empty-state"] },
  { name: "settings", path: "/settings", require: [".settings-layout"] },
  { name: "import", path: "/import", require: [".page-header"], phone: false },
];

// A screen the Vite proxy cannot reach: it only forwards /api, so the intake
// surface has to be visited on the backend origin. It consumes the same
// tokens and would otherwise drift unnoticed.
export const backendStates = [
  { name: "intake", path: "/library/intake", require: ["body"] },
];
