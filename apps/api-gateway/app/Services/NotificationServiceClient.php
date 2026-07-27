<?php

declare(strict_types=1);

namespace App\Services;

/**
 * Client for the notifications service.
 *
 * Channels are configuration rather than jobs, so this is the only client here
 * with a delete.
 */
final class NotificationServiceClient extends ServiceClient
{
    public static function fromConfig(): self
    {
        return new self(
            rtrim((string) config('services.notifications.url', 'http://notifications-api:8000'), '/'),
            (int) config('services.notifications.timeout', 15),
            'notifications service',
        );
    }

    /**
     * @param  array<string, mixed>  $body
     * @return array<string, mixed>
     *
     * @throws RequestRejected|ServiceUnavailable
     */
    public function create(array $body): array
    {
        $response = $this->send(fn () => $this->request()->post('/v1/channels', $body));

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
    public function channels(?string $tenantId): array
    {
        $response = $this->send(
            fn () => $this->request()->get('/v1/channels', $this->scopedTo($tenantId))
        );

        return $this->usable($response)->json();
    }

    /** @throws ServiceUnavailable */
    public function delete(string $channelId, ?string $tenantId): bool
    {
        $response = $this->send(fn () => $this->request()->delete(
            "/v1/channels/{$channelId}", $this->scopedTo($tenantId)
        ));

        if ($response->status() === 404) {
            return false;
        }

        $this->usable($response);

        return true;
    }

    /**
     * @return array<string, mixed>|null  null when the channel id is unknown
     *
     * @throws ServiceUnavailable
     */
    public function test(string $channelId, ?string $tenantId): ?array
    {
        $response = $this->send(fn () => $this->request()->post(
            "/v1/channels/{$channelId}/test?".http_build_query($this->scopedTo($tenantId))
        ));

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
    public function deliveries(int $limit, ?string $tenantId): array
    {
        $response = $this->send(fn () => $this->request()->get(
            '/v1/deliveries', ['limit' => $limit] + $this->scopedTo($tenantId)
        ));

        return $this->usable($response)->json();
    }
}
