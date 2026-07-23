#!/bin/sh
set -eu

: "${RESTORE_DATABASE_URL:?请提供专用 RESTORE_DATABASE_URL}"
backup_file="${1:?请提供备份文件}"

case "$RESTORE_DATABASE_URL" in
  *_restore_test*) ;;
  *)
    echo "拒绝恢复：目标数据库名称必须包含 _restore_test" >&2
    exit 2
    ;;
esac

pg_restore --clean --if-exists --no-owner --no-acl \
  --dbname "$RESTORE_DATABASE_URL" "$backup_file"
