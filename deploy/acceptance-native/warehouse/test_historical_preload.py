import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import warehouse_acceptance as acceptance
class HistoricalPreloadTests(unittest.TestCase):
    def test_existing_cache_fails_before_download(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'setup/2026-10-15/cache/ignition-fuel').mkdir(parents=True)
            with patch.object(acceptance,'download_fuel_archive') as download:
                with self.assertRaises(ValueError):acceptance.preload_fuel(root,root,{})
                download.assert_not_called()
    def test_absent_cache_uses_only_historical_materializer(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'data';receipt={'historical_cache_verified':True}
            with patch.object(acceptance,'download_fuel_archive',return_value=archive) as download,patch.object(acceptance,'materialize',return_value=receipt) as materialize:
                self.assertEqual(acceptance.preload_fuel(root,root,{}),receipt)
                download.assert_called_once_with(root)
                materialize.assert_called_once_with(archive,root/'setup/2026-10-15/cache/ignition-fuel')
if __name__=='__main__':unittest.main()
