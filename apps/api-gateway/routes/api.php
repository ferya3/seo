<?php

declare(strict_types=1);

use App\Http\Controllers\Api\CrawlController;
use App\Http\Controllers\Api\KeywordController;
use Illuminate\Support\Facades\Route;

/*
 * Public API. Auth and rate limiting live here so no downstream service has to
 * reimplement them; services trust that anything reaching them is authorised.
 */

Route::get('/healthz', fn () => response()->json(['status' => 'ok', 'service' => 'api-gateway']));

Route::middleware(['auth:sanctum', 'throttle:api'])->prefix('v1')->group(function () {
    // Starting work is rate limited harder than reading it: a crawl costs
    // minutes of someone else's bandwidth, a GET costs a lookup.
    Route::post('/crawls', [CrawlController::class, 'store'])->middleware('throttle:crawls');
    Route::get('/crawls', [CrawlController::class, 'index']);
    Route::get('/crawls/{crawl}', [CrawlController::class, 'show']);

    Route::post('/research', [KeywordController::class, 'store'])->middleware('throttle:crawls');
    Route::get('/research', [KeywordController::class, 'index']);
    Route::get('/research/{research}', [KeywordController::class, 'show']);
});
