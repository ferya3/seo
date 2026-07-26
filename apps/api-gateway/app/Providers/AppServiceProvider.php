<?php

namespace App\Providers;

use App\Services\CrawlServiceClient;
use Illuminate\Support\ServiceProvider;

class AppServiceProvider extends ServiceProvider
{
    /**
     * Register any application services.
     */
    public function register(): void
    {
        $this->app->singleton(CrawlServiceClient::class, fn () => CrawlServiceClient::fromConfig());
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
