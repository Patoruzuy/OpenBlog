# OpenBlog Ops Guide

This document describes operational procedures for async systems.

## Admin Dashboard

- `/admin/ops`
- `/admin/ops/ai-reviews`
- `/admin/ops/digests`
- `/admin/ops/notifications`

All endpoints require admin role.

## Common Issues

### AI Reviews stuck in queued
- Verify Redis reachable
- Verify Celery worker running
- Retry failed jobs from Ops

### AI Reviews failing
- Inspect error_message in Ops table
- Common causes: provider timeout, invalid config
- Retry after fix

### Digests failing
- Check SMTP configuration
- Inspect digest_runs.error_message
- Retry failed run

### Notification spikes
- Inspect event_type distribution in `/admin/ops/notifications`
- Verify dedupe fingerprint logic is active

## Important Tables

- ai_review_requests
- ai_review_results
- digest_runs
- notifications
- subscriptions

## Safety Rules

- Prefer retry over manual DB edits.
- Never re-enable public caching for workspace/admin endpoints.
- Do not modify fingerprint or idempotency keys manually.