<?php

declare(strict_types=1);

namespace App\Services;

/**
 * Client for the orchestrator.
 *
 * Endpoints only — transport and error translation live in ServiceClient.
 * A workflow is the multi-step version of what CrawlServiceClient and
 * KeywordServiceClient each do once.
 */
final class OrchestratorClient extends ServiceClient
{
    public static function fromConfig(): self
    {
        return new self(
            rtrim((string) config('services.orchestrator.url', 'http://orchestrator:8000'), '/'),
            (int) config('services.orchestrator.timeout', 15),
            'orchestrator',
        );
    }

    /**
     * @param  array<string, mixed>  $body
     * @return array{workflow_id: string, status: string, result_url: string}
     *
     * @throws RequestRejected|ServiceUnavailable
     */
    public function start(array $body): array
    {
        $response = $this->send(fn () => $this->request()->post('/v1/workflows', $body));

        if ($response->status() === 400 || $response->status() === 422) {
            throw new RequestRejected($this->reason($response));
        }

        return $this->usable($response)->json();
    }

    /**
     * @return array<string, mixed>|null  null when the workflow id is unknown
     *
     * @throws ServiceUnavailable
     */
    public function get(string $workflowId, ?string $tenantId): ?array
    {
        $response = $this->send(
            fn () => $this->request()->get("/v1/workflows/{$workflowId}", $this->scopedTo($tenantId))
        );

        if ($response->status() === 404) {
            return null;
        }

        return $this->usable($response)->json();
    }

    /**
     * @param  array<string, mixed>  $body
     * @return array<string, mixed>
     *
     * @throws RequestRejected|ServiceUnavailable
     */
    public function schedule(array $body): array
    {
        $response = $this->send(fn () => $this->request()->post('/v1/schedules', $body));

        if ($response->status() === 400 || $response->status() === 422) {
            throw new RequestRejected($this->reason($response));
        }

        return $this->usable($response)->json();
    }

    /**
     * @return array<int, array<string, mixed>>
     *
     * @throws ServiceUnavailable
     */
    public function schedules(?string $tenantId): array
    {
        $response = $this->send(
            fn () => $this->request()->get('/v1/schedules', $this->scopedTo($tenantId))
        );

        return $this->usable($response)->json();
    }

    /**
     * @return array<string, mixed>|null  null when the schedule id is unknown
     *
     * @throws ServiceUnavailable
     */
    public function pauseSchedule(string $scheduleId, bool $active, ?string $tenantId): ?array
    {
        $response = $this->send(fn () => $this->request()->post(
            "/v1/schedules/{$scheduleId}/pause?".http_build_query(
                ['active' => $active ? 'true' : 'false'] + $this->scopedTo($tenantId)
            )
        ));

        if ($response->status() === 404) {
            return null;
        }

        return $this->usable($response)->json();
    }

    /** @throws ServiceUnavailable */
    public function deleteSchedule(string $scheduleId, ?string $tenantId): bool
    {
        $response = $this->send(fn () => $this->request()->delete(
            "/v1/schedules/{$scheduleId}", $this->scopedTo($tenantId)
        ));

        if ($response->status() === 404) {
            return false;
        }

        $this->usable($response);

        return true;
    }

    /**
     * @return array<int, array<string, mixed>>
     *
     * @throws ServiceUnavailable
     */
    public function recent(int $limit, ?string $tenantId): array
    {
        $response = $this->send(fn () => $this->request()->get(
            '/v1/workflows', ['limit' => $limit] + $this->scopedTo($tenantId)
        ));

        return $this->usable($response)->json();
    }
}
