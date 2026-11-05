"""Pinned map downloads must preserve existing files and reject corrupt bytes."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fetch_ros_maps', ROOT / 'tools/fetch_ros_maps.py')
maps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maps)


class MapDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.manifest = self.directory / 'maps.json'
        self.payloads = {'maze.yaml': b'image: maze.pgm\n', 'maze.pgm': b'P5\n1 1\n255\n\xff'}
        self.manifest.write_text(json.dumps({
            'commit': 'pinned-fixture', 'raw_base_url': 'https://maps.example',
            'files': [{'name': name, 'bytes': len(content),
                       'sha256': maps.hashlib.sha256(content).hexdigest()}
                      for name, content in self.payloads.items()]}))
        self.destination = self.directory / 'download'

    def response(self, url, **_kwargs):
        return io.BytesIO(self.payloads[url.rsplit('/', 1)[-1]])

    def test_verified_download_then_offline_reuse(self):
        with patch.object(maps, 'urlopen', side_effect=self.response):
            self.assertEqual(maps.fetch(self.destination, self.manifest)['files'], 2)
        with patch.object(maps, 'urlopen', side_effect=AssertionError('unexpected network')):
            maps.fetch(self.destination, self.manifest)
        for name, content in self.payloads.items():
            self.assertEqual((self.destination / name).read_bytes(), content)

    def test_corrupt_existing_file_is_preserved_before_any_download(self):
        self.destination.mkdir()
        existing = self.destination / 'maze.pgm'
        existing.write_bytes(b'local data')
        with patch.object(maps, 'urlopen') as download, self.assertRaises(ValueError):
            maps.fetch(self.destination, self.manifest)
        download.assert_not_called()
        self.assertEqual(existing.read_bytes(), b'local data')
        self.assertEqual(list(self.destination.iterdir()), [existing])

    def test_failed_later_download_installs_no_partial_maps(self):
        with patch.object(maps, 'urlopen', side_effect=[self.response('maze.yaml'), io.BytesIO(b'corrupt')]), \
                self.assertRaises(ValueError):
            maps.fetch(self.destination, self.manifest)
        self.assertEqual(list(self.destination.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
