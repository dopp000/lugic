#!/bin/sh
# Saves a dated copy of the bot's data folder and keeps the newest 14.
# Usage: deploy/backup.sh [bot folder] [backup folder]
BOT_DIR="${1:-/opt/lugic}"
BACKUP_DIR="${2:-/opt/lugic-backups}"

mkdir -p "$BACKUP_DIR" || exit 1
[ -d "$BOT_DIR/data" ] || { echo "No data folder in $BOT_DIR" >&2; exit 1; }

STAMP=$(date +%Y-%m-%d_%H%M)
tar -czf "$BACKUP_DIR/data_$STAMP.tar.gz" -C "$BOT_DIR" data || exit 1

# Delete everything except the 14 newest backups.
ls -1t "$BACKUP_DIR"/data_*.tar.gz | tail -n +15 | while read -r old; do rm -f "$old"; done
echo "Saved $BACKUP_DIR/data_$STAMP.tar.gz"
