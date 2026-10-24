"""Regression tests for buitre.extractor.entities (bug 5)."""

from buitre.extractor.entities import extract_entities


def ld(*blocks: str) -> str:
    return "".join(
        f'<script type="application/ld+json">{b}</script>' for b in blocks
    )


def test_list_type_does_not_crash():
    # Used to raise: TypeError: unhashable type: 'list'
    result = extract_entities(
        ld('{"@type": ["Organization", "NGO"], "name": "Foo Org"}')
    )

    assert result["organizations"] == ["Foo Org"]


def test_list_type_on_publisher():
    result = extract_entities(
        ld(
            '{"@type": "Article", '
            '"publisher": {"@type": ["Organization"], "name": "Acme"}}'
        )
    )

    assert result["organizations"] == ["Acme"]


def test_author_list_of_people_and_strings():
    result = extract_entities(
        ld(
            '{"@type": "Article", "author": ['
            '{"@type": "Person", "name": "Ann"},'
            '{"@type": "Person", "name": "Bob"}, "Cara"]}'
        )
    )

    assert result["people"] == ["Ann", "Bob", "Cara"]


def test_organization_author_is_not_filed_as_a_person():
    result = extract_entities(
        ld('{"author": {"@type": "Organization", "name": "Acme"}}')
    )

    assert result["people"] == []
    assert result["organizations"] == ["Acme"]


def test_location_list():
    result = extract_entities(
        ld('{"@type": "Event", "location": [{"name": "Delhi"}, "Pune"]}')
    )

    assert result["locations"] == ["Delhi", "Pune"]


def test_bad_blocks_are_skipped_not_fatal():
    result = extract_entities(
        ld(
            "{ broken json",
            "42",
            "null",
            '{"@type": "Organization", "name": "Still Found"}',
        )
    )

    assert result["organizations"] == ["Still Found"]
