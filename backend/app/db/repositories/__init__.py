"""The only place SQL lives. Every function takes `cafe_id` and filters by it.

Exceptions are named and listed here: `cafes.by_phone_number_id` (tenant lookup for webhooks) and the
cross-café job queries in `jobs.py` (scheduled jobs that sweep every café).
"""
