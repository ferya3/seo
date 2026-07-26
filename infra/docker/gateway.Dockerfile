# API gateway — Laravel 13 on PHP 8.4.
#
# Not built or run in the environment this was written in (no docker daemon),
# unlike the gateway itself, which was run directly against Postgres and Redis.
FROM php:8.4-cli-alpine

ENV COMPOSER_ALLOW_SUPERUSER=1 \
    COMPOSER_NO_INTERACTION=1

WORKDIR /app

# pdo_pgsql for the shared database, redis for cache and the rate limiter.
RUN apk add --no-cache postgresql-dev libzip-dev $PHPIZE_DEPS \
 && docker-php-ext-install pdo_pgsql pcntl \
 && pecl install redis && docker-php-ext-enable redis \
 && apk del $PHPIZE_DEPS

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

# Dependencies first so a code change does not re-resolve the whole tree.
COPY apps/api-gateway/composer.json apps/api-gateway/composer.lock ./
RUN composer install --no-dev --no-scripts --no-autoloader --prefer-dist

COPY apps/api-gateway/ ./
RUN composer dump-autoload --optimize --no-dev

# The schema comes from infra/db/migrations, applied to Postgres directly —
# this image deliberately cannot migrate the shared database.
RUN addgroup -S app && adduser -S -G app app \
 && chown -R app:app storage bootstrap/cache
USER app

EXPOSE 8000
CMD ["php", "artisan", "serve", "--host=0.0.0.0", "--port=8000"]
