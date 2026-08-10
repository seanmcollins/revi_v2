"""Cohort TTL sweep (CLI, cron-able) — see :mod:`revi_scheduler.sweep`.

That sweep is the entirety of this app: the anomaly/portfolio population it
might otherwise pre-materialize is baked into the warehouse generator
(``write_detected_anomalies``, run by ``make warehouse``).
"""
