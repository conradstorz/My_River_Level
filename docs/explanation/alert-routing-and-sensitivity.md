# Alert routing and sensitivity

Alerts are routed per landing page, not broadcast. A USGS transition,
reminder, or trend alert reaches only the active subscribers of the pages
that link that site through `page_sites`; a NOAA category change reaches
only the active subscribers of the pages that link that gauge through
`page_noaa_gauges`. `db/models.py`'s `get_page_subscribers_for_site` is the
query behind the USGS side of this: it joins `page_subscribers` to
`page_sites` and `user_pages`, and only returns a subscriber whose page is
"live" — its admin `active` flag is 1 and its lifecycle `status` is
`'active'`. A paused, stopped, or still-pending page's subscribers are
invisible to this query no matter how severe the condition. The global
`subscribers` table plays no part in any of this; it exists solely for the
admin Broadcast form, which sends to every globally opted-in subscriber on
selected channels regardless of which pages or gauges they care about.

Whether an individual live subscriber actually receives a given alert is a
second question, decided by `monitor/scheduler.py`'s `alert_allowed`, which
checks the page's `sensitivity` dial against the alert's type and severity.
There are three dials, each a strict superset of the one below it:

| Alert | `floods` | `unusual` | `all` |
|---|---|---|---|
| NOAA flood-category change | yes | yes | yes |
| USGS SEVERE HIGH transition, either direction (including the all-clear back to NORMAL) | yes | yes | yes |
| USGS SEVERE HIGH reminder | yes | yes | yes |
| USGS HIGH / LOW / SEVERE LOW transition or reminder, and the return to NORMAL | — | yes | yes |
| Rise/fall (trend) alert | — | — | yes |

A `floods` page hears about NOAA changes unconditionally, and on the USGS
side only ever hears SEVERE HIGH — including its own all-clear, since a
transition is admitted when either its new severity or its previous severity
qualifies, so the drop back to NORMAL still reaches a page that heard the
SEVERE HIGH begin. `unusual` adds every other USGS band, HIGH/LOW/SEVERE LOW
and their reminders and transitions. Its all-clear works differently than
`floods`'s, though: `unusual`'s severity set includes NORMAL outright, so a
HIGH/LOW/SEVERE LOW page on that dial is admitted by the new severity alone,
where `floods` has no NORMAL in its set and reaches its all-clear only
through the `previous_severity` fallback. `all` adds nothing on the USGS side
beyond `unusual` except trend alerts, which are gated so strictly that only
`all` ever sees them. An unrecognised sensitivity value is treated as
`unusual` rather than silencing the page outright.

The two default dials differ by how a page was created. A page created by an
operator on the portal starts at `all`, so existing admin pages keep hearing
everything they always did. A pin page created through the public onboarding
flow starts at `unusual` — enough to be useful without opting a stranger
into every rise-and-fall alert by default.

A page's `status` governs both whether its subscribers can be reached at all
and whether its sources keep being polled on its behalf. `pending` is a page
that has been created but has no pin yet (or, for a web-first page, has a
pin but has not been claimed by a Telegram chat) — its sources may already
exist but no subscriber can hear from it. `active` is live and fully
reachable. `paused` (`/pause`) keeps the page's sources polled and its
subscription intact but silences alerts, and `/resume` reverses it exactly.
`stopped` (`/stop`) closes the page for good; its subscribers stop hearing
from it and its sources become eligible for the retirement sweep described in
[`source-retirement.md`](source-retirement.md).

`monitor/dispatcher.py`'s `NotificationDispatcher` is where all of this
converges: it dequeues one item, formats its message once, looks up every
live subscriber for the affected site or gauge, and calls `alert_allowed` per
subscriber before sending. Filtering happens here rather than in the pollers
because the same gauge can be watched by many pages at many different
sensitivities — evaluating it once upstream and fanning the decision out at
dispatch means a gauge shared by ten pages still costs one poll, not ten, no
matter how differently each page's subscribers have tuned their dial.

There is one message type this whole mechanism does not touch: `direct`.
It is queued by the pin-save route itself, addressed to exactly one Telegram
chat — the page's owner — as the confirmation sent immediately after saving
a pin, listing what was picked. It bypasses `page_subscribers` and
`alert_allowed` entirely, since it is not an alert about a changing
condition; it is a receipt.
