---
name: hevy
description: Answer questions about the user's strength training from their Hevy account: past workouts, exercise progress and estimated 1RM, personal records, weekly training load, muscle balance, consistency, routines and body measurements. Also log or edit workouts, save routines, create exercises and log body measurements when the user asks. Use when the user asks what they lifted, how a lift is progressing, what to train next, or wants a workout or routine recorded in Hevy.
---

# Hevy workout data

The `hevy_*` tools read and write the user's own Hevy account through Hevy's API. Reads are
served from a local cache that refreshes itself on use. Writes change the Hevy account. Training
logs are personal data: use them to answer the user, do not repeat more than the question needs,
and never share them with other tools unless asked.

## Which tool

| Question | Tool |
|---|---|
| "What did I do this week?", workouts in a period, "my last leg day" | `hevy_workouts` (`days`, `date`, `start_date`/`end_date`, `query`), then `hevy_workout` with `id` (`"latest"` = most recent) |
| Every set of one workout | `hevy_workout` |
| Find an exercise's exact name or id | `hevy_exercises` (`query`, `muscle`, `equipment`, `custom_only`) |
| "Is my bench going up?", progress on one lift | `hevy_exercise_progress` (`exercise`, date range) |
| "Personal records", "what PRs did I set this month" | `hevy_records` (`days`) |
| "How hard was this week?", volume spikes, muscles missed | `hevy_training_load` (`days`, default 7) |
| "How often do I train?", streaks, gaps | `hevy_consistency` (`weeks`, default 12) |
| "What routines do I have?", a routine's exercises and sets | `hevy_routines` (no id lists; `id` shows one) |
| Weight, body fat and other measurements over time | `hevy_body_measurements` |
| Log a finished session | `hevy_log_workout` |
| Fix a logged session | `hevy_update_workout` (`id`) |
| Build or change a routine | `hevy_save_routine` (`routine_id` to update), `hevy_create_routine_folder` |
| Exercise missing from Hevy | `hevy_create_exercise` |
| Record weight or a measurement for a date | `hevy_log_body_measurement` |

Dates are the user's local calendar days. Workout times are shown in local time. Every data tool
accepts `raw: true` for the Hevy objects; it is large, so use it only when a field you need is missing.

## Units

Hevy stores weight in kg, and the read tools return kg (`weight_kg`, `volume_kg`). When the user
speaks in pounds, convert for the answer (1 kg = 2.2046 lb) and say which unit you used. The write
tools accept `weight_lbs` as well as `weight_kg`; use `weight_lbs` when the user gave pounds, so
the conversion is not rounded twice. Durations are in minutes (`duration_min`) or `hh:mm` text.

## Interpreting the numbers

- **Working sets** exclude warm-ups everywhere. Set types `normal`, `failure` and `dropset` count.
- **Estimated 1RM (e1rm)** is an Epley estimate, `weight x (1 + reps/30)`, from sets of 1 to 12
  reps. It is less reliable above about 10 reps or on failure sets. Call it an estimate, not a
  tested max.
- **Progress over several sessions**, not one. Use `summary.e1rm_trend` (`change_pct` compares the
  last 3 sessions with the 3 before; `slope_kg_per_4_weeks` is the trend) and `stalled`. `stalled`
  means at least 4 sessions with an e1rm and at least 21 days without a new best e1rm. A single
  off day is noise.
- **PR kinds**: `e1rm` (estimated max), `weight` (heaviest working set), `reps` (most reps in a set
  for exercises never loaded), `volume` (session volume). The first session of an exercise is not a
  PR. A PR is a session that beats every earlier one.
- **Volume** is weight x reps of working sets (kg). It rises with weight, reps and sets, so compare
  it with the user's own baseline, not with others.
- **Training-load flags** compare the last `days` with the 28 days before, scaled to the same length.
  `sets_vs_baseline` and `volume_vs_baseline` need a meaningful baseline. `muscle_spike` and
  `muscle_missed` are per muscle. Quote the flag text and numbers. No flags means the week looks like
  the user's usual training.
- **Sets per muscle** count a primary muscle as 1 set and each secondary muscle as 0.5.
- **Push/pull balance** (`push_pull.ratio`) uses chest, shoulders and triceps against lats, upper back
  and biceps. A ratio far from 1 is worth a mention when the user asks about balance; it is not a
  fault by itself.
- **Consistency**: weeks run Monday to Sunday. `current_streak_weeks`, `avg_sessions_per_week` (complete
  weeks only), `longest_gap_days` and `usual_days` describe the pattern. Report them neutrally. Do not
  moralise about missed sessions.
- **Body measurements**: one entry per date. Weight changes day to day with water and food; judge the
  trend over weeks.

This is not medical or coaching certainty. For pain, injury or a plan change beyond the data, suggest
a qualified coach or doctor.

## Writing to Hevy

- **Write only when the user asked for the change.** Hermes shows the user an approval dialog with a
  preview of every write. If they decline, the call is blocked: say the change was not saved. Do not
  ask "are you sure?" in chat as well; once the request is clear, make the call once.
- **Resolve exercises first.** Call `hevy_exercises` and use the exact title or id it returns. If the
  exercise does not exist, offer `hevy_create_exercise` before logging it.
- **Routines**: build them for the goal. Use `rep_range` (`start`, `end`) for hypertrophy-style targets
  instead of a fixed `reps`. Set `rest_seconds` per exercise. Type warm-up sets `"warmup"`. Link
  supersets with the same `superset_id`.
- **Replacing lists**: when updating a routine or workout, `exercises` REPLACES the whole list. Fetch the
  current version first (`hevy_routines` with `id`, or `hevy_workout`) and pass the complete list with
  the changes made.
- **Deleting is not possible** through the API. If the user wants a workout or routine removed, tell
  them to delete it in the Hevy app.
- **Privacy flag**: Hevy does not report whether an existing workout is private, and an update overwrites it.
  `hevy_update_workout` needs `is_private` (true or false): use what the user said, or ask them once.
- **Body measurements** are one entry per date. `hevy_log_body_measurement` merges the new values with
  what is already saved for that date, so pass only the fields that changed. To remove a value, list it in
  `clear`; nothing is removed otherwise.
- **Plan limits**: Hevy's free accounts cannot use the API (Hevy Pro is needed). Routine and custom
  exercise limits also come back as errors. Relay Hevy's message; do not work around the limit.

## Crossing with Garmin

Use this section only when the `garmin_*` tools are also available (the separate garmin-connect
plugin). If they are not, do not mention Garmin.

- **Recovery before a session**: `garmin_today` and `garmin_trends` (HRV, resting heart rate, sleep)
  against the planned session from `hevy_training_load` or `hevy_workouts`. Give the answer based on
  the flags, not one value.
- **After heavy sessions**: check HRV, resting heart rate and sleep on the days after a hard lift.
  Match by local date. Present it as a pattern over several sessions.
- **Heart rate of a strength session**: `garmin_activities` with `activity_type: strength_training` on
  the same date, then `garmin_activity` with its `id`. Match it to the Hevy workout by overlapping start
  time. Heart rate and calories come from Garmin; sets, reps and weights come from Hevy.
- **Never add the two apps together.** The same session often exists in both (Hevy can sync to Garmin,
  or the watch records it). Do not add session counts, durations or volume from Hevy and Garmin
  together. Hevy is the source of truth for training content; Garmin for heart rate, recovery and calories.

## Errors

- **Key missing or rejected** (`NotConfigured`, 401): the user needs a Hevy API key and a Hevy Pro
  subscription. The key is created at https://hevy.com/settings?developer and pasted in Hermes Desktop
  under Settings > Plugins > hevy (Hevy API key), or with `hermes config set HEVY_API_KEY <key>` in a
  terminal. Never ask the user to paste the key into the chat; chat messages go to the model provider.
- **Plan or limit** (403): relay Hevy's message (Pro needed, or the routine or custom-exercise limit).
- **Not found** (`hevy_workout`): list recent workouts with `hevy_workouts` and retry with a listed id.
- **Workout missing after just finishing it**: the cache refreshes itself on use, at most about once a
  minute. If it is still missing, the user can run `/hevy sync` to refresh it.
- **Rate limit or server errors**: wait and retry later with a shorter range.
