# Security policy

## Supported versions

Only the latest release gets security fixes.

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through
[GitHub's private vulnerability reporting](https://github.com/adrianlaborda/hermes-hevy/security/advisories/new).

Include what an attacker can do, the steps to reproduce and the plugin and Hermes Agent versions. You will get an
answer within a week. Once a fix is released, the advisory is published and you are credited unless you prefer
otherwise.

Never include a real Hevy API key or training data in a report. Synthetic values are enough.

## What the plugin handles

- **The Hevy API key.** Read through Hermes' secret route (per profile) on each request, stored by Hermes in the
  profile's `.env`. It is sent only to `api.hevyapp.com` in the `api-key` header. The plugin never writes, logs or
  returns it, never stores it in the cache, and never reads it from files.
- **Your workout and body data.** Fetched from `api.hevyapp.com` and passed to the model provider you chose only when
  a tool runs. No telemetry, no other host.
- **The workout cache.** Workouts and exercise templates are cached on disk in `<HERMES_HOME>/plugin-data/hevy/`,
  one file per key, named by a hash of the key. Delete that folder to clear it.
- **Writes.** Every write tool (logging, updating, routines, folders, exercises and body measurements) is held behind
  Hermes' approval dialog, unless you turn `confirm_writes` off. A declined write is blocked.
- **Scheduled jobs.** Scheduled writes follow Hermes' `approvals.cron_mode`; any other unattended context blocks
  writes.

## Out of scope

- Hevy's own services and accounts. Report those to Hevy.
- Hevy Pro billing and API key management in the Hevy app.
- Hermes Agent itself. Report those to [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).
- What the model provider you chose does with data you send it.
