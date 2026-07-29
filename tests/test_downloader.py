import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lanzou_epub_downloader import downloader


class DownloaderHelpersTest(unittest.TestCase):
    def test_expired_deadline_never_gets_extended(self):
        deadline = time.monotonic() - 1

        self.assertEqual(downloader.timeout_left_ms(deadline), 0)
        self.assertEqual(downloader.remaining_timeout_ms(deadline, 30000), 0)

    def test_remaining_timeout_honors_cap(self):
        deadline = time.monotonic() + 60

        remaining = downloader.remaining_timeout_ms(deadline, 250)

        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 250)

    def test_pick_share_items_keeps_all_bundles_and_skips_epubs(self):
        items = [
            {"text": "bundle2.zip", "href": "https://example/2", "kind": "bundle"},
            {"text": "volume.epub", "href": "https://example/3", "kind": "epub"},
            {"text": "bundle1.zip", "href": "https://example/1", "kind": "bundle"},
        ]

        with mock.patch.dict(os.environ, {"LANZOU_MAX_BUNDLES": "20"}):
            picked = downloader.pick_share_items(items)

        self.assertEqual({item["text"] for item in picked}, {"bundle1.zip", "bundle2.zip"})

    def test_archive_names_are_stable_per_label(self):
        self.assertEqual(
            downloader.archive_name_for_label("share123", "bundle1.zip"),
            "share123_bundle1.zip",
        )

    def test_cleanup_orphan_archives_preserves_state_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_dir = Path(tmp)
            keep = archive_dir / "share_bundle.zip"
            orphan = archive_dir / "orphan.zip"
            generic = archive_dir / "bundle.zip"
            for path in (keep, orphan, generic):
                path.write_bytes(b"data")
            state = {"labels": {"share": {"archive_paths": [str(keep)]}}}

            deleted = downloader.cleanup_orphan_archives(archive_dir, state)

            self.assertEqual(deleted, 2)
            self.assertTrue(keep.exists())
            self.assertFalse(orphan.exists())
            self.assertFalse(generic.exists())


if __name__ == "__main__":
    unittest.main()
