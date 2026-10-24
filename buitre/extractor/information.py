from urllib.parse import urlparse

from buitre.extractor.contacts import (
    _normalize_domain,
    collect_emails,
    extract_domains,
    extract_phone_numbers,
    infer_region,
    outbound_domains,
)
from buitre.extractor.dates import extract_dates
from buitre.extractor.entities import extract_entities
from buitre.models import ExtractedData, LinkInfo
from buitre.utils.soup import make_soup


def _page_text(html: str) -> str:
    """Visible text of the WHOLE document, footer and sidebars included.

    Contact details usually live in the footer, which the content
    extractor (rightly) removes, so contacts are searched here too.
    """

    if not html:
        return ""

    soup = make_soup(html)

    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    return soup.get_text("\n")


def extract_information(
    text: str,
    html: str = "",
    important_content: list[str] | None = None,
    source_url: str = "",
    language: str | None = None,
    link_details: list[LinkInfo] | None = None,
) -> ExtractedData:

    entities = extract_entities(html)

    region = infer_region(
        source_url=source_url,
        language=language,
    )

    page_text = _page_text(html)

    # -------------------------------------------------- e-mail
    email_records = collect_emails(text, html, page_text)
    emails = [record["value"] for record in email_records]

    # -------------------------------------------------- phones
    from_content = set(extract_phone_numbers(text, "", region))
    from_links = set(extract_phone_numbers("", html, region))
    from_page = set(extract_phone_numbers(page_text, "", region))

    phones = sorted(from_content | from_links | from_page)

    phone_records = []

    for number in phones:
        if number in from_links:
            source = "tel"
        elif number in from_content:
            source = "content"
        else:
            source = "page"

        phone_records.append(
            {"type": "phone", "value": number, "source": source}
        )

    # -------------------------------------------------- domains
    # "domains": mentioned in the content, plus links inside the main
    # content. "outbound_domains": every external link on the page.
    details = link_details or []
    hrefs = [link.url for link in details]

    content_hrefs = [
        link.url for link in details if link.region in ("main", "body")
    ]

    domains = set(
        extract_domains(
            text,
            anchors=content_hrefs if details else [],
        )
    )

    for email in emails:
        domain = _normalize_domain(
            email.rsplit("@", 1)[-1],
            allow_single_character_domain=True,
        )

        if domain:
            domains.add(domain)

    return ExtractedData(
        emails=emails,

        phone_numbers=phones,

        domains=sorted(domains),

        outbound_domains=(
            outbound_domains(hrefs, source_url)
            if hrefs and urlparse(source_url).hostname
            else []
        ),

        contacts=email_records + phone_records,

        dates=extract_dates(text, region=region),

        people=entities["people"],

        organizations=entities["organizations"],

        locations=entities["locations"],

        important_content=important_content or [],
    )
