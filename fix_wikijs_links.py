#!/usr/bin/env python3
import argparse
import re
import subprocess
import sys
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse


LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\n]*(?:\([^)\n]*\)[^)\n]*)*)\)")
HTML_HREF_RE = re.compile(r'(<a\b[^>]*?\bhref=")([^"]+)(")', re.IGNORECASE)
COMPOSE_DIR = None
HOMOGLYPH_TABLE = str.maketrans(
    {
        "А": "A", "В": "B", "Е": "E", "І": "I", "К": "K", "М": "M",
        "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
        "У": "Y", "а": "a", "в": "b", "е": "e", "і": "i", "к": "k",
        "м": "m", "н": "h", "о": "o", "р": "p", "с": "c", "т": "t",
        "х": "x", "у": "y",
    }
)


def run_psql(sql, *, input_sql=None):
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        "wikijs",
        "-d",
        "wiki",
        "-X",
        "-qAt",
    ]
    if sql:
        cmd += ["-c", sql]

    proc = subprocess.run(
        cmd,
        input=input_sql,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        cwd=COMPOSE_DIR,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(proc.returncode)
    return proc.stdout


def load_pages():
    sql = """
copy (
  select id, path, title, '' as content, '' as render
  from pages
  order by id
) to stdout with csv delimiter E'\\t';
"""
    return parse_tsv_pages(run_psql(sql))


def load_pages_with_links():
    sql = """
copy (
  select id, path, title, content, render
  from pages
  where content like '%](%' or render like '%href=%'
  order by id
) to stdout with csv delimiter E'\\t';
"""
    return parse_tsv_pages(run_psql(sql))


def set_csv_field_limit():
    import csv

    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit = limit // 10
            if limit < 131072:
                raise


def parse_tsv_pages(raw):
    import csv
    from io import StringIO

    set_csv_field_limit()
    pages = []
    reader = csv.reader(StringIO(raw), delimiter="\t")
    for row in reader:
        if not row:
            continue
        if len(row) != 5:
            raise ValueError(f"Unexpected psql row with {len(row)} fields: {row[:3]!r}")
        pages.append(
            {
                "id": int(row[0]),
                "path": row[1],
                "title": row[2],
                "content": row[3],
                "render": row[4],
            }
        )
    return pages


def split_anchor(href):
    if "#" not in href:
        return href, ""
    base, anchor = href.split("#", 1)
    return base, "#" + anchor


def clean_segment(segment):
    segment = unquote(segment).strip()
    segment = segment.replace("\\", "/")
    segment = segment.replace("[", "").replace("]", "")
    segment = segment.replace("&amp;", "&")
    segment = re.sub(r"[()\"'`]+", "", segment)
    segment = re.sub(r"[,;:]+", "", segment)
    segment = re.sub(r"\s+", "-", segment)
    segment = re.sub(r"-+", "-", segment)
    return segment.strip("-")


def normalize_path(path):
    path = unquote(path).replace("\\", "/").strip()
    path = re.sub(r"^https?://[^/]+", "", path, flags=re.I)
    path = path.split("?", 1)[0]
    path, anchor = split_anchor(path)
    if path.lower().endswith(".md"):
        path = path[:-3]
    path = path.strip("/")
    if path.startswith("uk/"):
        path = path[3:]
    parts = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        cleaned = clean_segment(part)
        if cleaned:
            parts.append(cleaned)
    return "/".join(parts), anchor


def normalize_text(text):
    text = unquote(text)
    text = text.replace("&amp;", "&")
    text = text.replace("[", "").replace("]", "")
    text = text.replace(":", " ")
    text = re.sub(r"[()\"'`.,;_/\\|-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().casefold()


def comparable_slug(value):
    value = normalize_path(value)[0]
    value = value.translate(HOMOGLYPH_TABLE)
    value = re.sub(r"[^0-9a-zA-ZА-Яа-яЇїЄєҐґ/]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-").casefold()


def common_prefix_len(left, right):
    count = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        count += 1
    return count


def dirname(path):
    if "/" not in path:
        return ""
    return path.rsplit("/", 1)[0]


def page_url(path, anchor=""):
    return "/" + path + anchor


def is_external(href, allowed_hosts):
    parsed = urlparse(href)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return True
    if parsed.scheme not in ("http", "https"):
        return False
    return parsed.netloc.casefold() not in allowed_hosts


def href_to_candidate(current_path, href, allowed_hosts):
    href = href.strip()
    if (
        not href
        or href.startswith("#")
        or href.startswith("/_assets/")
        or href.startswith("/assets/")
        or href.startswith("/uploads/")
        or href.startswith("/attachments")
    ):
        return None, ""

    parsed = urlparse(href)

    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return None, ""

    if parsed.scheme in ("http", "https"):
        if parsed.netloc.casefold() not in allowed_hosts:
            return None, ""
        raw_path = parsed.path
        _, anchor = split_anchor(parsed.fragment)
        normalized, path_anchor = normalize_path(raw_path)
        return normalized, ("#" + parsed.fragment if parsed.fragment else path_anchor)

    raw, anchor = split_anchor(href)
    raw = raw.split("?", 1)[0]
    raw = raw.replace("\\", "/")

    if raw.startswith("/"):
        combined = raw
    else:
        combined = str(PurePosixPath(dirname(current_path)) / raw)

    normalized, path_anchor = normalize_path(combined)
    return normalized, anchor or path_anchor


def build_indexes(pages):
    by_path = {p["path"]: p for p in pages}
    by_norm_path = {normalize_path(p["path"])[0]: p for p in pages}
    by_title = {}
    for page in pages:
        by_title.setdefault(normalize_text(page["title"]), []).append(page)
    return by_path, by_norm_path, by_title


def resolve_link(current_page, label, href, indexes, allowed_hosts):
    by_path, by_norm_path, by_title = indexes
    if is_external(href, allowed_hosts):
        return None

    candidate, anchor = href_to_candidate(current_page["path"], href, allowed_hosts)
    if not candidate:
        return None

    exact = by_norm_path.get(candidate)
    if exact:
        return page_url(exact["path"], anchor)

    if "/" in candidate:
        parent, leaf = candidate.rsplit("/", 1)
        parent_key = comparable_slug(parent)
        leaf_key = comparable_slug(leaf)
        prefix_matches = [
            p for p in by_path.values()
            if comparable_slug(dirname(p["path"])) == parent_key
            and comparable_slug(p["path"].rsplit("/", 1)[-1]).startswith(leaf_key)
        ]
        if len(prefix_matches) == 1:
            return page_url(prefix_matches[0]["path"], anchor)

        fuzzy_matches = []
        for page in by_path.values():
            page_parent = comparable_slug(dirname(page["path"]))
            page_leaf = comparable_slug(page["path"].rsplit("/", 1)[-1])
            if page_parent != parent_key:
                continue
            prefix_len = common_prefix_len(leaf_key, page_leaf)
            if prefix_len >= 45 and prefix_len >= min(len(leaf_key), len(page_leaf)) * 0.72:
                fuzzy_matches.append(page)
        if len(fuzzy_matches) == 1:
            return page_url(fuzzy_matches[0]["path"], anchor)

    label_matches = by_title.get(normalize_text(label), [])
    if len(label_matches) == 1:
        return page_url(label_matches[0]["path"], anchor)

    if "/" in candidate and label_matches:
        parent = candidate.rsplit("/", 1)[0]
        nearby = [
            p for p in label_matches
            if normalize_path(dirname(p["path"]))[0] == parent
            or normalize_path(dirname(p["path"]))[0].startswith(parent + "/")
            or parent.startswith(normalize_path(dirname(p["path"]))[0] + "/")
        ]
        if len(nearby) == 1:
            return page_url(nearby[0]["path"], anchor)

    return None


def fix_content(page, indexes, allowed_hosts):
    changes = []

    def replace(match):
        label, href = match.group(1), match.group(2).strip()
        target = resolve_link(page, label, href, indexes, allowed_hosts)
        if not target or target == href:
            return match.group(0)
        changes.append((href, target, label))
        return f"[{label}]({target})"

    return LINK_RE.sub(replace, page.get("content") or ""), changes


def fix_render(page, indexes, allowed_hosts):
    changes = []

    def replace(match):
        prefix, href, suffix = match.group(1), match.group(2).strip(), match.group(3)
        target = resolve_link(page, "", href, indexes, allowed_hosts)
        if not target or target == href:
            return match.group(0)
        changes.append((href, target, "render href"))
        return f"{prefix}{target}{suffix}"

    return HTML_HREF_RE.sub(replace, page.get("render") or ""), changes


def sql_literal(value):
    if value is None:
        return "null"
    return "'" + value.replace("'", "''") + "'"


def main():
    parser = argparse.ArgumentParser(
        description="Fix imported Wiki.js markdown links that still point to .md file paths."
    )
    parser.add_argument("--apply", action="store_true", help="write fixes to the pages table")
    parser.add_argument(
        "--host",
        action="append",
        default=[],
        help="internal Wiki.js host to treat as local, e.g. wikijs.csirt.local:330",
    )
    parser.add_argument("--limit-report", type=int, default=80)
    parser.add_argument(
        "--compose-dir",
        default=".",
        help="directory containing docker-compose.yml (default: current directory)",
    )
    args = parser.parse_args()

    global COMPOSE_DIR
    COMPOSE_DIR = args.compose_dir

    allowed_hosts = {h.casefold() for h in args.host}
    allowed_hosts.update({"localhost:8080", "127.0.0.1:8080", "wikijs.csirt.local:330"})

    pages = load_pages()
    link_pages = load_pages_with_links()
    indexes = build_indexes(pages)

    updates = []
    total_changes = 0
    report_lines = []

    for page in link_pages:
        fixed_content, content_changes = fix_content(page, indexes, allowed_hosts)
        fixed_render, render_changes = fix_render(page, indexes, allowed_hosts)
        changes = content_changes + render_changes
        if not changes:
            continue
        updates.append((page["id"], fixed_content, fixed_render, changes, page["path"]))
        total_changes += len(changes)
        for old, new, label in changes:
            if len(report_lines) < args.limit_report:
                report_lines.append(f"- page {page['id']} {page['path']}\n  [{label}] {old} -> {new}")

    print(f"Pages indexed: {len(pages)}")
    print(f"Pages scanned for links: {len(link_pages)}")
    print(f"Pages to update: {len(updates)}")
    print(f"Links to fix: {total_changes}")
    if report_lines:
        print("\nSample changes:")
        print("\n".join(report_lines))

    if not args.apply:
        print("\nDry run only. Re-run with --apply to update the database.")
        return

    if not updates:
        print("Nothing to update.")
        return

    statements = ["begin;"]
    for page_id, content, render, _, _ in updates:
        statements.append(
            f"update pages set content = {sql_literal(content)}, render = {sql_literal(render)}, "
            f"\"updatedAt\" = \"updatedAt\" where id = {int(page_id)};"
        )
    statements.append("commit;")
    run_psql("", input_sql="\n".join(statements))
    print("Database updated. Restart Wiki.js or rebuild page render/cache if needed.")


if __name__ == "__main__":
    main()
