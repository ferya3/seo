<?php

declare(strict_types=1);

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Services\RequestRejected;
use App\Services\SerpServiceClient;
use App\Services\ServiceUnavailable;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

/**
 * Rank checks: where a domain sits in search results for a set of keywords.
 */
final class SerpController extends Controller
{
    use ResolvesProject;

    public function __construct(private readonly SerpServiceClient $serp)
    {
    }

    public function store(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'target_domain' => ['required', 'string', 'max:253'],
            // Capped here as well as in the service: every keyword is a live
            // search request, so an unbounded list is a way to get the whole
            // platform's IP blocked.
            'keywords' => ['required', 'array', 'min:1', 'max:50'],
            'keywords.*' => ['string', 'min:1', 'max:200'],
            'provider' => ['sometimes', 'string', 'max:40'],
            'lang' => ['sometimes', 'string', 'max:10'],
            'country' => ['sometimes', 'string', 'max:5'],
        ]);

        $validated['tenant_id'] = $request->user()?->tenant_id;
        $validated['project_id'] = $this->ownedProjectId($request);
        $validated['correlation_id'] = $request->header('X-Correlation-Id');

        try {
            $accepted = $this->serp->start($validated);
        } catch (RequestRejected $e) {
            return response()->json(['error' => $e->getMessage()], 422);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'serp service unavailable'], 503);
        }

        return response()->json($accepted, 202);
    }

    public function show(Request $request, string $checkId): JsonResponse
    {
        try {
            // Scoped to the caller's tenant, so another account's id answers
            // 404 rather than handing over their report.
            $check = $this->serp->get($checkId, $request->user()?->tenant_id);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'serp service unavailable'], 503);
        }

        if ($check === null) {
            return response()->json(['error' => 'check not found'], 404);
        }

        return response()->json($check);
    }

    public function index(Request $request): JsonResponse
    {
        try {
            $checks = $this->serp->recent((int) $request->query('limit', '25'), $request->user()?->tenant_id);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'serp service unavailable'], 503);
        }

        return response()->json($checks);
    }
}
