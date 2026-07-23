#!/bin/sh
set -eu

: "${DATABASE_URL:?请通过环境变量提供 DATABASE_URL}"
backup_directory="${1:-./backups}"
mkdir -p "$backup_directory"
backup_file="$backup_directory/job-search-agent-$(date +%Y%m%d-%H%M%S).dump"
pg_dump --format=custom --no-owner --no-acl --file "$backup_file" "$DATABASE_URL"
chmod 600 "$backup_file"
echo "$backup_file"
