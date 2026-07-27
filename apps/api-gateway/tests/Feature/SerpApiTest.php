<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Project;
use App\Models\Tenant;
use App\Models\User;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

/**
 * The rank-check endpoints, with the SERP service faked.
 */
final class SerpApiTest extends TestCase
{
    private const ACCEPTED = [
        'check_id' => 'cccccccc-dddd-eeee-ffff-000000000000',
        'status' => 'queued',
        'result_url' => '/v1/checks/cccccccc-dddd-eeee-ffff-000000000000',
    ];

    private const BODY = ['target_domain' => 'example.com', 'keywords' => ['کفش ورزشی']];

    /** @return array{User, Tenant} */
    private function actor(): array
    {
        $tenant = Tenant::factory()->create();

        return [User::factory()->inTenant($tenant)->create(), $tenant];
    }

    public function test_a_check_requires_auth(): void
    {
        Http::fake();

        $this->postJson('/api/v1/checks', self::BODY)->assertUnauthorized();

        Http::assertNothingSent();
    }

    public function test_a_check_starts_and_returns_its_id(): void
    {
        [$user] = $this->actor();
        Http::fake(['*/v1/checks' => Http::response(self::ACCEPTED, 202)]);

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/checks', self::BODY)
            ->assertStatus(202)
            ->assertJson(self::ACCEPTED);

        Http::assertSent(fn ($request) => $request['keywords'] === ['کفش ورزشی']);
    }

    public function test_tenancy_comes_from_the_token(): void
    {
        [$user, $tenant] = $this->actor();
        Http::fake(['*/v1/checks' => Http::response(self::ACCEPTED, 202)]);

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/checks', [
            ...self::BODY,
            'tenant_id' => '22222222-2222-2222-2222-222222222222',
        ])->assertStatus(202);

        Http::assertSent(fn ($request) => $request['tenant_id'] === $tenant->id);
    }

    public function test_a_check_may_not_name_another_tenants_project(): void
    {
        [$user] = $this->actor();
        $theirs = Project::factory()->create();
        Http::fake();

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/checks', [...self::BODY, 'project_id' => $theirs->id])
            ->assertStatus(422)->assertJsonValidationErrors('project_id');

        Http::assertNothingSent();
    }

    /** @return array<string, array{array<string, mixed>}> */
    public static function badRequests(): array
    {
        return [
            'nothing' => [[]],
            'no keywords' => [['target_domain' => 'example.com']],
            'empty keyword list' => [['target_domain' => 'example.com', 'keywords' => []]],
            'no domain' => [['keywords' => ['کفش']]],
            // Every keyword is a live search request; an unbounded list is a
            // way to get the platform's IP blocked.
            'over the cap' => [[
                'target_domain' => 'example.com',
                'keywords' => array_fill(0, 51, 'کفش'),
            ]],
        ];
    }

    /**
     * @param  array<string, mixed>  $body
     */
    #[\PHPUnit\Framework\Attributes\DataProvider('badRequests')]
    public function test_bad_input_is_rejected_before_the_service_is_called(array $body): void
    {
        [$user] = $this->actor();
        Http::fake();

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/checks', $body)->assertStatus(422);

        Http::assertNothingSent();
    }

    public function test_an_unreachable_service_is_a_503(): void
    {
        [$user] = $this->actor();
        Http::fake(fn () => throw new \Illuminate\Http\Client\ConnectionException('refused'));

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/checks', self::BODY)
            ->assertStatus(503)
            ->assertJson(['error' => 'serp service unavailable']);
    }

    public function test_an_unknown_check_is_a_404(): void
    {
        [$user] = $this->actor();
        Http::fake(['*' => Http::response(['detail' => 'check not found'], 404)]);

        $this->actingAs($user, 'sanctum')
            ->getJson('/api/v1/checks/'.self::ACCEPTED['check_id'])
            ->assertStatus(404);
    }

    public function test_a_finished_check_is_returned(): void
    {
        [$user] = $this->actor();
        $body = [
            'check_id' => self::ACCEPTED['check_id'],
            'status' => 'completed',
            'target_domain' => 'example.com',
            'report' => [
                'average_position' => 3.5,
                'top_competitors' => [['domain' => 'rival.com', 'outranks_on' => 2]],
            ],
        ];
        Http::fake(['*' => Http::response($body, 200)]);

        $this->actingAs($user, 'sanctum')
            ->getJson('/api/v1/checks/'.self::ACCEPTED['check_id'])
            ->assertOk()
            ->assertJson($body);
    }

    public function test_checks_are_rate_limited(): void
    {
        [$user] = $this->actor();
        Http::fake(['*/v1/checks' => Http::response(self::ACCEPTED, 202)]);

        for ($i = 0; $i < 5; $i++) {
            $this->actingAs($user, 'sanctum')
                ->postJson('/api/v1/checks', self::BODY)->assertStatus(202);
        }

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/checks', self::BODY)->assertStatus(429);
    }
    public function test_reading_a_check_asks_only_for_the_callers_own(): void
    {
        // Reads used to carry no tenant at all: any id read any account's data.
        [$user, $tenant] = $this->actor();
        Http::fake(['*' => Http::response(['id' => 'x'], 200)]);

        $this->actingAs($user, 'sanctum')->getJson('/api/v1/checks/x')->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), 'tenant_id='.$tenant->id));
    }

    public function test_listing_checks_asks_only_for_the_callers_own(): void
    {
        [$user, $tenant] = $this->actor();
        Http::fake(['*' => Http::response([], 200)]);

        $this->actingAs($user, 'sanctum')->getJson('/api/v1/checks')->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), 'tenant_id='.$tenant->id));
    }

}
