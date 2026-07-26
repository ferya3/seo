<?php

declare(strict_types=1);

namespace Tests;

use Illuminate\Foundation\Testing\TestCase as BaseTestCase;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\RateLimiter;

/**
 * Tests run against a real Postgres, not SQLite.
 *
 * The gateway now depends on things SQLite does not have: UUID primary keys,
 * CITEXT for case-insensitive email uniqueness, and foreign keys into tables
 * the Python services own. Testing against a different engine than production
 * would mean the interesting cases — a duplicate Ali@ vs ali@, a project id
 * belonging to another tenant — pass here and fail in the real thing.
 *
 * The schema comes from infra/db/migrations/*.sql, which is authoritative for
 * this database. That is also why there is no RefreshDatabase: Laravel does
 * not own this schema and must not try to build it. Tables are truncated
 * between tests instead.
 *
 * Point TEST_DATABASE_URL at a database with those migrations applied. Without
 * it the suite skips rather than quietly testing something else:
 *
 *   createdb seo_gw
 *   for f in ../../infra/db/migrations/*.sql; do psql -d seo_gw -f "$f"; done
 *   TEST_DATABASE_URL=postgresql://seo@127.0.0.1:5432/seo_gw php artisan test
 */
abstract class TestCase extends BaseTestCase
{
    /** Tables this suite writes. Truncated together, so order does not matter. */
    private const TABLES = [
        'personal_access_tokens', 'outbox', 'research', 'crawls',
        'projects', 'users', 'tenants',
    ];

    protected function setUp(): void
    {
        if (! self::dsn()) {
            $this->markTestSkipped('TEST_DATABASE_URL is not set');
        }

        parent::setUp();

        DB::statement('TRUNCATE '.implode(', ', self::TABLES).' RESTART IDENTITY CASCADE');

        // Limits are keyed per user and per IP, and the test client reuses one
        // IP. Without clearing, the first test to exhaust a bucket fails every
        // test after it, in an order-dependent way that is miserable to debug.
        foreach (['api', 'crawls', 'auth'] as $limiter) {
            RateLimiter::clear($limiter);
        }

        // An HTTP call this suite did not explicitly fake is a bug in the test,
        // not something to let escape to a real network.
        Http::preventStrayRequests();
    }

    public static function dsn(): ?string
    {
        return getenv('TEST_DATABASE_URL') ?: null;
    }
}
