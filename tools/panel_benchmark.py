"""Panel benchmark: the automatic readings against the pages a person fixed by hand.

Every page someone corrected in the panel editor is a page the machine got
wrong, or read differently from the way that reader wanted to read it. This
measures how far apart the two are, page by page and in aggregate, so a
change to the detector can be judged on the library it serves rather than
on drawn fixtures -- and so a change that moves nothing is not kept.

It runs the LOCAL tiers only -- the gutter cut, the ONNX detector when a
model is given, the leftover pass -- on a copy of the catalog, with the
library mounted read-only. It never asks a vision connector: the provider
config it starts the app with is empty, and the connector's entry point is
replaced with one that raises. Where the catalog kept the reading a
correction replaced (schema 65, `page_panel_history`), that reading is
measured too, under its own name, which is how the vision tier's agreement
is reported without spending a key.

Two levels of agreement are reported, because a reader's fixes encode intent
as well as geometry:

- box agreement: each of the reader's boxes has one automatic box like it;
- group agreement: each of the reader's boxes is one automatic box, or the
  union of several -- the reader grouped what the machine split, which is
  a choice about reading, not a detection failure.

Run it in a throwaway container from the build tree, the catalog copied
somewhere writable and the library read-only; nothing it writes goes near
the real config:

    docker run --rm --network none -v ~/flipparr-build:/src:ro -v /volume/comics:/comics:ro \\
      -v "$WORK":/work -w /src --entrypoint python3 flipparr:local -B tools/panel_benchmark.py \\
      --catalog /work/flipparr.db --out /work/panels --model /work/panels.onnx

The pages it writes (composites, masks) name the owner's comics: keep `--out`
outside the repository.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import statistics
import sys
import time
from pathlib import Path
from typing import Any

SOURCE = Path(__file__).resolve().parent.parent
MATCH_IOU = 0.5          # a reader's box and an automatic box are "the same panel"
EXACT_IOU = 0.7          # ... and close enough to count as exact structure
INSIDE = 0.6             # share of a box inside another for merge/split counting
GROUP_INSIDE = 0.8       # share of an automatic box inside a reader's box to be part of its group
GROUP_COVER = 0.8        # share of a reader's box its group of automatic boxes must cover


# ---- geometry on normalised boxes --------------------------------------------

def _inter(a: dict[str, Any], b: dict[str, Any]) -> float:
    iw = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    ih = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    return iw * ih


def _area(a: dict[str, Any]) -> float:
    return max(0.0, a["w"]) * max(0.0, a["h"])


def iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    i = _inter(a, b)
    u = _area(a) + _area(b) - i
    return i / u if u > 0 else 0.0


def inside(a: dict[str, Any], b: dict[str, Any]) -> float:
    """The share of `a` that lies inside `b`."""
    area = _area(a)
    return _inter(a, b) / area if area > 0 else 0.0


def _ordered(panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """In reading order: by the `order` a person or a model gave, else row-major."""
    if panels and all(isinstance(panel.get("order"), int) for panel in panels):
        return sorted(panels, key=lambda panel: int(panel["order"]))
    sys.path.insert(0, str(SOURCE))
    import page_panels  # noqa: PLC0415 -- the app's own ordering, so the comparison reads as the reader does

    return page_panels.order_panels(list(panels))


def compare_page(manual: list[dict[str, Any]], auto: list[dict[str, Any]]) -> dict[str, Any]:
    """How an automatic reading differs from a reader's, on one page.

    Boxes are matched greedily by IoU in the reader's order. Then:
    merges -- an automatic box holding most of two or more of the reader's;
    splits -- a reader's box holding most of two or more automatic ones;
    order inversions -- pairs of matched panels the machine reads the other
    way round; edge deviation -- the mean distance, per side, between
    matched boxes, as a share of the page.
    """
    manual = _ordered(manual)
    auto = _ordered(auto)
    pairs: list[tuple[int, int, float]] = []
    used: set[int] = set()
    for i, m in enumerate(manual):
        best = max(((iou(m, a), j) for j, a in enumerate(auto) if j not in used), default=(0.0, -1))
        # Strictly above: two equal panels merged into one give exactly 0.5
        # against either, and that is a merge, not a match.
        if best[0] > MATCH_IOU:
            pairs.append((i, best[1], best[0]))
            used.add(best[1])
    merges = sum(1 for a in auto if sum(1 for m in manual if inside(m, a) >= INSIDE) >= 2)
    splits = sum(1 for m in manual if sum(1 for a in auto if inside(a, m) >= INSIDE) >= 2)
    inversions = sum(1 for x in range(len(pairs)) for y in range(x + 1, len(pairs)) if pairs[x][1] > pairs[y][1])
    edges = [
        (abs(manual[i]["x"] - auto[j]["x"]) + abs(manual[i]["y"] - auto[j]["y"])
         + abs(manual[i]["x"] + manual[i]["w"] - auto[j]["x"] - auto[j]["w"])
         + abs(manual[i]["y"] + manual[i]["h"] - auto[j]["y"] - auto[j]["h"])) / 4
        for i, j, _ in pairs
    ]
    # Group agreement: a reader's box is "agreed" when one automatic box is
    # like it, or when the automatic boxes mostly inside it together cover it.
    grouped = 0
    for i, m in enumerate(manual):
        if any(pair[0] == i and pair[2] >= EXACT_IOU for pair in pairs):
            grouped += 1
            continue
        members = [a for a in auto if inside(a, m) >= GROUP_INSIDE]
        if len(members) >= 2:
            covered = sum(_inter(a, m) for a in members) / (_area(m) or 1)
            if covered >= GROUP_COVER:
                grouped += 1
    exact = (
        bool(manual) and len(pairs) == len(manual) == len(auto)
        and all(score >= EXACT_IOU for _i, _j, score in pairs) and inversions == 0
    )
    return {
        "manual": len(manual), "auto": len(auto), "matched": len(pairs),
        "manualUnmatched": len(manual) - len(pairs), "autoUnmatched": len(auto) - len(pairs),
        "merges": merges, "splits": splits, "orderInversions": inversions,
        "edgeDeviation": round(statistics.fmean(edges), 4) if edges else None,
        "boxAgreement": bool(manual) and len(pairs) == len(manual) and len(auto) == len(manual),
        "groupAgreement": bool(manual) and grouped == len(manual) and all(
            any(inside(a, m) >= GROUP_INSIDE or iou(a, m) >= MATCH_IOU for m in manual) for a in auto),
        "exactStructure": exact,
    }


def summarise(pages: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Aggregate one reading's comparisons (`key` names it in each page)."""
    rows = [page[key] for page in pages if page.get(key)]
    if not rows:
        return {"pages": 0}
    devs = [row["edgeDeviation"] for row in rows if row["edgeDeviation"] is not None]
    return {
        "pages": len(rows),
        "exactStructure": sum(1 for row in rows if row["exactStructure"]),
        "boxAgreement": sum(1 for row in rows if row["boxAgreement"]),
        "groupAgreement": sum(1 for row in rows if row["groupAgreement"]),
        "unsegmented": sum(1 for row in rows if row["auto"] == 0),
        "autoFewer": sum(1 for row in rows if 0 < row["auto"] < row["manual"]),
        "autoMore": sum(1 for row in rows if row["auto"] > row["manual"]),
        "pagesWithMerges": sum(1 for row in rows if row["merges"]),
        "pagesWithSplits": sum(1 for row in rows if row["splits"]),
        "pagesWithInversions": sum(1 for row in rows if row["orderInversions"]),
        "manualUnmatched": sum(row["manualUnmatched"] for row in rows),
        "autoUnmatched": sum(row["autoUnmatched"] for row in rows),
        "meanEdgeDeviation": round(statistics.fmean(devs), 4) if devs else None,
    }


# ---- the run -------------------------------------------------------------------

def _point_app_at(folder: Path, catalog: Path, model: Path | None) -> None:
    """Every file the app would write goes under `folder`; the catalog it
    opens is a copy; no provider is configured, so no connector can be asked."""
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(catalog, folder / "flipparr.db")
    for name in ("providers.json", "services.json", "settings.json"):
        (folder / name).write_text("{}")
    os.environ.update({
        "FLIPPARR_DATABASE": str(folder / "flipparr.db"),
        "FLIPPARR_PROVIDER_CONFIG": str(folder / "providers.json"),
        "FLIPPARR_ACQUISITION_CONFIG": str(folder / "services.json"),
        "FLIPPARR_SETTINGS_CONFIG": str(folder / "settings.json"),
        "FLIPPARR_AUTH_CONFIG": str(folder / "auth.json"),
        "FLIPPARR_TEMP_DIR": str(folder),
        "FLIPPARR_PANEL_MODEL": str(model) if model else "",
    })
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(key, None)


def _never(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("the panel benchmark never asks a vision model")


def _draw(image: Any, manual: list[dict[str, Any]], auto: list[dict[str, Any]]) -> Any:
    from PIL import ImageDraw

    canvas = image.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas)
    width, height = canvas.size
    stroke = max(2, width // 300)
    for number, box in enumerate(_ordered(auto), start=1):
        rect = (box["x"] * width, box["y"] * height, (box["x"] + box["w"]) * width, (box["y"] + box["h"]) * height)
        draw.rectangle(rect, outline=(255, 40, 40), width=stroke)
        draw.text((rect[0] + 4, rect[1] + 4), f"A{number}", fill=(255, 40, 40))
    for number, box in enumerate(_ordered(manual), start=1):
        rect = (box["x"] * width + stroke, box["y"] * height + stroke,
                (box["x"] + box["w"]) * width - stroke, (box["y"] + box["h"]) * height - stroke)
        draw.rectangle(rect, outline=(0, 220, 0), width=stroke)
        draw.text((rect[0] + 4, rect[1] + 4 + 6 * stroke), f"M{number}", fill=(0, 220, 0))
    return canvas


def run(args: argparse.Namespace) -> dict[str, Any]:
    from PIL import Image

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    _point_app_at(out / "scratch", args.catalog, args.model)
    sys.path.insert(0, str(SOURCE))
    import app  # noqa: PLC0415 -- after the environment points it at the scratch copy
    import page_panels  # noqa: PLC0415

    app.ask_vision_model = _never  # type: ignore[assignment]
    if app.vision_model_ready():
        raise SystemExit("a vision connector is configured in the scratch app; refusing to run")
    store = app.catalog_store()
    session = app.panel_model_session()
    rows = store.manual_page_panels_with_history()
    if args.only_file:
        rows = [row for row in rows if row["fileId"] in set(args.only_file)]
    pages: list[dict[str, Any]] = []
    started = time.monotonic()
    (out / "composites").mkdir(exist_ok=True)
    if args.masks:
        (out / "masks").mkdir(exist_ok=True)
    for row in rows:
        file_id, member = row["fileId"], row["member"]
        page: dict[str, Any] = {"fileId": file_id, "member": member, "manualCount": len(row["panels"])}
        try:
            path = store.library_file_path(file_id)
            if args.library_map:
                old, new = args.library_map.split("=", 1)
                path = Path(str(path).replace(old, new, 1))
            members = app.cached_page_members(path)
            index = members.index(member)
            direction = store.file_reading_direction(file_id)
            with Image.open(io.BytesIO(app.render_file_page(file_id, index))) as image:
                image.load()
                if page_panels.is_spread(*image.size):
                    page["spread"] = True
                    pages.append(page)
                    continue
                page["background"] = "white" if page_panels._border_level(image.convert("L")) == 255 else "dark"
                with Image.open(io.BytesIO(app.render_file_page(file_id, index, "backdrop"))) as detail:
                    detail.load()
                    local = app._read_page_panels(image, session, False, False, direction, detail)
                cut_only = app._read_page_panels(image, None, False, False, direction)
                page["local"] = compare_page(row["panels"], local["panels"])
                page["localSource"] = local["source"]
                page["cut"] = compare_page(row["panels"], cut_only["panels"])
                if row.get("before") is not None:
                    page["before"] = compare_page(row["panels"], row["before"])
                    page["beforeSource"] = row["beforeSource"]
                if args.masks:
                    page_panels.page_mask(image).save(out / "masks" / f"{file_id}-{index:03d}.png")
                disagree = not page["local"]["boxAgreement"]
                if disagree and len(os.listdir(out / "composites")) < args.composites:
                    _draw(image, row["panels"], local["panels"]).save(
                        out / "composites" / f"{file_id}-{index:03d}.jpg", quality=80)
        except Exception as exc:  # noqa: BLE001 -- one unreadable page must not end the run
            page["error"] = f"{type(exc).__name__}: {exc}"[:200]
        pages.append(page)
    compared = [page for page in pages if "local" in page]
    report = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "build": os.environ.get("FLIPPARR_BUILD", ""),
        "model": bool(session), "seconds": round(time.monotonic() - started, 1),
        "manualPages": len(rows), "spreads": sum(1 for page in pages if page.get("spread")),
        "errors": sum(1 for page in pages if "error" in page),
        "local": summarise(compared, "local"),
        "cutOnly": summarise(compared, "cut"),
        "before": summarise(compared, "before"),
        "byBackground": {
            kind: summarise([page for page in compared if page.get("background") == kind], "local")
            for kind in ("white", "dark")
        },
        "byFile": {
            str(file_id): summarise([page for page in compared if page["fileId"] == file_id], "local")
            for file_id in sorted({page["fileId"] for page in compared})
        },
        "pages": pages,
    }
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    with (out / "pages.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["fileId", "member", "background", "manual", "auto", "matched", "merges", "splits",
                         "inversions", "edgeDeviation", "boxAgreement", "groupAgreement", "exactStructure"])
        for page in compared:
            local = page["local"]
            writer.writerow([page["fileId"], page["member"], page.get("background"), local["manual"], local["auto"],
                             local["matched"], local["merges"], local["splits"], local["orderInversions"],
                             local["edgeDeviation"], local["boxAgreement"], local["groupAgreement"], local["exactStructure"]])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--catalog", required=True, type=Path, help="a copy of the catalog database (it is copied again, and opened writable)")
    parser.add_argument("--out", required=True, type=Path, help="where the report, composites and masks go (outside the repository)")
    parser.add_argument("--model", type=Path, help="the ONNX panel detector; without it only the cut and the leftover pass run")
    parser.add_argument("--library-map", help="OLD=NEW: rewrite the library's path prefix when it is mounted elsewhere")
    parser.add_argument("--only-file", type=int, action="append", help="measure only this file id (repeatable)")
    parser.add_argument("--masks", action="store_true", help="also write the ink mask of every page")
    parser.add_argument("--composites", type=int, default=40, help="how many disagreeing pages to draw")
    args = parser.parse_args()
    report = run(args)
    local, cut = report["local"], report["cutOnly"]
    print(f"{report['manualPages']} hand-fixed pages: {local.get('pages', 0)} compared, "
          f"{report['spreads']} spreads skipped, {report['errors']} errors, {report['seconds']}s")
    for name, part in (("local tiers", local), ("cut only", cut), ("stored before", report["before"])):
        if part.get("pages"):
            print(f"  {name:13} exact {part['exactStructure']}/{part['pages']}  box {part['boxAgreement']}  group {part['groupAgreement']}"
                  f"  unsegmented {part['unsegmented']}  fewer {part['autoFewer']}  more {part['autoMore']}"
                  f"  merges {part['pagesWithMerges']}  splits {part['pagesWithSplits']}  inversions {part['pagesWithInversions']}"
                  f"  edge {part['meanEdgeDeviation']}")
    for kind, part in report["byBackground"].items():
        if part.get("pages"):
            print(f"  {kind:13} pages {part['pages']}  exact {part['exactStructure']}  group {part['groupAgreement']}  unsegmented {part['unsegmented']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
