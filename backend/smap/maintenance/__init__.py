"""One-shot maintenance tasks.

Distinct from `app/workers/`: those are recurring policies on a cron, these are
repairs that run once against a deployment and are then done. A repair does not
belong on a schedule -- a nightly job that has had nothing to do for a year is
indistinguishable from one that is broken.
"""
