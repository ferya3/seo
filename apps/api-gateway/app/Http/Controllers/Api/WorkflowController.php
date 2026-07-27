<?php

declare(strict_types=1);

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Services\OrchestratorClient;
use App\Services\RequestRejected;
use App\Services\ServiceUnavailable;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

/**
 * Workflows: multi-step work driven by the orchestrator.
 *
 * From the caller's side this is one request that produces a crawl and a
 * keyword study; the sequencing, and the waiting, happen on the event bus.
 */
final class WorkflowController extends Controller
{
    use ResolvesProject;

    public function __construct(private readonly OrchestratorClient $orchestrator)
    {
    }

    public function store(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'goal' => ['sometimes', 'string', 'max:60'],
            'start_url' => ['required', 'string', 'max:2048'],
            'seed' => ['sometimes', 'nullable', 'string', 'max:200'],
            'max_pages' => ['sometimes', 'integer', 'min:1', 'max:100000'],
            'max_depth' => ['sometimes', 'integer', 'min:1', 'max:20'],
            // Each tracked keyword becomes a live search request downstream.
            'track_keywords' => ['sometimes', 'integer', 'min:1', 'max:50'],
            'lang' => ['sometimes', 'string', 'max:10'],
            'country' => ['sometimes', 'string', 'max:5'],
        ]);

        $body = [
            'goal' => $validated['goal'] ?? 'site_audit',
            // Inputs are nested because the orchestrator's contract nests them:
            // the goal decides what inputs mean, so they travel together.
            'inputs' => array_diff_key($validated, ['goal' => true]),
            // Tenancy from the principal, project checked against it.
            'tenant_id' => $request->user()?->tenant_id,
            'project_id' => $this->ownedProjectId($request),
        ];

        try {
            $accepted = $this->orchestrator->start($body);
        } catch (RequestRejected $e) {
            return response()->json(['error' => $e->getMessage()], 422);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'orchestrator unavailable'], 503);
        }

        return response()->json($accepted, 202);
    }

    public function show(string $workflowId): JsonResponse
    {
        try {
            $workflow = $this->orchestrator->get($workflowId);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'orchestrator unavailable'], 503);
        }

        if ($workflow === null) {
            return response()->json(['error' => 'workflow not found'], 404);
        }

        return response()->json($workflow);
    }

    public function index(Request $request): JsonResponse
    {
        try {
            $workflows = $this->orchestrator->recent((int) $request->query('limit', '25'));
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'orchestrator unavailable'], 503);
        }

        return response()->json($workflows);
    }
}
