<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Share Presentation Contract

Build Loop owns the design, implementation, and validation of what a recipient
sees when content leaves the product. Persona review is optional critique.
Apply to websites, mobile/native apps, public artifacts and shareable links,
including emailed reports without an app UI. An app with no outbound sharing
surface does not need a new sharing feature.

## Assess and plan

Every build records `applicability` as `applicable` or `not_applicable`, with a
concrete `reason`, in `.build-loop/share-presentation.json`. Inspect affected
routes, metadata, generated links/files, and native share actions; do not infer
absence from `uiTarget == null`. For applicable builds add a plan section
`## Share Presentation` referencing this record and one `surfaces` entry per
affected recipient surface. For unrelated backend/internal work, an explicit
not-applicable reason and empty surfaces finish the contract.

Each surface names these nonempty string fields:

| Field | Required decision |
|---|---|
| `surface` | Route, artifact or share action plus source path |
| `recipient` | Audience and target receiving platform(s) |
| `title` | Concrete topic/outcome title; identify shared content rather than only its owner |
| `description` | Concise preview/share text explaining value and context; app/site attribution stays secondary to the topic |
| `image` | Relevant topic image or icon, asset source, purpose and safe crop; or explicit image fallback with reason |
| `identity` | Browser favicon, link-preview/social image, installed app icon and share-sheet identity are separate; state which apply |
| `payload` | Web metadata and canonical URL, or native outgoing text/image/file and MIME type; public URL fallback for deep links when applicable |
| `privacy` | Auth/private content and token handling; avoid disclosure in crawler metadata, public images or share payloads |
| `fallback` | Meaningful text/URL/file behavior when image, metadata, receiver or deep link is unsupported; image omission never waives other fields |
| `validation` | Commands/tooling, target mobile and desktop receivers, expected crop/readability, and unavailable checks |

Also include `evidence`, initially `[]`. Each later entry has `check`, `status`
(`verified`, `unverified`, `failed` or `not_applicable`), and `detail` (artifact path or
concrete limitation/reason). Keep this record linked from the UI input/output
and design contracts when they exist; do not duplicate a second policy.

## Implement

Read current official platform documentation when choosing platform-specific
metadata, payload APIs or image requirements. Do not assume one universal image
size or that a favicon/app icon controls a link-preview image. Build topic-aware
title, description and relevant imagery together; a generic owner portrait or
logo needs a content-specific rationale. Use truthful text when images cannot
be shown. Native file shares can apply without any public URL. Do not expose
private content to make a preview fetchable.

## Review

Run in Plan and Review:

```bash
python3 "$RUNTIME_PLUGIN_ROOT/scripts/share_presentation_check.py" .build-loop/share-presentation.json
```
This checks structural
coverage, not content quality or actual recipient rendering. A recorded `failed`
check makes `checks_pass` false and the command exit nonzero even when
`structural_pass` is true; fix the defect and update its evidence after rechecking. Missing coverage
requires a fix or explicit not-applicable disposition; it is not a global hook
for unrelated operations.

For applicable surfaces, inspect and record:

- Actual HTTP response received by a browser/social crawler: correct final URL,
  topic title/description/image; no duplicate, conflicting or misleading metadata.
  Check image URL fetchability without unintended auth, response type, decodability,
  dimensions and byte size against the selected receiver's current requirements.
- Rendered mobile and desktop share previews: crop, text truncation, small-size
  readability, image relevance and fallback. Source tags or a mock card alone
  do not prove a receiver rendered them.
- Native outgoing share-sheet payload and receiving app behavior when tooling
  exists: actual text, image/file, MIME type and link/deep-link fallback. The
  receiving app controls its rendering; inspect the sender share-sheet preview separately.
  Open Graph does not control arbitrary file shares; payload inspection is narrower evidence.
- Private/authenticated/no-image/unsupported states. Preserve access boundaries
  and test safe fallback without publishing private links to external validators.

Record platform/version, URL or artifact, capture time and evidence path. Report
caching, redirects, platform differences and unavailable device/receiver tooling
as limits. An untested receiver stays `unverified`; do not turn unavailable
rendering into a pass. Confirmed defects route to Iterate. Report structural,
fetch, payload and rendered checks separately, with any remaining limitations.
