"""Render layer: Rich tables for the terminal, Markdown for the report."""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.table import Table

from carpet_scan.model import (
    FOLDER_DEPTH_WARN,
    MIN_NAMES_SHARING_A_MODIFIER,
    MIN_PUBLICS_FOR_SURFACE_REPORT,
    VULTURE_MIN_CONFIDENCE,
    DepthRow,
    FolderRow,
    ScanConfig,
    SurfaceRow,
    VultureFinding,
)


def _short(dotted: str, package: str) -> str:
    """Drop the `src.<package>.` prefix pyan3 emits; it is on every row."""
    for prefix in (f"src.{package}.", f"{package}.", "src."):
        if dotted.startswith(prefix):
            return dotted[len(prefix) :]
    return dotted


def _numeric(table: Table, *headers: str) -> None:
    for header in headers:
        table.add_column(header, justify="right")


def _surface_table(rows: list[SurfaceRow], config: ScanConfig) -> Table:
    table = Table(
        title="Check 2 — public surface with no caller outside its own module",
        box=box.ROUNDED,
        title_justify="left",
        header_style="bold",
        caption_justify="left",
        caption=(
            f"pyan3 {config.versions.pyan3} uses-graph + vulture {config.versions.vulture} "
            f"(>= {VULTURE_MIN_CONFIDENCE}% confidence)\n"
            f"public module-level functions only; listed at >= "
            f"{MIN_PUBLICS_FOR_SURFACE_REPORT} publics\n"
            "tests = only tests reference it   home = only its own file -> make private   "
            "gone = vulture confirms dead -> delete\n"
            "cc = worst radon complexity in the module\n"
            f"omitting {config.skipped} module(s) under "
            f"{'/, '.join(config.surface_skip)}/ — decorator-registered CLI entry points, "
            "which vulture still covers below"
        ),
    )
    table.add_column("module", overflow="fold")
    _numeric(table, "pub", "tests", "home", "gone", "cc")
    for row in rows[: config.top]:
        table.add_row(
            _short(row.module, config.package),
            str(row.publics),
            str(len(row.tests_only)) if row.tests_only else "0",
            f"[bold]{len(row.home_only)}[/]" if row.home_only else "0",
            f"[bold]{len(row.unused)}[/]" if row.unused else "0",
            str(row.max_complexity),
        )
    if len(rows) > config.top:
        table.caption = f"{len(rows) - config.top} further modules in the report\n\n{table.caption}"
    return table


def _depth_table(rows: list[DepthRow], config: ScanConfig) -> Table:
    table = Table(
        title="Check 4 — intra-module call depth",
        box=box.ROUNDED,
        title_justify="left",
        header_style="bold",
        caption_justify="left",
        caption=(
            f"pyan3 {config.versions.pyan3} uses-edges between a module's own callables, "
            f"cycles condensed; listed at depth >= {config.warn_depth}\n"
            "cc1 = links radon scores at complexity 1, i.e. no branching, out of chain length"
        ),
    )
    table.add_column("module", overflow="fold")
    _numeric(table, "depth", "cc1")
    table.add_column("chain ends at", overflow="fold", style="dim")
    for row in rows[: config.top]:
        table.add_row(
            _short(row.module, config.package),
            str(row.depth),
            f"{row.trivial}/{len(row.chain)}",
            row.chain[-1] if row.chain else "",
        )
    return table


def _folder_table(rows: list[FolderRow], config: ScanConfig) -> Table:
    table = Table(
        title="Check 5 — intra-folder import depth",
        box=box.ROUNDED,
        title_justify="left",
        header_style="bold",
        caption_justify="left",
        caption=(
            f"grimp {config.versions.grimp} module import graph, siblings only; "
            f"bold at depth >= {FOLDER_DEPTH_WARN}"
        ),
    )
    table.add_column("folder", overflow="fold")
    _numeric(table, "mods", "edges", "depth")
    for row in rows:
        table.add_row(
            _short(row.folder, config.package) or config.package,
            str(row.modules),
            str(row.edges),
            f"[bold]{row.depth}[/]" if row.depth >= FOLDER_DEPTH_WARN else str(row.depth),
        )
    return table


def render_terminal(
    surface: list[SurfaceRow],
    depth: list[DepthRow],
    folders: list[FolderRow],
    dead: list[VultureFinding],
    config: ScanConfig,
) -> None:
    console = Console()
    console.print()
    console.print(f"[bold]carpet-scan[/] {config.command}")
    console.print()
    console.print(_surface_table(surface, config))
    console.print()
    console.print(_depth_table(depth, config))
    console.print()
    console.print(_folder_table(folders, config))
    kinds: dict[str, int] = {}
    for finding in dead:
        kinds[finding.kind] = kinds.get(finding.kind, 0) + 1
    summary = ", ".join(f"{n} {kind}" for kind, n in sorted(kinds.items())) or "nothing"
    console.print(f"\n[bold]vulture[/] {config.versions.vulture} reports dead: {summary}")
    console.print(
        "\n[dim]A module is flagged, not judged: a deep chain is a carpet, staging, a structural"
        "\ndescent, or genuine altitude, and only the last two are fine as they are."
        "\nA folder is a layer — its modules should be siblings, not a ladder.[/]"
    )


def _markdown_method(config: ScanConfig) -> list[str]:
    versions = config.versions
    return [
        "## Method",
        "",
        "This report aggregates four tools. It contains no analysis of its own except the",
        "shared-name-modifier count, which is marked where it appears.",
        "",
        "| Signal | Tool | Version |",
        "| --- | --- | --- |",
        f"| name-level uses graph (Checks 2, 4) | `pyan3` | {versions.pyan3} |",
        f"| dead code (the `unused` bucket) | `vulture` | {versions.vulture} |",
        f"| complexity and maintainability | `radon` | {versions.radon} |",
        f"| module import graph (Check 5) | `grimp` | {versions.grimp} |",
        "",
        "```bash",
        f"pyan3 {config.src} {config.tests or ''} --root . --uses --no-defines --dot",
        f"vulture {config.src} {config.tests or ''} --min-confidence {VULTURE_MIN_CONFIDENCE} \\",
        f"    --ignore-decorators {config.ignore_decorators}",
        f"radon cc {config.src} -j     # and: radon mi {config.src} -j",
        f"python -c \"import grimp; grimp.build_graph('{config.package}')\"",
        "```",
        "",
        f"- {config.modules} modules in the pyan3 graph across `{config.src}`"
        + (f" and `{config.tests}`" if config.tests else ""),
        f"- Check 2 lists a module at >= {MIN_PUBLICS_FOR_SURFACE_REPORT} public functions",
        f"- Check 4 lists modules at call depth >= {config.warn_depth}",
        f"- Check 5 marks folders at import depth >= {FOLDER_DEPTH_WARN}",
        f"- a name modifier is reported when >= {MIN_NAMES_SHARING_A_MODIFIER} names share it",
        f"- Check 2 omits {config.skipped} module(s) under "
        + ", ".join(f"`{part}/`" for part in config.surface_skip)
        + ": their public functions are CLI commands registered by decorator, invisible to a"
        + " uses-graph. Vulture covers them in the dead-code section instead.",
        "",
        "**Reading Check 2.** pyan3 supplies cross-module `uses` edges per name; vulture decides",
        "whether a name with no external user is dead or merely used at home. A public function",
        "not listed in any bucket has a caller in another `src` module and is genuine public API.",
        "",
        "| Bucket | Derivation | Fix |",
        "| --- | --- | --- |",
        "| (not listed) | pyan3 shows a cross-module edge from `src` | none — real public API |",
        "| tests only | the only cross-module edges come from `tests` | check the test path |",
        "| home only | no cross-module edge, vulture does *not* call it dead | make private |",
        "| unused | no cross-module edge, vulture confirms it dead | delete |",
        "",
        "**Trust the buckets unequally.** `unused` is vulture-confirmed and actionable.",
        "`home only` is *unverified*: pyan3 documents its own analysis as superficial, with a",
        "simplified linear binding model. Three reference forms are known to escape it, and each",
        "one inflates `home only`:",
        "",
        "- a bare name used in a dispatch table in another module",
        "- a call through a module alias, `from pkg import mod` then `mod.name(...)`",
        "- registration by decorator, e.g. a CLI framework's command decorator",
        "",
        "It also *over*-resolves common names: a project-local `normalize`, `replace` or `parse`",
        "can be conflated with `str.replace` or `dataclasses.replace`, hiding a real candidate",
        "behind a spurious edge. Confirm any `home only` name by hand before acting on it.",
        "",
        "*(All three forms were verified on the anndata-proteomics-bridge codebase, where they",
        "produced roughly 28 false positives against a hand-checked list of 20.)*",
        "",
        "Only the roots passed on the command line are scanned. A name whose sole consumer is a",
        "different package reads as unreferenced — pass that package as a root, or expect it.",
        "",
        "No timestamp is recorded, deliberately: the report is meant to be diffed between runs.",
        "",
    ]


def _markdown_surface(rows: list[SurfaceRow], config: ScanConfig) -> list[str]:
    out = [
        "## Check 2 — public surface with no caller outside its own module",
        "",
        "| module | publics | tests only | home only | unused | worst cc | mi |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        out.append(
            f"| `{_short(r.module, config.package)}` | {r.publics} | {len(r.tests_only)} | "
            f"{len(r.home_only)} | {len(r.unused)} | {r.max_complexity} | {r.maintainability:.0f} |"
        )
    out += ["", "### Names", ""]
    for r in rows:
        if not (r.home_only or r.unused or r.tests_only or r.modifiers):
            continue
        out.append(f"**`{_short(r.module, config.package)}`**")
        out.append("")
        for label, names in (
            ("used only inside this file, make private", r.home_only),
            ("vulture confirms dead, delete", r.unused),
            ("referenced only from tests", r.tests_only),
        ):
            if names:
                out.append(f"- {label}: {', '.join(f'`{n}`' for n in names)}")
        if r.modifiers:
            fingerprint = ", ".join(f"`{t}` x{n}" for t, n in r.modifiers[:8])
            out.append(f"- shared name modifiers *(ours, no tool provides this)*: {fingerprint}")
        out.append("")
    return out


def _markdown_graphs(
    depth: list[DepthRow], folders: list[FolderRow], config: ScanConfig
) -> list[str]:
    out = [
        "## Check 4 — intra-module call depth",
        "",
        "| module | depth | cc1 | chain |",
        "| --- | --- | --- | --- |",
    ]
    for r in depth:
        chain = " -> ".join(f"`{c}`" for c in r.chain)
        out.append(
            f"| `{_short(r.module, config.package)}` | {r.depth} | "
            f"{r.trivial}/{len(r.chain)} | {chain} |"
        )
    out += [
        "",
        "## Check 5 — intra-folder import depth",
        "",
        "| folder | modules | edges | depth | deepest chain |",
        "| --- | --- | --- | --- | --- |",
    ]
    for f in folders:
        label = _short(f.folder, config.package) or config.package
        out.append(
            f"| `{label}` | {f.modules} | {f.edges} | {f.depth} | "
            f"{' -> '.join(f'`{c}`' for c in f.chain)} |"
        )
    return out


def _markdown_dead(dead: list[VultureFinding], config: ScanConfig) -> list[str]:
    out = [
        "",
        f"## Dead code — vulture {config.versions.vulture}",
        "",
        "Everything vulture reports, not only the public functions Check 2 buckets. Unused",
        "methods, variables and unreachable blocks appear here and nowhere else.",
        "",
        "| kind | name | location | confidence |",
        "| --- | --- | --- | --- |",
    ]
    for f in sorted(dead, key=lambda finding: (-finding.confidence, str(finding.path))):
        out.append(f"| {f.kind} | `{f.name}` | `{f.path}:{f.lineno}` | {f.confidence}% |")
    return out


def render_markdown(
    surface: list[SurfaceRow],
    depth: list[DepthRow],
    folders: list[FolderRow],
    dead: list[VultureFinding],
    config: ScanConfig,
) -> str:
    header = [
        "# Carpet diagnostics",
        "",
        "A *carpet* is a function laid over another to avoid changing it. These numbers flag a",
        "module for triage; they never adjudicate one. Do not add a function, module, or folder to",
        "bring a number down — that move is the defect being measured.",
        "",
    ]
    body = (
        _markdown_method(config)
        + _markdown_surface(surface, config)
        + [""]
        + _markdown_graphs(depth, folders, config)
        + _markdown_dead(dead, config)
    )
    return "\n".join(header + body) + "\n"
