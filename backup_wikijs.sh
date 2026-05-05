#!/usr/bin/env bash
set -euo pipefail

COMPOSE_DIR="."
BACKUP_DIR=""
ARCHIVE_NAME="wiki-js-backup-monthly.tar.gz"
HOSTNAME_LABEL="$(hostname -s 2>/dev/null || echo wikijs)"

usage() {
  cat <<'EOF'
Usage:
  backup_wikijs.sh --compose-dir /opt/prod/wiki-js [--backup-dir /opt/prod/wiki-js/backups] [--archive-name wiki-js-backup-monthly.tar.gz]

Creates a restorable Wiki.js backup archive containing:
  - PostgreSQL pg_dump SQL
  - docker-compose.yml
  - wiki-data/ if present

It intentionally does not archive live db-data/.
By default the archive name is stable, so the next run replaces the previous
monthly backup.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-dir)
      COMPOSE_DIR="$2"
      shift 2
      ;;
    --backup-dir)
      BACKUP_DIR="$2"
      shift 2
      ;;
    --archive-name)
      ARCHIVE_NAME="$2"
      shift 2
      ;;
    --retention-days)
      echo "Note: --retention-days is ignored; this script replaces the stable monthly archive." >&2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$COMPOSE_DIR/docker-compose.yml" && ! -f "$COMPOSE_DIR/compose.yml" ]]; then
  echo "Compose file not found in: $COMPOSE_DIR" >&2
  exit 1
fi

if [[ -z "$BACKUP_DIR" ]]; then
  BACKUP_DIR="$COMPOSE_DIR/backups"
fi

mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
STAGING_DIR="$(mktemp -d)"
ARCHIVE_PATH="$BACKUP_DIR/$ARCHIVE_NAME"
TEMP_ARCHIVE_PATH="$BACKUP_DIR/.${ARCHIVE_NAME}.${TIMESTAMP}.tmp"

cleanup() {
  rm -rf "$STAGING_DIR"
}
trap cleanup EXIT

echo "Creating Wiki.js backup: $ARCHIVE_PATH"

mkdir -p "$STAGING_DIR/wiki-js"

echo "Dumping PostgreSQL database..."
SQL_DUMP="$STAGING_DIR/wiki-js/wiki-full-${TIMESTAMP}.sql"
ASSETDATA_MODE="included"
if ! docker compose --project-directory "$COMPOSE_DIR" exec -T db \
  pg_dump -U wikijs -d wiki --no-owner --no-privileges \
  > "$SQL_DUMP"; then
  echo "Full pg_dump failed. Retrying without public.assetData table data..." >&2
  rm -f "$SQL_DUMP"
  docker compose --project-directory "$COMPOSE_DIR" exec -T db \
    pg_dump -U wikijs -d wiki --no-owner --no-privileges \
    --exclude-table-data='public."assetData"' \
    > "$SQL_DUMP"
  ASSETDATA_MODE="excluded-table-data"
fi

echo "Copying compose file..."
if [[ -f "$COMPOSE_DIR/docker-compose.yml" ]]; then
  cp "$COMPOSE_DIR/docker-compose.yml" "$STAGING_DIR/wiki-js/docker-compose.yml"
else
  cp "$COMPOSE_DIR/compose.yml" "$STAGING_DIR/wiki-js/compose.yml"
fi

if [[ -d "$COMPOSE_DIR/wiki-data" ]]; then
  echo "Copying wiki-data..."
  cp -a "$COMPOSE_DIR/wiki-data" "$STAGING_DIR/wiki-js/wiki-data"
else
  echo "wiki-data directory not found; continuing with SQL + compose only."
fi

cat > "$STAGING_DIR/wiki-js/RESTORE.md" <<EOF
# Restore Notes

Backup created: ${TIMESTAMP}
Source host: ${HOSTNAME_LABEL}
assetData table data: ${ASSETDATA_MODE}

This archive intentionally excludes live db-data/.
If assetData table data is marked as excluded, the SQL dump does not include
binary blobs from Wiki.js assetData. The archive still includes wiki-data/.

Basic restore flow:

\`\`\`bash
docker compose up -d db
docker compose exec -T db psql -U wikijs -d postgres -c "DROP DATABASE IF EXISTS wiki;"
docker compose exec -T db psql -U wikijs -d postgres -c "CREATE DATABASE wiki OWNER wikijs;"
cat wiki-full-${TIMESTAMP}.sql | docker compose exec -T db psql -U wikijs -d wiki
docker compose up -d
\`\`\`
EOF

echo "Creating tar.gz archive..."
tar -C "$STAGING_DIR" -czf "$TEMP_ARCHIVE_PATH" wiki-js
mv -f "$TEMP_ARCHIVE_PATH" "$ARCHIVE_PATH"

echo "Archive created:"
ls -lh "$ARCHIVE_PATH"

echo "Backup complete."
