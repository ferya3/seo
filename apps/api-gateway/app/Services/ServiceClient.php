<?php

declare(strict_types=1);

namespace App\Services;

use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\PendingRequest;
use Illuminate\Http\Client\Response;
use Illuminate\Support\Facades\Http;

/**
 * Shared transport for the backend service clients.
 *
 * The gateway owns auth, tenancy and rate limiting; the services own the work.
 * Keeping HTTP details here means a change to how services are reached touches
 * one file, and controllers stay unaware of it.
 *
 * The error translation is the interesting part, and it is written the way it
 * is because of a bug that only running the app exposed. The obvious-looking
 * `Http::baseUrl(...)->throw(fn ($response, $e) => ...)` is wrong twice over:
 *
 *   1. `throw()` raises RequestException on *every* error response, so an
 *      explicit `if ($response->status() === 404)` after the call is dead code
 *      and a missing crawl surfaces as a 500.
 *   2. The callback never runs for a ConnectionException at all, because there
 *      is no response to hand it — so an unreachable service also became a 500.
 *
 * Every downstream failure mode collapsed into 500. Hence: no automatic
 * throwing, an explicit try/catch for transport failure, and each caller
 * deciding what its own status codes mean.
 */
abstract class ServiceClient
{
    /**
     * Stands in for a caller with no tenant. The same trick Project::ownedBy
     * uses: an id nothing can be owned by, so a broken principal reads an
     * empty list instead of everyone's.
     */
    private const NOBODY = '00000000-0000-0000-0000-000000000000';

    public function __construct(
        protected readonly string $baseUrl,
        protected readonly int $timeout,
        protected readonly string $name,
    ) {}

    protected function request(): PendingRequest
    {
        return Http::baseUrl($this->baseUrl)
            ->timeout($this->timeout)
            ->acceptJson();
    }

    /**
     * Run a call, translating a transport failure into ServiceUnavailable.
     *
     * @param  callable(): Response  $call
     *
     * @throws ServiceUnavailable
     */
    protected function send(callable $call): Response
    {
        try {
            return $call();
        } catch (ConnectionException $e) {
            throw new ServiceUnavailable("{$this->name} unreachable", previous: $e);
        }
    }

    /**
     * A 5xx from a service we own is an outage, not a client error: the caller
     * should see 503 and retry, not a 500 implying they broke something.
     *
     * An unexpected 4xx is our bug — we sent something the service rejected and
     * did not anticipate — so it stays an exception and surfaces as a 500.
     *
     * @throws ServiceUnavailable
     */
    protected function usable(Response $response): Response
    {
        if ($response->serverError()) {
            throw new ServiceUnavailable("{$this->name} returned {$response->status()}");
        }

        return $response->throw();
    }

    protected function reason(Response $response): string
    {
        return (string) $response->json('detail', 'invalid request');
    }

    /**
     * Query parameters that scope a read to one tenant.
     *
     * Every read method takes the tenant as a required argument rather than an
     * optional one, because the version where it was optional is the version
     * that shipped: writes were tenant-scoped, reads were not, and any id was
     * enough to read another account's report. A required parameter cannot be
     * forgotten the way an optional one was.
     *
     * @return array<string, string>
     */
    protected function scopedTo(?string $tenantId): array
    {
        return ['tenant_id' => $tenantId ?? self::NOBODY];
    }
}
