# Hevy for Hermes

[![CI](https://github.com/adrianlaborda/hermes-hevy/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/adrianlaborda/hermes-hevy/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/adrianlaborda/hermes-hevy)](https://github.com/adrianlaborda/hermes-hevy/releases/latest)
[![Hermes](https://img.shields.io/badge/Hermes-%E2%89%A50.23.0-blue)](https://github.com/NousResearch/hermes-agent)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Your Hevy strength training in [Hermes Agent](https://github.com/NousResearch/hermes-agent): workouts, exercise
progress with estimated 1RM, personal records, sets per muscle group against your own usual week, consistency,
routines and body measurements, read through Hevy's official
[public API](https://api.hevyapp.com). No password, no scraping, no third-party relay: Hermes talks to Hevy
directly with an API key you create in Hevy.

Hermes can also log workouts, build routines and record measurements. Every one of those writes is shown to you
first and does nothing until you approve it.

The plugin does the arithmetic itself (estimated 1RM, PRs, load flags, consistency), so Hermes can say "working
sets 1.4x your usual week" or "bench e1RM up 6% over the last three sessions" instead of estimating from memory.

> **Unofficial.** This is a community project. It is not affiliated with, endorsed by or supported by Hevy
> Studios S.L. or Nous Research. "Hevy" is a trademark of its owner. Hevy says its public API is early and may
> change.

**Requirements:** Hermes Agent 0.23.0 or later, Python 3.11 or later, and a **Hevy Pro** subscription (Hevy's API
is available only with Pro). MIT licensed ([LICENSE](LICENSE)). Changes are listed in
[CHANGELOG.md](CHANGELOG.md).

## Quick start

1. **Create an API key.** In the Hevy web app, open
   [Settings > Developer](https://hevy.com/settings?developer) and create a key. Copy it.
2. **Install the plugin and add the key.** No terminal needed in Hermes Desktop; the terminal route follows.

### In Hermes Desktop

1. Open **Capabilities** in the sidebar, then the **Plugins** tab.
2. Select **Install from Git**, paste `https://github.com/adrianlaborda/hermes-hevy` in **Repository**, then
   select **Install**. Leave **Enable agent plugin after install** checked. A toast saying the plugin needs
   `HEVY_API_KEY` is expected: that is the next step.
3. Open **Settings > Plugins > hevy** (also reachable with the gear in its row under Capabilities > Plugins).
   Paste the key in **Hevy API key** and select **Save settings**. Desktop stores it in the profile's `.env`,
   never in `config.yaml`.
4. Start a new chat and ask a question about your training. No restart is needed. If a chat that was already open
   does not see the Hevy tools, start a new one.

### From a terminal

```sh
hermes plugins install adrianlaborda/hermes-hevy
hermes plugins enable hevy
```

The installer asks for `HEVY_API_KEY` with a masked prompt and saves it to your profile's `.env`. To set or
replace it later, run `hermes config set HEVY_API_KEY <key>` (start the line with a space, which keeps the key out
of most shells' history).

### Check it

In any chat, `/hevy status` shows whether the key is set, which Hevy account it belongs to and whether that
account has Pro, how many workouts Hevy holds, when the workout cache last synced, the time zone in use and whether
writes ask for approval. `/hevy sync` fetches workout changes from Hevy now.

Never paste the key into a chat message: messages go to your model provider.

Then just ask:

> How is my bench press progressing?
> What PRs did I hit this month?
> Was this week harder than usual?
> Build me a 3-day full-body routine with rep ranges.
> Log today's session: squat 3x5 at 100 kg...
> I weighed 79.8 kg this morning.

The last three ask Hermes to write to Hevy. Each write opens an approval dialog first.

## What Hermes gets

All reads are read-only. Dates are your local calendar days.

| Tool | What it returns |
|---|---|
| `hevy_workouts` | Workouts in a date range (`days` 30 by default, up to 366, or `date`, or `start_date`/`end_date`), newest first, optionally matching `query` on a workout or exercise title. Each one as a summary (date, time, duration, exercises, working sets, reps, volume, muscles hit), plus totals for the range |
| `hevy_workout` | One workout in full: every exercise, every set with weight, reps, RPE and distance or duration where logged, the best set and estimated 1RM per exercise. `id` `"latest"` gives the most recent |
| `hevy_exercises` | Search your exercise library (Hevy's and your custom ones) by name, primary muscle, equipment or custom only, with each exercise's id, and how often and when you did it in the last 90 days |
| `hevy_exercise_progress` | One exercise over a period (default 365 days): sessions, best estimated 1RM and best weight with dates, the trend (change over the last three sessions and per 4 weeks), whether it has stalled, its PRs, and the last 15 sessions |
| `hevy_records` | Personal records in the last `days` (30 by default): estimated 1RM, heaviest weight, most reps and session volume, newest first |
| `hevy_training_load` | The last `days` (7 by default) against the 28 days before, scaled to the same length: sessions, working sets, volume, duration, sets per muscle group against the baseline, push/pull balance, and flags |
| `hevy_consistency` | The last `weeks` (12 by default) as Monday-to-Sunday weeks: sessions and sets per week, current streak, longest gap, usual training days, and days since the last workout |
| `hevy_routines` | Without `id`: your routines and folders. With `id`: one routine with each exercise, its sets, rep ranges and rest |
| `hevy_body_measurements` | Body measurements in a date range (default 365 days), newest first, with the latest entry |

Responses are compact summaries made for a model to read: units in the field names (`weight_kg`, `volume_kg`),
durations in minutes or `h:mm`, and local times. Every data tool takes `raw: true` when Hermes needs Hevy's full
object.

### Writes

Six tools change your Hevy account. Each one is shown to you in Hermes' approval dialog before it runs, with a
preview of what will change:

| Tool | What it does |
|---|---|
| `hevy_log_workout` | Logs a finished workout: title, time or duration, privacy, exercises and sets |
| `hevy_update_workout` | Changes a logged workout. Its exercise list is replaced as a whole; other fields you do not change are kept |
| `hevy_save_routine` | Creates a routine, or updates one (its exercise list is replaced as a whole) |
| `hevy_create_routine_folder` | Creates a routine folder |
| `hevy_create_exercise` | Creates a custom exercise |
| `hevy_log_body_measurement` | Saves measurements for a date. Only the fields you pass change; the rest of that date's entry is kept |

The approval dialog is the same gate Hermes uses for dangerous terminal commands. If you decline, the write is
blocked and nothing is sent to Hevy. Choosing **Always** in that dialog trusts that tool from then on.

- **Turning the gate off.** The setting `confirm_writes` (on by default) controls it. Turn it off only if you
  trust the model to write unattended.
- **Scheduled jobs** follow Hermes' `approvals.cron_mode`. Any other unattended context blocks writes.
- **Nothing is deleted.** Hevy's API has no delete, so the plugin cannot remove a workout, routine or exercise.
  Delete those in the Hevy app.
- **Privacy on updates.** Hevy does not report whether a workout is private, and an update overwrites that
  setting. The plugin never guesses: `hevy_update_workout` refuses to run until the model passes `is_private`, so
  Hermes asks you if it does not already know.

## How the numbers are computed

The plugin computes these itself, from your workouts, so the same data always gives the same numbers. Warm-up
sets are excluded everywhere; working sets are normal, failure and drop sets.

- **Estimated 1RM** uses Epley, `weight x (1 + reps / 30)`, for sets of 1 to 12 reps. A single rep counts as the
  weight itself. It is an estimate, not a tested max.
- **Personal records** come in four kinds: `e1rm`, `weight` (heaviest working set), `reps` (most reps in a set, for
  exercises never loaded) and `volume` (session volume). The first session of an exercise is not a PR.
- **Training load** compares the window with the 28 days before it, scaled to the same length. Flags fire at these
  thresholds:
  - `sets_vs_baseline`: working sets at least 1.3x or at most 0.7x the baseline (baseline at least 10 sets);
  - `volume_vs_baseline`: the same thresholds on volume (baseline at least 1,000 kg);
  - `muscle_spike`: one muscle at least 1.5x its baseline (baseline at least 3 sets);
  - `muscle_missed`: a muscle with at least 4 sets in the baseline and none in the window;
  - `push_pull_imbalance`: push and pull together at least 6 sets, with a ratio above 1.5 or below 0.67;
  - `no_sessions`: no workouts in the window although the baseline had some.
- **Sets per muscle**: each working set counts 1 for its primary muscle and 0.5 for each secondary muscle.
  Push is chest, shoulders and triceps; pull is lats, upper back and biceps.
- **Consistency** reports weeks Monday to Sunday: average sessions per complete week, current streak in weeks,
  longest gap in days, and the weekdays you usually train.

```json
{"kind": "sets_vs_baseline", "direction": "up",
 "text": "working sets 1.4x your usual week (38 vs 27)"}
```

The arithmetic is deterministic and runs in the plugin, so the model reads numbers instead of estimating them.

## Workout cache

Hevy's workout list has no date filter and returns 10 workouts a page, so listing a month of training would take
several requests. The plugin keeps a mirror of your workouts and exercise library in SQLite, under
`<HERMES_HOME>/plugin-data/hevy/`, one file per API key. The file name is a hash of the key, never the key.

The first use does a full sync. After that, the cache follows Hevy's workout events feed (updates and deletions),
at most once a minute, when a question needs it. `/hevy sync` forces a sync now. Exercise templates are refreshed
when they are more than a day old. Body measurements are not cached; they are read from Hevy each time.

## Working with Garmin

Hevy records what you lifted. [Garmin Connect](https://github.com/adrianlaborda/hermes-garmin) records how your
body responded. With both plugins enabled, Hermes can relate training to recovery: HRV, resting heart rate and
sleep around a hard session, and the heart rate of a strength workout. It matches the two by date and time.

The two apps often record the same session. Hermes is told not to add them together: Hevy is the source for
sets and weights, Garmin for heart rate, recovery and calories.

> Did my HRV drop the day after the heavy leg session?
> What was my heart rate during Tuesday's upper body workout?

The Garmin tools are used only when the separate `garmin-connect` plugin is enabled.

## Weekly training review

The plugin adds the automation blueprint **Weekly training review** (`weekly-training-review`). Schedule it from
`/blueprint`, the dashboard or Desktop's automations: pick the time (default 19:00), the day, your training goal
and where to deliver it (`origin`, the chat you set it up from; `local`; Telegram, Discord or email).

Each run reads the last week's training load, the last eight weeks of consistency and the week's personal
records, adds recovery from Garmin when that plugin is on, and sends at most ten short lines: sessions and sets
against your usual week, muscle groups that got much more or less work, new PRs, consistency, and one suggestion
for next week.

The day choices are Sunday, Monday, Friday and Saturday.

## Skill

The bundled `hevy:hevy` skill tells the agent which tool answers which question, how to read estimated 1RM, load
flags and consistency against your own baseline, how to resolve exercise names before logging, and how to write
routines (rep ranges, rest, supersets). It also says when to suggest a qualified coach or doctor instead.

## Settings

In Desktop **Settings > Plugins > hevy**, or under `plugins.entries.hevy.settings` in `config.yaml`:

| Setting | Default | |
|---|---|---|
| `api_key` | | The Hevy API key. A masked field in Desktop, stored in the profile's `.env` as `HEVY_API_KEY`, never in `config.yaml` |
| `timezone` | `auto` | Which day is "today", and the local times of your workouts. `auto` uses Hermes' `timezone` setting, then the server's. Hevy's API does not expose your time zone, so pick an IANA zone such as `Europe/Madrid` if `auto` is wrong. `/hevy status` shows the zone in use |
| `confirm_writes` | `true` | **Ask before writing to Hevy**. See [Writes](#writes) |

## Privacy, security and limits

**Where your data goes.** Hevy's servers send your workouts to your Hermes; when a tool runs, its result goes to
the model provider you use with Hermes, like anything else in the chat. No telemetry, no analytics, no update
check, no remote fonts or images.

**Network hosts.** The plugin contacts exactly one host, over HTTPS:

- `api.hevyapp.com`

**Background work.** None. The plugin starts no thread and no schedule of its own. It syncs the cache when a tool
or `/hevy sync` asks for it. The weekly review runs only if you schedule it.

**The key.** It is read through Hermes' secret route (per profile) on each request and sent to Hevy in the
`api-key` header. It is never logged, never written to the cache and never returned by a tool. Error messages are
scrubbed of it. A rejected key gives you the link to create a new one. Delete the key in Hevy at any time to revoke
access.

**What is stored.** Workouts and exercise templates are cached on disk in `<HERMES_HOME>/plugin-data/hevy/`. The
cache holds your workout notes and descriptions too. To clear it, delete that folder; the plugin rebuilds it with a
full sync on the next question.

**Limits.**

- Hevy's API is available only with **Hevy Pro**. Without it, Hevy refuses the calls and the plugin shows Hevy's
  message.
- Hevy's routine and custom-exercise limits come back as errors. The plugin relays Hevy's message.
- Hevy's API has no webhooks and no delete. Data is fetched when asked, and changes reach the cache at most once a
  minute.
- Hevy's API is early and may change; the plugin may need an update when it does.
- Hevy's time zone is not exposed, so "today" follows the `timezone` setting.

**Not medical or coaching advice.** Estimated 1RM is an estimate, and the flags describe your own history. Hermes
reads them as a pattern, not as a diagnosis or a training prescription.

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
python -m pytest
hermes plugins validate .
```

`hermes plugins validate . --install-deps` (what the catalog CI runs) installs the plugin's dependencies into the
Hermes environment, so run it in a disposable Hermes install, not your daily one. CI
(`.github/workflows/ci.yml`) runs the tests on Python 3.11 and 3.13 and validates the plugin against the latest
stable Hermes release. See [CONTRIBUTING.md](CONTRIBUTING.md) and, for vulnerabilities,
[SECURITY.md](SECURITY.md).

The tests run without network or a Hevy account, against synthetic fixtures shaped from Hevy's OpenAPI
description.

Layout: `hermes_hevy/client.py` (HTTP, retries, cache, pagination), `store.py` (the workout cache), `dates.py`,
`summaries.py` (workouts and exercises to compact dicts, e1RM, PRs), `training.py` (load, muscle balance,
consistency), `writes.py` (payload builders, merges and approval previews), `tools.py` (the model-facing tools),
`approval.py` (the write gate), `commands.py` (`/hevy`) and `runtime.py` (key, time zone and settings).

## License

[MIT](LICENSE).
