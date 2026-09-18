"""Retire user-provisioned sources nobody references any more.

A pin page provisions USGS sites and NOAA gauges automatically (origin =
'user'). When every page that referenced one has been stopped, polling it
would be wasted API calls, so this sweep switches it off. Rows are never
deleted: history and conditions stay, and re-selecting the source flips it
back on. Admin-origin rows are never touched.

Pending pages that never received a pin are deleted after 24 hours so an
abandoned /start does not accumulate rows forever.
"""

import logging

from db.models import get_db

logger = logging.getLogger(__name__)

PENDING_PAGE_MAX_AGE_HOURS = 24

_LIVE_STATUSES = ("active", "paused")


def sweep(db_path=None):
    """Deactivate unreferenced user sources and drop stale pending pages.

    Returns {"sites": n, "gauges": n, "pending_pages": n}.
    """
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE sites s SET active = 0
               WHERE s.origin = 'user' AND s.active = 1
                 AND NOT EXISTS (
                     SELECT 1 FROM page_sites ps
                     JOIN user_pages up ON up.id = ps.page_id
                     WHERE ps.site_id = s.id AND up.status IN %s)""",
            (_LIVE_STATUSES,)
        )
        sites = cur.rowcount
        cur.execute(
            """UPDATE noaa_gauges g SET active = 0
               WHERE g.origin = 'user' AND g.active = 1
                 AND NOT EXISTS (
                     SELECT 1 FROM page_noaa_gauges png
                     JOIN user_pages up ON up.id = png.page_id
                     WHERE png.noaa_gauge_id = g.id AND up.status IN %s)""",
            (_LIVE_STATUSES,)
        )
        gauges = cur.rowcount
        cur.execute(
            """SELECT id FROM user_pages
               WHERE status = 'pending' AND pin_lat IS NULL
                 AND created_at::timestamptz < NOW() - (%s * INTERVAL '1 hour')""",
            (PENDING_PAGE_MAX_AGE_HOURS,)
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
