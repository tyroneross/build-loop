# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for payload_codec.

This codec is an authentication boundary, not just a serializer: it carries
Build Loop payloads through a field that peer agents can also write. So the
tests that matter are the negative ones — a mutated chunk, a truncated group,
and two competing groups must all decode to ``None`` rather than to something
plausible. A codec that round-trips perfectly and accepts a forged chunk is
worse than no codec, because callers would trust its output.

Extracted alongside the module from bl/resume-state-repair-019fff08, which
shipped it without a test.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "payload_codec", Path(__file__).with_name("payload_codec.py")
)
codec = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(codec)


class RoundTrip(unittest.TestCase):
    def test_simple_payload(self) -> None:
        payload = {"run_id": "abc123", "outcome": "pass", "count": 7}
        self.assertEqual(codec.decode_payload(codec.encode_payload(payload)), payload)

    def test_empty_and_none_are_both_the_empty_dict(self) -> None:
        for value in ({}, None):
            with self.subTest(value=value):
                self.assertEqual(codec.decode_payload(codec.encode_payload(value)), {})

    def test_unicode_survives(self) -> None:
        payload = {"note": "drift ±14.4% — café ✦"}
        self.assertEqual(codec.decode_payload(codec.encode_payload(payload)), payload)

    def test_multi_chunk_payload_reassembles(self) -> None:
        # Comfortably past CHUNK_BYTES so the group really splits.
        payload = {"blob": "x" * (codec.CHUNK_BYTES * 3)}
        chunks = codec.encode_payload(payload)
        self.assertGreater(len(chunks), 3)
        self.assertEqual(codec.decode_payload(chunks), payload)

    def test_key_order_does_not_change_the_encoding(self) -> None:
        # canonical_payload sorts keys, so the digest must be order-independent.
        a = codec.encode_payload({"b": 2, "a": 1})
        b = codec.encode_payload({"a": 1, "b": 2})
        self.assertEqual(a, b)

    def test_unrelated_evidence_entries_are_ignored(self) -> None:
        payload = {"k": "v"}
        noisy = ["some peer note", 42, None, *codec.encode_payload(payload), "trailing"]
        self.assertEqual(codec.decode_payload(noisy), payload)


class RejectsUntrustedInput(unittest.TestCase):
    """Every case here must return None. Returning a plausible dict is the bug."""

    def test_mutated_chunk_body_fails_the_digest(self) -> None:
        chunks = codec.encode_payload({"amount": 100})
        prefix, digest, length, ordinal, encoded = chunks[0].split(":", 4)
        forged = base64.urlsafe_b64encode(b'{"amount":999}').decode("ascii").rstrip("=")
        tampered = [f"{prefix}:{digest}:{length}:{ordinal}:{forged}"]
        self.assertIsNone(codec.decode_payload(tampered))

    def test_declared_length_must_match_the_bytes(self) -> None:
        chunks = codec.encode_payload({"k": "v"})
        prefix, digest, length, ordinal, encoded = chunks[0].split(":", 4)
        self.assertIsNone(
            codec.decode_payload([f"{prefix}:{digest}:{int(length) + 1}:{ordinal}:{encoded}"])
        )

    def test_missing_chunk_of_a_group_decodes_to_nothing(self) -> None:
        chunks = codec.encode_payload({"blob": "y" * (codec.CHUNK_BYTES * 2)})
        self.assertGreater(len(chunks), 2)
        self.assertIsNone(codec.decode_payload(chunks[:-1]))

    def test_two_valid_groups_are_ambiguous_and_rejected(self) -> None:
        # The documented rule: a caller-supplied group must never win merely by
        # appearing first. Both are internally valid; the pair is the failure.
        both = codec.encode_payload({"real": True}) + codec.encode_payload({"forged": True})
        self.assertIsNone(codec.decode_payload(both))

    def test_non_dict_json_is_refused(self) -> None:
        raw = json.dumps([1, 2, 3], separators=(",", ":")).encode()
        digest = hashlib.sha256(raw).hexdigest()
        body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        entry = f"{codec.PREFIX}:{digest}:{len(raw)}:1/1:{body}"
        self.assertIsNone(codec.decode_payload([entry]))

    def test_malformed_entries_do_not_raise(self) -> None:
        for entry in (
            f"{codec.PREFIX}:short:1:1/1:aaaa",          # digest wrong length
            f"{codec.PREFIX}:{'z' * 64}:1:1/1:aaaa",     # digest not hex
            f"{codec.PREFIX}:{'a' * 64}:-1:1/1:aaaa",    # negative length
            f"{codec.PREFIX}:{'a' * 64}:1:0/1:aaaa",     # index below 1
            f"{codec.PREFIX}:{'a' * 64}:1:2/1:aaaa",     # index beyond total
            f"{codec.PREFIX}:nocolons",                  # unsplittable
            "",
        ):
            with self.subTest(entry=entry[:40]):
                self.assertIsNone(codec.decode_payload([entry]))

    def test_empty_and_none_evidence(self) -> None:
        self.assertIsNone(codec.decode_payload([]))
        self.assertIsNone(codec.decode_payload(None))


class Oversize(unittest.TestCase):
    def test_oversize_payload_yields_a_marker_not_chunks(self) -> None:
        payload = {"blob": "z" * (codec.MAX_PAYLOAD_BYTES + 1)}
        encoded = codec.encode_payload(payload)
        self.assertEqual(len(encoded), 1)
        self.assertTrue(encoded[0].startswith(codec.OVERSIZE_PREFIX))
        self.assertTrue(codec.has_oversize_marker(encoded))
        # The whole point: it must NOT decode, so a caller cannot silently lose data.
        self.assertIsNone(codec.decode_payload(encoded))

    def test_marker_is_not_reported_for_a_normal_payload(self) -> None:
        self.assertFalse(codec.has_oversize_marker(codec.encode_payload({"k": "v"})))

    def test_marker_detection_tolerates_junk(self) -> None:
        self.assertFalse(codec.has_oversize_marker(["note", 7, None]))
        self.assertFalse(codec.has_oversize_marker(None))


class Events(unittest.TestCase):
    def test_event_round_trip(self) -> None:
        encoded = codec.encode_event(kind="run.close", payload={"a": 1}, run_id="r1")
        decoded = codec.decode_event(encoded)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["schema"], codec.EVENT_SCHEMA)
        self.assertEqual(decoded["kind"], "run.close")
        self.assertEqual(decoded["run_id"], "r1")
        self.assertEqual(decoded["payload"], {"a": 1})

    def test_source_record_is_carried_and_authenticated(self) -> None:
        # A historical row's original keys must survive the codec intact —
        # that is the whole reason source_record exists.
        original = {"legacy_key": "legacy_value", "n": 3}
        encoded = codec.encode_event(kind="migrate", payload={}, source_record=original)
        self.assertEqual(codec.decode_event(encoded)["source_record"], original)

    def test_source_record_is_omitted_when_not_supplied(self) -> None:
        decoded = codec.decode_event(codec.encode_event(kind="k", payload={}))
        self.assertNotIn("source_record", decoded)

    def test_a_payload_without_the_event_schema_is_not_an_event(self) -> None:
        self.assertIsNone(codec.decode_event(codec.encode_payload({"a": 1})))

    def test_event_with_a_non_dict_payload_is_refused(self) -> None:
        entry = codec.encode_payload({"schema": codec.EVENT_SCHEMA, "payload": "not-a-dict"})
        self.assertIsNone(codec.decode_event(entry))


if __name__ == "__main__":
    unittest.main()
