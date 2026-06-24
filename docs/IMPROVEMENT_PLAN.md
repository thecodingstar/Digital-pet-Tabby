# Tabby Improvement Log

A running, tracked log of concrete tuning/hardening proposals for Tabby. The
Phase-6 monitoring review loop (`/analyze-cat`, see `docs/MONITORING.md`) appends
data-driven suggestions here from rolled-up telemetry; humans triage them into the
roadmap (`docs/ULTRAPLAN.md`).

Each entry: a date, the signal that prompted it, and a proposed change.

## Format

```
### YYYY-MM-DD — <short title>
- **Signal:** what the telemetry roll-up showed (e.g. "fear events 9/day, 70% from
  permission prompts; bond slope -2.1/day during a 5-day gap").
- **Proposal:** the concrete tuning/code change.
- **Status:** proposed | accepted | done | rejected.
```

## Entries

_(none yet — the monitoring loop has not run. Seeded by the Ultraplan v2 work.)_
