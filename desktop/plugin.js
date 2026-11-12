// Hevy (unofficial) for Hermes Desktop: a read-only training panel.
//
// Plain ESM, loaded uncompiled by the Desktop plugin loader: no JSX, no build step, and only the
// three specifiers the loader resolves. Data comes from this package's own backend
// (dashboard/plugin_api.py, mounted at /api/plugins/hevy/) through ctx.rest; the panel never sees
// the Hevy API key. Colours are the Desktop theme variables only.
import {
  PALETTE_AREA,
  PANES_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  host,
  queryClient,
  useQuery,
  useValue
} from '@hermes/plugin-sdk'
import { useCallback, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'hevy'
const ROUTE = '/hevy'
const KEY_PAGE = 'https://hevy.com/settings?developer'
const KEY_ENV = 'HEVY_API_KEY'
// The manifest's config_schema `api_key` (type: secret) renders as a masked field on this plugin's own page
// in Settings › Plugins; Desktop saves it to the profile's .env. The page is addressed by the agent key.
const SETTINGS_PATH_TEXT = 'Settings › Plugins › hevy'
const KEY_FIELD = 'Hevy API key'
export const REVIEW_PROMPT =
  'Review my training week from my Hevy data: call hevy_training_load (7 days) and hevy_records, ' +
  'compare my sets per muscle group with my usual weeks, mention any flags and PRs, and suggest what ' +
  'to focus on in my next sessions. If my Garmin tools are available, add how my recovery (HRV, sleep, ' +
  'resting heart rate) looked this week. Keep it short.'

let pluginCtx = null

// -- data ---------------------------------------------------------------------------------------

class BackendError extends Error {}

async function rest(path) {
  if (!pluginCtx) throw new BackendError('The Hevy plugin is not loaded.')
  try {
    return await pluginCtx.rest(path, { timeoutMs: 45000 })
  } catch (error) {
    throw new BackendError(String(error?.message || error || 'Request failed'))
  }
}

function useOverview() {
  const profile = useValue(host.state.profile)
  const fresh = useRef(false)
  const query = useQuery({
    queryKey: [ID, profile, 'overview'],
    queryFn: () => {
      // an explicit refresh asks the backend to sync from Hevy before reading
      const path = fresh.current ? '/overview?fresh=true' : '/overview'
      fresh.current = false
      return rest(path)
    },
    staleTime: 5 * 60 * 1000,
    refetchInterval: 15 * 60 * 1000,
    refetchOnWindowFocus: false,
    retry: false
  })
  const { refetch } = query
  const refresh = useCallback(() => {
    fresh.current = true
    return refetch()
  }, [refetch])
  return { ...query, refresh }
}

// -- formatting ---------------------------------------------------------------------------------

// The panel's copy is English, so dates and numbers are formatted in English too.
const locale = () => 'en'
const finite = value => (typeof value === 'number' && Number.isFinite(value) ? value : 0)

export function parseLocal(text) {
  // Hevy dates are local calendar values ("2026-11-06" or "2026-11-06T18:05"); never shift zones.
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(String(text || ''))
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 12), +(m[5] || 0)) : null
}

export function longDate(text) {
  const d = parseLocal(text)
  return d ? d.toLocaleDateString(locale(), { weekday: 'long', day: 'numeric', month: 'long' }) : ''
}

export function whenText(text, now = new Date()) {
  const d = parseLocal(text)
  if (!d) return ''
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const diff = Math.round((today - day) / 86400000)
  const time = /T\d/.test(String(text)) ? d.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' }) : ''
  const prefix =
    diff === 0
      ? 'Today'
      : diff === 1
        ? 'Yesterday'
        : diff < 7
          ? d.toLocaleDateString(locale(), { weekday: 'long' })
          : d.toLocaleDateString(locale(), { day: 'numeric', month: 'short' })
  return time ? `${prefix}, ${time}` : prefix
}

// "HH:MM" of a local timestamp ("2026-11-09T17:58"), or '' when there is no time part.
export function timeText(text) {
  const d = parseLocal(text)
  return d && /T\d/.test(String(text))
    ? d.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' })
    : ''
}

export function clockText(ms) {
  return ms ? new Date(ms).toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' }) : ''
}

// "1:12" (h:mm, as duration_hmm) → "1 h 12 min"; "0:45" → "45 min".
export function durationText(hmm) {
  const parts = String(hmm || '').split(':').map(Number)
  if (parts.length !== 2 || parts.some(n => !Number.isFinite(n))) return hmm || ''
  const [h, m] = parts
  return h ? `${h} h ${String(m).padStart(2, '0')} min` : `${m} min`
}

// "18 Aug" for a week_start date.
export function weekLabel(text) {
  const d = parseLocal(text)
  return d ? `${d.getDate()} ${d.toLocaleDateString(locale(), { month: 'short' })}` : ''
}

const num = (value, digits = 0) =>
  typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString(locale(), { maximumFractionDigits: digits, minimumFractionDigits: digits })
    : ''

// Set counts: up to one decimal, no forced trailing zero ("12.5", "10").
export function setsText(value) {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString(locale(), { maximumFractionDigits: 1 })
    : ''
}

// The shared scale for the muscle bars: the largest of sets and baseline over all rows.
export function muscleScale(rows) {
  let max = 0
  for (const row of rows || []) max = Math.max(max, finite(row.sets), finite(row.baseline_sets))
  return max > 0 ? max : 1
}

// Width in percent (0..100) of a value on a bar scaled to max.
export function barPercent(value, max) {
  if (typeof value !== 'number' || !Number.isFinite(value) || !(max > 0)) return 0
  return Math.min(100, Math.max(0, (value / max) * 100))
}

// -- icons (inline, currentColor) ---------------------------------------------------------------

const ICONS = {
  dumbbell:
    'M6.5 6.5v11M17.5 6.5v11M3.5 9.5v5M20.5 9.5v5M6.5 12h11M3.5 9.5h3M17.5 9.5h3M3.5 14.5h3M17.5 14.5h3',
  refresh: 'M20 11a8 8 0 0 0-14.9-3.5M4 4v4h4M4 13a8 8 0 0 0 14.9 3.5M20 20v-4h-4',
  spark: 'M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z',
  key: 'M15 7a3 3 0 1 1-2.8 4H4v3h3v-2h2v2h3.2A3 3 0 0 1 15 7Z',
  alert: 'M12 9v4m0 4h.01M10.3 3.9 2.6 17.4A2 2 0 0 0 4.3 20.4h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z',
  external: 'M14 4h6v6m0-6-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5',
  copy: 'M8 8h11v11H8zM5 16V5h11'
}

function Icon({ name, className = 'hv-icon' }) {
  return jsx('svg', {
    className,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.75,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    'aria-hidden': 'true',
    focusable: 'false',
    children: jsx('path', { d: ICONS[name] || ICONS.dumbbell })
  })
}

// -- ask hermes ---------------------------------------------------------------------------------

const wait = ms => new Promise(resolve => (pluginCtx ? pluginCtx.setTimeout(resolve, ms) : resolve()))

export async function askHermes(prompt = REVIEW_PROMPT) {
  const composer = host.composer
  try {
    // A chat composer is on screen (the pane next to a chat): send it there.
    if (composer?.submit?.(null, prompt)) {
      host.notify({ kind: 'success', message: 'Asked Hermes to review your training week.' })
      return true
    }
    // Full-page view: go back to the chat, then send once its composer is mounted.
    host.navigate?.('/')
    for (const delay of [150, 350, 700]) {
      await wait(delay)
      if (composer?.submit?.(null, prompt)) {
        host.notify({ kind: 'success', message: 'Asked Hermes to review your training week.' })
        return true
      }
    }
    if (await composer?.insertText?.('new', prompt, { mode: 'block' })) {
      host.notify({ kind: 'info', message: 'The prompt is in a new chat. Press Enter to send it.' })
      return true
    }
  } catch {
    // fall through to the clipboard
  }
  const copied = await pluginCtx?.os?.writeClipboard?.(prompt)
  host.notify({
    kind: copied ? 'info' : 'warning',
    message: copied
      ? 'Open a chat and paste: the training review prompt is on your clipboard.'
      : 'Open a chat and ask Hermes: "Review my training week".'
  })
  return false
}

function AskButton() {
  const busy = useValue(host.state.busy)
  const [sending, setSending] = useState(false)
  const onClick = async () => {
    setSending(true)
    try {
      await askHermes()
    } finally {
      setSending(false)
    }
  }
  return jsxs('button', {
    type: 'button',
    className: 'hv-ask',
    onClick,
    disabled: sending,
    'aria-busy': sending ? 'true' : 'false',
    title: busy ? 'Hermes is working; the request will follow the current turn.' : 'Sends a prompt to the chat',
    children: [jsx(Icon, { name: 'spark' }), jsx('span', { children: 'Ask Hermes to review my training week' })]
  })
}

// -- pieces -------------------------------------------------------------------------------------

function MetricCard({ card }) {
  const hasValue = card.display != null && card.display !== ''
  const value = hasValue ? `${card.display}${card.unit ? ' ' + card.unit : ''}` : 'no data yet'
  return jsxs('article', {
    className: 'hv-card',
    'aria-label': `${card.label}: ${value}${card.badge ? `, ${card.badge_label || card.badge}` : ''}`,
    children: [
      jsxs('div', {
        className: 'hv-card-head',
        children: [
          jsx('h3', { className: 'hv-card-label', children: card.label }),
          card.badge &&
            jsx('span', { className: 'hv-badge', title: card.badge_label, children: card.badge })
        ]
      }),
      jsxs('div', {
        className: 'hv-card-value',
        children: [
          jsx('span', { className: 'hv-value', children: hasValue ? card.display : '—' }),
          hasValue && card.unit && jsx('span', { className: 'hv-unit', children: card.unit })
        ]
      }),
      card.detail && jsx('p', { className: 'hv-card-detail', children: card.detail })
    ]
  })
}

function ThisWeek({ week }) {
  const cards = week.cards || []
  return jsxs('section', {
    className: 'hv-section',
    'aria-labelledby': 'hv-week-title',
    children: [
      jsxs('h2', {
        id: 'hv-week-title',
        className: 'hv-section-title',
        children: [
          'This week',
          jsx('span', { className: 'hv-section-note', children: `vs your ${week.baseline_label || '4-week average'}` })
        ]
      }),
      jsx('div', {
        className: 'hv-cards',
        children: cards.map((card, i) => jsx(MetricCard, { card }, card.id || i))
      })
    ]
  })
}

function PushPull({ pushPull }) {
  if (!pushPull) return null
  const ratio =
    typeof pushPull.ratio === 'number' && Number.isFinite(pushPull.ratio)
      ? ` · ratio ${num(pushPull.ratio, 2)}`
      : ''
  return jsx('p', {
    className: 'hv-pushpull',
    children: `Push ${setsText(pushPull.push_sets)} · Pull ${setsText(pushPull.pull_sets)} sets${ratio}`
  })
}

function Muscles({ rows, pushPull }) {
  const list = rows || []
  const max = muscleScale(list)
  return jsxs('section', {
    className: 'hv-section',
    'aria-labelledby': 'hv-muscles-title',
    children: [
      jsx('h2', { id: 'hv-muscles-title', className: 'hv-section-title', children: 'Sets per muscle' }),
      list.length
        ? jsx('ul', {
            className: 'hv-muscles',
            children: list.map(row => {
              const hasBaseline = typeof row.baseline_sets === 'number' && Number.isFinite(row.baseline_sets)
              const usual = hasBaseline ? ` · usual ${setsText(row.baseline_sets)}` : ''
              const tick = hasBaseline ? barPercent(row.baseline_sets, max) : null
              return jsxs(
                'li',
                {
                  className: 'hv-muscle',
                  'aria-label': `${row.label}: ${setsText(row.sets)} sets${hasBaseline ? `, usual ${setsText(row.baseline_sets)}` : ''}`,
                  children: [
                    jsxs('div', {
                      className: 'hv-muscle-head',
                      children: [
                        jsx('span', { className: 'hv-muscle-name', children: row.label }),
                        jsx('span', { className: 'hv-muscle-nums', children: `${setsText(row.sets)} sets${usual}` })
                      ]
                    }),
                    jsxs('div', {
                      className: 'hv-bar',
                      'aria-hidden': 'true',
                      children: [
                        jsx('span', { className: 'hv-bar-fill', style: { width: `${barPercent(finite(row.sets), max)}%` } }),
                        tick != null &&
                          jsx('span', { className: 'hv-bar-tick', style: { left: `${tick}%` } })
                      ]
                    })
                  ]
                },
                row.muscle || row.label
              )
            })
          })
        : jsx('p', { className: 'hv-quiet', children: 'No sets logged this week.' }),
      jsx(PushPull, { pushPull })
    ]
  })
}

const SIGNALS_SHOWN = 4

function Signals({ signals }) {
  const [expanded, setExpanded] = useState(false)
  const list = signals || []
  const visible = expanded ? list : list.slice(0, SIGNALS_SHOWN)
  const hidden = list.length - visible.length
  return jsxs('section', {
    className: 'hv-section',
    'aria-labelledby': 'hv-signals-title',
    children: [
      jsx('h2', { id: 'hv-signals-title', className: 'hv-section-title', children: 'Signals' }),
      list.length
        ? jsx('ul', {
            className: 'hv-signals',
            children: visible.map((s, i) =>
              jsxs(
                'li',
                {
                  className: 'hv-signal',
                  'data-tone': s.tone || 'warn',
                  children: [
                    jsx('span', { className: 'hv-signal-dot', 'aria-hidden': 'true' }),
                    jsx('p', { className: 'hv-signal-text', children: s.text }),
                    jsx('span', { className: 'hv-sr', children: '(flagged)' })
                  ]
                },
                `${s.kind || 'signal'}-${i}`
              )
            )
          })
        : jsx('p', {
            className: 'hv-quiet',
            children: 'Nothing unusual: this week looks like your usual training.'
          }),
      (hidden > 0 || expanded) &&
        list.length > SIGNALS_SHOWN &&
        jsx('button', {
          type: 'button',
          className: 'hv-link hv-more',
          'aria-expanded': expanded ? 'true' : 'false',
          onClick: () => setExpanded(!expanded),
          children: expanded ? 'Show fewer' : `Show ${hidden} more`
        })
    ]
  })
}

function Stat({ label, value }) {
  return value
    ? jsxs('div', {
        className: 'hv-stat',
        children: [jsx('dt', { children: label }), jsx('dd', { children: value })]
      })
    : null
}

function LastWorkout({ workout }) {
  const titleId = 'hv-workout-title'
  if (!workout) {
    return jsxs('section', {
      className: 'hv-section',
      'aria-labelledby': titleId,
      children: [
        jsx('h2', { id: titleId, className: 'hv-section-title', children: 'Last workout' }),
        jsx('p', { className: 'hv-quiet', children: 'No workouts logged yet.' })
      ]
    })
  }
  const exercises = workout.exercises || []
  const more = finite(workout.more_exercises)
  const when = whenText(workout.start_local ? `${workout.date}T${workout.start_local}` : workout.date)
  const meta = [when, durationText(workout.duration_hmm)].filter(Boolean).join(' · ')
  const exerciseCount = exercises.length + more
  return jsxs('section', {
    className: 'hv-section',
    'aria-labelledby': titleId,
    children: [
      jsx('h2', { id: titleId, className: 'hv-section-title', children: 'Last workout' }),
      jsxs('div', {
        className: 'hv-workout',
        children: [
          jsxs('div', {
            children: [
              jsx('p', { className: 'hv-workout-name', children: workout.title }),
              meta && jsx('p', { className: 'hv-workout-when', children: meta })
            ]
          }),
          jsxs('dl', {
            className: 'hv-stats',
            children: [
              jsx(Stat, { label: 'Sets', value: workout.working_sets }),
              jsx(Stat, { label: 'Volume', value: workout.volume_kg ? `${num(workout.volume_kg)} kg` : '' }),
              jsx(Stat, { label: 'Exercises', value: exerciseCount || '' })
            ]
          }),
          exercises.length
            ? jsxs('ul', {
                className: 'hv-exercises',
                children: [
                  ...exercises.map((ex, i) =>
                    jsxs(
                      'li',
                      {
                        className: 'hv-exercise',
                        children: [
                          jsx('span', { className: 'hv-exercise-name', children: ex.title }),
                          jsx('span', { className: 'hv-exercise-set', children: ex.best_set || '' })
                        ]
                      },
                      i
                    )
                  ),
                  more > 0 && jsx('li', { className: 'hv-more', children: `+${more} more` })
                ]
              })
            : null
        ]
      })
    ]
  })
}

function RecentPrs({ records }) {
  const days = records?.days || 30
  const items = (records?.items || []).slice(0, 6)
  return jsxs('section', {
    className: 'hv-section',
    'aria-labelledby': 'hv-prs-title',
    children: [
      jsxs('h2', {
        id: 'hv-prs-title',
        className: 'hv-section-title',
        children: ['Recent PRs', jsx('span', { className: 'hv-section-note', children: `last ${days} days` })]
      }),
      items.length
        ? jsx('ul', {
            className: 'hv-prs',
            children: items.map((item, i) =>
              jsxs(
                'li',
                {
                  className: 'hv-pr',
                  children: [
                    jsxs('div', {
                      children: [
                        jsx('p', { className: 'hv-pr-name', children: item.exercise }),
                        jsxs('p', {
                          className: 'hv-pr-value',
                          children: [
                            `${item.kind_label || ''} ${item.display || ''}`.trim(),
                            item.previous_display &&
                              jsx('span', { className: 'hv-pr-prev', children: ` (was ${item.previous_display})` })
                          ]
                        })
                      ]
                    }),
                    item.date &&
                      jsx('time', { className: 'hv-pr-when', dateTime: item.date, children: whenText(item.date) })
                  ]
                },
                `${item.workout_id || item.exercise}-${item.kind}-${i}`
              )
            )
          })
        : jsx('p', { className: 'hv-quiet', children: `No new records in the last ${days} days.` })
    ]
  })
}

function Consistency({ consistency }) {
  const weeks = consistency?.weeks || []
  const max = Math.max(1, ...weeks.map(w => finite(w.sessions)))
  const streak = consistency?.current_streak_weeks
  const avg = consistency?.avg_sessions_per_week
  const since = consistency?.days_since_last_workout
  const usual = consistency?.usual_days
  const stats = []
  if (typeof streak === 'number') stats.push(`Streak ${streak} ${streak === 1 ? 'week' : 'weeks'}`)
  if (typeof avg === 'number' && Number.isFinite(avg)) stats.push(`Avg ${num(avg, 1)} / week`)
  if (typeof since === 'number') {
    stats.push(`Last session: ${since === 0 ? 'today' : since === 1 ? 'yesterday' : `${since} days ago`}`)
  }
  return jsxs('section', {
    className: 'hv-section',
    'aria-labelledby': 'hv-consistency-title',
    children: [
      jsx('h2', { id: 'hv-consistency-title', className: 'hv-section-title', children: 'Consistency' }),
      weeks.length
        ? jsx('div', {
            className: 'hv-weeks',
            children: weeks.map((w, i) => {
              const n = finite(w.sessions)
              const current = i === weeks.length - 1
              return jsx('div', {
                className: 'hv-week',
                role: 'img',
                'data-current': current ? 'true' : 'false',
                'aria-label': `Week of ${weekLabel(w.week_start)}: ${n} ${n === 1 ? 'session' : 'sessions'}`,
                children: jsx('span', { className: 'hv-week-fill', style: { height: `${barPercent(n, max)}%` } })
              }, w.week_start || i)
            })
          })
        : jsx('p', { className: 'hv-quiet', children: 'No training history yet.' }),
      stats.length
        ? jsx('ul', { className: 'hv-cstats', children: stats.map(s => jsx('li', { children: s }, s)) })
        : null,
      Array.isArray(usual) &&
        usual.length > 0 &&
        jsx('p', { className: 'hv-usual', children: `Usual days: ${usual.join(', ')}` })
    ]
  })
}

function Header({ date, updatedAt, syncedAt, fetching, onRefresh }) {
  // The last Hevy sync matters more than the panel's fetch time: the logged workouts stop there.
  const updated = syncedAt
    ? `synced ${timeText(syncedAt)}`
    : updatedAt
      ? `updated ${clockText(updatedAt)}`
      : ''
  const sub = date
    ? [longDate(date), updated].filter(Boolean).join(' · ')
    : 'Hevy (unofficial)'
  return jsxs('header', {
    className: 'hv-header',
    children: [
      jsx('span', { className: 'hv-mark', children: jsx(Icon, { name: 'dumbbell' }) }),
      jsxs('div', {
        className: 'hv-title',
        children: [jsx('h1', { children: 'Hevy' }), jsx('p', { children: sub })]
      }),
      onRefresh &&
        jsx('button', {
          type: 'button',
          className: 'hv-icon-button',
          onClick: onRefresh,
          disabled: fetching,
          'aria-label': fetching ? 'Refreshing Hevy data' : 'Refresh Hevy data',
          title: 'Refresh',
          children: jsx(Icon, { name: 'refresh', className: fetching ? 'hv-icon hv-spin' : 'hv-icon' })
        })
    ]
  })
}

function Loading() {
  return jsxs('div', {
    className: 'hv-body',
    'aria-busy': 'true',
    children: [
      jsx('p', { className: 'hv-sr', role: 'status', children: 'Loading your Hevy data' }),
      jsx('div', {
        className: 'hv-cards',
        children: Array.from({ length: 4 }, (_, i) => jsx('div', { className: 'hv-card hv-skeleton' }, i))
      }),
      jsx('div', { className: 'hv-skeleton hv-skeleton-wide' })
    ]
  })
}

function openKeyPage() {
  pluginCtx?.os?.openExternal?.(KEY_PAGE).then(ok => {
    if (!ok) host.notify({ kind: 'info', message: KEY_PAGE })
  })
}

// Opens this plugin's settings page (the schema form with the key field), for the profile the panel shows.
export function keySettingsPath(profile) {
  const params = new URLSearchParams({ tab: 'plugins', agent: ID })
  if (profile) params.set('profile', profile)
  return `/settings?${params}`
}

function openKeySettings(profile) {
  try {
    if (typeof host.navigate === 'function') {
      host.navigate(keySettingsPath(profile))
      return
    }
  } catch {
    // fall through to the written path
  }
  host.notify({ kind: 'info', message: `Open ${SETTINGS_PATH_TEXT} and paste your key in “${KEY_FIELD}”.` })
}

function CopyCommand() {
  const command = `hermes config set ${KEY_ENV} <your key>`
  const [copied, setCopied] = useState(false)
  return jsxs('div', {
    className: 'hv-command',
    children: [
      jsx('code', { children: command }),
      jsx('button', {
        type: 'button',
        className: 'hv-icon-button',
        'aria-label': copied ? 'Command copied' : 'Copy command',
        title: copied ? 'Copied' : 'Copy',
        onClick: async () => setCopied(Boolean(await pluginCtx?.os?.writeClipboard?.(command))),
        children: jsx(Icon, { name: 'copy' })
      })
    ]
  })
}

function TerminalHint() {
  return jsxs('details', {
    className: 'hv-terminal',
    children: [
      jsx('summary', { children: 'Using the terminal?' }),
      jsx('p', {
        className: 'hv-fineprint',
        children: 'Run this instead (start the line with a space to keep the key out of your shell history):'
      }),
      jsx(CopyCommand, {})
    ]
  })
}

function Onboarding({ state, message, onRetry }) {
  const profile = useValue(host.state.profile)
  const rejected = state === 'auth_error'
  return jsxs('div', {
    className: 'hv-body hv-onboarding',
    children: [
      jsxs('div', {
        className: 'hv-callout',
        'data-tone': rejected ? 'bad' : 'neutral',
        role: rejected ? 'alert' : undefined,
        children: [
          jsx(Icon, { name: rejected ? 'alert' : 'key' }),
          jsxs('div', {
            children: [
              jsx('p', {
                className: 'hv-callout-title',
                children: rejected ? 'Hevy rejected your API key' : 'Connect your Hevy account'
              }),
              jsx('p', {
                children: rejected
                  ? 'It may have been revoked, or your Hevy Pro subscription has ended. Create a new key and paste it in Hermes in place of the old one.'
                  : 'The panel reads your own training log through Hevy’s API. It needs an API key from a Hevy Pro account; your password is never used.'
              })
            ]
          })
        ]
      }),
      jsxs('ol', {
        className: 'hv-steps',
        children: [
          jsxs('li', {
            children: [
              jsx('p', {
                children: 'In the Hevy web app, open Settings › Developer and create an API key. The API needs Hevy Pro.'
              }),
              jsxs('button', {
                type: 'button',
                className: 'hv-button',
                onClick: openKeyPage,
                children: [jsx('span', { children: 'Open the key page' }), jsx(Icon, { name: 'external' })]
              })
            ]
          }),
          jsxs('li', {
            children: [
              jsx('p', { children: `Paste it in “${KEY_FIELD}” and select Save settings. Never paste it into a chat.` }),
              jsxs('button', {
                type: 'button',
                className: 'hv-button hv-button-primary',
                onClick: () => openKeySettings(profile),
                children: [jsx(Icon, { name: 'key' }), jsx('span', { children: 'Add your API key' })]
              }),
              jsx('p', { className: 'hv-fineprint hv-path', children: `It is in ${SETTINGS_PATH_TEXT}.` }),
              jsx(TerminalHint, {})
            ]
          }),
          jsxs('li', {
            children: [
              jsx('p', { children: 'Come back here and check again.' }),
              jsx('button', { type: 'button', className: 'hv-button', onClick: onRetry, children: 'Check again' })
            ]
          })
        ]
      }),
      message && jsx('p', { className: 'hv-fineprint', children: message })
    ]
  })
}

function ErrorView({ title, message, hint, onRetry }) {
  return jsxs('div', {
    className: 'hv-body',
    children: [
      jsxs('div', {
        className: 'hv-callout',
        'data-tone': 'bad',
        role: 'alert',
        children: [
          jsx(Icon, { name: 'alert' }),
          jsxs('div', {
            children: [
              jsx('p', { className: 'hv-callout-title', children: title }),
              message && jsx('p', { children: message }),
              hint && jsx('p', { className: 'hv-fineprint', children: hint })
            ]
          })
        ]
      }),
      jsx('button', { type: 'button', className: 'hv-button', onClick: onRetry, children: 'Try again' })
    ]
  })
}

function Footer() {
  return jsx('footer', {
    className: 'hv-footer',
    children: jsx('p', {
      children: 'Unofficial plugin · your data from the Hevy API (Hevy Pro) · not medical advice'
    })
  })
}

// -- panel --------------------------------------------------------------------------------------

export function HevyPanel() {
  const { data, error, isLoading, isFetching, dataUpdatedAt, refresh, refetch } = useOverview()
  const state = data?.state
  let body
  if (isLoading) body = jsx(Loading, {})
  else if (error)
    body = jsx(ErrorView, {
      title: 'The Hevy backend is not reachable',
      message: 'Turn the plugin’s agent half on: in Capabilities › Plugins, switch on Agent in the Hevy row, then select Try again. If it still fails, quit and reopen Hermes.',
      hint: String(error.message || error),
      onRetry: () => refetch()
    })
  else if (state === 'not_configured' || state === 'auth_error')
    body = jsx(Onboarding, { state, message: state === 'auth_error' ? data.message : '', onRetry: refresh })
  else if (state && state !== 'ok')
    body = jsx(ErrorView, {
      title:
        state === 'plan_required'
          ? 'Hevy Pro is needed'
          : state === 'rate_limited'
            ? 'Hevy is busy'
            : 'Could not load your Hevy data',
      message: data.message,
      onRetry: refresh
    })
  else if (data) {
    const week = data.week || {}
    body = jsxs('div', {
      className: 'hv-body',
      children: [
        jsx(ThisWeek, { week }),
        jsx(AskButton, {}),
        jsxs('div', {
          className: 'hv-columns',
          children: [
            jsxs('div', {
              className: 'hv-side',
              children: [
                jsx(Muscles, { rows: week.muscles, pushPull: week.push_pull }),
                jsx(Signals, { signals: data.signals })
              ]
            }),
            jsxs('div', {
              className: 'hv-side',
              children: [
                jsx(LastWorkout, { workout: data.last_workout }),
                jsx(RecentPrs, { records: data.records }),
                jsx(Consistency, { consistency: data.consistency })
              ]
            })
          ]
        }),
        jsx(Footer, {})
      ]
    })
  }
  return jsxs('div', {
    className: 'hv-root',
    role: 'region',
    'aria-label': 'Hevy training',
    'aria-busy': isFetching ? 'true' : 'false',
    children: [
      jsx('style', { children: CSS }),
      jsx(Header, {
        date: state === 'ok' ? data.date : '',
        updatedAt: state === 'ok' ? dataUpdatedAt : 0,
        syncedAt: state === 'ok' ? data.synced_at : '',
        fetching: isFetching,
        onRefresh: refresh
      }),
      body
    ]
  })
}

// -- styles -------------------------------------------------------------------------------------

const CSS = `
.hv-root{--hv-line:var(--ui-stroke-secondary);--hv-soft:var(--ui-stroke-tertiary,var(--ui-stroke-secondary));
  height:100%;min-height:0;overflow:auto;container-type:inline-size;color:var(--ui-text-primary,var(--foreground));font-size:13px;line-height:1.45}
.hv-root *{box-sizing:border-box}
.hv-root :focus-visible{outline:2px solid var(--ui-accent);outline-offset:2px;border-radius:6px}
.hv-root h1,.hv-root h2,.hv-root h3,.hv-root p{margin:0}
.hv-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.hv-icon{width:16px;height:16px;flex:none}
.hv-spin{animation:hv-spin .9s linear infinite}
@keyframes hv-spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.hv-spin,.hv-skeleton{animation:none!important}}

.hv-header{position:sticky;top:0;z-index:1;display:flex;align-items:center;gap:10px;padding:14px 16px 12px;background:var(--ui-surface-background,var(--background));border-bottom:1px solid var(--hv-soft)}
.hv-mark{width:30px;height:30px;flex:none;display:flex;align-items:center;justify-content:center;border-radius:9px;color:var(--ui-accent);background:color-mix(in srgb,var(--ui-accent) 14%,transparent)}
.hv-mark .hv-icon{width:18px;height:18px}
.hv-title{flex:1;min-width:0}
.hv-title h1{font-size:15px;font-weight:600;letter-spacing:-.01em}
.hv-title p{font-size:12px;color:var(--ui-text-tertiary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hv-icon-button{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;flex:none;border:0;border-radius:7px;background:transparent;color:var(--ui-text-secondary);cursor:pointer}
.hv-icon-button:hover{background:var(--ui-control-hover-background);color:var(--ui-text-primary,var(--foreground))}
.hv-icon-button:disabled{cursor:default;opacity:.7}

.hv-body{display:flex;flex-direction:column;gap:16px;padding:14px 16px 18px}
.hv-section{display:flex;flex-direction:column;gap:8px;min-width:0}
.hv-section-title{display:flex;align-items:baseline;gap:8px;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--ui-text-tertiary)}
.hv-section-note{font-weight:400;letter-spacing:0;text-transform:none;color:var(--ui-text-quaternary,var(--ui-text-tertiary))}
.hv-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.hv-card{position:relative;display:flex;flex-direction:column;min-width:0;padding:10px 12px 8px;border:1px solid var(--hv-line);border-radius:10px;background:var(--ui-bg-card,transparent)}
.hv-card-head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:2px 6px;min-width:0}
.hv-card-label{font-size:11px;font-weight:500;letter-spacing:.02em;color:var(--ui-text-tertiary);white-space:nowrap}
.hv-card-value{display:flex;align-items:baseline;gap:4px;margin-top:4px}
.hv-value{font-size:22px;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.15}
.hv-unit{font-size:12px;color:var(--ui-text-tertiary)}
.hv-card-detail{margin-top:4px;font-size:11.5px;color:var(--ui-text-secondary);overflow-wrap:anywhere}
.hv-badge{flex:none;display:inline-flex;align-items:center;padding:1px 6px;border-radius:999px;font-size:10.5px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--ui-text-secondary);background:var(--ui-control-hover-background)}

.hv-ask{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;min-height:36px;padding:8px 14px;border:0;border-radius:9px;font:inherit;font-weight:600;cursor:pointer;color:var(--background);background:var(--ui-accent);transition:filter .15s}
.hv-ask:hover{filter:brightness(1.08)}
.hv-ask:disabled{cursor:progress;opacity:.75}

.hv-columns{display:grid;grid-template-columns:minmax(0,1fr);gap:16px}
.hv-side{display:flex;flex-direction:column;gap:16px;min-width:0}
.hv-muscles{display:flex;flex-direction:column;gap:10px;margin:0;padding:0;list-style:none}
.hv-muscle-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:12.5px}
.hv-muscle-name{font-weight:500;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hv-muscle-nums{flex:none;font-size:11.5px;color:var(--ui-text-tertiary);font-variant-numeric:tabular-nums;white-space:nowrap}
.hv-bar{position:relative;height:8px;margin-top:5px;border-radius:999px;background:var(--ui-control-hover-background)}
.hv-bar-fill{position:absolute;left:0;top:0;bottom:0;border-radius:999px;background:var(--ui-accent)}
.hv-bar-tick{position:absolute;top:-3px;bottom:-3px;width:2px;margin-left:-1px;border-radius:1px;background:var(--ui-text-secondary)}
.hv-pushpull{font-size:12px;color:var(--ui-text-secondary);font-variant-numeric:tabular-nums}
.hv-signals{display:flex;flex-direction:column;gap:6px;margin:0;padding:0;list-style:none}
.hv-signal{display:flex;align-items:flex-start;gap:10px;padding:9px 12px;border-radius:9px;background:color-mix(in srgb,var(--hv-tone) 8%,transparent);border:1px solid color-mix(in srgb,var(--hv-tone) 22%,transparent);--hv-tone:var(--ui-yellow,var(--ui-text-tertiary))}
.hv-signal-dot{width:8px;height:8px;margin-top:6px;flex:none;border-radius:50%;background:var(--hv-tone)}
.hv-signal-text{font-weight:500;overflow-wrap:anywhere}
.hv-quiet{padding:10px 12px;border:1px dashed var(--hv-line);border-radius:9px;color:var(--ui-text-tertiary)}

.hv-workout{display:flex;flex-direction:column;gap:10px;padding:12px;border:1px solid var(--hv-line);border-radius:10px;background:var(--ui-bg-card,transparent)}
.hv-workout-name{font-weight:600;overflow-wrap:anywhere}
.hv-workout-when{font-size:12px;color:var(--ui-text-tertiary)}
.hv-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px 10px;margin:0}
.hv-stat{min-width:0}
.hv-stat dt{font-size:10.5px;color:var(--ui-text-tertiary)}
.hv-stat dd{margin:0;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
.hv-exercises{display:flex;flex-direction:column;gap:6px;margin:0;padding:0;list-style:none}
.hv-exercise{display:flex;justify-content:space-between;align-items:baseline;gap:10px;font-size:12.5px}
.hv-exercise-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hv-exercise-set{flex:none;font-variant-numeric:tabular-nums;color:var(--ui-text-secondary)}
.hv-more{font-size:12px;color:var(--ui-text-tertiary)}

.hv-prs{display:flex;flex-direction:column;gap:6px;margin:0;padding:0;list-style:none}
.hv-pr{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;padding:9px 12px;border:1px solid var(--hv-line);border-radius:9px}
.hv-pr-name{font-weight:500;overflow-wrap:anywhere}
.hv-pr-value{font-size:12px;color:var(--ui-text-secondary);font-variant-numeric:tabular-nums}
.hv-pr-prev{color:var(--ui-text-tertiary)}
.hv-pr-when{flex:none;font-size:11.5px;color:var(--ui-text-tertiary);white-space:nowrap}

.hv-weeks{display:flex;align-items:flex-end;gap:4px;height:64px}
.hv-week{flex:1;min-width:0;height:100%;display:flex;align-items:flex-end;border-radius:4px;background:var(--ui-control-hover-background);overflow:hidden}
.hv-week-fill{display:block;width:100%;border-radius:4px 4px 0 0;background:color-mix(in srgb,var(--ui-accent) 50%,transparent)}
.hv-week[data-current=true] .hv-week-fill{background:var(--ui-accent)}
.hv-week[data-current=true]{outline:1px solid var(--ui-text-tertiary);outline-offset:1px}
.hv-cstats{display:flex;flex-wrap:wrap;gap:4px 14px;margin:0;padding:0;list-style:none;font-size:12px;color:var(--ui-text-secondary);font-variant-numeric:tabular-nums}
.hv-usual{font-size:11.5px;color:var(--ui-text-tertiary)}

.hv-footer{padding-top:12px;border-top:1px dashed var(--hv-soft);font-size:11px;color:var(--ui-text-quaternary,var(--ui-text-tertiary))}
.hv-link{border:0;padding:0;background:none;font:inherit;color:var(--ui-accent);cursor:pointer;text-decoration:underline;text-underline-offset:2px}
.hv-fineprint{font-size:11.5px;color:var(--ui-text-tertiary);overflow-wrap:anywhere}

.hv-callout{display:flex;gap:10px;padding:12px;border-radius:10px;border:1px solid color-mix(in srgb,var(--hv-tone) 28%,transparent);background:color-mix(in srgb,var(--hv-tone) 8%,transparent);--hv-tone:var(--ui-accent)}
.hv-callout>.hv-icon{margin-top:2px;color:var(--hv-tone)}
.hv-callout[data-tone=bad]{--hv-tone:var(--ui-red,currentColor)}
.hv-callout p{color:var(--ui-text-secondary)}
.hv-callout .hv-callout-title{font-weight:600;color:var(--ui-text-primary,var(--foreground));margin-bottom:2px}
.hv-steps{display:flex;flex-direction:column;gap:14px;margin:0;padding:0 0 0 20px}
.hv-steps li{padding-left:2px}
.hv-steps li::marker{color:var(--ui-text-tertiary);font-weight:600}
.hv-steps li>p{margin-bottom:6px;color:var(--ui-text-secondary)}
.hv-button{align-self:flex-start;display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border:1px solid var(--hv-line);border-radius:8px;font:inherit;font-weight:500;cursor:pointer;color:var(--ui-text-primary,var(--foreground));background:transparent}
.hv-button:hover{background:var(--ui-control-hover-background)}
.hv-button .hv-icon{width:14px;height:14px}
.hv-button-primary{border-color:transparent;color:var(--background);background:var(--ui-accent)}
.hv-button-primary:hover{background:var(--ui-accent);filter:brightness(1.08)}
.hv-command{display:flex;align-items:center;gap:6px;padding:4px 4px 4px 10px;border:1px solid var(--hv-line);border-radius:8px;background:var(--ui-inline-code-background,transparent)}
.hv-command code{flex:1;min-width:0;font-family:var(--font-mono,ui-monospace,monospace);font-size:11.5px;overflow-wrap:anywhere}
.hv-path{margin-top:6px}
.hv-terminal{margin-top:8px}
.hv-terminal summary{cursor:pointer;width:max-content;font-size:11.5px;color:var(--ui-text-tertiary)}
.hv-terminal summary:hover{color:var(--ui-text-secondary)}
.hv-terminal[open] summary{margin-bottom:6px}
.hv-terminal .hv-fineprint{margin-bottom:6px}

.hv-skeleton{min-height:104px;border-color:transparent;background:var(--ui-bg-quaternary,var(--ui-control-hover-background));animation:hv-pulse 1.4s ease-in-out infinite}
.hv-skeleton-wide{min-height:36px;border-radius:9px}
@keyframes hv-pulse{50%{opacity:.55}}

@container (min-width:560px){
  .hv-cards{grid-template-columns:repeat(4,minmax(0,1fr))}
  .hv-body{padding:18px 22px 22px;gap:18px}
  .hv-header{padding:16px 22px 14px}
  .hv-ask{width:auto;align-self:flex-start}
}
@container (min-width:900px){
  .hv-columns{grid-template-columns:minmax(0,1.2fr) minmax(0,1fr)}
  .hv-body{max-width:1280px}
}
`

// -- registration -------------------------------------------------------------------------------

export default {
  id: ID,
  name: 'Hevy',
  register(ctx) {
    pluginCtx = ctx
    ctx.registerMany([
      {
        id: 'pane',
        area: PANES_AREA,
        title: 'Hevy',
        data: { placement: 'right', width: '380px' },
        render: () => jsx(HevyPanel, {})
      },
      { id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => jsx(HevyPanel, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: ROUTE, label: 'Hevy', codicon: 'flame' } },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'hevy.open',
          label: 'Hevy: Open training overview',
          keywords: ['hevy', 'workout', 'gym', 'training', 'strength'],
          run: () => host.navigate(ROUTE)
        }
      },
      {
        id: 'review',
        area: PALETTE_AREA,
        data: {
          id: 'hevy.review',
          label: 'Hevy: Ask Hermes to review my training week',
          keywords: ['hevy', 'training', 'review', 'workout', 'volume', 'sets'],
          run: () => void askHermes()
        }
      }
    ])
    // A new profile or backend means another key: drop what the panel cached.
    ctx.onEvent('gateway.ready', () => queryClient.invalidateQueries({ queryKey: [ID] }))
    ctx.onDispose(() => {
      queryClient.removeQueries({ queryKey: [ID] })
      pluginCtx = null
    })
  }
}
