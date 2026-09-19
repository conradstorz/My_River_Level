# Adding gauges as an admin

1. Open the Sites page.
   ```
   http://<portal-host>:5743/sites
   ```
   Expected: **Monitored Sites**, with a name-search form and an
   "Add Site by USGS Number" form.

2. Add a gauge you already know the number for: enter its 8-digit **USGS
   Site #** (e.g. `03293000`), an optional station name, and pick the
   **Parameter** (Discharge `00060` or Gage height `00065`), then **Add**.
   Expected: a "Site \<number\> (\<name\>) added." banner and a new row in
   the table below.

3. Or find it by name instead: type keywords in any order into "Find a Gauge
   by Name" (e.g. `louisville ohio river`) and **Search**.
   Expected: a paginated, combined USGS+NOAA results table — a
   `matched NOAA: <name>` badge on a row means that USGS gauge also has a
   NOAA flood forecast; each row has its own **Add** button and parameter
   picker.

4. Read the health badge on an already-added site. **Reporting** means its
   last USGS fetch succeeded; **⚠ Not reporting** means the last successful
   fetch is older than `site_stale_hours` (6 hours by default) — hover the
   badge for the specific error, or see
   [`../reference/settings.md`](../reference/settings.md).

5. Create a landing page to attach gauges to and share with subscribers.
   ```
   http://<portal-host>:5743/pages/new
   ```
   Enter a page name and **Create Page**. Expected: a **Created!** screen
   showing a public view link (`/view/<public_token>`) and an edit link
   (`/edit/<edit_token>`) — copy both now; the edit link is not shown again.

6. Open the edit link and attach a NOAA gauge by name: search "Flood gauges
   (NOAA)" (e.g. `mcalpine upper`) and **Add** a result, or type its LID
   directly (e.g. `MLUK2`) under "…or enter a NOAA LID directly" and
   **Add by ID**. A USGS site number also resolves to its NOAA LID here, so
   entering one in the LID field works too.
   Expected: the gauge appears under "Flood gauges (NOAA)" with its
   flood-prediction grade badge (see
   [`../explanation/gauge-quality-grading.md`](../explanation/gauge-quality-grading.md)).

7. Attach a USGS river gauge the same way, under "River gauges (USGS)":
   enter its site number and parameter, then **Add**.
   Expected: the gauge appears in that card's list; this is what
   `page_sites` routes USGS alerts through — see
   [`../explanation/alert-routing-and-sensitivity.md`](../explanation/alert-routing-and-sensitivity.md).

8. Subscribe a recipient under "Subscribe to Alerts": pick a **Channel**
   (SMS, WhatsApp, Telegram, Facebook Messenger), enter their phone number,
   chat ID, or PSID, and **Subscribe**.
   Expected: the recipient appears under "Active Subscribers" and starts
   receiving this page's alerts (subject to its sensitivity, below).

9. Set the sensitivity dial under "Alert sensitivity" — **Floods only**,
   **Floods and unusual levels**, or **Everything, including rapid
   changes** — and **Save**.
   Expected: the radio selection updates; see
   [`../explanation/alert-routing-and-sensitivity.md`](../explanation/alert-routing-and-sensitivity.md)
   for exactly which alert types each level admits.

10. Manage the page from the admin list.
    ```
    http://<portal-host>:5743/admin/pages
    ```
    Expected: **User Pages**, listing every page with its gauge, site, and
    active-subscriber counts.

11. Switch a page off or on with its **Disable** / **Enable** button in that list (the *Active* / *Disabled* badge beside it only shows the current state).
    Expected: an inactive page's `/view/<public_token>` starts returning 404
    to visitors; its subscribers stop hearing from it, but the page itself
    and its gauges are untouched.

## How this differs from pin pages

A page created here has `origin` `admin` and starts at sensitivity `all`, so
it hears every alert type by default. Its sources are never touched by the
retirement sweep no matter how many pages stop referencing them — only
`user`-origin sources (the ones a public pin page provisions automatically)
are ever deactivated that way. See
[`../explanation/source-retirement.md`](../explanation/source-retirement.md).
An admin page also has no owning Telegram chat and no lifecycle beyond its
`active` flag — it is never `pending`, `paused`, or `stopped`, so it cannot
be closed with `/stop` the way a pin page can; the **Active** toggle here is
the only way to take one down.

## If it went wrong

- No alerts arrive — [`diagnose.md#no-alerts-arrive`](diagnose.md#no-alerts-arrive)
- Site shows "Not reporting" — [`diagnose.md#site-shows-not-reporting`](diagnose.md#site-shows-not-reporting)
