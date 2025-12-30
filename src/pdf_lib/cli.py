from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .catalog import categorize_library
from .library import Library, default_library_path
from .scanner import default_excludes, default_scan_roots, scan_and_copy
from .util import expand_path


def _parse_paths(values: list[str] | None) -> list[Path]:
    if not values:
        return []
    return [expand_path(v) for v in values]


def cmd_init(args: argparse.Namespace) -> int:
    lib = Library(root=expand_path(args.library))
    lib.ensure_initialized()
    print(f"Initialized library at: {lib.root}")
    print(f"- vault: {lib.vault_dir}")
    print(f"- categorized: {lib.categorized_dir}")
    print(f"- manifest: {lib.db_path}")
    print(f"- categories config: {lib.categories_config_path}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    lib = Library(root=expand_path(args.library))

    roots = _parse_paths(args.roots) or default_scan_roots()
    excludes = _parse_paths(args.exclude) or default_excludes()
    # Avoid re-ingesting the library itself if it's under a scanned root.
    excludes = excludes + [lib.root]

    stats = scan_and_copy(
        library=lib,
        roots=roots,
        method=args.method,
        exclude_prefixes=excludes,
        dry_run=args.dry_run,
        limit=args.limit,
        verbose=args.verbose,
    )
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


def cmd_categorize(args: argparse.Namespace) -> int:
    lib = Library(root=expand_path(args.library))
    cfg_path = expand_path(args.config) if args.config else None

    stats = categorize_library(
        library=lib,
        config_path=cfg_path,
        link_mode=args.link_mode,
        refresh_view=not args.no_refresh,
        recategorize_all=args.all,
        text_sample_bytes=args.text_sample_bytes,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        llm_mode=args.llm_mode,
        llm_min_confidence=args.llm_min_confidence,
        llm_timeout_seconds=args.llm_timeout_seconds,
        llm_max_output_tokens=args.llm_max_output_tokens,
        llm_path_mode=args.llm_path_mode,
        llm_path_tail_parts=args.llm_path_tail_parts,
        verbose=args.verbose,
    )
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    lib = Library(root=expand_path(args.library))

    roots = _parse_paths(args.roots) or default_scan_roots()
    excludes = _parse_paths(args.exclude) or default_excludes()
    excludes = excludes + [lib.root]

    scan_stats = scan_and_copy(
        library=lib,
        roots=roots,
        method=args.method,
        exclude_prefixes=excludes,
        dry_run=args.dry_run,
        limit=args.limit,
        verbose=args.verbose,
    )
    cat_stats = categorize_library(
        library=lib,
        config_path=(expand_path(args.config) if args.config else None),
        link_mode=args.link_mode,
        refresh_view=not args.no_refresh,
        recategorize_all=args.all,
        text_sample_bytes=args.text_sample_bytes,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        llm_mode=args.llm_mode,
        llm_min_confidence=args.llm_min_confidence,
        llm_timeout_seconds=args.llm_timeout_seconds,
        llm_max_output_tokens=args.llm_max_output_tokens,
        llm_path_mode=args.llm_path_mode,
        llm_path_tail_parts=args.llm_path_tail_parts,
        verbose=args.verbose,
    )
    out = {"scan": scan_stats, "categorize": cat_stats}
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pdf-lib", description="macOS PDF collector + categorizer")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--library",
        default=str(default_library_path()),
        help='Library root folder (default: "~/PDF_Library")',
    )
    common.add_argument("--verbose", action="store_true", help="More logging")

    p_init = sub.add_parser(
        "init",
        parents=[common],
        help="Initialize the library folder + manifest + default config",
    )
    p_init.set_defaults(func=cmd_init)

    def add_scan_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--roots",
            nargs="*",
            help="Scan roots (default: Desktop/Documents/Downloads/iCloud Drive/CloudStorage/home)",
        )
        sp.add_argument(
            "--exclude",
            nargs="*",
            help="Exclude path prefixes (default: caches/system dirs; see scanner.default_excludes)",
        )
        sp.add_argument("--method", choices=["auto", "mdfind", "walk"], default="auto")
        sp.add_argument("--dry-run", action="store_true", help="Do not copy or modify the manifest")
        sp.add_argument("--limit", type=int, help="Process only the first N PDFs (debugging)")

    p_scan = sub.add_parser(
        "scan",
        parents=[common],
        help="Find PDFs and COPY them into the vault (never moves originals)",
    )
    add_scan_opts(p_scan)
    p_scan.set_defaults(func=cmd_scan)

    def add_categorize_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--config", help="Path to categories.json (default: <library>/categories.json)")
        sp.add_argument("--link-mode", choices=["symlink", "hardlink", "copy"], default="symlink")
        sp.add_argument(
            "--no-refresh",
            action="store_true",
            help="Do not rebuild categorized/ from scratch (not recommended)",
        )
        sp.add_argument("--all", action="store_true", help="Re-categorize all docs, not just uncategorized")
        sp.add_argument(
            "--text-sample-bytes",
            type=int,
            default=8192,
            help="Max bytes of Spotlight text sample to use per PDF (0 disables)",
        )
        sp.add_argument(
            "--llm-provider",
            choices=["off", "uzu"],
            default="off",
            help="Optional LLM assistance for categorization (uzu is local)",
        )
        sp.add_argument("--llm-model", help="Override model name (provider-specific)")
        sp.add_argument(
            "--llm-mode",
            choices=["fallback", "always"],
            default="fallback",
            help="When to call the LLM (fallback=only for Unsorted/low-confidence, always=every doc)",
        )
        sp.add_argument(
            "--llm-min-confidence",
            type=float,
            default=0.6,
            help="Minimum LLM confidence to override rule-based category",
        )
        sp.add_argument("--llm-timeout-seconds", type=float, default=30.0, help="LLM API timeout")
        sp.add_argument(
            "--llm-max-output-tokens",
            type=int,
            default=200,
            help="Max tokens in LLM response (keep small to reduce cost)",
        )
        sp.add_argument(
            "--llm-path-mode",
            choices=["basename", "tail", "full"],
            default="tail",
            help="How much of the source path to send to the LLM (privacy control)",
        )
        sp.add_argument(
            "--llm-path-tail-parts",
            type=int,
            default=3,
            help="If --llm-path-mode=tail, number of trailing path components to include",
        )

    p_cat = sub.add_parser(
        "categorize",
        parents=[common],
        help="Categorize vault PDFs + build categorized/ view",
    )
    add_categorize_opts(p_cat)
    p_cat.set_defaults(func=cmd_categorize)

    p_run = sub.add_parser("run", parents=[common], help="scan + categorize")
    add_scan_opts(p_run)
    add_categorize_opts(p_run)
    p_run.set_defaults(func=cmd_run)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


