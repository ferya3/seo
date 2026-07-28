<?php

declare(strict_types=1);

namespace App\Services;

use RuntimeException;

/** A backend service could not be reached, or returned 5xx. Retryable. */
final class ServiceUnavailable extends RuntimeException {}
