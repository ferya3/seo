<?php

declare(strict_types=1);

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Services\RequestRejected;
use App\Services\CrawlServiceClient;
use App\Services\ServiceUnavailable;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

/**
 * Public crawl endpoints.
 *
 * The gateway validates and authorises; the crawl service does the work.
 * Deliberately thin — business logic here would be logic the bus-driven path
 * never runs, and the two entry points would drift.
 */
final class CrawlController extends Controller
{
    use ResolvesProject;

    public function __construct(private readonly CrawlServiceClient $crawls)
    {
    }

    public function store(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'start_url' => ['required', 'string', 'max:2048'],
            'max_pages' => ['sometimes', 'integer', 'min:1', 'max:100000'],
            'max_depth' => ['sometimes', 'integer', 'min:1', 'max:20'],
            'user_agent' => ['sometimes', 'in:mobile,desktop,googlebot'],
            'respect_robots' => ['sometimes', 'boolean'],
            'check_external_links' => ['sometimes', 'boolean'],
            'follow_subdomains' => ['sometimes', 'boolean'],
            'target_keywords' => ['sometimes', 'array', 'max:50'],
            'target_keywords.*' => ['string', 'max:200'],
        ]);

        // Tenancy comes from the authenticated principal, never from the body —
        // trusting a client-supplied tenant_id would let any caller read or
        // write another tenant's data.
        $validated['tenant_id'] = $request->user()?->tenant_id;
        $validated['project_id'] = $this->ownedProjectId($request);
        $validated['correlation_id'] = $request->header('X-Correlation-Id');

        try {
            $accepted = $this->crawls->start($validated['start_url'], $validated);
        } catch (RequestRejected $e) {
            return response()->json(['error' => $e->getMessage()], 422);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'crawl service unavailable'], 503);
        }

        return response()->json($accepted, 202);
    }

    public function show(Request $request, string $crawlId): JsonResponse
    {
        try {
            // Scoped to the caller's tenant, so another account's id answers
            // 404 rather than handing over their report.
            $crawl = $this->crawls->get($crawlId, $request->user()?->tenant_id);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'crawl service unavailable'], 503);
        }

        if ($crawl === null) {
            return response()->json(['error' => 'crawl not found'], 404);
        }

        return response()->json($crawl);
    }

    public function index(Request $request): JsonResponse
    {
        try {
            $crawls = $this->crawls->recent((int) $request->query('limit', '25'), $request->user()?->tenant_id);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'crawl service unavailable'], 503);
        }

        return response()->json($crawls);
    }
}
