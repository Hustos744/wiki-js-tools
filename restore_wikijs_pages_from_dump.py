#!/usr/bin/env python3
import argparse
import subprocess
import sys


COLUMNS = [
    "id",
    "path",
    "hash",
    "title",
    "description",
    "isPrivate",
    "isPublished",
    "privateNS",
    "publishStartDate",
    "publishEndDate",
    "content",
    "render",
    "toc",
    "contentType",
    "createdAt",
    "updatedAt",
    "editorKey",
    "localeCode",
    "authorId",
    "creatorId",
    "extra",
]
COMPOSE_DIR = None


def copy_unescape(value):
    if value == r"\N":
        return None

    result = []
    i = 0
    while i < len(value):
        char = value[i]
        if char != "\\" or i + 1 >= len(value):
            result.append(char)
            i += 1
            continue

        i += 1
        esc = value[i]
        result.append(
            {
                "b": "\b",
                "f": "\f",
                "n": "\n",
                "r": "\r",
                "t": "\t",
                "v": "\v",
                "\\": "\\",
            }.get(esc, esc)
        )
        i += 1

    return "".join(result)


def sql_literal(value):
    if value is None:
        return "null"
    return "'" + value.replace("'", "''") + "'"


def iter_pages_dump(path):
    in_copy = False
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not in_copy:
                if line.startswith("COPY public.pages "):
                    in_copy = True
                continue

            if line == r"\.":
                return

            parts = line.split("\t")
            if len(parts) != len(COLUMNS):
                raise ValueError(f"Unexpected dump row with {len(parts)} columns")
            row = dict(zip(COLUMNS, (copy_unescape(part) for part in parts)))
            yield row


def run_psql(sql):
    proc = subprocess.run(
        [
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
            "-q",
        ],
        input=sql,
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


def main():
    parser = argparse.ArgumentParser(description="Restore Wiki.js pages.content/render from a pages-only pg_dump.")
    parser.add_argument("dump", help="path to pages-before-link-fix-*.sql")
    parser.add_argument(
        "--compose-dir",
        default=".",
        help="directory containing docker-compose.yml (default: current directory)",
    )
    args = parser.parse_args()

    global COMPOSE_DIR
    COMPOSE_DIR = args.compose_dir

    statements = ["begin;"]
    count = 0
    for row in iter_pages_dump(args.dump):
        statements.append(
            "update pages set "
            f"content = {sql_literal(row['content'])}, "
            f"render = {sql_literal(row['render'])} "
            f"where id = {int(row['id'])};"
        )
        count += 1
    statements.append("commit;")
    run_psql("\n".join(statements))
    print(f"Restored content/render for {count} pages.")


if __name__ == "__main__":
    main()
