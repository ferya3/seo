<?php

declare(strict_types=1);

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Models\Tenant;
use App\Models\User;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;
use Illuminate\Validation\ValidationException;

/**
 * Registration, login, logout.
 *
 * This is the layer that was missing: the services enforce tenancy with a
 * foreign key, and until something creates the tenant row, every authenticated
 * request fails on it. Registering creates the tenant and its first user in
 * one transaction.
 */
final class AuthController extends Controller
{
    public function register(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'name' => ['required', 'string', 'max:120'],
            'email' => ['required', 'email', 'max:255'],
            'password' => ['required', 'string', 'min:12', 'max:255'],
            'tenant_name' => ['required', 'string', 'max:120'],
        ]);

        // Checked before inserting so the caller gets a validation error rather
        // than a unique-violation 500. The unique index is still the authority
        // — this check races, that one cannot.
        if (User::where('email', $validated['email'])->exists()) {
            throw ValidationException::withMessages(['email' => 'That email is already registered.']);
        }

        // One transaction: a user with no tenant cannot authorise anything, and
        // a tenant with no user is unreachable. Half of this is worse than none.
        $user = DB::transaction(function () use ($validated) {
            $tenant = Tenant::create(['name' => $validated['tenant_name']]);

            $user = new User(['name' => $validated['name'], 'email' => $validated['email']]);
            // forceFill, because tenant_id is intentionally not fillable.
            $user->forceFill([
                'tenant_id' => $tenant->id,
                'password_hash' => Hash::make($validated['password']),
            ])->save();

            return $user;
        });

        return response()->json([
            'user' => $this->present($user),
            'token' => $user->createToken('api')->plainTextToken,
        ], 201);
    }

    public function login(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'email' => ['required', 'email'],
            'password' => ['required', 'string'],
        ]);

        $user = User::where('email', $validated['email'])->first();

        // One message for both "no such user" and "wrong password". Telling
        // them apart turns the login form into an account enumerator.
        // Hash::check is still called on a dummy hash when the user is missing,
        // so the response time does not leak the answer either.
        $hash = $user?->password_hash ?? '$2y$12$'.str_repeat('x', 53);
        if (! Hash::check($validated['password'], $hash) || $user === null) {
            throw ValidationException::withMessages(['email' => 'These credentials do not match our records.']);
        }

        return response()->json([
            'user' => $this->present($user),
            'token' => $user->createToken('api')->plainTextToken,
        ]);
    }

    public function logout(Request $request): JsonResponse
    {
        // Only the token that made this call, not every token the user holds:
        // logging out of a laptop should not sign them out on their phone.
        $request->user()->currentAccessToken()->delete();

        return response()->json(['status' => 'ok']);
    }

    public function me(Request $request): JsonResponse
    {
        return response()->json($this->present($request->user()));
    }

    /** @return array<string, mixed> */
    private function present(User $user): array
    {
        return [
            'id' => $user->id,
            'name' => $user->name,
            'email' => $user->email,
            'tenant_id' => $user->tenant_id,
        ];
    }
}
