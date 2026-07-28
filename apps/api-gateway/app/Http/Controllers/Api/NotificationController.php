<?php

declare(strict_types=1);

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Services\NotificationServiceClient;
use App\Services\RequestRejected;
use App\Services\ServiceUnavailable;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

/**
 * Notification channels: who gets told when a report is ready.
 *
 * The service does the SSRF check on a webhook target; this layer does not
 * repeat it, because a check in two places is a check that disagrees with
 * itself after the first change.
 */
final class NotificationController extends Controller
{
    use ResolvesProject;

    public function __construct(private readonly NotificationServiceClient $notifications) {}

    public function store(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'kind' => ['required', 'string', 'in:webhook,email'],
            'target' => ['required', 'string', 'max:2048'],
            'events' => ['sometimes', 'array', 'max:10'],
            'events.*' => ['string', 'max:60'],
        ]);

        $validated['tenant_id'] = $request->user()?->tenant_id;
        $validated['project_id'] = $this->ownedProjectId($request);

        try {
            $channel = $this->notifications->create($validated);
        } catch (RequestRejected $e) {
            return response()->json(['error' => $e->getMessage()], 422);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'notifications service unavailable'], 503);
        }

        // The signing secret is in this response and in no other, which is why
        // it is returned rather than fetched later.
        return response()->json($channel, 201);
    }

    public function index(Request $request): JsonResponse
    {
        try {
            $channels = $this->notifications->channels($request->user()?->tenant_id);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'notifications service unavailable'], 503);
        }

        return response()->json($channels);
    }

    public function destroy(Request $request, string $channelId): JsonResponse
    {
        try {
            $deleted = $this->notifications->delete($channelId, $request->user()?->tenant_id);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'notifications service unavailable'], 503);
        }

        if (! $deleted) {
            return response()->json(['error' => 'channel not found'], 404);
        }

        return response()->json(null, 204);
    }

    public function test(Request $request, string $channelId): JsonResponse
    {
        try {
            $result = $this->notifications->test($channelId, $request->user()?->tenant_id);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'notifications service unavailable'], 503);
        }

        if ($result === null) {
            return response()->json(['error' => 'channel not found'], 404);
        }

        return response()->json($result);
    }

    public function deliveries(Request $request): JsonResponse
    {
        try {
            $deliveries = $this->notifications->deliveries(
                (int) $request->query('limit', '50'), $request->user()?->tenant_id
            );
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'notifications service unavailable'], 503);
        }

        return response()->json($deliveries);
    }
}
