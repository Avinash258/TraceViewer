"""CLI: Playwright trace.zip → Postman Collection v2.1 JSON + API sequence."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from apiextracter import __version__
from apiextracter.flow import analyze_flow, render_sequence_markdown
from apiextracter.parser import parse_trace
from apiextracter.postman import build_collection

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRACE_DIR = PROJECT_ROOT / "Trace"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="apiextracter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Read a Playwright trace.zip and write the API sequence (from the "
            "access-token fetch) plus a Postman Collection v2.1 JSON file."
        ),
        epilog=(
            "Folders\n"
            f"  Input traces : {DEFAULT_TRACE_DIR}\n"
            f"  Output JSON  : {DEFAULT_OUTPUT_DIR}\n"
            "\n"
            "Examples\n"
            "  python extract.py\n"
            "      Process every .zip in the Trace folder.\n"
            "  python extract.py Trace\\my-trace.zip\n"
            "      Sequence starts at the access-token fetch.\n"
            "  python extract.py Trace\\my-trace.zip --full-flow\n"
            "      Keep APIs that happened before the token fetch."
        ),
    )
    parser.add_argument(
        "trace",
        nargs="?",
        help=(
            "Path to a Playwright trace.zip, extracted trace folder, or .network file. "
            f"If omitted, every .zip in {DEFAULT_TRACE_DIR} is processed."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        help=f"Postman JSON path (default: {DEFAULT_OUTPUT_DIR}\\<trace-name>.postman_collection.json)",
    )
    parser.add_argument(
        "--name",
        help="Collection name shown in Postman",
    )
    parser.add_argument(
        "--group-by",
        choices=("sequence", "host", "path", "none"),
        default="sequence",
        help="Folder grouping in the collection (default: sequence / call order)",
    )
    parser.add_argument(
        "--full-flow",
        action="store_true",
        help="Include APIs that ran before the access-token fetch",
    )
    parser.add_argument(
        "--no-sequence",
        action="store_true",
        help="Do not write the .api_sequence.json / .api_sequence.md breakup files",
    )
    parser.add_argument(
        "--include-static",
        action="store_true",
        help="Keep JS/CSS/images/fonts and other static assets",
    )
    parser.add_argument(
        "--include-preflight",
        action="store_true",
        help="Keep CORS OPTIONS preflight requests",
    )
    parser.add_argument(
        "--include-browser-headers",
        action="store_true",
        help="Keep browser fingerprint headers (sec-ch-ua, sec-fetch-*)",
    )
    parser.add_argument(
        "--no-responses",
        action="store_true",
        help="Do not attach example responses captured in the trace",
    )
    parser.add_argument(
        "--dedupe",
        action="store_true",
        help="Drop duplicate requests with the same method, URL, and body",
    )
    parser.add_argument(
        "--no-variables",
        action="store_true",
        help="Do not extract Authorization / API keys into collection variables",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"apiextracter {__version__}",
    )
    args = parser.parse_args(argv)

    traces = _resolve_inputs(args.trace)
    if traces is None:
        return 1
    if args.output and len(traces) > 1:
        print("error: --output can be used with only one input trace", file=sys.stderr)
        return 1

    failed = 0
    for trace_path in traces:
        output = Path(args.output) if args.output else _default_output(trace_path)
        if not _convert(trace_path, output, args):
            failed += 1
    return 1 if failed else 0


def _resolve_inputs(trace_arg: str | None) -> list[Path] | None:
    if trace_arg:
        path = Path(trace_arg)
        if not path.exists():
            print(f"error: Trace not found: {path}", file=sys.stderr)
            return None
        if path.is_dir() and _zip_files(path):
            zips = _zip_files(path)
            print(f"Found {len(zips)} trace zip(s) in {path}")
            return zips
        return [path]

    DEFAULT_TRACE_DIR.mkdir(parents=True, exist_ok=True)
    zips = _zip_files(DEFAULT_TRACE_DIR)
    if not zips:
        print(
            f"error: No trace.zip found in {DEFAULT_TRACE_DIR}\n"
            "Put a Playwright trace.zip in the Trace folder, then run: python extract.py",
            file=sys.stderr,
        )
        return None
    print(f"Found {len(zips)} trace zip(s) in {DEFAULT_TRACE_DIR}")
    return zips


def _zip_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".zip")


def _default_output(trace_path: Path) -> Path:
    return DEFAULT_OUTPUT_DIR / f"{trace_path.stem}.postman_collection.json"


def _companion(output: Path, suffix: str) -> Path:
    name = output.name
    marker = ".postman_collection.json"
    base = name[: -len(marker)] if name.endswith(marker) else output.stem
    return output.with_name(f"{base}{suffix}")


def _convert(trace_path: Path, output: Path, args) -> bool:
    try:
        result = parse_trace(
            trace_path,
            include_static=args.include_static,
            include_preflight=args.include_preflight,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return False

    flow = analyze_flow(result.requests, from_token=not args.full_flow)
    collection = build_collection(
        result,
        name=args.name,
        group_by=args.group_by,
        include_responses=not args.no_responses,
        include_browser_headers=args.include_browser_headers,
        dedupe=args.dedupe,
        extract_variables=not args.no_variables,
        flow=flow,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(collection, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    sequence_json = sequence_md = None
    if not args.no_sequence:
        sequence_json = _companion(output, ".api_sequence.json")
        sequence_md = _companion(output, ".api_sequence.md")
        sequence_json.write_text(
            json.dumps(flow.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        sequence_md.write_text(
            render_sequence_markdown(flow, title=args.name or result.title or trace_path.stem),
            encoding="utf-8",
        )

    _print_summary(result, collection, output, trace_path, flow, sequence_json, sequence_md)
    return True


def _print_summary(result, collection: dict, output: Path, trace_path: Path, flow, sequence_json, sequence_md) -> None:
    requests = flow.requests
    hosts = Counter(urlparse(req.url).netloc for req in requests)
    methods = Counter(req.method for req in requests)

    print(f"Input  trace     : {trace_path}")
    print(f"Output Postman   : {output}")
    if sequence_json:
        print(f"Output sequence  : {sequence_json}")
    if sequence_md:
        print(f"Output breakup   : {sequence_md}")
    print(f"  Collection      : {collection['info']['name']}")
    print(f"  API sequence    : {len(flow.steps)}")
    print(f"  Token fetch     : {'yes' if flow.token_found else 'no'}")
    if flow.skipped_before_token:
        print(f"  Skipped before  : {flow.skipped_before_token}")
    print(f"  Seen in trace   : {result.total_seen}")
    print(f"  Filtered out    : {result.filtered_out}")
    if methods:
        method_bits = ", ".join(f"{name} {count}" for name, count in methods.most_common())
        print(f"  Methods         : {method_bits}")
    if hosts:
        print("  Hosts           :")
        for host, count in hosts.most_common():
            print(f"    {host or '(none)'}: {count}")
    if flow.steps:
        print("  Sequence        :")
        for step in flow.steps:
            mark = "  <- token fetch" if step.role == "access_token_fetch" else ""
            print(f"    {step.seq:02d}. {step.method} {step.path}  [{step.status}]{mark}")
    print("Import in Postman: Import -> File -> select the JSON.")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
