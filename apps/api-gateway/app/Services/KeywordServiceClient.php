<?php

declare(strict_types=1);

namespace App\Services;

use Illuminate\Http\Client\ConnectionException;
use Illuminate\Support\Facades\Http;

/**
 * Client for the keyword service.
 *
 * The gateway owns auth, tenancy and rate limiting; the keyword service owns
 * research. Keeping the HTTP details in one class means a change to the
 * service's transport touches one file, and controllers stay unaware of it.
 *
 * Contract: shared/contracts/openapi/keyword.yaml
 */
final class KeywordServiceClient
{
    public function __construct(
        private readonly string $baseUrl,
        private readonly int $timeout = 30,
    ) {
    }

    public static function fromConfig(): self
    {
        return new self(
            rtrim((string) config('services.keyword.url', 'http://keyword-api:8000'), '/'),
            (int) config('services.keyword.timeout', 30),
        );
    }

    /**
     * Start research. Returns immediately with an id — research is asynchronous.
     *
     * @param  array<string, mixed>  $options
     * @return array{research_id: string, status: string, result_url: string}
     *
     * @throws CrawlServiceUnavailable
     */
    public function start(string $seed, array $options = []): array
    {
        $response = $this->request()->post('/v1/research', array_merge($options, [
            'seed' => $seed,
        ]));

        // A 4xx here is the caller's bad input, not an outage — surface the
        // service's own message rather than flattening it into a generic 502.
        if ($response->status() === 400 || $response->status() === 422) {
            throw new CrawlRequestRejected((string) $response->json('detail', 'invalid request'));
        }

        $response->throw();

        return $response->json();
    }

    /**
     * @return array<string, mixed>|null  null when the research id is unknown
     *
     * @throws CrawlServiceUnavailable
     */
    public function get(string $researchId): ?array
    {
        $response = $this->request()->get("/v1/research/{$researchId}");

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
        return $this->request()->get('/v1/research', ['limit' => $limit])->throw()->json();
    }

    private function request(): \Illuminate\Http\Client\PendingRequest
    {
        return Http::baseUrl($this->baseUrl)
            ->timeout($this->timeout)
            ->acceptJson()
            ->throw(function ($response, $e) {
                if ($e instanceof ConnectionException) {
                    throw new CrawlServiceUnavailable('keyword service unreachable', previous: $e);
                }
            });
    }
}
