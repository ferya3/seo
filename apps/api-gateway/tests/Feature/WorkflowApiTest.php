<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Project;
use App\Models\Tenant;
use App\Models\User;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

/**
 * The workflow endpoints, with the orchestrator faked.
 *
 * A workflow is one request that becomes a crawl and a keyword study. The
 * gateway's job is unchanged — authorise, validate, forward, translate — so
 * these cover the same ground as the single-service endpoints plus the shape
 * of the nested inputs.
 */
final class WorkflowApiTest extends TestCase
{
    private const ACCEPTED = [
        'workflow_id' => 'bbbbbbbb-cccc-dddd-eeee-ffffffffffff',
        'status' => 'queued',
        'result_url' => '/v1/workflows/bbbbbbbb-cccc-dddd-eeee-ffffffffffff',
    ];

    /** @return array{User, Tenant} */
    private function actor(): array
    {
        $tenant = Tenant::factory()->create();

        return [User::factory()->inTenant($tenant)->create(), $tenant];
    }

    public function test_starting_a_workflow_requires_auth(): void
    {
        Http::fake();

        $this->postJson('/api/v1/workflows', ['start_url' => 'https://example.com'])
            ->assertUnauthorized();

        Http::assertNothingSent();
    }

    public function test_a_workflow_starts_and_returns_its_id(): void
    {
        [$user] = $this->actor();
        Http::fake(['*/v1/workflows' => Http::response(self::ACCEPTED, 202)]);

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/workflows', ['start_url' => 'https://example.com'])
            ->assertStatus(202)
            ->assertJson(self::ACCEPTED);

        Http::assertSent(fn ($request) => $request['goal'] === 'site_audit');
    }

    public function test_the_inputs_are_nested_the_way_the_contract_expects(): void
    {
        /*
         * The goal decides what the inputs mean, so they travel together as one
         * object rather than being flattened alongside it.
         */
        [$user] = $this->actor();
        Http::fake(['*/v1/workflows' => Http::response(self::ACCEPTED, 202)]);

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/workflows', [
            'start_url' => 'https://example.com',
            'seed' => 'کفش ورزشی',
            'max_pages' => 20,
        ])->assertStatus(202);

        Http::assertSent(fn ($request) => $request['inputs']['start_url'] === 'https://example.com'
            && $request['inputs']['seed'] === 'کفش ورزشی'
            && $request['inputs']['max_pages'] === 20
            && ! isset($request['inputs']['goal']));
    }

    public function test_tenancy_comes_from_the_token(): void
    {
        [$user, $tenant] = $this->actor();
        Http::fake(['*/v1/workflows' => Http::response(self::ACCEPTED, 202)]);

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/workflows', [
            'start_url' => 'https://example.com',
            'tenant_id' => '22222222-2222-2222-2222-222222222222',
        ])->assertStatus(202);

        Http::assertSent(fn ($request) => $request['tenant_id'] === $tenant->id);
    }

    public function test_a_workflow_may_not_name_another_tenants_project(): void
    {
        [$user] = $this->actor();
        $theirs = Project::factory()->create();
        Http::fake();

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/workflows', [
            'start_url' => 'https://example.com',
            'project_id' => $theirs->id,
        ])->assertStatus(422)->assertJsonValidationErrors('project_id');

        Http::assertNothingSent();
    }

    /** @return array<string, array{array<string, mixed>}> */
    public static function badRequests(): array
    {
        return [
            'no url' => [[]],
            'max_pages too high' => [['start_url' => 'https://example.com', 'max_pages' => 100001]],
            'depth too high' => [['start_url' => 'https://example.com', 'max_depth' => 21]],
        ];
    }

    /**
     * @param  array<string, mixed>  $body
     */
    #[\PHPUnit\Framework\Attributes\DataProvider('badRequests')]
    public function test_bad_input_is_rejected_before_the_orchestrator_is_called(array $body): void
    {
        [$user] = $this->actor();
        Http::fake();

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/workflows', $body)->assertStatus(422);

        Http::assertNothingSent();
    }

    public function test_an_unplannable_goal_surfaces_the_orchestrators_reason(): void
    {
        [$user] = $this->actor();
        Http::fake(['*' => Http::response(['detail' => "no plan for goal 'conquer-mars'"], 422)]);

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/workflows', [
            'goal' => 'conquer-mars',
            'start_url' => 'https://example.com',
        ])->assertStatus(422)->assertJson(['error' => "no plan for goal 'conquer-mars'"]);
    }

    public function test_an_unreachable_orchestrator_is_a_503(): void
    {
        [$user] = $this->actor();
        Http::fake(fn () => throw new \Illuminate\Http\Client\ConnectionException('refused'));

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/workflows', ['start_url' => 'https://example.com'])
            ->assertStatus(503)
            ->assertJson(['error' => 'orchestrator unavailable']);
    }

    public function test_an_unknown_workflow_is_a_404(): void
    {
        [$user] = $this->actor();
        Http::fake(['*' => Http::response(['detail' => 'workflow not found'], 404)]);

        $this->actingAs($user, 'sanctum')
            ->getJson('/api/v1/workflows/'.self::ACCEPTED['workflow_id'])
            ->assertStatus(404);
    }

    public function test_a_finished_workflow_is_returned_with_its_steps(): void
    {
        [$user] = $this->actor();
        $body = [
            'workflow_id' => self::ACCEPTED['workflow_id'],
            'status' => 'completed',
            'steps' => [
                ['position' => 1, 'kind' => 'crawl', 'status' => 'completed'],
                ['position' => 2, 'kind' => 'keyword_research', 'status' => 'completed'],
            ],
            'report' => ['headline' => ['overall_score' => 73, 'keywords_found' => 128]],
        ];
        Http::fake(['*' => Http::response($body, 200)]);

        $this->actingAs($user, 'sanctum')
            ->getJson('/api/v1/workflows/'.self::ACCEPTED['workflow_id'])
            ->assertOk()
            ->assertJson($body);
    }

    public function test_starting_workflows_is_rate_limited(): void
    {
        [$user] = $this->actor();
        Http::fake(['*/v1/workflows' => Http::response(self::ACCEPTED, 202)]);

        for ($i = 0; $i < 5; $i++) {
            $this->actingAs($user, 'sanctum')
                ->postJson('/api/v1/workflows', ['start_url' => 'https://example.com'])
                ->assertStatus(202);
        }

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/workflows', ['start_url' => 'https://example.com'])
            ->assertStatus(429);
    }
    public function test_reading_a_workflow_asks_only_for_the_callers_own(): void
    {
        /*
         * The hole this closes: reads carried no tenant, so a workflow id was
         * enough to read another account's entire audit — score, keywords,
         * rankings and all — and GET /v1/workflows returned every tenant's.
         */
        [$user, $tenant] = $this->actor();
        Http::fake(['*' => Http::response(['workflow_id' => 'w-1'], 200)]);

        $this->actingAs($user, 'sanctum')->getJson('/api/v1/workflows/w-1')->assertOk();

        Http::assertSent(fn ($request) => str_contains(
            $request->url(), 'tenant_id='.$tenant->id
        ));
    }

    public function test_listing_workflows_asks_only_for_the_callers_own(): void
    {
        [$user, $tenant] = $this->actor();
        Http::fake(['*' => Http::response([], 200)]);

        $this->actingAs($user, 'sanctum')->getJson('/api/v1/workflows')->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), 'tenant_id='.$tenant->id));
    }

    public function test_another_tenants_workflow_is_not_found(): void
    {
        // The orchestrator answers 404 for a workflow the tenant does not own,
        // and the gateway passes that through unchanged — not 403, which would
        // confirm the id belongs to someone.
        [$user] = $this->actor();
        Http::fake(['*' => Http::response(['detail' => 'workflow not found'], 404)]);

        $this->actingAs($user, 'sanctum')
            ->getJson('/api/v1/workflows/somebody-elses-id')
            ->assertNotFound();
    }


    public function test_history_is_scoped_to_the_caller(): void
    {
        [$user, $tenant] = $this->actor();
        Http::fake(['*' => Http::response(['start_url' => 'https://site.test/', 'runs' => []], 200)]);

        $this->actingAs($user, 'sanctum')->getJson('/api/v1/workflows/w-1/history')->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), "tenant_id={$tenant->id}")
            && str_contains($request->url(), '/history'));
    }

    public function test_history_for_an_unknown_workflow_is_404(): void
    {
        [$user] = $this->actor();
        Http::fake(['*' => Http::response(['detail' => 'workflow not found'], 404)]);

        $this->actingAs($user, 'sanctum')
            ->getJson('/api/v1/workflows/nope/history')
            ->assertNotFound()
            ->assertJson(['error' => 'workflow not found']);
    }

    public function test_competitors_reach_the_orchestrator_inside_inputs(): void
    {
        [$user] = $this->actor();
        Http::fake(['*/v1/workflows' => Http::response(
            ['workflow_id' => 'w-9', 'status' => 'queued', 'result_url' => '/v1/workflows/w-9'], 202
        )]);

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/workflows', [
            'start_url' => 'https://site.test',
            'competitors' => ['https://rival.test'],
        ])->assertStatus(202);

        Http::assertSent(fn ($request) => $request['inputs']['competitors'] === ['https://rival.test']);
    }

    public function test_more_than_four_competitors_never_leaves_the_gateway(): void
    {
        [$user] = $this->actor();
        Http::fake();

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/workflows', [
            'start_url' => 'https://site.test',
            'competitors' => array_map(fn ($i) => "https://rival{$i}.test", range(1, 5)),
        ])->assertStatus(422);

        Http::assertNothingSent();
    }
}
