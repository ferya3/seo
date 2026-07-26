"""Deployable services.

A real package rather than a namespace package: without it, pytest names test
packages from the first directory lacking __init__.py, which made
`services/keyword/tests` resolve as `keyword.tests` and collide with the
standard library's `keyword` module.
"""
