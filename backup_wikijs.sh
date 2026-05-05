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

Creates a full cold Wiki.js backup archive containing:
  - db-data/
  - wiki-data/ if present
  - docker-compose.yml or compose.yml
  - RESTORE.md

The script stops the Docker Compose stack before archiving and starts it again
after the archive is created. This avoids pg_dump failures on huge assetData
bytea values and keeps PostgreSQL data files consistent.

By default the archive name is stable, so the next monthly run replaces the
previous monthly backup.
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

if [[ ! -d "$COMPOSE_DIR" ]]; then
  echo "Compose directory not found: $COMPOSE_DIR" >&2
  exit 1
fi

if [[ ! -f "$COMPOSE_DIR/docker-compose.yml" && ! -f "$COMPOSE_DIR/compose.yml" ]]; then
  echo "Compose file not found in: $COMPOSE_DIR" >&2
  exit 1
fi

if [[ ! -d "$COMPOSE_DIR/db-data" ]]; then
  echo "db-data directory not found in: $COMPOSE_DIR" >&2
  exit 1
fi

if [[ -z "$BACKUP_DIR" ]]; then
  BACKUP_DIR="$COMPOSE_DIR/backups"
fi

mkdir -p "$BACKUP_DIR"

if [[ -f "$COMPOSE_DIR/docker-compose.yml" ]]; then
  COMPOSE_FILE_NAME="docker-compose.yml"
else
  COMPOSE_FILE_NAME="compose.yml"
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
STAGING_DIR="$(mktemp -d)"
ARCHIVE_PATH="$BACKUP_DIR/$ARCHIVE_NAME"
TEMP_TAR_PATH="$BACKUP_DIR/.${ARCHIVE_NAME}.${TIMESTAMP}.tar"
TEMP_ARCHIVE_PATH="$BACKUP_DIR/.${ARCHIVE_NAME}.${TIMESTAMP}.tmp.gz"
STACK_STOPPED=0

cleanup() {
  local exit_code=$?

  rm -rf "$STAGING_DIR"
  rm -f "$TEMP_TAR_PATH"
  rm -f "$TEMP_ARCHIVE_PATH"

  if [[ "$STACK_STOPPED" -eq 1 ]]; then
    echo "Starting Wiki.js stack..."
    docker compose --project-directory "$COMPOSE_DIR" up -d
  fi

  exit "$exit_code"
}
trap cleanup EXIT

echo "Creating full cold Wiki.js backup: $ARCHIVE_PATH"
echo "Stopping Wiki.js stack for a consistent filesystem backup..."
docker compose --project-directory "$COMPOSE_DIR" down
STACK_STOPPED=1

mkdir -p "$STAGING_DIR/wiki-js"

cat > "$STAGING_DIR/wiki-js/RESTORE.md" <<EOF
# Restore Notes

Backup created: ${TIMESTAMP}
Source host: ${HOSTNAME_LABEL}
Backup type: full cold filesystem backup

This archive includes:

- db-data/
- wiki-data/ if present
- compose file

It was created while the Docker Compose stack was stopped, so PostgreSQL data
files are backed up consistently. It includes Wiki.js assetData because the
entire database data directory is included.

Basic restore flow:

\`\`\`bash
docker compose down
mv db-data db-data.before-restore-\$(date +%Y%m%d-%H%M%S) 2>/dev/null || true
mv wiki-data wiki-data.before-restore-\$(date +%Y%m%d-%H%M%S) 2>/dev/null || true
tar -xzf wiki-js-backup-monthly.tar.gz
cp -a wiki-js/db-data ./db-data
cp -a wiki-js/wiki-data ./wiki-data
cp wiki-js/${COMPOSE_FILE_NAME} ./${COMPOSE_FILE_NAME}
docker compose up -d
\`\`\`
EOF

TAR_ITEMS=()
TAR_ITEMS+=("$COMPOSE_FILE_NAME")
TAR_ITEMS+=("db-data")
if [[ -d "$COMPOSE_DIR/wiki-data" ]]; then
  TAR_ITEMS+=("wiki-data")
fi

echo "Creating tar.gz archive..."
tar -C "$STAGING_DIR" -cf "$TEMP_TAR_PATH" wiki-js/RESTORE.md
tar -C "$COMPOSE_DIR" --transform 's#^#wiki-js/#' -rf "$TEMP_TAR_PATH" "${TAR_ITEMS[@]}"
gzip -c "$TEMP_TAR_PATH" > "$TEMP_ARCHIVE_PATH"
mv -f "$TEMP_ARCHIVE_PATH" "$ARCHIVE_PATH"

echo "Archive created:"
ls -lh "$ARCHIVE_PATH"

echo "Backup complete."
