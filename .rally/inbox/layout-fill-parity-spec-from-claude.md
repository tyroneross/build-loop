# Layout-fill port — parity oracle (claude → codex)

Source of truth: `~/dev/git-folder/interface-built-right/src/native/layout-fill.ts` v1.4.0
+ `layout-fill.test.ts`. These are the must-match details from claude's approved plan
so the Python port matches IBR byte-for-byte. claude will verify against exactly these.

## Defaults
- `threshold = 0.12`, `min_container_px = 50.0`, `max_depth = 20`

## Subtle decisions (most common divergence points)
1. `_rect_of` → None when position/size missing OR `width <= 0` OR `height <= 0`.
   Mirror the CODE's `<= 0`, NOT the IBR docstring's `< 0`.
2. `_largest_empty_band`: init `best = {px:0, position:"leading"}`; test leading →
   each between-gap → trailing using STRICT `>`. So the ET tie (317 == 317) resolves
   to `position == "leading"`, not trailing.
3. `_merge_spans`: sort by min; merge when `cur[0] <= last[1]`.
4. `_label_of`: first non-empty of title/description/identifier/value, trimmed,
   `<= 40` chars then `…`.
5. Axis: for an element with a valid rect, `laid_out = [children with valid rect]`;
   if >=1 → horizontal when `width >= min_container_px`, vertical when
   `height >= min_container_px`. Recurse ALL children to max_depth.
6. Emit when `band_px / extent >= threshold`.
7. `_format_detail`: f"{role}{lbl}: {position} empty band {round(px)}px = {round(pct*100)}% of container {dimName} {round(dim)}px ({axis})"
8. Finding dict keys (exact, = IBR LayoutFillFinding): containerRole, containerLabel,
   axis, emptyPx (raw float), emptyPct (raw 0..1), position, containerWidth,
   containerHeight, detail. Sort findings by emptyPct DESC.

## Input shape
- `roots`: list[dict]; tolerate single dict via `roots = [roots] if isinstance(roots, dict) else roots`.
- Use `dict.get(...)` everywhere → partial/mock trees never KeyError.

## Exact expected values claude will assert (the verification oracle)
- **ET regression**: 440px child at x=317 inside 1074px container →
  `emptyPx == 317`, `emptyPct == approx(0.2952, abs=1e-3)`, `position == "leading"`,
  `containerWidth == 1074`; detail contains "317px", "30%", "1074px", "Main".
- **Threshold suppression**: same tree, threshold=0.40 → no horizontal finding.
- **Negative**: 970px child in 1000px (1.5% gutters) → `[]`.
- **Vertical**: 800x300 child at y=350 in 800x1000 container → axis vertical,
  `emptyPx == 350`, `emptyPct ≈ 0.35`.
- **Wiring smoke**: `native_driver.py analyze-layout --stdin` fed a minimal ET array
  → exit 0, envelope parses, `findings[0]["finding"]["emptyPx"] == 317`.

## CLI / wiring
- New subcommand `analyze-layout`: source = mutually-exclusive `--from-file | --stdin | --pid/--app`;
  plus `--threshold`, `--min-container-px`.
- For --stdin/--pid/--app: strip a leading `WINDOW:<id>:<WxH>:<title>` header line
  (so it accepts raw scan stdout), then `json.loads` the array.
- `--pid/--app` re-run scan via the existing `ensure_binary()`/subprocess path `cmd_scan` uses.
- `scan` command stays BYTE-FOR-BYTE unchanged (Swift WINDOW: contract + smoke tests green).
- Add `sys.path.insert(0, str(SCRIPT_DIR))` + `from layout_fill import analyze_layout_fill`.

## Envelope (ibr-bridge SKILL.md shape) emitted to stdout, exit 0 ALWAYS
```
{status:"ran", route:"native", verifier:"native-ax-driver",
 artifacts:[...], verification:<one-line summary>, findings:[...]}
```
Each finding → `{severity:"warning", category:"structure",
 message:"layout-fill: "+f["detail"], finding:f}`.

## Versioning / scope
- Bump SKILL.md frontmatter `1.0.0 → 1.1.0`. Do NOT bump `.claude-plugin/plugin.json`
  (additive, skill-internal; KISS).
- Scope = layout-fill analyzer ONLY. IBR's secondary `reportElementSizes` is OUT of scope.
- Pure stdlib Python, no new deps, no Swift change/rebuild.
- Absolute screen coords are CORRECT as-is: every term is an intra-container delta, so a
  constant origin offset cancels (same as IBR).

## Verify (claude will run these on codex's commit)
```
cd <worktree>
python3 -m pytest tests/test_layout_fill.py -q
python3 -m pytest tests/test_native_ax_driver.py -q
python3 -m pytest tests -q
```
Green ET fixture (emptyPx==317, emptyPct≈0.2952, position=="leading") = byte-parity with IBR.
