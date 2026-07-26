<?php

declare(strict_types=1);

namespace App\Services;

use Illuminate\Http\Client\ConnectionException;
use Illuminate\Support\Facades\Http;

/**
 * Client for the crawl service.
 *
 * The gateway owns auth, tenancy and rate limiting; the crawl service owns
 * crawling. Keeping the HTTP details in one class means a change to the
 * service's transport touches one file, and controllers stay unaware of it.
 *
 * Contract: shared/contracts/openapi/crawl.yaml
 */
final class CrawlServiceClient
{
    public function __construct(
        private readonly string $baseUrl,
        private readonly int $timeout = 10,
    ) {
    }

    public static function fromConfig(): self
    {
        return new self(
            rtrim((string) config('services.crawl.url', 'http://crawl-api:8000'), '/'),
            (int) config('services.crawl.timeout', 10),
        );
    }

    /**
     * Start a crawl. Returns immediately with an id — crawling is asynchronous.
     *
     * @param  array<string, mixed>  $options
     * @return array{crawl_id: string, status: string, result_url: string}
     *
     * @throws CrawlServiceUnavailable
     */
    public function start(string $startUrl, array $options = []): array
    {
        $response = $this->request()->post('/v1/crawls', array_merge($options, [
            'start_url' => $startUrl,
        ]));

        // A 400 here is the caller's bad URL (or a blocked internal target),
        // not an outage — surface the service's own message rather than
        // flattening it into a generic 502.
        if ($response->status() === 400) {
            throw new CrawlRequestRejected((string) $response->json('detail', 'invalid request'));
        }

        $response->throw();

        return $response->json();
    }

    /**
     * @return array<string, mixed>|null  null when the crawl id is unknown
     *
     * @throws CrawlServiceUnavailable
     */
    public function get(string $crawlId): ?array
    {
        $response = $this->request()->get("/v1/crawls/{$crawlId}");

        if ($response->status() === 404) {
            return null;
        }

        $response->throw();

        return $response->json();
    }

    /**
     * @return array<int, array<string, mixed>>
     *
     * @throws CrawlServiceUnavailable
     */
    public function recent(int $limit = 25): array
    {
        return $this->request()->get('/v1/crawls', ['limit' => $limit])->throw()->json();
    }

    private function request(): \Illuminate\Http\Client\PendingRequest
    {
        return Http::baseUrl($this->baseUrl)
            ->timeout($this->timeout)
            ->acceptJson()
            ->throw(function ($response, $e) {
                if ($e instanceof ConnectionException) {
                    throw new CrawlServiceUnavailable('crawl service unreachable', previous: $e);
                }
            });
    }
}
