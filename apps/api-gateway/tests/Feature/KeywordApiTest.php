<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Tenant;
use App\Models\User;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

/**
 * The research endpoints, with the keyword service faked.
 *
 * Near-identical to CrawlApiTest on purpose: the two endpoints are meant to
 * behave the same way, and a divergence here would be a real one.
 */
final class KeywordApiTest extends TestCase
{
    private const ACCEPTED = [
        'research_id' => 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        'status' => 'queued',
        'result_url' => '/v1/research/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    ];


    /**
     * The tenant is a real row now, not a made-up uuid: users.tenant_id has a
     * foreign key, so an invented tenant no longer inserts.
     */
    private function user(?Tenant $tenant = null): User
    {
        return User::factory()->inTenant($tenant ?? Tenant::factory()->create())->create();
    }

    public function test_an_unauthenticated_request_is_rejected(): void
    {
        Http::fake();

        $this->postJson('/api/v1/research', ['seed' => 'کفش'])->assertUnauthorized();

        Http::assertNothingSent();
    }

    public function test_research_starts_and_returns_the_id(): void
    {
        Http::fake(['*/v1/research' => Http::response(self::ACCEPTED, 202)]);

        $this->actingAs($this->user(), 'sanctum')
            ->postJson('/api/v1/research', ['seed' => 'کفش ورزشی'])
            ->assertStatus(202)
            ->assertJson(self::ACCEPTED);

        Http::assertSent(fn ($request) => $request['seed'] === 'کفش ورزشی');
    }

    public function test_a_persian_seed_survives_the_hop(): void
    {
        /*
         * Encoding has bitten this project before — a header-declared charset
         * turned Persian into mojibake in the fetcher. Worth pinning at the
         * gateway boundary too, since this is where the bytes change hands.
         */
        Http::fake(['*/v1/research' => Http::response(self::ACCEPTED, 202)]);
        $seed = 'خرید کفش ورزشی زنانه';

        $this->actingAs($this->user(), 'sanctum')
            ->postJson('/api/v1/research', ['seed' => $seed])
            ->assertStatus(202);

        Http::assertSent(fn ($request) => $request['seed'] === $seed);
    }

    public function test_a_tenant_id_in_the_body_is_ignored(): void
    {
        Http::fake(['*/v1/research' => Http::response(self::ACCEPTED, 202)]);
        $mine = Tenant::factory()->create();

        $this->actingAs($this->user($mine), 'sanctum')
            ->postJson('/api/v1/research', [
                'seed' => 'کفش',
                'tenant_id' => '22222222-2222-2222-2222-222222222222',
            ])
            ->assertStatus(202);

        Http::assertSent(fn ($request) => $request['tenant_id'] === $mine->id);
    }

    /** @return array<string, array{array<string, mixed>}> */
    public static function badRequests(): array
    {
        return [
            'no seed' => [[]],
            'blank seed' => [['seed' => '']],
            'max_keywords too low' => [['seed' => 'کفش', 'max_keywords' => 9]],
            'max_keywords too high' => [['seed' => 'کفش', 'max_keywords' => 5001]],
            'unknown source' => [['seed' => 'کفش', 'sources' => ['yandex']]],
        ];
    }

    /**
     * @param  array<string, mixed>  $body
     */
    #[\PHPUnit\Framework\Attributes\DataProvider('badRequests')]
    public function test_bad_input_is_rejected_before_the_service_is_called(array $body): void
    {
        Http::fake();

        $this->actingAs($this->user(), 'sanctum')->postJson('/api/v1/research', $body)->assertStatus(422);

        Http::assertNothingSent();
    }

    public function test_an_unreachable_service_is_a_503(): void
    {
        Http::fake(fn () => throw new \Illuminate\Http\Client\ConnectionException('refused'));

        $this->actingAs($this->user(), 'sanctum')
            ->postJson('/api/v1/research', ['seed' => 'کفش'])
            ->assertStatus(503)
            ->assertJson(['error' => 'keyword service unavailable']);
    }

    public function test_unknown_research_is_a_404(): void
    {
        Http::fake(['*' => Http::response(['detail' => 'research not found'], 404)]);

        $this->actingAs($this->user(), 'sanctum')
            ->getJson('/api/v1/research/'.self::ACCEPTED['research_id'])
            ->assertStatus(404);
    }

    public function test_a_finished_run_is_returned(): void
    {
        $result = [
            'research_id' => self::ACCEPTED['research_id'],
            'status' => 'completed',
            'total' => 128,
            'report' => ['clusters' => [['label' => 'قیمت']]],
        ];
        Http::fake(['*' => Http::response($result, 200)]);

        $this->actingAs($this->user(), 'sanctum')
            ->getJson('/api/v1/research/'.self::ACCEPTED['research_id'])
            ->assertOk()
            ->assertJson($result);
    }

    public function test_starting_research_is_rate_limited(): void
    {
        Http::fake(['*/v1/research' => Http::response(self::ACCEPTED, 202)]);
        $user = $this->user();

        for ($i = 0; $i < 5; $i++) {
            $this->actingAs($user, 'sanctum')->postJson('/api/v1/research', ['seed' => 'کفش'])->assertStatus(202);
        }

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/research', ['seed' => 'کفش'])->assertStatus(429);
    }
    public function test_reading_a_research_asks_only_for_the_callers_own(): void
    {
        // Reads used to carry no tenant at all: any id read any account's data.
        $tenant = Tenant::factory()->create();
        $user = $this->user($tenant);
        Http::fake(['*' => Http::response(['id' => 'x'], 200)]);

        $this->actingAs($user, 'sanctum')->getJson('/api/v1/research/x')->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), 'tenant_id='.$tenant->id));
    }

    public function test_listing_research_asks_only_for_the_callers_own(): void
    {
        $tenant = Tenant::factory()->create();
        $user = $this->user($tenant);
        Http::fake(['*' => Http::response([], 200)]);

        $this->actingAs($user, 'sanctum')->getJson('/api/v1/research')->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), 'tenant_id='.$tenant->id));
    }

}
