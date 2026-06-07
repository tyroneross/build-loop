"""Native layout-fill / gap analyzer.

Pure Python port of IBR's ``src/native/layout-fill.ts``. The input is the
JSON tree emitted by the Swift AX extractor: a list of element dictionaries
with absolute ``position`` / ``size`` frames and nested ``children``.
"""

from __future__ import annotations

from typing import Any, Literal

Axis = Literal["horizontal", "vertical"]
Position = Literal["leading", "between", "trailing"]
Rect = dict[str, float]
Finding = dict[str, Any]


def _rect_of(element: dict[str, Any] | None) -> Rect | None:
    if not isinstance(element, dict):
        return None
    position = element.get("position")
    size = element.get("size")
    if not isinstance(position, dict) or not isinstance(size, dict):
        return None

    try:
        x = float(position.get("x"))
        y = float(position.get("y"))
        width = float(size.get("width"))
        height = float(size.get("height"))
    except (TypeError, ValueError):
        return None

    if width <= 0 or height <= 0:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def _label_of(element: dict[str, Any]) -> str:
    for key in ("title", "description", "identifier", "value"):
        value = element.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text[:40] + "…" if len(text) > 40 else text
    return ""


def _children_of(element: dict[str, Any]) -> list[dict[str, Any]]:
    children = element.get("children")
    if not isinstance(children, list):
        return []
    return [child for child in children if isinstance(child, dict)]


def _merge_spans(spans: list[tuple[float, float]]) -> list[list[float]]:
    if not spans:
        return []

    sorted_spans = sorted(spans, key=lambda span: span[0])
    merged: list[list[float]] = [[sorted_spans[0][0], sorted_spans[0][1]]]

    for cur_min, cur_max in sorted_spans[1:]:
        last = merged[-1]
        if cur_min <= last[1]:
            last[1] = max(last[1], cur_max)
        else:
            merged.append([cur_min, cur_max])

    return merged


def _largest_empty_band(
    min_value: float,
    max_value: float,
    spans: list[tuple[float, float]],
) -> dict[str, float | Position] | None:
    if not spans:
        return None

    merged = _merge_spans(spans)
    best: dict[str, float | Position] = {"px": 0.0, "position": "leading"}

    leading = merged[0][0] - min_value
    if leading > best["px"]:
        best = {"px": leading, "position": "leading"}

    for index in range(1, len(merged)):
        gap = merged[index][0] - merged[index - 1][1]
        if gap > best["px"]:
            best = {"px": gap, "position": "between"}

    trailing = max_value - merged[-1][1]
    if trailing > best["px"]:
        best = {"px": trailing, "position": "trailing"}

    return best


def _format_detail(
    role: str,
    label: str,
    axis: Axis,
    position: Position,
    px: float,
    pct: float,
    dim: float,
) -> str:
    lbl = f" [{label}]" if label else ""
    dim_name = "width" if axis == "horizontal" else "height"
    return (
        f"{role}{lbl}: {position} empty band "
        f"{round(px)}px = {round(pct * 100)}% of "
        f"container {dim_name} {round(dim)}px ({axis})"
    )


def analyze_layout_fill(
    roots: list[dict[str, Any]] | dict[str, Any],
    *,
    threshold: float = 0.12,
    min_container_px: float = 50.0,
    max_depth: int = 20,
) -> list[Finding]:
    """Analyze an AX element tree for centered-narrow / empty-gutter layouts."""

    root_list = [roots] if isinstance(roots, dict) else roots
    if not isinstance(root_list, list):
        return []

    findings: list[Finding] = []

    def visit(element: dict[str, Any], depth: int) -> None:
        if depth >= max_depth:
            return

        rect = _rect_of(element)
        children = _children_of(element)

        if rect:
            laid_out = [child for child in children if _rect_of(child)]

            if laid_out:
                role = str(element.get("role") or "")
                label = _label_of(element)

                if rect["width"] >= min_container_px:
                    x_spans = []
                    for child in laid_out:
                        child_rect = _rect_of(child)
                        if child_rect:
                            x_spans.append((child_rect["x"], child_rect["x"] + child_rect["width"]))
                    band = _largest_empty_band(rect["x"], rect["x"] + rect["width"], x_spans)
                    if band and float(band["px"]) / rect["width"] >= threshold:
                        empty_px = float(band["px"])
                        empty_pct = empty_px / rect["width"]
                        position = str(band["position"])
                        findings.append(
                            {
                                "containerRole": role,
                                "containerLabel": label,
                                "axis": "horizontal",
                                "emptyPx": empty_px,
                                "emptyPct": empty_pct,
                                "position": position,
                                "containerWidth": rect["width"],
                                "containerHeight": rect["height"],
                                "detail": _format_detail(
                                    role,
                                    label,
                                    "horizontal",
                                    position,  # type: ignore[arg-type]
                                    empty_px,
                                    empty_pct,
                                    rect["width"],
                                ),
                            }
                        )

                if rect["height"] >= min_container_px:
                    y_spans = []
                    for child in laid_out:
                        child_rect = _rect_of(child)
                        if child_rect:
                            y_spans.append((child_rect["y"], child_rect["y"] + child_rect["height"]))
                    band = _largest_empty_band(rect["y"], rect["y"] + rect["height"], y_spans)
                    if band and float(band["px"]) / rect["height"] >= threshold:
                        empty_px = float(band["px"])
                        empty_pct = empty_px / rect["height"]
                        position = str(band["position"])
                        findings.append(
                            {
                                "containerRole": role,
                                "containerLabel": label,
                                "axis": "vertical",
                                "emptyPx": empty_px,
                                "emptyPct": empty_pct,
                                "position": position,
                                "containerWidth": rect["width"],
                                "containerHeight": rect["height"],
                                "detail": _format_detail(
                                    role,
                                    label,
                                    "vertical",
                                    position,  # type: ignore[arg-type]
                                    empty_px,
                                    empty_pct,
                                    rect["height"],
                                ),
                            }
                        )

        for child in children:
            visit(child, depth + 1)

    for root in root_list:
        if isinstance(root, dict):
            visit(root, 0)

    findings.sort(key=lambda finding: finding.get("emptyPct", 0), reverse=True)
    return findings
