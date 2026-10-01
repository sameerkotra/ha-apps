"""A report's optional chart from its result (SPEC.md section 19.9)."""
from __future__ import annotations

import math

from markupsafe import Markup, escape

from .charts import multi_series_chart
from .query_engine import Result
from .report_core import is_number, numeric_columns

MAX_POINTS = 500
MAX_SERIES = 5


def _find(columns: list[str], name: str) -> int | None:
    name = (name or "").strip().lower()
    for i, c in enumerate(columns):
        if c.lower() == name:
            return i
    return None


def render(result: Result, chart: dict) -> tuple[Markup | None, str | None]:
    """(svg + legend, note). The note explains a chart that was asked for but can't be drawn."""
    kind = (chart or {}).get("type")
    if kind not in ("bar", "line") or not result.columns:
        return None, None
    if not result.rows:
        return None, None
    x = _find(result.columns, chart.get("x", "")) if chart.get("x") else 0
    if x is None:
        return None, f"Chart: there is no column named “{chart.get('x')}” in the result."
    wanted = chart.get("y") or []
    if wanted:
        ys = []
        for name in wanted:
            i = _find(result.columns, name)
            if i is None:
                return None, f"Chart: there is no column named “{name}” in the result."
            ys.append(i)
    else:
        ys = [i for i in numeric_columns(result) if i != x and not result.columns[i].lower().endswith("id")]
    ys = [i for i in ys if i != x][:MAX_SERIES]
    if not ys:
        return None, "Chart: pick at least one numeric column for the Y axis."
    if len(result.rows) > MAX_POINTS:
        return None, f"Chart skipped: {len(result.rows):,} points is more than {MAX_POINTS}. The table still shows every row."
    keep = bool(chart.get("keep_signs"))
    labels = ["(empty)" if r[x] is None else str(r[x]) for r in result.rows]
    series = []
    for n, i in enumerate(ys, start=1):
        vals = [(v if keep else abs(v)) if is_number(v) and math.isfinite(v) else None for v in (r[i] for r in result.rows)]
        series.append({"label": result.columns[i], "values": [None if v is None else float(v) for v in vals],
                       "css_class": f"chart-cat-{n}"})
    svg = multi_series_chart(labels, series, kind=kind, label=f"{kind} chart of {', '.join(result.columns[i] for i in ys)}")
    if not svg:
        return None, "Chart: no numbers to draw."
    legend = ""
    if len(series) > 1 or not keep:
        items = "".join(f'<li><span class="chart-swatch {s["css_class"]}"></span>{escape(s["label"])}</li>' for s in series)
        note = "" if keep else '<li class="hint">Drawn as positive amounts</li>'
        legend = f'<ul class="chart-legend">{items}{note}</ul>'
    return Markup(legend) + svg, None
