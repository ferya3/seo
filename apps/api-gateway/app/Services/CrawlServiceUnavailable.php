<?php

declare(strict_types=1);

namespace App\Services;

use RuntimeException;

/** The crawl service could not be reached or returned 5xx. Retryable. */
final class CrawlServiceUnavailable extends RuntimeException
{
}
