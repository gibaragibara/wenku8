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

    def test_direct_download_streams_to_disk(self):
        class FakeResponse:
            status_code = 200
            url = "https://example.test/book.zip"
            headers = {
                "content-disposition": 'attachment; filename="book.zip"',
                "content-length": "6",
            }

            def iter_content(self, chunk_size):
                self.assert_chunk_size = chunk_size
                yield b"abc"
                yield b"def"

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            response = FakeResponse()
            with mock.patch.object(downloader.SESSION, "get", return_value=response):
                path = downloader.download_direct_file(
                    "https://example.test/book.zip",
                    Path(tmp),
                    "book",
                    "https://example.test/share",
                    1000,
                )

            self.assertIsNotNone(path)
            self.assertEqual(path.read_bytes(), b"abcdef")
            self.assertEqual(response.assert_chunk_size, 65536)
            self.assertEqual(list(Path(tmp).glob("*.part")), [])

    def test_navigation_timeout_cap_defaults_and_clamps(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LANZOU_NAV_TIMEOUT_MS", None)
            self.assertEqual(downloader.navigation_timeout_cap_ms(), 90000)
        with mock.patch.dict(os.environ, {"LANZOU_NAV_TIMEOUT_MS": "5000"}):
            self.assertEqual(downloader.navigation_timeout_cap_ms(), 15000)
        with mock.patch.dict(os.environ, {"LANZOU_NAV_TIMEOUT_MS": "999999"}):
            self.assertEqual(downloader.navigation_timeout_cap_ms(), 180000)

    def test_navigation_timeout_error_detection(self):
        self.assertTrue(
            downloader.is_navigation_timeout_error(
                Exception("Page.goto: Timeout 30000ms exceeded.")
            )
        )
        self.assertFalse(downloader.is_navigation_timeout_error(Exception("no iframe")))

    def test_retryable_item_status(self):
        self.assertTrue(downloader.is_retryable_item_status("no_download"))
        self.assertTrue(downloader.is_retryable_item_status("nav_timeout"))
        self.assertTrue(
            downloader.is_retryable_item_status(
                "exception: Page.goto: Timeout 30000ms exceeded."
            )
        )
        self.assertFalse(downloader.is_retryable_item_status("skip_zht_epub"))

    def test_safe_page_goto_retries_then_succeeds(self):
        page = mock.Mock()
        page.url = "about:blank"
        page.content.return_value = ""
        page.goto.side_effect = [
            Exception("Page.goto: Timeout 30000ms exceeded."),
            None,
        ]
        deadline = time.monotonic() + 30

        with mock.patch.dict(os.environ, {"LANZOU_NAV_RETRIES": "3", "LANZOU_NAV_TIMEOUT_MS": "20000"}):
            ok = downloader.safe_page_goto(
                page,
                "https://wenku8.lanzov.com/b00g4erhgb",
                deadline_ts=deadline,
                label="test",
            )

        self.assertTrue(ok)
        self.assertEqual(page.goto.call_count, 2)

    def test_safe_page_goto_accepts_partial_load(self):
        page = mock.Mock()
        page.url = "https://wenku8.lanzov.com/b00g4erhgb"
        page.content.return_value = "<html>" + ("x" * 250)
        page.goto.side_effect = Exception("Page.goto: Timeout 30000ms exceeded.")
        deadline = time.monotonic() + 30

        ok = downloader.safe_page_goto(
            page,
            "https://wenku8.lanzov.com/b00g4erhgb",
            deadline_ts=deadline,
            max_attempts=1,
        )

        self.assertTrue(ok)

    def test_safe_page_goto_gives_up_when_deadline_expired(self):
        page = mock.Mock()
        page.goto = mock.Mock()

        ok = downloader.safe_page_goto(
            page,
            "https://example.test",
            deadline_ts=time.monotonic() - 1,
        )

        self.assertFalse(ok)
        page.goto.assert_not_called()

    def test_direct_download_rejects_oversize_response(self):
        response = mock.Mock()
        response.status_code = 200
        response.headers = {"content-length": "5"}
        response.close = mock.Mock()

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"LANZOU_MAX_DOWNLOAD_BYTES": "4"}):
                with mock.patch.object(downloader.SESSION, "get", return_value=response):
                    path = downloader.download_direct_file(
                        "https://example.test/book.zip",
                        Path(tmp),
                        "book",
                        "https://example.test/share",
                        1000,
                    )

            self.assertIsNone(path)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            response.iter_content.assert_not_called()


if __name__ == "__main__":
    unittest.main()
