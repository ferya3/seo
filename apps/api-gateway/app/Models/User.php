<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Foundation\Auth\User as Authenticatable;
use Illuminate\Notifications\Notifiable;
use Laravel\Sanctum\HasApiTokens;

/**
 * A user in the shared services database.
 *
 * Mapped onto the schema in infra/db/migrations, not the other way round: the
 * table is shared with the Python services, which key their tenancy off it, so
 * the column names are the schema's and this model adapts. Hence
 * `password_hash` rather than Laravel's conventional `password`, and a UUID
 * primary key rather than an auto-increment.
 */
class User extends Authenticatable
{
    /** @use HasFactory<\Database\Factories\UserFactory> */
    use HasApiTokens, HasFactory, HasUuids, Notifiable;

    protected $keyType = 'string';

    public $incrementing = false;

    /**
     * tenant_id is deliberately not fillable. It is the multi-tenancy
     * boundary, and a mass-assignable tenant is one crafted request away from
     * a caller placing themselves inside someone else's data.
     */
    protected $fillable = ['name', 'email'];

    protected $hidden = ['password_hash', 'remember_token'];

    protected function casts(): array
    {
        return [
            'email_verified_at' => 'datetime',
            'password_hash' => 'hashed',
        ];
    }

    /**
     * Laravel authenticates against `password` by convention; the shared
     * schema calls the column `password_hash`. Overriding here is what lets
     * both be true at once.
     */
    public function getAuthPassword(): string
    {
        return (string) $this->password_hash;
    }

    /** @return BelongsTo<Tenant, $this> */
    public function tenant(): BelongsTo
    {
        return $this->belongsTo(Tenant::class);
    }
}
