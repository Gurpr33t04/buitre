import argparse
import os
import sys

from buitre.config import (
    TOR_CONTROL_HOST,
    TOR_CONTROL_PORT,
    VERSION,
)
from buitre.context import PipelineContext
from buitre.crawler.crawler import crawl
from buitre.output.json import (
    crawl_to_json,
    result_to_json,
    save_output,
)
from buitre.pipeline import run_pipeline


def print_single_result(result) -> None:
    print()
    print("╔════════════════════════════════════════════════════╗")
    print("║                      BUITRE                        ║")
    print("║             Web Information Extractor              ║")
    print("╚════════════════════════════════════════════════════╝")
    print()

    print("TARGET")
    print("─" * 52)
    print(f"URL          : {result.target.url}")
    print(f"Host         : {result.target.host}")
    print(f"Target type  : {result.target.target_type}")
    print(f"Scheme       : {result.target.scheme}")
    print(f"Onion        : {result.target.is_onion}")
    print()

    print("ACQUISITION")
    print("─" * 52)
    print(f"Method       : {result.fetch.fetch_method}")
    print(f"Status       : {result.fetch.status_code}")
    print(f"Final URL    : {result.fetch.final_url}")
    print(f"Content-Type : {result.fetch.content_type}")
    print(
        f"Time         : "
        f"{result.fetch.elapsed_time:.2f}s"
    )
    print()

    print("PAGE")
    print("─" * 52)
    print(f"Title        : {result.page.title}")
    print(f"Description  : {result.page.description}")
    print(f"Language     : {result.page.language}")
    print(f"Page type    : {result.page.page_type}")
    print(f"Published    : {result.page.published_at}")
    print(f"Author       : {result.page.author}")
    print(f"Canonical    : {result.page.canonical_url}")
    print(f"Headings     : {len(result.page.headings)}")
    print(f"Links        : {len(result.page.links)}")
    print(f"Images       : {len(result.page.images)}")
    print()

    print("IMPORTANT CONTENT")
    print("─" * 52)

    if result.extracted.important_content:
        for item in result.extracted.important_content:
            print(f"• {item}")
    else:
        print("• None")

    print()

    print("EXTRACTED INFORMATION")
    print("─" * 52)
    print(f"Emails       : {result.extracted.emails}")
    print(f"Phone        : {result.extracted.phone_numbers}")
    print(f"Domains      : {result.extracted.domains}")
    print(f"Outbound     : {result.extracted.outbound_domains}")
    print(f"Dates        : {result.extracted.dates}")
    print(f"People       : {result.extracted.people}")
    print(f"Organizations: {result.extracted.organizations}")
    print(f"Locations    : {result.extracted.locations}")

    if result.extracted.contacts:
        print()
        print("Contacts (with source):")

        for contact in result.extracted.contacts:
            print(
                f"  {contact['type']:<6} {contact['value']}"
                f"  [{contact['source']}]"
            )

    print()

    print("STATISTICS")
    print("─" * 52)
    print(
        f"Text length  : "
        f"{result.statistics.text_length}"
    )
    print(
        f"Links        : "
        f"{result.statistics.link_count}"
    )
    print(
        f"Images       : "
        f"{result.statistics.image_count}"
    )
    print(
        f"Quality      : "
        f"{result.statistics.extraction_score}"
    )

    if result.statistics.quality_flags:
        print(
            f"Flags        : "
            f"{', '.join(result.statistics.quality_flags)}"
        )

    if result.warnings:
        print()
        print("WARNINGS")
        print("─" * 52)

        for warning in result.warnings:
            print(f"• {warning}")

    print()

def print_crawl_result(result) -> None:
    print()
    print("╔════════════════════════════════════════════════════╗")
    print("║                   BUITRE CRAWLER                   ║")
    print("║               Controlled Web Crawler               ║")
    print("╚════════════════════════════════════════════════════╝")
    print()

    print("TARGET")
    print("─" * 52)
    print(f"URL          : {result.target.url}")
    print(f"Host         : {result.target.host}")
    print(f"Target type  : {result.target.target_type}")
    print(f"Onion        : {result.target.is_onion}")
    print()

    successful_pages = sum(
        1
        for page in result.page_metadata
        if page.success
    )

    failed_pages = sum(
        1
        for page in result.page_metadata
        if not page.success
    )

    print("CRAWL SUMMARY")
    print("─" * 52)
    print(f"URLs attempted : {result.urls_attempted}")
    print(f"Pages scanned  : {result.pages_scanned}")
    print(f"Maximum depth  : {result.max_depth}")
    print(f"Depth reached  : {result.max_depth_reached}")
    print(f"Maximum pages  : {result.max_pages}")
    print(f"Successful     : {successful_pages}")
    print(f"Failed         : {failed_pages}")
    print(f"Near-duplicates: {result.duplicates}")
    print(f"Retries        : {result.retries}")

    if result.resumed_pages:
        print(f"Resumed pages  : {result.resumed_pages}")

    if result.skipped:
        print(f"Skipped        : {result.skipped}")

    print()

    print("CRAWLED PAGES")
    print("─" * 52)

    for page in result.page_metadata:
        print()
        print(f"URL       : {page.url}")
        print(f"Parent    : {page.parent_url}")
        print(f"Depth     : {page.depth}")
        print(f"Status    : {page.status_code}")
        print(f"Method    : {page.fetch_method}")
        print(f"Success   : {page.success}")

        if page.page_type:
            print(f"Type      : {page.page_type}")

        if page.quality is not None:
            print(f"Quality   : {page.quality}")

        if page.duplicate_of:
            print(f"Duplicate : of {page.duplicate_of}")

        if page.error:
            print(f"Error     : {page.error}")
            continue

        if (
            page.result_index is None
            or page.result_index >= len(result.pages)
        ):
            continue

        scraped_page = result.pages[page.result_index]

        print(f"Title     : {scraped_page.page.title}")
        print(
            f"Description: "
            f"{scraped_page.page.description}"
        )

        print()
        print("Important content:")

        important_content = (
            scraped_page.extracted.important_content
        )

        if important_content:
            for item in important_content:
                print(f"  • {item}")
        else:
            print("  • None")

        print()
        print("Extracted:")

        extracted = scraped_page.extracted

        if extracted.emails:
            print(f"  Emails        : {extracted.emails}")

        if extracted.phone_numbers:
            print(
                f"  Phone numbers : "
                f"{extracted.phone_numbers}"
            )

        if extracted.domains:
            print(f"  Domains       : {extracted.domains}")

        if extracted.dates:
            print(f"  Dates         : {extracted.dates}")

        if extracted.people:
            print(f"  People        : {extracted.people}")

        if extracted.organizations:
            print(
                f"  Organizations : "
                f"{extracted.organizations}"
            )

        if extracted.locations:
            print(
                f"  Locations     : "
                f"{extracted.locations}"
            )

        print()
        print(
            f"Statistics: "
            f"text={scraped_page.statistics.text_length}, "
            f"links={scraped_page.statistics.link_count}, "
            f"images={scraped_page.statistics.image_count}, "
            f"score={scraped_page.statistics.extraction_score}"
        )

    if result.warnings:
        print()
        print("WARNINGS")
        print("─" * 52)

        for warning in result.warnings:
            print(f"• {warning}")

    print()

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Buitre - Adaptive Web "
            "Information Extractor"
        )
    )

    parser.add_argument(
        "url",
        help="Target HTTP or HTTPS URL",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON output",
    )

    parser.add_argument(
        "--output",
        help=(
            "Save structured JSON output "
            "to a file"
        ),
    )

    parser.add_argument(
        "--crawl",
        action="store_true",
        help="Enable controlled crawling",
    )

    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="Maximum crawl depth (default: 1)",
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="Maximum successful pages (default: 10)",
    )

    parser.add_argument(
        "--content-limit",
        type=int,
        default=10,
        help=(
            "Maximum number of important "
            "content blocks (default: 10)"
        ),
    )

    parser.add_argument(
        "--allow-subdomains",
        action="store_true",
        help="Allow crawling subdomains of the starting host",
    )

    parser.add_argument(
        "--include",
        dest="include_paths",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "Include a URL path prefix. "
            "Repeat the option to specify multiple paths."
        ),
    )

    parser.add_argument(
        "--exclude",
        dest="exclude_paths",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "Exclude a URL path prefix. "
            "Repeat the option to specify multiple paths."
        ),
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"buitre {VERSION}",
    )

    parser.add_argument(
        "--text",
        action="store_true",
        help="Include the full page text and headings in JSON output",
    )

    parser.add_argument(
        "--links",
        action="store_true",
        help="Include every link (text, rel, region) in JSON output",
    )

    parser.add_argument(
        "--documents",
        action="store_true",
        help="Read PDFs too (needs the optional 'pypdf' package)",
    )

    crawl_group = parser.add_argument_group("crawler options")

    crawl_group.add_argument(
        "--strategy",
        choices=["best_first", "bfs"],
        default="best_first",
        help="Queue order: best_first scores links (default) or plain bfs",
    )

    crawl_group.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel fetches (default: 1)",
    )

    crawl_group.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds between requests to one host (default: 1.0)",
    )

    crawl_group.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Retries for timeouts, 429 and 5xx (default: 2)",
    )

    crawl_group.add_argument(
        "--no-robots",
        action="store_true",
        help="Do not obey robots.txt",
    )

    crawl_group.add_argument(
        "--no-sitemap",
        action="store_true",
        help="Do not seed the crawl from sitemap.xml",
    )

    crawl_group.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Keep near-duplicate pages",
    )

    crawl_group.add_argument(
        "--keep-login",
        action="store_true",
        help="Keep login and error pages instead of skipping them",
    )

    crawl_group.add_argument(
        "--focus",
        action="append",
        default=None,
        metavar="WORD",
        help="Keyword that raises a link's priority (repeatable)",
    )

    crawl_group.add_argument(
        "--deep",
        action="store_true",
        help=(
            "Also enumerate safe GET forms (select options). Never "
            "submits POST forms or touches login forms."
        ),
    )

    crawl_group.add_argument(
        "--max-per-template",
        type=int,
        default=50,
        help="Max queued URLs of one path shape (default: 50)",
    )

    crawl_group.add_argument(
        "--max-per-query-template",
        type=int,
        default=10,
        help="Max queued URLs of one query-filter shape (default: 10)",
    )

    crawl_group.add_argument(
        "--store",
        metavar="FILE",
        help="SQLite file that stores the crawl so it can be resumed",
    )

    crawl_group.add_argument(
        "--resume",
        action="store_true",
        help="Continue the crawl saved in --store",
    )

    crawl_group.add_argument(
        "--jsonl",
        metavar="FILE",
        help="Stream one JSON record per page to FILE as pages finish",
    )

    crawl_group.add_argument(
        "--quiet",
        action="store_true",
        help="No per-page progress lines",
    )

    tor_group = parser.add_argument_group("Tor options")

    tor_group.add_argument(
        "--tor-control-port",
        type=int,
        default=TOR_CONTROL_PORT,
        help=(
            "Tor ControlPort used to rotate circuits after repeated "
            f"onion failures (default: {TOR_CONTROL_PORT})"
        ),
    )

    tor_group.add_argument(
        "--tor-control-password",
        default=os.environ.get("BUITRE_TOR_PASSWORD"),
        help="ControlPort password (or set BUITRE_TOR_PASSWORD)",
    )

    return parser


def _progress(event: dict) -> None:
    kind = event["event"]

    if kind == "page":
        print(
            f"[{event['depth']}] {event['url']}  "
            f"quality={event['quality']}  type={event['page_type']}  "
            f"queued={event['queued']}",
            file=sys.stderr,
        )
    elif kind == "retry":
        print(
            f"retry in {event['wait']}s: {event['url']}",
            file=sys.stderr,
        )
    elif kind == "failed":
        print(
            f"failed: {event['url']}  ({event['error']})",
            file=sys.stderr,
        )
    elif kind == "resumed":
        print(
            f"resuming: {event['done']} done, "
            f"{event['pending']} queued",
            file=sys.stderr,
        )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.depth < 0:
        parser.error(
            "--depth must be 0 or greater."
        )

    if args.max_pages < 1:
        parser.error(
            "--max-pages must be at least 1."
        )

    if args.content_limit < 1:
        parser.error(
            "--content-limit must be at least 1."
        )

    if args.workers < 1:
        parser.error("--workers must be at least 1.")

    if args.retries < 0 or args.delay < 0:
        parser.error("--retries and --delay must not be negative.")

    if args.resume and not args.store:
        parser.error("--resume needs --store FILE.")

    json_options = {
        "include_text": args.text,
        "include_links": args.links,
    }

    tor_control = {
        "host": TOR_CONTROL_HOST,
        "port": args.tor_control_port,
        "password": args.tor_control_password,
    }

    # -------------------------------------------------
    # CRAWLER MODE
    # -------------------------------------------------

    if args.crawl:

        try:
            with PipelineContext(
                documents=args.documents,
                tor_control=tor_control,
            ) as context:
                crawl_result = crawl(
                    args.url,
                    max_depth=args.depth,
                    max_pages=args.max_pages,
                    content_limit=args.content_limit,
                    allow_subdomains=args.allow_subdomains,
                    include_paths=args.include_paths,
                    exclude_paths=args.exclude_paths,
                    strategy=args.strategy,
                    workers=args.workers,
                    delay=args.delay,
                    max_retries=args.retries,
                    respect_robots=not args.no_robots,
                    use_sitemap=not args.no_sitemap,
                    dedupe_content=not args.no_dedupe,
                    skip_page_types=(
                        () if args.keep_login else ("login", "error")
                    ),
                    focus=args.focus,
                    deep=args.deep,
                    include_documents=args.documents,
                    max_per_template=args.max_per_template,
                    max_per_query_template=args.max_per_query_template,
                    store_path=args.store,
                    resume=args.resume,
                    jsonl_path=args.jsonl,
                    include_text=args.text,
                    context=context,
                    progress=None if args.quiet else _progress,
                )

            # --output takes priority over terminal
            # output.
            if args.output:
                output = crawl_to_json(
                    crawl_result,
                    **json_options,
                )

                save_output(
                    output,
                    args.output,
                )

                print(
                    f"Saved successfully: "
                    f"{args.output}"
                )

                return

            if args.json:
                print(
                    crawl_to_json(
                        crawl_result,
                        **json_options,
                    )
                )

                return

            print_crawl_result(
                crawl_result
            )

            return

        except Exception as exc:
            print(
                f"Runtime error: {exc}"
            )

            return

    # -------------------------------------------------
    # SINGLE PAGE MODE
    # -------------------------------------------------

    try:
        with PipelineContext(
            documents=args.documents,
            tor_control=tor_control,
        ) as context:
            result = run_pipeline(
                args.url,
                content_limit=args.content_limit,
                context=context,
            )

        # --output takes priority over terminal
        # output.
        if args.output:
            output = result_to_json(
                result,
                **json_options,
            )

            save_output(
                output,
                args.output,
            )

            print(
                f"Saved successfully: "
                f"{args.output}"
            )

            return

        if args.json:
            print(
                result_to_json(
                    result,
                    **json_options,
                )
            )

            return

        print_single_result(
            result
        )

    except Exception as exc:
        print(
            f"Runtime error: {exc}"
        )

        return
