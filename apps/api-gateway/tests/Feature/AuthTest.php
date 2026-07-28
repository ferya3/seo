<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Tenant;
use App\Models\User;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use Tests\TestCase;

/**
 * Registration, login, logout.
 *
 * The reason this layer exists: the Python services constrain tenant_id with a
 * foreign key, so until a tenant row is created here, an authenticated request
 * carrying a perfectly valid-looking tenant id fails downstream.
 */
final class AuthTest extends TestCase
{
    private const CREDENTIALS = [
        'name' => 'Saeed',
        'email' => 'saeed@example.com',
        'password' => 'a-long-enough-password',
        'tenant_name' => 'Acme',
    ];

    // -------------------------------------------------------------- register

    public function test_registering_creates_a_tenant_and_a_token(): void
    {
        $response = $this->postJson('/api/v1/auth/register', self::CREDENTIALS)
            ->assertStatus(201)
            ->assertJsonStructure(['user' => ['id', 'name', 'email', 'tenant_id'], 'token']);

        $tenantId = $response->json('user.tenant_id');
        $this->assertNotNull($tenantId);
        $this->assertDatabaseHas('tenants', ['id' => $tenantId, 'name' => 'Acme']);
        $this->assertDatabaseHas('users', ['email' => 'saeed@example.com', 'tenant_id' => $tenantId]);
    }

    public function test_the_tenant_row_is_the_one_the_services_can_reference(): void
    {
        /*
         * The point of the whole change. Before this existed, the gateway
         * handed the services a tenant id from its own separate user store and
         * the foreign key rejected it. Inserting a crawl against the new
         * tenant is the direct proof that no longer happens.
         */
        $tenantId = $this->postJson('/api/v1/auth/register', self::CREDENTIALS)
            ->json('user.tenant_id');

        DB::insert(
            'INSERT INTO crawls (id, tenant_id, start_url, status) VALUES (?, ?, ?, ?)',
            ['dddddddd-dddd-dddd-dddd-dddddddddddd', $tenantId, 'https://example.com', 'queued']
        );

        $this->assertDatabaseHas('crawls', ['tenant_id' => $tenantId]);
    }

    public function test_the_returned_token_authenticates(): void
    {
        $token = $this->postJson('/api/v1/auth/register', self::CREDENTIALS)->json('token');

        $this->withHeader('Authorization', "Bearer {$token}")
            ->getJson('/api/v1/me')
            ->assertOk()
            ->assertJson(['email' => 'saeed@example.com']);
    }

    public function test_the_password_is_never_stored_or_returned_in_the_clear(): void
    {
        $response = $this->postJson('/api/v1/auth/register', self::CREDENTIALS);

        $this->assertStringNotContainsString('a-long-enough-password', $response->getContent());

        $stored = User::where('email', 'saeed@example.com')->first();
        $this->assertNotSame('a-long-enough-password', $stored->password_hash);
        $this->assertTrue(Hash::check('a-long-enough-password', $stored->password_hash));
    }

    public function test_a_duplicate_email_is_rejected(): void
    {
        $this->postJson('/api/v1/auth/register', self::CREDENTIALS)->assertStatus(201);

        $this->postJson('/api/v1/auth/register', self::CREDENTIALS)
            ->assertStatus(422)
            ->assertJsonValidationErrors('email');
    }

    public function test_email_uniqueness_ignores_case(): void
    {
        /*
         * The column is CITEXT precisely so Saeed@ and saeed@ cannot become
         * two accounts — one of the cases that would have passed against
         * SQLite and failed in production.
         */
        $this->postJson('/api/v1/auth/register', self::CREDENTIALS)->assertStatus(201);

        $this->postJson('/api/v1/auth/register', [
            ...self::CREDENTIALS,
            'email' => 'SAEED@Example.com',
        ])->assertStatus(422);
    }

    public function test_a_short_password_is_rejected(): void
    {
        $this->postJson('/api/v1/auth/register', [...self::CREDENTIALS, 'password' => 'short'])
            ->assertStatus(422)
            ->assertJsonValidationErrors('password');

        $this->assertDatabaseCount('tenants', 0);
    }

    public function test_a_failed_registration_leaves_no_orphan_tenant(): void
    {
        // A tenant with no user is unreachable; half of this is worse than none.
        $this->postJson('/api/v1/auth/register', ['tenant_name' => 'Ghost'])->assertStatus(422);

        $this->assertDatabaseCount('tenants', 0);
        $this->assertDatabaseCount('users', 0);
    }

    // ----------------------------------------------------------------- login

    public function test_login_returns_a_working_token(): void
    {
        $this->postJson('/api/v1/auth/register', self::CREDENTIALS);

        $token = $this->postJson('/api/v1/auth/login', [
            'email' => 'saeed@example.com',
            'password' => 'a-long-enough-password',
        ])->assertOk()->json('token');

        $this->withHeader('Authorization', "Bearer {$token}")->getJson('/api/v1/me')->assertOk();
    }

    public function test_login_is_case_insensitive_on_email(): void
    {
        $this->postJson('/api/v1/auth/register', self::CREDENTIALS);

        $this->postJson('/api/v1/auth/login', [
            'email' => 'SAEED@example.com',
            'password' => 'a-long-enough-password',
        ])->assertOk();
    }

    public function test_a_wrong_password_and_an_unknown_email_look_identical(): void
    {
        /*
         * Different messages here would turn the login form into an account
         * enumerator: an attacker could confirm who has an account without
         * ever guessing a password.
         */
        $this->postJson('/api/v1/auth/register', self::CREDENTIALS);

        $wrongPassword = $this->postJson('/api/v1/auth/login', [
            'email' => 'saeed@example.com', 'password' => 'not-the-password',
        ])->assertStatus(422);

        $noSuchUser = $this->postJson('/api/v1/auth/login', [
            'email' => 'nobody@example.com', 'password' => 'not-the-password',
        ])->assertStatus(422);

        $this->assertSame($wrongPassword->json(), $noSuchUser->json());
    }

    public function test_login_is_rate_limited_by_ip(): void
    {
        for ($i = 0; $i < 10; $i++) {
            $this->postJson('/api/v1/auth/login', ['email' => 'a@b.com', 'password' => 'wrong']);
        }

        $this->postJson('/api/v1/auth/login', ['email' => 'a@b.com', 'password' => 'wrong'])
            ->assertStatus(429);
    }

    // ---------------------------------------------------------------- logout

    public function test_logout_revokes_only_the_token_that_used_it(): void
    {
        $user = User::factory()->create();
        $laptop = $user->createToken('laptop')->plainTextToken;
        $phone = $user->createToken('phone')->plainTextToken;

        $this->withHeader('Authorization', "Bearer {$laptop}")
            ->postJson('/api/v1/auth/logout')->assertOk();

        // The test process reuses one container across requests, so the guard
        // still holds the user it resolved a moment ago. Real requests each get
        // a fresh container; without this the assertion below passes for the
        // wrong reason. (Verified against a running server: a revoked token
        // does return 401 over real HTTP.)
        $this->app['auth']->forgetGuards();

        $this->withHeader('Authorization', "Bearer {$laptop}")
            ->getJson('/api/v1/me')->assertUnauthorized();

        // Signing out of a laptop must not sign you out on a phone.
        $this->app['auth']->forgetGuards();
        $this->withHeader('Authorization', "Bearer {$phone}")
            ->getJson('/api/v1/me')->assertOk();
    }

    public function test_me_requires_a_token(): void
    {
        $this->getJson('/api/v1/me')->assertUnauthorized();
    }

    public function test_me_reports_the_users_own_tenant(): void
    {
        $tenant = Tenant::factory()->create(['name' => 'Acme']);
        $user = User::factory()->inTenant($tenant)->create();

        $this->actingAs($user, 'sanctum')->getJson('/api/v1/me')
            ->assertOk()
            ->assertJson(['tenant_id' => $tenant->id]);
    }

    public function test_an_unauthenticated_request_is_401_even_without_an_accept_header(): void
    {
        /*
         * Found by curling the running gateway, not by this suite: getJson()
         * sets Accept: application/json, and that path never reaches the guest
         * redirect. A plain curl did, Laravel tried to redirect to a `login`
         * route this API does not have, and a missing token came back as 500.
         */
        $response = $this->get('/api/v1/workflows');

        $response->assertStatus(401);
        $this->assertStringContainsString('Unauthenticated', $response->getContent());
    }
}
