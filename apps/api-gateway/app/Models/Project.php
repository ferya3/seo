<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Builder;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

/**
 * One site a tenant tracks. Crawls and research runs hang off it.
 */
class Project extends Model
{
    /** @use HasFactory<\Database\Factories\ProjectFactory> */
    use HasFactory, HasUuids;

    protected $keyType = 'string';

    public $incrementing = false;

    /** As with User: tenant_id is set from the authenticated principal only. */
    protected $fillable = ['name', 'domain'];

    /** @return BelongsTo<Tenant, $this> */
    public function tenant(): BelongsTo
    {
        return $this->belongsTo(Tenant::class);
    }

    /**
     * Every query for a project must go through here.
     *
     * Looking a project up by id alone is the classic tenancy hole: the ids are
     * UUIDs, but an id that leaks — from a shared report, a log line, a support
     * ticket — would otherwise be enough to read or write another tenant's
     * project. Scoping at the query is what makes that a 404 instead.
     *
     * @param  Builder<Project>  $query
     * @return Builder<Project>
     */
    public function scopeOwnedBy(Builder $query, ?string $tenantId): Builder
    {
        // A null tenant matches nothing rather than everything. Without this
        // guard an unauthenticated or tenant-less principal would see the
        // whole table.
        return $query->where('tenant_id', $tenantId ?? '00000000-0000-0000-0000-000000000000');
    }
}
