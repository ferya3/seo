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
 * Schedules: run an audit on a cadence instead of when someone remembers.
 *
 * The inputs are the same ones a workflow takes, validated the same way, so a
 * schedule cannot be created that would be refused if it were started by hand.
 */
final class ScheduleController extends Controller
{
    use ResolvesProject;

    public function __construct(private readonly OrchestratorClient $orchestrator) {}

    public function store(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'goal' => ['sometimes', 'string', 'max:60'],
            'start_url' => ['required', 'string', 'max:2048'],
            'seed' => ['sometimes', 'nullable', 'string', 'max:200'],
            'max_pages' => ['sometimes', 'integer', 'min:1', 'max:100000'],
            'track_keywords' => ['sometimes', 'integer', 'min:1', 'max:50'],
            'optimize_pages' => ['sometimes', 'integer', 'min:1', 'max:25'],
            'competitors' => ['sometimes', 'array', 'max:4'],
            'competitors.*' => ['string', 'max:2048'],
            'competitor_pages' => ['sometimes', 'integer', 'min:1', 'max:200'],
            'lang' => ['sometimes', 'string', 'max:10'],
            'country' => ['sometimes', 'string', 'max:5'],
            'cadence' => ['required', 'string', 'in:daily,weekly,monthly'],
            'hour' => ['sometimes', 'integer', 'min:0', 'max:23'],
            'weekday' => ['sometimes', 'integer', 'min:0', 'max:6'],
            'day_of_month' => ['sometimes', 'integer', 'min:1', 'max:31'],
            'timezone' => ['sometimes', 'string', 'max:60'],
        ]);

        // The cadence fields describe when; everything else describes the
        // work, and the orchestrator's contract nests those under `inputs`.
        $when = ['goal', 'cadence', 'hour', 'weekday', 'day_of_month', 'timezone'];
        $body = [
            'goal' => $validated['goal'] ?? 'site_audit',
            'inputs' => array_diff_key($validated, array_flip($when)),
            'cadence' => $validated['cadence'],
            'tenant_id' => $request->user()?->tenant_id,
            'project_id' => $this->ownedProjectId($request),
        ];
        foreach (['hour', 'weekday', 'day_of_month', 'timezone'] as $field) {
            if (array_key_exists($field, $validated)) {
                $body[$field] = $validated[$field];
            }
        }

        try {
            $schedule = $this->orchestrator->schedule($body);
        } catch (RequestRejected $e) {
            return response()->json(['error' => $e->getMessage()], 422);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'orchestrator unavailable'], 503);
        }

        return response()->json($schedule, 201);
    }

    public function index(Request $request): JsonResponse
    {
        try {
            $schedules = $this->orchestrator->schedules($request->user()?->tenant_id);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'orchestrator unavailable'], 503);
        }

        return response()->json($schedules);
    }

    public function pause(Request $request, string $scheduleId): JsonResponse
    {
        $active = $request->boolean('active');

        try {
            $schedule = $this->orchestrator->pauseSchedule(
                $scheduleId, $active, $request->user()?->tenant_id
            );
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'orchestrator unavailable'], 503);
        }

        if ($schedule === null) {
            return response()->json(['error' => 'schedule not found'], 404);
        }

        return response()->json($schedule);
    }

    public function destroy(Request $request, string $scheduleId): JsonResponse
    {
        try {
            $deleted = $this->orchestrator->deleteSchedule(
                $scheduleId, $request->user()?->tenant_id
            );
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'orchestrator unavailable'], 503);
        }

        if (! $deleted) {
            return response()->json(['error' => 'schedule not found'], 404);
        }

        return response()->json(null, 204);
    }
}
