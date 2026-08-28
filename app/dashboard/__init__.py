"""Dashboard package (D5) — read-only operations surface + experiment metrics.

Server-rendered Jinja2 over the SAME SQLite store the pipeline writes
(WAL keeps concurrent readers safe); the only write endpoints are the
kill switch and the human-gate decide buttons.
"""
