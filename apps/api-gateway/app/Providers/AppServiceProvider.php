<?php

namespace App\Providers;

use App\Services\CrawlServiceClient;
use App\Services\KeywordServiceClient;
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
        //
    }

    /**
     * Bootstrap any application services.
     */
    public function boot(): void
    {
        //
    }
}
