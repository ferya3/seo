<?php

use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Foundation\Configuration\Middleware;
use Illuminate\Http\Request;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        web: __DIR__.'/../routes/web.php',
        // Without this line routes/api.php is never loaded and every endpoint
        // in it 404s — the file existing is not the same as it being routed.
        api: __DIR__.'/../routes/api.php',
        commands: __DIR__.'/../routes/console.php',
        health: '/up',
    )
    ->withMiddleware(function (Middleware $middleware): void {
        /*
         * There is no login page to send anyone to — this app is an API and
         * nothing else. Laravel's default guest redirect calls route('login'),
         * which does not exist here, so an unauthenticated request that did
         * not ask for JSON blew up with RouteNotFoundException and came back
         * as 500 instead of 401. Found by curling the running gateway; the
         * test suite missed it because getJson() sets an Accept header and
         * that path skips the redirect entirely.
         */
        $middleware->redirectGuestsTo(fn (Request $request) => null);
    })
    ->withExceptions(function (Exceptions $exceptions): void {
        $exceptions->shouldRenderJsonWhen(
            fn (Request $request) => $request->is('api/*'),
        );
    })->create();
