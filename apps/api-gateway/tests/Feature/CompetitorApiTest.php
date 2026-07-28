<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Project;
use App\Models\Tenant;
use App\Models\User;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

/**
 * The comparison endpoints, with the competitor service faked.
 *
 * The interesting cases are the ones about what the gateway refuses to
 * forward: a comparison with no competitors, a list long enough to be a denial
 * of service against our own crawl service, and a project id belonging to
 * somebody else.
 */
final class CompetitorApiTest extends TestCase
{
    private const ACCEPTED = [
        'comparison_id' => 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        'status' => 'queued',
        'result_url' => '/v1/comparisons/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    ];

    /** @return array{User, Tenant} */
    private function actor(): array
    {
        $tenant = Tenant::factory()->create();

        return [User::factory()->inTenant($tenant)->create(), $tenant];
    }

    public function test_requesting_a_comparison_requires_auth(): void
    {
        Http::fake();

        $this->postJson('/api/v1/comparisons', [
            'crawl_id' => 'c-1', 'competitor_crawl_ids' => ['c-2'],
        ])->assertUnauthorized();

        Http::assertNothingSent();
    }

    public function test_a_comparison_is_requested_for_the_callers_tenant(): void
    {
        [$user, $tenant] = $this->actor();
        Http::fake(['*/v1/comparisons' => Http::response(self::ACCEPTED, 202)]);

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/comparisons', [
                'crawl_id' => 'c-1', 'competitor_crawl_ids' => ['c-2', 'c-3'],
            ])
            ->assertStatus(202)
            ->assertJson(self::ACCEPTED);

        Http::assertSent(fn ($request) => $request['tenant_id'] === $tenant->id
            && $request['crawl_id'] === 'c-1'
            && $request['competitor_crawl_ids'] === ['c-2', 'c-3']);
    }

    public function test_a_comparison_with_nobody_to_compare_against_is_refused(): void
    {
        [$user] = $this->actor();
        Http::fake();

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/comparisons', ['crawl_id' => 'c-1', 'competitor_crawl_ids' => []])
            ->assertStatus(422);

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/comparisons', ['crawl_id' => 'c-1'])
            ->assertStatus(422);

        Http::assertNothingSent();
    }

    public function test_an_over_long_list_of_competitors_is_refused_here(): void
    {
        // Nine crawl reports is nine fetches held in memory at once. Refusing
        // at the door beats discovering it downstream.
        [$user] = $this->actor();
        Http::fake();

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/comparisons', [
                'crawl_id' => 'c-1',
                'competitor_crawl_ids' => array_map(fn ($i) => "c-{$i}", range(1, 9)),
            ])
            ->assertStatus(422);

        Http::assertNothingSent();
    }

    public function test_a_project_belonging_to_someone_else_is_refused(): void
    {
        [$user] = $this->actor();
        $stranger = Project::factory()->create();   // another tenant's project
        Http::fake();

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/comparisons', [
                'crawl_id' => 'c-1',
                'competitor_crawl_ids' => ['c-2'],
                'project_id' => $stranger->id,
            ])
            ->assertStatus(422);

        Http::assertNothingSent();
    }

    public function test_the_service_refusal_is_passed_through_with_its_reason(): void
    {
        [$user] = $this->actor();
        Http::fake(['*/v1/comparisons' => Http::response(
            ['detail' => 'a site cannot be its own competitor'], 422
        )]);

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/comparisons', [
                'crawl_id' => 'c-1', 'competitor_crawl_ids' => ['c-1'],
            ])
            ->assertStatus(422)
            ->assertJson(['error' => 'a site cannot be its own competitor']);
    }

    public function test_reading_a_comparison_is_scoped_to_the_caller(): void
    {
        [$user, $tenant] = $this->actor();
        Http::fake(['*' => Http::response(['comparison_id' => 'x', 'status' => 'completed'], 200)]);

        $this->actingAs($user, 'sanctum')->getJson('/api/v1/comparisons/x')->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), "tenant_id={$tenant->id}"));
    }

    public function test_an_unknown_comparison_is_404_not_a_500(): void
    {
        [$user] = $this->actor();
        Http::fake(['*' => Http::response(['detail' => 'comparison not found'], 404)]);

        $this->actingAs($user, 'sanctum')
            ->getJson('/api/v1/comparisons/nope')
            ->assertNotFound()
            ->assertJson(['error' => 'comparison not found']);
    }

    public function test_a_service_that_is_down_is_503_not_a_stack_trace(): void
    {
        [$user] = $this->actor();
        Http::fake(['*' => Http::response('', 500)]);

        $this->actingAs($user, 'sanctum')
            ->getJson('/api/v1/comparisons')
            ->assertStatus(503)
            ->assertJson(['error' => 'competitor service unavailable']);
    }
}
