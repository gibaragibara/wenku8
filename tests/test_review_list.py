import unittest

import main


PASTE_HTML = (
    '<a href="https://paste.gentoo.zip" target="_blank">https://paste.gentoo.zip</a>'
    '/pqdtNM4x<br>最新的 epub 为自动生成'
)
TXT_HTML = '<a href="https://0x0.st/8QWZ.txt" target="_blank">https://0x0.st/8QWZ.txt</a><br>'
DISCUSSION_HTML = '<div>这一卷的人物关系写得很好</div>'


class ExtractLatestListUrlTest(unittest.TestCase):
    def test_anchor_plus_suffix(self):
        self.assertEqual(
            main.extract_latest_list_url(PASTE_HTML),
            'https://paste.gentoo.zip/pqdtNM4x',
        )

    def test_direct_txt_link(self):
        self.assertEqual(
            main.extract_latest_list_url(TXT_HTML),
            'https://0x0.st/8QWZ.txt',
        )

    def test_missing_link(self):
        self.assertIsNone(main.extract_latest_list_url(DISCUSSION_HTML))
        self.assertIsNone(main.extract_latest_list_url(''))


class CollectReviewEntriesTest(unittest.TestCase):
    def test_untitled_share_on_first_row_refreshes_list_and_is_catalogued(self):
        fetched = []
        refreshed = []

        def fetch_post(link):
            fetched.append(link)
            if link.endswith('319982'):
                return PASTE_HTML
            return DISCUSSION_HTML

        rows = [
            {
                'raw_title': '第二卷',
                'post_link': 'https://www.wenku8.net/reviewshow.php?rid=319982',
                'novel_title': 'A子与社团时间',
                'novel_link': 'https://www.wenku8.net/book/1.htm',
            },
            {
                'raw_title': '第三卷 epub',
                'post_link': 'https://www.wenku8.net/reviewshow.php?rid=319981',
                'novel_title': '七星降灵学园的恶魔',
                'novel_link': 'https://www.wenku8.net/book/2.htm',
            },
        ]
        entries, stopped = main.collect_review_entries(
            rows,
            page_num=1,
            latest_post_link='https://www.wenku8.net/reviewshow.php?rid=319981',
            fetch_post=fetch_post,
            refresh_list=refreshed.append,
        )

        self.assertEqual(refreshed, ['https://paste.gentoo.zip/pqdtNM4x'])
        self.assertEqual(fetched, ['https://www.wenku8.net/reviewshow.php?rid=319982'])
        self.assertTrue(stopped)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][0], '"第二卷"')
        self.assertEqual(entries[0][2], '"A子与社团时间"')

    def test_known_epub_title_still_refreshes_before_stopping(self):
        refreshed = []
        rows = [
            {
                'raw_title': '第三卷 epub',
                'post_link': 'https://www.wenku8.net/reviewshow.php?rid=319981',
                'novel_title': '七星',
                'novel_link': 'https://www.wenku8.net/book/2.htm',
            }
        ]
        entries, stopped = main.collect_review_entries(
            rows,
            page_num=1,
            latest_post_link=rows[0]['post_link'],
            fetch_post=lambda _link: PASTE_HTML,
            refresh_list=refreshed.append,
        )
        self.assertEqual(refreshed, ['https://paste.gentoo.zip/pqdtNM4x'])
        self.assertEqual(entries, [])
        self.assertTrue(stopped)

    def test_discussion_row_is_skipped_and_next_epub_refreshes(self):
        fetched = []
        refreshed = []

        def fetch_post(link):
            fetched.append(link)
            if link.endswith('10'):
                return DISCUSSION_HTML
            return PASTE_HTML

        rows = [
            {
                'raw_title': '读后感',
                'post_link': 'https://example/10',
                'novel_title': '某书',
                'novel_link': 'https://example/book.htm',
            },
            {
                'raw_title': '第一卷 epub',
                'post_link': 'https://example/11',
                'novel_title': '新书',
                'novel_link': 'https://example/new.htm',
            },
        ]
        entries, stopped = main.collect_review_entries(
            rows,
            page_num=1,
            latest_post_link=None,
            fetch_post=fetch_post,
            refresh_list=refreshed.append,
        )
        self.assertEqual(fetched, ['https://example/10', 'https://example/11'])
        self.assertEqual(refreshed, ['https://paste.gentoo.zip/pqdtNM4x'])
        self.assertFalse(stopped)
        self.assertEqual([entry[0] for entry in entries], ['"第一卷"'])

    def test_later_pages_ignore_untitled_posts_and_do_not_fetch(self):
        rows = [
            {
                'raw_title': '第二卷',
                'post_link': 'https://example/1',
                'novel_title': '某书',
                'novel_link': 'https://example/book.htm',
            },
            {
                'raw_title': '第一卷 epub',
                'post_link': 'https://example/2',
                'novel_title': '某书',
                'novel_link': 'https://example/book.htm',
            },
        ]

        def fetch_post(_link):
            raise AssertionError('page 2 must not fetch post bodies')

        entries, stopped = main.collect_review_entries(
            rows,
            page_num=2,
            latest_post_link=None,
            fetch_post=fetch_post,
            refresh_list=lambda _url: (_ for _ in ()).throw(AssertionError('no refresh')),
        )
        self.assertFalse(stopped)
        self.assertEqual([entry[0] for entry in entries], ['"第一卷"'])


if __name__ == '__main__':
    unittest.main()
