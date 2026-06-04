# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""scripts/backlog/ — capture-time product-impact triage + causal-tree assessment.

Two small modules:
- ``triage.classify(text, context)`` — deterministic product-impact yes/no + one-line impact.
- ``assess.build_item(deferral, ...)`` — render a ``templates/backlog-item.md`` body with the
  two new frontmatter fields (``product_impacting``, ``impact``) plus a causal-tree section.

The orchestrator calls these at the descope/followup-capture point. Non-product-impacting
deferrals stay in ``.build-loop/followup/`` as today; product-impacting ones land in
``.build-loop/backlog/<repo>/<id>-<slug>.md``.
"""
from backlog.triage import classify
from backlog.assess import build_item

__all__ = ["classify", "build_item"]
