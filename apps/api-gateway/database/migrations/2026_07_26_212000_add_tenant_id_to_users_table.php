<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * The controllers take tenancy from `$request->user()->tenant_id` and forward
 * it to the services, which store it on every job and copy it onto every
 * event. Without this column that expression is silently null and the whole
 * multi-tenancy boundary evaluates to "no tenant" without any error.
 *
 * Nullable rather than required: the single-tenant install has no tenants
 * table at all, and a NOT NULL here would make the gateway unusable there.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('users', function (Blueprint $table) {
            $table->uuid('tenant_id')->nullable()->after('id');
            $table->index('tenant_id');
        });
    }

    public function down(): void
    {
        Schema::table('users', function (Blueprint $table) {
            $table->dropIndex(['tenant_id']);
            $table->dropColumn('tenant_id');
        });
    }
};
