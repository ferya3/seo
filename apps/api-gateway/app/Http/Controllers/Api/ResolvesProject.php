<?php

declare(strict_types=1);

namespace App\Http\Controllers\Api;

use App\Models\Project;
use Illuminate\Http\Request;
use Illuminate\Validation\ValidationException;

/**
 * Turns a caller-supplied project_id into one we have confirmed they own.
 *
 * tenant_id is safe because it never comes from the request. project_id does,
 * and forwarding it unchecked was a hole: the services store it and the
 * foreign key only proves the project exists somewhere, not that it belongs to
 * the caller. Anyone with a project id could have filed crawls against another
 * tenant's project.
 */
trait ResolvesProject
{
    private function ownedProjectId(Request $request): ?string
    {
        $projectId = $request->input('project_id');

        // Optional: work not tied to a project is allowed, it just does not
        // show up under one.
        if ($projectId === null || $projectId === '') {
            return null;
        }

        // Validate the shape first — a non-UUID would reach Postgres as a
        // malformed literal and come back as a 500 rather than a 422.
        $request->validate(['project_id' => ['uuid']]);

        if (! Project::ownedBy($request->user()?->tenant_id)->whereKey($projectId)->exists()) {
            // Same message whether it is missing or someone else's, for the
            // same reason ProjectController::show answers 404 rather than 403.
            throw ValidationException::withMessages(['project_id' => 'Unknown project.']);
        }

        return (string) $projectId;
    }
}
