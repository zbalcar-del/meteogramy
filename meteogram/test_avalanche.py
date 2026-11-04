"""Synthetic fixtures only: no archived danger rating is used as current data."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import avalanche as ava
import build_pages

NOW = datetime(2026, 11, 4, 10, tzinfo=timezone.utc)


def bulletin(region='04', **extra):
    return {'region_id': region, 'date_time': '2026-11-04 08:00:00', 'warning_level': 2,
            'warnings': [{'type': 'drifting_snow', 'exposition': 'N, NE, S'}], **extra}


def collect(rows):
    return ava.collect(NOW, lambda _: json.dumps(rows).encode())


class AvalancheTests(unittest.TestCase):
    def test_empty_source_is_not_zero_or_low_risk(self):
        html = ava.render(collect([]), NOW)
        self.assertEqual(html.count('Předpověď není dostupná'), 2)
        self.assertIn('Čekáme na aktuální lavinová data', html)
        self.assertNotIn('role="status" hidden', html)
        self.assertNotIn('data-level="0"', html)
        self.assertNotIn('data-level="1"', html)

    def test_maps_regions_times_levels_and_aspects(self):
        snapshot = collect([bulletin(), bulletin('06')])
        self.assertEqual(snapshot['reports'][0]['issued_at'], '2026-11-04T07:00:00Z')
        self.assertEqual(snapshot['reports'][0]['warnings'][0]['aspects'], ['S', 'SV', 'J'])
        html = ava.render(snapshot, NOW)
        self.assertEqual(html.count('data-level="2"'), 2)
        self.assertIn('class="snow-notice ava-infobar" role="status" hidden', html)
        self.assertIn('4. 11. 2026 08:00 SEČ', html)
        self.assertIn('Navátý sníh', html)

    def test_only_latest_bulletin_per_region(self):
        snapshot = collect([bulletin(date_time='2026-11-04 07:00:00', warning_level=1), bulletin()])
        self.assertEqual(len(snapshot['reports']), 1)
        self.assertEqual(snapshot['reports'][0]['level'], 2)

    def test_unsupported_levels_formats_and_future_are_rejected(self):
        for level in (0, 6, True, None, 'unknown', 2.5):
            with self.assertRaises(ValueError):
                collect([bulletin(warning_level=level)])
        with self.assertRaises(ValueError):
            collect({'error': 'offline'})
        with self.assertRaises(ValueError):
            collect([bulletin(date_time='2026-11-05 08:00:00')])

    def test_yesterdays_bulletin_does_not_display_active_danger(self):
        html = ava.render(collect([bulletin(date_time='2026-11-03 08:00:00')]), NOW)
        self.assertIn('Starší bulletin', html)
        self.assertNotIn('data-level="2"', html)
        self.assertNotIn('ava-problems', html)

    def test_old_snapshot_and_failed_source_cannot_confirm_danger(self):
        snapshot = collect([bulletin()])
        snapshot['retrieved_at'] = ava.iso(NOW - timedelta(hours=3))
        self.assertNotIn('data-level="2"', ava.render(snapshot, NOW))
        snapshot = collect([bulletin()])
        snapshot['ok'] = False
        self.assertNotIn('data-level="2"', ava.render(snapshot, NOW))
        self.assertIn('Předpověď není dostupná', ava.render(None, NOW))

    def test_source_cannot_inject_html(self):
        raw = bulletin(warnings=[{'type': '<script>alert(1)</script>', 'exposition': '<img src=x>'}],
                       description='<script>danger</script>')
        html = ava.render(collect([raw]), NOW)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img', html)
        self.assertIn('Další riziková situace', html)

    def test_static_page_fragment_and_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'data'
            data.mkdir()
            (data / 'avalanche.json').write_text(json.dumps(collect([])))
            build_pages.write_avalanche_page(root / 'site', data)
            page = (root / 'site/lavinova-predpoved/index.html').read_text()
            fragment = (root / 'site/data/avalanche-fragment.html').read_text()
            self.assertIn(fragment, page)
            self.assertIn('id="avalanche">', page)
            self.assertIn('id="snow" hidden', page)
            self.assertIn('class="snow-nav avalanche-nav" aria-current="page"', page)
            self.assertLess(page.index('<span>Sněhové zpravodajství'), page.index('<span>Lavinová předpověď'))
            self.assertIn('href="https://meteogramy.cz/lavinova-predpoved/"', page)

    def test_failed_fetch_does_not_overwrite_saved_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'avalanche.json'
            output.write_text('previous')
            with patch.object(ava, 'OUT', output), patch.object(ava, 'collect', side_effect=OSError('offline')):
                with self.assertRaises(OSError):
                    ava.main()
            self.assertEqual(output.read_text(), 'previous')


if __name__ == '__main__':
    unittest.main()
