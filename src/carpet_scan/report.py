"""Render layer: Rich tables for the terminal, Markdown for the report, HTML for a browser."""

from __future__ import annotations

from markdown_it import MarkdownIt
from rich import box
from rich.console import Console
from rich.table import Table

from carpet_scan.model import (
    COMMUNITY_SEED,
    FOLDER_DEPTH_WARN,
    MIN_MODULES_FOR_COHESION,
    MIN_PUBLICS_FOR_SURFACE_REPORT,
    VULTURE_MIN_CONFIDENCE,
    CohesionResult,
    DepthRow,
    FolderRow,
    PrivateRow,
    ScanConfig,
    SurfaceRow,
    VultureFinding,
)


def short_name(dotted: str, package: str) -> str:
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
        title="Public surface with no caller outside its own module (Check 2)",
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
            short_name(row.module, config.package),
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
        title="Intra-module call depth (Check 4)",
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
            short_name(row.module, config.package),
            str(row.depth),
            f"{row.trivial}/{len(row.chain)}",
            row.chain[-1] if row.chain else "",
        )
    return table


def _folder_table(rows: list[FolderRow], config: ScanConfig) -> Table:
    table = Table(
        title="Intra-folder import depth (Check 5)",
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
            short_name(row.folder, config.package) or config.package,
            str(row.modules),
            str(row.edges),
            f"[bold]{row.depth}[/]" if row.depth >= FOLDER_DEPTH_WARN else str(row.depth),
        )
    return table


def _cohesion_table(result: CohesionResult, config: ScanConfig) -> Table:
    table = Table(
        title="Folder cohesion vs the graph's own clusters (Check 6)",
        box=box.ROUNDED,
        title_justify="left",
        header_style="bold",
        caption_justify="left",
        caption=(
            f"grimp {config.versions.grimp} import graph, networkx Louvain communities; "
            f"dim below {MIN_MODULES_FOR_COHESION} modules, where the ratio is noise"
        ),
    )
    table.add_column("folder", overflow="fold")
    _numeric(table, "mods", "in", "out", "cohesion")
    for row in result.rows:
        measurable = row.modules >= MIN_MODULES_FOR_COHESION
        table.add_row(
            short_name(row.folder, config.package) or config.package,
            str(row.modules),
            str(row.internal),
            str(row.external),
            f"{row.cohesion:.0%}" if measurable else "n/a",
            style=None if measurable else "dim",
        )
    return table


def render_terminal(
    surface: list[SurfaceRow],
    depth: list[DepthRow],
    folders: list[FolderRow],
    dead: list[VultureFinding],
    private: list[PrivateRow],
    cohesion: CohesionResult,
    config: ScanConfig,
) -> None:
    surface = [row for row in surface if row.publics >= MIN_PUBLICS_FOR_SURFACE_REPORT]
    depth = [row for row in depth if row.depth >= config.warn_depth]
    console = Console()
    console.print()
    console.print(f"[bold]carpet-scan[/] {config.command}")
    console.print()
    console.print(_surface_table(surface, config))
    console.print()
    console.print(_depth_table(depth, config))
    console.print()
    console.print(_folder_table(folders, config))
    console.print()
    console.print(_cohesion_table(cohesion, config))
    console.print(
        f"[bold]modularity[/] folders Q={cohesion.q_folders:.3f} vs "
        f"detected Q={cohesion.q_communities:.3f} across {len(cohesion.communities)} "
        f"communities; {cohesion.intra}/{cohesion.edges} edges "
        f"({cohesion.intra_share:.0%}) stay inside a folder"
    )
    kinds: dict[str, int] = {}
    for finding in dead:
        kinds[finding.kind] = kinds.get(finding.kind, 0) + 1
    summary = ", ".join(f"{n} {kind}" for kind, n in sorted(kinds.items())) or "nothing"
    console.print(f"\n[bold]vulture[/] {config.versions.vulture} reports dead: {summary}")
    # Counts only: the per-module detail is long, belongs in the report, and is read for movement
    # over time rather than acted on line by line.
    console.print(
        f"[bold]ruff[/] {config.versions.ruff} finds cross-module private access: "
        f"{sum(r.imported for r in private)} imported (PLC2701), "
        f"{sum(r.attribute for r in private)} by attribute (SLF001), "
        f"across {len(private)} module(s)"
    )
    console.print(
        "\n[dim]A module is flagged, not judged: a deep chain is a carpet, staging, a structural"
        "\ndescent, or genuine altitude, and only the last two are fine as they are."
        "\nA folder is a layer — its modules should be siblings, not a ladder.[/]"
    )


def _columns(*pairs: tuple[str, str]) -> list[str]:
    """A caption's column glossary. Every table states what its own columns mean."""
    return [
        "",
        "| column | meaning |",
        "| --- | --- |",
        *(f"| {name} | {meaning} |" for name, meaning in pairs),
    ]


def _generated_by(*commands: str) -> list[str]:
    """The exact invocations behind the table above it.

    Kept beside each table rather than pooled in the Method section: a reader checking one
    number should not have to scroll away to find out which tool and which options produced it,
    and three of the four tables combine more than one tool.
    """
    return ["", "*Generated by:*", "", "```bash", *commands, "```"]


def _markdown_method(config: ScanConfig) -> list[str]:
    versions = config.versions
    return [
        "## Method",
        "",
        "This report aggregates five tools and performs no analysis of its own, except the",
        "shared-name-modifier count, which is marked where it appears. Each section below states",
        "which of the five produced its table and with which options.",
        "",
        "| Tool | Version | What it contributes |",
        "| --- | --- | --- |",
        f"| `pyan3` | {versions.pyan3} | name-level `uses` graph: who calls what |",
        f"| `vulture` | {versions.vulture} | dead code, decorator-aware |",
        f"| `radon` | {versions.radon} | cyclomatic complexity, maintainability index |",
        f"| `grimp` | {versions.grimp} | module import graph |",
        f"| `ruff` | {versions.ruff} | cross-module private access (`SLF001`, `PLC2701`) |",
        "",
        f"Scope: {config.modules} modules across `{config.src}`"
        + (f" and `{config.tests}`" if config.tests else "")
        + ". Only the roots passed on the command line are scanned — a name whose sole consumer"
        + " is a different package reads as unreferenced unless that package is a root too.",
        "",
        "Reproduce the whole report with:",
        "",
        "```bash",
        config.command,
        "```",
        "",
        "**Why Check 3 is missing.** The section numbers cross-reference the five checks in",
        "`TODO_additional_code_quality_checks.md`, so a finding here can be traced to the rule that",
        "motivated it. Four of the five are *gauges* — measurements with no correct value, read for",
        "movement and triage — and they are what this report carries. Check 3 is a *gate*: an",
        "`import-linter` `layers` contract asserting the subpackage order someone declared in",
        "`.importlinter`. There is no number to report, only a contract that holds or does not, and",
        "wrapping it in a command that always exits 0 would turn a working gate into a line nobody",
        "acts on. It runs as a blocking pre-commit hook instead. Its measurable half — whether a",
        "folder has become a ladder — is Check 5 below.",
        "",
        "No timestamp is recorded, deliberately: the report is meant to be diffed between runs.",
        "",
    ]


def _markdown_surface(rows: list[SurfaceRow], config: ScanConfig) -> list[str]:
    out = [
        "## Public surface with no caller outside its own module",
        "",
        f"*Cross-reference: Check 2.* One row per `{config.src}` module with at least"
        f" {MIN_PUBLICS_FOR_SURFACE_REPORT} public functions. A public function absent from all"
        " three buckets has a caller in another module and is genuine public API.",
        *_columns(
            ("module", "dotted module path, with the common `src.<package>.` prefix stripped"),
            ("publics", "module-level functions not starting with `_`, as pyan3 saw them"),
            ("tests only", f"count whose only cross-module callers live in `{config.tests}`"),
            ("home only", "count with no cross-module caller that vulture does *not* call dead"),
            ("unused", "count with no cross-module caller that vulture confirms dead"),
            ("worst cc", "highest cyclomatic complexity of any block in the file (radon cc)"),
            ("mi", "maintainability index for the file, 0-100, higher is better (radon mi)"),
        ),
        *_generated_by(
            f"pyan3 {config.src} {config.tests or ''} --root . --uses --no-defines --dot",
            f"vulture {config.src} {config.tests or ''}"
            f" --min-confidence {VULTURE_MIN_CONFIDENCE} \\",
            f"    --ignore-decorators {config.ignore_decorators}",
            f"radon cc {config.src} -j",
            f"radon mi {config.src} -j",
        ),
        "",
        "Three tools make one row: pyan3 supplies the cross-module edges that fill the buckets,",
        "vulture decides whether a name with no external caller is dead or merely used at home,",
        "and radon supplies `worst cc` and `mi`. The buckets are how the two graphs disagree:",
        "",
        "| bucket | derivation | fix |",
        "| --- | --- | --- |",
        "| (not listed) | pyan3 shows a cross-module edge | none — real public API |",
        "| tests only | the only cross-module edges come from tests | check the test path |",
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
        f"Omitted: {config.skipped} module(s) under "
        + ", ".join(f"`{part}/`" for part in config.surface_skip)
        + ". Their public functions are CLI commands registered by decorator, invisible to a"
        + " uses-graph, so every one of them would appear here on every run. Vulture covers them"
        + " in the dead-code section instead, where decorator awareness is built in.",
        "",
        "| module | publics | tests only | home only | unused | worst cc | mi |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        out.append(
            f"| `{short_name(r.module, config.package)}` | {r.publics} | {len(r.tests_only)} | "
            f"{len(r.home_only)} | {len(r.unused)} | {r.max_complexity} | {r.maintainability:.0f} |"
        )
    out += ["", "### Names", ""]
    for r in rows:
        if not (r.home_only or r.unused or r.tests_only or r.modifiers):
            continue
        out.append(f"**`{short_name(r.module, config.package)}`**")
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


def _markdown_private(rows: list[PrivateRow], config: ScanConfig) -> list[str]:
    out = [
        "",
        "## Private names used across module boundaries",
        "",
        "*Cross-reference: Check 1.* One row per module that reaches into a private name defined"
        " somewhere else. Keyed by the module doing the reaching, because that is the file that"
        " has to change.",
        *_columns(
            ("module", "the module doing the reaching, common prefix stripped"),
            ("attr", "`SLF001` — attribute access, `other._private(...)`"),
            ("import", "`PLC2701` — `from other import _private`"),
            ("total", "the two summed; the tracker follows this as a proxy metric over time"),
            ("names", "the private names touched, de-duplicated"),
        ),
        *_generated_by(
            f"ruff check {config.src} {config.tests or ''} \\",
            "    --select SLF001,PLC2701 --preview --output-format json",
        ),
        "",
        "`--preview` is not optional: `PLC2701` is preview-gated, and without the flag Ruff prints"
        ' "Selection PLC2701 has no effect" and reports none of them. The project\'s own Ruff'
        " configuration still applies, so a `per-file-ignores` entry excusing these rules is"
        " respected — the count measures what policy does *not* already excuse.",
        "",
        "**`import` is the column that matters.** Importing a private *module* (`_common`,"
        " `_logging`) is idiomatic; importing a private *function* is coupling to an implementation"
        " detail. And a private function whose only outside caller is its own test is how a carpet"
        " survives: it looks used to a coverage gate while nothing in the package needs it.",
        "",
        "White-box testing is legitimate, so a high `attr` count over the test tree is not by"
        " itself a defect. Read this table for movement, not for a target of zero.",
        "",
        "| module | attr | import | total | names |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        names = ", ".join(f"`{n}`" for n in r.names)
        out.append(
            f"| `{short_name(r.module, config.package)}` | {r.attribute} | {r.imported} | "
            f"{r.total} | {names} |"
        )
    return out


def _markdown_graphs(
    depth: list[DepthRow], folders: list[FolderRow], config: ScanConfig
) -> list[str]:
    out = [
        "## Intra-module call depth",
        "",
        f"*Cross-reference: Check 4.* One row per `{config.src}` module whose own functions call"
        f" each other at least {config.warn_depth} levels deep. Within one module, public"
        " functions should sit at one level of abstraction and privates one below, so a long"
        " chain means the module holds a ladder.",
        *_columns(
            ("module", "dotted module path, common prefix stripped"),
            ("depth", "edges in the longest call chain among that module's *own* callables"),
            ("cc1", "of the functions in the chain, how many radon scores at complexity 1"),
            ("chain", "the chain itself, caller first"),
        ),
        *_generated_by(
            f"pyan3 {config.src} {config.tests or ''} --root . --uses --no-defines --dot",
            f"radon cc {config.src} -j",
        ),
        "",
        "pyan3 supplies the edges and radon the `cc1` count; `networkx` computes the longest path",
        "after condensing cycles, so mutual recursion counts once instead of running forever."
        " Where several chains tie at the longest length the lexicographically smallest is shown,"
        " so the row does not change between runs.",
        "",
        "`cc1` is the discriminator, not `depth`. A deep chain of complexity-1 functions is a"
        " carpet — each layer only forwards to the next. A deep chain of substantial functions is"
        " usually a structural descent, and correct.",
        "",
        "| module | depth | cc1 | chain |",
        "| --- | --- | --- | --- |",
    ]
    for r in depth:
        chain = " -> ".join(f"`{c}`" for c in r.chain)
        out.append(
            f"| `{short_name(r.module, config.package)}` | {r.depth} | "
            f"{r.trivial}/{len(r.chain)} | {chain} |"
        )
    out += [
        "",
        "## Intra-folder import depth",
        "",
        "*Cross-reference: Check 5.* One row per folder, counting only imports between modules"
        " that are direct siblings in it. A folder is a layer, so its modules should be siblings"
        f" rather than a ladder; depth >= {FOLDER_DEPTH_WARN} means the folder has become one.",
        *_columns(
            ("folder", "package path, common prefix stripped"),
            ("modules", "direct children of the folder in grimp's graph"),
            ("edges", "imports between those siblings; imports out of the folder are not counted"),
            ("depth", "edges in the longest sibling-import chain"),
            ("deepest chain", "that chain, importer first, module names only"),
        ),
        *_generated_by(f"python -c \"import grimp; grimp.build_graph('{config.package}')\""),
        "",
        "grimp supplies the import graph and `networkx` the longest path, condensed and"
        " tie-broken as above. grimp resolves the package through the Python path, so this table"
        " is the one that disappears if the scan runs outside the target's own environment.",
        "",
        "| folder | modules | edges | depth | deepest chain |",
        "| --- | --- | --- | --- | --- |",
    ]
    for f in folders:
        label = short_name(f.folder, config.package) or config.package
        out.append(
            f"| `{label}` | {f.modules} | {f.edges} | {f.depth} | "
            f"{' -> '.join(f'`{c}`' for c in f.chain)} |"
        )
    return out


def _markdown_dead(dead: list[VultureFinding], config: ScanConfig) -> list[str]:
    out = [
        "",
        "## Dead code",
        "",
        "Everything vulture reports, not only the public functions the surface table buckets."
        " Unused methods, variables, imports and unreachable blocks appear here and nowhere else,"
        " including for the omitted subpackages.",
        *_columns(
            ("kind", "what vulture found: function, method, variable, import, attribute, ..."),
            ("name", "the identifier"),
            ("location", "file and line, as vulture reports it"),
            (
                "confidence",
                f"vulture's own estimate; anything below {VULTURE_MIN_CONFIDENCE}% was not"
                " requested. 100% means syntactically unreachable, 60% means an unused name that"
                " a dynamic reference could still reach",
            ),
        ),
        *_generated_by(
            f"vulture {config.src} {config.tests or ''}"
            f" --min-confidence {VULTURE_MIN_CONFIDENCE} \\",
            f"    --ignore-decorators {config.ignore_decorators}",
        ),
        "",
        "The ignored decorators matter: without them every pydantic validator and every CLI"
        " command reads as dead, because the framework calls them by registration rather than by"
        " name. A project on a different framework must pass its own set.",
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
    private: list[PrivateRow],
    config: ScanConfig,
) -> str:
    surface = [row for row in surface if row.publics >= MIN_PUBLICS_FOR_SURFACE_REPORT]
    depth = [row for row in depth if row.depth >= config.warn_depth]
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
        + _markdown_private(private, config)
        + [""]
        + _markdown_graphs(depth, folders, config)
        + _markdown_dead(dead, config)
    )
    return "\n".join(header + body) + "\n"


def _markdown_cohesion_folders(result: CohesionResult, config: ScanConfig) -> list[str]:
    out = [
        "## Cohesion per folder",
        "",
        "*Cross-reference: Check 6.* "
        "One row per folder: of every import edge with at least one end inside it, the share that"
        " stays inside. Direction is deliberately ignored — a folder everything imports and a"
        " folder that imports everything are equally uncohesive, and which end initiated is a"
        " question for the layering gate, not this table.",
        *_columns(
            ("folder", "package path, common prefix stripped"),
            ("modules", "modules that are files; package nodes are excluded"),
            ("internal", "edges with both ends in this folder"),
            ("external", "edges with exactly one end in it, either direction"),
            ("cohesion", "internal / (internal + external)"),
        ),
        *_generated_by(f"python -c \"import grimp; grimp.build_graph('{config.package}')\""),
        "",
        f"A folder holding fewer than {MIN_MODULES_FOR_COHESION} modules cannot have an internal"
        " edge at all, so its ratio is reported as `n/a` rather than 0%.",
        "",
        "**Low cohesion is not automatically a defect.** An orchestration folder — CLI entry"
        " points, workflows, adapters — earns its keep precisely by importing across the tree, and"
        " will measure near zero however well it is written. Read this table for folders that"
        " were *meant* to be cohesive units and are not.",
        "",
        "| folder | modules | internal | external | cohesion |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in result.rows:
        label = short_name(row.folder, config.package) or config.package
        ratio = f"{row.cohesion:.0%}" if row.modules >= MIN_MODULES_FOR_COHESION else "n/a"
        out.append(f"| `{label}` | {row.modules} | {row.internal} | {row.external} | {ratio} |")
    return out


def _markdown_cohesion_clusters(result: CohesionResult, config: ScanConfig) -> list[str]:
    out = [
        "",
        "## The clusters the graph actually has",
        "",
        "Community detection partitions the same graph without being shown the folder names, so"
        " the result is what the imports say the modules are, independent of where they were"
        " filed. Comparing the two partitions is the measurement:",
        "",
        "| partition | modularity Q |",
        "| --- | --- |",
        f"| the folders you declared | {result.q_folders:.3f} |",
        f"| the graph's own best split ({len(result.communities)} communities) | "
        f"{result.q_communities:.3f} |",
        "",
        f"{result.intra}/{result.edges} edges ({result.intra_share:.0%}) stay inside a folder.",
        "",
        "**A gap between the two Q values is a finding to triage, not a defect.** Modularity"
        " rewards clustered topologies. A deliberately *layered* design cuts across features by"
        " construction — every feature contributes a module to the adapter layer, one to the"
        " workflow layer, and so on — so its folder partition will always score below a"
        " clustering found by maximising modularity, no matter how disciplined the layering is."
        " What the gap tells you is that folder membership and coupling are answering different"
        " questions; whether that is intended is a judgement the numbers cannot make.",
        "",
        "What *is* worth acting on is a folder appearing in several clusters while its clusters"
        " each have an obvious name — that is one folder holding two unrelated things, and the"
        " split below says where the seam runs.",
        *_generated_by(
            f"python -c \"import grimp; grimp.build_graph('{config.package}')\"",
            f'python -c "import networkx; networkx.community.louvain_communities(g,'
            f' seed={COMMUNITY_SEED})"',
        ),
        "",
        f"The community search is seeded (`seed={COMMUNITY_SEED}`) so unchanged code produces an"
        " identical report; like the carpet report, this file carries no timestamp and is meant to"
        " be diffed between runs.",
        "",
        "| # | modules | folders spanned | composition |",
        "| --- | --- | --- | --- |",
    ]
    for index, community in enumerate(result.communities, start=1):
        mix = ", ".join(f"`{name}` x{count}" for name, count in community.spread)
        out.append(f"| {index} | {len(community.members)} | {community.folders} | {mix} |")

    out += ["", "### Membership", ""]
    for index, community in enumerate(result.communities, start=1):
        out += [
            f"**Community {index}** — {len(community.members)} modules, home `{community.home}`",
            "",
            *(f"- `{name}`" for name in community.members),
            "",
        ]

    split = result.split_folders
    out += [
        "## Folders split across more than one cluster",
        "",
        "The seam to look at first. A folder listed here holds modules the import graph assigns to"
        " different clusters — either two features sharing a folder, or one feature whose modules"
        " genuinely belong to layers below and above it.",
        "",
    ]
    if not split:
        out.append("None: every folder falls entirely inside one cluster.")
        return out
    out += ["| folder | clusters it spans |", "| --- | --- |"]
    out += [
        f"| `{short_name(folder, config.package) or config.package}` | {pieces} |"
        for folder, pieces in split
    ]
    return out


def render_cohesion_markdown(result: CohesionResult, config: ScanConfig) -> str:
    """The cohesion report, written beside the carpet report rather than inside it.

    Kept a separate document because it answers a different question. The carpet report asks
    whether a *module* has grown layers it does not need; this one asks whether the *folders*
    correspond to anything in the import graph. They are read at different moments, by different
    decisions, and merging them would bury a package-shaped finding inside a function-shaped one.
    """
    header = [
        "# Folder cohesion",
        "",
        "Do the folders correspond to clusters in the import graph — more dependencies within a"
        " folder than between folders? Like every number in the carpet report, these flag a",
        "boundary for triage and never adjudicate one. Do not move a module to improve a ratio.",
        "",
        f"Scope: `{config.package}`, {sum(r.modules for r in result.rows)} modules and"
        f" {result.edges} import edges between them.",
        "",
    ]
    body = _markdown_cohesion_folders(result, config) + _markdown_cohesion_clusters(result, config)
    return "\n".join(header + body) + "\n"


_HTML_STYLE = """
:root {
  --bg: #ffffff; --fg: #1c1e21; --muted: #5c6370; --rule: #d7dae0;
  --code-bg: #f2f3f5; --head-bg: #f7f8fa; --zebra: #fafbfc; --accent: #8b3a2f;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16181c; --fg: #e3e5e8; --muted: #9aa0a8; --rule: #2f333a;
    --code-bg: #22252b; --head-bg: #1d2025; --zebra: #191c21; --accent: #e0876f;
  }
}
* { box-sizing: border-box; }
body {
  background: var(--bg); color: var(--fg); margin: 0 auto; padding: 2.5rem 1.5rem 6rem;
  max-width: 78rem; line-height: 1.6;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
h1 { font-size: 1.9rem; margin: 0 0 .6rem; letter-spacing: -.02em; }
h2 {
  font-size: 1.3rem; margin: 3rem 0 .4rem; padding-bottom: .35rem;
  border-bottom: 1px solid var(--rule);
}
h3 { font-size: 1.05rem; margin: 2rem 0 .3rem; color: var(--muted); }
p, li { max-width: 60rem; }
em { color: var(--muted); font-style: normal; }
strong { color: var(--accent); }
code {
  background: var(--code-bg); border-radius: 3px; padding: .1em .35em;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace; font-size: .87em;
}
pre { background: var(--code-bg); border-radius: 6px; padding: .9rem 1rem; overflow-x: auto; }
pre code { background: none; padding: 0; }
/* Tables are the payload and several are wide: let each scroll on its own. */
table {
  display: block; overflow-x: auto; width: 100%; border-collapse: collapse;
  margin: 1rem 0; font-size: .86rem; font-variant-numeric: tabular-nums;
}
th, td {
  border: 1px solid var(--rule); padding: .32rem .6rem; text-align: left;
  vertical-align: top; white-space: nowrap;
}
th { background: var(--head-bg); font-weight: 600; }
tbody tr:nth-child(even) { background: var(--zebra); }
td code { white-space: pre-wrap; }
blockquote {
  margin: 1rem 0; padding: .1rem 1rem; border-left: 3px solid var(--accent); color: var(--muted);
}
"""


def render_html(markdown: str, config: ScanConfig) -> str:
    """Wrap the Markdown report in one self-contained HTML page.

    The tables are the payload, so the stylesheet is inline and each table scrolls
    independently: several are wider than a window and must not push the page sideways.
    """
    body = MarkdownIt("commonmark").enable("table").render(markdown)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Carpet diagnostics — {config.package}</title>\n"
        f"<style>{_HTML_STYLE}</style>\n</head>\n<body>\n{body}</body>\n</html>\n"
    )
