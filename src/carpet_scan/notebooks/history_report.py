# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "marimo>=0.23",
# ]
# ///

import marimo

__generated_with = "0.23.16"
app = marimo.App(width="full")


@app.cell
def _() -> None:
    import html
    from pathlib import Path

    import marimo as mo

    from carpet_scan.linked_report import render_linked_report

    arguments = mo.cli_args()
    database = Path(str(arguments["database"]))
    run_id = str(arguments["run-id"])
    output = Path(str(arguments["output"]))
    report = mo.md(render_linked_report(database, run_id))
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(f"Carpet evidence {run_id}")}</title>
<style>
:root {{ color-scheme: light dark; --bg:#fbfaf7; --fg:#202124; --muted:#62666d;
  --rule:#d7d4cc; --surface:#fff; --link:#145ea8; }}
@media (prefers-color-scheme:dark) {{ :root {{ --bg:#17181a; --fg:#ecebea; --muted:#aaa;
  --rule:#3b3d40; --surface:#202225; --link:#8fc5ff; }} }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:var(--fg);
  font:16px/1.55 system-ui,-apple-system,sans-serif; }} main {{ max-width:1180px;
  margin:auto; padding:3rem clamp(1rem,4vw,4rem) 6rem; }} h1,h2,h3 {{ line-height:1.15;
  margin-top:2em; }} h1:first-child {{ margin-top:0; }} a {{ color:var(--link); }} code {{
  font-family:ui-monospace,SFMono-Regular,monospace; font-size:.9em; }} pre,code {{
  overflow-wrap:anywhere; }} table {{ border-collapse:collapse; width:100%; margin:1.5rem 0;
  background:var(--surface); }} th,td {{ border-bottom:1px solid var(--rule); padding:.55rem .7rem;
  text-align:left; vertical-align:top; }} th {{ position:sticky; top:0; background:var(--surface); }}
blockquote {{ border-left:3px solid var(--rule); margin-left:0; padding-left:1rem;
  color:var(--muted); }} hr {{ border:0; border-top:1px solid var(--rule); margin:4rem 0; }}
.prose {{ max-width:none; }}
.graph-scroll {{ overflow-x:auto; margin:1rem 0 2rem; }} .dependency-matrix th {{
  font-family:ui-monospace,SFMono-Regular,monospace; font-size:.8rem; }} .dependency-matrix td {{
  text-align:center; }} .dependency-matrix .diagonal {{ outline:2px solid var(--link);
  outline-offset:-2px; }} .call-chain {{ min-width:560px; width:100%; height:auto; }}
.call-node {{ fill:var(--surface); stroke:var(--rule); stroke-width:1.5; }} .call-chain text {{
  fill:var(--fg); font:13px ui-monospace,SFMono-Regular,monospace; }} .call-edge,.call-arrow {{
  fill:none; stroke:var(--muted); stroke-width:1.5; }}
</style>
</head>
<body><main>{report.text}</main></body>
</html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    mo.output.replace(report)
    return


if __name__ == "__main__":
    app.run()
