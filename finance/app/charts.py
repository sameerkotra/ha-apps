"""Small inline-SVG charts, no dependencies (used by the Utilities > Compare page).

One measure per chart (never two y-axes): a bill's cost and its usage are separate
charts. Marks follow the app theme through CSS variables (style.css `.chart`), so all
four themes work and nothing here hard-codes a colour. Each column carries a `data-tip`
that ui.js shows as a tooltip on hover and keyboard focus; the page also offers the same
numbers as a table, so nothing is reachable only by hovering.
"""
import math
from html import escape

from markupsafe import Markup

_W, _H = 640, 240
_LEFT, _RIGHT, _TOP, _BOTTOM = 52, 12, 26, 30
_MAX_COLUMN = 24          # never fill the whole slot
_MAX_X_LABELS = 8


def nice_ticks(top: float, target: int = 4) -> list[float]:
    """Round tick values from 0 up to and including a value >= top."""
    if top <= 0:
        return [0.0, 1.0]
    raw = top / target
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    ticks, value = [], 0.0
    while value < top + step * 0.001:
        ticks.append(round(value, 10))
        value += step
    if ticks[-1] < top:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def _fmt(value: float, prefix: str = "", decimals: int | None = None) -> str:
    if decimals is None:
        decimals = 0 if abs(value) >= 100 or float(value).is_integer() else 2
    return f"{prefix}{value:,.{decimals}f}"


def _column_path(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """A column with a rounded data end (top) and a square base."""
    r = min(r, w / 2, h)
    return (f"M{x:.1f},{y + h:.1f} L{x:.1f},{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"L{x + w - r:.1f},{y:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} L{x + w:.1f},{y + h:.1f} Z")


_CAT_GAP = 2   # px, surface-coloured gap between stacked segments (dataviz: never fill-to-fill)


def stacked_column_chart(periods: list[dict], series: list[dict], *, prefix: str = "", label: str) -> Markup:
    """A stacked column per period, one segment per series (E-470 Compare's "by group" trend).

    periods: [{"label": "2026-03", "tip_label": "March 2026", "values": {"g1": 12.3, "g2": 0.0}}, ...] in time order.
    series:  [{"key": "g1", "label": "Work", "css_class": "chart-cat-1"}, ...] in the SAME fixed order every
    render (colour follows identity, never rank — dataviz skill) — callers must not reorder this by value.
    A period with no data at all (every series 0) still draws its baseline tick, just no visible segment."""
    periods = [p for p in periods if p.get("values") is not None]
    if not periods or not series:
        return Markup("")
    totals = [sum(p["values"].get(s["key"], 0.0) for s in series) for p in periods]
    ticks = nice_ticks(max(totals) if totals else 0)
    top = ticks[-1] or 1.0
    plot_w, plot_h = _W - _LEFT - _RIGHT, _H - _TOP - _BOTTOM
    band = plot_w / len(periods)
    width = min(_MAX_COLUMN, band * 0.6)
    y_of = lambda v: _TOP + plot_h * (1 - v / top)

    parts = [f'<svg class="chart chart-stack" viewBox="0 0 {_W} {_H}" role="img" aria-label="{escape(label)}" preserveAspectRatio="xMidYMid meet">']
    for tick in ticks:
        y = y_of(tick)
        parts.append(f'<line class="chart-grid" x1="{_LEFT}" x2="{_W - _RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="chart-axis" x="{_LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">{escape(_fmt(tick, prefix))}</text>')

    step = max(1, math.ceil(len(periods) / _MAX_X_LABELS))
    for i, p in enumerate(periods):
        cx = _LEFT + band * (i + 0.5)
        base_y = _TOP + plot_h
        tip_lines = [p.get("tip_label", p["label"])]
        for s in series:
            v = p["values"].get(s["key"], 0.0)
            if v:
                tip_lines.append(f"{s['label']}: {_fmt(v, prefix)}")
        if len(tip_lines) == 1:
            tip_lines.append("No tolls")
        tip = "\n".join(tip_lines)
        parts.append(
            f'<g class="chart-mark" tabindex="0" data-tip="{escape(tip, quote=True)}">'
            f'<rect class="chart-hit" x="{cx - band / 2:.1f}" y="{_TOP}" width="{band:.1f}" height="{plot_h}"/>'
        )
        cursor = base_y            # bottom of the next segment to draw; each segment stacks upward with a gap above it
        for s in series:
            v = p["values"].get(s["key"], 0.0)
            if v <= 0:
                continue
            h = max(1.0, plot_h * v / top)
            seg_top = cursor - h
            parts.append(f'<path class="chart-bar {s["css_class"]}" d="{_column_path(cx - width / 2, seg_top, width, cursor - seg_top, r=2)}"/>')
            cursor = seg_top - _CAT_GAP
        parts.append('</g>')
        if i % step == 0 or i == len(periods) - 1 and (len(periods) - 1) % step >= 2:
            parts.append(f'<text class="chart-axis" x="{cx:.1f}" y="{_H - 10}" text-anchor="middle">{escape(p["label"])}</text>')
    parts.append(f'<line class="chart-baseline" x1="{_LEFT}" x2="{_W - _RIGHT}" y1="{_TOP + plot_h}" y2="{_TOP + plot_h}"/>')
    parts.append("</svg>")
    return Markup("".join(parts))


def column_chart(points: list[dict], *, prefix: str = "", suffix: str = "", css_class: str, label: str) -> Markup:
    """points: [{"label": "2026-03", "value": 85.2, "tip": "March 2026\\nCost $85.20"}, ...] in time order.

    Label the newest column and the highest one; every other value is in the tooltip and
    the table view. `css_class` picks the mark colour (see style.css)."""
    points = [p for p in points if p.get("value") is not None]
    if not points:
        return Markup("")
    ticks = nice_ticks(max(p["value"] for p in points))
    top = ticks[-1] or 1.0
    plot_w, plot_h = _W - _LEFT - _RIGHT, _H - _TOP - _BOTTOM
    band = plot_w / len(points)
    width = min(_MAX_COLUMN, band * 0.6)
    y_of = lambda v: _TOP + plot_h * (1 - v / top)

    parts = [f'<svg class="chart {css_class}" viewBox="0 0 {_W} {_H}" role="img" aria-label="{escape(label)}" preserveAspectRatio="xMidYMid meet">']
    for tick in ticks:                                    # hairline grid + y labels
        y = y_of(tick)
        parts.append(f'<line class="chart-grid" x1="{_LEFT}" x2="{_W - _RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="chart-axis" x="{_LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">{escape(_fmt(tick, prefix))}</text>')

    step = max(1, math.ceil(len(points) / _MAX_X_LABELS))
    labelled = {len(points) - 1, max(range(len(points)), key=lambda i: points[i]["value"])}
    for i, p in enumerate(points):
        cx = _LEFT + band * (i + 0.5)
        y = y_of(p["value"])
        h = max(2.0, _TOP + plot_h - y)
        parts.append(
            f'<g class="chart-mark" tabindex="0" data-tip="{escape(p["tip"], quote=True)}">'
            f'<rect class="chart-hit" x="{cx - band / 2:.1f}" y="{_TOP}" width="{band:.1f}" height="{plot_h}"/>'
            f'<path class="chart-bar" d="{_column_path(cx - width / 2, _TOP + plot_h - h, width, h)}"/>'
            f'</g>'
        )
        if i in labelled:
            text = _fmt(p["value"], prefix) + suffix
            half = len(text) * 3.4                        # ~half the rendered width at 12px
            anchor, lx = "middle", cx
            if cx + half > _W - 2:                        # keep the newest label inside the chart
                anchor, lx = "end", _W - 2
            elif cx - half < _LEFT:
                anchor, lx = "start", _LEFT
            parts.append(f'<text class="chart-value" x="{lx:.1f}" y="{_TOP + plot_h - h - 6:.1f}" text-anchor="{anchor}">{escape(text)}</text>')
        if i % step == 0 or i == len(points) - 1 and (len(points) - 1) % step >= 2:
            parts.append(f'<text class="chart-axis" x="{cx:.1f}" y="{_H - 10}" text-anchor="middle">{escape(p["label"])}</text>')
    parts.append(f'<line class="chart-baseline" x1="{_LEFT}" x2="{_W - _RIGHT}" y1="{_TOP + plot_h}" y2="{_TOP + plot_h}"/>')
    parts.append("</svg>")
    return Markup("".join(parts))


def _range_ticks(lo: float, hi: float, target: int = 4) -> list[float]:
    """Round ticks covering lo..hi, always including 0 (for charts that can go below zero)."""
    lo, hi = min(0.0, lo), max(0.0, hi)
    if hi == lo or not (math.isfinite(lo) and math.isfinite(hi)):
        return [0.0, 1.0]
    raw = (hi - lo) / target
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start, end = math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step
    ticks, value = [], start
    while value <= end + step * 0.001:
        ticks.append(round(value, 10))
        value += step
    return ticks


def _bar_path(x: float, y_from: float, y_to: float, w: float, r: float = 3) -> str:
    """A column from the zero line (y_from) to its value (y_to), rounded at the value end."""
    if abs(y_to - y_from) < 1:
        y_to = y_from - 1 if y_to <= y_from else y_from + 1
    top, bottom = min(y_from, y_to), max(y_from, y_to)
    r = min(r, w / 2, bottom - top)
    if y_to < y_from:        # going up: round the top
        return (f"M{x:.1f},{bottom:.1f} L{x:.1f},{top + r:.1f} Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} "
                f"L{x + w - r:.1f},{top:.1f} Q{x + w:.1f},{top:.1f} {x + w:.1f},{top + r:.1f} L{x + w:.1f},{bottom:.1f} Z")
    return (f"M{x:.1f},{top:.1f} L{x + w:.1f},{top:.1f} L{x + w:.1f},{bottom - r:.1f} Q{x + w:.1f},{bottom:.1f} {x + w - r:.1f},{bottom:.1f} "
            f"L{x + r:.1f},{bottom:.1f} Q{x:.1f},{bottom:.1f} {x:.1f},{bottom - r:.1f} Z")


def multi_series_chart(labels: list[str], series: list[dict], *, kind: str = "bar", label: str,
                       decimals: int | None = None) -> Markup:
    """Report charts: several series side by side (bars) or as lines, on one axis that can go below zero.

    labels: one per x position, in order. series: [{"label": "money_in", "values": [12.0, None, ...],
    "css_class": "chart-cat-1"}] in a fixed order (colour follows the series, never its rank)."""
    values = [v for s in series for v in s["values"] if v is not None]
    if not labels or not series or not values:
        return Markup("")
    ticks = _range_ticks(min(values), max(values))
    bottom, top = ticks[0], ticks[-1]
    plot_w, plot_h = _W - _LEFT - _RIGHT, _H - _TOP - _BOTTOM
    band = plot_w / len(labels)
    y_of = lambda v: _TOP + plot_h * (top - v) / (top - bottom)
    y0 = y_of(0.0)

    parts = [f'<svg class="chart chart-multi" viewBox="0 0 {_W} {_H}" role="img" aria-label="{escape(label)}" preserveAspectRatio="xMidYMid meet">']
    for tick in ticks:
        y = y_of(tick)
        parts.append(f'<line class="chart-grid" x1="{_LEFT}" x2="{_W - _RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="chart-axis" x="{_LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">{escape(_fmt(tick, decimals=decimals))}</text>')

    n = len(series)
    group = min(band * 0.75, _MAX_COLUMN * n + 2 * (n - 1))
    bar_w = max(1.0, (group - 2 * (n - 1)) / n)
    step = max(1, math.ceil(len(labels) / _MAX_X_LABELS))
    for i, x_label in enumerate(labels):
        cx = _LEFT + band * (i + 0.5)
        tip = "\n".join([x_label] + [f"{s['label']}: {'—' if s['values'][i] is None else _fmt(s['values'][i], decimals=2)}"
                                     for s in series])
        parts.append(f'<g class="chart-mark" tabindex="0" data-tip="{escape(tip, quote=True)}">'
                     f'<rect class="chart-hit" x="{cx - band / 2:.1f}" y="{_TOP}" width="{band:.1f}" height="{plot_h}"/>')
        if kind == "bar":
            x = cx - group / 2
            for s in series:
                v = s["values"][i]
                if v is not None:
                    parts.append(f'<path class="chart-bar {s["css_class"]}" d="{_bar_path(x, y0, y_of(v), bar_w)}"/>')
                x += bar_w + 2
        parts.append('</g>')
        if i % step == 0 or i == len(labels) - 1 and (len(labels) - 1) % step >= 2:
            text = x_label if len(x_label) <= 14 else x_label[:13] + "…"
            parts.append(f'<text class="chart-axis" x="{cx:.1f}" y="{_H - 10}" text-anchor="middle">{escape(text)}</text>')
    if kind == "line":
        for s in series:
            run: list[str] = []
            for i, v in enumerate(s["values"]):
                if v is None:
                    if len(run) > 1:
                        parts.append(f'<polyline class="chart-line {s["css_class"]}" points="{" ".join(run)}"/>')
                    run = []
                    continue
                pt = f"{_LEFT + band * (i + 0.5):.1f},{y_of(v):.1f}"
                run.append(pt)
                parts.append(f'<circle class="chart-dot {s["css_class"]}" cx="{pt.split(",")[0]}" cy="{pt.split(",")[1]}" r="3"/>')
            if len(run) > 1:
                parts.append(f'<polyline class="chart-line {s["css_class"]}" points="{" ".join(run)}"/>')
    parts.append(f'<line class="chart-baseline" x1="{_LEFT}" x2="{_W - _RIGHT}" y1="{y0:.1f}" y2="{y0:.1f}"/>')
    parts.append("</svg>")
    return Markup("".join(parts))
