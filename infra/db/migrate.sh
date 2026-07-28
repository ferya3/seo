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

if [ -z "${DATABASE_URL:-}" ]; then
    # Without it psql quietly falls back to a local socket and the unix user's
    # name, which either fails with a confusing message or — worse — succeeds
    # against the wrong database.
    echo "DATABASE_URL is not set" >&2
    exit 2
fi

DIR="${MIGRATIONS_DIR:-$(dirname "$0")/migrations}"
PSQL="psql $DATABASE_URL -v ON_ERROR_STOP=1"

if [ ! -d "$DIR" ] || [ -z "$(ls "$DIR"/*.sql 2>/dev/null)" ]; then
    echo "no migrations in $DIR" >&2
    exit 2
fi

$PSQL -q -c "CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)"

# A database that was migrated by hand — which is every database created before
# this script existed, including the one in the README's old instructions — has
# the whole schema and an empty ledger. Left alone, the loop below would try to
# re-apply 0001 and die on `relation "tenants" already exists`, which reads
# like a broken migration rather than a bookkeeping gap.
#
# Guessing is not an option: the script cannot tell which files a hand-run
# applied. So it stops and says what to do, and `MIGRATE_BASELINE=1` records
# every current file as applied **without running any of them** — the one
# operation that is correct when the schema is already there.
known=$($PSQL -tAc "SELECT count(*) FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'tenants'")
ledger=$($PSQL -tAc "SELECT count(*) FROM schema_migrations")

if [ "$known" = "1" ] && [ "$ledger" = "0" ]; then
    if [ -z "${MIGRATE_BASELINE:-}" ]; then
        echo "this database has the schema but no record of which migrations made it." >&2
        echo "if it was migrated by hand, adopt it with:" >&2
        echo "    MIGRATE_BASELINE=1 $0" >&2
        echo "that records every current file as applied and runs none of them." >&2
        exit 3
    fi

    for file in $(ls "$DIR"/*.sql | sort); do
        $PSQL -q -c "INSERT INTO schema_migrations (filename) VALUES ('$(basename "$file")')
                     ON CONFLICT DO NOTHING"
    done
    echo "baselined: $(ls "$DIR"/*.sql | wc -l) files recorded as already applied"
    exit 0
fi

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
