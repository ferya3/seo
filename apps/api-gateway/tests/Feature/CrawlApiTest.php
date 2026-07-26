<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\RateLimiter;
use Tests\TestCase;

/**
 * The crawl endpoints, with the crawl service faked.
 *
 * What is under test is the gateway's job — auth, tenancy, validation, and how
 * a downstream failure is translated — not the crawl service, which has its
 * own suite in services/crawl/tests.
 */
final class CrawlApiTest extends TestCase
{
    use RefreshDatabase;

    private const ACCEPTED = [
        'crawl_id' => '11111111-2222-3333-4444-555555555555',
        'status' => 'queued',
        'result_url' => '/v1/crawls/11111111-2222-3333-4444-555555555555',
    ];

    protected function setUp(): void
    {
        parent::setUp();
        RateLimiter::clear('api');
        Http::preventStrayRequests();
    }

    private function user(?string $tenantId = null): User
    {
        $user = User::factory()->create();
        if ($tenantId !== null) {
            $user->forceFill(['tenant_id' => $tenantId])->save();
        }

        return $user;
    }

    // ------------------------------------------------------------------ auth

    public function test_healthz_is_open(): void
    {
        $this->getJson('/api/healthz')
            ->assertOk()
            ->assertJson(['status' => 'ok', 'service' => 'api-gateway']);
    }

    public function test_an_unauthenticated_request_is_rejected(): void
    {
        Http::fake();

        $this->postJson('/api/v1/crawls', ['start_url' => 'https://example.com'])
            ->assertUnauthorized();

        // The point of rejecting at the gateway: nothing reached the service.
        Http::assertNothingSent();
    }

    public function test_reading_a_crawl_also_requires_auth(): void
    {
        $this->getJson('/api/v1/crawls/'.self::ACCEPTED['crawl_id'])->assertUnauthorized();
        $this->getJson('/api/v1/crawls')->assertUnauthorized();
    }

    // -------------------------------------------------------------- tenancy

    public function test_tenancy_comes_from_the_authenticated_user(): void
    {
        Http::fake(['*/v1/crawls' => Http::response(self::ACCEPTED, 202)]);
        $tenant = '99999999-8888-7777-6666-555555555555';

        $this->actingAs($this->user($tenant))
            ->postJson('/api/v1/crawls', ['start_url' => 'https://example.com'])
            ->assertStatus(202)
            ->assertJson(self::ACCEPTED);

        Http::assertSent(fn ($request) => $request['tenant_id'] === $tenant);
    }

    public function test_a_tenant_id_in_the_body_is_ignored(): void
    {
        /*
         * The one test in this file that is about security rather than
         * plumbing. If the body could set tenant_id, any authenticated caller
         * could read and write another tenant's data by typing their id.
         */
        Http::fake(['*/v1/crawls' => Http::response(self::ACCEPTED, 202)]);
        $mine = '11111111-1111-1111-1111-111111111111';
        $theirs = '22222222-2222-2222-2222-222222222222';

        $this->actingAs($this->user($mine))
            ->postJson('/api/v1/crawls', [
                'start_url' => 'https://example.com',
                'tenant_id' => $theirs,
            ])
            ->assertStatus(202);

        Http::assertSent(fn ($request) => $request['tenant_id'] === $mine);
    }

    public function test_a_correlation_id_header_is_forwarded(): void
    {
        Http::fake(['*/v1/crawls' => Http::response(self::ACCEPTED, 202)]);

        $this->actingAs($this->user())
            ->withHeader('X-Correlation-Id', 'trace-abc')
            ->postJson('/api/v1/crawls', ['start_url' => 'https://example.com'])
            ->assertStatus(202);

        Http::assertSent(fn ($request) => $request['correlation_id'] === 'trace-abc');
    }

    // ----------------------------------------------------------- validation

    /** @return array<string, array{array<string, mixed>}> */
    public static function badRequests(): array
    {
        return [
            'no url' => [[]],
            'max_pages too low' => [['start_url' => 'https://example.com', 'max_pages' => 0]],
            'max_pages too high' => [['start_url' => 'https://example.com', 'max_pages' => 100001]],
            'depth too high' => [['start_url' => 'https://example.com', 'max_depth' => 21]],
            'unknown agent' => [['start_url' => 'https://example.com', 'user_agent' => 'ie6']],
            'too many keywords' => [[
                'start_url' => 'https://example.com',
                'target_keywords' => array_fill(0, 51, 'x'),
            ]],
        ];
    }

    /**
     * @param  array<string, mixed>  $body
     */
    #[\PHPUnit\Framework\Attributes\DataProvider('badRequests')]
    public function test_bad_input_is_rejected_before_the_service_is_called(array $body): void
    {
        Http::fake();

        $this->actingAs($this->user())
            ->postJson('/api/v1/crawls', $body)
            ->assertStatus(422);

        Http::assertNothingSent();
    }

    // ------------------------------------------------- downstream failures

    public function test_a_rejected_url_surfaces_the_services_own_reason(): void
    {
        /*
         * A blocked internal target is the caller's mistake, not an outage.
         * Flattening it into a generic 502 would hide the one piece of
         * information that lets them fix it.
         */
        Http::fake(['*/v1/crawls' => Http::response(
            ['detail' => 'target 169.254.169.254 is not allowed'], 400
        )]);

        $this->actingAs($this->user())
            ->postJson('/api/v1/crawls', ['start_url' => 'http://169.254.169.254/'])
            ->assertStatus(422)
            ->assertJson(['error' => 'target 169.254.169.254 is not allowed']);
    }

    public function test_an_unreachable_service_is_a_503_not_a_500(): void
    {
        Http::fake(fn () => throw new \Illuminate\Http\Client\ConnectionException('refused'));

        $this->actingAs($this->user())
            ->postJson('/api/v1/crawls', ['start_url' => 'https://example.com'])
            ->assertStatus(503)
            ->assertJson(['error' => 'crawl service unavailable']);
    }

    public function test_an_unreachable_service_on_read_is_also_a_503(): void
    {
        Http::fake(fn () => throw new \Illuminate\Http\Client\ConnectionException('refused'));

        $this->actingAs($this->user())
            ->getJson('/api/v1/crawls/'.self::ACCEPTED['crawl_id'])
            ->assertStatus(503);
    }

    public function test_an_unknown_crawl_is_a_404(): void
    {
        Http::fake(['*' => Http::response(['detail' => 'crawl not found'], 404)]);

        $this->actingAs($this->user())
            ->getJson('/api/v1/crawls/'.self::ACCEPTED['crawl_id'])
            ->assertStatus(404)
            ->assertJson(['error' => 'crawl not found']);
    }

    // ---------------------------------------------------------------- reads

    public function test_a_finished_crawl_is_returned_as_the_service_reports_it(): void
    {
        $report = [
            'crawl_id' => self::ACCEPTED['crawl_id'],
            'status' => 'completed',
            'overall_score' => 73,
            'report' => ['grade' => 'B'],
        ];
        Http::fake(['*' => Http::response($report, 200)]);

        $this->actingAs($this->user())
            ->getJson('/api/v1/crawls/'.self::ACCEPTED['crawl_id'])
            ->assertOk()
            ->assertJson($report);
    }

    public function test_the_list_passes_the_limit_through(): void
    {
        Http::fake(['*' => Http::response([], 200)]);

        $this->actingAs($this->user())->getJson('/api/v1/crawls?limit=5')->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), 'limit=5'));
    }

    // --------------------------------------------------------- rate limiting

    public function test_starting_crawls_is_rate_limited(): void
    {
        /*
         * A crawl costs minutes of someone else's bandwidth, so POST has a
         * tighter budget than the general API limit. Without this the gateway
         * is a free amplifier.
         */
        Http::fake(['*/v1/crawls' => Http::response(self::ACCEPTED, 202)]);
        $user = $this->user();

        for ($i = 0; $i < 5; $i++) {
            $this->actingAs($user)
                ->postJson('/api/v1/crawls', ['start_url' => 'https://example.com'])
                ->assertStatus(202);
        }

        $this->actingAs($user)
            ->postJson('/api/v1/crawls', ['start_url' => 'https://example.com'])
            ->assertStatus(429);
    }

    public function test_the_limit_is_per_user_not_global(): void
    {
        Http::fake(['*/v1/crawls' => Http::response(self::ACCEPTED, 202)]);
        $noisy = $this->user();

        for ($i = 0; $i < 6; $i++) {
            $this->actingAs($noisy)->postJson('/api/v1/crawls', ['start_url' => 'https://example.com']);
        }

        // A second user must not inherit the first one's exhausted budget.
        $this->actingAs($this->user())
            ->postJson('/api/v1/crawls', ['start_url' => 'https://example.com'])
            ->assertStatus(202);
    }
}
