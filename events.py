"""
events.py — Unified event logging for SecureVision Pro.

This module is the single place that writes to the `events` table, which
acts as a lightweight, unified timeline across fire alerts, after-hours
intrusion alerts, and attendance milestones. It does NOT replace the
existing `fire_alerts` / `after_hours_alerts` tables — those remain the
detailed source-of-truth records. `events` is an index layer on top,
so the dashboard (and future features) can show one combined feed
without every consumer having to know about three different tables.

Design goals:
- Zero coupling to Flask request context (importable/testable standalone)
- Takes an existing sqlite3 connection OR opens its own — caller's choice
- Never raises — a logging failure must not break the calling detection
  or attendance code path. Errors are printed, not propagated.
"""

import sqlite3
from datetime import datetime

DB_PATH = 'database.db'

VALID_EVENT_TYPES = {'fire', 'after_hours', 'attendance', 'system'}
VALID_SEVERITIES = {'info', 'warning', 'critical'}


def init_events_table(conn=None):
    """Create the events table if it doesn't exist yet. Safe to call on
    every app startup alongside the existing init_db()."""
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            camera_source TEXT,
            confidence REAL,
            ref_table TEXT,
            ref_id INTEGER,
            timestamp DATETIME NOT NULL
        )''')
        # Index for the common dashboard query pattern: recent events,
        # optionally filtered by type/severity.
        c.execute('CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type)')
        conn.commit()
    except Exception as e:
        print('events.py: failed to init events table:', e)
    finally:
        if own_conn:
            conn.close()


def log_event(event_type, severity, message, camera_source=None,
              confidence=None, ref_table=None, ref_id=None, conn=None):
    """Insert one row into the unified events timeline.

    Called from the existing detection/attendance code right after the
    existing DB write (e.g. right after the fire_alerts INSERT). Accepts
    an already-open connection to avoid opening a second one mid-request;
    if none is given, opens and closes its own.

    Never raises — a bad event_type/severity is normalized to a safe
    default rather than blowing up the caller (e.g. fire detection loop).
    """
    if event_type not in VALID_EVENT_TYPES:
        print(f'events.py: unknown event_type "{event_type}", logging as "system"')
        event_type = 'system'
    if severity not in VALID_SEVERITIES:
        print(f'events.py: unknown severity "{severity}", defaulting to "info"')
        severity = 'info'

    timestamp_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    own_conn = conn is None

    try:
        if own_conn:
            conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute(
            '''INSERT INTO events
               (event_type, severity, message, camera_source, confidence,
                ref_table, ref_id, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (event_type, severity, message, camera_source, confidence,
             ref_table, ref_id, timestamp_str)
        )
        if own_conn:
            conn.commit()
        # If conn was passed in, the caller controls when to commit —
        # avoids a stray commit interrupting a caller's own transaction.
    except Exception as e:
        print('events.py: failed to log event:', e)
    finally:
        if own_conn and conn:
            conn.close()


def get_recent_events(limit=50, event_type=None, severity=None):
    """Fetch recent events for the dashboard feed, newest first.
    Optional filters by type ('fire'/'after_hours'/'attendance'/'system')
    and/or severity ('info'/'warning'/'critical')."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        c = conn.cursor()
        query = 'SELECT id, event_type, severity, message, camera_source, confidence, ref_table, ref_id, timestamp FROM events WHERE 1=1'
        params = []
        if event_type:
            query += ' AND event_type = ?'
            params.append(event_type)
        if severity:
            query += ' AND severity = ?'
            params.append(severity)
        query += ' ORDER BY id DESC LIMIT ?'
        params.append(limit)

        c.execute(query, params)
        rows = c.fetchall()
        return [
            {
                'id': r[0], 'event_type': r[1], 'severity': r[2],
                'message': r[3], 'camera_source': r[4], 'confidence': r[5],
                'ref_table': r[6], 'ref_id': r[7], 'timestamp': r[8]
            }
            for r in rows
        ]
    except Exception as e:
        print('events.py: failed to fetch events:', e)
        return []
    finally:
        conn.close()