#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import threading
from pathlib import Path
from typing import Any, Dict, List

import fitz

from microfiche_overlap_extractor import (
    OVERLAP_CSV_FIELDS,
    PythonHeuristicEngine,
    export_blurry_pages,
    export_extracted_non_overlap,
    export_overlap_pages,
    export_uncertain_pages,
    list_pdfs,
    now_file_ts,
    overlap_row_for_csv,
)


def write_flagged_csv(records: List[Dict[str, Any]], csv_path: Path) -> int:
    rows = [
        overlap_row_for_csv(r)
        for r in records
        if r.get("scope") == "source"
        and (r.get("is_overlap") or r.get("is_blurry") or r.get("decision") == "uncertain")
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OVERLAP_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pure Python microfiche overlap/blurry detector.")
    parser.add_argument("source_dir", help="Directory containing source PDFs")
    parser.add_argument("--output-dir", default="", help="Directory for CSV and exported PDFs")
    parser.add_argument("--no-recursive", action="store_true", help="Do not scan subfolders")
    parser.add_argument("--fast", action="store_true", help="Lower DPI for faster scanning")
    parser.add_argument("--no-csv", action="store_true", help="Do not write flagged CSV")
    parser.add_argument("--no-overlap", action="store_true", help="Do not export overlap pages")
    parser.add_argument("--blurry", action="store_true", help="Export blurry pages as B_*.pdf")
    parser.add_argument("--no-uncertain", action="store_true", help="Do not export uncertain pages as U_*.pdf")
    parser.add_argument("--no-extracted-original", action="store_true", help="Do not write E_*.pdf")
    args = parser.parse_args()

    source_dir = Path(args.source_dir).expanduser()
    if not source_dir.is_dir():
        raise SystemExit(f"Source directory does not exist: {source_dir}")
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else source_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    pdfs = list_pdfs(source_dir, recursive=not args.no_recursive)
    if not pdfs:
        raise SystemExit("No PDF files found.")

    cancel_event = threading.Event()
    pause_event = threading.Event()
    engine = PythonHeuristicEngine(
        memory={"global_notes": [], "overrides": {}, "correction_history": []},
        logger=lambda msg: print(msg, flush=True),
        cancel_event=cancel_event,
        pause_event=pause_event,
        progress_cb=lambda done, total: print(f"progress {done}/{total}", flush=True) if total and done == total else None,
        render_dpi=170 if args.fast else 220,
    )
    records = engine.scan_pdfs(pdfs, scope="source", custom_prompt="")

    if not args.no_csv:
        csv_path = output_dir / f"overlap_report_{now_file_ts()}.csv"
        count = write_flagged_csv(records, csv_path)
        print(f"CSV: {csv_path} ({count} rows)", flush=True)

    if not args.no_overlap:
        count = export_overlap_pages(records, print, output_dir=output_dir)
        print(f"Overlap exports: {count}", flush=True)

    if not args.no_extracted_original:
        count = export_extracted_non_overlap(records, print, output_dir=output_dir)
        print(f"Extracted Original exports: {count}", flush=True)

    if args.blurry:
        count = export_blurry_pages(records, print, output_dir=output_dir)
        print(f"Blurry exports: {count}", flush=True)

    if not args.no_uncertain:
        count = export_uncertain_pages(records, print, output_dir=output_dir)
        print(f"Uncertain exports: {count}", flush=True)


if __name__ == "__main__":
    main()
