<?php

declare(strict_types=1);

namespace App\Services;

/**
 * Client for the crawl service.
 *
 * Endpoints only — transport and error translation live in ServiceClient.
 *
 * Contract: shared/contracts/openapi/crawl.yaml
 */
final class CrawlServiceClient extends ServiceClient
{
    public static function fromConfig(): self
    {
        return new self(
            rtrim((string) config('services.crawl.url', 'http://crawl-api:8000'), '/'),
            (int) config('services.crawl.timeout', 10),
            'crawl service',
        );
    }

    /**
     * Start a crawl. Returns immediately with an id — crawling is asynchronous.
     *
     * @param  array<string, mixed>  $options
     * @return array{crawl_id: string, status: string, result_url: string}
     *
     * @throws RequestRejected|ServiceUnavailable
     */
    public function start(string $startUrl, array $options = []): array
    {
        $response = $this->send(fn () => $this->request()->post('/v1/crawls', array_merge($options, [
            'start_url' => $startUrl,
        ])));

        // A 400 here is the caller's bad URL (or a blocked internal target),
        // not an outage — surface the service's own message rather than
        // flattening it into a generic 502.
        if ($response->status() === 400 || $response->status() === 422) {
            throw new RequestRejected($this->reason($response));
        }

        return $this->usable($response)->json();
    }

    /**
     * @return array<string, mixed>|null  null when the crawl id is unknown
     *
     * @throws ServiceUnavailable
     */
    public function get(string $crawlId, ?string $tenantId): ?array
    {
        $response = $this->send(
            fn () => $this->request()->get("/v1/crawls/{$crawlId}", $this->scopedTo($tenantId))
        );

        if ($response->status() === 404) {
            return null;
        }

        return $this->usable($response)->json();
    }

    /**
     * @return array<int, array<string, mixed>>
     *
     * @throws ServiceUnavailable
     */
    public function recent(int $limit, ?string $tenantId): array
    {
        $response = $this->send(fn () => $this->request()->get(
            '/v1/crawls', ['limit' => $limit] + $this->scopedTo($tenantId)
        ));

        return $this->usable($response)->json();
    }
}
