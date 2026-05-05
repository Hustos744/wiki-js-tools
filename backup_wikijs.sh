#!/usr/bin/env bash
set -euo pipefail

COMPOSE_DIR="."
RETENTION_DAYS=92
BACKUP_DIR=""
HOSTNAME_LABEL="$(hostname -s 2>/dev/null || echo wikijs)"

usage() {
  cat <<'EOF'
Usage:
  backup_wikijs.sh --compose-dir /opt/prod/wiki-js [--backup-dir /opt/prod/wiki-js/backups] [--retention-days 92]

Creates a restorable Wiki.js backup archive containing:
  - PostgreSQL pg_dump SQL
  - docker-compose.yml
  - wiki-data/ if present

It intentionally does not archive live db-data/.
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
    --retention-days)
      RETENTION_DAYS="$2"
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
ARCHIVE_NAME="wiki-js-backup-${HOSTNAME_LABEL}-${TIMESTAMP}.tar.gz"
ARCHIVE_PATH="$BACKUP_DIR/$ARCHIVE_NAME"

cleanup() {
  rm -rf "$STAGING_DIR"
}
trap cleanup EXIT

echo "Creating Wiki.js backup: $ARCHIVE_PATH"

mkdir -p "$STAGING_DIR/wiki-js"

echo "Dumping PostgreSQL database..."
docker compose --project-directory "$COMPOSE_DIR" exec -T db \
  pg_dump -U wikijs -d wiki --no-owner --no-privileges \
  > "$STAGING_DIR/wiki-js/wiki-full-${TIMESTAMP}.sql"

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

This archive intentionally excludes live db-data/.

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
tar -C "$STAGING_DIR" -czf "$ARCHIVE_PATH" wiki-js

echo "Archive created:"
ls -lh "$ARCHIVE_PATH"

echo "Applying retention: deleting backup archives older than ${RETENTION_DAYS} days..."
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'wiki-js-backup-*.tar.gz' -mtime "+${RETENTION_DAYS}" -print -delete

echo "Backup complete."
