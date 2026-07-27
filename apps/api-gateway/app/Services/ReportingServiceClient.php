<?php

declare(strict_types=1);

namespace App\Services;

use Illuminate\Http\Client\Response;

/**
 * Client for the reporting service.
 *
 * One method more than the others: `document()` returns the raw Response
 * rather than a decoded array, because what comes back is a file. Decoding it
 * into JSON and re-encoding it at the controller would corrupt the encoding
 * and lose the content type, which is the whole point of the endpoint.
 */
final class ReportingServiceClient extends ServiceClient
{
    public static function fromConfig(): self
    {
        return new self(
            rtrim((string) config('services.reporting.url', 'http://reporting-api:8000'), '/'),
            (int) config('services.reporting.timeout', 20),
            'reporting service',
        );
    }

    /**
     * @param  array<string, mixed>  $body
     * @return array{report_id: string, status: string, result_url: string}
     *
     * @throws RequestRejected|ServiceUnavailable
     */
    public function start(array $body): array
    {
        $response = $this->send(fn () => $this->request()->post('/v1/reports', $body));

        if ($response->status() === 400 || $response->status() === 422) {
            throw new RequestRejected($this->reason($response));
        }

        return $this->usable($response)->json();
    }

    /**
     * @return array<string, mixed>|null  null when the report id is unknown
     *
     * @throws ServiceUnavailable
     */
    public function get(string $reportId, ?string $tenantId): ?array
    {
        $response = $this->send(
            fn () => $this->request()->get("/v1/reports/{$reportId}", $this->scopedTo($tenantId))
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
            '/v1/reports', ['limit' => $limit] + $this->scopedTo($tenantId)
        ));

        return $this->usable($response)->json();
    }

    /**
     * The rendered file, untouched.
     *
     * @throws ServiceUnavailable
     */
    public function document(string $reportId, string $format, ?string $tenantId): ?Response
    {
        $response = $this->send(fn () => $this->request()->get(
            "/v1/reports/{$reportId}/document",
            ['format' => $format] + $this->scopedTo($tenantId)
        ));

        if ($response->status() === 404) {
            return null;
        }

        return $this->usable($response);
    }
}
