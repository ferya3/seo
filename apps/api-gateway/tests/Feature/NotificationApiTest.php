<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Tenant;
use App\Models\User;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

/**
 * Notification channels, with the service faked.
 *
 * The interesting part is what the gateway must not do: it does not repeat the
 * SSRF check (one check in two places is a check that disagrees with itself),
 * and it does not store or re-serve the signing secret.
 */
final class NotificationApiTest extends TestCase
{
    private const CHANNEL = [
        'id' => 'cccccccc-dddd-eeee-ffff-000000000000',
        'kind' => 'webhook',
        'target' => 'https://example.com/hooks/seo',
        'events' => [],
        'active' => true,
        'secret' => 'shown-once',
        'has_secret' => true,
    ];

    /** @return array{User, Tenant} */
    private function actor(): array
    {
        $tenant = Tenant::factory()->create();

        return [User::factory()->inTenant($tenant)->create(), $tenant];
    }

    public function test_managing_channels_requires_auth(): void
    {
        Http::fake();

        $this->getJson('/api/v1/notification-channels')->assertUnauthorized();
        Http::assertNothingSent();
    }

    public function test_a_channel_is_created_for_the_callers_tenant(): void
    {
        [$user, $tenant] = $this->actor();
        Http::fake(['*/v1/channels' => Http::response(self::CHANNEL, 201)]);

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/notification-channels', [
                'kind' => 'webhook', 'target' => 'https://example.com/hooks/seo',
            ])
            ->assertStatus(201)
            ->assertJsonPath('secret', 'shown-once');

        Http::assertSent(fn ($request) => $request['tenant_id'] === $tenant->id);
    }

    public function test_a_target_the_service_refuses_comes_back_as_422(): void
    {
        // The SSRF decision belongs to the service; the gateway passes on the
        // reason rather than making its own.
        [$user] = $this->actor();
        Http::fake(['*' => Http::response(['detail' => 'target not allowed: blocked'], 422)]);

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/notification-channels', [
                'kind' => 'webhook', 'target' => 'http://169.254.169.254/',
            ])
            ->assertStatus(422)
            ->assertJsonPath('error', 'target not allowed: blocked');
    }

    public function test_an_unknown_kind_is_refused_before_it_is_forwarded(): void
    {
        [$user] = $this->actor();
        Http::fake();

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/notification-channels', [
                'kind' => 'pigeon', 'target' => 'x',
            ])
            ->assertStatus(422);

        Http::assertNothingSent();
    }

    public function test_channels_are_listed_for_the_callers_tenant_only(): void
    {
        [$user, $tenant] = $this->actor();
        Http::fake(['*' => Http::response([], 200)]);

        $this->actingAs($user, 'sanctum')->getJson('/api/v1/notification-channels')->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), 'tenant_id='.$tenant->id));
    }

    public function test_deleting_another_tenants_channel_is_not_found(): void
    {
        [$user] = $this->actor();
        Http::fake(['*' => Http::response(['detail' => 'channel not found'], 404)]);

        $this->actingAs($user, 'sanctum')
            ->deleteJson('/api/v1/notification-channels/somebody-elses-id')
            ->assertNotFound();
    }

    public function test_a_channel_can_be_tested_through_the_gateway(): void
    {
        [$user] = $this->actor();
        Http::fake(['*' => Http::response(['ok' => true, 'status' => 200], 200)]);

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/notification-channels/c-1/test')
            ->assertOk()
            ->assertJsonPath('ok', true);
    }

    public function test_deliveries_are_listed_for_the_callers_tenant(): void
    {
        [$user, $tenant] = $this->actor();
        Http::fake(['*' => Http::response([], 200)]);

        $this->actingAs($user, 'sanctum')->getJson('/api/v1/notification-deliveries')->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), 'tenant_id='.$tenant->id));
    }

    public function test_an_unreachable_service_is_503(): void
    {
        [$user] = $this->actor();
        Http::fake(fn () => throw new \Illuminate\Http\Client\ConnectionException('refused'));

        $this->actingAs($user, 'sanctum')
            ->getJson('/api/v1/notification-channels')
            ->assertStatus(503);
    }
}
