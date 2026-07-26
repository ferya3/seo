<?php

declare(strict_types=1);

namespace App\Services;

use RuntimeException;

/** The crawl service refused the request — a bad or disallowed URL. Not retryable. */
final class CrawlRequestRejected extends RuntimeException
{
}
