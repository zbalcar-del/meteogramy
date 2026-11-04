from datetime import datetime, timezone
import unittest

import alerts

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


def bulletin(infos, status='Actual'):
    return (f'<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">'
            f'<sender>chmi@chmi.cz</sender><sent>2026-11-04T09:00:00Z</sent>'
            f'<status>{status}</status><scope>Public</scope><msgType>Update</msgType>'
            f'{infos}</alert>').encode()


def info(event='Silný vítr', severity='Moderate', certainty='Likely', language='cs',
         end='2026-11-05T12:00:00Z', area='Liberecký kraj'):
    return (f'<info><language>{language}</language><event>{event}</event>'
            f'<severity>{severity}</severity><certainty>{certainty}</certainty>'
            f'<onset>2026-11-04T18:00:00Z</onset>'
            + (f'<expires>{end}</expires>' if end else '')
            + f'<area><areaDesc>{area}</areaDesc></area></info>')


class AlertTests(unittest.TestCase):
    def test_green_blocks_are_not_warnings(self):
        result = alerts.parse(bulletin(info('Žádná výstraha', 'Minor', 'Unlikely', end=None)), NOW)
        self.assertTrue(result['ok'])
        self.assertEqual(result['warnings'], [])

    def test_future_warnings_kept_and_english_ignored(self):
        result = alerts.parse(bulletin(info() + info(language='en')), NOW)
        self.assertEqual(len(result['warnings']), 1)
        self.assertEqual(result['warnings'][0]['level'], 1)

    def test_expired_and_cancelled_blocks_ignored(self):
        result = alerts.parse(bulletin(info(end='2026-11-04T11:00:00Z') + info(certainty='Unlikely')), NOW)
        self.assertEqual(result['warnings'], [])

    def test_indefinite_warning_and_severity_sort(self):
        result = alerts.parse(bulletin(info() + info('Povodňové ohrožení', 'Extreme', end=None)), NOW)
        self.assertEqual([w['level'] for w in result['warnings']], [3, 1])
        self.assertIsNone(result['warnings'][0]['expires'])

    def test_same_warning_merges_regions(self):
        result = alerts.parse(bulletin(info() + info(area='Královéhradecký kraj')), NOW)
        self.assertEqual(len(result['warnings']), 1)
        self.assertEqual(len(result['warnings'][0]['areas']), 2)

    def test_outlook_is_not_dropped(self):
        result = alerts.parse(bulletin(info(severity='Unknown', certainty='Unknown')), NOW)
        self.assertEqual(result['warnings'][0]['level'], 0)

    def test_unrecognized_protocol_is_not_clear(self):
        for raw in [bulletin('', status='Test'), bulletin(info(language='en')),
                    bulletin(info(severity='Unexpected')), bulletin(info(area='')),
                    bulletin(info(end='not-a-date')), b'<html>unavailable</html>']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                alerts.parse(raw, NOW)


if __name__ == '__main__':
    unittest.main()
