import unittest
from unittest.mock import patch

from exh_rec.exhentai import (
    check_access,
    merge_gallery,
    normalize_cookie_header,
    parse_gallery_detail,
    parse_gallery_list,
    valid_cookie_header,
)


class ParserTest(unittest.TestCase):
    def test_parse_gallery_from_result_like_html(self):
        html = """
        <tr class="gtr0">
          <td><div class="glthumb"><a href="https://exhentai.org/g/12345/abcdef1234/"><img data-src="https://t.example/thumb.jpg"></a></div></td>
          <td>
            <div class="cn">Manga</div>
            <a class="glink" href="https://exhentai.org/g/12345/abcdef1234/">Sample Gallery Title</a>
            <a href="https://exhentai.org/tag/artist:test_artist">artist:test_artist</a>
            <a href="https://exhentai.org/tag/language:english">language:english</a>
            <span title="Rating: 4.5"></span>
          </td>
        </tr>
        """
        galleries = parse_gallery_list(html, source_query="artist:test_artist")
        self.assertEqual(len(galleries), 1)
        gallery = galleries[0]
        self.assertEqual(gallery.gid, "12345")
        self.assertEqual(gallery.token, "abcdef1234")
        self.assertEqual(gallery.title, "Sample Gallery Title")
        self.assertEqual(gallery.category, "Manga")
        self.assertEqual(gallery.thumb_url, "https://t.example/thumb.jpg")
        self.assertEqual(gallery.rating, 4.5)
        self.assertIn("artist:test artist", gallery.tags)
        self.assertIn("language:english", gallery.tags)

    def test_parse_ehentai_gallery_links_as_canonical_exhentai_urls(self):
        html = """
        <tr class="gtr0">
          <td><a class="glink" href="https://e-hentai.org/g/54321/abc123def0/">Mirror Host Title</a></td>
          <td>
            <a href="https://e-hentai.org/tag/artist:mirror_artist">tag</a>
            <a href="/tag/female:mirror_tag">tag</a>
          </td>
        </tr>
        """

        galleries = parse_gallery_list(html)

        self.assertEqual(len(galleries), 1)
        self.assertEqual(galleries[0].url, "https://exhentai.org/g/54321/abc123def0/")
        self.assertIn("artist:mirror artist", galleries[0].tags)
        self.assertIn("female:mirror tag", galleries[0].tags)

    def test_parse_relative_gallery_links_as_canonical_exhentai_urls(self):
        html = """
        <tr class="gtr0">
          <td><a class="glink" href="/g/54322/abc123def1/">Relative Host Title</a></td>
        </tr>
        <tr class="gtr0">
          <td><a class="glink" href="//exhentai.org/g/54323/abc123def2/">Protocol Relative Title</a></td>
        </tr>
        """

        galleries = parse_gallery_list(html)

        self.assertEqual([gallery.url for gallery in galleries], [
            "https://exhentai.org/g/54322/abc123def1/",
            "https://exhentai.org/g/54323/abc123def2/",
        ])

    def test_parse_gallery_list_reads_css_background_thumbnail(self):
        html = """
        <tr class="gtr0">
          <td>
            <a href="https://exhentai.org/g/55555/abc555/">
              <div style="background-image: url('https://t.example/css-thumb.jpg')"></div>
            </a>
          </td>
          <td><a class="glink" href="https://exhentai.org/g/55555/abc555/">CSS Thumb Title</a></td>
        </tr>
        """

        galleries = parse_gallery_list(html)

        self.assertEqual(len(galleries), 1)
        self.assertEqual(galleries[0].thumb_url, "https://t.example/css-thumb.jpg")

    def test_parse_gallery_detail(self):
        html = """
        <html>
          <h1 id="gn">Detailed Gallery Title</h1>
          <div id="gdc"><div class="cn">Doujinshi</div></div>
          <div id="gdn"><a>UploaderName</a></div>
          <table><tr><td>Posted:</td><td>2026-06-01 12:34</td></tr></table>
          <div id="gd1"><img src="https://t.example/detail.jpg"></div>
          <a href="https://exhentai.org/tag/artist:detail_artist">tag</a>
          <a href="https://exhentai.org/tag/female:tag_one">tag</a>
          <script>Average: 4.72</script>
        </html>
        """
        gallery = parse_gallery_detail(html, "https://exhentai.org/g/67890/fedcba9876/")
        self.assertEqual(gallery.title, "Detailed Gallery Title")
        self.assertEqual(gallery.category, "Doujinshi")
        self.assertEqual(gallery.uploader, "UploaderName")
        self.assertEqual(gallery.posted_at, "2026-06-01 12:34")
        self.assertEqual(gallery.thumb_url, "https://t.example/detail.jpg")
        self.assertEqual(gallery.rating, 4.72)
        self.assertIn("artist:detail artist", gallery.tags)
        self.assertIn("female:tag one", gallery.tags)

    def test_parse_gallery_detail_reads_exhentai_taglist_attributes(self):
        html = """
        <html>
          <h1 id="gn">Attribute Tags</h1>
          <div id="taglist">
            <div class="gt" title="artist:taglist_artist">taglist artist</div>
            <div class="gtl" id="ta_female:big_breasts">big breasts</div>
            <div class="gt" title="parody:space%20title">space title</div>
          </div>
        </html>
        """

        gallery = parse_gallery_detail(html, "https://exhentai.org/g/67891/fedcba9877/")

        self.assertIn("artist:taglist artist", gallery.tags)
        self.assertIn("female:big breasts", gallery.tags)
        self.assertIn("parody:space title", gallery.tags)

    def test_merge_gallery_keeps_source_query_and_combines_tags(self):
        base = parse_gallery_list(
            '<a class="glink" href="https://exhentai.org/g/1/abcdef/">Base Title</a>'
            '<a href="https://exhentai.org/tag/language:english">tag</a>',
            source_query="language:english",
        )[0]
        detail = parse_gallery_detail(
            '<h1 id="gn">Detail Title</h1><a href="https://exhentai.org/tag/artist:abc">tag</a>',
            "https://exhentai.org/g/1/abcdef/",
        )
        merged = merge_gallery(base, detail)
        self.assertEqual(merged.title, "Detail Title")
        self.assertEqual(merged.source_query, "language:english")
        self.assertIn("language:english", merged.tags)
        self.assertIn("artist:abc", merged.tags)

    def test_check_access_reports_visible_galleries(self):
        html = '<a class="glink" href="https://exhentai.org/g/1/abcdef/">Visible Gallery</a>'
        with patch("exh_rec.exhentai.fetch_page", return_value=html):
            result = check_access("ipb_member_id=1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["gallery_count"], 1)

    def test_check_access_reports_no_gallery_listings(self):
        with patch("exh_rec.exhentai.fetch_page", return_value="<html>login</html>"):
            result = check_access("bad=cookie")
        self.assertFalse(result["ok"])
        self.assertEqual(result["gallery_count"], 0)

    def test_normalize_cookie_header_keeps_regular_cookie_header(self):
        self.assertEqual(
            normalize_cookie_header("Cookie: ipb_member_id=123; ipb_pass_hash=abc; sk=xyz"),
            "ipb_member_id=123; ipb_pass_hash=abc; sk=xyz",
        )

    def test_valid_cookie_header_requires_name_value_pairs(self):
        self.assertTrue(valid_cookie_header("ipb_member_id=123; ipb_pass_hash=abc"))
        self.assertFalse(valid_cookie_header("not a cookie"))
        self.assertFalse(valid_cookie_header("ipb_member_id=; ipb_pass_hash=abc"))
        self.assertFalse(valid_cookie_header("bad name=abc"))

    def test_normalize_cookie_header_accepts_browser_cookie_table(self):
        exported = "\n".join(
            [
                "Name\tValue\tDomain\tPath\tExpires\tSize\tHttpOnly\tSecure\tSameSite",
                "ipb_member_id\t123\t.e-hentai.org\t/\t2026-12-08T06:34:06.123Z\t19\t\t\t\t\t\tMedium",
                "ipb_pass_hash\tabc123\t.e-hentai.org\t/\t2026-12-08T06:34:06.123Z\t45\t\t\t\t\t\tMedium",
                "sk\tsecret-session\t.e-hentai.org\t/\t2026-12-08T06:34:08.149Z\t30\t\t\t\t\t\tMedium",
            ]
        )
        self.assertEqual(
            normalize_cookie_header(exported),
            "ipb_member_id=123; ipb_pass_hash=abc123; sk=secret-session",
        )

    def test_normalize_cookie_header_does_not_treat_plain_words_as_cookie_table(self):
        normalized = normalize_cookie_header("this is not a cookie")

        self.assertFalse(valid_cookie_header(normalized))

    def test_normalize_cookie_header_accepts_netscape_cookie_file(self):
        exported = "\n".join(
            [
                "# Netscape HTTP Cookie File",
                ".e-hentai.org\tTRUE\t/\tTRUE\t1796711646\tipb_member_id\t123",
                ".e-hentai.org\tTRUE\t/\tTRUE\t1796711646\tipb_pass_hash\tabc123",
                "#HttpOnly_.exhentai.org\tTRUE\t/\tTRUE\t1796711648\tsk\tsecret-session",
            ]
        )
        self.assertEqual(
            normalize_cookie_header(exported),
            "ipb_member_id=123; ipb_pass_hash=abc123; sk=secret-session",
        )


if __name__ == "__main__":
    unittest.main()
