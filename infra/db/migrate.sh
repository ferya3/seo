#!/bin/sh
# Apply the migrations that have not been applied yet.
#
#   DATABASE_URL=postgresql://seo@127.0.0.1:5432/seo infra/db/migrate.sh
#
# Until now the migrations were applied by hand, with a shell loop written out
# in the README. That works exactly once per person and not at all inside
# compose, where nothing ran them and every service came up against an empty
# database.
#
# The whole reason this is more than `for f in *.sql; do psql -f $f; done`:
# the files are not idempotent. They CREATE TABLE, so the second run of that
# loop fails, and a stack you cannot restart is not a stack. `schema_migrations`
# records what has been applied, and each file is applied together with its
# own row **in one transaction** — so a file that fails halfway leaves neither
# the tables nor the claim that it ran.
#
# Written for the postgres image's own shell, which is not bash.
set -eu

DIR="${MIGRATIONS_DIR:-$(dirname "$0")/migrations}"
PSQL="psql ${DATABASE_URL:-} -v ON_ERROR_STOP=1"

$PSQL -q -c "CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)"

applied=0
skipped=0

# Sorted, because the numbering is the order: 0003 adds columns 0001 created.
for file in $(ls "$DIR"/*.sql | sort); do
    name=$(basename "$file")

    if [ "$($PSQL -tAc "SELECT 1 FROM schema_migrations WHERE filename = '$name'")" = "1" ]; then
        skipped=$((skipped + 1))
        continue
    fi

    echo "applying $name"
    # One transaction for the file and the row that says it ran. Separately,
    # a crash between them leaves a migration that is applied and will be
    # applied again.
    $PSQL -q --single-transaction \
        -f "$file" \
        -c "INSERT INTO schema_migrations (filename) VALUES ('$name')"
    applied=$((applied + 1))
done

echo "migrations: $applied applied, $skipped already there"
