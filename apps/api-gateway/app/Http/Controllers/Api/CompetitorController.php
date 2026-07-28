<?php

declare(strict_types=1);

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Services\CompetitorServiceClient;
use App\Services\RequestRejected;
use App\Services\ServiceUnavailable;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

/**
 * Comparisons: your site against the sites that outrank you.
 *
 * Takes crawl ids because the service does. Crawling a competitor is a request
 * someone makes deliberately — it is minutes of that site's bandwidth — so it
 * stays a separate call to `POST /v1/crawls` rather than something this
 * endpoint does on the caller's behalf.
 */
final class CompetitorController extends Controller
{
    use ResolvesProject;

    /** Matches the service's own ceiling, so an over-long list is refused here
     *  with a message rather than at the next hop with a 422 that has to be
     *  translated. */
    private const MAX_COMPETITORS = 8;

    public function __construct(private readonly CompetitorServiceClient $competitors)
    {
    }

    public function store(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'crawl_id' => ['required', 'string', 'max:64'],
            'competitor_crawl_ids' => ['required', 'array', 'min:1', 'max:'.self::MAX_COMPETITORS],
            'competitor_crawl_ids.*' => ['string', 'max:64'],
        ]);

        $validated['tenant_id'] = $request->user()?->tenant_id;
        $validated['project_id'] = $this->ownedProjectId($request);
        $validated['correlation_id'] = $request->header('X-Correlation-Id');

        try {
            $accepted = $this->competitors->start($validated);
        } catch (RequestRejected $e) {
            return response()->json(['error' => $e->getMessage()], 422);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'competitor service unavailable'], 503);
        }

        return response()->json($accepted, 202);
    }

    public function show(Request $request, string $comparisonId): JsonResponse
    {
        try {
            $comparison = $this->competitors->get($comparisonId, $request->user()?->tenant_id);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'competitor service unavailable'], 503);
        }

        if ($comparison === null) {
            return response()->json(['error' => 'comparison not found'], 404);
        }

        return response()->json($comparison);
    }

    public function index(Request $request): JsonResponse
    {
        try {
            $comparisons = $this->competitors->recent(
                (int) $request->query('limit', '25'), $request->user()?->tenant_id
            );
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'competitor service unavailable'], 503);
        }

        return response()->json($comparisons);
    }
}
