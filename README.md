# Wiki.js maintenance tools

Small maintenance scripts for a Docker Compose based Wiki.js installation.

The repository intentionally contains only scripts and documentation. Do not add
Wiki.js data directories, PostgreSQL data directories, SQL dumps, uploaded files,
or environment files with secrets.

## Scripts

- `fix_wikijs_links.py` fixes imported internal links that still point to old
  file-style paths such as `Playbook%20name.md`. It updates both `pages.content`
  and the cached HTML in `pages.render`.
- `restore_wikijs_pages_from_dump.py` restores `pages.content` and
  `pages.render` from a pages-only `pg_dump`.
- `audit_wikijs_links.py` scans internal page links and writes problematic
  links to a TSV report. It does not perform HTTP requests and does not
  download linked files.

## Requirements

- Python 3.10 or newer
- Docker Compose
- Run commands from a machine that can execute `docker compose exec db ...`
  against the Wiki.js PostgreSQL container.

The scripts expect the compose services to be named:

- `db`
- `wiki`

The database/user defaults used by the scripts are:

- database: `wiki`
- user: `wikijs`

## Dry run

From the directory that contains `docker-compose.yml`:

```bash
python3 /opt/wiki-js-tools/fix_wikijs_links.py --host wikijs.csirt.local:330 --limit-report 80
```

Or run from any directory and point to the Wiki.js compose directory:

```bash
python3 /opt/wiki-js-tools/fix_wikijs_links.py --compose-dir /opt/wiki-js --host wikijs.csirt.local:330 --limit-report 80
```

Dry run does not modify the database. It prints how many pages and links would
be updated, plus sample replacements.

## Backup before apply

Create a pages-only backup before applying changes:

```bash
cd /opt/wiki-js
mkdir -p backups
docker compose exec -T db pg_dump -U wikijs -d wiki --no-owner --no-privileges --table=public.pages > backups/pages-before-link-fix-$(date +%Y%m%d-%H%M%S).sql
```

This fix only modifies the `pages` table, so a pages-only backup is enough for
rollback of this operation.

## Apply

```bash
python3 /opt/wiki-js-tools/fix_wikijs_links.py --compose-dir /opt/wiki-js --host wikijs.csirt.local:330 --apply --limit-report 20
docker compose --project-directory /opt/wiki-js restart wiki
```

Then run dry run again. A successful repeat check should show:

```text
Pages to update: 0
Links to fix: 0
```

## Restore

If you need to roll back the page content/render fields:

```bash
python3 /opt/wiki-js-tools/restore_wikijs_pages_from_dump.py /opt/wiki-js/backups/pages-before-link-fix-YYYYMMDD-HHMMSS.sql --compose-dir /opt/wiki-js
docker compose --project-directory /opt/wiki-js restart wiki
```

## Audit Internal Links

Create a report of problematic internal page links:

```bash
python3 /opt/wiki-js-tools/audit_wikijs_links.py --compose-dir /opt/wiki-js --host wikijs.csirt.local:330 --output /opt/wiki-js/backups/link-audit-$(date +%Y%m%d-%H%M%S).tsv
```

This script only reads the Wiki.js database. It does not open links over HTTP
and does not download files.

To also list links that are resolvable but non-canonical and could be fixed by
`fix_wikijs_links.py`, add:

```bash
--include-fixable
```

## Windows local example

From `D:\Projects\codex_dir`:

```powershell
python .\wiki-js-tools\fix_wikijs_links.py --compose-dir D:\Projects\codex_dir --host wikijs.csirt.local:330 --limit-report 40
```

Apply locally:

```powershell
python .\wiki-js-tools\fix_wikijs_links.py --compose-dir D:\Projects\codex_dir --host wikijs.csirt.local:330 --apply --limit-report 20
docker compose restart wiki
```
