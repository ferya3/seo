<?php

declare(strict_types=1);

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Models\Project;
use Illuminate\Database\UniqueConstraintViolationException;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Validation\ValidationException;

/**
 * Projects: the sites a tenant tracks.
 *
 * Every query goes through the ownedBy scope. Reading a project by id alone
 * would make a leaked id enough to reach another tenant's data.
 */
final class ProjectController extends Controller
{
    public function store(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'name' => ['required', 'string', 'max:120'],
            'domain' => ['required', 'string', 'max:253'],
        ]);

        $project = new Project($validated);
        $project->forceFill(['tenant_id' => $request->user()->tenant_id]);

        try {
            $project->save();
        } catch (UniqueConstraintViolationException) {
            // The index is per tenant, so this only fires on a genuine
            // duplicate inside the caller's own account.
            throw ValidationException::withMessages([
                'name' => 'You already have a project with that name.',
            ]);
        }

        return response()->json($this->present($project), 201);
    }

    public function index(Request $request): JsonResponse
    {
        $projects = Project::ownedBy($request->user()->tenant_id)
            ->orderByDesc('created_at')
            ->limit(100)
            ->get();

        return response()->json($projects->map(fn (Project $p) => $this->present($p)));
    }

    public function show(Request $request, string $projectId): JsonResponse
    {
        $project = Project::ownedBy($request->user()->tenant_id)->find($projectId);

        // 404, not 403. A 403 would confirm the project exists, which is
        // itself a leak — the caller learns another tenant owns that id.
        if ($project === null) {
            return response()->json(['error' => 'project not found'], 404);
        }

        return response()->json($this->present($project));
    }

    public function destroy(Request $request, string $projectId): JsonResponse
    {
        $project = Project::ownedBy($request->user()->tenant_id)->find($projectId);

        if ($project === null) {
            return response()->json(['error' => 'project not found'], 404);
        }

        $project->delete();

        return response()->json(null, 204);
    }

    /** @return array<string, mixed> */
    private function present(Project $project): array
    {
        return [
            'id' => $project->id,
            'name' => $project->name,
            'domain' => $project->domain,
            'created_at' => $project->created_at?->toIso8601String(),
        ];
    }
}
