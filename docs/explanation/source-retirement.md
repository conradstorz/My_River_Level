# Source retirement

Every USGS site and NOAA gauge has an `origin`: `admin` for one added by an
operator on the portal Sites page or a page editor, `user` for one
provisioned automatically by the pin-onboarding flow. Only `user`-origin
rows are ever touched by `monitor/retirement.py`'s `sweep` — an operator who
added a gauge by hand will never find it silently deactivated because a pin
page stopped using it.

## What counts as live

A page counts as a live reference to its sources when its lifecycle
`status` is `active` or `paused`, or when it is still `pending` but already
has a pin saved — a web-first page that has picked its gauges but is still
waiting to be claimed by a Telegram chat. A `pending` page with no pin yet,
or a `stopped` page, is not live: nothing it links to counts toward keeping
that source active.

## Garbage collection

`sweep` runs two passes over `user`-origin rows. A USGS site is deactivated
once it has no live page referencing it *and* it is more than ten minutes
old — the age check exists so a source a visitor just picked, before its
page's link rows have all committed, is never caught mid-save. A NOAA gauge
is deactivated the moment it has no live reference at all, with no such
grace window. Neither pass deletes anything: a deactivated row keeps its
history and its stored conditions, and the moment someone selects that same
source again, saving the pin reactivates it in place rather than creating a
duplicate. Admin-origin rows never appear in either query, so they are never
deactivated by this sweep no matter how many pages stop referencing them.

The same pass also deletes — not deactivates — pending pages that were
themselves abandoned: one with no pin at all after 24 hours, or one with a
pin but never claimed by Telegram after 7 days. Deleting a stale pending
page removes its subscriber and source-link rows immediately, which is what
lets its sources be swept as unreferenced on the sweep's next hourly run.

`sweep` is called from `SchedulerThread`, once an hour, right after that
pass's reminder check. If a sweep raises, the failure is logged and the
thread's internal sweep timer is left untouched, so the very next five-minute
reminder pass retries the sweep rather than waiting out the rest of the hour
with stale rows still active.

The Sites page's per-site subscriber count only reflects this same notion of
"live": it counts pages linking a site whose `status` is `active` or
`paused`, so a site referenced only by a still-`pending` page reads as
unreferenced there even though the retirement sweep itself will not yet
touch it.
