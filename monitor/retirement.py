"""Retire user-provisioned sources nobody references any more.

A pin page provisions USGS sites and NOAA gauges automatically (origin =
'user'). When every page that referenced one has been stopped, polling it
would be wasted API calls, so this sweep switches it off. Rows are never
deleted: history and conditions stay, and re-selecting the source flips it
back on. Admin-origin rows are never touched.

A page counts as a live reference when it is 'active' or 'paused', or when
it is still 'pending' but already has a pin saved (a web-first page awaiting
its Telegram deep-link bind). Pending pages with no pin are deleted after 24
hours so an abandoned /start does not accumulate rows forever; pending pages
that do have a pin but are never bound to Telegram are deleted after a week,
after which their sources retire on the following sweep.
"""

import logging

from db.models import get_db

logger = logging.getLogger(__name__)

PENDING_PAGE_MAX_AGE_HOURS = 24
PENDING_PINNED_MAX_AGE_HOURS = 168

_LIVE_PAGE = "(up.status IN ('active', 'paused') OR (up.status = 'pending' AND up.pin_lat IS NOT NULL))"


def sweep(db_path=None):
    """Deactivate unreferenced user sources and drop stale pending pages.

    Returns {"sites": n, "gauges": n, "pending_pages": n}.
    """
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            f"""UPDATE sites s SET active = 0
               WHERE s.origin = 'user' AND s.active = 1
                 AND s.added_at::timestamptz < NOW() - INTERVAL '10 minutes'
                 AND NOT EXISTS (
                     SELECT 1 FROM page_sites ps
                     JOIN user_pages up ON up.id = ps.page_id
                     WHERE ps.site_id = s.id AND {_LIVE_PAGE})"""
        )
        sites = cur.rowcount
        cur.execute(
            f"""UPDATE noaa_gauges g SET active = 0
               WHERE g.origin = 'user' AND g.active = 1
                 AND NOT EXISTS (
                     SELECT 1 FROM page_noaa_gauges png
                     JOIN user_pages up ON up.id = png.page_id
                     WHERE png.noaa_gauge_id = g.id AND {_LIVE_PAGE})"""
        )
        gauges = cur.rowcount
        cur.execute(
            """SELECT id FROM user_pages
               WHERE (status = 'pending' AND pin_lat IS NULL
                      AND created_at::timestamptz < NOW() - (%s * INTERVAL '1 hour'))
                  OR (status = 'pending' AND pin_lat IS NOT NULL
                      AND created_at::timestamptz < NOW() - (%s * INTERVAL '1 hour'))""",
            (PENDING_PAGE_MAX_AGE_HOURS, PENDING_PINNED_MAX_AGE_HOURS)
        )
        stale_ids = [r["id"] for r in cur.fetchall()]
        if stale_ids:
            cur.execute("DELETE FROM page_subscribers WHERE page_id = ANY(%s)", (stale_ids,))
            cur.execute("DELETE FROM page_sites WHERE page_id = ANY(%s)", (stale_ids,))
            cur.execute("DELETE FROM page_noaa_gauges WHERE page_id = ANY(%s)", (stale_ids,))
            cur.execute("DELETE FROM user_pages WHERE id = ANY(%s)", (stale_ids,))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    result = {"sites": sites, "gauges": gauges, "pending_pages": len(stale_ids)}
    if any(result.values()):
        logger.info("Retirement sweep: %s", result)
    return result
