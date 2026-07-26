"""WSGI entrypoint for production servers.

    gunicorn --workers 1 --threads 8 wsgi:app

Note the single worker. Jobs run in background threads and their state lives in
this process, so a second worker would answer progress polls for jobs it has
never heard of. Concurrency comes from threads instead, which is the right
shape here anyway: the work is network-bound crawling, not CPU.
"""

from seoagent.web import create_app

app = create_app()
