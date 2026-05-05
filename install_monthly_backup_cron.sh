#!/usr/bin/env bash
set -euo pipefail

COMPOSE_DIR="/opt/prod/wiki-js"
TOOLS_DIR="/opt/prod/wiki-js-tools"
ARCHIVE_NAME="wiki-js-backup-monthly.tar.gz"
CRON_MARKER="# wiki-js-tools monthly backup"

usage() {
  cat <<'EOF'
Usage:
  install_monthly_backup_cron.sh [--compose-dir /opt/prod/wiki-js] [--tools-dir /opt/prod/wiki-js-tools] [--archive-name wiki-js-backup-monthly.tar.gz]

Installs a root crontab entry that runs Wiki.js backup on the 1st day of every
month at 12:00. The backup archive uses a stable filename, so every new monthly
run replaces the previous archive.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-dir)
      COMPOSE_DIR="$2"
      shift 2
      ;;
    --tools-dir)
      TOOLS_DIR="$2"
      shift 2
      ;;
    --archive-name)
      ARCHIVE_NAME="$2"
      shift 2
      ;;
    --retention-days)
      echo "Note: --retention-days is ignored; monthly backup uses one stable archive file." >&2
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

BACKUP_SCRIPT="$TOOLS_DIR/backup_wikijs.sh"
LOG_PATH="$COMPOSE_DIR/backups/monthly-backup.log"

if [[ ! -x "$BACKUP_SCRIPT" ]]; then
  echo "Backup script is not executable: $BACKUP_SCRIPT" >&2
  echo "Run: chmod +x $BACKUP_SCRIPT" >&2
  exit 1
fi

mkdir -p "$COMPOSE_DIR/backups"

CRON_LINE="0 12 1 * * $BACKUP_SCRIPT --compose-dir $COMPOSE_DIR --archive-name $ARCHIVE_NAME >> $LOG_PATH 2>&1 $CRON_MARKER"

TMP_CRON="$(mktemp)"
trap 'rm -f "$TMP_CRON"' EXIT

crontab -l 2>/dev/null | grep -vF "$CRON_MARKER" > "$TMP_CRON" || true
echo "$CRON_LINE" >> "$TMP_CRON"
crontab "$TMP_CRON"

echo "Installed monthly Wiki.js backup cron:"
echo "$CRON_LINE"
