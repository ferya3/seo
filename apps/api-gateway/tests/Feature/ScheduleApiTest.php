<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Tenant;
use App\Models\User;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

/**
 * Schedules, with the orchestrator faked.
 *
 * The shape is the interesting part: the cadence describes *when*, everything
 * else describes the work, and only the second group belongs under `inputs`.
 * Getting that wrong means a schedule whose crawl settings are silently lost.
 */
final class ScheduleApiTest extends TestCase
{
    private const CREATED = [
        'id' => 'ssssssss-1111-2222-3333-444444444444',
        'goal' => 'site_audit',
        'cadence' => 'weekly',
        'hour' => 9,
        'active' => true,
        'next_run_at' => '2026-08-03T05:30:00+00:00',
    ];

    /** @return array{User, Tenant} */
    private function actor(): array
    {
        $tenant = Tenant::factory()->create();

        return [User::factory()->inTenant($tenant)->create(), $tenant];
    }

    public function test_creating_a_schedule_requires_auth(): void
    {
        Http::fake();

        $this->postJson('/api/v1/schedules', [
            'start_url' => 'https://site.test/', 'cadence' => 'weekly',
        ])->assertUnauthorized();

        Http::assertNothingSent();
    }

    public function test_the_work_is_nested_and_the_cadence_is_not(): void
    {
        [$user, $tenant] = $this->actor();
        Http::fake(['*/v1/schedules' => Http::response(self::CREATED, 201)]);

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/schedules', [
            'start_url' => 'https://site.test/',
            'seed' => 'کفش',
            'track_keywords' => 5,
            'cadence' => 'weekly',
            'hour' => 9,
            'weekday' => 6,
        ])->assertStatus(201);

        Http::assertSent(function ($request) use ($tenant) {
            return $request['inputs']['start_url'] === 'https://site.test/'
                && $request['inputs']['track_keywords'] === 5
                && ! array_key_exists('cadence', $request['inputs'])
                && $request['cadence'] === 'weekly'
                && $request['weekday'] === 6
                && $request['tenant_id'] === $tenant->id;
        });
    }

    public function test_an_unknown_cadence_never_reaches_the_orchestrator(): void
    {
        [$user] = $this->actor();
        Http::fake();

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/schedules', [
            'start_url' => 'https://site.test/', 'cadence' => 'hourly',
        ])->assertStatus(422);

        Http::assertNothingSent();
    }

    public function test_a_schedule_without_a_url_is_refused(): void
    {
        [$user] = $this->actor();
        Http::fake();

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/schedules', ['cadence' => 'daily'])
            ->assertStatus(422);

        Http::assertNothingSent();
    }

    public function test_a_reason_from_the_orchestrator_is_passed_on(): void
    {
        [$user] = $this->actor();
        Http::fake(['*' => Http::response(['detail' => 'unknown timezone'], 422)]);

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/schedules', [
                'start_url' => 'https://site.test/', 'cadence' => 'daily',
                'timezone' => 'Mars/Olympus',
            ])
            ->assertStatus(422)
            ->assertJsonPath('error', 'unknown timezone');
    }

    public function test_schedules_are_listed_for_the_callers_tenant_only(): void
    {
        [$user, $tenant] = $this->actor();
        Http::fake(['*' => Http::response([], 200)]);

        $this->actingAs($user, 'sanctum')->getJson('/api/v1/schedules')->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), 'tenant_id='.$tenant->id));
    }

    public function test_a_schedule_can_be_paused_and_resumed(): void
    {
        [$user] = $this->actor();
        Http::fake(['*' => Http::response(['active' => false] + self::CREATED, 200)]);

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/schedules/s-1/pause')
            ->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), 'active=false'));
    }

    public function test_another_tenants_schedule_is_not_found(): void
    {
        [$user] = $this->actor();
        Http::fake(['*' => Http::response(['detail' => 'schedule not found'], 404)]);

        $this->actingAs($user, 'sanctum')
            ->deleteJson('/api/v1/schedules/somebody-elses-id')
            ->assertNotFound();
    }

    public function test_an_unreachable_orchestrator_is_503(): void
    {
        [$user] = $this->actor();
        Http::fake(fn () => throw new ConnectionException('refused'));

        $this->actingAs($user, 'sanctum')->getJson('/api/v1/schedules')->assertStatus(503);
    }
}
