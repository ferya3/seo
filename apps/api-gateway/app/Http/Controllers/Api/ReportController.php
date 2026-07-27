<?php

declare(strict_types=1);

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Services\ReportingServiceClient;
use App\Services\RequestRejected;
use App\Services\ServiceUnavailable;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Http\Response;

/**
 * Reports: a finished workflow as a document someone can send.
 *
 * The only endpoint in this gateway that answers something other than JSON.
 */
final class ReportController extends Controller
{
    /** What the service renders. Anything else is refused here rather than
     *  forwarded, so a typo comes back as 422 and not as an empty download. */
    private const FORMATS = ['html', 'md'];

    public function __construct(private readonly ReportingServiceClient $reports)
    {
    }

    public function store(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'workflow_id' => ['required', 'string', 'max:64'],
        ]);

        $validated['tenant_id'] = $request->user()?->tenant_id;
        $validated['correlation_id'] = $request->header('X-Correlation-Id');

        try {
            $accepted = $this->reports->start($validated);
        } catch (RequestRejected $e) {
            return response()->json(['error' => $e->getMessage()], 422);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'reporting service unavailable'], 503);
        }

        return response()->json($accepted, 202);
    }

    public function show(Request $request, string $reportId): JsonResponse
    {
        try {
            $report = $this->reports->get($reportId, $request->user()?->tenant_id);
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'reporting service unavailable'], 503);
        }

        if ($report === null) {
            return response()->json(['error' => 'report not found'], 404);
        }

        return response()->json($report);
    }

    public function index(Request $request): JsonResponse
    {
        try {
            $reports = $this->reports->recent(
                (int) $request->query('limit', '25'), $request->user()?->tenant_id
            );
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'reporting service unavailable'], 503);
        }

        return response()->json($reports);
    }

    public function document(Request $request, string $reportId): Response|JsonResponse
    {
        $format = (string) $request->query('format', 'html');
        if (! in_array($format, self::FORMATS, true)) {
            return response()->json(['error' => 'unsupported format'], 422);
        }

        try {
            $document = $this->reports->document(
                $reportId, $format, $request->user()?->tenant_id
            );
        } catch (ServiceUnavailable) {
            return response()->json(['error' => 'reporting service unavailable'], 503);
        }

        if ($document === null) {
            return response()->json(['error' => 'report not found'], 404);
        }

        // Passed through as bytes with the service's own content type. Decoding
        // and re-encoding would lose both the type and the UTF-8 the document
        // is written in.
        return response(
            $document->body(),
            200,
            [
                'Content-Type' => $document->header('Content-Type') ?: 'text/plain; charset=utf-8',
                'Content-Disposition' => "attachment; filename=\"seo-report-{$reportId}.{$format}\"",
            ],
        );
    }
}
