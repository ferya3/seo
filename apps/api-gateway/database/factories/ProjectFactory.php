<?php

declare(strict_types=1);

namespace Database\Factories;

use App\Models\Project;
use App\Models\Tenant;
use Illuminate\Database\Eloquent\Factories\Factory;

/**
 * @extends Factory<Project>
 */
class ProjectFactory extends Factory
{
    protected $model = Project::class;

    /** @return array<string, mixed> */
    public function definition(): array
    {
        return [
            // A project with no tenant cannot exist: the column is a foreign
            // key, and the tenancy scope keys off it.
            'tenant_id' => Tenant::factory(),
            'name' => fake()->unique()->words(2, true),
            'domain' => fake()->domainName(),
        ];
    }
}
