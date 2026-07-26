<?php

declare(strict_types=1);

use App\Http\Controllers\Api\CrawlController;
use Illuminate\Support\Facades\Route;

/*
 * Public API. Auth and rate limiting live here so no downstream service has to
 * reimplement them; services trust that anything reaching them is authorised.
 */

Route::get('/healthz', fn () => response()->json(['status' => 'ok', 'service' => 'api-gateway']));

Route::middleware(['auth:sanctum', 'throttle:api'])->prefix('v1')->group(function () {
    Route::post('/crawls', [CrawlController::class, 'store']);
    Route::get('/crawls', [CrawlController::class, 'index']);
    Route::get('/crawls/{crawl}', [CrawlController::class, 'show']);
});
