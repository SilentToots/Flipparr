import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowsClockwise,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Books,
  CaretDown,
  ChartLineUp,
  ChatCircle,
  CheckCircle,
  ClockCounterClockwise,
  CloudArrowDown,
  Database,
  DotsThree,
  Eye,
  FolderOpen,
  Funnel,
  Gear,
  HardDrive,
  ListBullets,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  ShieldCheck,
  SpinnerGap,
  SquaresFour,
  UploadSimple,
  WarningCircle,
  X,
} from "@phosphor-icons/react";

const NAV_ITEMS = [
  { id: "library", label: "Your library", icon: BookOpen },
  { id: "add", label: "Add comics", icon: Plus },
  { id: "requests", label: "Requests", icon: ChatCircle },
  { id: "metadata", label: "Library health", icon: Database, count: 3 },
  { id: "activity", label: "Activity", icon: ChartLineUp },
  { id: "settings", label: "Settings", icon: Gear },
];

const DEMO_SERIES = [
  {
    id: "absolute-batman", title: "Absolute Batman", year: "2024", publisher: "DC Comics",
    run: "2024 – Ongoing", tags: ["Superhero", "Detective"], owned: 3, total: 3,
    ownership: "Complete to date", format: "Single issues", updated: "Aug 26, 2026", time: "10:14 AM",
    cover: "/covers/absolute-batman.jpg", status: "complete",
  },
  {
    id: "birthright", title: "Birthright", year: "2014", publisher: "Image Comics",
    run: "2014 – 2017", tags: ["Fantasy", "Adventure"], owned: 2, total: 50,
    ownership: "48 issues missing", format: "Single issues", updated: "Aug 26, 2026", time: "10:02 AM",
    cover: "/covers/birthright.jpg", status: "partial",
  },
  {
    id: "locke-key", title: "Locke & Key", year: "2008", publisher: "IDW Publishing",
    run: "Head Games · Volume 2", tags: ["Horror", "Volume"], owned: 1, total: 1,
    ownership: "Volume owned", format: "EPUB volume", updated: "Aug 26, 2026", time: "9:58 AM",
    cover: "/covers/locke-key.jpg", status: "complete",
  },
  {
    id: "southern-bastards", title: "Southern Bastards", year: "2014", publisher: "Image Comics",
    run: "Gridiron · Volume 2", tags: ["Crime", "Volume"], owned: 1, total: 1,
    ownership: "Needs a metadata fix", format: "CBZ volume", updated: "Aug 26, 2026", time: "9:41 AM",
    cover: "/covers/southern-bastards.jpg", status: "warning",
  },
];

const DEMO_META_ITEMS = [
  { file: "strangetalentoflutherstrode_vol2.cbz", issue: "Empty archive", detail: "The archive is only 22 bytes and contains no comic pages.", severity: "error", category: "file", code: "empty_archive" },
  { file: "southernbastards_vol1.cbz", issue: "No image pages", detail: "Metadata exists, but the archive has no readable cover or pages.", severity: "error", category: "file", code: "no_image_pages" },
  { file: "southernbastards_vol2.cbz", issue: "Conflicting volume metadata", detail: "The filename and external catalog disagree with ComicInfo.xml.", severity: "warning", category: "metadata", code: "metadata_conflict" },
];

const EDITION_KIND_LABELS = {
  omnibus: "Omnibus",
  compendium: "Compendium",
  deluxe_edition: "Deluxe volume",
  graphic_novel: "Graphic novel",
  hardcover: "Hardcover",
  collected_volume: "Collected volume",
  collection: "Collected volume",
  edition: "Volume",
};

const ACQUISITION_LABELS = {
  volumes: "Volumes preferred",
  issues: "Single issues preferred",
  either: "Issues or volumes",
};

const JOB_STATUS_LABELS = {
  queued: "Queued",
  waiting: "Waiting",
  searching: "Searching",
  grabbed: "Download sent",
  failed: "Search failed",
  fulfilled: "Covered",
  cancelled: "Cancelled",
};

const DOWNLOAD_STATUS_LABELS = {
  queued: "Waiting for SABnzbd",
  downloading: "Downloading",
  completed: "Download complete",
  importing: "Adding to library",
  imported: "Added to library",
  failed: "Import needs attention",
};

const METADATA_PROVIDER_LABELS = {
  gcd: "Grand Comics Database",
  metron: "Metron",
  comic_vine: "Comic Vine",
};

function editionKindLabel(kind) {
  return EDITION_KIND_LABELS[kind] || "Volume";
}

function publicationState(series) {
  const state = series?.publicationStatus
    || (series?.issueCatalog?.status === "complete" ? "completed"
      : series?.issueCatalog?.status === "complete_to_date" ? "ongoing" : "unknown");
  return state === "completed"
    ? { label: "Completed", tone: "completed" }
    : state === "ongoing"
      ? { label: "Ongoing", tone: "ongoing" }
      : { label: "Status unknown", tone: "unknown" };
}

function PublicationStatus({ series }) {
  const state = publicationState(series);
  return <span className={`publication-status ${state.tone}`}>{state.label}</span>;
}

function seriesAttentionLabel(series) {
  const unhealthyFiles = (series?.fileDetails || []).filter((file) => file.health?.status === "error").length;
  if (unhealthyFiles) return `${unhealthyFiles} comic file${unhealthyFiles === 1 ? "" : "s"} ${unhealthyFiles === 1 ? "needs" : "need"} attention`;
  return "Match needs attention";
}

function seriesAttentionDetail(series) {
  const hasFileError = (series?.fileDetails || []).some((file) => file.health?.status === "error");
  return hasFileError ? "Review the comic files for page or archive errors." : "Review the series match before using its ownership totals.";
}

function volumeTerminology(value) {
  if (!value) return value;
  return String(value)
    .replace(/collected[- ]editions/gi, "volumes")
    .replace(/collected[- ]edition/gi, "volume")
    .replace(/collection (coverage|contents|identity|source)/gi, "volume $1")
    .replace(/file edition/gi, "file volume");
}

function parseIssueInput(value) {
  const issues = [];
  for (const token of value.split(",").map((item) => item.trim()).filter(Boolean)) {
    const range = token.match(/^(\d+)\s*[-–]\s*(\d+)$/);
    if (!range) { issues.push(token); continue; }
    const start = Number(range[1]);
    const end = Number(range[2]);
    if (end < start || end - start > 499) throw new Error(`Invalid issue range: ${token}`);
    for (let issue = start; issue <= end; issue += 1) issues.push(String(issue));
  }
  return [...new Set(issues)];
}

function identityKey(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "");
}

function familyRepresentsSeries(family) {
  const runs = family?.runs || [];
  const arcs = family?.storyArcs || [];
  if (family?.structureStatus !== "confirmed" || !runs.length || !arcs.length) return false;
  const groupedRunIds = new Set(arcs.flatMap((arc) => arc.runIds || []).map(String));
  if (!runs.every((run) => groupedRunIds.has(String(run.id)))) return false;
  const root = identityKey(family.name);
  if (!root) return false;
  const alignedRuns = runs.filter((run) => identityKey(run.title).includes(root));
  return alignedRuns.length / runs.length >= 0.75;
}

function collectionSeriesEntry(family) {
  const runs = family.runs || [];
  const runLifecycle = runs.map((run) => publicationState(run).tone);
  const directIssueFiles = runs.reduce((count, run) => count + (run.inventory?.directIssueFiles || 0), 0);
  const editionCount = runs.reduce((count, run) => count + (run.inventory?.editionCount || 0), 0);
  const fileDetails = runs.flatMap((run) => run.fileDetails || []);
  const issues = runs.flatMap((run) => run.issues || []);
  const ownedIssueCount = issues.filter((issue) => issue.ownership !== "unowned").length;
  const missingIssueCount = Math.max(0, issues.length - ownedIssueCount);
  const latestRun = [...runs].sort((a, b) => `${b.updated || ""} ${b.time || ""}`.localeCompare(`${a.updated || ""} ${a.time || ""}`))[0] || {};
  const storyArcCount = family.mainArcCount || 0;
  const specialCount = family.specialGroupCount || 0;
  return {
    ...family,
    id: `collection-series-${family.id}`,
    title: family.name,
    run: `${storyArcCount} run${storyArcCount === 1 ? "" : "s"}${specialCount ? ` · ${specialCount} special${specialCount === 1 ? "" : "s"}` : ""}`,
    tags: ["Series"],
    format: "Series",
    updated: latestRun.updated || "",
    time: latestRun.time || "",
    inventory: { directIssueFiles, editionCount },
    fileDetails,
    issues,
    owned: ownedIssueCount,
    total: issues.length,
    unowned: missingIssueCount,
    catalogKnown: issues.length > 0,
    publicationStatus: runLifecycle.includes("ongoing")
      ? "ongoing"
      : runLifecycle.length && runLifecycle.every((state) => state === "completed")
        ? "completed" : "unknown",
    status: issues.length ? (missingIssueCount ? "partial" : "complete") : family.status,
    ownership: issues.length ? (missingIssueCount ? `${missingIssueCount} issues missing` : "All issues owned") : family.ownership,
    files: fileDetails.map((file) => file.path),
    isCollectionSeries: true,
    collection: family,
    searchText: `${family.name} ${family.publisher} ${runs.map((run) => run.title).join(" ")} ${(family.storyArcs || []).map((arc) => arc.name).join(" ")}`,
  };
}

function logicalCatalogSeries(catalog, fallback = []) {
  const runs = catalog?.series ?? fallback;
  const logicalFamilies = (catalog?.families || []).filter(familyRepresentsSeries);
  const hiddenRunIds = new Set(logicalFamilies.flatMap((family) => family.runIds || []).map(String));
  return [
    ...runs.filter((run) => !hiddenRunIds.has(String(run.id))),
    ...logicalFamilies.map(collectionSeriesEntry),
  ].sort((a, b) => a.title.localeCompare(b.title));
}

async function apiRequest(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function Nav({ active, onNavigate, catalog, backendStatus, logicalSeriesCount }) {
  const counts = { requests: catalog?.stats?.openRequests ?? 0, metadata: catalog?.stats?.needAttention ?? 0 };
  return (
    <aside className="sidebar">
      <div className="brand"><Books size={24} weight="duotone" /><div><strong>Comic Library</strong><span>V1 prototype</span></div></div>
      <nav aria-label="Primary navigation">
        {NAV_ITEMS.map(({ id, label, icon: Icon, count }) => (
          <button className={`nav-item ${active === id ? "active" : ""}`} key={id} onClick={() => onNavigate(id)} aria-label={label} aria-current={active === id ? "page" : undefined}>
            <Icon size={21} weight={active === id ? "fill" : "regular"} /><span>{label}</span>{(counts[id] ?? count) ? <b>{counts[id] ?? count}</b> : null}
          </button>
        ))}
      </nav>
      <div className="sidebar-footer">
        <div className={`health-line ${backendStatus === "offline" ? "offline" : ""}`}><i /> {backendStatus === "offline" ? "Library unavailable" : "Library ready"}</div><span>Last scan: {catalog?.lastScan?.iso ? `${catalog.lastScan.date} ${catalog.lastScan.time}` : "Not run yet"}</span>
        <div className="storage-title">Library</div><strong>{catalog?.stats?.files ?? 0} comics across {logicalSeriesCount} series</strong><div className="storage-track"><i style={{ width: catalog?.stats?.files ? "100%" : "0%" }} /></div>
      </div>
    </aside>
  );
}

function SearchBar({ value, onChange, onSubmit, actionLabel = "Search online", busy = false, placeholder = "Search series, issue, or creator…", label = "Search" }) {
  return <div className="search-field"><MagnifyingGlass size={20} /><input aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} onKeyDown={(event) => {
    if (event.key === "Enter" && onSubmit) {
      event.preventDefault();
      onSubmit();
    }
  }} placeholder={placeholder} />{onSubmit ? <button type="button" disabled={busy || value.trim().length < 2} onClick={onSubmit}>{busy ? <SpinnerGap className="spin" size={15} /> : <MagnifyingGlass size={15} />}{busy ? "Searching…" : actionLabel}</button> : null}</div>;
}

function PageHeader({ eyebrow, title, description, children }) {
  return <header className="page-header"><div>{eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}<h1>{title}</h1><p>{description}</p></div>{children ? <div className="page-actions">{children}</div> : null}</header>;
}

function StatStrip({ stats }) {
  const cards = [
    { icon: Books, value: stats?.series ?? 0, label: "Series", tone: "green" },
    { icon: BookOpen, value: stats?.files ?? 0, label: "Comic files", tone: "green" },
    { icon: ClockCounterClockwise, value: stats?.unownedIssues ?? 0, label: "Issues missing", tone: "muted" },
    { icon: WarningCircle, value: stats?.damaged ?? 0, label: "Comic file problems", tone: "danger" },
  ];
  return <section className="stat-strip" aria-label="Library summary">{cards.map(({ icon: Icon, value, label, tone }) => <div className={`stat ${tone}`} key={label}><Icon size={27} weight="duotone" /><div><strong>{value}</strong><span>{label}</span></div></div>)}</section>;
}

function MetadataSetupStatus({ enrichment }) {
  if (!enrichment?.total) return null;
  const processed = enrichment.complete + enrichment.review + enrichment.failed;
  const percent = Math.round((processed / enrichment.total) * 100);
  const active = enrichment.active > 0;
  const current = enrichment.nextJobs?.find((job) => job.status === "running");
  const next = enrichment.nextJobs?.find((job) => ["queued", "waiting"].includes(job.status));
  const cooldown = enrichment.providerCooldowns?.[0];
  const paused = active && !current && enrichment.waiting > 0 && Boolean(cooldown);
  const providerName = METADATA_PROVIDER_LABELS[cooldown?.provider] || cooldown?.provider || "The metadata service";
  const retryTime = cooldown?.nextRetryAt
    ? new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(cooldown.nextRetryAt))
    : null;
  const workingLabel = current ? `Checking ${current.title}` : enrichment.waiting && !enrichment.queued ? "Waiting for a metadata service; retrying automatically" : next ? `${next.title} is next` : "Preparing the next series";
  const heading = paused ? "Metadata lookup is paused temporarily" : active ? "Building your comic details in the background" : enrichment.review ? "Initial metadata check finished" : "Comic details are ready";
  const detail = paused
    ? `${processed} of ${enrichment.total} series checked · ${providerName} is limiting requests${retryTime ? ` until ${retryTime}` : ""}`
    : active
    ? `${processed} of ${enrichment.total} series checked · ${workingLabel}`
    : enrichment.review
      ? `${enrichment.complete} series identified automatically · ${enrichment.review} need a match review`
      : `${enrichment.complete} series identified automatically`;
  return <section className={`metadata-setup-status ${paused ? "paused" : active ? "active" : enrichment.review ? "review" : "complete"}`} aria-live="polite"><span className="metadata-setup-icon">{paused ? <ClockCounterClockwise size={21} weight="fill" /> : active ? <SpinnerGap className="spin" size={21} /> : enrichment.review ? <MagnifyingGlass size={21} /> : <CheckCircle size={21} weight="fill" />}</span><div className="metadata-setup-copy"><strong>{heading}</strong><small>{detail}. Your files and covers are already available.</small>{paused ? <small className="metadata-provider-help">SonicBoom will retry automatically. Adding Metron or Comic Vine under Settings → Metadata services can let intake continue with another catalog.</small> : null}<details><summary>View progress details</summary><div className="metadata-progress-details"><span><b>{enrichment.complete}</b> Matched</span><span><b>{enrichment.queued + enrichment.running}</b> Waiting</span><span><b>{enrichment.waiting}</b> Retrying later</span><span><b>{enrichment.review}</b> Need review</span>{enrichment.failed ? <span><b>{enrichment.failed}</b> Could not finish</span> : null}</div>{paused ? <p className="metadata-pause-detail">{cooldown.error || `${providerName} asked SonicBoom to wait before making more requests.`}</p> : null}</details></div><div className="metadata-setup-meter"><b>{percent}%</b><div className="metadata-setup-progress" aria-label={`${percent}% of initial metadata jobs processed`}><i style={{ width: `${percent}%` }} /></div></div></section>;
}

function Ownership({ series }) {
  const percent = Math.max(5, Math.round((series.owned / series.total) * 100));
  const catalogUnknown = series.catalogKnown === false || series.status === "unknown";
  const volumeCount = series.inventory?.editionCount || 0;
  const collectedOnly = volumeCount > 0 && !series.inventory?.directIssueFiles;
  const coveredIssues = series.issues?.filter((issue) => issue.ownership !== "unowned").length || 0;
  const arcCoverageKnown = collectedOnly && coveredIssues > 0;
  const ownedLabel = arcCoverageKnown ? `${coveredIssues} issue${coveredIssues === 1 ? "" : "s"} owned` : collectedOnly ? `${volumeCount} volume${volumeCount === 1 ? "" : "s"} owned` : `${series.owned} owned`;
  const label = series.status === "warning" ? seriesAttentionLabel(series) : catalogUnknown ? ownedLabel : `${series.owned} of ${series.total}`;
  const coverageDetail = arcCoverageKnown ? `${coveredIssues} issue${coveredIssues === 1 ? "" : "s"} collected in ${volumeCount} volume${volumeCount === 1 ? "" : "s"}${series.family ? " · complete-series progress is tracked separately" : ""}` : "";
  const detail = series.status === "warning" ? [coverageDetail, seriesAttentionDetail(series)].filter(Boolean).join(" · ") : series.isCollectionSeries ? series.ownership : coverageDetail || series.ownership;
  return <div className={`ownership ${series.status}`}><div className="ownership-label">{series.status === "warning" ? <WarningCircle size={19} weight="fill" /> : catalogUnknown ? <ClockCounterClockwise size={19} weight="fill" /> : <CheckCircle size={19} weight="fill" />}<strong>{label}</strong></div>{series.status !== "warning" && !catalogUnknown ? <div className="progress"><i style={{ width: `${percent}%` }} /></div> : null}{detail && detail !== label ? <span>{detail}</span> : null}</div>;
}

function SeriesCover({ series, decorative = false }) {
  const candidates = series.coverCandidates?.length ? series.coverCandidates : (series.cover ? [series.cover] : []);
  const [candidateIndex, setCandidateIndex] = useState(0);
  useEffect(() => setCandidateIndex(0), [series.id]);
  if (candidates[candidateIndex]) return <img src={candidates[candidateIndex]} alt={decorative ? "" : `${series.title} cover`} onError={() => setCandidateIndex((index) => index + 1)} />;
  return <span className="cover-placeholder" role="img" aria-label={decorative ? undefined : `No cover available for ${series.title}`}><BookOpen size={28} weight="duotone" /></span>;
}

function SeriesList({ series, onOpen, view }) {
  if (view === "grid") return <div className="series-grid">{series.map((item) => <button className="series-card" onClick={() => onOpen(item)} key={item.id}><SeriesCover series={item} /><div><strong>{item.title}</strong><span>{item.year} · {item.publisher}</span><PublicationStatus series={item} /><Ownership series={item} /></div></button>)}</div>;
  return (
    <div className="series-table">
      <div className="series-table-head"><span>Series</span><span>Ownership</span><span>Format</span><span>Last updated</span><span /></div>
      {series.map((item) => (
        <button className="series-row" onClick={() => onOpen(item)} key={item.id}>
          <span className="series-identity"><SeriesCover series={item} decorative /><span><strong>{item.title} <em>({item.year})</em></strong><small>{item.isCollectionSeries ? `${item.publisher} · ${item.run}` : item.publisher}</small><span className="tag-line"><PublicationStatus series={item} /><small>{item.format}</small></span></span></span>
          <Ownership series={item} /><span className="table-copy">{item.format}</span><span className="table-copy">{item.updated}<small>{item.time}</small></span><DotsThree size={22} />
        </button>
      ))}
    </div>
  );
}

function CollectionGroups({ families, onOpenCollection }) {
  return <div className="family-groups">
    {families.map((family) => {
      const display = { ...family, id: `family-${family.id}`, title: family.name };
      return <section className="family-group" key={family.id}>
        <button className="family-summary" onClick={() => onOpenCollection(family)}>
          <span className="family-cover"><SeriesCover series={display} decorative /></span>
          <span className="family-copy"><b>Collection</b><strong>{family.name}</strong><small>{family.year} · {family.publisher} · {family.runCount} publication run{family.runCount === 1 ? "" : "s"} · {family.mainArcCount || 0} story arc{family.mainArcCount === 1 ? "" : "s"}</small></span>
          <Ownership series={family} />
          <ArrowRight size={20} />
        </button>
      </section>;
    })}
  </div>;
}

function CollectionEmpty({ query }) {
  return <div className="empty-state"><Books size={35} weight="duotone" /><strong>{query ? `No collections match “${query}”` : "No collections created yet"}</strong><span>{query ? "Try another collection or run title." : "Open a run and use its Collection tab to group related publication runs."}</span></div>;
}

function AttentionPanel({ onReview, items }) {
  if (!items.length) return null;
  return <section className="attention-panel"><header><WarningCircle size={24} weight="fill" /><strong>Needs attention ({items.length})</strong><button onClick={onReview}>Review problems <ArrowRight size={16} /></button></header><div className="attention-details">{items.slice(0, 2).map((item) => <button onClick={onReview} key={item.id}><span>{item.issue}</span><small>{item.file}: {item.detail}</small><b>{item.category === "file" ? "Review comic file" : "Review metadata"}</b></button>)}</div></section>;
}

function discoveryRunStatus(item) {
  const status = String(item?.status || "").toLowerCase();
  if (item?.yearEnded || ["completed", "complete", "cancelled"].includes(status)) return "Completed run";
  if (["ongoing", "continuing"].includes(status)) return "Ongoing run";
  return "Status unknown";
}

function discoveryByline(item) {
  const rolePriority = { writer: 0, artist: 1, penciller: 1, inker: 2, colorist: 3, letterer: 4 };
  const creators = (item?.creators || []).filter((creator) => creator?.name && creator?.roles?.length).sort((a, b) => {
    const priority = (creator) => Math.min(...creator.roles.map((role) => rolePriority[role.toLowerCase()] ?? 9));
    return priority(a) - priority(b);
  });
  if (!creators.length) return "";
  const visible = creators.slice(0, 3).map((creator) => (
    `${creator.name} (${creator.roles.map((role) => role.toLowerCase()).join(", ")})`
  ));
  if (creators.length > visible.length) visible.push(`+${creators.length - visible.length} more`);
  return `By ${visible.join(" · ")}`;
}

function searchQueryParts(value) {
  const cleaned = String(value || "").trim().replace(/\s+/g, " ");
  const match = cleaned.match(/^(.+?)(?:\s+|\s*\()((?:19|20)\d{2})\)?$/);
  return match ? { title: match[1].trim(), year: match[2] } : { title: cleaned, year: "" };
}

function DiscoveryCover({ item }) {
  const [failed, setFailed] = useState(false);
  return <span className="discovery-cover">{item.cover && !failed ? <img src={item.cover} alt={`${item.title} cover`} onError={() => setFailed(true)} /> : <BookOpen size={22} weight="duotone" />}</span>;
}

function DiscoveryCard({ item, provider, busyId, onAdd }) {
  const byline = discoveryByline(item);
  const itemId = `${item.provider}-${item.providerSeriesId}`;
  return <article className="discovery-result">
    <DiscoveryCover item={item} />
    <div className="discovery-copy">
      <div className="discovery-title-row"><strong>{item.title}</strong><b>{item.yearLabel}</b></div>
      {byline ? <span className="discovery-byline" title={`${item.creatorCreditsSource || "Provider"} credits`}>{byline}</span> : null}
      <span>{item.publisher || "Publisher unknown"} · {item.issueCount} known issue{item.issueCount === 1 ? "" : "s"}</span>
      <small>{discoveryRunStatus(item)} · {item.providerName || provider}</small>
    </div>
    <button disabled={busyId === itemId} onClick={() => onAdd(item)}>{busyId === itemId ? <SpinnerGap className="spin" size={16} /> : <Plus size={16} />} {busyId === itemId ? "Adding…" : "Add & request"}</button>
  </article>;
}

function DiscoveryResults({ query, results, state, error, provider, yearHint, busyId, onAdd, onRetry, page = false }) {
  const available = results.filter((item) => !item.inLibrary);
  const present = results.filter((item) => item.inLibrary);
  const providerSummary = provider ? `${available.length || results.length} publication runs from ${provider}${yearHint ? ` · closest to ${yearHint} first` : ""}` : "Searching comic databases";
  const cards = available.map((item) => <DiscoveryCard item={item} provider={provider} busyId={busyId} onAdd={onAdd} key={`${item.provider}-${item.providerSeriesId}`} />);
  return <section className={`discovery-results${page ? " discovery-results-page" : ""}`} aria-label="Discover series results">
    <header><div><strong>Discover new series</strong><span>{providerSummary}</span></div>{state === "loading" ? <SpinnerGap className="spin" size={18} /> : null}</header>
    {error ? <div className="discovery-message warning"><WarningCircle size={18} /><span><strong>Online search needs another try</strong><small>The comics provider is busy. Your library results are still available above.</small></span><button type="button" onClick={onRetry}>Try again</button></div> : null}
    {!error && state === "done" && !available.length ? <div className="discovery-message"><MagnifyingGlass size={18} /><span><strong>{present.length ? "All matching runs are already in your library" : `No new series found for “${query}”`}</strong><small>Try the exact publication title or add a four-digit publication year.</small></span></div> : null}
    {page ? <div className="discovery-grid">{cards}</div> : cards}
  </section>;
}

function LibraryLoadingSkeleton() {
  return <div className="library-loading" role="status" aria-live="polite" aria-busy="true">
    <section className="library-loading-message">
      <span className="library-loading-icon"><SpinnerGap className="spin" size={21} /></span>
      <span><strong>Loading your library…</strong><small>Bringing in your comic runs, covers, and collection status.</small></span>
    </section>
    <section className="library-loading-stats" aria-hidden="true">
      {[0, 1, 2, 3].map((item) => <span key={item}><i /><b /></span>)}
    </section>
    <div className="library-loading-tools" aria-hidden="true"><i /><i /><i /></div>
    <section className="library-loading-table" aria-hidden="true">
      <header><i /><i /><i /><i /></header>
      {[0, 1, 2, 3, 4].map((item) => <article key={item}><span className="library-loading-cover" /><span className="library-loading-copy"><i /><i /><i /></span><span className="library-loading-progress"><i /><i /></span><span className="library-loading-cell" /><span className="library-loading-cell short" /></article>)}
    </section>
  </div>;
}

function LibraryView({ onNavigate, onOpenSeries, onOpenCollection, onSearch, catalog, backendStatus }) {
  const [query, setQuery] = useState("");
  const [view, setView] = useState("list");
  const [scope, setScope] = useState("runs");
  const fallbackSeries = backendStatus === "offline" ? DEMO_SERIES : [];
  const series = useMemo(() => logicalCatalogSeries(catalog, fallbackSeries), [catalog, backendStatus]);
  const families = useMemo(() => (catalog?.families || []).map((family) => ({ ...family, runCount: family.runs?.length || family.runCount || 0 })), [catalog?.families]);
  const initialLoading = backendStatus === "loading" && !catalog;
  return (
    <>
      <div className="topbar"><div className="library-search"><SearchBar value={query} onChange={setQuery} onSubmit={() => onSearch(query)} actionLabel="Search" label="Search library and discover series" placeholder="Search your library or add a series…" /></div></div>
      <PageHeader title="Your library" description="See what you own, what’s missing, and what needs your attention." />
      {initialLoading ? <LibraryLoadingSkeleton /> : null}
      {!initialLoading ? <>
      {backendStatus === "offline" ? <div className="backend-banner"><WarningCircle size={19} weight="fill" /> Showing sample comics because your library is unavailable.</div> : null}
      <MetadataSetupStatus enrichment={catalog?.enrichment} />
      <StatStrip stats={{ ...(catalog?.stats ?? { files: series.reduce((count, item) => count + item.owned, 0), needAttention: 0, damaged: 0 }), series: series.length }} />
      <div className="library-tools"><div className="scope-toggle" aria-label="Choose catalog grouping"><button className={scope === "runs" ? "active" : ""} onClick={() => setScope("runs")}><ListBullets size={17} /> Runs</button><button className={scope === "collections" ? "active" : ""} onClick={() => setScope("collections")}><Books size={17} /> Collections</button></div>{scope === "runs" ? <div className="view-toggle" aria-label="Choose library view"><button className={view === "grid" ? "active" : ""} onClick={() => setView("grid")} aria-label="Grid view"><SquaresFour size={18} /></button><button className={view === "list" ? "active" : ""} onClick={() => setView("list")} aria-label="List view"><ListBullets size={18} /></button></div> : null}<label className="sort-field"><span>Sort by</span><select><option>Title (A–Z)</option><option>Recently added</option><option>Needs attention</option></select></label><button className="filter-button"><Funnel size={18} /> Filter series</button></div>
      {scope === "collections" ? (families.length ? <CollectionGroups families={families} onOpenCollection={onOpenCollection} /> : <CollectionEmpty query="" />) : series.length ? <SeriesList series={series} onOpen={(item) => item.isCollectionSeries ? onOpenCollection(item.collection) : onOpenSeries(item)} view={view} /> : <CatalogEmpty onAdd={() => onNavigate("add")} />}
      <AttentionPanel items={catalog?.inbox ?? []} onReview={() => onNavigate("metadata")} />
      </> : null}
    </>
  );
}

function SearchResultsView({ query, catalog, backendStatus, onSearch, onOpenSeries, onOpenCollection, onDiscoverRequest }) {
  const [draft, setDraft] = useState(query);
  const [discovery, setDiscovery] = useState({ state: "loading", results: [], error: "", provider: "", yearHint: null });
  const [discoverBusyId, setDiscoverBusyId] = useState(null);
  const allSeries = useMemo(() => logicalCatalogSeries(catalog, backendStatus === "offline" ? DEMO_SERIES : []), [catalog, backendStatus]);
  const localResults = useMemo(() => {
    const parts = searchQueryParts(query);
    const needle = parts.title.toLowerCase();
    return allSeries.filter((item) => {
      const textMatches = `${item.searchText || item.title} ${item.publisher || ""}`.toLowerCase().includes(needle);
      return textMatches && (!parts.year || `${item.year || ""} ${item.run || ""}`.includes(parts.year));
    });
  }, [allSeries, query]);

  async function searchProviders(searchQuery = query) {
    const cleaned = searchQuery.trim();
    if (cleaned.length < 2 || backendStatus === "offline") {
      setDiscovery({ state: "done", results: [], error: backendStatus === "offline" ? "Library service is unavailable" : "", provider: "", yearHint: null });
      return;
    }
    setDiscovery({ state: "loading", results: [], error: "", provider: "", yearHint: null });
    try {
      const result = await apiRequest(`/api/v1/discover?query=${encodeURIComponent(cleaned)}`);
      setDiscovery({ state: "done", results: result.results || [], error: "", provider: result.provider || "", yearHint: result.yearHint || null });
    } catch (error) {
      setDiscovery({ state: "done", results: [], error: error.message, provider: "", yearHint: null });
    }
  }
  useEffect(() => { setDraft(query); searchProviders(query); }, [query, backendStatus]);
  async function addDiscovered(item) {
    setDiscoverBusyId(`${item.provider}-${item.providerSeriesId}`);
    await onDiscoverRequest({ ...item, query });
    setDiscoverBusyId(null);
  }
  return <>
    <div className="search-results-topbar"><SearchBar value={draft} onChange={setDraft} onSubmit={() => onSearch(draft)} actionLabel="Search" label="Search comics" placeholder="Try a title and year, such as Wolverine 2026…" /></div>
    <PageHeader eyebrow="Search" title={`Results for “${query}”`} description="Comics already in your library appear first, followed by new series you can add." />
    <section className="search-results-section owned-results"><header><div><span className="eyebrow">In your library</span><h2>{localResults.length ? `${localResults.length} match${localResults.length === 1 ? "" : "es"}` : "No matches"}</h2></div></header>{localResults.length ? <SeriesList series={localResults} onOpen={(item) => item.isCollectionSeries ? onOpenCollection(item.collection) : onOpenSeries(item)} view="list" /> : <div className="search-section-empty"><BookOpen size={25} /><span>No comics in your library match this search.</span></div>}</section>
    <DiscoveryResults page query={query} results={discovery.results} state={discovery.state} error={discovery.error} provider={discovery.provider} yearHint={discovery.yearHint} busyId={discoverBusyId} onAdd={addDiscovered} onRetry={() => searchProviders(query)} />
  </>;
}

function EmptySearch({ query }) {
  return <div className="empty-state"><MagnifyingGlass size={32} /><strong>No series match “{query}”</strong><span>Try a title, creator, or publisher.</span></div>;
}

function CatalogEmpty({ onAdd }) {
  return <div className="empty-state"><Books size={35} weight="duotone" /><strong>Your library is ready for its first comics</strong><span>Choose a folder to scan without changing your files.</span><button className="primary-button" onClick={onAdd}><FolderOpen size={18} /> Add comics</button></div>;
}

function AddComicsView({ onNavigate, onStartInventory, onScanLibrary, catalog, scanState, scanProgress }) {
  const [path, setPath] = useState("/Users/dev/Downloads/Comics");
  const [recursive, setRecursive] = useState(true);
  const [pickerState, setPickerState] = useState("");
  const busy = scanState === "scanning";
  const roots = catalog?.roots || [];
  const lastScan = catalog?.lastScan?.iso ? `${catalog.lastScan.date} ${catalog.lastScan.time}` : "Not scanned yet";
  const scanTotal = Number(scanProgress?.total_files || 0);
  const scanProcessed = Number(scanProgress?.processed_files || 0);
  const scanPercent = scanTotal ? Math.min(100, Math.round((scanProcessed / scanTotal) * 100)) : 0;
  async function chooseFolder() {
    setPickerState("Opening Finder…");
    try {
      const result = await apiRequest("/api/pick-folder");
      if (result.folder) setPath(result.folder);
      setPickerState(result.folder ? "Folder selected" : "Selection canceled");
    } catch (error) {
      setPickerState(error.message);
    }
  }
  return <><PageHeader eyebrow="Library setup" title="Add comics" description="Add another comics folder or scan your existing library for changes." />
    {scanState === "scanning" ? <div className={`scan-progress ${scanTotal ? "determinate" : ""}`} aria-live="polite"><span><strong>{scanTotal ? `Scanning comic ${Math.min(scanProcessed + 1, scanTotal)} of ${scanTotal}` : "Finding comic files…"}</strong><small>{scanTotal ? `${scanProcessed} complete · reading embedded details, cover art, and file health` : "Counting files before the library scan begins"}</small></span><b>{scanTotal ? `${scanPercent}%` : "Starting"}</b><i style={scanTotal ? { width: `${scanPercent}%` } : undefined} /></div> : null}
    {scanState === "done" ? <div className="success-banner"><CheckCircle size={19} weight="fill" /> Scan complete. Your library is up to date.</div> : null}
    {roots.length ? <section className="library-scan-panel"><div><span className="eyebrow">Existing library</span><h2>Update your library</h2><p>Check {roots.length} comic folder{roots.length === 1 ? "" : "s"} for new, changed, or damaged files.</p><small>{roots.map((root) => root.path).join(" · ")}</small></div><div className="library-scan-action"><span>Last scanned</span><strong>{lastScan}</strong><button className={`primary-button ${busy ? "loading" : ""}`} onClick={onScanLibrary} disabled={busy}>{busy ? <SpinnerGap size={19} /> : <ArrowsClockwise size={19} />}{busy ? "Scanning…" : "Scan existing folders"}</button></div></section> : null}
    <section className="focused-panel add-panel"><div className="panel-icon"><FolderOpen size={30} weight="duotone" /></div><h2>Add another comics folder</h2><p>We’ll quickly inventory issues and volumes, use covers and metadata already in the files, and check for damaged archives. Online details can be refreshed after the library is visible.</p><label className="form-field"><span>Comics folder</span><div className="path-input"><input value={path} onChange={(event) => setPath(event.target.value)} /><button type="button" onClick={chooseFolder}>Choose folder</button></div>{pickerState ? <small>{pickerState}</small> : null}</label><label className="check-row"><input type="checkbox" checked={recursive} onChange={(event) => setRecursive(event.target.checked)} /><span><strong>Include subfolders</strong><small>Useful when each series has its own folder</small></span></label><div className="safety-note"><ShieldCheck size={22} weight="fill" /><span><strong>Your files stay untouched</strong><small>No files will be renamed, moved, or modified during this scan.</small></span></div><div className="panel-actions"><button className={`primary-button ${busy ? "loading" : ""}`} disabled={busy || !path.trim()} onClick={() => onStartInventory(path, recursive)}>{busy ? <SpinnerGap size={19} /> : <UploadSimple size={19} />} {busy ? "Scanning…" : "Scan folder"}</button><button className="ghost-button" onClick={() => onNavigate("library")}>Cancel</button></div></section></>;
}

function RequestsView({ catalog, onCreateRequest, onCancelReplacement, onRefresh }) {
  const [requestOpen, setRequestOpen] = useState(false);
  const [releaseJob, setReleaseJob] = useState(null);
  const [tab, setTab] = useState("open");
  const requests = catalog?.requests || [];
  const replacements = catalog?.replacementRequests || [];
  const seriesEntries = requests.filter((request) => tab === "fulfilled" ? request.status === "fulfilled" : request.status === "open");
  const replacementEntries = replacements.filter((request) => tab === "fulfilled" ? request.status === "fulfilled" : !["fulfilled", "cancelled"].includes(request.status));
  const openCount = requests.filter((request) => request.status === "open").length + replacements.filter((request) => !["fulfilled", "cancelled"].includes(request.status)).length;
  const fulfilledCount = requests.filter((request) => request.status === "fulfilled").length + replacements.filter((request) => request.status === "fulfilled").length;
  const hasEntries = seriesEntries.length || replacementEntries.length;
  return <><PageHeader title="Requests" description="Follow missing comics and replace files that are damaged, incorrect, or poor quality."><button className="primary-button" onClick={() => setRequestOpen(true)}><Plus size={19} /> Add series</button></PageHeader><div className="request-tabs"><button className={tab === "open" ? "active" : ""} onClick={() => setTab("open")}>Wanted <b>{openCount}</b></button><button className={tab === "fulfilled" ? "active" : ""} onClick={() => setTab("fulfilled")}>Complete <b>{fulfilledCount}</b></button></div><section className="request-list">{hasEntries ? <>{replacementEntries.map((request) => <ReplacementRequestRow request={request} onCancel={onCancelReplacement} key={`replacement-${request.id}`} />)}{seriesEntries.map((request) => <RequestRow request={request} onFindRelease={setReleaseJob} key={`series-${request.id}`} />)}</> : <div className="empty-state request-empty"><CheckCircle size={34} weight="duotone" /><strong>{tab === "open" ? "Nothing on your wanted list" : "No completed requests yet"}</strong><span>{tab === "open" ? "Add a series or request a replacement from Library health." : "Completed series and file replacements appear here."}</span></div>}</section>{requestOpen ? <RequestModal catalog={catalog} onCreate={async (target) => { const result = await onCreateRequest(target); if (result?.ok) setRequestOpen(false); return result; }} onClose={() => setRequestOpen(false)} /> : null}{releaseJob ? <ReleaseSearchModal job={releaseJob} onClose={() => setReleaseJob(null)} onGrabbed={async () => { await onRefresh?.(); setReleaseJob(null); }} /> : null}</>;
}

function ReplacementRequestRow({ request, onCancel }) {
  const display = { id: `replacement-${request.id}`, title: request.seriesTitle || request.targetTitle, cover: request.cover };
const statusLabels = request.targetType === "issue" ? { wanted: "Fix issue", searching: "Finding issue", grabbed: "Issue found", failed: "Needs attention", fulfilled: "Issue fixed" } : { wanted: "Fix run", searching: "Finding comics", grabbed: "Run fix found", failed: "Needs attention", fulfilled: "Run fixed" };
  const tone = request.status === "fulfilled" ? "green" : request.status === "failed" ? "red" : "violet";
  const preference = ACQUISITION_LABELS[request.acquisitionPreference] || ACQUISITION_LABELS.either;
  const title = request.seriesTitle || request.targetTitle;
  const scope = [preference, request.coverageTarget, request.reason, request.desiredLanguage ? `Wanted language: ${request.desiredLanguage}` : null].filter(Boolean).join(" · ");
  return <article className="request-card replacement-request-card"><div className="request-row"><span className="request-cover"><SeriesCover series={display} decorative /></span><div><h3>{title}</h3><p>{scope}</p><span>{request.targetTitle !== title ? `${request.targetTitle} · ` : ""}{request.filename} · Added {request.requestedDate} {request.requestedTime}</span></div><b className={`status-chip ${tone}`}>{statusLabels[request.status] || request.status}</b>{!["fulfilled", "cancelled"].includes(request.status) ? <button className="request-expand" onClick={() => onCancel(request)}><span>Cancel request</span><X size={15} /></button> : <span />}</div><footer className="replacement-safety-note"><ShieldCheck size={16} weight="fill" /> The current comic stays in your library until replacement issues or volumes complete this run.</footer></article>;
}

function RequestRow({ request, onFindRelease }) {
  const [expanded, setExpanded] = useState(false);
  const jobs = request.jobs || [];
  const ready = request.wantedIssueCount || 0;
  const upcoming = request.upcomingIssueCount || 0;
  const unknown = request.unknownReleaseIssueCount || 0;
  const queued = request.queuedJobCount || 0;
  const searching = jobs.filter((job) => job.status === "searching").length;
  const downloading = jobs.filter((job) => ["queued", "downloading"].includes(job.downloadStatus)).length;
  const importing = jobs.filter((job) => ["completed", "importing"].includes(job.downloadStatus)).length;
  const failed = jobs.filter((job) => job.status === "failed" || job.downloadStatus === "failed").length;
  const scope = [
    ready ? `${ready} missing issue${ready === 1 ? "" : "s"}` : null,
    upcoming ? `${upcoming} upcoming` : null,
    unknown ? `${unknown} release date${unknown === 1 ? "" : "s"} unknown` : null,
    `${request.ownedIssueCount} of ${request.targetIssueCount} owned`,
  ].filter(Boolean).join(" · ");
  const status = request.status === "fulfilled" ? "Complete" : failed ? "Needs attention" : importing ? `${importing} adding to library` : downloading ? `${downloading} downloading` : searching ? "Searching" : queued ? `${queued} wanted` : upcoming ? "Waiting for release" : "Checking release dates";
  const tone = request.status === "fulfilled" ? "green" : failed ? "red" : queued || searching || downloading || importing ? "violet" : upcoming ? "green" : "muted";
  const display = { id: `request-${request.id}`, title: request.title, cover: request.cover };
  const jobGroups = jobs.reduce((groups, job) => {
    const key = job.seriesId || "series";
    if (!groups[key]) groups[key] = { id: key, title: job.seriesTitle || request.title, jobs: [] };
    groups[key].jobs.push(job);
    return groups;
  }, {});
  return <article className={`request-card ${expanded ? "expanded" : ""}`}>
    <div className="request-row">
      <span className="request-cover"><SeriesCover series={display} decorative /></span>
      <div><h3>{request.title}</h3><p>{scope}</p><span>Added {request.requestedDate} {request.requestedTime} · {ACQUISITION_LABELS[request.acquisitionPreference] || ACQUISITION_LABELS.either}</span></div>
      <b className={`status-chip ${tone}`}>{status}</b>
      <button className="request-expand" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}><span>{expanded ? "Hide issues" : "View missing issues"}</span><CaretDown size={17} /></button>
    </div>
    {expanded ? <div className="request-job-panel">
      <header><div><strong>Missing issues</strong><span>Search when you’re ready and compare Prowlarr results before anything is downloaded.</span></div><b>Following</b></header>
      {jobs.length ? <div className="request-jobs">{Object.values(jobGroups).map((group) => <section className="request-job-group" key={group.id}>
        <header><span>Series run</span><strong>{group.title}</strong><b>{group.jobs.length} issue{group.jobs.length === 1 ? "" : "s"}</b></header>
        {group.jobs.map((job) => { const displayStatus = job.downloadStatus || job.status; const displayDetail = job.downloadError || (job.downloadDestination ? `Added as ${job.downloadDestination.split("/").pop()}` : job.downloadTitle); return <div className="request-job" key={job.id}>
          <b>#{job.issueNumber}</b>
          <div><strong>{job.issueTitle || `Issue ${job.issueNumber}`}</strong><span>{job.reason}</span>{displayDetail ? <span className={job.downloadError ? "job-error" : ""}>{displayDetail}</span> : null}</div>
          <span className="request-job-actions"><span className={`job-state ${displayStatus}`}>{DOWNLOAD_STATUS_LABELS[job.downloadStatus] || JOB_STATUS_LABELS[job.status] || displayStatus}</span>{!["grabbed", "fulfilled", "cancelled"].includes(job.status) ? <button type="button" onClick={() => onFindRelease(job)}><MagnifyingGlass size={14} /> Find release</button> : null}</span>
        </div>; })}
      </section>)}</div> : <div className="request-job-empty"><ArrowsClockwise size={20} /><div><strong>Nothing is ready to search yet</strong><span>No action is required. Comic Library will keep checking release dates automatically.</span></div></div>}
      {upcoming || unknown ? <footer>{upcoming ? `${upcoming} upcoming issue${upcoming === 1 ? " is" : "s are"} being followed` : null}{upcoming && unknown ? " · " : null}{unknown ? `${unknown} issue${unknown === 1 ? " has" : "s have"} an unknown release date` : null}</footer> : null}
    </div> : null}
  </article>;
}

function formatReleaseSize(bytes) {
  const value = Number(bytes || 0);
  if (!value) return "Size unavailable";
  if (value >= 1024 ** 3) return `${(value / (1024 ** 3)).toFixed(2)} GB`;
  return `${Math.max(1, Math.round(value / (1024 ** 2)))} MB`;
}

function ReleaseSearchModal({ job, onClose, onGrabbed }) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [grabbingId, setGrabbingId] = useState(null);
  async function search() {
    setLoading(true); setError("");
    try {
      setResult(await apiRequest(`/api/v1/acquisition-jobs/${job.id}/search`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      }));
    } catch (searchError) {
      setError(searchError.message || "Prowlarr search failed");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { search(); }, [job.id]);
  async function grab(candidate) {
    setGrabbingId(candidate.id); setError("");
    try {
      await apiRequest(`/api/v1/acquisition-jobs/${job.id}/grab`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidateId: candidate.id }),
      });
      await onGrabbed?.();
    } catch (grabError) {
      setError(grabError.message || "SABnzbd could not accept this release");
      setGrabbingId(null);
    }
  }
  const candidates = result?.candidates || [];
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal release-search-modal" role="dialog" aria-modal="true" aria-labelledby="release-search-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close release search"><X size={20} /></button><span className="eyebrow">Find one missing issue</span><h2 id="release-search-title">{job.seriesTitle} #{job.issueNumber}</h2><p className="workbench-intro">Compare Prowlarr results below. Nothing is sent to SABnzbd until you choose a release.</p>{result?.query ? <div className="release-query"><MagnifyingGlass size={16} /><span>Search</span><strong>{result.query}</strong></div> : null}{loading ? <div className="release-loading"><SpinnerGap size={24} /><div><strong>Searching your indexers…</strong><span>This can take a few seconds.</span></div></div> : null}{error ? <div className="release-error"><WarningCircle size={19} weight="fill" /><span><strong>Release search needs attention</strong>{error}</span><button type="button" onClick={search}>Try again</button></div> : null}{!loading && !error && !candidates.length ? <div className="release-empty"><MagnifyingGlass size={28} /><strong>No credible releases found</strong><span>Prowlarr returned no Usenet results that matched both this series and issue number.</span><button type="button" className="secondary-button" onClick={search}>Search again</button></div> : null}{candidates.length ? <div className="release-candidates"><header><div><strong>{candidates.length} candidate{candidates.length === 1 ? "" : "s"}</strong><span>Best matches appear first. Confirm the title, issue, language, and format.</span></div></header>{candidates.map((candidate) => <article className="release-candidate" key={candidate.id}><div className="release-candidate-main"><span className={`status-chip ${candidate.matchScore >= 85 ? "green" : "amber"}`}>{candidate.matchStrength}</span><h3>{candidate.title}</h3><p>{candidate.indexer} · {candidate.protocol} · {formatReleaseSize(candidate.sizeBytes)}{candidate.publishDate ? ` · ${new Date(candidate.publishDate).toLocaleDateString()}` : ""}</p>{candidate.formatTags?.length ? <div className="release-tags">{candidate.formatTags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}</div><div className="release-match"><strong>{candidate.matchScore}</strong><span>match score</span></div><ul>{candidate.matchReasons.map((reason) => <li key={reason}><CheckCircle size={14} weight="fill" />{reason}</li>)}</ul><button type="button" className="primary-button" disabled={Boolean(grabbingId)} onClick={() => grab(candidate)}>{grabbingId === candidate.id ? <SpinnerGap size={17} /> : <CloudArrowDown size={17} />}{grabbingId === candidate.id ? "Sending…" : "Send to SABnzbd"}</button></article>)}</div> : null}<footer className="release-modal-footer"><ShieldCheck size={17} weight="fill" /> Prowlarr download links stay on the server and are never exposed in this page.</footer></section></div>;
}

function RequestModal({ catalog, onClose, onCreate }) {
  const [term, setTerm] = useState("");
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState("");
  const targets = logicalCatalogSeries(catalog).filter((item) => `${item.title} ${item.publisher}`.toLowerCase().includes(term.toLowerCase()));
  async function choose(target) {
    setBusyId(target.id); setError("");
    const result = await onCreate(target);
    if (!result?.ok) setError(result?.error || "Request could not be created");
    setBusyId(null);
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}><section className="modal request-modal" role="dialog" aria-modal="true" aria-labelledby="request-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close"><X size={20} /></button><span className="eyebrow">Add to wanted list</span><h2 id="request-title">Choose a series</h2><p className="workbench-intro">Comic Library will follow the issue list and add released missing issues automatically.</p><SearchBar value={term} onChange={setTerm} placeholder="Search series…" /><div className="request-results">{targets.map((target) => { const summary = target.releaseSummary || {}; const unavailable = !target.total || !target.catalogKnown || !target.unowned; const scopeType = target.isCollectionSeries ? "collection" : "series"; const scopeId = String(target.isCollectionSeries ? target.collection.id : target.id); const existing = (catalog?.requests || []).some((request) => request.status === "open" && request.scopeType === scopeType && request.scopeId === scopeId); return <div className="request-result" key={target.id}><span className="request-result-cover"><SeriesCover series={target} decorative /></span><div><strong>{target.title}</strong><span>{target.publisher} · {target.year}</span><small>{target.catalogKnown ? `${target.owned} of ${target.total} owned · ${summary.releasedMissing || 0} missing issue${summary.releasedMissing === 1 ? "" : "s"}` : "Issue list unavailable"}</small></div><button disabled={unavailable || busyId === target.id} onClick={() => choose(target)}>{busyId === target.id ? "Adding…" : existing ? "Update request" : target.monitoringStatus === "monitored" ? "Request missing" : "Add and follow"}</button></div>; })}</div>{!targets.length ? <div className="drawer-empty"><MagnifyingGlass size={27} /><strong>No matching series</strong></div> : null}{error ? <p className="workbench-error">{error}</p> : null}<p className="modal-hint">Upcoming issues stay followed. Issues with unknown release dates stay off the missing list until confirmed.</p></section></div>;
}

function MetadataView({ items, backendStatus, onResolve, onReplace }) {
  const entries = items.length ? items : (backendStatus === "offline" ? DEMO_META_ITEMS : []);
  const [selected, setSelected] = useState(0);
  const [resolved, setResolved] = useState([]);
  const item = entries[Math.min(selected, Math.max(entries.length - 1, 0))];
  const isResolved = resolved.includes(selected);
  if (!item) return <><PageHeader title="Library health" description="Replace damaged or incorrect comics and review uncertain metadata." /><div className="empty-state"><CheckCircle size={35} weight="duotone" /><strong>Your library looks healthy</strong><span>Damaged files and uncertain matches will appear here after a scan.</span></div></>;
  async function resolveCurrent() {
    if (item.path && item.code && item.fingerprint) await onResolve(item);
    else setResolved([...resolved, selected]);
  }
  const fileProblem = item.category === "file" || ["empty_archive", "no_image_pages", "corrupt_archive", "file_health"].includes(item.code);
  return <><PageHeader title="Library health" description="Replace damaged or incorrect comics and review uncertain metadata." /><div className="metadata-layout"><aside className="inbox-list"><div className="inbox-label">Needs attention <b>{entries.length - resolved.length}</b></div>{entries.map((entry, index) => <button key={entry.id ?? `${entry.file}-${entry.issue}`} className={`${selected === index ? "active" : ""} ${resolved.includes(index) ? "resolved" : ""}`} onClick={() => setSelected(index)}><WarningCircle size={19} weight={entry.severity === "error" ? "fill" : "regular"} /><span><strong>{entry.file}</strong><small>{resolved.includes(index) ? "Fixed" : `${entry.category === "file" ? "Comic file" : "Metadata"} · ${entry.issue}`}</small></span></button>)}</aside><section className="review-panel"><div className="review-heading"><span className={`status-chip ${item.severity === "error" ? "red" : "amber"}`}>{fileProblem ? "Comic file problem" : "Metadata review"}</span><h2>{item.file}</h2><p>{item.detail}</p></div>{fileProblem ? <FileProblem item={item} onReplace={onReplace} /> : item.code === "metadata_conflict" && item.comparison ? <MetadataComparison comparison={item.comparison} /> : <MetadataProblem item={item} />}<div className="review-actions"><button className={fileProblem ? "ghost-button" : "primary-button"} disabled={isResolved} onClick={resolveCurrent}><CheckCircle size={19} /> {isResolved ? "Fixed" : "Mark fixed"}</button>{!fileProblem ? <button className="ghost-button"><PencilSimple size={18} /> Edit metadata</button> : null}<button className="ghost-button">Dismiss</button></div></section></div></>;
}

function FileProblem({ item, onReplace }) {
  const [revealState, setRevealState] = useState({ status: "idle", message: "" });
  useEffect(() => setRevealState({ status: "idle", message: "" }), [item.path]);
  async function revealFile() {
    if (!item.path) {
      setRevealState({ status: "error", message: "The file path is unavailable." });
      return;
    }
    setRevealState({ status: "loading", message: "Opening Finder…" });
    try {
      const result = await apiRequest("/api/v1/reveal-file", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: item.path }),
      });
      setRevealState({ status: "success", message: result.message || "Shown in Finder." });
    } catch (error) {
      setRevealState({ status: "error", message: error.message || "Finder could not show this file." });
    }
  }
  const replacementOpen = item.replacementStatus && !["fulfilled", "cancelled"].includes(item.replacementStatus);
  return <div className="file-problem"><HardDrive size={35} weight="duotone" /><div><strong>{item.issue}</strong><p>{item.detail}</p><div className="file-problem-actions"><button className="primary-button" onClick={() => onReplace(item)} disabled={replacementOpen}><CloudArrowDown size={18} /> {replacementOpen ? "Fix requested" : "Replace comic"}</button><button onClick={revealFile} disabled={revealState.status === "loading"}>{revealState.status === "loading" ? <SpinnerGap className="spin" size={18} /> : <FolderOpen size={18} />} {revealState.status === "loading" ? "Opening Finder…" : "Show comic file"}</button></div><small className="replacement-help">Add replacement issues or volumes without deleting this file. It stays in place until the requested issues or volumes are downloaded and verified.</small>{revealState.message ? <small className={`file-reveal-status ${revealState.status}`}>{revealState.message}</small> : null}</div></div>;
}

function MetadataProblem({ item }) {
  return <div className="metadata-problem"><Database size={32} weight="duotone" /><div><strong>{item.issue}</strong><p>{item.detail}</p><small>This changes the library record only; the comic file is not modified.</small></div></div>;
}

function ReplacementModal({ file, busy, error, onClose, onSubmit }) {
  const automaticReason = file.code === "empty_archive" ? "empty" : file.code === "no_image_pages" ? "no_pages" : "corrupt";
  const [reason, setReason] = useState(automaticReason);
  const [language, setLanguage] = useState("English");
  const [acquisitionPreference, setAcquisitionPreference] = useState("either");
  function submit(event) {
    event.preventDefault();
    onSubmit({ reason, desiredLanguage: reason === "wrong_language" ? language : null, acquisitionPreference });
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal replacement-modal" role="dialog" aria-modal="true" aria-labelledby="replacement-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close replacement request"><X size={20} /></button><span className="eyebrow">Fix comic run</span><h2 id="replacement-title">{file.file || file.filename}</h2><p className="workbench-intro">Add this comic’s issues to the wanted list. Comic Library can use replacement volumes, the underlying issues, or whichever is found first.</p><form onSubmit={submit}><label className="form-field"><span>How should Comic Library complete the run?</span><select value={acquisitionPreference} onChange={(event) => setAcquisitionPreference(event.target.value)}><option value="either">Issues or volumes</option><option value="issues">Single issues</option><option value="volumes">Volumes</option></select></label><label className="form-field"><span>Why replace it?</span><select value={reason} onChange={(event) => setReason(event.target.value)}><option value="corrupt">Corrupt or unreadable file</option><option value="no_pages">No readable comic pages</option><option value="empty">Empty archive</option><option value="wrong_language">Wrong language</option><option value="wrong_release">Wrong edition or release</option><option value="poor_quality">Poor scan or image quality</option></select></label>{reason === "wrong_language" ? <label className="form-field"><span>Language wanted</span><input value={language} onChange={(event) => setLanguage(event.target.value)} placeholder="English" required /></label> : null}<div className="replacement-summary"><ShieldCheck size={19} weight="fill" /><span><strong>No automatic deletion</strong><small>The current file stays in place until replacement issues or volumes pass file checks and complete the same run.</small></span></div>{error ? <p className="workbench-error">{error}</p> : null}<div className="metadata-edit-actions"><button type="button" className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || (reason === "wrong_language" && !language.trim())}>{busy ? <SpinnerGap size={18} /> : <CloudArrowDown size={18} />} Add to wanted</button></div></form></section></div>;
}

function MetadataComparison({ comparison }) {
  return <div className="comparison"><div className="comparison-head"><span>Field</span><span>{comparison.catalogLabel}</span><span>{comparison.fileLabel}</span></div>{comparison.rows.map((row) => <div className="comparison-row" key={row.field}><strong>{row.field}</strong><span className={row.catalog ? "" : "missing"}>{row.catalog ? <CheckCircle size={16} weight="fill" /> : <WarningCircle size={16} />} {row.catalog || "Not available"}</span><span className={row.status}>{row.status === "match" ? <CheckCircle size={16} weight="fill" /> : <WarningCircle size={16} weight={row.status === "conflict" ? "fill" : "regular"} />} {row.file || "Not available"}</span></div>)}{comparison.catalogUrl ? <div className="comparison-source"><a href={comparison.catalogUrl} target="_blank" rel="noreferrer">Open metadata source</a></div> : null}</div>;
}

function ActivityView() {
  const events = [{ icon: CheckCircle, title: "Library scan finished", copy: "10 comic files checked · no new files", time: "10:14 AM", tone: "green" }, { icon: WarningCircle, title: "3 files need attention", copy: "One empty archive and two metadata fixes", time: "10:13 AM", tone: "amber" }, { icon: Database, title: "Issue matched", copy: "Absolute Batman #3 matched to the correct series", time: "10:12 AM", tone: "violet" }, { icon: CloudArrowDown, title: "Series added to wanted list", copy: "Birthright’s missing issues are ready to search", time: "9:48 AM", tone: "muted" }];
  return <><PageHeader title="Activity" description="A history of library scans, metadata fixes, and wanted series." /><section className="timeline">{events.map(({ icon: Icon, title, copy, time, tone }) => <article key={title}><div className={`timeline-icon ${tone}`}><Icon size={20} weight="fill" /></div><div><h3>{title}</h3><p>{copy}</p></div><time>Aug 26, 2026<br />{time}</time></article>)}</section></>;
}

function SettingsView({ catalog }) {
  const [notifications, setNotifications] = useState(true);
  const [autoAccept, setAutoAccept] = useState(true);
  const [providers, setProviders] = useState([]);
  const [providerError, setProviderError] = useState("");
  const [editingProvider, setEditingProvider] = useState(null);
  const [services, setServices] = useState([]);
  const [serviceError, setServiceError] = useState("");
  const [editingService, setEditingService] = useState(null);
  async function loadProviders() {
    try {
      const result = await apiRequest("/api/v1/providers");
      setProviders(result.providers || []);
      setProviderError("");
    } catch (error) {
      setProviderError(error.message);
    }
  }
  async function loadServices() {
    try {
      const result = await apiRequest("/api/v1/acquisition-services");
      setServices(result.services || []);
      setServiceError("");
    } catch (error) {
      setServiceError(error.message);
    }
  }
  useEffect(() => { loadProviders(); loadServices(); }, []);
  return <><PageHeader title="Settings" description="Configure your library, metadata sources, indexer search, and download client." /><div className="settings-layout"><section><h2>Library</h2><label className="form-field"><span>Comics folder</span><input value={catalog?.roots?.[0]?.path ?? "No comics folder configured"} readOnly /></label><button className="secondary-button"><FolderOpen size={18} /> Change folder</button></section><section><h2>Matching and fixes</h2><Toggle checked={autoAccept} onChange={setAutoAccept} title="Automatically accept strong matches" description="Use source data automatically when services agree. Only unclear comics appear under Fix metadata." /><Toggle checked={notifications} onChange={setNotifications} title="Notify me when comics need attention" description="Notify me about damaged files or uncertain matches." /></section><section className="metadata-source-settings acquisition-source-settings"><header><div><h2>Acquisition services</h2><p>Connect Prowlarr to find releases and SABnzbd to download the one you choose.</p></div></header>{services.map((service) => <AcquisitionService service={service} onConfigure={() => setEditingService(service)} key={service.id} />)}{serviceError ? <p className="workbench-error">{serviceError}</p> : null}</section><section className="metadata-source-settings"><header><div><h2>Metadata sources</h2><p>Built-in sources work immediately. Add API credentials for more issue titles, dates, covers, and matches.</p></div></header>{providers.map((provider) => <Provider provider={provider} onConfigure={() => setEditingProvider(provider)} key={provider.id} />)}{providerError ? <p className="workbench-error">{providerError}</p> : null}<aside className="provider-policy-note"><ShieldCheck size={19} weight="fill" /><span><strong>Your API credentials stay on this device</strong><small>Keys are hidden after saving and sent only to the service you configure.</small></span></aside></section></div>{editingProvider ? <ProviderSettingsModal provider={editingProvider} onClose={() => setEditingProvider(null)} onSaved={async () => { await loadProviders(); setEditingProvider(null); }} /> : null}{editingService ? <AcquisitionServiceSettingsModal service={editingService} onClose={() => setEditingService(null)} onSaved={async () => { await loadServices(); setEditingService(null); }} /> : null}</>;
}

function Toggle({ checked, onChange, title, description }) {
  return <label className="toggle-row"><span><strong>{title}</strong><small>{description}</small></span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
}

function Provider({ provider, onConfigure }) {
  const status = provider.builtIn ? "Available without an account" : provider.enabled ? "Enabled" : provider.configured ? "Configured but disabled" : "Not configured";
  return <div className={`provider-row ${provider.enabled ? "enabled" : ""}`}><Database size={23} weight="duotone" /><span><span className="provider-title-line"><strong>{provider.name}</strong><b className={`provider-state ${provider.enabled ? "connected" : provider.configured ? "paused" : "optional"}`}>{status}</b></span><small>{provider.description}</small><em>{provider.capabilities.join(" · ")}</em></span>{provider.builtIn ? <b className="provider-priority">Priority {provider.priority}</b> : <button onClick={onConfigure}>{provider.configured ? "Manage" : "Configure"}</button>}</div>;
}

function AcquisitionService({ service, onConfigure }) {
  const status = service.enabled ? "Ready" : service.configured ? "Configured but disabled" : "Not connected";
  const Icon = service.id === "prowlarr" ? MagnifyingGlass : CloudArrowDown;
  return <div className={`provider-row ${service.enabled ? "enabled" : ""}`}><Icon size={23} weight="duotone" /><span><span className="provider-title-line"><strong>{service.name}</strong><b className={`provider-state ${service.enabled ? "connected" : service.configured ? "paused" : "optional"}`}>{status}</b></span><small>{service.description}</small><em>{service.kind} · {service.capabilities.join(" · ")}</em></span><button onClick={onConfigure}>{service.configured ? "Manage" : "Connect"}</button></div>;
}

function AcquisitionServiceSettingsModal({ service, onClose, onSaved }) {
  const [url, setUrl] = useState(service.url || "");
  const [apiKey, setApiKey] = useState("");
  const [category, setCategory] = useState(service.category || "comics");
  const [enabled, setEnabled] = useState(service.enabled || !service.configured);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const payload = () => ({ url, ...(apiKey ? { apiKey } : {}), ...(service.id === "sabnzbd" ? { category } : {}) });
  async function test() {
    setBusy("test"); setError(""); setResult("");
    try {
      const response = await apiRequest(`/api/v1/acquisition-services/${service.id}/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload()) });
      setResult(response.detail);
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(""); }
  }
  async function save(event) {
    event.preventDefault(); setBusy("save"); setError("");
    try {
      await apiRequest(`/api/v1/acquisition-services/${service.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...payload(), enabled }) });
      await onSaved();
    } catch (requestError) { setError(requestError.message); setBusy(""); }
  }
  async function disconnect() {
    setBusy("remove"); setError("");
    try {
      await apiRequest(`/api/v1/acquisition-services/${service.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ clearCredentials: true, enabled: false }) });
      await onSaved();
    } catch (requestError) { setError(requestError.message); setBusy(""); }
  }
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal provider-settings-modal" role="dialog" aria-modal="true" aria-labelledby="acquisition-service-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close acquisition service settings"><X size={20} /></button><span className="eyebrow">{service.kind}</span><h2 id="acquisition-service-title">Connect {service.name}</h2><p className="workbench-intro">{service.description}</p><form onSubmit={save}><label className="form-field"><span>Server URL</span><input value={url} onChange={(event) => setUrl(event.target.value)} placeholder={service.id === "prowlarr" ? "http://nas:9696" : "http://nas:8080"} required /></label><label className="form-field"><span>API key</span><input type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={service.configured ? "Saved locally · enter a new key to replace it" : `Enter your ${service.name} API key…`} /></label>{service.id === "sabnzbd" ? <label className="form-field"><span>SABnzbd category</span><input value={category} onChange={(event) => setCategory(event.target.value)} placeholder="comics" required /><small>Comic Library will use this category to identify and monitor its downloads.</small></label> : null}<Toggle checked={enabled} onChange={setEnabled} title={`Use ${service.name}`} description={service.id === "prowlarr" ? "Search configured Usenet indexers for wanted comics." : "Send selected NZBs to SABnzbd and monitor their progress."} />{result ? <p className="provider-test-result"><CheckCircle size={17} weight="fill" /> {result}</p> : null}{error ? <p className="workbench-error">{error}</p> : null}<div className="provider-modal-actions"><button type="button" className="secondary-button" onClick={test} disabled={Boolean(busy) || !url.trim() || (!apiKey && !service.configured)}>{busy === "test" ? <SpinnerGap size={17} /> : <ArrowsClockwise size={17} />} Test connection</button><span />{service.configured ? <button type="button" className="danger-button" onClick={disconnect} disabled={Boolean(busy)}>Disconnect</button> : null}<button className="primary-button" disabled={Boolean(busy) || !url.trim() || (!apiKey && !service.configured)}>{busy === "save" ? <SpinnerGap size={17} /> : <ShieldCheck size={17} />} Save connection</button></div><small className="provider-credential-help">The API key is stored locally and is never returned to the browser after saving.</small></form></section></div>;
}

function ProviderSettingsModal({ provider, onClose, onSaved }) {
  const field = provider.credentialField;
  const [credential, setCredential] = useState("");
  const [enabled, setEnabled] = useState(provider.enabled || !provider.configured);
  const [priority, setPriority] = useState(provider.priority || 20);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const label = provider.id === "metron" ? "API token" : "API key";
  async function test() {
    setBusy("test"); setError(""); setResult("");
    try {
      const payload = credential ? { [field]: credential } : {};
      const response = await apiRequest(`/api/v1/providers/${provider.id}/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      setResult(response.detail);
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(""); }
  }
  async function save(event) {
    event.preventDefault();
    setBusy("save"); setError("");
    try {
      const payload = { enabled, priority, ...(credential ? { [field]: credential } : {}) };
      await apiRequest(`/api/v1/providers/${provider.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      await onSaved();
    } catch (requestError) { setError(requestError.message); setBusy(""); }
  }
  async function removeCredentials() {
    setBusy("remove"); setError("");
    try {
      await apiRequest(`/api/v1/providers/${provider.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ clearCredentials: true, enabled: false, priority }) });
      await onSaved();
    } catch (requestError) { setError(requestError.message); setBusy(""); }
  }
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal provider-settings-modal" role="dialog" aria-modal="true" aria-labelledby="provider-settings-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close provider settings"><X size={20} /></button><span className="eyebrow">Metadata provider</span><h2 id="provider-settings-title">Connect {provider.name}</h2><p className="workbench-intro">{provider.description}</p><form onSubmit={save}><label className="form-field"><span>{label}</span><input type="password" autoComplete="off" value={credential} onChange={(event) => setCredential(event.target.value)} placeholder={provider.configured ? "Saved locally · enter a new value to replace it" : `Enter your ${provider.name} ${label.toLowerCase()}…`} /></label><small className="provider-credential-help">{provider.id === "metron" ? "Create a token from the API Tokens section of your Metron account." : "Comic Vine API access is intended for personal, non-commercial use. Your key is never returned to the browser after saving."}</small><Toggle checked={enabled} onChange={setEnabled} title={`Use ${provider.name} for enrichment`} description="Fill missing fields automatically while preserving locked local corrections and higher-priority source data." /><label className="form-field provider-priority-field"><span>Provider priority</span><select value={priority} onChange={(event) => setPriority(Number(event.target.value))}><option value="15">Before other optional providers</option><option value="20">Normal priority</option><option value="30">Fallback priority</option></select><small>Built-in GCD structure remains first. Optional providers fill fields that are still missing.</small></label>{result ? <p className="provider-test-result"><CheckCircle size={17} weight="fill" /> {result}</p> : null}{error ? <p className="workbench-error">{error}</p> : null}<div className="provider-modal-actions"><button type="button" className="secondary-button" onClick={test} disabled={Boolean(busy) || (!credential && !provider.configured)}>{busy === "test" ? <SpinnerGap size={17} /> : <ArrowsClockwise size={17} />} Test connection</button><span />{provider.configured ? <button type="button" className="danger-button" onClick={removeCredentials} disabled={Boolean(busy)}>Remove</button> : null}<button className="primary-button" disabled={Boolean(busy) || (!credential && !provider.configured)}>{busy === "save" ? <SpinnerGap size={17} /> : <ShieldCheck size={17} />} Save provider</button></div></form></section></div>;
}

function GroupedIssueInventory({ issues, onEditIssue }) {
  if (!issues?.length) return <div className="drawer-empty"><BookOpen size={26} weight="duotone" /><strong>No issue list linked yet</strong><span>Comic Library will add series runs and their issues automatically when a match is found.</span></div>;
  const groups = [];
  const byRun = new Map();
  for (const issue of issues) {
    const key = String(issue.contextRunId || issue.contextLabel || "unknown");
    if (!byRun.has(key)) {
      const group = {
        key,
        title: issue.contextGroupName || issue.contextLabel || "Unknown run",
        runTitle: issue.contextLabel,
        year: issue.contextYear,
        type: issue.contextType || "main",
        issues: [],
      };
      byRun.set(key, group);
      groups.push(group);
    }
    byRun.get(key).issues.push(issue);
  }
  return <div className="grouped-issue-inventory">{groups.map((group) => {
    const owned = group.issues.filter((issue) => issue.ownership !== "unowned").length;
    return <section className="issue-run-group" key={group.key}>
      <header><div><span>{group.type === "specials" ? "Special / one-shot" : "Series run"}</span><h3>{group.title}</h3>{group.runTitle !== group.title || group.year ? <small>{[group.runTitle !== group.title ? group.runTitle : null, group.year].filter(Boolean).join(" · ")}</small> : null}</div><strong>{owned} of {group.issues.length} owned</strong></header>
      <div>{group.issues.map((issue) => {
        const genericTitle = !issue.title || identityKey(issue.title) === identityKey(`Issue ${issue.number}`);
        const releaseLabel = issue.publicationDate
          ? new Date(`${issue.publicationDate}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
          : issue.publicationYear ? `Published ${issue.publicationYear}` : "Release date unknown";
        const stateLabel = issue.ownership === "both" ? "Single + volume"
          : issue.ownership === "direct" ? "Single issue"
          : issue.ownership === "collection" ? "In volume"
          : issue.releaseState === "upcoming" ? "Upcoming"
          : issue.releaseState === "unknown" ? "Date needed" : "Missing";
        return <article key={issue.id}><span className="grouped-issue-number">#{issue.number}</span><div>{!genericTitle ? <strong>{issue.title}{issue.metadataLocked ? <ShieldCheck className="issue-local-lock" size={13} weight="fill" aria-label="Local metadata correction locked" /> : null}</strong> : null}<small>{releaseLabel}</small></div><div className="issue-row-actions"><span className={`ownership-source ${issue.ownership} ${issue.acquisitionState || ""}`}>{stateLabel}</span>{onEditIssue ? <button type="button" onClick={() => onEditIssue(issue)} aria-label={`Edit metadata for ${issue.contextLabel || "issue"} issue ${issue.number}`} title="Edit issue metadata"><PencilSimple size={14} /></button> : null}</div></article>;
      })}</div>
    </section>;
  })}</div>;
}

function VolumeInventory({ editions }) {
  if (!editions?.length) return <div className="drawer-empty"><Books size={26} weight="duotone" /><strong>No volumes linked yet</strong><span>Collected files will appear here after inventory.</span></div>;
  return <div className="edition-inventory">{editions.map((edition) => <article key={edition.id}><header><div><strong>{edition.subtitle ? `${edition.title}: ${edition.subtitle}` : edition.title}</strong><small>{[edition.publisher, edition.publicationYear, edition.format].filter(Boolean).join(" · ") || "Volume details incomplete"}</small></div><span><b>{editionKindLabel(edition.editionKind)}{edition.volume ? ` · Vol. ${edition.volume}` : ""}</b><small>{edition.source || "Local metadata"}</small>{edition.coverageOverrideCount ? <small>{edition.coverageOverrideCount} local contents correction{edition.coverageOverrideCount === 1 ? "" : "s"}</small> : null}</span></header>{edition.isbns?.length ? <p><b>ISBN</b> {edition.isbns.join(", ")}</p> : null}<div className="coverage-groups">{edition.coverageGroups?.length ? edition.coverageGroups.map((coverage) => <div className={coverage.resolved ? "resolved" : "unresolved"} key={`${coverage.seriesLabel}-${coverage.issueLabel}-${coverage.source}`}><span><strong>{coverage.seriesLabel} #{coverage.issueLabel}</strong><small>{coverage.resolved ? "Counts toward canonical ownership" : "Visible claim; canonical numbering unresolved"}</small></span><b>{coverage.confidence}</b><p>{coverage.source}{coverage.evidence ? ` · ${volumeTerminology(coverage.evidence)}` : ""}</p></div>) : <div className="no-coverage"><WarningCircle size={18} /><span><strong>Contents not established</strong><small>{volumeTerminology(edition.coverageStatus) || "No structured issue coverage was returned by the current sources."}</small></span></div>}</div></article>)}</div>;
}

function FileInventory({ files, onOpenWorkbench, onOpenCover, onOpenContents, onChangeRun, onReplace }) {
  if (!files?.length) return <div className="drawer-empty"><HardDrive size={26} weight="duotone" /><strong>No local files linked</strong></div>;
  return <div className="file-inventory">{files.map((file) => <article key={file.path}><HardDrive size={20} weight="duotone" /><div><strong>{file.filename}</strong><small>{file.identityKind === "issue" ? "Single issue" : editionKindLabel(file.editionKind)} · {(file.sizeBytes / 1024 / 1024).toFixed(1)} MB</small>{file.metadataLocked ? <small className="metadata-lock"><ShieldCheck size={13} weight="fill" /> Local corrections locked</small> : null}</div><span className="file-row-actions">{file.identityKind === "edition" ? <button onClick={() => onOpenContents(file)}><ListBullets size={15} /> Issues</button> : null}<button onClick={() => onReplace(file)}><CloudArrowDown size={15} /> Replace</button><button onClick={() => onChangeRun(file)}><Books size={15} /> Change run</button><button onClick={() => onOpenCover(file)}><BookOpen size={15} /> Cover</button><button onClick={() => onOpenWorkbench(file, "match")}><ArrowsClockwise size={15} /> Fix match</button><button onClick={() => onOpenWorkbench(file, "edit")}><PencilSimple size={15} /> Edit</button></span></article>)}</div>;
}

function IssueCatalogCard({ series, catalogKnown, syncing, error, lastResult, onSync, onReviewFiles, onFindRun }) {
  const catalog = series.issueCatalog || { status: "unknown", syncReady: false };
  const collectionIssueCount = series.issues?.filter((issue) => issue.collectionOwned).length || 0;
  const directFiles = series.inventory?.directIssueFiles || 0;
  const editionCount = series.inventory?.editionCount || 0;
  const issues = series.issues || [];
  const numberedOnly = catalog.titlePolicy === "numbered_only";
  const missingTitleIssues = numberedOnly ? [] : issues.filter((issue) => !String(issue.title || "").trim());
  const upcomingUntitled = missingTitleIssues.filter((issue) => issue.releaseState === "upcoming");
  const repairableTitles = missingTitleIssues.filter((issue) => issue.releaseState !== "upcoming");
  const missingDateIssues = issues.filter((issue) => issue.publicationYear == null);
  const repairableFields = repairableTitles.length + missingDateIssues.length;
  const resultMetadata = lastResult?.metadata;
  const repairedFields = (resultMetadata?.repairedTitleCount || 0) + (resultMetadata?.repairedDateCount || 0);
  const providerErrors = lastResult?.providerErrors || [];
  const checkedAt = catalog.lastSyncedAt ? new Date(catalog.lastSyncedAt).toLocaleString() : null;
  if (catalogKnown) {
    let tone = repairableFields ? "repairing" : "";
    let heading = "Issue details complete";
    let outcome = `No missing issue titles or release dates across ${catalog.issueCount || issues.length} issues.`;
    if (syncing) {
      heading = "Checking metadata sources";
      outcome = "Looking for newly available issue titles and release dates.";
    } else if (error || catalog.error) {
      tone = "error";
      heading = "Metadata check couldn’t finish";
      outcome = "Existing issue information is unchanged. You can try again without losing local corrections.";
    } else if (repairableFields) {
      heading = `${repairableFields} issue detail${repairableFields === 1 ? " is" : "s are"} still unavailable`;
      const parts = [];
      if (repairableTitles.length) parts.push(`${repairableTitles.length} missing title${repairableTitles.length === 1 ? "" : "s"}`);
      if (missingDateIssues.length) parts.push(`${missingDateIssues.length} missing release date${missingDateIssues.length === 1 ? "" : "s"}`);
      outcome = `${parts.join(" and ")}. Enabled providers were checked but do not currently supply ${repairableFields === 1 ? "this field" : "these fields"}.`;
    } else if (numberedOnly) {
      heading = "Issue metadata current";
      outcome = "This run is listed by issue number. The matched sources do not publish separate issue titles.";
    } else if (upcomingUntitled.length) {
      heading = "Metadata current";
      const labels = upcomingUntitled.map((issue) => `#${issue.number}`).join(", ");
      outcome = `${labels} ${upcomingUntitled.length === 1 ? "is" : "are"} upcoming; ${upcomingUntitled.length === 1 ? "its title has" : "their titles have"} not been announced yet.`;
    }
    return <section className={`issue-catalog-card synced ${tone}`}><div className="issue-catalog-outcome"><strong>{heading}</strong><small>{outcome}</small>{repairedFields ? <em><CheckCircle size={14} weight="fill" /> {repairedFields} field{repairedFields === 1 ? "" : "s"} filled during the latest check.</em> : resultMetadata && !syncing ? <em>No additional fields were available during the latest check.</em> : null}<details><summary>View metadata details</summary><div className="issue-catalog-details"><p>{catalog.detail || `The issue list is linked through ${catalog.provider?.toUpperCase() || "the metadata provider"}.`}</p>{checkedAt ? <time>Last checked {checkedAt}</time> : null}{providerErrors.length ? <ul>{providerErrors.map((item) => <li key={item.provider}><b>{item.provider}</b>: {item.error}</li>)}</ul> : null}<p>Local corrections remain locked and are never replaced by provider refreshes.</p></div></details></div><button onClick={onSync} disabled={syncing}>{syncing ? <SpinnerGap size={17} /> : <ArrowsClockwise size={17} />} {syncing ? "Checking…" : "Refresh details"}</button>{error || catalog.error ? <p>{error || catalog.error}</p> : null}</section>;
  }
  if (catalog.syncReady) return <section className="issue-catalog-card ready"><div><strong>Complete issue list is ready to load</strong><small>A verified single issue has confirmed the exact GCD series run. Loading it will add the complete issue numbering and improve ownership totals.</small></div><button onClick={onSync} disabled={syncing}>{syncing ? <SpinnerGap size={17} /> : <CloudArrowDown size={17} />} Load issue list</button>{error || catalog.error ? <p>{error || catalog.error}</p> : null}</section>;
  if (editionCount > 0 && directFiles === 0) return <section className="issue-catalog-card pending"><div><strong>Find the full series</strong><small>We can see {collectionIssueCount} issue{collectionIssueCount === 1 ? "" : "s"} covered by your volumes. Comic Library can find the complete issue list, related miniseries, and specials automatically.</small><em>You choose how to acquire gaps; provider-run maintenance stays automatic.</em></div><button onClick={() => onFindRun(series)}><MagnifyingGlass size={17} /> Find full series</button></section>;
  if (directFiles > 0) return <section className="issue-catalog-card pending"><div><strong>Confirm one issue match to load the full series</strong><small>Your issue files are in the library, but none is linked to a verified GCD issue yet. Review one file and use Fix Match to choose the correct result.</small></div><button onClick={onReviewFiles}><Eye size={17} /> Review issue files</button></section>;
  return <section className="issue-catalog-card pending"><div><strong>Full issue list not available</strong><small>No verified issue-run evidence is available yet. Your existing library records are still safe and usable.</small><em>No action is required.</em></div></section>;
}

function CollectionManagement({ series, families, allSeries, onCreateFamily, onSetFamily }) {
  const [name, setName] = useState("");
  const [targetFamily, setTargetFamily] = useState("");
  const [candidateRun, setCandidateRun] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const family = families.find((item) => item.id === series.family?.id);
  const familyRunIds = new Set(family?.runIds || []);
  const otherFamilies = families.filter((item) => item.id !== family?.id);
  const availableRuns = allSeries.filter((item) => !familyRunIds.has(item.id));
  async function create(event) {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true); setError("");
    const result = await onCreateFamily(name.trim(), [series.id]);
    if (result.ok) setName(""); else setError(result.error);
    setBusy(false);
  }
  async function assign(runId, familyId) {
    setBusy(true); setError("");
    const result = await onSetFamily(runId, familyId || null);
    if (!result.ok) setError(result.error);
    setBusy(false);
  }
  return <div className="family-management">
    <section className={`current-family ${family ? "linked" : ""}`}><Books size={24} weight="duotone" /><div><span>{family ? "Collection" : "Independent publication run"}</span><strong>{family?.name || series.title}</strong><small>{family ? `${family.runCount} runs are grouped in this collection. Each run keeps its own numbering and metadata.` : "This run is currently shown independently. It can be added to a collection without merging its identity."}</small></div></section>
    {family ? <section className="family-members"><header><strong>Publication runs</strong><small>Remove any run to show it independently again.</small></header>{family.runs.map((run) => <article key={run.id}><div><strong>{run.title}</strong><small>{run.year} · {run.total} issue{run.total === 1 ? "" : "s"}</small></div><button disabled={busy} onClick={() => assign(run.id, null)}>Remove</button></article>)}</section> : null}
    {family && availableRuns.length ? <section className="family-assignment"><h3>Add or move a run into this collection</h3><div><select value={candidateRun} onChange={(event) => setCandidateRun(event.target.value)}><option value="">Choose a run…</option>{availableRuns.map((run) => <option value={run.id} key={run.id}>{run.title} ({run.year}){run.family ? ` · from ${run.family.name}` : ""}</option>)}</select><button disabled={busy || !candidateRun} onClick={() => assign(candidateRun, family.id)}><Plus size={17} /> Add run</button></div><small>Moving a run changes only its collection membership.</small></section> : null}
    {otherFamilies.length ? <section className="family-assignment"><h3>{family ? "Move this run to another collection" : "Add this run to an existing collection"}</h3><div><select value={targetFamily} onChange={(event) => setTargetFamily(event.target.value)}><option value="">Choose a collection…</option>{otherFamilies.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.runCount} run{item.runCount === 1 ? "" : "s"}</option>)}</select><button disabled={busy || !targetFamily} onClick={() => assign(series.id, targetFamily)}>{family ? "Move run" : "Join collection"}</button></div></section> : null}
    <form className="family-create-form" onSubmit={create}><h3>Create a new collection from this run</h3><p>The current run will move into the new collection. Files, issues, volumes, aliases, and provider matches stay on the run.</p><div><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Collection name…" /><button disabled={busy || !name.trim()}><Plus size={17} /> Create collection</button></div></form>
    {error ? <p className="workbench-error">{error}</p> : null}
  </div>;
}

function SeriesDrawer({ series, families, allSeries, parentCollection, onBack, onClose, onRequest, onAddAlias, onSyncIssues, onFindRun, onCreateFamily, onSetFamily, onOpenWorkbench, onOpenCover, onOpenContents, onChangeRun, onEditIssue, onReplace }) {
  const [alias, setAlias] = useState("");
  const [savingAlias, setSavingAlias] = useState(false);
  const [aliasError, setAliasError] = useState("");
  const [syncingIssues, setSyncingIssues] = useState(false);
  const [syncError, setSyncError] = useState("");
  const [lastSyncResult, setLastSyncResult] = useState(null);
  const autoRepairKey = useRef(null);
  const [tab, setTab] = useState("overview");
  const issueCatalog = series?.issueCatalog || { status: "unknown" };
  const catalogKnown = ["complete", "complete_to_date"].includes(issueCatalog.status);
  const repairableIssueDetails = (series?.issues || []).filter((issue) => (
    (issueCatalog.titlePolicy !== "numbered_only" && !String(issue.title || "").trim() && issue.releaseState !== "upcoming")
    || issue.publicationYear == null
  )).length;
  const lastCheckedAge = issueCatalog.lastSyncedAt ? Date.now() - Date.parse(issueCatalog.lastSyncedAt) : Infinity;
  const currentRepairKey = `${series?.id || "none"}:${issueCatalog.lastSyncedAt || "never"}`;
  useEffect(() => {
    if (!series || !catalogKnown || !repairableIssueDetails || lastCheckedAge < 6 * 60 * 60 * 1000 || autoRepairKey.current === currentRepairKey) return;
    autoRepairKey.current = currentRepairKey;
    syncIssues(true);
  }, [series?.id, catalogKnown, repairableIssueDetails, currentRepairKey]);
  if (!series) return null;
  const identityStrength = series.identityConfidence == null ? "Unknown" : series.identityConfidence >= 85 ? "High" : series.identityConfidence >= 65 ? "Medium" : "Low";
  async function saveAlias(event) {
    event.preventDefault();
    if (!alias.trim()) return;
    setSavingAlias(true);
    setAliasError("");
    const result = await onAddAlias(series.id, alias.trim());
    if (result.ok) setAlias("");
    else setAliasError(result.error);
    setSavingAlias(false);
  }
  async function syncIssues(automatic = false) {
    setSyncingIssues(true);
    setSyncError("");
    const result = await onSyncIssues(series.id, automatic);
    if (!result.ok) setSyncError(result.error);
    else setLastSyncResult(result.result);
    setSyncingIssues(false);
  }
  const groupedIssues = (series.issues || []).map((issue) => ({
    ...issue,
    contextRunId: series.id,
    contextLabel: series.title,
    contextYear: series.year,
    contextGroupName: series.title,
    contextType: "main",
  }));
  return <div className="drawer-backdrop" onMouseDown={onClose}><aside className="series-drawer" onMouseDown={(event) => event.stopPropagation()}>{parentCollection ? <button className="drawer-back-link" onClick={onBack}><ArrowLeft size={17} /><span>Back to <strong>{parentCollection.name}</strong></span></button> : null}<button className="modal-close" onClick={onClose} aria-label="Close series details"><X size={20} /></button><div className="drawer-identity"><div className="drawer-cover"><SeriesCover series={series} /></div><div><h2>{series.title} <em>({series.year})</em></h2><p>{series.publisher}</p><div className="drawer-statuses"><PublicationStatus series={series} />{series.family ? <button className="family-link-chip" onClick={() => setTab("family")}><Books size={14} /> {series.family.name}</button> : null}</div></div></div><div className="drawer-facts"><span><strong>{series.fileDetails?.length ?? series.owned}</strong>Comic files</span><span><strong>{series.inventory?.directIssueFiles ?? 0}</strong>Single issues</span><span><strong>{series.inventory?.editionCount ?? series.editions?.length ?? 0}</strong>Volumes</span><span><strong>{identityStrength}</strong>Match confidence</span></div><nav className="drawer-tabs" aria-label="Series details">{[["overview", "Overview"], ["issues", `Issues (${series.issues?.length ?? 0})`], ["editions", `Volumes (${series.editions?.length ?? 0})`], ["files", `Files (${series.fileDetails?.length ?? 0})`], ["aliases", "Aliases"], ["family", "Collection"]].map(([id, label]) => <button className={tab === id ? "active" : ""} onClick={() => setTab(id)} key={id}>{label}</button>)}</nav><div className="drawer-tab-content">{tab === "overview" ? <><h3>Your collection</h3><Ownership series={series} /><section className="coverage-overview"><span><strong>{series.issues?.filter((issue) => issue.directOwned).length ?? 0}</strong>Single issues owned</span><span><strong>{series.issues?.filter((issue) => issue.collectionOwned).length ?? 0}</strong>Issues in volumes</span><span><strong>{series.editions?.reduce((count, edition) => count + (edition.coverageGroups?.filter((claim) => !claim.resolved).length ?? 0), 0)}</strong>Volume contents to verify</span></section><IssueCatalogCard series={series} catalogKnown={catalogKnown} syncing={syncingIssues} error={syncError} lastResult={lastSyncResult} onSync={syncIssues} onReviewFiles={() => setTab("files")} onFindRun={onFindRun} /></> : null}{tab === "issues" ? <GroupedIssueInventory issues={groupedIssues} onEditIssue={onEditIssue} /> : null}{tab === "editions" ? <VolumeInventory editions={series.editions} /> : null}{tab === "files" ? <FileInventory files={series.fileDetails} onOpenWorkbench={onOpenWorkbench} onOpenCover={onOpenCover} onOpenContents={onOpenContents} onChangeRun={onChangeRun} onReplace={onReplace} /> : null}{tab === "aliases" ? <><div className="alias-list">{series.aliases?.length ? series.aliases.map((item) => <span className={item.confirmed ? "confirmed" : ""} key={`${item.name}-${item.source}`}><strong>{item.name}</strong><small>{item.confirmed ? "Manually confirmed" : item.source}</small></span>) : <p>No alternate titles recorded.</p>}</div><form className="alias-form" onSubmit={saveAlias}><label><span>Add a title alias</span><div><input value={alias} onChange={(event) => setAlias(event.target.value)} placeholder="Alternate series title…" /><button disabled={savingAlias || !alias.trim()}>{savingAlias ? <SpinnerGap size={18} /> : <Plus size={18} />} Add</button></div></label>{aliasError ? <small className="form-error">{aliasError}</small> : <small>Confirmed aliases are used during future scans and searches.</small>}</form></> : null}{tab === "family" ? <CollectionManagement series={series} families={families} allSeries={allSeries} onCreateFamily={onCreateFamily} onSetFamily={onSetFamily} /> : null}</div><div className="drawer-actions"><button className="primary-button" onClick={onRequest}><ChatCircle size={19} /> Request missing</button><button className="ghost-button" onClick={() => setTab("files")}><Eye size={18} /> View files</button></div></aside></div>;
}

function StoryArcList({ arcs, emptyTitle, onOpenSeries }) {
  if (!arcs.length) return <div className="drawer-empty"><Books size={27} weight="duotone" /><strong>{emptyTitle}</strong><span>Runs are discovered automatically. Manual grouping is available under Advanced tools.</span></div>;
  return <div className="story-arc-list">{arcs.map((arc) => <article key={arc.id}><header><div><span>{arc.type === "specials" ? "Specials / one-shots" : "Series run"}</span><strong>{arc.name}</strong></div><b className={arc.status}>{arc.status === "complete" ? "Complete" : arc.status === "partial" ? "Partially owned" : arc.status === "cataloged" ? "Not owned" : "Missing"}</b></header><div className="arc-progress-copy"><strong>{arc.ownedIssueCount} of {arc.issueCount || "unknown"} issues owned</strong><span>{arc.volumeCount} volume{arc.volumeCount === 1 ? "" : "s"} · {arc.fileCount} file{arc.fileCount === 1 ? "" : "s"}</span></div><div className="arc-run-links">{arc.runs.map((run) => <button onClick={() => onOpenSeries(run)} key={run.id}><span>{run.title} ({run.year})</span><ArrowRight size={15} /></button>)}</div></article>)}</div>;
}

function CollectionDrawer({ collection, tab, onTabChange, onClose, onFindStructure, onOpenSeries, onReviewPlacement, onRequest, onEditIssue }) {
  if (!collection) return null;
  const arcs = collection.storyArcs || [];
  const files = collection.runs.flatMap((run) => (run.fileDetails || []).map((file) => ({ ...file, run })));
  const arcByRunId = new Map(arcs.flatMap((arc) => (arc.runIds || []).map((runId) => [String(runId), arc])));
  const volumes = collection.runs.flatMap((run) => (run.editions || []).map((volume) => {
    const storyGroup = arcByRunId.get(String(run.id));
    const hasResolvedCoverage = (volume.coverageGroups || []).some((group) => group.resolved);
    const linkedFile = files.find((file) => (volume.files || []).includes(file.filename));
    return {
      ...volume,
      run,
      linkedFile,
      storyGroup,
      placementStatus: hasResolvedCoverage || storyGroup?.type === "specials" ? "placed" : "unplaced",
    };
  }));
  const unplacedVolumes = volumes.filter((volume) => volume.placementStatus === "unplaced");
  const unplacedFileNames = new Set(unplacedVolumes.flatMap((volume) => volume.files || []));
  const displayArcs = arcs.map((arc) => {
    const runIds = new Set((arc.runIds || []).map(String));
    const placedVolumes = volumes.filter((volume) => runIds.has(String(volume.run.id)) && volume.placementStatus === "placed");
    const linkedFileNames = new Set(placedVolumes.flatMap((volume) => volume.files || []));
    const directFiles = files.filter((file) => runIds.has(String(file.run.id)) && file.identityKind === "issue");
    return { ...arc, volumeCount: placedVolumes.length, fileCount: linkedFileNames.size + directFiles.length };
  });
  const mainArcs = displayArcs.filter((arc) => arc.type === "main");
  const specials = displayArcs.filter((arc) => arc.type === "specials");
  const issues = collection.runs.flatMap((run) => {
    const storyGroup = arcByRunId.get(String(run.id));
    return (run.issues || []).map((issue) => ({
      ...issue,
      contextRunId: run.id,
      contextLabel: run.title,
      contextYear: run.year,
      contextGroupName: storyGroup?.name || run.title,
      contextType: storyGroup?.type || "main",
    }));
  });
  const ownedIssueCount = issues.filter((issue) => issue.ownership !== "unowned").length;
  const missingIssueCount = Math.max(0, issues.length - ownedIssueCount);
  const coverage = {
    ...collection,
    owned: ownedIssueCount,
    total: issues.length,
    unowned: missingIssueCount,
    catalogKnown: issues.length > 0,
    status: issues.length ? (missingIssueCount ? "partial" : "complete") : collection.status,
    ownership: issues.length ? (missingIssueCount ? `${missingIssueCount} issues missing` : "All issues owned") : collection.ownership,
  };
  const display = { ...collection, id: `collection-${collection.id}`, title: collection.name };
  const preferenceLabel = ACQUISITION_LABELS[collection.acquisitionPreference] || ACQUISITION_LABELS.either;
  return <div className="drawer-backdrop" onMouseDown={onClose}>
    <aside className="series-drawer collection-drawer" onMouseDown={(event) => event.stopPropagation()}>
      <button className="modal-close" onClick={onClose} aria-label="Close collection details"><X size={20} /></button>
      <div className="drawer-identity"><div className="drawer-cover"><SeriesCover series={display} /></div><div><span className={`status-chip ${collection.structureStatus === "unmapped" || unplacedVolumes.length ? "amber" : "green"}`}>{unplacedVolumes.length ? `${unplacedVolumes.length} metadata fix${unplacedVolumes.length === 1 ? "" : "es"} needed` : collection.monitoringStatus === "monitored" ? "Following" : "In your library"}</span><h2>{collection.name}</h2><p>{collection.publisher} · {collection.year} · {mainArcs.length} runs{specials.length ? ` + ${specials.length} specials` : ""}</p></div></div>
      <div className="drawer-facts collection-coverage-facts"><span><strong>{ownedIssueCount} <em>of {issues.length}</em></strong>Issues owned</span><span><strong>{missingIssueCount}</strong>Issues missing</span><span><strong>{volumes.length}</strong>Volumes owned</span><span><strong>{files.length}</strong>Comic files</span></div>
      <nav className="drawer-tabs" aria-label="Collection details">{[["overview", "Overview"], ["issues", `Issues (${issues.length})`], ["volumes", `Volumes (${volumes.length})`], ["specials", `Specials (${specials.length})`], ["arcs", `Runs (${mainArcs.length})`], ["files", `Files (${files.length})`]].map(([id, label]) => <button className={tab === id ? "active" : ""} onClick={() => onTabChange(id)} key={id}>{label}</button>)}</nav>
      <div className="drawer-tab-content">
        {tab === "overview" ? <><h3>Your collection</h3><Ownership series={coverage} /><section className="monitoring-summary"><div><strong>{preferenceLabel}</strong><small>{collection.includeSpecials === false ? "Main series only" : "Main series + specials"}</small></div><span><CheckCircle size={18} weight="fill" /> Series connections updated automatically</span></section>{unplacedVolumes.length ? <section className="placement-callout"><WarningCircle size={21} weight="fill" /><div><strong>{unplacedVolumes.length} metadata fix{unplacedVolumes.length === 1 ? "" : "es"} needed</strong><small>Your series stays followed. Review only if automatic matching cannot place these volumes.</small></div><button onClick={() => onTabChange("volumes")}>Review</button></section> : null}{collection.structureStatus === "unmapped" ? <section className="structure-callout"><div><strong>Complete-series details are still being found</strong><small>Comic Library will keep your current files safe while it looks for a confident series structure.</small></div><button onClick={() => onFindStructure(collection)}><MagnifyingGlass size={17} /> Review details</button></section> : null}<details className="advanced-collection-tools"><summary><Gear size={16} /> Advanced tools</summary><p>Inspect provider runs, split or combine groups, and correct unusual series structures.</p><button onClick={() => onFindStructure(collection)}><PencilSimple size={16} /> Review series structure</button></details></> : null}
        {tab === "issues" ? <GroupedIssueInventory issues={issues} onEditIssue={onEditIssue} /> : null}
        {tab === "arcs" ? <StoryArcList arcs={mainArcs} emptyTitle="No runs found" onOpenSeries={onOpenSeries} /> : null}
        {tab === "specials" ? <StoryArcList arcs={specials} emptyTitle="No specials found" onOpenSeries={onOpenSeries} /> : null}
        {tab === "volumes" ? <div className="collection-volume-list">{unplacedVolumes.length ? <div className="placement-list-note"><WarningCircle size={18} weight="fill" /><span><strong>Automatic matching needs help</strong><small>These volumes remain safely cataloged. Open one only if you want to correct its issue coverage or grouping.</small></span></div> : null}{volumes.length ? volumes.map((volume) => <button className={volume.placementStatus} onClick={() => volume.placementStatus === "unplaced" ? onReviewPlacement({ collection, volume, file: volume.linkedFile }) : onOpenSeries(volume.run)} key={`${volume.run.id}-${volume.id}`}><Books size={21} weight="duotone" /><span><strong>{volume.subtitle ? `${volume.title}: ${volume.subtitle}` : volume.title}</strong><small>{volume.run.title} · {editionKindLabel(volume.editionKind)}{volume.volume ? ` · Vol. ${volume.volume}` : ""}</small>{volume.placementStatus === "unplaced" ? <b>Needs automatic match review</b> : null}</span><ArrowRight size={16} /></button>) : <div className="drawer-empty"><Books size={27} /><strong>No volumes cataloged</strong></div>}</div> : null}
        {tab === "files" ? <div className="collection-volume-list">{files.map((file) => { const unplacedVolume = unplacedVolumes.find((volume) => (volume.files || []).includes(file.filename)); return <button className={unplacedVolume ? "unplaced" : ""} onClick={() => unplacedVolume ? onReviewPlacement({ collection, volume: unplacedVolume, file }) : onOpenSeries(file.run)} key={file.id}><HardDrive size={20} weight="duotone" /><span><strong>{file.filename}</strong><small>{file.run.title} · {file.identityKind === "issue" ? "Single issue" : editionKindLabel(file.editionKind)}{unplacedFileNames.has(file.filename) ? " · Match review" : ""}</small></span><ArrowRight size={16} /></button>; })}</div> : null}
      </div>
      <div className="drawer-actions"><button className="primary-button" onClick={onRequest}><ChatCircle size={18} /> Request missing</button><button className="ghost-button" onClick={() => onTabChange("files")}><Eye size={18} /> View files</button></div>
    </aside>
  </div>;
}

function VolumePlacementWorkbench({ data, busy, error, onClose, onSave }) {
  const defaultName = data.volume.title.replace(new RegExp(`^${data.collection.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*[:–-]?\\s*`, "i"), "").trim() || data.volume.title;
  const [type, setType] = useState("specials");
  const [name, setName] = useState(defaultName);
  function submit(event) { event.preventDefault(); onSave({ type, name: name.trim() }); }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal placement-workbench" role="dialog" aria-modal="true" aria-labelledby="placement-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close volume placement"><X size={20} /></button><span className="eyebrow">Fix volume placement</span><h2 id="placement-title">{data.volume.title}</h2><p className="workbench-intro">This volume is cataloged inside {data.collection.name}, but it has no verified issue contents and is currently attached to a broader run. Give it an independent structural home without changing the comic file.</p><form className="placement-form" onSubmit={submit}><label className="form-field"><span>Placement type</span><select value={type} onChange={(event) => setType(event.target.value)}><option value="specials">Special / one-shot group</option><option value="main">Story arc</option></select></label><label className="form-field"><span>Group name</span><input value={name} onChange={(event) => setName(event.target.value)} required /></label><section className="placement-safety"><ShieldCheck size={20} weight="fill" /><span><strong>Catalog-only correction</strong><small>This creates an independent publication run for the volume and places it in the selected group. Its issue contents will remain marked unknown until separately verified.</small></span></section>{error ? <p className="workbench-error">{error}</p> : null}<div className="metadata-edit-actions"><button type="button" className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || !name.trim()}>{busy ? <SpinnerGap size={18} /> : <ArrowRight size={18} />} Save placement</button></div></form></section></div>;
}

function StoryStructureWorkbench({ data, busy, error, onClose, onSave }) {
  const [arcs, setArcs] = useState(() => (data.arcs || []).map((arc) => ({ ...arc, runIds: [...arc.runIds], runLabels: [...arc.runLabels] })));
  function update(index, fields) { setArcs((items) => items.map((arc, position) => position === index ? { ...arc, ...fields } : arc)); }
  function move(index, direction) { setArcs((items) => { const target = index + direction; if (target < 0 || target >= items.length) return items; const next = [...items]; [next[index], next[target]] = [next[target], next[index]]; return next; }); }
  function addGroup() { setArcs((items) => [...items, { id: null, name: "New story group", type: "main", runIds: [], runLabels: [], issueCount: 0, volumeCount: 0, confidence: "manual", reason: "Created for manual organization" }]); }
  function moveRun(fromIndex, runIndex, toIndex) { setArcs((items) => { const next = items.map((arc) => ({ ...arc, runIds: [...arc.runIds], runLabels: [...arc.runLabels] })); const [runId] = next[fromIndex].runIds.splice(runIndex, 1); const [runLabel] = next[fromIndex].runLabels.splice(runIndex, 1); next[toIndex].runIds.push(runId); next[toIndex].runLabels.push(runLabel); return next; }); }
  function submit(event) { event.preventDefault(); onSave(arcs.filter((arc) => arc.runIds.length).map((arc) => ({ name: arc.name.trim(), type: arc.type, runIds: arc.runIds }))); }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal structure-workbench" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close series structure"><X size={20} /></button><span className="eyebrow">Find series structure</span><h2>{data.collection.name}</h2><p className="workbench-intro">Review the proposed hierarchy before saving. Runs can be grouped into one story arc or split into separate arcs and specials; files and metadata are not changed.</p><form onSubmit={submit}><div className="structure-groups">{arcs.map((arc, index) => <article className={!arc.runIds.length ? "empty" : ""} key={`${arc.id || "new"}-${index}`}><header><span>{index + 1}</span><input value={arc.name} onChange={(event) => update(index, { name: event.target.value })} aria-label={`Story group ${index + 1} name`} /><select value={arc.type} onChange={(event) => update(index, { type: event.target.value })}><option value="main">Story arc</option><option value="specials">Specials / one-shots</option></select><button type="button" onClick={() => move(index, -1)} disabled={index === 0} aria-label="Move group up">↑</button><button type="button" onClick={() => move(index, 1)} disabled={index === arcs.length - 1} aria-label="Move group down">↓</button></header><small>{arc.reason} · {arc.confidence} confidence</small><div className="structure-runs">{arc.runIds.length ? arc.runIds.map((runId, runIndex) => <div key={runId}><span>{arc.runLabels[runIndex]}</span>{arcs.length > 1 ? <select value={index} onChange={(event) => moveRun(index, runIndex, Number(event.target.value))} aria-label={`Move ${arc.runLabels[runIndex]} to another group`}>{arcs.map((target, targetIndex) => <option value={targetIndex} key={targetIndex}>Move to {target.name || `group ${targetIndex + 1}`}</option>)}</select> : null}</div>) : <em>Empty group — move a run here or it will not be saved.</em>}</div></article>)}</div><button type="button" className="ghost-button add-structure-group" onClick={addGroup}><Plus size={17} /> Add story group</button>{error ? <p className="workbench-error">{error}</p> : null}<div className="metadata-edit-actions"><button type="button" className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || !arcs.some((arc) => arc.runIds.length)}>{busy ? <SpinnerGap size={18} /> : <ShieldCheck size={18} />} Save series structure</button></div></form></section></div>;
}

function candidateValue(value) {
  if (value === null || value === undefined || value === "") return "Not set";
  if (value === "issue") return "Single issue";
  if (value === "edition") return "Volume";
  return String(value);
}

function MatchCandidateCard({ candidate, current, selectedKey, busy, onMatch }) {
  const preview = candidate.selectionPreview || { changes: [], associations: [], fields: {} };
  const isSelected = selectedKey === candidate.key || (!selectedKey && preview.changes.length === 0);
  const edition = candidate.matched_edition || {};
  const creators = candidate.creators || [];
  const rank = candidate.identity_confidence?.score ?? candidate.match_score;
  const facts = [
    ["Record", candidateValue(preview.fields.recordType)],
    ["Publisher", candidate.publisher || "Not supplied"],
    ["Year", candidate.publication_year || candidate.published_date || "Not supplied"],
    ["Format", candidate.format || "Not supplied"],
    ["ISBN", (candidate.isbns || []).join(", ") || "Not supplied"],
    ["Creators", creators.join(", ") || "Not supplied"],
    ["Pages", edition.number_of_pages || candidate.page_count || "Not supplied"],
    ["Cover", preview.coverEffect],
  ];
  return <article className={`match-candidate-card ${isSelected ? "selected" : ""}`}><div className="candidate-cover">{candidate.cover ? <img src={candidate.cover} alt={`${candidate.title || "Candidate"} cover`} /> : <span><Books size={28} weight="duotone" />No cover</span>}</div><div className="candidate-body"><header><div><span>{candidate.source || "Catalog candidate"}</span><h3>{candidate.title || "Untitled candidate"}</h3>{candidate.subtitle ? <strong>{candidate.subtitle}</strong> : null}</div><div className="candidate-badges">{isSelected ? <b className="selected-badge"><CheckCircle size={14} weight="fill" /> Current</b> : null}{rank != null ? <b>{candidate.identity_confidence ? `Identity ${rank}%` : `Rank ${rank}`}</b> : null}{candidate.cover ? <b>Cover</b> : null}</div></header>{candidate.verification_status ? <p className="candidate-verification">{volumeTerminology(candidate.verification_status)}</p> : null}<dl className="candidate-facts">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><section className="candidate-associations"><h4>Issue association</h4>{preview.associations.length ? preview.associations.map((association, index) => <div key={`${association.series}-${association.issueLabel}-${index}`}><strong>{association.series} #{association.issueLabel}</strong><small>{association.source || "Catalog evidence"} · {association.confidence}</small></div>) : <p>No issue or volume coverage association supplied by this match.</p>}</section><section className="candidate-impact"><h4>What selecting this will change</h4>{preview.changes.length ? <div>{preview.changes.map((change) => <span key={change.field}><b>{change.label}</b><del>{candidateValue(change.from)}</del><ArrowRight size={13} /><ins>{candidateValue(change.to)}</ins></span>)}</div> : <p>This candidate matches the currently applied fields.</p>}<small>{candidate.cover ? "Its provider image will also become available in the Cover picker; automatic cover priority will not be changed." : "No provider cover will be added by this candidate."}</small></section>{candidate.match_reasons?.length || candidate.description ? <details className="candidate-evidence"><summary>Evidence and description</summary>{candidate.match_reasons?.length ? <ul>{candidate.match_reasons.map((reason) => <li key={reason}>{volumeTerminology(reason)}</li>)}</ul> : null}{candidate.description ? <p>{candidate.description}</p> : null}</details> : null}<button className="candidate-select" disabled={busy || isSelected} onClick={() => onMatch(candidate.key)}>{isSelected ? "Currently selected" : `Use this match · ${preview.changes.length} change${preview.changes.length === 1 ? "" : "s"}`}</button></div></article>;
}

function MetadataWorkbench({ data, mode, busy, error, onClose, onSave, onMatch, onReset }) {
  const [fields, setFields] = useState(() => ({ ...data.current, ...data.override }));
  const set = (key) => (event) => setFields({ ...fields, [key]: event.target.value });
  const isIssue = fields.recordType === "issue";
  function submit(event) {
    event.preventDefault();
    const lockedFields = Object.keys(fields).filter((key) => fields[key] !== "" && fields[key] != null);
    onSave(fields, lockedFields);
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className={`modal metadata-workbench ${mode === "match" ? "match-workbench" : ""}`} onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close metadata workbench"><X size={20} /></button><span className="eyebrow">{mode === "match" ? "Fix match" : "Edit metadata"}</span><h2>{data.file.filename}</h2>{mode === "match" ? <><p className="workbench-intro">Compare the complete metadata and ownership impact before selecting a match. Source evidence remains available after the change.</p><div className="match-candidates">{data.candidates.length ? data.candidates.map((candidate) => <MatchCandidateCard candidate={candidate} current={data.current} selectedKey={data.selectedCandidateKey} busy={busy} onMatch={onMatch} key={candidate.key} />) : <div className="drawer-empty"><MagnifyingGlass size={26} /><strong>No alternate candidates were retained</strong><span>Edit the metadata manually, or rescan after adding another provider.</span></div>}</div></> : <form className="metadata-edit-form" onSubmit={submit}><p className="workbench-intro">Saved values are stored locally and locked against future provider refreshes. The comic file itself is not modified.</p><label><span>Series</span><input value={fields.seriesTitle || ""} onChange={set("seriesTitle")} required /></label><label><span>Display title</span><input value={fields.title || ""} onChange={set("title")} required /></label><label><span>Subtitle</span><input value={fields.subtitle || ""} onChange={set("subtitle")} /></label><label><span>Record type</span><select value={fields.recordType || "edition"} onChange={set("recordType")}><option value="issue">Single issue</option><option value="edition">Volume</option></select></label>{isIssue ? <label><span>Issue number</span><input value={fields.issueNumber || ""} onChange={set("issueNumber")} /></label> : <><label><span>Volume number</span><input type="number" min="0" value={fields.volumeNumber ?? ""} onChange={set("volumeNumber")} /></label><label><span>Volume type</span><select value={fields.editionKind || "edition"} onChange={set("editionKind")}>{Object.entries(EDITION_KIND_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label></>}<label><span>Publisher</span><input value={fields.publisher || ""} onChange={set("publisher")} /></label><label><span>Publication year</span><input type="number" min="1800" max="2200" value={fields.publicationYear ?? ""} onChange={set("publicationYear")} /></label><label><span>ISBN / GTIN</span><input value={fields.isbn || ""} onChange={set("isbn")} /></label><label><span>Format</span><input value={fields.format || ""} onChange={set("format")} /></label><div className="metadata-edit-actions">{Object.keys(data.override).length ? <button type="button" className="danger-button" onClick={onReset} disabled={busy}>Restore provider metadata</button> : <span />}<button className="primary-button" disabled={busy}>{busy ? <SpinnerGap size={18} /> : <ShieldCheck size={18} />} Save and lock</button></div></form>}{error ? <p className="workbench-error">{error}</p> : null}</section></div>;
}

function IssueMetadataWorkbench({ issue, busy, error, onClose, onSave, onReset }) {
  const [title, setTitle] = useState(issue.title || "");
  const [publicationYear, setPublicationYear] = useState(issue.publicationYear || "");
  const providerTitle = issue.providerTitle || "Not supplied by provider";
  const providerYear = issue.providerPublicationYear || "Not supplied by provider";
  function submit(event) {
    event.preventDefault();
    onSave({ title: title.trim(), publicationYear: publicationYear || null });
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal issue-metadata-workbench" role="dialog" aria-modal="true" aria-labelledby="issue-metadata-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close issue metadata editor"><X size={20} /></button><span className="eyebrow">Edit issue metadata</span><h2 id="issue-metadata-title">{issue.contextLabel || "Issue"} #{issue.number}</h2><p className="workbench-intro">Correct the catalog after ingestion without modifying the comic file. Saved values remain locked when provider metadata is refreshed.</p><form className="issue-metadata-form" onSubmit={submit}><label className="form-field"><span>Issue title</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder={`Issue ${issue.number} title…`} /></label><label className="form-field"><span>Publication year</span><input type="number" min="1800" max="2200" value={publicationYear} onChange={(event) => setPublicationYear(event.target.value)} /></label><section className="provider-issue-evidence"><header><Database size={18} /><span><strong>Provider metadata retained</strong><small>You can restore these values at any time.</small></span></header><dl><div><dt>Title</dt><dd>{providerTitle}</dd></div><div><dt>Year</dt><dd>{providerYear}</dd></div></dl></section>{issue.metadataLocked ? <p className="issue-lock-note"><ShieldCheck size={17} weight="fill" /> A local correction is currently locked for this issue.</p> : null}{error ? <p className="workbench-error">{error}</p> : null}<div className="metadata-edit-actions">{issue.metadataLocked ? <button type="button" className="danger-button" onClick={onReset} disabled={busy}>Restore provider metadata</button> : <button type="button" className="ghost-button" onClick={onClose}>Cancel</button>}<button className="primary-button" disabled={busy || (!title.trim() && !publicationYear)}>{busy ? <SpinnerGap size={18} /> : <ShieldCheck size={18} />} Save and lock</button></div></form></section></div>;
}

function CoverWorkbench({ data, busy, error, onClose, onSelect, onUpload }) {
  const selectedSource = data.covers.selectedSource;
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal cover-workbench" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close cover picker"><X size={20} /></button><span className="eyebrow">Change cover</span><h2>{data.file.filename}</h2><p className="workbench-intro">Choose art from the comic, a metadata provider, or upload your own image. This does not alter the original comic file.</p><div className="cover-option-grid">{data.covers.options.map((option) => <article className={selectedSource === option.source && (!data.covers.selectedUrl || data.covers.selectedUrl === option.url) ? "selected" : ""} key={`${option.source}-${option.url}`}><img src={option.url} alt={option.label} /><div><strong>{option.label}</strong><small>{option.detail}</small><button disabled={busy} onClick={() => onSelect(option.source, option.url)}>{selectedSource === option.source && (!data.covers.selectedUrl || data.covers.selectedUrl === option.url) ? "Selected" : "Use cover"}</button></div></article>)}</div><div className="cover-picker-actions"><button className="ghost-button" disabled={busy} onClick={() => onSelect("auto", null)}><ArrowsClockwise size={17} /> Use automatic cover</button><label className="primary-button upload-cover-button"><UploadSimple size={18} /> Upload image<input type="file" accept="image/jpeg,image/png,image/webp,image/gif,image/heic,image/heif" disabled={busy} onChange={(event) => event.target.files?.[0] && onUpload(event.target.files[0])} /></label></div>{error ? <p className="workbench-error">{error}</p> : null}</section></div>;
}

function VolumeContentsWorkbench({ data, busy, error, onClose, onChange, onReset }) {
  const contents = data.collectionContents;
  const [seriesId, setSeriesId] = useState(contents.seriesId);
  const [issueInput, setIssueInput] = useState("");
  const [note, setNote] = useState("");
  const [inputError, setInputError] = useState("");
  function addIssues(event) {
    event.preventDefault();
    try {
      const issueNumbers = parseIssueInput(issueInput);
      if (!issueNumbers.length) throw new Error("Enter at least one issue number");
      setInputError("");
      onChange({ seriesId, issueNumbers, included: true, note });
    } catch (parseError) { setInputError(parseError.message); }
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal contents-workbench" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close volume contents"><X size={20} /></button><span className="eyebrow">Volume contents</span><h2>{data.file.filename}</h2><p className="workbench-intro">Review what this volume contains. Provider evidence remains visible; local corrections control which canonical issues count as owned.</p><div className="contents-summary"><strong>{contents.includedCount}</strong><span>canonical issues currently included</span>{contents.hasOverrides ? <b><ShieldCheck size={14} weight="fill" /> Local corrections applied</b> : null}</div><div className="contents-list">{contents.items.length ? contents.items.map((item) => <article className={!item.included ? "excluded" : item.resolved ? "resolved" : "unresolved"} key={`${item.seriesId}-${item.seriesLabel}-${item.issueNumber}-${item.source}`}><div><strong>{item.seriesLabel} #{item.issueNumber}</strong><small>{item.source} · {item.confidence}{item.evidence ? ` · ${item.evidence}` : ""}</small></div><span>{!item.included ? "Excluded" : item.resolved ? "Counts as owned" : "Needs series match"}</span>{item.seriesId ? <button disabled={busy} onClick={() => onChange({ seriesId: item.seriesId, issueNumbers: [item.issueNumber], included: !item.included, note: item.overrideNote || "Adjusted in volume contents" })}>{item.included ? "Exclude" : "Restore"}</button> : null}</article>) : <div className="drawer-empty"><ListBullets size={26} weight="duotone" /><strong>No issue contents established</strong><span>Add the known issues below; they will be stored as a local correction.</span></div>}</div><form className="contents-add-form" onSubmit={addIssues}><h3>Add included issues</h3><label><span>Canonical series</span><select value={seriesId} onChange={(event) => setSeriesId(event.target.value)}>{data.seriesOptions.map((series) => <option value={series.id} key={series.id}>{series.title}{series.year ? ` (${series.year})` : ""}</option>)}</select></label><label><span>Issue numbers</span><input value={issueInput} onChange={(event) => setIssueInput(event.target.value)} placeholder="1-6, 8, Annual 1" /></label><label><span>Correction note</span><input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Publisher contents page, checked manually…" /></label><button className="primary-button" disabled={busy}><Plus size={18} /> Add issues</button></form>{contents.hasOverrides ? <button className="danger-button contents-reset" disabled={busy} onClick={onReset}>Restore provider contents</button> : null}{inputError || error ? <p className="workbench-error">{inputError || error}</p> : null}</section></div>;
}

function SeriesRunWorkbench({ data, loading, busy, error, onClose, onConfirm, onBuildCollection }) {
  const series = data?.series || {};
  const candidates = data?.candidates;
  const [selected, setSelected] = useState([]);
  const [collectionName, setCollectionName] = useState(series.title || "");
  const [preference, setPreference] = useState("volumes");
  const [includeSpecials, setIncludeSpecials] = useState(true);
  useEffect(() => {
    setCollectionName(series.title || "");
    setSelected((data?.candidates || []).map((candidate) => candidate.providerSeriesId));
    setPreference("volumes");
    setIncludeSpecials(true);
  }, [series.id, candidates?.length]);
  const specialIds = new Set((candidates || []).filter((candidate) => /one[- ]shot|special/i.test(candidate.publishingFormat || "")).map((candidate) => candidate.providerSeriesId));
  const automaticSelection = selected.filter((id) => includeSpecials || !specialIds.has(id));
  const selectedCandidates = (candidates || []).filter((candidate) => automaticSelection.includes(candidate.providerSeriesId));
  const issueCount = selectedCandidates.reduce((count, candidate) => count + candidate.issueCount, 0);
  const specialCount = selectedCandidates.filter((candidate) => specialIds.has(candidate.providerSeriesId)).length;
  const mainCount = selectedCandidates.length - specialCount;
  function toggle(providerSeriesId) {
    setSelected((items) => items.includes(providerSeriesId) ? items.filter((id) => id !== providerSeriesId) : [...items, providerSeriesId]);
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}>
    <section className="modal series-run-workbench simple-series-plan" onMouseDown={(event) => event.stopPropagation()}>
      <button className="modal-close" onClick={onClose} aria-label="Close full-series search"><X size={20} /></button>
      <span className="eyebrow">Find full series</span>
      <h2>{series.title}{series.year ? ` (${series.year})` : ""}</h2>
      <p className="workbench-intro">Add the whole series once. Comic Library will maintain its publication runs, issue coverage, volumes, and specials in the background.</p>
      {loading ? <div className="run-loading"><SpinnerGap size={28} /><strong>Finding the full series…</strong><span>Checking related runs, issue lists, and specials.</span></div> : null}
      {!loading && candidates?.length ? <>
        <section className="automatic-series-plan">
          <div className="plan-heading"><span className="plan-icon"><CheckCircle size={23} weight="fill" /></span><div><strong>Complete series found</strong><small>{mainCount} main publication run{mainCount === 1 ? "" : "s"}{specialCount ? ` + ${specialCount} special${specialCount === 1 ? "" : "s"}` : ""} · {issueCount} canonical issues</small></div></div>
          <div className="plan-outcomes"><span><CheckCircle size={16} /> One series in your library</span><span><CheckCircle size={16} /> Volumes count toward their underlying issues</span><span><CheckCircle size={16} /> New releases and gaps stay monitored</span></div>
        </section>
        <section className="acquisition-choice">
          <div><strong>How should missing comics be acquired?</strong><small>You can change this later. Comic Library still recognizes every format you already own.</small></div>
          <div className="preference-grid">{[
            ["volumes", "Volumes first", "Prefer trades, hardcovers, and omnibuses that cover several issues."],
            ["issues", "Single issues first", "Prefer the original individual issue releases."],
            ["either", "Best available", "Use the fewest available releases to fill each gap."],
          ].map(([value, label, detail]) => <button type="button" className={preference === value ? "selected" : ""} onClick={() => setPreference(value)} key={value}><span>{preference === value ? <CheckCircle size={18} weight="fill" /> : <BookOpen size={18} />}</span><strong>{label}</strong><small>{detail}</small></button>)}</div>
          <Toggle checked={includeSpecials} onChange={setIncludeSpecials} title="Include specials and crossovers" description="One-shots stay visible in Specials and count toward complete-series coverage." />
        </section>
        <div className="full-series-actions"><button className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || automaticSelection.length < 1 || !collectionName.trim()} onClick={() => onBuildCollection(automaticSelection, collectionName.trim(), { acquisitionPreference: preference, includeSpecials })}>{busy ? <SpinnerGap size={18} /> : <Plus size={18} />} Add &amp; monitor {issueCount} issues</button></div>
        <details className="advanced-run-details"><summary><span><Gear size={17} /> Advanced: review provider runs</span><small>Optional · automatic selection includes {automaticSelection.length} records</small></summary><div className="advanced-run-toolbar"><label><span>Series name</span><input value={collectionName} onChange={(event) => setCollectionName(event.target.value)} /></label><button type="button" onClick={() => setSelected(selected.length === candidates.length ? [] : candidates.map((candidate) => candidate.providerSeriesId))}>{selected.length === candidates.length ? "Clear all" : "Select all"}</button></div><div className="advanced-run-list">{candidates.map((candidate) => {
          const included = selected.includes(candidate.providerSeriesId) && (includeSpecials || !specialIds.has(candidate.providerSeriesId));
          return <article className={included ? "included" : ""} key={candidate.providerSeriesId}><label><input type="checkbox" checked={selected.includes(candidate.providerSeriesId)} onChange={() => toggle(candidate.providerSeriesId)} /><span><strong>{candidate.title}</strong><small>{candidate.yearLabel} · {candidate.publishingFormat || "Publication run"} · {candidate.issueCount} issues</small></span></label><div><a href={`https://www.comics.org/series/${candidate.providerSeriesId}/`} target="_blank" rel="noreferrer">Source</a><button disabled={busy} onClick={() => onConfirm(candidate.providerSeriesId)}>Use only this run</button></div></article>;
        })}</div></details>
      </> : null}
      {!loading && candidates && !candidates.length ? <div className="drawer-empty"><MagnifyingGlass size={28} /><strong>No confident full-series match found</strong><span>The library was not changed. This exception can be reviewed later in Metadata.</span></div> : null}
      {error ? <p className="workbench-error">{error}</p> : null}
    </section>
  </div>;
}

function FileRunWorkbench({ data, busy, error, onClose, onMove }) {
  const current = data.current || {};
  const existingRuns = (data.seriesOptions || []).filter((run) => run.title !== current.seriesTitle);
  const suggestedTitle = current.title && current.title !== current.seriesTitle ? current.title : "";
  const [mode, setMode] = useState("new");
  const [seriesId, setSeriesId] = useState(existingRuns[0]?.id || "");
  const [title, setTitle] = useState(suggestedTitle);
  const [year, setYear] = useState(current.publicationYear || "");
  const [publisher, setPublisher] = useState(current.publisher || "");
  function submit(event) {
    event.preventDefault();
    if (mode === "existing") onMove({ seriesId });
    else onMove({ title: title.trim(), year: year || null, publisher: publisher.trim() });
  }
  return <div className="modal-backdrop workbench-backdrop" onMouseDown={onClose}><section className="modal file-run-workbench" role="dialog" aria-modal="true" aria-labelledby="file-run-title" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose} aria-label="Close run assignment"><X size={20} /></button><span className="eyebrow">Change publication run</span><h2 id="file-run-title">{data.file.filename}</h2><p className="workbench-intro">This file is currently attached to <strong>{current.seriesTitle}</strong>. Changing its run updates only the catalog relationship—the comic file will not be renamed, moved, or modified.</p><form onSubmit={submit} className="run-assignment-form"><div className="run-assignment-choice"><button type="button" className={mode === "new" ? "active" : ""} onClick={() => setMode("new")}><Plus size={18} /><span><strong>Separate into a new run</strong><small>Use this when the file represents a distinct series, miniseries, or group of one-shots.</small></span></button><button type="button" className={mode === "existing" ? "active" : ""} onClick={() => setMode("existing")}><Books size={18} /><span><strong>Move to an existing run</strong><small>Attach this file to another canonical run already in the library.</small></span></button></div>{mode === "existing" ? <label className="form-field"><span>Publication run</span><select value={seriesId} onChange={(event) => setSeriesId(event.target.value)}><option value="">Choose a run…</option>{existingRuns.map((run) => <option value={run.id} key={run.id}>{run.title}{run.year ? ` (${run.year})` : ""}</option>)}</select></label> : <div className="run-assignment-fields"><label className="form-field"><span>New run title</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Publication run title…" required /></label><label className="form-field"><span>Start year</span><input type="number" min="1800" max="2200" value={year} onChange={(event) => setYear(event.target.value)} /></label><label className="form-field"><span>Publisher</span><input value={publisher} onChange={(event) => setPublisher(event.target.value)} /></label></div>}<div className="run-assignment-note"><ShieldCheck size={20} weight="fill" /><span><strong>Reversible catalog change</strong><small>You can use Change run again later. Covers, volume metadata, and issue-content evidence remain attached to this file.</small></span></div>{error ? <p className="workbench-error">{error}</p> : null}<div className="metadata-edit-actions"><button type="button" className="ghost-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy || (mode === "existing" ? !seriesId : !title.trim())}>{busy ? <SpinnerGap size={18} /> : <ArrowRight size={18} />} {mode === "existing" ? "Move file" : "Create run and move file"}</button></div></form></section></div>;
}

export function App() {
  const [active, setActive] = useState("library");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedSeries, setSelectedSeries] = useState(null);
  const [selectedCollection, setSelectedCollection] = useState(null);
  const [seriesParentCollection, setSeriesParentCollection] = useState(null);
  const [collectionTab, setCollectionTab] = useState("overview");
  const [scanState, setScanState] = useState("idle");
  const [scanProgress, setScanProgress] = useState(null);
  const [toast, setToast] = useState("");
  const [catalog, setCatalog] = useState(null);
  const [backendStatus, setBackendStatus] = useState("loading");
  const [workbench, setWorkbench] = useState(null);
  const [workbenchBusy, setWorkbenchBusy] = useState(false);
  const [workbenchError, setWorkbenchError] = useState("");
  const [issueWorkbench, setIssueWorkbench] = useState(null);
  const [issueBusy, setIssueBusy] = useState(false);
  const [issueError, setIssueError] = useState("");
  const [coverWorkbench, setCoverWorkbench] = useState(null);
  const [coverBusy, setCoverBusy] = useState(false);
  const [coverError, setCoverError] = useState("");
  const [contentsWorkbench, setContentsWorkbench] = useState(null);
  const [contentsBusy, setContentsBusy] = useState(false);
  const [contentsError, setContentsError] = useState("");
  const [runWorkbench, setRunWorkbench] = useState(null);
  const [runLoading, setRunLoading] = useState(false);
  const [runBusy, setRunBusy] = useState(false);
  const [runError, setRunError] = useState("");
  const [fileRunWorkbench, setFileRunWorkbench] = useState(null);
  const [fileRunBusy, setFileRunBusy] = useState(false);
  const [fileRunError, setFileRunError] = useState("");
  const [structureWorkbench, setStructureWorkbench] = useState(null);
  const [structureBusy, setStructureBusy] = useState(false);
  const [structureError, setStructureError] = useState("");
  const [placementWorkbench, setPlacementWorkbench] = useState(null);
  const [placementBusy, setPlacementBusy] = useState(false);
  const [placementError, setPlacementError] = useState("");
  const [replacementFile, setReplacementFile] = useState(null);
  const [replacementBusy, setReplacementBusy] = useState(false);
  const [replacementError, setReplacementError] = useState("");
  function navigate(id) { setActive(id); setSelectedSeries(null); setSelectedCollection(null); setSeriesParentCollection(null); window.scrollTo({ top: 0, behavior: "smooth" }); }
  function openSearch(value) {
    const cleaned = String(value || "").trim();
    if (cleaned.length < 2) return;
    setSearchQuery(cleaned);
    navigate("search");
  }
  function openSeries(series) { setSelectedCollection(null); setSeriesParentCollection(null); setSelectedSeries(series); }
  function openCollection(collection) { setSelectedSeries(null); setSeriesParentCollection(null); setCollectionTab("overview"); setSelectedCollection(collection); }
  function openCollectionRun(series) { setSeriesParentCollection(selectedCollection); setSelectedCollection(null); setSelectedSeries(series); }
  function returnToCollection() {
    if (!seriesParentCollection) return;
    const refreshed = catalog?.families?.find((collection) => collection.id === seriesParentCollection.id) || seriesParentCollection;
    setSelectedSeries(null);
    setSeriesParentCollection(null);
    setSelectedCollection(refreshed);
  }
  function showToast(message) { setToast(message); window.setTimeout(() => setToast(""), 3200); }
  async function loadCatalog() {
    try {
      const data = await apiRequest("/api/v1/catalog");
      setCatalog(data);
      setBackendStatus("live");
      return data;
    } catch {
      setBackendStatus("offline");
      return null;
    }
  }
  async function pollScan(scanId) {
    for (;;) {
      const scan = await apiRequest(`/api/v1/scans/${scanId}`);
      setScanProgress(scan);
      if (scan.status === "complete") return scan;
      if (scan.status === "failed") throw new Error(scan.error || "Library scan failed");
      await new Promise((resolve) => window.setTimeout(resolve, 650));
    }
  }
  async function scanLibrary(folder, recursive = true) {
    const target = folder || catalog?.roots?.[0]?.path;
    if (!target) { navigate("add"); return; }
    setScanState("scanning");
    setScanProgress(null);
    try {
      const queued = await apiRequest("/api/v1/scans", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder: target, recursive, metadataMode: "local" }),
      });
      const scan = await pollScan(queued.id);
      await loadCatalog();
      setScanState("done");
      showToast(`Library inventory complete · ${scan.changed_files} changed, ${scan.reused_files} unchanged`);
    } catch (error) {
      setScanState("idle");
      showToast(error.message);
    }
  }
  async function resolveReview(item) {
    try {
      await apiRequest("/api/v1/reviews/resolve", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: item.path, code: item.code, fingerprint: item.fingerprint }),
      });
      await loadCatalog();
      showToast("Review item resolved");
    } catch (error) {
      showToast(error.message);
    }
  }
  async function addSeriesAlias(seriesId, alias) {
    try {
      await apiRequest(`/api/v1/series/${seriesId}/aliases`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alias }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === String(seriesId));
      if (refreshed) setSelectedSeries(refreshed);
      showToast("Series alias confirmed");
      return { ok: true };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  async function createSeriesFamily(name, runIds) {
    try {
      await apiRequest("/api/v1/families", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, runIds }),
      });
      const selectedId = selectedSeries?.id;
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === selectedId);
      if (refreshed) setSelectedSeries(refreshed);
      showToast(`Collection “${name}” created`);
      return { ok: true };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  async function setSeriesFamily(seriesId, familyId) {
    try {
      await apiRequest(`/api/v1/series/${seriesId}/family`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ familyId }),
      });
      const selectedId = selectedSeries?.id;
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === selectedId);
      if (refreshed) setSelectedSeries(refreshed);
      showToast(familyId ? "Run moved into collection" : "Run removed from collection");
      return { ok: true };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  async function openStoryStructure(collection) {
    setStructureError("");
    try {
      const data = await apiRequest(`/api/v1/collections/${collection.id}/structure`);
      setStructureWorkbench(data);
    } catch (error) { showToast(error.message); }
  }
  async function saveStoryStructure(arcs) {
    setStructureBusy(true); setStructureError("");
    try {
      const result = await apiRequest(`/api/v1/collections/${structureWorkbench.collection.id}/structure`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ arcs }),
      });
      const data = await loadCatalog();
      const refreshed = data?.families?.find((item) => item.id === result.collection.id);
      if (refreshed) setSelectedCollection(refreshed);
      setStructureWorkbench(null);
      showToast(`${result.arcCount} story group${result.arcCount === 1 ? "" : "s"} saved`);
    } catch (error) { setStructureError(error.message); }
    setStructureBusy(false);
  }
  function openVolumePlacement(data) {
    if (!data.file?.id) { showToast("The local file linked to this volume could not be resolved"); return; }
    setPlacementError("");
    setPlacementWorkbench(data);
  }
  async function saveVolumePlacement({ type, name }) {
    const { collection, volume, file } = placementWorkbench;
    const sourceRunId = volume.run.id;
    let movedRunId = null;
    let placementSaved = false;
    setPlacementBusy(true); setPlacementError("");
    try {
      const moved = await apiRequest(`/api/v1/files/${file.id}/series-run`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: volume.title, year: volume.publicationYear, publisher: volume.publisher || collection.publisher }),
      });
      movedRunId = moved.seriesId;
      await apiRequest(`/api/v1/series/${movedRunId}/family`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ familyId: collection.id }),
      });
      const structure = await apiRequest(`/api/v1/collections/${collection.id}/structure`);
      const arcs = structure.arcs.map((arc) => ({
        name: arc.name,
        type: arc.type,
        runIds: (arc.runIds || []).filter((runId) => String(runId) !== String(movedRunId)),
      })).filter((arc) => arc.runIds.length);
      const existing = arcs.find((arc) => identityKey(arc.name) === identityKey(name));
      if (existing) {
        existing.type = type;
        existing.runIds.push(String(movedRunId));
      } else {
        arcs.push({ name, type, runIds: [String(movedRunId)] });
      }
      await apiRequest(`/api/v1/collections/${collection.id}/structure`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ arcs }),
      });
      placementSaved = true;
      const data = await loadCatalog();
      const refreshed = data?.families?.find((item) => item.id === collection.id);
      if (refreshed) setSelectedCollection(refreshed);
      setPlacementWorkbench(null);
      setCollectionTab(type === "specials" ? "specials" : "arcs");
      showToast(`${volume.title} placed in ${name}`);
    } catch (error) {
      if (movedRunId && !placementSaved) {
        try {
          await apiRequest(`/api/v1/files/${file.id}/series-run`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ seriesId: sourceRunId }),
          });
          if (String(movedRunId) !== String(sourceRunId)) {
            await apiRequest(`/api/v1/series/${movedRunId}/family`, {
              method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ familyId: null }),
            });
          }
        } catch { /* Leave the original error visible; catalog recovery remains available through Change run. */ }
      }
      setPlacementError(error.message);
    }
    setPlacementBusy(false);
  }
  async function syncSeriesIssues(seriesId, automatic = false) {
    try {
      const result = await apiRequest(`/api/v1/series/${seriesId}/issues/sync`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === String(seriesId));
      if (refreshed) setSelectedSeries(refreshed);
      const metadata = result.metadata || {};
      const repaired = (metadata.repairedTitleCount || 0) + (metadata.repairedDateCount || 0);
      const remaining = (metadata.missingTitleCount || 0) + (metadata.missingDateCount || 0);
      if (repaired) {
        showToast(`${repaired} missing field${repaired === 1 ? "" : "s"} filled${remaining ? ` · ${remaining} still unavailable` : " · metadata complete"}`);
      } else if (remaining) {
        showToast(`Metadata checked · ${remaining} field${remaining === 1 ? "" : "s"} still unavailable from providers`);
      } else {
        showToast(`${result.issueCount} issues checked · metadata complete`);
      }
      return { ok: true, result };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  async function openSeriesRunWorkbench(series) {
    setRunError("");
    setRunLoading(true);
    setRunWorkbench({ series: { id: series.id, title: series.title, year: series.year, publisher: series.publisher, ownedIssueNumbers: series.issues?.filter((issue) => issue.directOwned || issue.collectionOwned).map((issue) => issue.number) || [] }, candidates: null });
    try {
      const data = await apiRequest(`/api/v1/series/${series.id}/issue-runs`);
      setRunWorkbench(data);
    } catch (error) {
      setRunError(error.message);
    }
    setRunLoading(false);
  }
  async function confirmSeriesRun(providerSeriesId) {
    setRunBusy(true); setRunError("");
    try {
      const result = await apiRequest(`/api/v1/series/${runWorkbench.series.id}/issue-run`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ providerSeriesId }),
      });
      const data = await loadCatalog();
      const refreshed = data?.series?.find((item) => item.id === runWorkbench.series.id);
      if (refreshed) setSelectedSeries(refreshed);
      setRunWorkbench(null);
      showToast(`${result.issueCount} canonical issues loaded from the confirmed run`);
    } catch (error) {
      setRunError(error.message);
    }
    setRunBusy(false);
  }
  async function buildSeriesCollection(providerSeriesIds, name, options = {}) {
    setRunBusy(true); setRunError("");
    try {
      const result = await apiRequest(`/api/v1/series/${runWorkbench.series.id}/collection-structure`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ providerSeriesIds, name, ...options }),
      });
      const data = await loadCatalog();
      const collection = data?.families?.find((item) => item.id === result.collection.id);
      setRunWorkbench(null);
      setSelectedSeries(null);
      if (collection) setSelectedCollection(collection);
      showToast(`${name} added · ${result.issueCount} issues monitored · ${ACQUISITION_LABELS[options.acquisitionPreference] || ACQUISITION_LABELS.either}`);
    } catch (error) { setRunError(error.message); }
    setRunBusy(false);
  }
  async function openFileWorkbench(file, mode) {
    setWorkbenchError("");
    try {
      const data = await apiRequest(`/api/v1/files/${file.id}`);
      setWorkbench({ data, mode });
    } catch (error) {
      showToast(error.message);
    }
  }
  async function openFileRunWorkbench(file) {
    setFileRunError("");
    try {
      const data = await apiRequest(`/api/v1/files/${file.id}`);
      setFileRunWorkbench(data);
    } catch (error) {
      showToast(error.message);
    }
  }
  async function moveFileToRun(payload) {
    setFileRunBusy(true); setFileRunError("");
    try {
      const result = await apiRequest(`/api/v1/files/${fileRunWorkbench.file.id}/series-run`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      const data = await loadCatalog();
      const movedRun = data?.series?.find((series) => series.id === result.seriesId);
      setSelectedSeries(movedRun || null);
      setFileRunWorkbench(null);
      showToast(`File moved to “${result.title}”`);
    } catch (error) {
      setFileRunError(error.message);
    }
    setFileRunBusy(false);
  }
  async function finishMetadataChange(message) {
    const selectedId = selectedSeries?.id;
    const data = await loadCatalog();
    const refreshed = data?.series?.find((item) => item.id === selectedId);
    if (refreshed) setSelectedSeries(refreshed);
    setWorkbench(null);
    showToast(message);
  }
  async function saveFileMetadata(fields, lockedFields) {
    setWorkbenchBusy(true); setWorkbenchError("");
    try {
      await apiRequest(`/api/v1/files/${workbench.data.file.id}/metadata`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fields, lockedFields }) });
      await finishMetadataChange("Metadata corrected and locked");
    } catch (error) { setWorkbenchError(error.message); }
    setWorkbenchBusy(false);
  }
  async function applyFileMatch(candidateKey) {
    setWorkbenchBusy(true); setWorkbenchError("");
    try {
      await apiRequest(`/api/v1/files/${workbench.data.file.id}/match`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ candidateKey }) });
      await finishMetadataChange("Match replaced and locked");
    } catch (error) { setWorkbenchError(error.message); }
    setWorkbenchBusy(false);
  }
  async function resetFileMetadata() {
    setWorkbenchBusy(true); setWorkbenchError("");
    try {
      await apiRequest(`/api/v1/files/${workbench.data.file.id}/metadata/reset`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await finishMetadataChange("Original provider metadata restored");
    } catch (error) { setWorkbenchError(error.message); }
    setWorkbenchBusy(false);
  }
  function openIssueWorkbench(issue) {
    setIssueError("");
    setIssueWorkbench(issue);
  }
  async function finishIssueMetadataChange(message) {
    const selectedSeriesId = selectedSeries?.id;
    const selectedCollectionId = selectedCollection?.id;
    const data = await loadCatalog();
    if (selectedSeriesId) {
      const refreshedSeries = data?.series?.find((item) => item.id === selectedSeriesId);
      if (refreshedSeries) setSelectedSeries(refreshedSeries);
    }
    if (selectedCollectionId) {
      const refreshedCollection = data?.families?.find((item) => item.id === selectedCollectionId);
      if (refreshedCollection) setSelectedCollection(refreshedCollection);
    }
    setIssueWorkbench(null);
    showToast(message);
  }
  async function saveIssueMetadata(fields) {
    setIssueBusy(true); setIssueError("");
    try {
      await apiRequest(`/api/v1/issues/${issueWorkbench.id}/metadata`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fields),
      });
      await finishIssueMetadataChange("Issue metadata corrected and locked");
    } catch (error) { setIssueError(error.message); }
    setIssueBusy(false);
  }
  async function resetIssueMetadata() {
    setIssueBusy(true); setIssueError("");
    try {
      await apiRequest(`/api/v1/issues/${issueWorkbench.id}/metadata/reset`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      await finishIssueMetadataChange("Provider issue metadata restored");
    } catch (error) { setIssueError(error.message); }
    setIssueBusy(false);
  }
  async function createAcquisitionRequest(target) {
    const collection = target?.isCollectionSeries ? target.collection : target;
    const isCollection = Boolean(collection?.runs);
    try {
      const request = await apiRequest("/api/v1/requests", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scopeType: isCollection ? "collection" : "series",
          scopeId: collection.id,
          acquisitionPreference: collection.acquisitionPreference || "either",
          includeSpecials: collection.includeSpecials ?? true,
        }),
      });
      await loadCatalog();
      setSelectedSeries(null); setSelectedCollection(null); setSeriesParentCollection(null);
      navigate("requests");
      showToast(`${request.title} is now monitored · ${request.wantedIssueCount} missing issues queued`);
      return { ok: true, request };
    } catch (error) {
      showToast(error.message);
      return { ok: false, error: error.message };
    }
  }
  async function requestDiscoveredSeries(target) {
    try {
      const result = await apiRequest("/api/v1/discover", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: target.query,
          provider: target.provider,
          providerSeriesId: target.providerSeriesId,
          acquisitionPreference: "either",
        }),
      });
      await loadCatalog();
      navigate("requests");
      showToast(`${result.series.title} added · ${result.issueCount} issues followed`);
      return { ok: true, result };
    } catch (error) {
      showToast(error.message);
      return { ok: false, error: error.message };
    }
  }
  function openReplacementRequest(file) {
    setReplacementError("");
    setReplacementFile({ ...file, id: file.fileId || file.id, filename: file.filename || file.file });
  }
  async function createFileReplacement(options) {
    setReplacementBusy(true); setReplacementError("");
    try {
      const request = await apiRequest(`/api/v1/files/${replacementFile.id}/replacement`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(options),
      });
      await loadCatalog();
      setReplacementFile(null); setSelectedSeries(null);
      navigate("requests");
      showToast(`${request.targetTitle} added to the replacement list`);
    } catch (error) { setReplacementError(error.message); }
    setReplacementBusy(false);
  }
  async function cancelFileReplacement(request) {
    try {
      await apiRequest(`/api/v1/replacements/${request.id}/status`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "cancelled" }),
      });
      await loadCatalog();
      showToast(`Replacement cancelled for ${request.targetTitle}`);
    } catch (error) { showToast(error.message); }
  }
  async function openCoverWorkbench(file) {
    setCoverError("");
    try {
      const data = await apiRequest(`/api/v1/files/${file.id}`);
      setCoverWorkbench(data);
    } catch (error) { showToast(error.message); }
  }
  async function finishCoverChange(message) {
    const selectedId = selectedSeries?.id;
    const data = await loadCatalog();
    const refreshed = data?.series?.find((item) => item.id === selectedId);
    if (refreshed) setSelectedSeries(refreshed);
    setCoverWorkbench(null);
    showToast(message);
  }
  async function selectFileCover(source, url) {
    setCoverBusy(true); setCoverError("");
    try {
      await apiRequest(`/api/v1/files/${coverWorkbench.file.id}/cover`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source, url }) });
      await finishCoverChange(source === "auto" ? "Automatic cover selection restored" : "Preferred cover updated");
    } catch (error) { setCoverError(error.message); }
    setCoverBusy(false);
  }
  async function uploadFileCover(file) {
    setCoverBusy(true); setCoverError("");
    try {
      await apiRequest(`/api/v1/files/${coverWorkbench.file.id}/cover/upload`, { method: "POST", headers: { "Content-Type": file.type }, body: file });
      await finishCoverChange("Uploaded cover saved");
    } catch (error) { setCoverError(error.message); }
    setCoverBusy(false);
  }
  async function openContentsWorkbench(file) {
    setContentsError("");
    try {
      const data = await apiRequest(`/api/v1/files/${file.id}`);
      if (!data.collectionContents) throw new Error("This file is not linked to a volume");
      setContentsWorkbench(data);
    } catch (error) { showToast(error.message); }
  }
  async function finishContentsChange(data, message) {
    const selectedId = selectedSeries?.id;
    const catalogData = await loadCatalog();
    const refreshed = catalogData?.series?.find((item) => item.id === selectedId);
    if (refreshed) setSelectedSeries(refreshed);
    setContentsWorkbench(data);
    showToast(message);
  }
  async function changeCollectionContents(change) {
    setContentsBusy(true); setContentsError("");
    try {
      const data = await apiRequest(`/api/v1/files/${contentsWorkbench.file.id}/contents`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(change) });
      await finishContentsChange(data, change.included ? "Volume contents updated" : "Issue excluded from volume");
    } catch (error) { setContentsError(error.message); }
    setContentsBusy(false);
  }
  async function resetCollectionContents() {
    setContentsBusy(true); setContentsError("");
    try {
      const data = await apiRequest(`/api/v1/files/${contentsWorkbench.file.id}/contents/reset`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await finishContentsChange(data, "Provider volume contents restored");
    } catch (error) { setContentsError(error.message); }
    setContentsBusy(false);
  }
  useEffect(() => { loadCatalog(); }, []);
  useEffect(() => {
    const activeDownload = (catalog?.requests || []).some((request) =>
      (request.jobs || []).some((job) => ["queued", "downloading", "completed", "importing"].includes(job.downloadStatus))
    );
    if (!(catalog?.enrichment?.active > 0) && !activeDownload) return undefined;
    const timer = window.setInterval(() => loadCatalog(), 5000);
    return () => window.clearInterval(timer);
  }, [catalog?.enrichment?.active, catalog?.requests]);
  const visibleSeries = catalog?.series ?? (backendStatus === "offline" ? DEMO_SERIES : []);
  const logicalSeriesCount = logicalCatalogSeries(catalog, visibleSeries).length;
  return <div className="app-shell"><Nav active={active === "search" ? "library" : active} onNavigate={navigate} catalog={catalog} backendStatus={backendStatus} logicalSeriesCount={logicalSeriesCount} /><main className="main-content">{active === "library" ? <LibraryView onNavigate={navigate} onOpenSeries={openSeries} onOpenCollection={openCollection} onSearch={openSearch} catalog={catalog} backendStatus={backendStatus} /> : null}{active === "search" ? <SearchResultsView query={searchQuery} catalog={catalog} backendStatus={backendStatus} onSearch={openSearch} onOpenSeries={openSeries} onOpenCollection={openCollection} onDiscoverRequest={requestDiscoveredSeries} /> : null}{active === "add" ? <AddComicsView onNavigate={navigate} onStartInventory={scanLibrary} onScanLibrary={() => scanLibrary()} catalog={catalog} scanState={scanState} scanProgress={scanProgress} /> : null}{active === "requests" ? <RequestsView catalog={catalog} onCreateRequest={createAcquisitionRequest} onCancelReplacement={cancelFileReplacement} onRefresh={loadCatalog} /> : null}{active === "metadata" ? <MetadataView items={catalog?.inbox ?? []} backendStatus={backendStatus} onResolve={resolveReview} onReplace={openReplacementRequest} /> : null}{active === "activity" ? <ActivityView /> : null}{active === "settings" ? <SettingsView catalog={catalog} /> : null}</main>{selectedSeries ? <SeriesDrawer series={selectedSeries} families={catalog?.families || []} allSeries={visibleSeries} parentCollection={seriesParentCollection} onBack={returnToCollection} onClose={() => { setSelectedSeries(null); setSeriesParentCollection(null); }} onRequest={() => createAcquisitionRequest(selectedSeries)} onAddAlias={addSeriesAlias} onSyncIssues={syncSeriesIssues} onFindRun={openSeriesRunWorkbench} onCreateFamily={createSeriesFamily} onSetFamily={setSeriesFamily} onOpenWorkbench={openFileWorkbench} onOpenCover={openCoverWorkbench} onOpenContents={openContentsWorkbench} onChangeRun={openFileRunWorkbench} onEditIssue={openIssueWorkbench} onReplace={openReplacementRequest} /> : null}{selectedCollection ? <CollectionDrawer collection={selectedCollection} tab={collectionTab} onTabChange={setCollectionTab} onClose={() => setSelectedCollection(null)} onFindStructure={openStoryStructure} onOpenSeries={openCollectionRun} onReviewPlacement={openVolumePlacement} onRequest={() => createAcquisitionRequest(selectedCollection)} onEditIssue={openIssueWorkbench} /> : null}{workbench ? <MetadataWorkbench data={workbench.data} mode={workbench.mode} busy={workbenchBusy} error={workbenchError} onClose={() => setWorkbench(null)} onSave={saveFileMetadata} onMatch={applyFileMatch} onReset={resetFileMetadata} /> : null}{issueWorkbench ? <IssueMetadataWorkbench issue={issueWorkbench} busy={issueBusy} error={issueError} onClose={() => setIssueWorkbench(null)} onSave={saveIssueMetadata} onReset={resetIssueMetadata} /> : null}{coverWorkbench ? <CoverWorkbench data={coverWorkbench} busy={coverBusy} error={coverError} onClose={() => setCoverWorkbench(null)} onSelect={selectFileCover} onUpload={uploadFileCover} /> : null}{contentsWorkbench ? <VolumeContentsWorkbench data={contentsWorkbench} busy={contentsBusy} error={contentsError} onClose={() => setContentsWorkbench(null)} onChange={changeCollectionContents} onReset={resetCollectionContents} /> : null}{runWorkbench ? <SeriesRunWorkbench data={runWorkbench} loading={runLoading} busy={runBusy} error={runError} onClose={() => setRunWorkbench(null)} onConfirm={confirmSeriesRun} onBuildCollection={buildSeriesCollection} /> : null}{fileRunWorkbench ? <FileRunWorkbench data={fileRunWorkbench} busy={fileRunBusy} error={fileRunError} onClose={() => setFileRunWorkbench(null)} onMove={moveFileToRun} /> : null}{structureWorkbench ? <StoryStructureWorkbench data={structureWorkbench} busy={structureBusy} error={structureError} onClose={() => setStructureWorkbench(null)} onSave={saveStoryStructure} /> : null}{placementWorkbench ? <VolumePlacementWorkbench data={placementWorkbench} busy={placementBusy} error={placementError} onClose={() => setPlacementWorkbench(null)} onSave={saveVolumePlacement} /> : null}{replacementFile ? <ReplacementModal file={replacementFile} busy={replacementBusy} error={replacementError} onClose={() => setReplacementFile(null)} onSubmit={createFileReplacement} /> : null}{toast ? <div className="toast"><CheckCircle size={20} weight="fill" /> {toast}</div> : null}</div>;
}
