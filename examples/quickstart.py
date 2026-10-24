"""Runs offline: extract a saved HTML page, no network or browser needed.

    python examples/quickstart.py
"""

from pathlib import Path

from buitre.eval.runner import CORPUS
from buitre.pipeline import process_html

page = Path(CORPUS) / "wordpress_blog.html"

result = process_html(
    page.read_text(encoding="utf-8"),
    "https://example.com/wordpress_blog",
    content_limit=5,
)

print("Title      :", result.page.title)
print("Type       :", result.page.page_type)
print("Published  :", result.page.published_at)
print("Author     :", result.page.author)
print("Quality    :", result.statistics.extraction_score)
print("Emails     :", result.extracted.emails)
print("\nImportant content (reading order):")

for block in result.extracted.important_content:
    print(" -", block)

print("\nWith a network connection, the same thing for a live page:")
print("    from buitre.pipeline import run_pipeline")
print('    run_pipeline("https://example.com")')
