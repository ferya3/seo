<?php

declare(strict_types=1);

namespace App\Services;

/**
 * Client for the SERP service.
 *
 * Endpoints only — transport and error translation live in ServiceClient.
 */
final class SerpServiceClient extends ServiceClient
{
    public static function fromConfig(): self
    {
        return new self(
            rtrim((string) config('services.serp.url', 'http://serp-api:8000'), '/'),
            (int) config('services.serp.timeout', 15),
            'serp service',
        );
    }

    /**
     * @param  array<string, mixed>  $body
     * @return array{check_id: string, status: string, result_url: string}
     *
     * @throws RequestRejected|ServiceUnavailable
     */
    public function start(array $body): array
    {
        $response = $this->send(fn () => $this->request()->post('/v1/checks', $body));

        if ($response->status() === 400 || $response->status() === 422) {
            throw new RequestRejected($this->reason($response));
        }

        return $this->usable($response)->json();
    }

    /**
     * @return array<string, mixed>|null null when the check id is unknown
     *
     * @throws ServiceUnavailable
     */
    public function get(string $checkId, ?string $tenantId): ?array
    {
        $response = $this->send(
            fn () => $this->request()->get("/v1/checks/{$checkId}", $this->scopedTo($tenantId))
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
            '/v1/checks', ['limit' => $limit] + $this->scopedTo($tenantId)
        ));

        return $this->usable($response)->json();
    }
}
