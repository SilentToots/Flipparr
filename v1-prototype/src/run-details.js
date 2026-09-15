// What the run drawer shows beside the story, after Plex's details page: who
// made the run, and the other runs in this library that share a maker or a
// publisher. Library-only on purpose -- suggestions from outside it would be a
// new capability, and those are parked while the UI is refined.

const ROLE_ORDER = ["writer", "artist", "penciller", "inker", "colorist", "letterer", "cover", "editor"];
const ROLE_LABELS = {
  writer: "Writer", artist: "Artist", penciller: "Pencils", inker: "Inks",
  colorist: "Colors", letterer: "Letters", cover: "Cover", editor: "Editor",
};
// Who a run is "by". Colorists and letterers work across far more runs than
// writers and artists do -- in one library Dave McCaig colours five runs that
// Scott Snyder writes six of -- so ranking on them would make every row about
// lettering.
const LEAD_ROLES = new Set(["writer", "artist", "penciller", "inker"]);

const fold = (value) => String(value || "")
  .normalize("NFKD").replace(/[̀-ͯ]/g, "")
  .toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

function roleRank(roles = []) {
  const ranks = roles.map((role) => {
    const index = ROLE_ORDER.indexOf(String(role).toLowerCase());
    return index < 0 ? ROLE_ORDER.length : index;
  });
  return ranks.length ? Math.min(...ranks) : ROLE_ORDER.length;
}

// One entry per person, however many credits the files and catalogs gave
// them, writers first.
export function orderedCreators(creators = []) {
  const byName = new Map();
  for (const creator of creators || []) {
    const key = fold(creator?.name);
    if (!key) continue;
    const current = byName.get(key);
    const roles = [...new Set([...(current?.roles || []), ...(creator.roles || []).map((role) => String(role).toLowerCase())])];
    byName.set(key, { name: current?.name || creator.name, roles });
  }
  return [...byName.values()].sort((a, b) => roleRank(a.roles) - roleRank(b.roles) || a.name.localeCompare(b.name));
}

export function creatorRoleLabel(roles = []) {
  const sorted = [...roles].sort((a, b) => roleRank([a]) - roleRank([b]));
  const labels = [...new Set(sorted.map((role) => ROLE_LABELS[role] || `${role.charAt(0).toUpperCase()}${role.slice(1)}`))];
  return labels.slice(0, 2).join(" · ");
}

// No catalog carries creator photos, so the circle holds initials.
export function creatorInitials(name) {
  const parts = String(name || "")
    .replace(/[^\p{L}\p{N}\s'-]/gu, " ")
    .split(/\s+/)
    .filter((part) => part && !/^(jr|sr|ii|iii|iv)$/i.test(part));
  if (!parts.length) return "?";
  return `${parts[0][0]}${parts.length > 1 ? parts[parts.length - 1][0] : ""}`.toUpperCase();
}

// "Image" and "Image Comics" are one publisher, and so are "BOOM! Studios"
// and "Boom Studios".
export function normalizedPublisher(name) {
  return fold(name)
    .replace(/\b(comics?|publishing|entertainment|studios?|inc|llc|press)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const leadsOf = (run) => orderedCreators(run?.creators).filter((creator) => creator.roles.some((role) => LEAD_ROLES.has(role)));
const byYearThenTitle = (a, b) => (Number(b.year) || 0) - (Number(a.year) || 0) || String(a.title).localeCompare(String(b.title));

export function relatedRuns(series, allSeries = [], limit = 12) {
  const others = (allSeries || []).filter((run) => run && run.id !== series?.id);
  // The lead with the most other runs here, not simply the first-billed one:
  // a row is only worth showing when it has something in it.
  let moreBy = null;
  for (const lead of leadsOf(series)) {
    const key = fold(lead.name);
    const runs = others.filter((run) => leadsOf(run).some((creator) => fold(creator.name) === key));
    if (runs.length > (moreBy?.runs.length || 0)) moreBy = { name: lead.name, runs };
  }
  if (moreBy) moreBy.runs = [...moreBy.runs].sort(byYearThenTitle).slice(0, limit);
  const shown = new Set((moreBy?.runs || []).map((run) => run.id));
  const key = normalizedPublisher(series?.publisher);
  const year = Number(series?.year) || 0;
  const publisherRuns = key
    ? others
      .filter((run) => !shown.has(run.id) && normalizedPublisher(run.publisher) === key)
      .sort((a, b) => Math.abs((Number(a.year) || 0) - year) - Math.abs((Number(b.year) || 0) - year) || String(a.title).localeCompare(String(b.title)))
      .slice(0, limit)
    : [];
  return { moreBy, publisher: publisherRuns.length ? { name: series.publisher, runs: publisherRuns } : null };
}

function rgbToHsl(r, g, b) {
  const [rn, gn, bn] = [r / 255, g / 255, b / 255];
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const h = max === rn ? (gn - bn) / d + (gn < bn ? 6 : 0) : max === gn ? (bn - rn) / d + 2 : (rn - gn) / d + 4;
  return [h / 6, s, l];
}

function hslToRgb(h, s, l) {
  if (!s) return [l, l, l].map((v) => Math.round(v * 255));
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const channel = (t) => {
    let x = t;
    if (x < 0) x += 1;
    if (x > 1) x -= 1;
    if (x < 1 / 6) return p + (q - p) * 6 * x;
    if (x < 1 / 2) return q;
    if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
    return p;
  };
  return [channel(h + 1 / 3), channel(h), channel(h - 1 / 3)].map((v) => Math.round(v * 255));
}

// The drawer's ground, taken from the cover the way Plex takes it from a
// poster: the average of the art's more colourful pixels, then pinned dark
// enough that white and the muted grey both clear AA on it whatever the art
// is. `data` is canvas ImageData.data. Returns "r g b" for an rgb() var.
export function coverTint(data) {
  let r = 0; let g = 0; let b = 0; let weight = 0;
  for (let i = 0; i + 3 < data.length; i += 4) {
    if (data[i + 3] < 128) continue;
    const [R, G, B] = [data[i], data[i + 1], data[i + 2]];
    const w = (Math.max(R, G, B) - Math.min(R, G, B)) / 255 + 0.05;
    r += R * w; g += G * w; b += B * w; weight += w;
  }
  if (!weight) return null;
  const [h, s] = rgbToHsl(r / weight, g / weight, b / weight);
  return hslToRgb(h, Math.min(s, 0.55), 0.14).join(" ");
}
