# API Routers Plugin

Official Baselith plugin packaging the default FastAPI router modules.

This plugin is the canonical home for:

- chat
- admin
- feedback
- tenant management
- indexing
- metrics
- status
- console SPA routing
- online evaluation summary (`GET /v1/evaluation/online/summary`, behind `EVAL_ONLINE_ENABLED`; fleet-wide when Redis is the cache backend, with a `scope` field saying so)

The `baselith/core/routers/*` modules remain as backward-compatible shims during the Sacred Core migration.
