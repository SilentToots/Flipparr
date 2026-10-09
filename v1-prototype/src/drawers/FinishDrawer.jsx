import { useEffect, useMemo, useState } from "react";
import { apiRequest } from "../api.js";
import { DiscoverCover } from "../components/CoverArt.jsx";
import { useDialog, useDrawerExit, useSwipeToDismiss } from "../dialogs.jsx";
import { issueLabel } from "../discover.js";

import { RATING_SOURCES } from "../ratings.js";
import { nextInList, skippedLine } from "../reading-list.js";

import { ComicDrawerIssueCard, ComicDrawerRow, DiscoverDrawerHero, DrawerPrimary, RunSynopsis, StarRating, toneProps, useArtTone } from "./frame.jsx";
import { BookOpen, CheckCircle, WarningCircle } from "@phosphor-icons/react";
import { useManages } from "../host.js";

/**
 * The end of an issue.
 *
 * It opens over the reader rather than inside it, which is why it is the app's
 * drawer and not a card of its own: the same panel, the same top bar, the same
 * four ways out. Dismissing it puts you back on the last page, because the
 * reader underneath never unmounted -- so there is nothing here called "Keep
 * reading" or "Done". Leaving is leaving.
 *
 * What it says is what a bookshelf says when you close a book: this is what
 * you finished, what did you think of it, here is the next one and what it is
 * about, and here is the rest of the run.
 */
export function FinishDrawer({ series, issue, nextIssue, medium, title, readingVersion = 0, fileId = "", list = null,
  onPullMissing, onRequestMissing, onRateIssue, onRead, onOpenSeries, onClose }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  useSwipeToDismiss(dialogRef, requestClose);
  const admin = useManages();
  // Opened from a story arc: the next comic is the arc's next, whatever run
  // it is in, and the issues passed over to reach it are named. Asked again
  // after this very comic changed the arc's places.
  const [arc, setArc] = useState(null);
  useEffect(() => {
    if (!list?.id) { setArc(null); return undefined; }
    let live = true;
    apiRequest(`/api/v1/reading-lists/${list.id}`)
      .then((data) => { if (live) setArc(data); })
      .catch(() => { if (live) setArc(null); });
    return () => { live = false; };
  }, [list?.id, readingVersion]);
  const arcNext = useMemo(() => (arc ? nextInList(arc.items, fileId || issue?.fileId) : null), [arc, fileId, issue?.fileId]);
  const arcName = arc?.name || list?.name || "the arc";
  const inArc = Boolean(list && arc);
  const art = issue?.fileCover || issue?.cover || series?.cover || null;
  const toned = toneProps(useArtTone(art));
  // The run's progress has just changed -- by this very comic -- so the shelf
  // asks again rather than showing places that are one issue out of date.
  const [reading, setReading] = useState(null);
  useEffect(() => {
    if (!series?.id) return undefined;
    let live = true;
    apiRequest(`/api/v1/series/${series.id}/reading`)
      .then((data) => { if (live) setReading(data); })
      .catch(() => { if (live) setReading(null); });
    return () => { live = false; };
  }, [series?.id, readingVersion]);
  const readingFiles = useMemo(() => Object.fromEntries(
    [...(reading?.issues || []), ...(reading?.volumes || [])].map((file) => [String(file.id), file]),
  ), [reading]);
  // What the next issue is about, asked for when the drawer opens. It is the
  // next issue's blurb, not this one's: a card offering a comic is where a
  // description earns its place, and one about the comic you have just read
  // is a recap. Nothing is asked when the run is over.
  const [detail, setDetail] = useState({ state: "loading", data: null });
  const offeredIssueId = inArc ? arcNext?.next?.issueId : nextIssue?.id;
  useEffect(() => {
    if (!offeredIssueId) return undefined;
    let live = true;
    setDetail({ state: "loading", data: null });
    apiRequest(`/api/v1/issues/${offeredIssueId}/detail`)
      .then((data) => { if (live) setDetail({ state: "done", data }); })
      .catch(() => { if (live) setDetail({ state: "done", data: { status: "unavailable" } }); });
    return () => { live = false; };
  }, [offeredIssueId]);
  const description = detail.state === "loading" ? <RunSynopsis loading heading={null} />
    : detail.data?.description
      ? <RunSynopsis heading={null} text={detail.data.description} source={detail.data.providerName} />
      : <p className="discover-note">{detail.data?.status === "unavailable"
        ? "This issue’s details are unavailable right now."
        : "No description on record for this issue."}</p>;
  const heading = issue
    ? `${issueLabel(issue.number, medium)}${issue.title ? ` · ${issue.title}` : ""}`
    : title;
  // Everything else in the run, including what is missing from it: "next" skips
  // the issues you do not own, and a shelf that hid them would make that skip
  // look like the run itself.
  const rest = (series?.issues || []).filter((item) => (
    item !== issue && item !== nextIssue
  ));
  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer comic-drawer finish-drawer${toned.className} ${closing ? "closing" : ""}`}
      style={toned.style} ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="finish-drawer-title"
      onMouseDown={(event) => event.stopPropagation()}>
      <DiscoverDrawerHero art={art} titleId="finish-drawer-title" title={heading}
        cover={<DiscoverCover src={art} alt="" glyph={30} />}
        byline={issue ? series?.title : null}
        onClose={requestClose} closeLabel="Close">
        <span className="finish-done"><CheckCircle size={15} weight="fill" /> Finished</span>
      </DiscoverDrawerHero>
      {/* Reading on is the band's pill, as Read is every comic drawer's; the
          card below says what it is and what was passed over to reach it. */}
      {onRead && inArc && arcNext?.next ? <DrawerPrimary icon={<BookOpen size={20} weight="fill" />} verb="Read"
        noun={`Issue ${issueLabel(arcNext.next.number, "comic")}`} label={`Read ${arcNext.next.seriesTitle} ${issueLabel(arcNext.next.number, "comic")}, next in ${arcName}`}
        onClick={() => onRead({ id: arcNext.next.fileId, listId: list.id, listName: arcName })} />
        : onRead && !inArc && nextIssue ? <DrawerPrimary icon={<BookOpen size={20} weight="fill" />} verb="Read"
          noun={issueLabel(nextIssue.number, medium).startsWith("#") ? `Issue ${issueLabel(nextIssue.number, medium)}` : issueLabel(nextIssue.number, medium)}
          label={`Read ${[series?.title, issueLabel(nextIssue.number, medium)].filter(Boolean).join(" ")}, next in this run`}
          onClick={() => onRead({ id: nextIssue.fileId })} /> : null}
      <div className="comic-drawer-body">
        {/* The two cards sit close, as one pair of things to do; the shelf
            below keeps the body's own distance. */}
        <div className="finish-cards">
        {issue && onRateIssue ? <section className="finish-card finish-card--rating" aria-label="Your rating">
          <h3>What did you think?</h3>
          <StarRating size={28} title={issue.title || issueLabel(issue.number, medium)}
            rating={{ value: issue.yourRating || 0, source: issue.yourRating ? RATING_SOURCES.yours : RATING_SOURCES.none, count: 0 }}
            onRate={(value) => onRateIssue(issue, value)} />
        </section> : null}

        {inArc && onRead ? (arcNext?.next ? <section className="finish-next-block" aria-label={`Next in ${arcName}`}>
          <h3>Next in {arcName}</h3>
          <div className="finish-card finish-next">
            <DiscoverCover src={arcNext.next.fileCover || arcNext.next.cover} alt="" glyph={24} />
            <div className="finish-next-copy">
              <strong>{arcNext.next.seriesTitle} {issueLabel(arcNext.next.number, "comic")}</strong>
              {/* What was passed over to get here, and the way to close the
                  gap. Reading is never blocked by it. */}
              {arcNext.skipped.length ? <p className="finish-skipped">
                <WarningCircle size={15} weight="fill" aria-hidden="true" />
                <span>{skippedLine(arcNext.skipped)}</span>
                {!arcNext.skipped.every((item) => item.queued) && (admin ? onPullMissing : onRequestMissing)
                  ? <button type="button" onClick={() => (admin ? onPullMissing : onRequestMissing)(arc)}>{admin ? "Pull" : "Request"}</button> : null}
              </p> : null}
              {description}
            </div>
          </div>
        </section> : <>
          <p className="finish-last">That is the last comic in {arcName}.</p>
          {arcNext?.skipped?.length ? <p className="finish-skipped">
            <WarningCircle size={15} weight="fill" aria-hidden="true" />
            <span>{skippedLine(arcNext.skipped)}</span>
            {!arcNext.skipped.every((item) => item.queued) && (admin ? onPullMissing : onRequestMissing)
              ? <button type="button" onClick={() => (admin ? onPullMissing : onRequestMissing)(arc)}>{admin ? "Pull" : "Request"}</button> : null}
          </p> : null}
        </>) : null}
        {!(inArc && arcNext?.next) ? (nextIssue && onRead ? <section className="finish-next-block" aria-label="Next in this run">
          <h3>Next in this run</h3>
          <div className="finish-card finish-next">
            <DiscoverCover src={nextIssue.fileCover || nextIssue.cover} alt="" glyph={24} />
            <div className="finish-next-copy">
              {/* Named the way a shelf names it: the run and the number, then
                  the story's own title as the thing you are being offered. */}
              <strong>{[series?.title, issueLabel(nextIssue.number, medium)].filter(Boolean).join(" ")}</strong>
              {nextIssue.title ? <span className="finish-next-title">{nextIssue.title}</span> : null}
              {description}
            </div>
          </div>
        </section> : <p className="finish-last">That is the last comic this run has.</p>) : null}
        </div>

        {rest.length ? <ComicDrawerRow title="More from this run" count={rest.length}>
          {rest.map((item) => <ComicDrawerIssueCard key={item.id} issue={item} medium={medium}
            readingFiles={readingFiles} onRead={onRead}
            onOpen={() => { requestClose(); onOpenSeries?.(series); }} />)}
        </ComicDrawerRow> : null}
      </div>
    </aside>
  </div>;
}
