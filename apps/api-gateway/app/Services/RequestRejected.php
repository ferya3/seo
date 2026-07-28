<?php

declare(strict_types=1);

namespace App\Services;

use RuntimeException;

/**
 * A backend service refused the request — a bad URL, a blocked internal
 * target, a seed it cannot use. The caller's problem, not an outage, so it
 * must not be retried and must keep the service's own explanation.
 */
final class RequestRejected extends RuntimeException {}
