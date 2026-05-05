#!/usr/bin/env python3
import argparse
import csv
import sys
from pathlib import Path
from urllib.parse import urlparse

import fix_wikijs_links as linkfix


def is_skipped_href(href):
    href = (href or "").strip()
    if not href:
        return "empty"
    if href.startswith("#"):
        return "anchor"
    lowered = href.lower()
    if lowered.startswith(("mailto:", "tel:", "javascript:", "data:")):
        return "non-page-scheme"
    if lowered.startswith(("/_assets/", "/assets/", "/uploads/", "/attachments")):
        return "asset"
    if lowered.startswith(("/wiki/download/attachments/", "wiki/download/attachments/")):
        return "asset"
    if "/wiki/download/attachments/" in lowered:
        return "asset"
    if lowered.endswith((
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
        ".mp4", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".wav",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".zip", ".7z", ".rar", ".tar", ".gz",
    )):
        return "asset"
    if is_code_like_href(href):
        return "code-like"
    return ""


def is_code_like_href(href):
    href = (href or "").strip()
    if not href:
        return False
    if len(href) <= 2 and href.isdigit():
        return True
    if any(token in href for token in ("SELECT%20", "WHERE%20", "HKLM", "Win32", ".NETFramework", "//")):
        return True
    if href.startswith(("'", '"')) or href.endswith(("'", '"')):
        return True
    if "," in href and "/" not in href:
        return True
    if "%5B" in href or "%5D" in href:
        return True
    if "/" not in href and "." not in href and "%" not in href:
        return True
    return False


def possible_matches(candidate, indexes):
    by_path, _, by_title = indexes
    matches = []

    if "/" in candidate:
        parent, leaf = candidate.rsplit("/", 1)
        parent_key = linkfix.comparable_slug(parent)
        leaf_key = linkfix.comparable_slug(leaf)
        for page in by_path.values():
            page_parent = linkfix.comparable_slug(linkfix.dirname(page["path"]))
            page_leaf = linkfix.comparable_slug(page["path"].rsplit("/", 1)[-1])
            if page_parent != parent_key:
                continue
            if page_leaf.startswith(leaf_key) or leaf_key.startswith(page_leaf):
                matches.append(page)
                continue
            prefix_len = linkfix.common_prefix_len(leaf_key, page_leaf)
            if prefix_len >= 35 and prefix_len >= min(len(leaf_key), len(page_leaf)) * 0.60:
                matches.append(page)

    label_key = linkfix.normalize_text(candidate.rsplit("/", 1)[-1])
    for page in by_title.get(label_key, []):
        if page not in matches:
            matches.append(page)

    return matches[:10]


def iter_links(page):
    content = page.get("content") or ""
    render = page.get("render") or ""

    for match in linkfix.LINK_RE.finditer(content):
        yield {
            "field": "content",
            "label": match.group(1),
            "href": match.group(2).strip(),
        }

    for match in linkfix.HTML_HREF_RE.finditer(render):
        yield {
            "field": "render",
            "label": "",
            "href": match.group(2).strip(),
        }


def audit_link(page, link, indexes, allowed_hosts, include_fixable):
    href = link["href"]
    skip_reason = is_skipped_href(href)
    if skip_reason:
        return None, skip_reason

    parsed = urlparse(href)
    if parsed.scheme in ("http", "https") and parsed.netloc.casefold() not in allowed_hosts:
        return None, "external"
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return None, "non-page-scheme"

    candidate, anchor = linkfix.href_to_candidate(page["path"], href, allowed_hosts)
    if not candidate:
        return None, "skipped"

    target = linkfix.resolve_link(page, link["label"], href, indexes, allowed_hosts)
    if target:
        if include_fixable and target != href:
            return {
                "issue": "fixable-noncanonical",
                "candidate": candidate,
                "suggested_path": target,
                "suggested_count": 1,
            }, ""
        return None, "ok"

    matches = possible_matches(candidate, indexes)
    if len(matches) == 1:
        issue = "probably-fixable"
    elif len(matches) > 1:
        issue = "ambiguous"
    elif href.lower().split("#", 1)[0].split("?", 1)[0].endswith(".md"):
        issue = "missing-old-md-link"
    else:
        issue = "missing-page"

    return {
        "issue": issue,
        "candidate": candidate,
        "suggested_path": "; ".join(page["path"] for page in matches),
        "suggested_count": len(matches),
    }, ""


def write_report(path, rows):
    fieldnames = [
        "issue",
        "source_page_id",
        "source_path",
        "source_title",
        "field",
        "label",
        "href",
        "normalized_candidate",
        "suggested_count",
        "suggested_path",
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Audit Wiki.js internal page links without downloading any linked files."
    )
    parser.add_argument(
        "--output",
        required=True,
        help="TSV report path for problematic links",
    )
    parser.add_argument(
        "--host",
        action="append",
        default=[],
        help="internal Wiki.js host to treat as local, e.g. wikijs.csirt.local:330",
    )
    parser.add_argument(
        "--compose-dir",
        default=".",
        help="directory containing docker-compose.yml (default: current directory)",
    )
    parser.add_argument(
        "--include-fixable",
        action="store_true",
        help="also report internal links that can be resolved but are non-canonical",
    )
    parser.add_argument("--limit-report", type=int, default=30)
    args = parser.parse_args()

    linkfix.COMPOSE_DIR = args.compose_dir
    allowed_hosts = {h.casefold() for h in args.host}
    allowed_hosts.update({"localhost:8080", "127.0.0.1:8080", "wikijs.csirt.local:330"})

    pages = linkfix.load_pages()
    link_pages = linkfix.load_pages_with_links()
    indexes = linkfix.build_indexes(pages)

    rows = []
    stats = {
        "checked": 0,
        "ok": 0,
        "skipped": 0,
        "problem": 0,
    }
    skip_counts = {}

    for page in link_pages:
        for link in iter_links(page):
            stats["checked"] += 1
            problem, status = audit_link(page, link, indexes, allowed_hosts, args.include_fixable)
            if problem is None:
                if status == "ok":
                    stats["ok"] += 1
                else:
                    stats["skipped"] += 1
                    skip_counts[status] = skip_counts.get(status, 0) + 1
                continue

            stats["problem"] += 1
            rows.append(
                {
                    "issue": problem["issue"],
                    "source_page_id": page["id"],
                    "source_path": page["path"],
                    "source_title": page["title"],
                    "field": link["field"],
                    "label": link["label"],
                    "href": link["href"],
                    "normalized_candidate": problem["candidate"],
                    "suggested_count": problem["suggested_count"],
                    "suggested_path": problem["suggested_path"],
                }
            )

    write_report(args.output, rows)

    print(f"Pages indexed: {len(pages)}")
    print(f"Pages scanned for links: {len(link_pages)}")
    print(f"Links checked: {stats['checked']}")
    print(f"OK internal links: {stats['ok']}")
    print(f"Skipped links: {stats['skipped']}")
    if skip_counts:
        print("Skipped by reason: " + ", ".join(f"{key}={value}" for key, value in sorted(skip_counts.items())))
    print(f"Problem links written: {stats['problem']}")
    print(f"Report: {args.output}")

    if rows and args.limit_report > 0:
        print("\nSample problems:")
        for row in rows[: args.limit_report]:
            print(
                f"- {row['issue']} page {row['source_page_id']} {row['source_path']}\n"
                f"  {row['field']}: {row['href']}\n"
                f"  candidate: {row['normalized_candidate']}\n"
                f"  suggestion: {row['suggested_path'] or '-'}"
            )


if __name__ == "__main__":
    main()
