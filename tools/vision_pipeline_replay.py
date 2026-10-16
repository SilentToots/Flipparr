"""Vision pipeline replay: what Flipparr does to a model's boxes, tried other ways, at no cost.

`tools/vision_model_test.py` (2026-10-16) found that the step after the
model -- merge, snap each edge to a gutter, shrink to the ink, refuse an
answer the page cannot vouch for -- turns Sonnet 5.5's boxes from 0.80
agreement with the owner's hand fixes to 0.61. This replays the boxes the
models already drew (no model is asked) through variants of that step and
scores each, two ways:

- against the owner's fixes, on the pages the model test asked about;
- against what the owner ACCEPTED: pages of the comics he read and fixed
  in, that he left as the vision answer had them -- a variant that moves
  those is changing pages he was content with, which the fixes alone
  cannot show.

Run inside the production container on a COPY of the catalog, with the
model test's log beside it:

    docker exec flipparr python3 /config/tmp/vmr/tools/vision_pipeline_replay.py \\
      --catalog /config/tmp/vmr/flipparr.db --log /config/tmp/vmr/vmt.log --out /config/tmp/vmr/out

It asks nothing of any connector: the vision entry point is replaced with
one that raises.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

SOURCE = Path(os.environ.get("FLIPPARR_SOURCE", "/app"))
# The model test's configurations, in the order it asked them (its CONFIGS).
TESTED = ["sonnet-5", "sonnet-5-no-thinking", "sonnet-5.5", "sonnet-5.5-no-thinking", "haiku-5.5", "haiku-5.5-no-thinking"]

# Each variant: the snap reach (share of the page's side), the most a snap
# may move an edge (None: as far as the reach), whether a panel shrinks to
# the ink inside it, whether an answer with too many unvouched edges is
# refused, and whether a snapped box that swallows a neighbour or more than
# doubles or halves is put back as the model drew it.
VARIANTS: dict[str, dict[str, Any]] = {
    "today": {"reach": 0.10, "move": None, "shrink": True, "refuse": True, "guard": False},
    "model boxes, merged": {"raw": True},
    "reach 5%": {"reach": 0.05, "move": None, "shrink": True, "refuse": True, "guard": False},
    "reach 3%": {"reach": 0.03, "move": None, "shrink": True, "refuse": True, "guard": False},
    "no shrink": {"reach": 0.10, "move": None, "shrink": False, "refuse": True, "guard": False},
    "no refusal": {"reach": 0.10, "move": None, "shrink": True, "refuse": False, "guard": False},
    # The two refusals apart: unvouched edges (most of them) and ink left
    # uncovered after the leftover pass (the panel view would skip art).
    "no edge refusal, ink check kept": {"reach": 0.10, "move": None, "shrink": True, "refuse": False, "inkCheck": True, "guard": False},
    "guard": {"reach": 0.10, "move": None, "shrink": True, "refuse": True, "guard": True},
    "reach 3%, no refusal": {"reach": 0.03, "move": None, "shrink": True, "refuse": False, "guard": False},
    "reach 3%, no shrink, no refusal": {"reach": 0.03, "move": None, "shrink": False, "refuse": False, "guard": False},
    "reach 5%, guard, no refusal": {"reach": 0.05, "move": None, "shrink": True, "refuse": False, "guard": True},
    "reach 3%, guard, no refusal": {"reach": 0.03, "move": None, "shrink": True, "refuse": False, "guard": True},
}


def _point_app_at(folder: Path, catalog: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("providers.json", "services.json", "settings.json"):
        (folder / name).write_text("{}")
    os.environ.update({
        "FLIPPARR_DATABASE": str(catalog),
        "FLIPPARR_PROVIDER_CONFIG": str(folder / "providers.json"),
        "FLIPPARR_ACQUISITION_CONFIG": str(folder / "services.json"),
        "FLIPPARR_SETTINGS_CONFIG": str(folder / "settings.json"),
        "FLIPPARR_AUTH_CONFIG": str(folder / "auth.json"),
        "FLIPPARR_TEMP_DIR": str(folder),
    })
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(key, None)


def _logged_boxes(log: Path) -> list[list[dict[str, float]]]:
    """The boxes each call drew, in the order the model test asked."""
    drawn = []
    for line in log.read_text().splitlines():
        if line.startswith("{") and '"vision_boxes"' in line:
            event = json.loads(line)
            if event.get("event") == "vision_boxes":
                drawn.append(event.get("boxes") or [])
    return drawn


def _match_blocks(logged: list[list[dict[str, float]]], candidates: list[dict[str, Any]], score: Any) -> list[tuple[dict[str, Any], int]]:
    """Each run of six answers, matched to the hand-fixed page it was asked about.

    The 2026-10-16 run logged its answers without naming the page, and the
    catalog's list of fixed pages came back in another order the second
    time; answers are matched to pages by how well they fit, each page once.
    A block that fits no page well is dropped.
    """
    size = len(TESTED)
    pairs = []
    for block in range(len(logged) // size):
        answers = logged[block * size:(block + 1) * size]
        for index, page in enumerate(candidates):
            fit = statistics.fmean(score(page["panels"], answer) for answer in answers)
            pairs.append((fit, block, index))
    taken_blocks: set[int] = set()
    taken_pages: set[int] = set()
    matched = []
    for fit, block, index in sorted(pairs, reverse=True):
        if fit < 0.5 or block in taken_blocks or index in taken_pages:
            continue
        taken_blocks.add(block)
        taken_pages.add(index)
        matched.append((candidates[index], block))
    return sorted(matched, key=lambda pair: pair[1])


def run(args: argparse.Namespace) -> dict[str, Any]:
    from PIL import Image

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    _point_app_at(out / "scratch", args.catalog)
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(SOURCE))
    sys.path.insert(0, str(here))
    import app  # noqa: PLC0415
    import page_panels as pp  # noqa: PLC0415
    from panel_benchmark import compare_page, iou  # noqa: PLC0415
    from vision_model_test import _sample  # noqa: PLC0415

    def never(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("the replay never asks a vision model")

    app.ask_vision_model = never  # type: ignore[assignment]
    if os.path.realpath(app.catalog_store().database_path) != os.path.realpath(args.catalog):
        raise SystemExit("the app did not open the catalog copy; refusing to run")

    def settle(mask: Any, drawn: tuple, region: tuple, reach: tuple, v: dict) -> tuple[tuple, int]:
        width, height = mask.size
        edges = list(region)
        doubted = 0
        for side in range(4):
            vertical = side % 2 == 0
            low = side < 2
            limit = width if vertical else height
            if region[side] == (0 if low else limit):
                continue
            span = (drawn[1], drawn[3]) if vertical else (drawn[0], drawn[2])
            line = pp._bare_line_near(mask, side, drawn[side], span, reach[0] if vertical else reach[1])
            if line is not None and (v["move"] is None or abs(line - drawn[side]) <= v["move"] * limit):
                edges[side] = line
                continue
            if line is None and pp._ink_along(mask, side, region[side] - (1 if low else 0), span) <= pp.VISION_LINE_INK:
                continue
            edges[side] = max(0, min(limit, drawn[side]))
            doubted += 1
        x0, y0, x1, y1 = edges
        if x1 - x0 < 2 or y1 - y0 < 2:
            return (0, 0, 0, 0), doubted
        if not v["shrink"]:
            return (x0, y0, x1, y1), doubted
        bbox = mask.crop((x0, y0, x1, y1)).getbbox()
        if bbox is None:
            return (0, 0, 0, 0), doubted
        return (x0 + bbox[0], y0 + bbox[1], x0 + bbox[2], y0 + bbox[3]), doubted

    def refine(mask: Any, boxes: list[dict[str, float]], v: dict) -> list[dict[str, float]] | None:
        width, height = mask.size
        min_area = width * height * pp.PANEL_MIN_AREA
        reach = (int(width * v["reach"]), int(height * v["reach"]))
        panels: list[tuple] = []
        unverified = 0
        for box in boxes:
            drawn = (int(round(box["x"] * width)), int(round(box["y"] * height)),
                     int(round((box["x"] + box["w"]) * width)), int(round((box["y"] + box["h"]) * height)))
            region = (max(0, drawn[0] - reach[0]), max(0, drawn[1] - reach[1]),
                      min(width, drawn[2] + reach[0]), min(height, drawn[3] + reach[1]))
            if region[2] - region[0] < 2 or region[3] - region[1] < 2:
                return None
            settled, doubted = settle(mask, drawn, region, reach, v)
            unverified += doubted
            if pp._area(settled) < min_area:
                continue
            if v["guard"]:
                ratio = pp._area(settled) / max(1, pp._area(drawn))
                swallows = any(pp._area(pp._clip(settled, other)) >= 0.3 * max(1, pp._area(other))
                               for other in (
                                   (int(round(o["x"] * width)), int(round(o["y"] * height)),
                                    int(round((o["x"] + o["w"]) * width)), int(round((o["y"] + o["h"]) * height)))
                                   for o in boxes if o is not box))
                if swallows or ratio < 0.5 or ratio > 2:
                    settled = drawn
            if all(pp._iou(settled, kept) < 0.5 for kept in panels):
                panels.append(settled)
        if not panels or (v["refuse"] and unverified > max(1, pp.VISION_UNVERIFIED_MAX * 4 * len(boxes))):
            return None
        return [{"x": x0 / width, "y": y0 / height, "w": (x1 - x0) / width, "h": (y1 - y0) / height} for x0, y0, x1, y1 in panels]

    def pipeline(drawn: list[dict[str, float]], mask: Any, v: dict) -> list[dict[str, float]] | None:
        """vision_panels after the model has answered, with the variant's rules."""
        if not drawn:
            return None
        boxes = pp.merge_overlapping(drawn)
        if len(boxes) <= 1:
            return [dict(pp.WHOLE_PAGE)]
        if not pp.accept_vision_boxes(boxes):
            return None
        if v.get("raw"):
            return boxes
        refined = refine(mask, boxes, v)
        if refined is None:
            return None
        refined = pp.add_leftover_panels(mask, refined)
        if (v["refuse"] or v.get("inkCheck")) and pp.ink_outside(mask, refined) > pp.INK_UNCOVERED_MAX:
            return None
        return refined

    def mean_iou(a: list[dict], b: list[dict]) -> float:
        return statistics.fmean(max((iou(x, y) for y in b), default=0.0) for x in a) if a else 0.0

    store = app.catalog_store()
    renders: dict[tuple[int, str], tuple[Any, list]] = {}

    def page(file_id: int, member: str) -> tuple[Any, list] | None:
        """The page's mask and the local tiers' reading (what a refused answer falls back to)."""
        key = (file_id, member)
        if key not in renders:
            path = store.library_file_path(file_id)
            index = app.cached_page_members(path).index(member)
            direction = store.file_reading_direction(file_id)
            with Image.open(io.BytesIO(app.render_file_page(file_id, index))) as image:
                image.load()
                if pp.is_spread(*image.size):
                    renders[key] = None  # type: ignore[assignment]
                    return None
                with Image.open(io.BytesIO(app.render_file_page(file_id, index, "backdrop"))) as detail:
                    detail.load()
                    renders[key] = (pp.page_mask(detail), app._read_page_panels(image, None, False, False, direction)["panels"])
        return renders[key]

    started = time.monotonic()
    # 1. The model test's pages, its boxes back in page order.
    logged = _logged_boxes(args.log)
    rows = [row for row in store.manual_page_panels_with_history() if row["panels"]]
    candidates = [row for row in _sample(rows, args.pages + 30) if page(row["fileId"], row["member"]) is not None]
    matched = _match_blocks(logged, candidates, mean_iou)
    print(f"{len(matched)} of {len(logged) // len(TESTED)} answer blocks matched to a page", file=sys.stderr)
    results: dict[str, dict[str, list[float]]] = {name: {} for name in VARIANTS}
    for row, block in matched:
        mask, local = page(row["fileId"], row["member"])
        for c_index, config in enumerate(TESTED):
            drawn = logged[block * len(TESTED) + c_index]
            for name, variant in VARIANTS.items():
                shown = pipeline(drawn, mask, variant) or local
                cmp = compare_page(row["panels"], shown)
                results[name].setdefault(config, []).append((mean_iou(row["panels"], shown), cmp["exactStructure"], cmp["groupAgreement"]))

    # 2. Pages he accepted: vision answers kept in comics he fixed pages in,
    # not fixed, no later than the last page he fixed there (so he read them).
    fixed = {(r["fileId"], r["member"]) for r in rows}
    last_fixed: dict[int, str] = {}
    for file_id, member in fixed:
        last_fixed[file_id] = max(last_fixed.get(file_id, ""), member)
    accepted = []
    for answer in store.vision_answers(limit=100000):
        key = (answer["fileId"], answer["member"])
        if (answer.get("outcome") == "kept" and answer["fileId"] in last_fixed and key not in fixed
                and answer["member"] <= last_fixed[answer["fileId"]] and not answer.get("half")):
            accepted.append(answer)
    seen = set()
    accepted = [a for a in accepted if not ((a["fileId"], a["member"]) in seen or seen.add((a["fileId"], a["member"])))]
    moved: dict[str, list[float]] = {name: [] for name in VARIANTS}
    for answer in accepted[: args.accepted]:
        got = page(answer["fileId"], answer["member"])
        if got is None:
            continue
        mask, local = got
        kept = answer.get("kept") or []
        for name, variant in VARIANTS.items():
            shown = pipeline(answer.get("drawn") or [], mask, variant) or local
            moved[name].append(min(mean_iou(kept, shown), mean_iou(shown, kept)) if kept else 0.0)
    # And on the fixed pages: how many came out refused (the local reading shown).
    refused_counts = {name: sum(1 for row, block in matched for c in (2,) if pipeline(logged[block * len(TESTED) + c], page(row["fileId"], row["member"])[0], VARIANTS[name]) is None)
                      for name in VARIANTS}

    summary = {}
    for name in VARIANTS:
        summary[name] = {
            config: {"iou": round(statistics.fmean(x[0] for x in values), 3), "exact": sum(x[1] for x in values),
                     "group": sum(x[2] for x in values), "pages": len(values)}
            for config, values in results[name].items()
        }
        summary[name]["refusedSonnet55"] = refused_counts[name]
        summary[name]["accepted"] = {
            "pages": len(moved[name]),
            "unchanged": sum(1 for x in moved[name] if x >= 0.95),
            "meanAgreement": round(statistics.fmean(moved[name]), 3) if moved[name] else None,
        }
    report = {"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "seconds": round(time.monotonic() - started),
              "fixedPages": len(matched), "acceptedPages": len(moved["today"]), "summary": summary}
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--catalog", required=True, type=Path, help="a COPY of the catalog database")
    parser.add_argument("--log", required=True, type=Path, help="the model test's log (its vision_boxes lines)")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--pages", type=int, default=90, help="the model test's --pages")
    parser.add_argument("--accepted", type=int, default=400, help="how many accepted pages to check for changes")
    args = parser.parse_args()
    report = run(args)
    print(f"{report['fixedPages']} fixed pages, {report['acceptedPages']} accepted pages, {report['seconds']}s")
    for name, row in report["summary"].items():
        acc = row["accepted"]
        line = "  ".join(f"{config} {row[config]['iou']}" for config in ("sonnet-5", "sonnet-5.5", "haiku-5.5") if config in row)
        print(f"  {name:34} {line}  | s5.5 exact {row.get('sonnet-5.5', {}).get('exact')} refused {row['refusedSonnet55']}  | accepted unchanged "
              f"{acc['unchanged']}/{acc['pages']} (agreement {acc['meanAgreement']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
