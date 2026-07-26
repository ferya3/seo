<?php

namespace App\Providers;

use App\Services\CrawlServiceClient;
use App\Services\KeywordServiceClient;
use Illuminate\Cache\RateLimiting\Limit;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Support\ServiceProvider;

class AppServiceProvider extends ServiceProvider
{
    /**
     * Register any application services.
     */
    public function register(): void
    {
        $this->app->singleton(CrawlServiceClient::class, fn () => CrawlServiceClient::fromConfig());
        $this->app->singleton(KeywordServiceClient::class, fn () => KeywordServiceClient::fromConfig());
    }

    /**
     * Bootstrap any application services.
     */
    public function boot(): void
    {
        // `throttle:api` names this limiter. Laravel does not define it for
        // you — without this the middleware throws at request time, which is
        // the sort of thing only running the app reveals.
        //
        // Per user, falling back to IP for unauthenticated requests: keying
        // solely on IP would let one tenant behind a shared NAT exhaust the
        // budget for everyone else there.
        RateLimiter::for('api', fn (Request $request) => Limit::perMinute(60)
            ->by($request->user()?->id ?: $request->ip()));

        // Crawling is expensive and asynchronous, so it gets its own, much
        // tighter budget rather than sharing the general one.
        RateLimiter::for('crawls', fn (Request $request) => Limit::perMinute(5)
            ->by($request->user()?->id ?: $request->ip()));
    }
}
