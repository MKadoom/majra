#!/bin/sh
# Nightly pg_dump with 14-day rotation. Runs inside the `backup` container.
#
# This gives you a LOCAL copy. A backup that lives on the same machine as the
# database is not a backup — copy /backups off the box (rclone, scp, S3) and
# test a restore before you need one. See docs/OPERATIONS.md.
set -e
STAMP=$(date -u +%Y-%m-%dT%H%M%SZ)
OUT="/backups/majra-${STAMP}.sql.gz"

echo "[$(date -u)] dumping to ${OUT}"
PGPASSWORD="${MAJRA_PG_PASSWORD}" pg_dump \
  -h postgres -U "${MAJRA_PG_USER}" -d "${MAJRA_PG_DB}" \
  --no-owner --clean --if-exists | gzip -9 > "${OUT}"

echo "[$(date -u)] wrote $(du -h "${OUT}" | cut -f1)"

# Rotation: keep 14 days.
find /backups -name 'majra-*.sql.gz' -mtime +14 -print -delete

echo "[$(date -u)] backups on disk: $(ls -1 /backups/majra-*.sql.gz 2>/dev/null | wc -l)"
