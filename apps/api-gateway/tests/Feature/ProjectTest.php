<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Project;
use App\Models\Tenant;
use App\Models\User;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

/**
 * Projects, and the tenancy boundary around them.
 *
 * Most of this file is about one question: can a caller reach a project that
 * is not theirs? Project ids travel — in URLs, logs, support tickets — so
 * "the id is a UUID" is not an access control.
 */
final class ProjectTest extends TestCase
{
    /** @return array{User, Tenant} */
    private function actor(): array
    {
        $tenant = Tenant::factory()->create();

        return [User::factory()->inTenant($tenant)->create(), $tenant];
    }

    public function test_creating_a_project_puts_it_in_the_callers_tenant(): void
    {
        [$user, $tenant] = $this->actor();

        $id = $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/projects', ['name' => 'Blog', 'domain' => 'example.com'])
            ->assertStatus(201)
            ->assertJson(['name' => 'Blog', 'domain' => 'example.com'])
            ->json('id');

        $this->assertDatabaseHas('projects', ['id' => $id, 'tenant_id' => $tenant->id]);
    }

    public function test_a_tenant_id_in_the_body_is_ignored(): void
    {
        [$user, $tenant] = $this->actor();
        $other = Tenant::factory()->create();

        $id = $this->actingAs($user, 'sanctum')->postJson('/api/v1/projects', [
            'name' => 'Blog',
            'domain' => 'example.com',
            'tenant_id' => $other->id,
        ])->assertStatus(201)->json('id');

        $this->assertDatabaseHas('projects', ['id' => $id, 'tenant_id' => $tenant->id]);
    }

    public function test_creating_a_project_requires_auth(): void
    {
        $this->postJson('/api/v1/projects', ['name' => 'Blog', 'domain' => 'example.com'])
            ->assertUnauthorized();
    }

    public function test_a_duplicate_name_inside_one_tenant_is_rejected(): void
    {
        [$user] = $this->actor();
        $body = ['name' => 'Blog', 'domain' => 'example.com'];

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/projects', $body)->assertStatus(201);
        $this->actingAs($user, 'sanctum')->postJson('/api/v1/projects', $body)
            ->assertStatus(422)
            ->assertJsonValidationErrors('name');
    }

    public function test_two_tenants_may_both_have_a_project_called_blog(): void
    {
        [$first] = $this->actor();
        [$second] = $this->actor();
        $body = ['name' => 'Blog', 'domain' => 'example.com'];

        $this->actingAs($first, 'sanctum')->postJson('/api/v1/projects', $body)->assertStatus(201);
        $this->actingAs($second, 'sanctum')->postJson('/api/v1/projects', $body)->assertStatus(201);
    }

    // --------------------------------------------------------------- reading

    public function test_the_list_shows_only_the_callers_projects(): void
    {
        [$user, $tenant] = $this->actor();
        $mine = Project::factory()->for($tenant)->create(['name' => 'Mine']);
        Project::factory()->create(['name' => 'Theirs']);

        $names = $this->actingAs($user, 'sanctum')->getJson('/api/v1/projects')->assertOk()->json('*.name');

        $this->assertSame(['Mine'], $names);
        $this->assertNotNull($mine);
    }

    public function test_another_tenants_project_is_a_404_not_a_403(): void
    {
        /*
         * 403 would confirm the project exists and belongs to someone else,
         * which is itself a disclosure. 404 tells the caller only that they
         * cannot see it.
         */
        [$user] = $this->actor();
        $theirs = Project::factory()->create();

        $this->actingAs($user, 'sanctum')->getJson("/api/v1/projects/{$theirs->id}")->assertStatus(404);
    }

    public function test_a_caller_can_read_their_own_project(): void
    {
        [$user, $tenant] = $this->actor();
        $mine = Project::factory()->for($tenant)->create(['name' => 'Mine']);

        $this->actingAs($user, 'sanctum')->getJson("/api/v1/projects/{$mine->id}")
            ->assertOk()
            ->assertJson(['id' => $mine->id, 'name' => 'Mine']);
    }

    public function test_deleting_another_tenants_project_does_nothing(): void
    {
        [$user] = $this->actor();
        $theirs = Project::factory()->create();

        $this->actingAs($user, 'sanctum')->deleteJson("/api/v1/projects/{$theirs->id}")->assertStatus(404);

        $this->assertDatabaseHas('projects', ['id' => $theirs->id]);
    }

    public function test_deleting_your_own_project_works(): void
    {
        [$user, $tenant] = $this->actor();
        $mine = Project::factory()->for($tenant)->create();

        $this->actingAs($user, 'sanctum')->deleteJson("/api/v1/projects/{$mine->id}")->assertStatus(204);

        $this->assertDatabaseMissing('projects', ['id' => $mine->id]);
    }

    // ------------------------------------- project_id on the work endpoints

    public function test_a_crawl_may_name_a_project_the_caller_owns(): void
    {
        [$user, $tenant] = $this->actor();
        $mine = Project::factory()->for($tenant)->create();
        Http::fake(['*/v1/crawls' => Http::response(['crawl_id' => 'x'], 202)]);

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/crawls', [
            'start_url' => 'https://example.com',
            'project_id' => $mine->id,
        ])->assertStatus(202);

        Http::assertSent(fn ($request) => $request['project_id'] === $mine->id);
    }

    public function test_a_crawl_may_not_name_another_tenants_project(): void
    {
        /*
         * The hole this closes: tenant_id is safe because it never comes from
         * the request, but project_id does. Forwarding it unchecked let anyone
         * holding a project id file work against another tenant's project —
         * and the services' foreign key would have accepted it, because the
         * project genuinely exists.
         */
        [$user] = $this->actor();
        $theirs = Project::factory()->create();
        Http::fake();

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/crawls', [
            'start_url' => 'https://example.com',
            'project_id' => $theirs->id,
        ])->assertStatus(422)->assertJsonValidationErrors('project_id');

        Http::assertNothingSent();
    }

    public function test_research_enforces_the_same_rule(): void
    {
        [$user] = $this->actor();
        $theirs = Project::factory()->create();
        Http::fake();

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/research', [
            'seed' => 'کفش',
            'project_id' => $theirs->id,
        ])->assertStatus(422);

        Http::assertNothingSent();
    }

    public function test_a_malformed_project_id_is_a_422_not_a_500(): void
    {
        // A non-UUID would reach Postgres as a malformed literal.
        [$user] = $this->actor();
        Http::fake();

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/crawls', [
            'start_url' => 'https://example.com',
            'project_id' => 'not-a-uuid',
        ])->assertStatus(422);
    }

    public function test_omitting_the_project_is_allowed(): void
    {
        [$user] = $this->actor();
        Http::fake(['*/v1/crawls' => Http::response(['crawl_id' => 'x'], 202)]);

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/crawls', ['start_url' => 'https://example.com'])
            ->assertStatus(202);

        Http::assertSent(fn ($request) => $request['project_id'] === null);
    }
}
