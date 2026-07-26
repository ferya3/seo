<?php

declare(strict_types=1);

namespace Tests\Unit;

use App\Services\CrawlServiceClient;
use App\Services\RequestRejected;
use App\Services\ServiceUnavailable;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

/**
 * The transport layer's error translation, pinned directly.
 *
 * These exist because the first version of this client used
 * `PendingRequest::throw(fn ($response, $e) => ...)`, which looks like it maps
 * failures to domain exceptions and does not: it throws on every error
 * response, making the explicit status checks below it unreachable, and it
 * never fires at all for a ConnectionException. Every downstream failure came
 * out as a 500. `php -l` cannot see that; only running it can.
 */
final class ServiceClientTest extends TestCase
{
    private function client(): CrawlServiceClient
    {
        return new CrawlServiceClient('http://crawl-api:8000', 5, 'crawl service');
    }

    public function test_a_404_is_a_missing_record_not_an_error(): void
    {
        Http::fake(['*' => Http::response(['detail' => 'crawl not found'], 404)]);

        $this->assertNull($this->client()->get('missing-id'));
    }

    public function test_a_400_keeps_the_services_own_explanation(): void
    {
        Http::fake(['*' => Http::response(['detail' => 'target 10.0.0.1 is not allowed'], 400)]);

        $this->expectException(RequestRejected::class);
        $this->expectExceptionMessage('target 10.0.0.1 is not allowed');

        $this->client()->start('http://10.0.0.1/');
    }

    public function test_a_400_with_no_detail_still_reads_as_a_rejection(): void
    {
        Http::fake(['*' => Http::response([], 400)]);

        $this->expectException(RequestRejected::class);

        $this->client()->start('https://example.com');
    }

    public function test_a_connection_failure_becomes_service_unavailable(): void
    {
        Http::fake(fn () => throw new ConnectionException('connection refused'));

        $this->expectException(ServiceUnavailable::class);

        $this->client()->start('https://example.com');
    }

    public function test_a_5xx_becomes_service_unavailable_not_a_leaked_500(): void
    {
        // The gateway is healthy; its dependency is not. The caller should be
        // told to retry, not that they broke something.
        Http::fake(['*' => Http::response('upstream exploded', 500)]);

        $this->expectException(ServiceUnavailable::class);
        $this->expectExceptionMessage('crawl service returned 500');

        $this->client()->start('https://example.com');
    }

    public function test_a_read_timeout_is_also_service_unavailable(): void
    {
        Http::fake(fn () => throw new ConnectionException('timed out'));

        $this->expectException(ServiceUnavailable::class);

        $this->client()->get('any-id');
    }

    public function test_a_successful_start_returns_the_payload(): void
    {
        Http::fake(['*' => Http::response(['crawl_id' => 'abc', 'status' => 'queued'], 202)]);

        $this->assertSame('abc', $this->client()->start('https://example.com')['crawl_id']);
    }

    public function test_options_are_merged_but_the_url_wins(): void
    {
        // start_url is a named argument for a reason: a caller passing a
        // different one inside $options must not override the validated value.
        Http::fake(['*' => Http::response(['crawl_id' => 'abc'], 202)]);

        $this->client()->start('https://example.com', [
            'start_url' => 'https://evil.test',
            'max_pages' => 10,
        ]);

        Http::assertSent(fn ($request) => $request['start_url'] === 'https://example.com'
            && $request['max_pages'] === 10);
    }
}
