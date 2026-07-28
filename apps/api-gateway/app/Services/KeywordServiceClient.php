<?php

declare(strict_types=1);

namespace App\Services;

/**
 * Client for the keyword service.
 *
 * Endpoints only — transport and error translation live in ServiceClient.
 * A longer default timeout than the crawl client because starting research
 * touches several autocomplete sources before it returns an id.
 *
 * Contract: shared/contracts/openapi/keyword.yaml
 */
final class KeywordServiceClient extends ServiceClient
{
    public static function fromConfig(): self
    {
        return new self(
            rtrim((string) config('services.keyword.url', 'http://keyword-api:8000'), '/'),
            (int) config('services.keyword.timeout', 30),
            'keyword service',
        );
    }

    /**
     * Start research. Returns immediately with an id — research is asynchronous.
     *
     * @param  array<string, mixed>  $options
     * @return array{research_id: string, status: string, result_url: string}
     *
     * @throws RequestRejected|ServiceUnavailable
     */
    public function start(string $seed, array $options = []): array
    {
        $response = $this->send(fn () => $this->request()->post('/v1/research', array_merge($options, [
            'seed' => $seed,
        ])));

        // A 4xx here is the caller's bad input, not an outage.
        if ($response->status() === 400 || $response->status() === 422) {
            throw new RequestRejected($this->reason($response));
        }

        return $this->usable($response)->json();
    }

    /**
     * @return array<string, mixed>|null null when the research id is unknown
     *
     * @throws ServiceUnavailable
     */
    public function get(string $researchId, ?string $tenantId): ?array
    {
        $response = $this->send(
            fn () => $this->request()->get("/v1/research/{$researchId}", $this->scopedTo($tenantId))
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
            '/v1/research', ['limit' => $limit] + $this->scopedTo($tenantId)
        ));

        return $this->usable($response)->json();
    }
}
