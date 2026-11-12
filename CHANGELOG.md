# Changelog

All notable changes to the `hevy` plugin are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-10

First release.

### Added

- Hevy through Hevy's official public API, with an API key read through Hermes' secret route. The key is sent only
  in the `api-key` header to `api.hevyapp.com`.
- The key can be entered without a terminal: Hermes Desktop shows a masked **Hevy API key** field in
  **Settings > Plugins > hevy** (manifest `config_schema` secret, stored in the profile's `.env` as `HEVY_API_KEY`).
  The installer also prompts for it.
- Read-only tools: `hevy_workouts`, `hevy_workout`, `hevy_exercises`, `hevy_exercise_progress`, `hevy_records`,
  `hevy_training_load`, `hevy_consistency`, `hevy_routines` and `hevy_body_measurements`. They are hidden until the
  key is set.
- Write tools, each one approved by the user first in Hermes' approval dialog: `hevy_log_workout`,
  `hevy_update_workout`, `hevy_save_routine`, `hevy_create_routine_folder`, `hevy_create_exercise` and
  `hevy_log_body_measurement`. A declined write is blocked. The `confirm_writes` setting turns the gate off.
- Estimated 1RM (Epley, 1 to 12 reps), personal records (`e1rm`, `weight`, `reps`, `volume`), and exercise progress
  with trend, stall detection and the last 15 sessions.
- `hevy_training_load`: the last N days (7 by default) against the 28 days before, scaled to the same length, with
  sets and volume per muscle group, push/pull balance and deterministic flags (`sets_vs_baseline`,
  `volume_vs_baseline`, `muscle_spike`, `muscle_missed`, `push_pull_imbalance`, `no_sessions`).
- `hevy_consistency`: weeks Monday to Sunday, average sessions per week, current streak, longest gap, usual training
  days and days since the last workout.
- A local SQLite cache of workouts and exercise templates under `<HERMES_HOME>/plugin-data/hevy/`, one file per API
  key, kept up to date with Hevy's workout events feed at most once a minute.
- `/hevy status`, `/hevy sync` and `/hevy help`.
- The `hevy:hevy` skill and the **Weekly training review** automation blueprint, which can relate training to
  recovery when the `garmin-connect` plugin is enabled.
- Time zone setting for "today", defaulting to `auto` (Hermes' `timezone`, then the server's).
- CI that runs the tests on Python 3.11 and 3.13 and validates the plugin against the latest stable Hermes release.

[0.1.0]: https://github.com/adrianlaborda/hermes-hevy/releases/tag/v0.1.0
