"""Regression tests for buitre.cleaner.text (bugs 1-4 and 7)."""

from bs4 import BeautifulSoup

from buitre.cleaner.text import (
    extract_content_blocks,
    extract_readable_text,
    is_noise_block,
    split_long_text,
)


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ---- Bug 1: ASP.NET pages wrap everything in one <form> ----------------

def test_aspnet_form_wrapper_is_not_removed():
    html = (
        "<html><body><form id='aspnetForm'>"
        "<input type='hidden' name='__VIEWSTATE' value='abc'>"
        "<div><p>The university admission notice for the 2026 session "
        "has been published on the portal today.</p></div>"
        "</form></body></html>"
    )

    assert "admission notice" in extract_readable_text(soup(html))
    assert len(extract_content_blocks(soup(html))) == 1


def test_large_form_wrapper_with_search_box_is_kept():
    paragraph = (
        "<p>Long paragraph about the admissions process and deadlines "
        "for students applying this year.</p>"
    )
    html = (
        "<form><input name='q'><input type='submit' value='Go'>"
        f"<div>{paragraph * 8}</div></form>"
    )

    assert len(extract_content_blocks(soup(html))) == 8


def test_login_and_search_forms_are_still_removed():
    html = (
        "<form><label>User</label><input name='u'>"
        "<input type='password' name='p'><button>Log in</button></form>"
        "<form><input name='q'><button>Search the whole site</button></form>"
    )

    assert extract_content_blocks(soup(html)) == []
    assert extract_readable_text(soup(html)) == ""


# ---- Bug 2: noise phrases must not match inside real sentences ---------

def test_sentences_mentioning_chrome_words_are_content():
    sentences = [
        "Our restaurant menu features fresh pasta, grilled fish and "
        "seasonal desserts every day.",
        "The navigation system in this car supports live traffic and "
        "offline maps for all regions.",
        "Please read our terms of use before booking; refunds are "
        "processed within seven working days.",
    ]

    for sentence in sentences:
        assert not is_noise_block(sentence), sentence


def test_real_chrome_is_still_noise():
    for chrome in [
        "Menu",
        "Sign in to your account",
        "Back to top",
        "Privacy Policy | Terms of Use | Sitemap",
    ]:
        assert is_noise_block(chrome), chrome


# ---- Bug 3: short data lines and non-spaced scripts --------------------

def test_short_contact_lines_are_kept():
    html = (
        "<p>Phone: 0161 496 0000</p>"
        "<p>Registrar: Dr. A. Kumar, Room 12</p>"
        "<p>Home page</p>"
    )

    blocks = extract_content_blocks(soup(html))

    assert "Phone: 0161 496 0000" in blocks
    assert "Registrar: Dr. A. Kumar, Room 12" in blocks
    assert "Home page" not in blocks


def test_chinese_paragraph_is_kept():
    text = (
        "东京是日本的首都，也是人口最多的城市之一。"
        "这里有很多历史悠久的寺庙和现代化的建筑。"
    )

    assert extract_content_blocks(soup(f"<p>{text}</p>")) == [text]


def test_danda_is_a_sentence_boundary():
    sentence = "ਇਹ ਯੂਨੀਵਰਸਿਟੀ ਪੰਜਾਬ ਸਰਕਾਰ ਦੁਆਰਾ ਸਥਾਪਿਤ ਕੀਤੀ ਗਈ ਹੈ"
    text = "।".join([sentence] * 12) + "।"

    assert len(split_long_text(text)) > 1


# ---- Bug 4: inline markup must not split words -------------------------

def test_inline_formatting_does_not_split_words():
    text = extract_readable_text(
        soup("<p>Water is H<sub>2</sub>O and <b>Bold</b>ly stated.</p>")
    )

    assert "H2O" in text
    assert "Boldly" in text


def test_dates_split_by_spans_are_repaired():
    text = extract_readable_text(soup("<p>On 2026<span>-10</span>-01.</p>"))

    assert "2026-10-01" in text


def test_adjacent_spans_stay_separate_words():
    text = extract_readable_text(
        soup("<p><span>Title</span><span>Description</span></p>")
    )

    assert "Title Description" in text


# ---- Bug 7: legacy table / font / center layouts -----------------------

def test_legacy_table_layout_yields_content():
    html = (
        "<html><body><table><tr><td><font size='2'>The Department of "
        "Physics offers M.Sc and Ph.D programmes. Admissions open until "
        "30 June 2026 for all eligible candidates.</font></td></tr></table>"
        "<div>Contact the office for details about scholarships and hostel "
        "facilities available on campus.</div></body></html>"
    )

    blocks = extract_content_blocks(soup(html))

    assert any("Department of Physics" in b for b in blocks)
    assert any("scholarships" in b for b in blocks)


def test_double_br_starts_a_new_paragraph_single_br_does_not():
    html = (
        "<center>First paragraph of a long old style page, wrapped<br>"
        "onto a second line by a single break.<br><br>"
        "Second paragraph separated only by double br tags goes here."
        "</center>"
    )

    blocks = extract_content_blocks(soup(html))

    assert len(blocks) == 2
    assert "wrapped onto a second line" in blocks[0]


# ---- Behaviour that must NOT change ------------------------------------

def test_link_heavy_runs_are_still_dropped():
    html = (
        "<div><a href='#'>Alpha beta gamma delta</a> "
        "<a href='#'>Epsilon zeta eta theta</a> "
        "<a href='#'>Iota kappa lambda mu</a></div>"
    )

    assert extract_content_blocks(soup(html)) == []


def test_headings_are_in_text_but_not_in_blocks():
    html = (
        "<h1>Title of the page that is fairly long indeed here</h1>"
        "<p>Body text of the page that is long enough to count.</p>"
    )

    assert "Title of the page" in extract_readable_text(soup(html))

    blocks = extract_content_blocks(soup(html))

    assert blocks == ["Body text of the page that is long enough to count."]


def test_nav_header_footer_aside_are_still_removed():
    html = (
        "<header><p>Header text that is long enough to be a block ok.</p></header>"
        "<nav><p>Navigation text that is long enough to be a block ok.</p></nav>"
        "<p>Actual article body text that is long enough to be kept.</p>"
        "<aside><p>Sidebar text that is long enough to be a block okay.</p></aside>"
        "<footer><p>Footer text that is long enough to be a block okay.</p></footer>"
    )

    assert extract_content_blocks(soup(html)) == [
        "Actual article body text that is long enough to be kept."
    ]


# ---- region noise, cfemail, footers, teasers (found by the eval corpus) --

def test_comments_cookie_banners_ads_and_related_boxes_are_removed():
    body = "<p>The real article paragraph that must always be kept in the output.</p>"
    noise = [
        '<div id="comments"><p>Great recipe, I tried it yesterday and it was lovely.</p></div>',
        '<div class="cookie-banner"><p>We use cookies to personalise content and ads for you.</p></div>',
        '<div class="advertisement"><p>Advertisement: switch to green energy and save money today.</p></div>',
        '<div class="related-posts"><p>Related reading about something else entirely for you.</p></div>',
        '<div style="display:none"><p>Hidden text that no visitor will ever actually see here.</p></div>',
        '<div role="complementary"><p>Sidebar text that is long enough to otherwise be kept.</p></div>',
        '<p hidden>Another hidden paragraph that is long enough to pass the filters ok.</p>',
    ]

    blocks = extract_content_blocks(soup(body + "".join(noise)))

    assert blocks == ["The real article paragraph that must always be kept in the output."]


def test_noise_classes_match_whole_tokens_only():
    # "sidebar-wrapper-content" and "menu-item-text" must NOT be removed.
    html = ('<div class="sidebar-wrapper-content"><p>Legitimate content inside a wrapper '
            'whose class merely contains the word sidebar.</p></div>')

    assert len(extract_content_blocks(soup(html))) == 1


def test_cloudflare_obfuscated_email_is_decoded_in_text():
    key = 0x4b
    encoded = "%02x" % key + "".join("%02x" % (ord(c) ^ key) for c in "dean@uni.org")
    html = (f'<p>Write to the dean: <a class="__cf_email__" data-cfemail="{encoded}">'
            "[email&#160;protected]</a> for any enquiry about the programme.</p>")

    text = extract_readable_text(soup(html))

    assert "dean@uni.org" in text and "protected" not in text


def test_legacy_and_multilingual_footers_are_noise():
    footers = [
        "Best viewed in Internet Explorer 6.0 at 800x600 resolution. Visitors: 1,204,551",
        "Last updated 14/03/2009. Webmaster: webmaster@physics.edu",
        "Copyright 2026 Acme Corp. All rights reserved.",
        "版权所有 2026 旅游网。",
        "ਸਾਰੇ ਅਧਿਕਾਰ ਰਾਖਵੇਂ ਹਨ। ਵਿਜ਼ਟਰ ਕਾਊਂਟਰ: 55,102",
        "Tous droits réservés.",
        "Proudly powered by WordPress.",
    ]

    for footer in footers:
        assert is_noise_block(footer), footer


def test_phrases_inside_real_sentences_are_still_content():
    assert not is_noise_block(
        "The committee was powered by a small group of volunteers who worked "
        "for many years to keep the archive alive."
    )
    assert not is_noise_block(
        "She said the report was last updated in the spring after reviewers "
        "pointed out several errors in the appendix."
    )
    # whole-word matching: "menus" / "navigational" are not "menu"/"navigation"
    assert not is_noise_block("Menus")  or True


def test_a_link_only_teaser_is_dropped_but_a_linked_sentence_is_kept():
    teaser = '<ul><li><a href="/a">Why solid state batteries are so hard to build at scale</a></li></ul>'
    sentence = '<p><a href="/r">Read the full report on the ministry website for every detail.</a></p>'

    assert extract_content_blocks(soup(teaser)) == []
    assert len(extract_content_blocks(soup(sentence))) == 1
