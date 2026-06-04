import unittest
from unittest.mock import patch

from exh_rec import exhentai
from exh_rec.exhentai import (
    SampleThumb,
    apply_gallery_metadata,
    check_access,
    Gallery,
    fetch_gallery_metadata,
    merge_gallery,
    normalize_cookie_header,
    parse_gallery_detail,
    parse_gallery_list,
    parse_gallery_pages,
    parse_gallery_pages_rich,
    parse_sprite_previews,
    sample_entry_url,
    sample_storage,
    sample_thumb_host,
    sample_page_url,
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

    def test_parse_gallery_list_ignores_css_sprite_thumbnail(self):
        html = """
        <tr class="gtr0">
          <td>
            <a href="https://exhentai.org/g/55556/abc556/">
              <div style="height:141px;width:100px;background:transparent url(https://s.exhentai.org/w/01/543/32150-shared.webp) -100px -282px no-repeat"></div>
            </a>
          </td>
          <td><a class="glink" href="https://exhentai.org/g/55556/abc556/">Sprite Thumb Title</a></td>
        </tr>
        """

        galleries = parse_gallery_list(html)

        self.assertEqual(len(galleries), 1)
        self.assertIsNone(galleries[0].thumb_url)

    def test_parse_gallery_list_prefers_data_src_over_blank_placeholder(self):
        # Lazy-loaded list thumbnails carry the real cover in data-src while src holds
        # a blank.gif placeholder; the real URL must win regardless of attribute order.
        html = """
        <tr class="gtr0">
          <td><div class="glthumb"><a href="https://exhentai.org/g/55557/abc557/">
            <img data-src="https://s.exhentai.org/t/aa/lazy-cover.jpg" src="https://exhentai.org/img/blank.gif">
          </a></div></td>
          <td><a class="glink" href="https://exhentai.org/g/55557/abc557/">Lazy Thumb Title</a></td>
        </tr>
        """

        galleries = parse_gallery_list(html)

        self.assertEqual(len(galleries), 1)
        self.assertEqual(galleries[0].thumb_url, "https://s.exhentai.org/t/aa/lazy-cover.jpg")

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

    def test_parse_gallery_pages_reads_length_and_thumbnails(self):
        html = """
        <html>
          <div id="gd1"><img src="https://t.example/cover.jpg"></div>
          <table><tr><td class="gdt1">Length:</td><td class="gdt2">1,312 pages</td></tr></table>
          <div id="gdt">
            <div class="gdtl"><a href="https://exhentai.org/s/aa/1-1"><img src="https://s.exhentai.org/t/aa/1.jpg"></a></div>
            <div class="gdtl"><a href="https://exhentai.org/s/bb/1-2"><img src="//s.exhentai.org/t/bb/2.jpg"></a></div>
            <div class="gdtl"><a href="https://exhentai.org/s/cc/1-3"><div style="background:transparent url(https://abc123.hath.network/c2/a/1.webp) 0 0 no-repeat"></div></a></div>
          </div>
          <div id="gdb"><a href="https://exhentai.org/g/1/a/?p=1"><img src="https://s.exhentai.org/img/should_not_count.jpg"></a></div>
        </html>
        """

        page_count, thumbs = parse_gallery_pages(html)

        self.assertEqual(page_count, 1312)
        self.assertEqual(
            thumbs,
            [
                "https://s.exhentai.org/t/aa/1.jpg",
                "https://s.exhentai.org/t/bb/2.jpg",
                "https://abc123.hath.network/c2/a/1.webp",
            ],
        )

    def test_parse_gallery_pages_ignores_css_sprite_samples(self):
        html = """
        <html>
          <table><tr><td>Length:</td><td>2 pages</td></tr></table>
          <div id="gdt">
            <div class="gdtl"><a href="https://exhentai.org/s/aa/9-1"><div style="background:transparent url(https://abc123.hath.network/c2/sprite/9.webp) -100px -200px no-repeat"></div></a></div>
            <div class="gdtl"><a href="https://exhentai.org/s/bb/9-2"><img src="https://s.exhentai.org/t/bb/real.jpg"></a></div>
          </div>
          <div id="gdb">nav</div>
        </html>
        """

        _, thumbs = parse_gallery_pages(html)

        self.assertEqual(thumbs, ["https://s.exhentai.org/t/bb/real.jpg"])

    def test_parse_gallery_detail_reads_css_cover_before_placeholder_images(self):
        html = """
        <html>
          <div id="gd1"><div style="width:250px; height:343px; background:transparent url(https://s.exhentai.org/w/02/428/84948-cover.webp) 0 0 no-repeat"></div></div>
          <img src="https://exhentai.org/img/blank.gif">
          <h1 id="gn">CSS Cover Gallery</h1>
        </html>
        """

        gallery = parse_gallery_detail(html, "https://exhentai.org/g/9/abc/")

        self.assertEqual(gallery.thumb_url, "https://s.exhentai.org/w/02/428/84948-cover.webp")

    def test_parse_gallery_detail_uses_only_gd1_for_cover(self):
        html = """
        <html>
          <img src="https://s.exhentai.org/w/01/543/32150-unrelated.webp">
          <div id="gd1"><div style="background:transparent url(https://s.exhentai.org/w/02/428/84948-real-cover.webp) 0 0 no-repeat"></div></div>
          <h1 id="gn">Correct Detail Cover</h1>
        </html>
        """

        gallery = parse_gallery_detail(html, "https://exhentai.org/g/9/abc/")

        self.assertEqual(gallery.thumb_url, "https://s.exhentai.org/w/02/428/84948-real-cover.webp")

    def test_parse_gallery_detail_populates_page_count_and_samples(self):
        html = """
        <html>
          <h1 id="gn">Sampled Gallery</h1>
          <table><tr><td>Length:</td><td>42 pages</td></tr></table>
          <div id="gdt">
            <div class="gdtl"><a href="https://exhentai.org/s/aa/9-1"><img src="https://s.exhentai.org/t/aa/9-1.jpg"></a></div>
          </div>
          <div id="gdb">nav</div>
        </html>
        """

        gallery = parse_gallery_detail(html, "https://exhentai.org/g/9/abc/")

        self.assertEqual(gallery.page_count, 42)
        self.assertEqual(gallery.sample_thumbs, ["https://s.exhentai.org/t/aa/9-1.jpg"])

    def test_parse_gallery_detail_falls_back_to_first_page_image_when_cover_missing(self):
        html = """
        <html>
          <h1 id="gn">No Cover Gallery</h1>
          <div id="gd1"><img src="https://exhentai.org/img/blank.gif"></div>
          <div id="gdt">
            <div class="gdtl"><a href="https://exhentai.org/s/aa/9-1"><img src="https://s.exhentai.org/t/aa/first.jpg"></a></div>
            <div class="gdtl"><a href="https://exhentai.org/s/bb/9-2"><img src="https://s.exhentai.org/t/bb/second.jpg"></a></div>
          </div>
          <div id="gdb">nav</div>
        </html>
        """

        gallery = parse_gallery_detail(html, "https://exhentai.org/g/9/abc/")

        self.assertEqual(gallery.thumb_url, "https://s.exhentai.org/t/aa/first.jpg")

    def test_sample_page_url_appends_page_param(self):
        self.assertEqual(
            sample_page_url("https://exhentai.org/g/1/abc/", 3),
            "https://exhentai.org/g/1/abc/?p=3",
        )
        self.assertEqual(sample_page_url("https://exhentai.org/g/1/abc/", 0), "https://exhentai.org/g/1/abc/")

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

    def test_normalize_cookie_header_accepts_multiline_cookie_header_fragments(self):
        raw = "Cookie: ipb_member_id=123\nipb_pass_hash=abc\nigneous=secret"

        self.assertEqual(
            normalize_cookie_header(raw),
            "ipb_member_id=123; ipb_pass_hash=abc; igneous=secret",
        )

    def test_normalize_cookie_header_preserves_equals_inside_values(self):
        raw = "ipb_member_id=123\nsk=abc=="

        self.assertEqual(normalize_cookie_header(raw), "ipb_member_id=123; sk=abc==")

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


class GdataTest(unittest.TestCase):
    def _payload(self, *gids):
        return {
            "gmetadata": [
                {
                    "gid": gid,
                    "token": token,
                    "title": title,
                    "category": "Manga",
                    "thumb": f"https://ehgt.org/aa/bb/{gid}.jpg",
                    "uploader": "someone",
                    "posted": "1700000000",
                    "filecount": "42",
                    "rating": "4.35",
                    "tags": ["artist:foo_bar", "language:english"],
                }
                for gid, token, title in gids
            ]
        }

    def test_fetch_gallery_metadata_parses_entries(self):
        with patch.object(exhentai, "post_api_json", return_value=self._payload((123, "abc", "Title"))):
            metadata = fetch_gallery_metadata("cookie", [("123", "abc")], sleep=lambda _: None)

        url = "https://exhentai.org/g/123/abc/"
        self.assertIn(url, metadata)
        meta = metadata[url]
        self.assertEqual(meta["thumb"], "https://ehgt.org/aa/bb/123.jpg")
        self.assertEqual(meta["category"], "Manga")
        self.assertEqual(meta["page_count"], 42)
        self.assertAlmostEqual(meta["rating"], 4.35)
        self.assertEqual(meta["tags"], ["artist:foo bar", "language:english"])

    def test_fetch_gallery_metadata_batches_by_25_with_one_pause(self):
        pairs = [(gid, "tok") for gid in range(30)]
        batch_sizes: list[int] = []
        sleeps: list[float] = []

        def fake_post(cookie, payload, proxy_url=""):
            batch_sizes.append(len(payload["gidlist"]))
            return {"gmetadata": []}

        with patch.object(exhentai, "post_api_json", side_effect=fake_post):
            fetch_gallery_metadata("cookie", pairs, sleep=sleeps.append)

        self.assertEqual(batch_sizes, [25, 5])
        self.assertEqual(len(sleeps), 1)

    def test_fetch_gallery_metadata_skips_error_entries_and_failed_batches(self):
        responses = [
            RuntimeError("boom"),
            {"gmetadata": [{"gid": 9, "token": "z", "error": "expunged"}, {"gid": 8, "token": "y", "thumb": "https://ehgt.org/x/8.jpg", "title": "Eight"}]},
        ]

        def fake_post(cookie, payload, proxy_url=""):
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        pairs = [(gid, "tok") for gid in range(30)]
        with patch.object(exhentai, "post_api_json", side_effect=fake_post):
            metadata = fetch_gallery_metadata("cookie", pairs, sleep=lambda _: None)

        # First batch raised; only the second batch's non-error entry survives.
        self.assertEqual(list(metadata), ["https://exhentai.org/g/8/y/"])

    def test_apply_gallery_metadata_prefers_ehgt_and_backfills(self):
        gallery = Gallery(
            url="https://exhentai.org/g/123/abc/",
            gid="123",
            token="abc",
            title="Gallery 123",
            thumb_url="https://s.exhentai.org/w/old.webp",
        )
        metadata = {
            "https://exhentai.org/g/123/abc/": {
                "thumb": "https://ehgt.org/aa/bb/123.jpg",
                "title": "Real Title",
                "category": "Doujinshi",
                "uploader": "person",
                "rating": 4.0,
                "page_count": 12,
                "posted": "1700000000",
                "tags": ["parody:x"],
            }
        }

        changed = apply_gallery_metadata([gallery], metadata)

        self.assertEqual(changed, 1)
        self.assertEqual(gallery.thumb_url, "https://ehgt.org/aa/bb/123.jpg")
        self.assertEqual(gallery.title, "Real Title")
        self.assertEqual(gallery.category, "Doujinshi")
        self.assertEqual(gallery.page_count, 12)
        self.assertIn("parody:x", gallery.tags)


class SpritePreviewTest(unittest.TestCase):
    NORMAL_HTML = """
    <div id="gdt">
      <div class="gdtm" style="height:170px"><div style="margin:1px auto 0;width:100px;height:142px;background:transparent url(https://s.exhentai.org/m/001/sheet.jpg) 0 0 no-repeat"><a href="https://exhentai.org/s/aa/1-1"><img src="https://s.exhentai.org/img/blank.gif"></a></div></div>
      <div class="gdtm" style="height:170px"><div style="margin:1px auto 0;width:100px;height:142px;background:transparent url(https://s.exhentai.org/m/001/sheet.jpg) -100px 0 no-repeat"><a href="https://exhentai.org/s/aa/1-2"><img src="https://s.exhentai.org/img/blank.gif"></a></div></div>
    </div>
    <div id="gdb"></div>
    """

    LARGE_HTML = """
    <div id="gdt">
      <div class="gdtl" style="height:300px"><a href="https://exhentai.org/s/aa/1-1"><img src="https://s.exhentai.org/m/001/page-1.jpg"></a></div>
      <div class="gdtl" style="height:300px"><a href="https://exhentai.org/s/aa/1-2"><img src="https://s.exhentai.org/m/001/page-2.jpg"></a></div>
    </div>
    <div id="gdb"></div>
    """

    def test_parse_sprite_previews_extracts_offsets_and_size(self):
        frames = parse_sprite_previews(self.NORMAL_HTML)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0], SampleThumb(url="https://s.exhentai.org/m/001/sheet.jpg", x=0, y=0, w=100, h=142))
        self.assertEqual(frames[1], SampleThumb(url="https://s.exhentai.org/m/001/sheet.jpg", x=100, y=0, w=100, h=142))

    def test_parse_gallery_pages_rich_returns_sprites_for_normal_mode(self):
        _, entries = parse_gallery_pages_rich(self.NORMAL_HTML)
        self.assertTrue(all(isinstance(entry, SampleThumb) for entry in entries))
        self.assertEqual(len(entries), 2)

    def test_parse_gallery_pages_returns_strings_for_large_mode(self):
        _, entries = parse_gallery_pages_rich(self.LARGE_HTML)
        self.assertTrue(all(isinstance(entry, str) for entry in entries))
        # The legacy wrapper drops sprite frames but keeps individual previews.
        _, legacy = parse_gallery_pages(self.LARGE_HTML)
        self.assertEqual(entries, legacy)

    def test_parse_gallery_pages_drops_sprites_for_backward_compatibility(self):
        _, legacy = parse_gallery_pages(self.NORMAL_HTML)
        self.assertEqual(legacy, [])

    def test_sample_storage_and_entry_url_round_trip(self):
        frame = SampleThumb(url="https://ehgt.org/x/sheet.jpg", x=100, y=0, w=100, h=142)
        stored = sample_storage(frame)
        self.assertEqual(stored, {"url": "https://ehgt.org/x/sheet.jpg", "x": 100, "y": 0, "w": 100, "h": 142})
        self.assertEqual(sample_entry_url(stored), "https://ehgt.org/x/sheet.jpg")
        self.assertEqual(sample_storage("https://ehgt.org/x/page.jpg"), "https://ehgt.org/x/page.jpg")
        self.assertEqual(sample_entry_url("https://ehgt.org/x/page.jpg"), "https://ehgt.org/x/page.jpg")

    def test_sample_thumb_host_accepts_ehgt(self):
        self.assertTrue(sample_thumb_host("https://ehgt.org/aa/bb/x.jpg"))
        self.assertTrue(sample_thumb_host("https://s.exhentai.org/m/x.jpg"))
        self.assertFalse(sample_thumb_host("https://evil.test/x.jpg"))


if __name__ == "__main__":
    unittest.main()
