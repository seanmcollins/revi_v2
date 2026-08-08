"""Cohort TTL sweep (CLI, cron-able) — see :mod:`revi_scheduler.sweep`.

That sweep is the entirety of this app. The portfolio pre-materialization
runner once planned here was cancelled: the anomaly/portfolio population is
baked into the warehouse generator (``write_detected_anomalies``, run by
``make warehouse``).
"""
