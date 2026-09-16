// Screens that read a third-party API are stubbed. A design check that fails
// because Metron is busy teaches nobody anything, and a search whose results
// change week to week cannot pin a card's measurements.
const shelfIssues = (shelf) => Array.from({ length: 14 }, (_, i) => ({
  providerIssueId: `${shelf}-${i}`, providerSeriesId: "9",
  seriesTitle: "Wolverine", number: String(i + 1),
  title: `Wolverine #${i + 1}`, cover: null, storeDate: "2026-09-02",
}));

export const STUBS = {
  releases: {
    url: "**/api/v1/discover/releases",
    body: {
      available: true,
      // Enough to overflow the row: the forward chevron is only enabled when
      // there is somewhere to scroll to, and it is one of the things measured.
      latest: { date: "2026-09-02", issues: shelfIssues("latest") },
      upcoming: { date: "2026-09-09", issues: shelfIssues("upcoming") },
      previous: { date: "2026-08-26", issues: shelfIssues("previous") },
    },
  },
  search: {
    url: "**/api/v1/discover?**",
    body: {
      query: "Batman", titleQuery: "Batman", yearHint: null,
      provider: "Metron", providerId: "metron",
      providersChecked: ["Metron"], providersAnswered: ["Metron"],
      results: [{
        provider: "metron", providerName: "Metron", providerSeriesId: "9",
        title: "Comic Title Goes in This Space and truncates", yearBegan: 2026,
        yearLabel: "2026", publisher: "Publisher", issueCount: 12,
        cover: null, status: "Ongoing", inLibrary: false,
        providerIds: { metron: "9" },
      }],
    },
  },
};

// Serve a stub on a page, by name.
export async function applyStubs(page, names = []) {
  for (const name of names) {
    const stub = STUBS[name];
    await page.route(stub.url, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(stub.body) }));
  }
}
