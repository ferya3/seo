<?php

declare(strict_types=1);

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Services\RequestRejected;
use App\Services\ServiceUnavailable;
use App\Services\KeywordServiceClient;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

/**
 * Keyword research endpoints. Thin, for the same reason CrawlController is:
 * logic here would be logic the bus-driven path never runs.
 */
final class KeywordController extends Controller
{
    use ResolvesProject;

    public function __construct(private readonly KeywordServiceClient $keywords)
    {
    }

    public function store(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'seed' => ['required', 'string', 'min:1', 'max:200'],
            'lang' => ['sometimes', 'string', 'max:10'],
            'country' => ['sometimes', 'string', 'max:5'],
            'max_keywords' => ['sometimes', 'integer', 'min:10', 'max:5000'],
            'include_questions' => ['sometimes', 'boolean'],
            'include_alphabet' => ['sometimes', 'boolean'],
            'include_comparisons' => ['sometimes', 'boolean'],
            'sources' => ['sometimes', 'array'],
            'sources.*' => ['in:google,youtube,bing,duckduckgo'],
        ]);

        // Tenancy comes from the authenticated principal, never the body.
        $validated['tenant_id'] = $request->user()?->tenant_id;
        $validated['project_id'] = $this->ownedProjectId($request);
        $validated['correlation_id'] = $request->header('X-Correlation-Id');

        try {
            $accepted = $this->keywords->start($validated['seed'], $validated);
        } catch (RequestRejected $e) {
            return response()->json(['error' => $e->getMessage()], 422);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'keyword service unavailable'], 503);
        }

        return response()->json($accepted, 202);
    }

    public function show(string $researchId): JsonResponse
    {
        try {
            $research = $this->keywords->get($researchId);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'keyword service unavailable'], 503);
        }

        if ($research === null) {
            return response()->json(['error' => 'research not found'], 404);
        }

        return response()->json($research);
    }

    public function index(Request $request): JsonResponse
    {
        try {
            $research = $this->keywords->recent((int) $request->query('limit', '25'));
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'keyword service unavailable'], 503);
        }

        return response()->json($research);
    }
}
