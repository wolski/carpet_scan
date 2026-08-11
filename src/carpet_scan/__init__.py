"""Carpet diagnostics — an aggregator over established tools. It measures nothing itself.

A *carpet* is a function laid over another to avoid changing it; a stack of them records a history
of avoidance rather than a hierarchy of abstraction. Carpets show up vertically as call depth and
horizontally as public surface nobody calls, so neither axis alone is diagnostic.

This script runs four tools, parses their machine-readable output, joins the rows per module, and
renders the tables and the Markdown report. **No AST walking, graph building, or reference
resolution is implemented here** — every number traces to a named tool:

===============  ===========================================================================
``pyan3``        name-level static ``uses`` graph over ``src`` and ``tests`` (DOT). The only
                 tool in the survey that reports cross-module edges *per name*, which is what
                 the public-surface question needs.
``vulture``      dead code: functions, methods, variables, unreachable blocks. Authoritative
                 for "referenced nowhere", and decorator-aware, so cyclopts commands are not
                 reported.
``radon``        cyclomatic complexity per function and maintainability index per module.
``grimp``        module import graph, used for folder shape.
===============  ===========================================================================

``pydot`` parses the DOT; ``networkx`` computes longest paths and condenses cycles.

It always exits 0. A number here flags a module for triage; it never adjudicates one, and
restructuring to bring a number down is the defect being measured. See ``AGENTS.md``,
``../TODO/TODO_carpet_scan.md`` and ``../TODO/TODO_additional_code_quality_checks.md``.
"""
