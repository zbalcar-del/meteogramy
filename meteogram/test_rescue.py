from datetime import datetime, timezone
import unittest

import rescue

NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


def feed(*items):
    body = ''.join(
        f'<item><title>{t}</title><link>https://www.horskasluzba.cz/cz/{path}</link>'
        f'<description>{d}</description><pubDate>{date}</pubDate></item>'
        for t, d, date, path in items)
    return f'<?xml version="1.0"?><rss><channel><title>HS</title>{body}</channel></rss>'.encode()


class RescueTest(unittest.TestCase):
    def test_keeps_only_useful_recent_items(self):
        raw = feed(
            ('V Beskydech pomáháme hledat pohřešovanou ženu', 'Policie pátrá po ženě.', 'Mon, 02 Nov 2026 13:00:00 +0000', 'aktuality/1'),
            ('Prodej nepotřebného majetku', 'Nabízíme vybavení.', 'Thu, 22 Oct 2026 10:00:00 +0000', 'aktuality/2'),
            ('Záchranáři spolupracovali při cvičení na lanových drahách', 'Zásah ve výšce.', 'Mon, 19 Oct 2026 10:00:00 +0000', 'aktuality/3'),
            ('Kluzké horské cesty jsou na podzim zrádné', 'Pozor na listí.', 'Tue, 20 Oct 2026 06:00:00 +0000', 'aktuality/4'),
            ('Lavina v Krkonoších', 'Staré.', 'Mon, 01 Sep 2026 06:00:00 +0000', 'aktuality/5'),
            ('Oblastní školení', 'Úraz.', 'Fri, 16 Oct 2026 06:00:00 +0000', 'video-a-foto-galerie/6'),
        )
        items = rescue.parse(raw, NOW)['items']
        self.assertEqual([i['title'] for i in items],
                         ['V Beskydech pomáháme hledat pohřešovanou ženu', 'Kluzké horské cesty jsou na podzim zrádné'])

    def test_generic_words_count_only_in_title(self):
        self.assertFalse(rescue.useful('Záchranáři budou mít čas pod kontrolou', 'Špatné podmínky, noc a mráz.'))
        self.assertTrue(rescue.useful('Zimní podmínky na hřebenech', 'Text.'))

    def test_limits_count_and_shortens_summary(self):
        long = 'slovo ' * 80
        raw = feed(*[(f'Úraz turisty {n}', long, f'Mon, 0{n} Nov 2026 10:00:00 +0000', f'aktuality/{n}') for n in range(1, 5)])
        items = rescue.parse(raw, NOW)['items']
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]['title'], 'Úraz turisty 4')
        self.assertTrue(items[0]['summary'].endswith('…') and len(items[0]['summary']) <= 201)

    def test_search_shown_only_for_a_week(self):
        raw = feed(('Pátráme po pohřešovaném muži', 'Policie pátrá.', 'Mon, 26 Oct 2026 10:00:00 +0000', 'aktuality/7'),
                   ('Úraz na hřebeni', 'Zásah.', 'Mon, 26 Oct 2026 10:00:00 +0000', 'aktuality/8'))
        self.assertEqual([i['title'] for i in rescue.parse(raw, NOW)['items']], ['Úraz na hřebeni'])

    def test_rejects_doctype(self):
        with self.assertRaises(ValueError):
            rescue.parse(b'<!DOCTYPE x><rss/>', NOW)


if __name__ == '__main__':
    unittest.main()
