"""Tests for native AX layout-fill / gap analysis."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "native-ax-driver"
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from layout_fill import analyze_layout_fill


def el(
    *,
    role: str,
    title: str | None = None,
    x: float | None = None,
    y: float | None = None,
    w: float | None = None,
    h: float | None = None,
    children: list[dict] | None = None,
) -> dict:
    has_frame = x is not None and y is not None and w is not None and h is not None
    return {
        "role": role,
        "subrole": None,
        "title": title,
        "description": None,
        "identifier": None,
        "value": None,
        "enabled": True,
        "focused": False,
        "actions": [],
        "position": {"x": x, "y": y} if has_frame else None,
        "size": {"width": w, "height": h} if has_frame else None,
        "children": children or [],
        "path": [],
    }


def test_et_regression_reports_ibr_parity_numbers():
    tree = [
        el(
            role="AXSplitGroup",
            title="Main",
            x=0,
            y=0,
            w=1074,
            h=700,
            children=[
                el(role="AXGroup", title="Terminal", x=317, y=0, w=440, h=700),
            ],
        )
    ]

    findings = analyze_layout_fill(tree)
    horiz = next(
        finding
        for finding in findings
        if finding["axis"] == "horizontal" and finding["containerRole"] == "AXSplitGroup"
    )

    assert horiz["emptyPx"] == 317
    assert horiz["emptyPct"] == pytest.approx(0.2952, abs=1e-3)
    assert horiz["position"] == "leading"
    assert horiz["containerWidth"] == 1074
    assert "317px" in horiz["detail"]
    assert "30%" in horiz["detail"]
    assert "1074px" in horiz["detail"]
    assert "Main" in horiz["detail"]


def test_threshold_suppresses_et_finding():
    tree = [
        el(
            role="AXSplitGroup",
            title="Main",
            x=0,
            y=0,
            w=1074,
            h=700,
            children=[el(role="AXGroup", x=317, y=0, w=440, h=700)],
        )
    ]

    assert analyze_layout_fill(tree, threshold=0.40) == []


def test_well_filled_container_is_silent():
    tree = [
        el(
            role="AXGroup",
            x=0,
            y=0,
            w=1000,
            h=600,
            children=[el(role="AXGroup", x=15, y=0, w=970, h=600)],
        )
    ]

    assert analyze_layout_fill(tree) == []


def test_container_with_no_children_is_silent():
    tree = [el(role="AXGroup", x=0, y=0, w=800, h=600, children=[])]

    assert analyze_layout_fill(tree) == []


def test_children_without_frames_are_silent():
    tree = [
        el(
            role="AXGroup",
            x=0,
            y=0,
            w=800,
            h=600,
            children=[el(role="AXStaticText")],
        )
    ]

    assert analyze_layout_fill(tree) == []


def test_container_below_min_container_px_is_silent():
    tree = [
        el(
            role="AXGroup",
            x=0,
            y=0,
            w=40,
            h=40,
            children=[el(role="AXButton", x=0, y=0, w=10, h=40)],
        )
    ]

    assert analyze_layout_fill(tree) == []


def test_vertical_centered_child_reports_vertical_finding():
    tree = [
        el(
            role="AXGroup",
            title="Stack",
            x=0,
            y=0,
            w=800,
            h=1000,
            children=[el(role="AXGroup", x=0, y=350, w=800, h=300)],
        )
    ]

    findings = analyze_layout_fill(tree)
    vert = next(finding for finding in findings if finding["axis"] == "vertical")

    assert vert["emptyPx"] == 350
    assert vert["emptyPct"] == pytest.approx(0.35, abs=1e-3)


def test_between_siblings_gap_reports_between_position():
    tree = [
        el(
            role="AXGroup",
            x=0,
            y=0,
            w=1000,
            h=600,
            children=[
                el(role="AXGroup", x=0, y=0, w=200, h=600),
                el(role="AXGroup", x=700, y=0, w=300, h=600),
            ],
        )
    ]

    horiz = next(finding for finding in analyze_layout_fill(tree) if finding["axis"] == "horizontal")

    assert horiz["position"] == "between"
    assert horiz["emptyPx"] == 500
    assert horiz["emptyPct"] == pytest.approx(0.5, abs=1e-3)


def test_nested_containers_are_analyzed_independently():
    tree = [
        el(
            role="AXWindow",
            x=0,
            y=0,
            w=1200,
            h=800,
            children=[
                el(
                    role="AXGroup",
                    title="Inner",
                    x=0,
                    y=0,
                    w=1200,
                    h=800,
                    children=[el(role="AXButton", x=400, y=350, w=400, h=100)],
                )
            ],
        )
    ]

    findings = analyze_layout_fill(tree)

    assert not any(
        finding["axis"] == "horizontal" and finding["containerRole"] == "AXWindow"
        for finding in findings
    )
    inner_horiz = next(
        finding
        for finding in findings
        if finding["axis"] == "horizontal" and finding["containerLabel"] == "Inner"
    )
    assert inner_horiz["emptyPx"] == 400


def test_findings_sort_by_empty_pct_descending():
    tree = [
        el(
            role="AXGroup",
            title="A",
            x=0,
            y=0,
            w=1000,
            h=600,
            children=[el(role="AXGroup", x=500, y=0, w=500, h=600)],
        ),
        el(
            role="AXGroup",
            title="B",
            x=0,
            y=700,
            w=1000,
            h=600,
            children=[el(role="AXGroup", x=200, y=700, w=800, h=600)],
        ),
    ]

    findings = [
        finding
        for finding in analyze_layout_fill(tree)
        if finding["axis"] == "horizontal"
    ]

    assert [finding["containerLabel"] for finding in findings] == ["A", "B"]


def test_single_dict_input_is_tolerated():
    root = el(
        role="AXGroup",
        x=0,
        y=0,
        w=1000,
        h=600,
        children=[el(role="AXGroup", x=500, y=0, w=500, h=600)],
    )

    findings = analyze_layout_fill(root)

    assert findings[0]["axis"] == "horizontal"
    assert findings[0]["emptyPct"] == pytest.approx(0.5)
