<?php

declare(strict_types=1);

namespace App\Services;

/**
 * Client for the competitor service.
 *
 * The service takes crawl ids, not domains, and this layer does not paper over
 * that: a competitor has to be crawled before it can be compared, and pretending
 * otherwise here would mean the gateway starting crawls behind the caller's
 * back and charging them for work they did not ask for.
 */
final class CompetitorServiceClient extends ServiceClient
{
    public static function fromConfig(): self
    {
        return new self(
            rtrim((string) config('services.competitor.url', 'http://competitor-api:8000'), '/'),
            (int) config('services.competitor.timeout', 30),
            'competitor service',
        );
    }

    /**
     * @param  array<string, mixed>  $body
     * @return array{comparison_id: string, status: string, result_url: string}
     *
     * @throws RequestRejected|ServiceUnavailable
     */
    public function start(array $body): array
    {
        $response = $this->send(fn () => $this->request()->post('/v1/comparisons', $body));

        if ($response->status() === 400 || $response->status() === 422) {
            throw new RequestRejected($this->reason($response));
        }

        return $this->usable($response)->json();
    }

    /**
     * @return array<string, mixed>|null null when the comparison id is unknown
     *
     * @throws ServiceUnavailable
     */
    public function get(string $comparisonId, ?string $tenantId): ?array
    {
        $response = $this->send(
            fn () => $this->request()->get("/v1/comparisons/{$comparisonId}", $this->scopedTo($tenantId))
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
            '/v1/comparisons', ['limit' => $limit] + $this->scopedTo($tenantId)
        ));

        return $this->usable($response)->json();
    }
}
