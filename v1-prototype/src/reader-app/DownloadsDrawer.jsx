// A run's downloads, managed together (Flipparr View; the owner, 2026-10-11):
// what Downloads' stacked card opens. The comic drawer's frame -- its top
// bar, hero, action band and body -- so it reads as the run drawer's
// sibling: Continue (or Read) and Download more beside it, then every comic
// of the run kept on this device, each with its reading state, size and its
// own Remove, and at the foot Remove read and Remove all. It is drawn from
// the device's own record of its downloads, so it works offline.
import { useState } from "react";
import { BookOpen, Trash } from "@phosphor-icons/react";
import { DiscoverCover } from "../components/CoverArt.jsx";
import { ProgressRing, StopSquare } from "../components/ProgressRing.jsx";
import { StatusBadge } from "../components/StatusBadge";
import { useDialog, useDrawerExit, useSwipeToDismiss } from "../dialogs.jsx";
import { DiscoverDrawerHero, ReadWithDownload } from "../drawers/frame.jsx";
import { formatBytes, nextDownloaded, progressOf } from "./offline.js";

const comicName = (row) => (row.issueNumber ? `#${row.issueNumber}` : row.filename);

function rowCaption(row) {
  if (row.state === "downloading") return `Downloading ${Math.round(progressOf(row) * 100)}%`;
  if (row.state === "failed") return "Didn’t finish";
  const reading = row.finished ? "Read" : row.lastReadAt ? "In progress" : "Unread";
  return row.bytes ? `${reading} · ${formatBytes(row.bytes)}` : reading;
}

export function DownloadsDrawer({ group, downloads, onRead, onClose, onToast }) {
  const { closing, requestClose } = useDrawerExit(onClose);
  const dialogRef = useDialog(requestClose);
  useSwipeToDismiss(dialogRef, requestClose);
  const [busy, setBusy] = useState(false);
  const rows = group.rows;
  const bytes = rows.reduce((sum, row) => sum + (row.bytes || 0), 0);
  const read = rows.filter((row) => row.finished);
  const next = nextDownloaded(rows.filter((row) => row.state === "ready" || row.state === "stale"));
  const started = next?.lastReadAt && !next.finished;
  const noun = next ? comicName(next) : "";

  async function remove(list, message) {
    setBusy(true);
    try {
      for (const row of list) await downloads.remove(row.fileId);
      onToast?.(message);
      if (list.length === rows.length) requestClose();
    } finally {
      setBusy(false);
    }
  }

  return <div className={`drawer-backdrop ${closing ? "closing" : ""}`} onMouseDown={requestClose}>
    <aside className={`series-drawer comic-drawer downloads-drawer ${closing ? "closing" : ""}`} ref={dialogRef}
      role="dialog" aria-modal="true" aria-labelledby="downloads-drawer-title" onMouseDown={(event) => event.stopPropagation()}>
      <DiscoverDrawerHero art={group.cover} title={group.title} titleId="downloads-drawer-title"
        byline={`${rows.length} on this device${bytes ? ` · ${formatBytes(bytes)}` : ""}`}
        onClose={requestClose} closeLabel={`Close ${group.title}'s downloads`}
        cover={<DiscoverCover src={group.cover} alt={`${group.title} cover`} glyph={30} />}>
        <StatusBadge tone="muted">Downloaded</StatusBadge>
        {read.length ? <StatusBadge tone={read.length === rows.length ? "green" : "violet"}>
          {read.length === rows.length ? "Read" : `${read.length} of ${rows.length} read`}</StatusBadge> : null}
      </DiscoverDrawerHero>
      <div className="comic-drawer-actions">
        <ReadWithDownload group={{ kind: "run", id: String(group.runId), title: group.title }}>
          {next ? <button type="button" className="primary-button comic-drawer-read" onClick={() => onRead(next.fileId)}
            aria-label={`${started ? "Continue" : next.finished ? "Read again" : "Read"} ${group.title} ${noun}`}>
            <BookOpen size={20} weight="fill" />
            <span>{started ? "Continue" : next.finished ? "Read again" : "Read"}<b>{noun}</b></span>
          </button> : <span />}
        </ReadWithDownload>
      </div>
      <div className="comic-drawer-body">
        <section className="downloads-drawer-list" aria-label="Downloaded issues">
          {rows.map((row) => {
            const readable = row.state === "ready" || row.state === "stale";
            const coming = row.state === "downloading";
            return <article className="download-row" key={row.fileId}>
              <button type="button" className="download-row-open" disabled={!readable} onClick={() => onRead(row.fileId)}
                aria-label={`${comicName(row)}, ${rowCaption(row)}${readable ? ". Read" : ""}`}>
                <span className="download-row-cover"><DiscoverCover src={row.pageUrls?.[0]} alt="" glyph={16} /></span>
                <span className="download-row-copy"><strong>{comicName(row)}</strong><small>{rowCaption(row)}</small></span>
              </button>
              {coming
                ? <button type="button" className="download-row-action" disabled={busy}
                  onClick={() => remove([row], `Stopped downloading ${group.title} ${comicName(row)}`)}
                  aria-label={`Stop downloading ${comicName(row)}`}><ProgressRing progress={progressOf(row)}><StopSquare /></ProgressRing></button>
                : <button type="button" className="download-row-action" disabled={busy}
                  onClick={() => remove([row], `${group.title} ${comicName(row)} removed from this device`)}
                  aria-label={`Remove ${comicName(row)} from this device`}><Trash size={18} /></button>}
            </article>;
          })}
        </section>
        <div className="advanced-tools">
          {read.length && read.length < rows.length ? <section className="advanced-card">
            <div><strong>Remove read</strong><p>{read.length} read {read.length === 1 ? "comic leaves" : "comics leave"} this device; the rest stay.</p></div>
            <button type="button" disabled={busy} onClick={() => remove(read, `Removed ${read.length} read from this device`)}><Trash size={16} /> Remove read</button>
          </section> : null}
          <section className="advanced-card danger">
            <div><strong>Remove all from this device</strong><p>Every comic of {group.title} here goes. They stay in your library to read online or download again.</p></div>
            <button type="button" disabled={busy} onClick={() => {
              if (!window.confirm(`Remove all ${rows.length} of ${group.title} from this device?`)) return;
              remove(rows, `Removed ${group.title} from this device`);
            }}><Trash size={16} /> Remove all</button>
          </section>
        </div>
      </div>
    </aside>
  </div>;
}
