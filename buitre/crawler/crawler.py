import random
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import replace
from typing import Any, Callable
from urllib.parse import urlparse

from buitre.cleaner.boilerplate import BoilerplateRegistry
from buitre.config import ROBOTS_TOKEN
from buitre.context import PipelineContext
from buitre.crawler import robots as robots_module
from buitre.crawler.deep import enumerate_form_urls
from buitre.crawler.frontier import Frontier, FrontierItem, score_link
from buitre.crawler.robots import RobotsCache
from buitre.crawler.rules import (
    is_allowed_host,
    is_crawlable_url,
    is_path_allowed,
    normalize_url,
    resolve_url,
)
from buitre.crawler.simhash import SimIndex, simhash
from buitre.crawler.sitemap import discover_sitemap_urls
from buitre.crawler.store import CrawlStore
from buitre.crawler.throttle import HostThrottle
from buitre.crawler.urlpatterns import has_query, looks_like_trap, url_template
from buitre.errors import FetchStatusError
from buitre.models import (
    CrawlPage,
    CrawlResult,
    LinkInfo,
    ScrapeResult,
)
from buitre.output.json import JsonlWriter, page_record
from buitre.pipeline import run_pipeline
from buitre.prioritizer.scorer import reselect
from buitre.target.analyzer import analyze_target


DEFAULT_SKIP_TYPES = ("login", "error")

# Below this many characters a page is too short for SimHash to be a
# reliable duplicate signal.
MIN_DEDUPE_CHARS = 250

SITEMAP_SCORE = 8.0
MAX_BACKOFF = 120.0


def _retry_delay(exc: Exception, retries: int) -> float:
    """Backoff: honour Retry-After, otherwise exponential with jitter."""

    retry_after = getattr(exc, "retry_after", None)

    if retry_after:
        try:
            return min(float(retry_after), MAX_BACKOFF)
        except ValueError:
            pass

    return min(2.0 * (2 ** retries) + random.random(), MAX_BACKOFF)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, FetchStatusError):
        return exc.verdict in ("rate_limited", "server_error")

    # Plain RuntimeError is what the fetchers raise for timeouts and
    # connection failures.
    return type(exc) is RuntimeError


def crawl(
    start_url: str,
    max_depth: int = 1,
    max_pages: int = 10,
    content_limit: int = 10,
    same_host: bool = True,
    allow_subdomains: bool = False,
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    *,
    strategy: str = "best_first",
    workers: int = 1,
    delay: float = 0.0,
    respect_robots: bool = True,
    use_sitemap: bool = True,
    max_per_template: int = 50,
    max_per_query_template: int = 10,
    max_retries: int = 0,
    dedupe_content: bool = True,
    skip_page_types: tuple[str, ...] | list[str] = DEFAULT_SKIP_TYPES,
    follow_nofollow: bool = False,
    focus: list[str] | None = None,
    deep: bool = False,
    include_documents: bool = False,
    store_path: str | None = None,
    resume: bool = False,
    jsonl_path: str | None = None,
    include_text: bool = False,
    keep_bodies: bool = False,
    refine: bool = True,
    context: PipelineContext | None = None,
    robots: RobotsCache | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> CrawlResult:
    """
    Crawl a site, best pages first.

    Library defaults are conservative and backwards compatible (no
    delay, no retries); the CLI turns politeness and retries on.

    strategy        "best_first" (link-scored priority queue) or "bfs".
    workers         Parallel fetches. Browser fetches are serialized
                    through one shared browser.
    delay           Minimum seconds between requests to one host
                    (robots.txt Crawl-delay raises it further).
    respect_robots  Obey robots.txt (longest-match, wildcards).
    use_sitemap     Seed the queue from sitemap.xml.
    max_per_template / max_per_query_template
                    Cap queued URLs of one *shape* (see url_template):
                    stops calendars, facet filters, endless pagination.
    max_retries     Retries for timeouts, 429 and 5xx, with backoff.
    dedupe_content  Skip near-identical pages (SimHash).
    skip_page_types Page types not worth keeping (login walls, soft 404s).
    focus           Keywords that raise a link's priority.
    deep            Also enumerate safe GET forms (select options).
    include_documents  Follow and parse PDFs (needs pypdf).
    store_path/resume  SQLite persistence; resume a stopped crawl.
    jsonl_path      Stream one JSON record per page as it completes.
    keep_bodies     Keep raw HTML in memory (off by default).
    refine          After the crawl, drop site-template blocks that were
                    not yet recognised on early pages.
    """

    if max_depth < 0:
        raise ValueError("max_depth must be 0 or greater.")

    if max_pages < 1:
        raise ValueError("max_pages must be at least 1.")

    if workers < 1:
        raise ValueError("workers must be at least 1.")

    start_target = analyze_target(start_url)
    normalized_start_url = normalize_url(start_target.url)

    if not normalized_start_url:
        raise ValueError("Unable to normalize the starting URL.")

    skip_types = set(skip_page_types or ())

    owns_context = context is None
    ctx = context or PipelineContext(documents=include_documents)

    if include_documents:
        ctx.documents = True

    robots_cache = (
        (robots or RobotsCache(agent=ROBOTS_TOKEN)) if respect_robots else None
    )
    throttle = HostThrottle(delay, clock=clock, sleep=sleep)
    # A custom run_pipeline without a `context` parameter still works:
    # the first TypeError about it switches context-passing off.
    pass_context = [True]

    store = CrawlStore(store_path) if store_path else None
    jsonl = JsonlWriter(jsonl_path) if jsonl_path else None

    frontier = Frontier(strategy)

    seen: set[str] = set()
    template_counts: Counter[str] = Counter()
    fetched_final_urls: set[str] = set()
    sim_index = SimIndex()

    results: list[ScrapeResult] = []
    page_metadata: list[CrawlPage] = []
    warnings: list[str] = []
    skipped: Counter[str] = Counter()

    urls_attempted = 0
    duplicates = 0
    retries_total = 0
    prior_ok = 0
    consecutive_failures: Counter[str] = Counter()

    def emit(event: str, **data: Any) -> None:
        if progress:
            progress({"event": event, **data})

    # ------------------------------------------------------------------
    # Queueing
    # ------------------------------------------------------------------

    def consider(
        raw_url: str,
        base_url: str,
        *,
        depth: int,
        parent: str | None,
        text: str = "",
        region: str = "body",
        rel: list[str] | None = None,
        parent_quality: float = 0.5,
        via: str = "link",
        base_score: float | None = None,
    ) -> None:
        if depth > max_depth:
            return

        resolved = resolve_url(base_url, raw_url)

        if not resolved or resolved in seen:
            return

        if not is_crawlable_url(
            resolved,
            normalized_start_url,
            same_host=same_host,
            allow_subdomains=allow_subdomains,
            include_documents=include_documents,
        ):
            return

        if not is_path_allowed(
            resolved,
            include_paths=include_paths,
            exclude_paths=exclude_paths,
        ):
            skipped["path_rules"] += 1
            return

        if "nofollow" in (rel or []) and not follow_nofollow:
            skipped["nofollow"] += 1
            return

        trap = looks_like_trap(resolved)

        if trap:
            skipped["trap"] += 1
            return

        template = url_template(resolved)
        cap = max_per_query_template if has_query(resolved) else max_per_template

        if template_counts[template] >= cap:
            skipped["template_cap"] += 1
            return

        score = (
            base_score
            if base_score is not None
            else score_link(
                resolved,
                text=text,
                region=region,
                rel=rel,
                depth=depth,
                parent_quality=parent_quality,
                template_seen=template_counts[template],
                focus=focus,
            )
        )

        if via == "form":
            score -= 2.0

        seen.add(resolved)
        template_counts[template] += 1

        item = FrontierItem(
            url=resolved,
            depth=depth,
            parent=parent,
            score=score,
            via=via,
            template=template,
        )

        frontier.push(item)

        if store:
            store.add_pending(item)

    # ------------------------------------------------------------------
    # Seeding / resuming
    # ------------------------------------------------------------------

    if store:
        existing_done = store.done_urls()
        existing_pending = store.pending()

        if (existing_done or existing_pending) and not resume:
            raise ValueError(
                f"{store_path} already contains a crawl. "
                "Use resume=True to continue it, or choose another file."
            )

        if resume and (existing_done or existing_pending):
            seen |= existing_done
            fetched_final_urls |= store.final_urls()
            prior_ok = store.ok_count()

            for value, label in store.simhashes():
                sim_index.add(value, label)

            for pending_item in existing_pending:
                seen.add(pending_item.url)
                frontier.push(pending_item)

            for url in list(existing_done) + [i.url for i in existing_pending]:
                template_counts[url_template(url)] += 1

            emit("resumed", done=len(existing_done), pending=len(existing_pending))

    if not len(frontier) and not seen:
        seen.add(normalized_start_url)
        template_counts[url_template(normalized_start_url)] += 1

        start_item = FrontierItem(
            url=normalized_start_url,
            depth=0,
            parent=None,
            score=100.0,
            template=url_template(normalized_start_url),
        )

        frontier.push(start_item)

        if store:
            store.add_pending(start_item)

        if use_sitemap and max_depth >= 1:
            fetch_text = (
                robots_cache._fetch_text
                if robots_cache
                else (lambda u: robots_module.default_fetch_text(u))
            )

            declared = (
                robots_cache.sitemaps(normalized_start_url)
                if robots_cache
                else []
            )

            for sitemap_url in discover_sitemap_urls(
                normalized_start_url, declared, fetch_text
            ):
                consider(
                    sitemap_url,
                    normalized_start_url,
                    depth=1,
                    parent=normalized_start_url,
                    via="sitemap",
                    base_score=SITEMAP_SCORE,
                )

    # ------------------------------------------------------------------
    # Work
    # ------------------------------------------------------------------

    def work(item: FrontierItem) -> tuple[str, FrontierItem, Any]:
        host = urlparse(item.url).hostname or ""

        if robots_cache is not None and not robots_cache.can_fetch(item.url):
            return "robots", item, None

        minimum = (
            robots_cache.crawl_delay(item.url) if robots_cache else None
        )

        throttle.wait(host, minimum)

        try:
            result = None

            if pass_context[0]:
                try:
                    result = run_pipeline(
                        item.url,
                        content_limit=content_limit,
                        context=ctx,
                    )

                except TypeError as exc:
                    if "context" not in str(exc) or "unexpected keyword" not in str(exc):
                        raise

                    pass_context[0] = False

            if result is None:
                result = run_pipeline(
                    item.url,
                    content_limit=content_limit,
                )

        except Exception as exc:  # noqa: BLE001
            return "error", item, exc

        return "ok", item, result

    def record_failure(item: FrontierItem, message: str, status: int | None) -> None:
        warnings.append(f"Failed to crawl {item.url}: {message}")

        page_metadata.append(
            CrawlPage(
                url=item.url,
                parent_url=item.parent,
                depth=item.depth,
                status_code=status,
                success=False,
                error=message,
                template=item.template,
                priority=item.score,
                retries=item.retries,
            )
        )

        if store:
            store.save_page(item.url, final_url=None, status=status, ok=False, error=message)

        emit("failed", url=item.url, error=message)

    def handle_ok(item: FrontierItem, result: ScrapeResult) -> None:
        nonlocal duplicates

        final_url = normalize_url(result.fetch.final_url)

        # ---- redirect boundary ----
        if (
            final_url
            and not is_allowed_host(
                final_url,
                normalized_start_url,
                same_host=same_host,
                allow_subdomains=allow_subdomains,
            )
            or (
                final_url != item.url
                and not is_path_allowed(
                    final_url,
                    include_paths=include_paths,
                    exclude_paths=exclude_paths,
                )
            )
        ):
            warning = (
                f"Redirected outside allowed crawl boundary "
                f"or path rules: {item.url} -> {final_url}"
            )
            warnings.append(warning)
            page_metadata.append(
                CrawlPage(
                    url=item.url,
                    parent_url=item.parent,
                    depth=item.depth,
                    status_code=result.fetch.status_code,
                    fetch_method=result.fetch.fetch_method,
                    success=False,
                    error=warning,
                    template=item.template,
                    priority=item.score,
                    retries=item.retries,
                )
            )

            if store:
                store.save_page(item.url, final_url=final_url, status=result.fetch.status_code, ok=False, error=warning)

            return

        # ---- same destination reached through a different URL ----
        if final_url and final_url in fetched_final_urls:
            existing_index = next(
                (
                    index
                    for index, existing in enumerate(results)
                    if normalize_url(existing.fetch.final_url) == final_url
                ),
                None,
            )

            page_metadata.append(
                CrawlPage(
                    url=item.url,
                    parent_url=item.parent,
                    depth=item.depth,
                    status_code=result.fetch.status_code,
                    fetch_method=result.fetch.fetch_method,
                    success=True,
                    result_index=existing_index,
                    template=item.template,
                    priority=item.score,
                    retries=item.retries,
                )
            )

            if store:
                store.save_page(item.url, final_url=final_url, status=result.fetch.status_code, ok=True, duplicate_of=final_url)

            return

        # ---- pages that are not content (login wall, soft 404) ----
        if result.page.page_type in skip_types:
            message = f"Skipped {result.page.page_type} page."
            skipped[result.page.page_type] += 1

            page_metadata.append(
                CrawlPage(
                    url=item.url,
                    parent_url=item.parent,
                    depth=item.depth,
                    status_code=result.fetch.status_code,
                    fetch_method=result.fetch.fetch_method,
                    success=False,
                    error=message,
                    page_type=result.page.page_type,
                    template=item.template,
                    priority=item.score,
                    retries=item.retries,
                )
            )

            if store:
                store.save_page(item.url, final_url=final_url, status=result.fetch.status_code, ok=False, error=message, page_type=result.page.page_type)

            return

        # ---- near-duplicate content (mirrors, print views, tracking) ----
        fingerprint = None

        if dedupe_content and len(result.page.text.strip()) >= MIN_DEDUPE_CHARS:
            fingerprint = simhash(result.page.text)
            original = sim_index.find(fingerprint)

            if original is not None:
                duplicates += 1
                skipped["duplicate"] += 1

                page_metadata.append(
                    CrawlPage(
                        url=item.url,
                        parent_url=item.parent,
                        depth=item.depth,
                        status_code=result.fetch.status_code,
                        fetch_method=result.fetch.fetch_method,
                        success=True,
                        duplicate_of=original,
                        page_type=result.page.page_type,
                        template=item.template,
                        priority=item.score,
                        retries=item.retries,
                    )
                )

                if store:
                    store.save_page(item.url, final_url=final_url, status=result.fetch.status_code, ok=True, page_type=result.page.page_type, simhash=fingerprint, duplicate_of=original)

                return

            sim_index.add(fingerprint, final_url or item.url)

        # ---- accept ----
        if final_url:
            fetched_final_urls.add(final_url)

        crawl_page = CrawlPage(
            url=item.url,
            parent_url=item.parent,
            depth=item.depth,
            status_code=result.fetch.status_code,
            fetch_method=result.fetch.fetch_method,
            success=True,
            result_index=len(results),
            page_type=result.page.page_type,
            quality=result.statistics.extraction_score,
            template=item.template,
            priority=item.score,
            retries=item.retries,
        )

        page_metadata.append(crawl_page)

        if jsonl or store:
            record = page_record(
                result,
                crawl_page,
                include_text=include_text,
            )

            if jsonl:
                jsonl.write(record)

            if store:
                store.save_page(
                    item.url,
                    final_url=final_url,
                    status=result.fetch.status_code,
                    ok=True,
                    page_type=result.page.page_type,
                    simhash=fingerprint,
                    record=record,
                )

        # ---- expand links ----
        if item.depth < max_depth:
            base_url = result.fetch.final_url or item.url

            details = result.page.link_details or [
                LinkInfo(url=link) for link in result.page.links
            ]

            for link in details:
                consider(
                    link.url,
                    base_url,
                    depth=item.depth + 1,
                    parent=item.url,
                    text=link.text,
                    region=link.region,
                    rel=link.rel,
                    parent_quality=result.statistics.extraction_score,
                )

            if deep:
                for form_url in enumerate_form_urls(result.page.forms):
                    consider(
                        form_url,
                        base_url,
                        depth=item.depth + 1,
                        parent=item.url,
                        via="form",
                        parent_quality=result.statistics.extraction_score,
                    )

        # ---- memory: raw bodies are not kept unless asked ----
        if not keep_bodies:
            result.fetch.body = ""
            result.fetch.content_bytes = None
            result.fetch.api_responses = []

        results.append(result)

        emit(
            "page",
            url=item.url,
            depth=item.depth,
            quality=result.statistics.extraction_score,
            page_type=result.page.page_type,
            queued=len(frontier),
        )

    def handle(outcome: tuple[str, FrontierItem, Any]) -> None:
        nonlocal urls_attempted, retries_total

        kind, item, payload = outcome
        host = urlparse(item.url).hostname or ""

        if kind == "robots":
            skipped["robots"] += 1
            message = "Blocked by robots.txt."

            page_metadata.append(
                CrawlPage(
                    url=item.url,
                    parent_url=item.parent,
                    depth=item.depth,
                    success=False,
                    error=message,
                    template=item.template,
                    priority=item.score,
                )
            )

            if store:
                store.save_page(item.url, final_url=None, status=None, ok=False, error=message)

            return

        if kind == "error":
            exc: Exception = payload

            consecutive_failures[host] += 1

            if _is_retryable(exc) and item.retries < max_retries:
                retries_total += 1
                wait_seconds = _retry_delay(exc, item.retries)

                frontier.push(
                    replace(
                        item,
                        retries=item.retries + 1,
                        not_before=clock() + wait_seconds,
                    )
                )

                emit("retry", url=item.url, wait=round(wait_seconds, 1))

                # Onion services: new circuits often fix a bad path.
                if host.endswith(".onion") and consecutive_failures[host] >= 2:
                    ctx.rotate_tor_circuit()

                return

            record_failure(
                item,
                str(exc),
                getattr(exc, "status_code", None),
            )

            return

        consecutive_failures[host] = 0
        handle_ok(item, payload)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    executor = ThreadPoolExecutor(workers) if workers > 1 else None
    inflight: dict[Future, FrontierItem] = {}

    def submit(item: FrontierItem) -> Future:
        if executor is not None:
            return executor.submit(work, item)

        future: Future = Future()

        try:
            future.set_result(work(item))
        except BaseException as exc:  # noqa: BLE001
            future.set_exception(exc)

        return future

    try:
        while True:
            # Fill the worker slots.
            while (
                len(inflight) < workers
                and prior_ok + len(results) + len(inflight) < max_pages
            ):
                item = frontier.pop(clock())

                if item is None:
                    break

                if item.depth > max_depth:
                    continue

                if item.retries == 0:
                    urls_attempted += 1

                inflight[submit(item)] = item

            if not inflight:
                if prior_ok + len(results) >= max_pages:
                    break

                pause = frontier.seconds_until_due(clock())

                if pause is None:
                    break

                if pause > 0:
                    sleep(pause)
                    continue

                # Ready work exists but could not be started; avoid a spin.
                break

            done, _ = wait(inflight, return_when=FIRST_COMPLETED)

            for future in done:
                item = inflight.pop(future)

                try:
                    outcome = future.result()
                except BaseException as exc:  # noqa: BLE001
                    outcome = ("error", item, exc)

                handle(outcome)

    finally:
        if executor is not None:
            executor.shutdown(wait=True)

    # ------------------------------------------------------------------
    # Refinement: with the whole site seen, remove template blocks that
    # were not yet recognised on the first few pages.
    # ------------------------------------------------------------------

    registry: BoilerplateRegistry = ctx.boilerplate

    for result in results:
        if refine and ctx.learn_boilerplate and result.candidates:
            learner = registry.for_host(result.target.host)
            result.extracted.important_content = reselect(
                result.candidates, content_limit, learner
            )

        result.candidates = []

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    if jsonl:
        jsonl.close()

    if store:
        store.close()

    if owns_context:
        ctx.close()

    max_depth_reached = max(
        (metadata.depth for metadata in page_metadata),
        default=0,
    )

    return CrawlResult(
        target=start_target,
        pages=results,
        page_metadata=page_metadata,
        pages_scanned=len(results),
        urls_attempted=urls_attempted,
        max_depth=max_depth,
        max_depth_reached=max_depth_reached,
        max_pages=max_pages,
        warnings=warnings,
        duplicates=duplicates,
        retries=retries_total,
        resumed_pages=prior_ok,
        skipped=dict(skipped),
    )
