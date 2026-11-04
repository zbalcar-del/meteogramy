"""Ruční nasazení nesmí použít starý místní běh ani změnit veřejný seznam běhů."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import deploy


class ManualDeployTests(unittest.TestCase):
    def test_build_reads_production_metadata_only(self):
        requested = []
        def reader(path):
            requested.append(path)
            return json.dumps({'runs': ['2026110400']} if path == 'index.json' else {}).encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / 'web/data/index.json'
            local.parent.mkdir(parents=True)
            local.write_text('old-local-data')
            builder = MagicMock()
            with patch.dict('sys.modules', {'build_pages': builder}):
                deploy.prepare_pages(root, reader)
            self.assertEqual(local.read_text(), 'old-local-data')
            self.assertEqual(requested, ['index.json','2026110400/index.json','2026110400/summary.json','snow.json','avalanche.json'])
            data = builder.main.call_args.args[1]
            self.assertEqual(json.loads((data / 'index.json').read_text())['runs'], ['2026110400'])
            self.assertEqual(builder.main.call_args.args[0], root / 'build/manual-deploy/site')

    def test_invalid_run_cannot_request_paths_or_upload(self):
        for runs in ([], ['../../secret'], ['2026110400/other'], '2026110400'):
            builder = MagicMock()
            reader = MagicMock(return_value=json.dumps({'runs': runs}).encode())
            with tempfile.TemporaryDirectory() as directory, patch.dict('sys.modules', {'build_pages': builder}):
                with self.assertRaises(ValueError):
                    deploy.prepare_pages(Path(directory), reader)
            builder.main.assert_not_called()
            reader.assert_called_once_with('index.json')

    def test_missing_snapshot_aborts_before_build(self):
        def reader(path):
            if path == 'avalanche.json':
                raise OSError('offline')
            return json.dumps({'runs': ['2026110400']} if path == 'index.json' else {}).encode()
        builder = MagicMock()
        with tempfile.TemporaryDirectory() as directory, patch.dict('sys.modules', {'build_pages': builder}):
            with self.assertRaises(OSError):
                deploy.prepare_pages(Path(directory), reader)
        builder.main.assert_not_called()
