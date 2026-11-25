import tempfile
import unittest
from pathlib import Path

import build_pages
import update_hosting


PAGE = ('<html><head><style>body { color: red }</style><script type="application/ld+json">{}</script></head>'
        '<body><p>{page}</p><script src="/assets/snow.js"></script><script>const a = 1;</script></body></html>')


class SharedAssetsTest(unittest.TestCase):
    def test_moves_style_and_script_into_one_shared_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            first = build_pages.shared_assets(out, PAGE.replace('{page}', 'Praděd'))
            second = build_pages.shared_assets(out, PAGE.replace('{page}', 'Sněžka'))
            files = sorted(p.name for p in (out / 'assets').iterdir())
            self.assertEqual(len(files), 2)
            css, js = (n for n in sorted(files, key=lambda n: not n.endswith('.css')))
            self.assertEqual((out / 'assets' / css).read_text(), 'body { color: red }')
            self.assertEqual((out / 'assets' / js).read_text(), 'const a = 1;')
            for html in (first, second):
                self.assertIn(f'<link rel="stylesheet" href="/assets/{css}">', html)
                self.assertIn(f'<script src="/assets/{js}"></script>', html)
                self.assertNotIn('<style>', html)
                self.assertIn('application/ld+json', html)  # strukturovaná data zůstávají ve stránce

    def test_changed_content_gets_new_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            build_pages.shared_assets(out, PAGE)
            build_pages.shared_assets(out, PAGE.replace('const a = 1;', 'const a = 2;'))
            self.assertEqual(len(list((out / 'assets').glob('app.*.js'))), 2)

    def test_page_upload_brings_its_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / 'index.html'
            page.write_text('<link rel="stylesheet" href="/assets/app.0123456789ab.css">'
                            '<script src="/assets/app.ba9876543210.js"></script>')
            files = update_hosting.with_assets({'lavinova-predpoved/index.html': page, 'data/x.json': page})
            self.assertEqual(files['assets/app.0123456789ab.css'], update_hosting.SITE / 'assets/app.0123456789ab.css')
            self.assertIn('assets/app.ba9876543210.js', files)
            self.assertEqual(len(files), 4)


if __name__ == '__main__':
    unittest.main()
