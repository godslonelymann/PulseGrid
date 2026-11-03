#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."

mode=${1:-}
shift || true
case "$mode" in
  upload) [[ $# == 0 ]] || exit 2 ;;
  rehearse) [[ $# == 2 ]] || { echo "Usage: $0 rehearse s3://bucket/key.dump.age /path/to/age-identity" >&2; exit 2; } ;;
  *) echo "Usage: $0 upload | rehearse s3://bucket/key.dump.age /path/to/age-identity" >&2; exit 2 ;;
esac

valid_s3() { [[ $1 =~ ^s3://[a-z0-9][a-z0-9.-]+/.+$ ]]; }
workspace=$(mktemp -d "${TMPDIR:-/tmp}/pulsegrid-backup.XXXXXXXX")
trap 'rm -rf -- "$workspace"' EXIT
trap 'exit 1' HUP INT TERM

if [[ $mode == upload ]]; then
  : "${BACKUP_S3_PREFIX:?Set BACKUP_S3_PREFIX to s3://bucket/prefix}"
  : "${BACKUP_AGE_RECIPIENT:?Set BACKUP_AGE_RECIPIENT to the public age recipient}"
  valid_s3 "$BACKUP_S3_PREFIX" || { echo "Invalid BACKUP_S3_PREFIX" >&2; exit 2; }
  .venv/bin/python scripts/database.py backup "$workspace"
  dumps=("$workspace"/*.dump)
  dump=${dumps[0]}
  object="${BACKUP_S3_PREFIX%/}/${dump##*/}.age"
  age --encrypt --recipient "$BACKUP_AGE_RECIPIENT" --output "$dump.age" "$dump"
  rm -- "$dump"
  aws s3 cp "$dump.age" "$object" --checksum-algorithm SHA256 --only-show-errors
  echo "Off-server backup uploaded: $object"
else
  object=$1
  identity=$2
  valid_s3 "$object" && [[ $object == *.dump.age ]] || { echo "Expected an S3 .dump.age object" >&2; exit 2; }
  [[ -r $identity ]] || { echo "Cannot read age identity file" >&2; exit 2; }
  aws s3 cp "$object" "$workspace/archive.dump.age" --checksum-mode ENABLED --only-show-errors
  age --decrypt --identity "$identity" --output "$workspace/archive.dump" "$workspace/archive.dump.age"
  .venv/bin/python scripts/database.py rehearse "$workspace/archive.dump"
fi
