<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Models\Tenant;
use App\Models\User;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

/**
 * The report endpoints, with the reporting service faked.
 *
 * The document route is the only one in this gateway that answers something
 * other than JSON, so most of what is worth testing here is about bytes:
 * that they arrive unaltered, with the right type, and only for their owner.
 */
final class ReportApiTest extends TestCase
{
    private const ACCEPTED = [
        'report_id' => 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        'status' => 'queued',
        'result_url' => '/v1/reports/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    ];

    /** @return array{User, Tenant} */
    private function actor(): array
    {
        $tenant = Tenant::factory()->create();

        return [User::factory()->inTenant($tenant)->create(), $tenant];
    }

    public function test_requesting_a_report_requires_auth(): void
    {
        Http::fake();

        $this->postJson('/api/v1/reports', ['workflow_id' => 'w-1'])->assertUnauthorized();
        Http::assertNothingSent();
    }

    public function test_a_report_is_requested_for_the_callers_tenant(): void
    {
        [$user, $tenant] = $this->actor();
        Http::fake(['*/v1/reports' => Http::response(self::ACCEPTED, 202)]);

        $this->actingAs($user, 'sanctum')
            ->postJson('/api/v1/reports', ['workflow_id' => 'w-1'])
            ->assertStatus(202)
            ->assertJson(self::ACCEPTED);

        Http::assertSent(fn ($request) => $request['tenant_id'] === $tenant->id
            && $request['workflow_id'] === 'w-1');
    }

    public function test_a_request_without_a_workflow_is_refused_before_it_is_forwarded(): void
    {
        [$user] = $this->actor();
        Http::fake();

        $this->actingAs($user, 'sanctum')->postJson('/api/v1/reports', [])->assertStatus(422);
        Http::assertNothingSent();
    }

    public function test_the_document_arrives_as_bytes_with_its_own_type(): void
    {
        [$user] = $this->actor();
        $html = "<!doctype html>\n<html lang='fa' dir='rtl'><body>گزارش</body></html>";
        Http::fake(['*' => Http::response($html, 200, ['Content-Type' => 'text/html; charset=utf-8'])]);

        $response = $this->actingAs($user, 'sanctum')->get('/api/v1/reports/r-1/document');

        $response->assertOk();
        $this->assertSame($html, $response->getContent());
        $this->assertStringContainsString('text/html', $response->headers->get('Content-Type'));
        $this->assertStringContainsString('.html', $response->headers->get('Content-Disposition'));
    }

    public function test_persian_text_survives_the_round_trip(): void
    {
        // Decoding and re-encoding the body is how a report full of Persian
        // comes out as mojibake.
        [$user] = $this->actor();
        Http::fake(['*' => Http::response('# گزارش سئو — سایت من', 200,
            ['Content-Type' => 'text/markdown; charset=utf-8'])]);

        $response = $this->actingAs($user, 'sanctum')
            ->get('/api/v1/reports/r-1/document?format=md');

        $this->assertSame('# گزارش سئو — سایت من', $response->getContent());
    }

    public function test_the_document_is_scoped_to_the_callers_tenant(): void
    {
        [$user, $tenant] = $this->actor();
        Http::fake(['*' => Http::response('x', 200)]);

        $this->actingAs($user, 'sanctum')->get('/api/v1/reports/r-1/document')->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), 'tenant_id='.$tenant->id));
    }

    public function test_an_unsupported_format_is_refused_rather_than_forwarded(): void
    {
        [$user] = $this->actor();
        Http::fake();

        $this->actingAs($user, 'sanctum')
            ->getJson('/api/v1/reports/r-1/document?format=exe')
            ->assertStatus(422);

        Http::assertNothingSent();
    }

    public function test_another_tenants_report_is_not_found(): void
    {
        [$user] = $this->actor();
        Http::fake(['*' => Http::response(['detail' => 'report not found'], 404)]);

        $this->actingAs($user, 'sanctum')
            ->getJson('/api/v1/reports/somebody-elses-id')
            ->assertNotFound();
    }

    public function test_an_unreachable_reporting_service_is_503(): void
    {
        [$user] = $this->actor();
        Http::fake(fn () => throw new \Illuminate\Http\Client\ConnectionException('refused'));

        $this->actingAs($user, 'sanctum')
            ->getJson('/api/v1/reports')
            ->assertStatus(503);
    }
}
