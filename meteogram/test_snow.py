"""Regrese pro měření: chybějící hodnoty, příznaky, změny a serverové stránky."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch, MagicMock

import snow
import build_pages
import update_hosting
import avalanche

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
PLACE = {'id': 'test', 'name': 'Test', 'range': 'Hory', 'alt': 1000, 'station': 'GH'}
WSI = '0-test'


def observation(value, day=4, quality=0, flag='', element='SCE'):
    return {'STATION': WSI, 'ELEMENT': element, 'VTYPE': '06:00',
            'DT': f'2026-11-{day:02d}T06:00:00Z', 'VAL': value, 'QUALITY': quality, 'FLAG': flag}


def record(rows):
    return snow.station_record(PLACE, WSI, rows, NOW)


def snapshot(records):
    return {'version': 1, 'retrieved_at': snow.iso(NOW), 'latest_at': '2026-11-04T06:00:00Z',
            'partial': False, 'stations': records}


class Measurements(unittest.TestCase):
    def test_zero_is_measurement_missing_is_not(self):
        self.assertEqual(record([observation(0)])['depth'], 0)
        for value in (None, True, float('nan'), -2):
            self.assertIsNone(record([observation(value)])['depth'])
        self.assertIsNone(record([observation(12, quality=2)])['depth'])

    def test_latest_invalid_does_not_reuse_old_valid_value(self):
        row = record([observation(20, day=3), observation(None)])
        self.assertIsNone(row['depth'])
        self.assertEqual(row['observed_at'], '2026-11-04T06:00:00Z')

    def test_change_requires_adjacent_days(self):
        self.assertEqual(record([observation(10, day=3), observation(14)])['change'], 4)
        self.assertIsNone(record([observation(10, day=2), observation(14)])['change'])
        self.assertEqual(record([observation(14), observation(-3, element='SCEdif')])['change'], -3)

    def test_symbolic_depth_and_quality_are_explicit(self):
        for flag in ('N', 'P'):
            row = record([observation(10, day=3), observation(0, flag=flag)])
            self.assertIsNone(row['change'])
            self.assertIn(snow.FLAGS[flag], row['notes'])
        self.assertIn('Odhadnutá hodnota', record([observation(5, quality=3)])['notes'])

    def test_future_and_other_stations_do_not_contaminate_measurements(self):
        wrong = observation(100)
        wrong['STATION'] = 'elsewhere'
        row = record([wrong, observation(5, day=3), observation(100, day=5)])
        self.assertEqual(row['depth'], 5)
        self.assertEqual(len(row['history']), 1)

    def test_maximum_includes_older_latest_station_reading_with_date(self):
        old = record([observation(100, day=2)])
        current = record([observation(0)])
        html = snow.render(snapshot([old, current]), NOW)
        card = html.split('snow-highlight-main', 1)[1].split('</a>', 1)[0]
        self.assertIn('<strong>100<small> cm', card)
        self.assertIn('Měřeno <time datetime="2026-11-02T06:00:00Z"', card)
        self.assertIn('Změna není k dispozici', html)
        self.assertIn('Starší měření', html)

    def test_only_measured_stations_render_including_zero(self):
        missing = record([observation(None)])
        missing['id'] = 'missing'
        missing['range'] = 'Pohoří bez měření'
        valid = record([observation(0)])
        valid['name'] = '<img src=x onerror=alert(1)>'
        html = snow.render(snapshot([missing, valid]), NOW)
        self.assertNotIn('<img src=x', html)
        self.assertIn('&lt;img', html)
        self.assertNotIn('href="/missing/"', html)
        self.assertNotIn('Pohoří bez měření', html)
        self.assertNotIn('data-depth=""', html)
        self.assertIn('data-depth="0"', html)

    def test_collector_uses_previous_month_at_month_boundary(self):
        def tab(columns, rows):
            return json.dumps({'data': {'data': {'header': ','.join(columns), 'values': rows}}}).encode()
        seen = []
        def reader(url):
            seen.append(url)
            if url.endswith('metadata/'):
                return b'<a href="meta1-20261104.json"></a><a href="meta2-20261104.json"></a>'
            if 'meta1-' in url:
                return tab(['GH_ID', 'WSI'], [['GH', WSI]])
            if 'meta2-' in url:
                return tab(['OBS_TYPE', 'WSI', 'EG_EL_ABBREVIATION', 'UN_DESCRIPTION', 'SCHEDULE'],
                           [['DLY', WSI, 'SCE', 'cm', '06:00']])
            columns = list(observation(0))
            return tab(columns, [list(observation(0).values())] if '-202611.json' in url else [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'meteogram').mkdir()
            (root / 'meteogram/places.json').write_text(json.dumps([PLACE]))
            with patch.object(snow, 'ROOT', root):
                result = snow.collect(NOW, reader)
        self.assertEqual(result['stations'][0]['depth'], 0)
        self.assertTrue(any('/daily/10/dly-' in url and url.endswith('-202610.json') for url in seen))

    def test_failed_collection_preserves_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'snow.json'
            output.write_text('previous')
            with patch.object(snow, 'OUT', output), patch.object(snow, 'collect', side_effect=OSError('offline')):
                with self.assertRaises(OSError):
                    snow.main()
            self.assertEqual(output.read_text(), 'previous')

    def test_hourly_snow_publishes_even_without_new_forecast(self):
        client = MagicMock()
        client.__enter__.return_value = client
        paramiko = types.SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: None)
        forecast = types.SimpleNamespace(list_runs=lambda: {'2026110300': {'temperature': 1}},
                                         FIELDS=['temperature'], OROGRAPHY='temperature')
        pages = MagicMock()
        environment = {'SFTP_HOST': 'example', 'SFTP_PORT': '22', 'SFTP_USER': 'test',
                       'SFTP_PASSWORD': 'test', 'SFTP_REMOTE_DIR': '/public_html',
                       'GITHUB_EVENT_NAME': 'schedule'}
        with patch.dict('sys.modules', {'paramiko': paramiko, 'fetch': forecast, 'build_pages': pages}), \
             patch.dict('os.environ', environment), \
             patch.object(snow, 'main'), \
             patch.object(avalanche, 'main'), \
             patch.object(update_hosting, 'remote_runs', return_value=(b'{}', ['2026110300'])), \
             patch.object(update_hosting, 'upload') as upload:
            update_hosting.main()
        pages.main.assert_not_called()
        pages.write_snow_page.assert_called_once()
        pages.write_avalanche_page.assert_called_once()
        files = upload.call_args_list[0].args[1]
        self.assertEqual(set(files), {'snehove-zpravodajstvi/index.html', 'data/snow-fragment.html', 'data/snow.json'})

    def test_static_page_and_fragment_share_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'data'
            data.mkdir()
            (data / 'snow.json').write_text(json.dumps(snapshot([record([observation(5)])])))
            build_pages.write_snow_page(root / 'site', data)
            html = (root / 'site/snehove-zpravodajstvi/index.html').read_text()
            fragment = (root / 'site/data/snow-fragment.html').read_text()
            self.assertIn(fragment, html)
            self.assertIn('id="snow">', html)
            self.assertIn('id="home" hidden', html)
            self.assertIn('href="https://meteogramy.cz/snehove-zpravodajstvi/"', html)
            self.assertLess(html.index('class="snow-nav"'), html.index('id="ddlab"'))


if __name__ == '__main__':
    unittest.main()
