"""Tests for the V1.3 source layer: transports, robots.txt, rate limits."""

import time

import pytest
import requests
import requests_mock

from sources import (
    SourceSpec,
    fetch_source,
    parse_robots,
    parse_wp_rest_posts,
    parse_feed,
    registrable_domain,
    reset_network_caches,
    sanitize_url,
    specs_from_config,
    specs_from_urls,
    validate_source_url,
)

FEED_XML = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Campaign One Airdrop</title>
      <link>https://example.com/c1</link>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
      <description>An airdrop campaign for holders.</description>
    </item>
  </channel>
</rss>"""

WP_JSON = b"""[
  {"id": 7, "date_gmt": "2026-10-28T12:37:35", "link": "https://airdropalert.com/blogs/x/",
   "title": {"rendered": "Testnet airdrop <b>guide</b>"},
   "excerpt": {"rendered": "A quest and points guide."}}
]"""


@pytest.fixture(autouse=True)
def clean_caches():
    reset_network_caches()
    yield
    reset_network_caches()


def rss_spec(url="https://feeds.example.com/feed.xml", **kw):
    return SourceSpec(id=kw.pop("id", "test-rss"), url=url, **kw)


class TestUrlHandling:
    def test_strips_tracking_and_referral_params(self):
        url = "https://proof.soei.io/?ref=raxf9h7&utm_source=x&id=7"
        assert sanitize_url(url) == "https://proof.soei.io/?id=7"

    def test_strips_all_utm_params(self):
        url = "https://x.example/p?a=1&utm_medium=email&utm_campaign=drop"
        assert sanitize_url(url) == "https://x.example/p?a=1"

    def test_rejects_non_http_schemes(self):
        assert sanitize_url("javascript:alert(1)") == ""
        assert sanitize_url("data:text/html,<h1>hi</h1>") == ""
        assert sanitize_url("mailto:a@b.c") == ""

    def test_empty_and_garbage_are_empty_strings(self):
        assert sanitize_url("") == ""
        assert sanitize_url("   ") == ""

    def test_fragment_is_dropped(self):
        assert sanitize_url("https://x.example/a#section") == "https://x.example/a"

    def test_registrable_domain_collapses_subdomains(self):
        assert registrable_domain("https://proof.soei.io/claim") == "soei.io"
        assert registrable_domain("https://soei.io") == "soei.io"

    def test_registrable_domain_handles_two_label_public_suffixes(self):
        assert registrable_domain("https://blog.example.co.uk/x") == "example.co.uk"

    def test_registrable_domain_of_nothing_is_empty(self):
        assert registrable_domain("") == ""
        assert registrable_domain("https://") == ""

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://example.com/feed.xml", True),
            ("http://example.com/feed.xml", False),
            ("https://localhost/feed.xml", False),
            ("https://127.0.0.1/feed.xml", False),
            ("https://example.com/" + "x" * 3000, False),
            ("javascript:alert(1)", False),
            ("", False),
        ],
    )
    def test_validate_source_url(self, url, expected):
        assert validate_source_url(url) is expected


class TestRobots:
    def test_wildcard_disallow_blocks_matching_path(self):
        rules = parse_robots("User-agent: *\nDisallow: /wp-admin/\n", "LUMAVUN/1.3")
        assert rules.allows("/wp-admin/x") is False
        assert rules.allows("/feed/") is True

    def test_longest_matching_rule_wins(self):
        text = (
            "User-agent: *\n"
            "Disallow: /wp-admin/\n"
            "Allow: /wp-admin/admin-ajax.php\n"
        )
        rules = parse_robots(text, "LUMAVUN/1.3")
        assert rules.allows("/wp-admin/admin-ajax.php") is True
        assert rules.allows("/wp-admin/edit.php") is False

    def test_specific_user_agent_group_is_preferred(self):
        text = (
            "User-agent: *\nDisallow: /\n\n"
            "User-agent: lumavun\nDisallow: /private/\n"
        )
        rules = parse_robots(text, "LUMAVUN/1.3")
        assert rules.allows("/feed/") is True
        assert rules.allows("/private/") is False

    def test_empty_disallow_means_allow_everything(self):
        rules = parse_robots("User-agent: *\nDisallow:\n", "LUMAVUN/1.3")
        assert rules.allows("/anything") is True

    def test_comments_and_sitemaps_are_ignored(self):
        text = "# comment\nUser-agent: *\nDisallow: /search/  # inline\nSitemap: https://x/s.xml\n"
        rules = parse_robots(text, "LUMAVUN/1.3")
        assert rules.disallow == ["/search/"]

    def test_robots_allows_when_robots_is_unreachable(self):
        with requests_mock.Mocker() as m:
            m.get(
                "https://x.example/robots.txt",
                exc=requests.exceptions.ConnectionError,
            )
            from sources import robots_allows

            assert robots_allows("https://x.example/feed.xml", "LUMAVUN/1.3") is True

    def test_fetch_source_refuses_a_disallowed_path(self):
        with requests_mock.Mocker() as m:
            m.get(
                "https://blocked.example/robots.txt",
                text="User-agent: *\nDisallow: /feed.xml\n",
                status_code=200,
            )
            result = fetch_source(rss_spec("https://blocked.example/feed.xml"))
            paths = [str(call.url) for call in m.request_history]
        assert result.robots_blocked is True
        assert result.error == "robots.txt disallows this path"
        # Only robots.txt may have been requested; the blocked path is untouched.
        assert paths == ["https://blocked.example/robots.txt"]

    def test_fetch_source_reads_a_path_robots_allows(self):
        with requests_mock.Mocker() as m:
            m.get(
                "https://open.example/robots.txt",
                text="User-agent: *\nDisallow: /private/\n",
                status_code=200,
            )
            m.get("https://open.example/feed.xml", text=FEED_XML, status_code=200)
            result = fetch_source(
                rss_spec("https://open.example/feed.xml"), min_interval=0
            )
        assert result.error is None
        assert result.entries_seen == 1

    def test_fetch_source_sends_identifying_user_agent(self):
        with requests_mock.Mocker() as m:
            m.get("https://ua.example/robots.txt", text="User-agent: *\n", status_code=200)
            m.get("https://ua.example/feed.xml", text=FEED_XML, status_code=200)
            fetch_source(rss_spec("https://ua.example/feed.xml"), min_interval=0)
        feed_call = m.request_history[-1]
        assert "LUMAVUN" in feed_call.headers.get("User-Agent", "")


class TestRateLimit:
    def test_requests_to_same_host_are_spaced(self):
        url = "https://slow.example/feed.xml"
        with requests_mock.Mocker() as m:
            m.get("https://slow.example/robots.txt", text="User-agent: *\n", status_code=200)
            m.get(url, text=FEED_XML, status_code=200)
            started = time.monotonic()
            fetch_source(rss_spec(url), min_interval=0.3)
            fetch_source(rss_spec(url), min_interval=0.3)
            elapsed = time.monotonic() - started
        assert elapsed >= 0.25, "second request to the same host must be delayed"

    def test_zero_interval_disables_delay(self):
        with requests_mock.Mocker() as m:
            m.get("https://fast.example/robots.txt", text="User-agent: *\n", status_code=200)
            m.get("https://fast.example/feed.xml", text=FEED_XML, status_code=200)
            started = time.monotonic()
            fetch_source(rss_spec("https://fast.example/feed.xml"), min_interval=0)
            fetch_source(rss_spec("https://fast.example/feed.xml"), min_interval=0)
            elapsed = time.monotonic() - started
        assert elapsed < 0.2


class TestParsers:
    def test_parse_feed_extracts_entry(self):
        spec = rss_spec()
        items = parse_feed(FEED_XML.encode("utf-8"), spec)
        assert len(items) == 1
        item = items[0]
        assert item.title == "Campaign One Airdrop"
        assert item.url == "https://example.com/c1"
        assert item.listing_url == "https://example.com/c1"
        assert item.source_id == "test-rss"
        assert item.published is not None

    def test_parse_feed_reads_publisher_specific_fields(self):
        xml = (
            '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
            "<item><title>SOEI Airdrop</title>"
            "<link>https://airdropalert.com/airdrops/soei/</link>"
            "<airdrop_link>https://proof.soei.io/?ref=raxf9h7</airdrop_link>"
            "<website_link>https://soei.io/</website_link>"
            "<description>Testnet quest with points.</description>"
            "</item></channel></rss>"
        )
        items = parse_feed(xml.encode("utf-8"), rss_spec())
        assert items[0].url == "https://proof.soei.io/"
        assert items[0].official_url == "https://soei.io/"
        assert items[0].listing_url == "https://airdropalert.com/airdrops/soei/"

    def test_parse_wp_rest_posts_maps_documented_fields(self):
        spec = rss_spec(id="wp", parser="wp_rest_posts", kind="json")
        items = parse_wp_rest_posts(WP_JSON, spec)
        assert len(items) == 1
        assert items[0].title == "Testnet airdrop guide"
        assert items[0].summary == "A quest and points guide."
        assert items[0].url == "https://airdropalert.com/blogs/x/"
        assert items[0].entry_id == "wp-7"
        assert items[0].published == "2026-10-28T12:37:35"

    def test_parse_wp_rest_posts_rejects_non_json(self):
        spec = rss_spec(id="wp", parser="wp_rest_posts")
        with pytest.raises(Exception) as exc:
            parse_wp_rest_posts(b"not json at all", spec)
        assert "not valid JSON" in str(exc.value)

    def test_parse_wp_rest_posts_rejects_non_list(self):
        spec = rss_spec(id="wp", parser="wp_rest_posts")
        with pytest.raises(Exception) as exc:
            parse_wp_rest_posts(b'{"error": "nope"}', spec)
        assert "not a list" in str(exc.value)


class TestFetchSource:
    def test_successful_feed_returns_items(self):
        with requests_mock.Mocker() as m:
            m.get("https://ok.example/robots.txt", text="User-agent: *\n", status_code=200)
            m.get("https://ok.example/feed.xml", text=FEED_XML, status_code=200)
            result = fetch_source(rss_spec("https://ok.example/feed.xml"), min_interval=0)
        assert result.error is None
        assert result.http_status == 200
        assert result.entries_seen == 1
        assert result.reachable is True

    def test_http_error_becomes_a_result_error_not_an_exception(self):
        with requests_mock.Mocker() as m:
            m.get("https://gone.example/robots.txt", text="User-agent: *\n", status_code=200)
            m.get("https://gone.example/feed.xml", status_code=404)
            result = fetch_source(rss_spec("https://gone.example/feed.xml"), min_interval=0)
        assert result.error == "HTTP 404"
        assert result.reachable is False

    def test_rate_limit_response_is_reported(self):
        with requests_mock.Mocker() as m:
            m.get("https://busy.example/robots.txt", text="User-agent: *\n", status_code=200)
            m.get(
                "https://busy.example/feed.xml",
                status_code=429,
                headers={"Retry-After": "30"},
            )
            result = fetch_source(rss_spec("https://busy.example/feed.xml"), min_interval=0)
        assert "429" in result.error
        assert "30" in result.error

    def test_connection_error_is_reported(self):
        with requests_mock.Mocker() as m:
            m.get("https://down.example/robots.txt", text="User-agent: *\n", status_code=200)
            m.get("https://down.example/feed.xml", exc=requests.exceptions.ConnectionError)
            result = fetch_source(rss_spec("https://down.example/feed.xml"), min_interval=0)
        assert "Network error" in result.error
        assert "ConnectionError" in result.error

    def test_invalid_json_api_is_reported(self):
        spec = rss_spec(url="https://api.example/posts", parser="wp_rest_posts", kind="json")
        with requests_mock.Mocker() as m:
            m.get("https://api.example/robots.txt", text="User-agent: *\n", status_code=200)
            m.get("https://api.example/posts", text="<html>nope</html>", status_code=200)
            result = fetch_source(spec, min_interval=0)
        assert result.error == "JSON response was not valid JSON"

    def test_empty_feed_is_reachable_with_zero_entries(self):
        empty = '<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
        with requests_mock.Mocker() as m:
            m.get("https://quiet.example/robots.txt", text="User-agent: *\n", status_code=200)
            m.get("https://quiet.example/feed.xml", text=empty, status_code=200)
            result = fetch_source(
                rss_spec("https://quiet.example/feed.xml"), min_interval=0
            )
        assert result.error is None
        assert result.entries_seen == 0

    def test_non_https_source_is_rejected_without_any_request(self):
        result = fetch_source(rss_spec("http://insecure.example/feed.xml"))
        assert result.error == "Invalid or non-HTTPS source URL"
        assert result.http_status is None


class TestRegistry:
    def test_specs_from_config_enables_only_enabled_sources(self):
        config = {
            "sources": [
                {"id": "a", "url": "https://a.example/feed", "kind": "rss", "enabled": True},
                {"id": "b", "url": "https://b.example/api", "kind": "json", "parser": "wp_rest_posts", "enabled": False},
                {"id": "c", "url": "https://c.example/feed", "kind": "rss", "enabled": True},
            ]
        }
        specs = specs_from_config(config)
        assert [s.id for s in specs] == ["a", "c"]
        assert specs[1].category == "airdrop_listing"

    def test_specs_from_urls_marks_sources_as_custom(self):
        specs = specs_from_urls(["https://a.example/rss", "", "  "])
        assert len(specs) == 1
        assert specs[0].kind == "rss"
        assert specs[0].category == "custom"

    def test_shipped_registry_has_both_transports(self):
        from config_loader import load_config

        specs = specs_from_config(load_config())
        assert len(specs) >= 4
        assert {"rss", "json"} == {s.kind for s in specs}
        assert any(s.official for s in specs)
