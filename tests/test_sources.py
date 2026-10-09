import pytest

from kenya_data_engine.config import SourceSpec, legacy_radar_keys, load_config, load_sources

NEW_FEEDS = {
    "the_eastafrican",
    "the_eastafrican_business",
    "kenyan_wall_street",
    "capital_fm_business",
    "kbc_business",
    "people_daily_business",
    "techcabal",
    "techpoint_africa_kenya",
    "disrupt_africa",
    "techweez",
    "africa_the_big_deal",
    "launch_base_africa",
    "reddit_kenya",
}
LISTINGS = {
    "parliament",
    "the_star_business",
    "citizen_digital_business",
    "cbk_press_releases",
    "cbk_news",
    "cbk_weekly_bulletin",
    "knbs_recent_releases",
    "epra_downloads_releases",
    "cma_press_releases",
    "nse_announcements",
    "national_treasury_releases",
    "kra_public_notices",
    "parliament_senate",
    "kenya_law_gazette",
    "trends24_kenya",
}


def test_disabled_packaged_sources_carry_an_audit_note(tmp_home):
    src = load_sources(tmp_home)
    disabled = {n: s for n, s in src.specs.items() if not s.enabled}
    assert disabled, "expected some sources disabled after the live test"
    assert all(s.notes and "needs audit" in s.notes for s in disabled.values())
    assert {"nation", "cbk_news", "parliament", "google_trends"}.isdisjoint(disabled)


def test_packaged_sources_are_all_valid(tmp_home):
    src = load_sources(tmp_home)
    assert src.invalid == {}
    rss = {n for n, s in src.specs.items() if s.type == "rss"}
    listing = {n for n, s in src.specs.items() if s.type == "listing"}
    assert (
        rss
        == {"nation", "standard", "business_daily", "kenyans", "capital_fm", "google_trends"}
        | NEW_FEEDS
    )
    assert listing == LISTINGS
    assert "the_star" not in src.specs and "cbk" not in src.specs


def test_packaged_known_values(tmp_home):
    s = load_sources(tmp_home).specs
    assert s["google_trends"].kind == "attention"
    assert s["google_trends"].url == "https://trends.google.com/trending/rss?geo=KE"
    assert s["reddit_kenya"].user_agent == "python:kenya-data-engine:v0.1 (research)"
    assert s["parliament"].item == "table tbody tr" and s["parliament"].date == "td:nth-child(2)"
    assert s["trends24_kenya"].date is None
    assert s["epra_downloads_releases"].date == "td.date, span.date"
    assert s["cbk_news"].kind == "policy" and s["cbk_weekly_bulletin"].kind == "data_release"


def test_user_file_merges_field_by_field(tmp_home):
    tmp_home.sources_path.write_text(
        "cbk_news:\n  title: h1 a\n"  # fix one selector
        "nation:\n  enabled: false\n"  # disable one
        "mine:\n  type: rss\n  url: https://x.ke/rss\n  kind: news\n"  # add one
    )
    src = load_sources(tmp_home)
    assert src.invalid == {}
    assert src.specs["cbk_news"].title == "h1 a"
    assert src.specs["cbk_news"].url == "https://www.centralbank.go.ke/news/"  # untouched
    assert src.specs["nation"].enabled is False and src.specs["nation"].url.startswith(
        "https://nation"
    )
    assert src.specs["mine"].enabled is True
    assert "standard" in src.specs


def test_user_can_null_a_field(tmp_home):
    tmp_home.sources_path.write_text("parliament:\n  date: null\n")
    assert load_sources(tmp_home).specs["parliament"].date is None


@pytest.mark.parametrize(
    "entry",
    [
        "bad:\n  type: rss\n  url: https://x.ke\n  kind: bogus\n",
        "bad:\n  type: rss\n  url: https://x.ke\n  kind: calendar\n",  # calendar is its own adapter
        "bad:\n  type: rss\n  kind: news\n",  # no url
        "bad:\n  type: listing\n  url: https://x.ke\n  kind: news\n  item: tr\n",  # no title/link
        "bad:\n  type: ftp\n  url: https://x.ke\n  kind: news\n",
        "bad:\n  type: rss\n  url: https://x.ke\n  kind: news\n  selector: a\n",  # unknown key
        "bad: just a string\n",
    ],
)
def test_invalid_entry_is_reported_and_skipped(tmp_home, entry):
    tmp_home.sources_path.write_text(
        entry + "good:\n  type: rss\n  url: https://g.ke\n  kind: news\n"
    )
    src = load_sources(tmp_home)
    assert "bad" in src.invalid and "bad" not in src.specs
    assert src.invalid["bad"]
    assert "good" in src.specs and "nation" in src.specs


def test_broken_override_of_packaged_entry_is_invalid_not_fatal(tmp_home):
    tmp_home.sources_path.write_text("cbk_news:\n  kind: bogus\n")
    src = load_sources(tmp_home)
    assert "cbk_news" in src.invalid and "cbk_news" not in src.specs
    assert "cbk_news" in src.invalid and "kind" in src.invalid["cbk_news"]


def test_unparseable_or_non_mapping_user_file_does_not_raise(tmp_home):
    tmp_home.sources_path.write_text("a: [unclosed\n")
    src = load_sources(tmp_home)
    assert "sources.yaml" in src.invalid and "nation" in src.specs
    tmp_home.sources_path.write_text("- just\n- a list\n")
    assert "sources.yaml" in load_sources(tmp_home).invalid


def test_empty_user_file_is_fine(tmp_home):
    tmp_home.sources_path.write_text("# nothing yet\n")
    assert load_sources(tmp_home).invalid == {}


def test_spec_requires_selectors_only_for_listing():
    SourceSpec(type="rss", url="https://x", kind="news")
    with pytest.raises(ValueError):
        SourceSpec(type="listing", url="https://x", kind="news", item="a", title="b")


def test_legacy_radar_keys_are_ignored_and_reported(tmp_home):
    tmp_home.config_path.write_text(
        "top_n: 7\nradar:\n  max_items: 4\n  feeds: {a: https://a}\n  enabled: [rss]\n"
        "  listings: {}\n  trends_feed: x\n  user_agents: {}\n"
    )
    cfg = load_config(tmp_home)  # must not raise
    assert cfg.top_n == 7 and cfg.radar.max_items == 4
    assert legacy_radar_keys(tmp_home) == [
        "enabled",
        "feeds",
        "listings",
        "trends_feed",
        "user_agents",
    ]


def test_no_legacy_keys_when_config_missing_or_clean(tmp_home):
    assert legacy_radar_keys(tmp_home) == []
    tmp_home.config_path.write_text("top_n: 3\n")
    assert legacy_radar_keys(tmp_home) == []


def test_source_timeout_default(tmp_home):
    assert load_config(tmp_home).radar.source_timeout_s == 20
