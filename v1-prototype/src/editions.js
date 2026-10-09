import { createContext, useContext } from "react";

export const EDITION_KIND_LABELS = {
  omnibus: "Omnibus",
  compendium: "Compendium",
  deluxe_edition: "Deluxe volume",
  graphic_novel: "Graphic novel",
  hardcover: "Hardcover",
  collected_volume: "Collected volume",
  collection: "Collected volume",
  edition: "Volume",
};

export function editionKindLabel(kind) {
  return EDITION_KIND_LABELS[kind] || "Volume";
}

// Collected editions are always catalogued; this flag only gates the surfaces
// for browsing and managing them (the Volumes tab, its stats, and editing an
// edition's issue contents). Wording is deliberately unchanged: a trade or
// hardcover is still a volume, and the files stay visible and fixable either
// way — the user just isn't offered edition management.
export const CollectedEditionsContext = createContext(false);

export function useCollectedEditions() {
  return useContext(CollectedEditionsContext);
}

export function volumeTerminology(value) {
  if (!value) return value;
  return String(value)
    .replace(/collected[- ]editions/gi, "volumes")
    .replace(/collected[- ]edition/gi, "volume")
    .replace(/collection (coverage|contents|identity|source)/gi, "volume $1")
    .replace(/file edition/gi, "file volume");
}

export function identityKey(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "");
}
