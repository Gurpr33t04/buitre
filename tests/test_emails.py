"""Regression tests for e-mail extraction (bug 6)."""

from buitre.extractor.contacts import extract_domains, extract_emails


def test_image_filenames_are_not_emails():
    text = "logo@2x.png banner@3x.jpg sprite@2x.webp icon@2x.svg"

    assert extract_emails(text) == []


def test_real_emails_survive_next_to_asset_names():
    text = "logo@2x.png write to info@example.com or admin@uni.ac.in"

    assert extract_emails(text) == ["admin@uni.ac.in", "info@example.com"]


def test_asset_names_do_not_become_domains():
    domains = extract_domains("logo@2x.png banner@3x.jpg")

    assert "2x.png" not in domains
    assert "3x.jpg" not in domains


def test_unknown_tld_is_rejected():
    assert extract_emails("someone@host.notarealtld") == []
