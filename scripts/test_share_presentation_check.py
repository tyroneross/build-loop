# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Structural completeness and honest evidence boundaries for recipient shares."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
try:
    from share_presentation_check import check
except ModuleNotFoundError:
    from scripts.share_presentation_check import check


class SharePresentationTests(unittest.TestCase):
    def record(self, surface='Website /report in pages/report.astro'):
        return dict(applicability='applicable', reason='Changed shareable output', surfaces=[dict(
            surface=surface, recipient='Report readers in mobile and desktop messaging apps',
            title='Quarterly revenue report', description='Revenue sources and changes, from Acme',
            image='Revenue chart with central safe crop', identity='Favicon Acme; chart is link image',
            payload='Canonical public report URL and metadata', privacy='Public aggregate data only',
            fallback='Title and URL when image is unsupported', validation='Fetch and render target cards',
            evidence=[])])

    def test_each_surface_field_required_even_without_image(self):
        for field in self.record()['surfaces'][0]:
            record = self.record()
            record['surfaces'][0]['image'] = 'No image: receiving app supports text only'
            del record['surfaces'][0][field]
            self.assertFalse(check(record)['structural_pass'], field)

    def test_web_native_file_and_artifact_without_ui(self):
        for name in ('Website route', 'Native generated PDF share', 'Emailed public artifact'):
            record = self.record(name)
            if name.startswith('Native'):
                record['surfaces'][0]['payload'] = 'Local PDF plus report text; no public URL'
            result = check(record)
            self.assertTrue(result['structural_pass'])
            self.assertTrue(result['unverified'])
            self.assertFalse(result['recipient_rendering_proven'])

    def test_not_applicable_requires_reason_and_no_surfaces(self):
        record = dict(applicability='not_applicable', reason='Internal database maintenance; no recipient outputs affected', surfaces=[])
        self.assertTrue(check(record)['structural_pass'])
        for value in ('', None):
            bad = copy.deepcopy(record); bad['reason'] = value
            self.assertFalse(check(bad)['structural_pass'])
        record['surfaces'] = self.record()['surfaces']
        self.assertFalse(check(record)['structural_pass'])

    def test_invalid_shapes_and_omissions(self):
        for record in (None, [], {}, {'applicability': 'applicable', 'reason': 'Web', 'surfaces': []}):
            self.assertFalse(check(record)['structural_pass'])

    def test_unavailable_receiver_preserved(self):
        record = self.record()
        record['surfaces'][0]['evidence'] = [dict(check='iOS Messages receiver', status='unverified', detail='No device available')]
        result = check(record)
        self.assertIn('No device available', result['unverified'][0])
        record['surfaces'][0]['evidence'][0]['status'] = 'verified'
        self.assertFalse(check(record)['recipient_rendering_proven'])

    def test_failed_check_blocks_review_but_keeps_structure_separate(self):
        record = self.record()
        record['surfaces'][0]['evidence'] = [dict(check='Preview crop', status='failed', detail='Headline clipped in capture.png')]
        result = check(record)
        self.assertTrue(result['structural_pass'])
        self.assertFalse(result['checks_pass'])
        self.assertIn('Headline clipped', result['failed'][0])

    def test_cli_missing_malformed_and_failed(self):
        script = Path(__file__).with_name('share_presentation_check.py')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'record.json'
            def run():
                process = subprocess.run([sys.executable, str(script), str(path)], capture_output=True, text=True)
                self.assertEqual(process.returncode, 1)
                return json.loads(process.stdout)
            self.assertFalse(run()['structural_pass'])
            path.write_text('{broken')
            self.assertFalse(run()['structural_pass'])
            record = self.record()
            record['surfaces'][0]['evidence'] = [dict(check='Image fetch', status='failed', detail='HTTP 404')]
            path.write_text(json.dumps(record))
            result = run()
            self.assertTrue(result['structural_pass'])
            self.assertFalse(result['checks_pass'])
            self.assertTrue(result['failed'])

    def test_phase_and_design_routes_reach_contract(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('phase-1-assess.md', 'phase-2-plan.md', 'phase-4-review.md', 'ui-io-contract.md'):
            self.assertIn('share-presentation', (root / 'skills/build-loop/references' / name).read_text())
        self.assertIn('share-presentation.md', (root / 'skills/ui-design/SKILL.md').read_text())
        contract = (root / 'skills/build-loop/references/share-presentation.md').read_text()
        for requirement in ('generic owner', 'unverified', 'privacy', 'share_presentation_check.py'):
            self.assertIn(requirement, contract)


if __name__ == '__main__':
    unittest.main()
