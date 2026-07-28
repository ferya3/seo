<?php

return [

    'keyword' => [
        'url' => env('KEYWORD_SERVICE_URL', 'http://keyword-api:8000'),
        // Research fans out hundreds of autocomplete queries, so it is slower
        // to accept than a crawl request.
        'timeout' => (int) env('KEYWORD_SERVICE_TIMEOUT', 30),
    ],

    'orchestrator' => [
        'url' => env('ORCHESTRATOR_URL', 'http://orchestrator:8000'),
        // Starting a workflow plans it and dispatches the first step; it does
        // not wait for any of the work.
        'timeout' => (int) env('ORCHESTRATOR_TIMEOUT', 15),
    ],

    'serp' => [
        'url' => env('SERP_SERVICE_URL', 'http://serp-api:8000'),
        'timeout' => (int) env('SERP_SERVICE_TIMEOUT', 15),
    ],

    'reporting' => [
        // Rendering reads the whole workflow report, so it is given more room
        // than a request that only hands off work.
        'url' => env('REPORTING_SERVICE_URL', 'http://reporting-api:8000'),
        'timeout' => (int) env('REPORTING_SERVICE_TIMEOUT', 20),
    ],

    'notifications' => [
        'url' => env('NOTIFICATIONS_SERVICE_URL', 'http://notifications-api:8000'),
        'timeout' => (int) env('NOTIFICATIONS_SERVICE_TIMEOUT', 15),
    ],

    'competitor' => [
        // Every competitor is a crawl report fetched and held in memory, so
        // accepting the request takes longer than handing off a single job.
        'url' => env('COMPETITOR_SERVICE_URL', 'http://competitor-api:8000'),
        'timeout' => (int) env('COMPETITOR_SERVICE_TIMEOUT', 30),
    ],

    'crawl' => [
        'url' => env('CRAWL_SERVICE_URL', 'http://crawl-api:8000'),
        'timeout' => (int) env('CRAWL_SERVICE_TIMEOUT', 10),
    ],


    /*
    |--------------------------------------------------------------------------
    | Third Party Services
    |--------------------------------------------------------------------------
    |
    | This file is for storing the credentials for third party services such
    | as Mailgun, Postmark, AWS and more. This file provides the de facto
    | location for this type of information, allowing packages to have
    | a conventional file to locate the various service credentials.
    |
    */

    'postmark' => [
        'key' => env('POSTMARK_API_KEY'),
    ],

    'resend' => [
        'key' => env('RESEND_API_KEY'),
    ],

    'ses' => [
        'key' => env('AWS_ACCESS_KEY_ID'),
        'secret' => env('AWS_SECRET_ACCESS_KEY'),
        'region' => env('AWS_DEFAULT_REGION', 'us-east-1'),
    ],

    'slack' => [
        'notifications' => [
            'bot_user_oauth_token' => env('SLACK_BOT_USER_OAUTH_TOKEN'),
            'channel' => env('SLACK_BOT_USER_DEFAULT_CHANNEL'),
        ],
    ],

];
