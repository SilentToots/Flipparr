"""Vision model test: each Claude model and setting, on the pages a person fixed by hand.

The panel benchmark (tools/panel_benchmark.py) measures the local tiers and
never asks a model. This asks one -- on purpose, with the owner's say-so,
because it spends their API key -- and so it refuses to run without
`--spend`. Each hand-fixed page is sent to every configuration exactly as
the reader sends a page (`app.vision_panels`: the same render, prompt,
gutter correction and refusals), with no examples, as an overnight page
goes; and it is scored against the person's fix with the benchmark's own
measures, for what the model drew and for what Flipparr kept. A refused or
unreadable answer is scored as what the reader would then show: the local
tiers' reading of the page.

Asked for by the owner on 2026-10-16 ("is one model better than another
specifically in Claude?"): Sonnet 5 as it runs, Sonnet 5 without thinking,
Sonnet 5.5 with and without, and Haiku 5.5, at about $2 for ~90 pages.

Run it inside the production container on a COPY of the catalog -- nothing
it writes reaches the real one; the provider config is read for the key and
never written:

    docker exec flipparr python3 /config/tmp/vmt/tools/vision_model_test.py --spend \\
      --catalog /config/tmp/vmt/flipparr.db --out /config/tmp/vmt/out --pages 90

with this file and tools/panel_benchmark.py (whose measures it uses) copied
to /config/tmp/vmt/tools -- the image carries no tools.

The output names the owner's comics: keep it outside the repository.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

SOURCE = Path(os.environ.get("FLIPPARR_SOURCE", "/app"))

CONFIGS: dict[str, tuple[str, dict[str, Any]]] = {
    "sonnet-5": ("claude-sonnet-5", {}),
    "sonnet-5-no-thinking": ("claude-sonnet-5", {"thinking": {"type": "disabled"}}),
    "sonnet-5.5": ("claude-sonnet-5-5", {}),
    "sonnet-5.5-no-thinking": ("claude-sonnet-5-5", {"thinking": {"type": "between_tools"}}),
    "haiku-5.5": ("claude-haiku-5-5", {}),
    # At its default it can spend the whole 1,024-token answer thinking (the
    # first dry run did): thinking off is the fair test of what it sees.
    "haiku-5.5-no-thinking": ("claude-haiku-5-5", {"thinking": {"type": "disabled"}}),
}


def _point_app_at(folder: Path, catalog: Path) -> None:
    """The app's catalog, settings and temp files are scratch copies; its
    provider config stays the real one, read only, for the key."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("services.json", "settings.json"):
        (folder / name).write_text("{}")
    os.environ.update({
        "FLIPPARR_DATABASE": str(catalog),
        "FLIPPARR_ACQUISITION_CONFIG": str(folder / "services.json"),
        "FLIPPARR_SETTINGS_CONFIG": str(folder / "settings.json"),
        "FLIPPARR_AUTH_CONFIG": str(folder / "auth.json"),
        "FLIPPARR_TEMP_DIR": str(folder),
    })


def _sample(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Up to `limit` pages, taken a page from each comic in turn, so one long
    comic does not stand for the library."""
    by_file: dict[int, list[dict[str, Any]]] = {}
    for row in sorted(rows, key=lambda row: (row["fileId"], row["member"])):
        by_file.setdefault(row["fileId"], []).append(row)
    picked: list[dict[str, Any]] = []
    while len(picked) < limit and any(by_file.values()):
        for file_id in sorted(by_file):
            if by_file[file_id] and len(picked) < limit:
                picked.append(by_file[file_id].pop(0))
    return picked


def _mean_iou(manual: list[dict[str, Any]], auto: list[dict[str, Any]], iou: Any) -> float:
    if not manual:
        return 0.0
    return statistics.fmean(max((iou(m, a) for a in auto), default=0.0) for m in manual)


def run(args: argparse.Namespace) -> dict[str, Any]:
    from PIL import Image

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    _point_app_at(out / "scratch", args.catalog)
    sys.path.insert(0, str(SOURCE))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import app  # noqa: PLC0415 -- after the environment points it at the copy
    import page_panels  # noqa: PLC0415
    from panel_benchmark import compare_page, iou  # noqa: PLC0415

    if os.path.realpath(app.catalog_store().database_path) != os.path.realpath(args.catalog):
        raise SystemExit("the app did not open the catalog copy; refusing to run")
    if app.vision_provider() != "anthropic":
        raise SystemExit("Claude is not the configured vision connector")

    # What each call drew and used, caught on the way past.
    calls: list[dict[str, Any]] = []
    drawn: list[list[dict[str, float]]] = []
    real_usage, real_request, real_log = app._vision_usage, app._vision_request, app.log_event
    extra: dict[str, Any] = {}

    def usage(provider: str, payload: dict[str, Any]) -> Any:
        found = real_usage(provider, payload)
        stop = payload.get("stop_reason") if isinstance(payload, dict) else None
        calls.append({"input": found[0], "output": found[1], "cost": found[2], "model": found[3], "stop": stop})
        return found

    def request(provider: str, credential: str, image: bytes, prompt: str, examples: Any = None) -> Any:
        url, body = real_request(provider, credential, image, prompt, examples)
        return url, json.dumps({**json.loads(body), **extra}).encode()

    def log(event: str, **fields: Any) -> None:
        if event == "vision_boxes":
            drawn.append(list(fields.get("boxes") or []))
        real_log(event, **fields)

    app._vision_usage, app._vision_request, app.log_event = usage, request, log

    store = app.catalog_store()
    rows = [row for row in store.manual_page_panels_with_history() if row["panels"]]
    pages: list[dict[str, Any]] = []
    started = time.monotonic()
    for row in _sample(rows, args.pages):
        file_id, member = row["fileId"], row["member"]
        page: dict[str, Any] = {"fileId": file_id, "member": member, "manual": len(row["panels"]), "configs": {}}
        try:
            path = store.library_file_path(file_id)
            index = app.cached_page_members(path).index(member)
            direction = store.file_reading_direction(file_id)
            with Image.open(io.BytesIO(app.render_file_page(file_id, index))) as image:
                image.load()
                if page_panels.is_spread(*image.size):
                    continue
                detail_bytes = app.render_file_page(file_id, index, "backdrop")
                with Image.open(io.BytesIO(detail_bytes)) as detail:
                    detail.load()
                    local = app._read_page_panels(image, None, False, False, direction)
                    mask = page_panels.page_mask(detail)
                    for name in args.configs:
                        model, fields = CONFIGS[name]
                        app.VISION_MODEL = model
                        extra.clear()
                        extra.update(fields)
                        calls.clear()
                        drawn.clear()
                        try:
                            boxes = app.vision_panels(detail_bytes, detail.size[0], detail.size[1], direction, mask)
                        except app.VisionUnavailable as exc:
                            page["configs"][name] = {"error": str(exc)[:200]}
                            continue
                        shown = boxes if boxes else local["panels"]
                        model_drew = drawn[-1] if drawn else []
                        call = calls[-1] if calls else {}
                        page["configs"][name] = {
                            "outcome": "kept" if boxes and len(boxes) > 1 else "whole" if boxes else "refused",
                            "drew": compare_page(row["panels"], model_drew) if model_drew else None,
                            "shown": compare_page(row["panels"], shown),
                            "iouDrew": round(_mean_iou(row["panels"], model_drew, iou), 3),
                            "iouShown": round(_mean_iou(row["panels"], shown, iou), 3),
                            **{key: call.get(key) for key in ("input", "output", "cost", "stop")},
                        }
        except Exception as exc:  # noqa: BLE001 -- one unreadable page must not end the run
            page["error"] = f"{type(exc).__name__}: {exc}"[:200]
        pages.append(page)
        print(f"{len(pages)} pages, {round(time.monotonic() - started)}s", file=sys.stderr, flush=True)

    summary: dict[str, Any] = {}
    for name in args.configs:
        results = [page["configs"][name] for page in pages if name in page.get("configs", {}) and "shown" in page["configs"][name]]
        if not results:
            continue
        costs = [result["cost"] for result in results if result.get("cost") is not None]
        summary[name] = {
            "pages": len(results),
            "kept": sum(result["outcome"] == "kept" for result in results),
            "whole": sum(result["outcome"] == "whole" for result in results),
            "refused": sum(result["outcome"] == "refused" for result in results),
            "errors": sum(1 for page in pages if "error" in page.get("configs", {}).get(name, {})),
            "cutOff": sum(result.get("stop") == "max_tokens" for result in results),
            "iouShown": round(statistics.fmean(result["iouShown"] for result in results), 3),
            "iouDrew": round(statistics.fmean(result["iouDrew"] for result in results), 3),
            "exactShown": sum(result["shown"]["exactStructure"] for result in results),
            "groupShown": sum(result["shown"]["groupAgreement"] for result in results),
            "boxShown": sum(result["shown"]["boxAgreement"] for result in results),
            "perPage": round(statistics.fmean(costs), 5) if costs else None,
            "spent": round(sum(costs), 3),
            "outputTokens": round(statistics.fmean(result["output"] or 0 for result in results)),
        }
    report = {"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "seconds": round(time.monotonic() - started),
              "configs": {name: list(CONFIGS[name]) for name in args.configs}, "summary": summary, "pages": pages}
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    with (out / "pages.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["fileId", "member", "manual", "config", "outcome", "iouDrew", "iouShown", "exactShown", "input", "output", "cost", "stop"])
        for page in pages:
            for name, result in page.get("configs", {}).items():
                if "shown" in result:
                    writer.writerow([page["fileId"], page["member"], page["manual"], name, result["outcome"], result["iouDrew"],
                                     result["iouShown"], result["shown"]["exactStructure"], result["input"], result["output"],
                                     result["cost"], result["stop"]])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--spend", action="store_true", help="required: this asks Claude, on the configured key")
    parser.add_argument("--catalog", required=True, type=Path, help="a COPY of the catalog database")
    parser.add_argument("--out", required=True, type=Path, help="where the report goes (outside the repository)")
    parser.add_argument("--pages", type=int, default=90)
    parser.add_argument("--configs", nargs="+", default=list(CONFIGS), choices=list(CONFIGS))
    args = parser.parse_args()
    if not args.spend:
        raise SystemExit("this asks Claude about every page on the configured key; pass --spend to agree to that")
    report = run(args)
    print(f"{len(report['pages'])} pages in {report['seconds']}s")
    for name, row in report["summary"].items():
        print(f"  {name:24} shown IoU {row['iouShown']}  drew IoU {row['iouDrew']}  exact {row['exactShown']}/{row['pages']}"
              f"  group {row['groupShown']}  kept/whole/refused {row['kept']}/{row['whole']}/{row['refused']}"
              f"  cut off {row['cutOff']}  out {row['outputTokens']} tok  ${row['perPage']}/page  spent ${row['spent']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
