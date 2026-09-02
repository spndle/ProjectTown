# Phase 4A: Local Workspace Task Workbench v1

Roadmap and gate definitions: [`v3-phase-4.md`](v3-phase-4.md).

This is a default-off, loopback-only, **read-only engineering slice**. It is
not a human acceptance result and not the complete ordinary-user workflow.

The CLI registers an existing canonical Draft and Result into an external UI
work root. Registration is create-only. Each browser request revalidates the
binding hash, external session bytes, result integrity, and material-source
freshness before projecting a task, bounded preview, and citations.

The browser has no filesystem-path inputs. It has no generator, confirmation,
authorization, Apply, Restore, Publish, or mutable material endpoint. A stale,
tampered, unsafe, or root-overlapping binding is rejected without returning
absolute paths or raw exceptions.

Enable only on a native host with an existing canonical root containing a
`bindings` directory, using `PROJECTTOWN_ENABLE_LOCAL_WORKSPACE_TASK=1` and
`PROJECTTOWN_LOCAL_WORKSPACE_TASK_ROOT`. The origin remains the existing strict
`PROJECTTOWN_V3_ORIGIN` loopback value. The default is off.

Register with `scripts/run_v3_local_workspace_task.py create`, then use
`check`. The browser serves `/workspace` and `/api/workspace/*` only when the
feature is enabled. This is deliberately a separate read-only projection, not
a second mutable truth.
