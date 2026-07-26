<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;

/**
 * A customer account. The row the services' foreign keys point at.
 *
 * This existing is the whole reason for this layer: crawls and research rows
 * carry a tenant_id constrained to this table, so until a tenant is created
 * here, no work can be stored for it.
 */
class Tenant extends Model
{
    /** @use HasFactory<\Database\Factories\TenantFactory> */
    use HasFactory, HasUuids;

    protected $keyType = 'string';

    public $incrementing = false;

    // The schema has created_at but no updated_at: a tenant row is a name and
    // an identity, and nothing about it is edited in place yet.
    public $timestamps = false;

    protected $fillable = ['name'];

    /** @return HasMany<User, $this> */
    public function users(): HasMany
    {
        return $this->hasMany(User::class);
    }

    /** @return HasMany<Project, $this> */
    public function projects(): HasMany
    {
        return $this->hasMany(Project::class);
    }
}
