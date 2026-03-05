#!/usr/bin/env python3
"""
Claw Recall — External Source Capture

Polls Gmail, Google Drive, and Slack for new content and captures it as thoughts.
Tracks captured items in capture_log to avoid re-processing.

Usage:
    python3 capture_sources.py gmail                    # Poll both accounts
    python3 capture_sources.py gmail --account personal # Poll one account
    python3 capture_sources.py drive                    # Poll Drive
    python3 capture_sources.py drive --account rbs      # Poll one account
    python3 capture_sources.py slack                    # Poll Slack channels
    python3 capture_sources.py all                      # Poll everything
    python3 capture_sources.py status                   # Show capture stats
"""

import sys
import json
import sqlite3
import argparse
import logging
import re
from pathlib import Path
from datetime import datetime
from html import unescape

# Add paths
sys.path.insert(0, str(Path(__file__).parent))
SCRIPTS_DIR = Path.home() / "clawd" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from capture import capture_thought, batch_embed_thoughts, _get_db, DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [capture] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger("capture_sources")

# Max items per poll cycle
GMAIL_POLL_LIMIT = 50
DRIVE_POLL_LIMIT = 30
# Max body length to capture (avoid bloating DB)
MAX_BODY_LENGTH = 1500


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities to plain text."""
    # Remove style/script blocks entirely
    text = re.sub(r'<(style|script)[^>]*>.*?</\1>', '', html, flags=re.IGNORECASE | re.DOTALL)
    # Convert block elements to newlines
    text = re.sub(r'<(br|p|div|tr|li|h[1-6])[^>]*/?>', '\n', text, flags=re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    text = unescape(text)
    # Collapse whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _is_captured(conn: sqlite3.Connection, source_type: str, source_id: str, account: str) -> bool:
    """Check if an item has already been captured."""
    row = conn.execute(
        "SELECT 1 FROM capture_log WHERE source_type = ? AND source_id = ? AND account = ?",
        (source_type, source_id, account)
    ).fetchone()
    return row is not None


def _log_capture(conn: sqlite3.Connection, source_type: str, source_id: str,
                 account: str, thought_id: int, source_modified: str = None):
    """Record a captured item in the log."""
    conn.execute(
        """INSERT OR REPLACE INTO capture_log
           (source_type, source_id, account, thought_id, source_modified)
           VALUES (?, ?, ?, ?, ?)""",
        (source_type, source_id, account, thought_id, source_modified)
    )


# ─── Gmail Capture ────────────────────────────────────────────────────────────

def poll_gmail(account: str = None, limit: int = GMAIL_POLL_LIMIT,
               full_body: bool = False) -> dict:
    """
    Poll Gmail for new emails and capture them as thoughts.

    Args:
        account: 'personal', 'rbs', or None for both
        limit: Max emails to check per account
        full_body: If True, fetch full email body (slower, more API calls)

    Returns:
        {captured: int, skipped: int, errors: int, accounts: [...]}
    """
    from email_helper import list_inbox, get_email

    accounts = [account] if account else ['personal', 'rbs']
    stats = {"captured": 0, "skipped": 0, "errors": 0, "accounts": accounts}
    new_thought_ids = []

    conn = _get_db()
    try:
        for acct in accounts:
            try:
                emails = list_inbox(acct, limit=limit, query='in:inbox')
                log.info(f"Gmail [{acct}]: {len(emails)} inbox messages")

                for email_meta in emails:
                    msg_id = email_meta['id']

                    if _is_captured(conn, 'gmail', msg_id, acct):
                        stats["skipped"] += 1
                        continue

                    # Build content from metadata or full body
                    sender = email_meta.get('from', 'Unknown')
                    subject = email_meta.get('subject', 'No subject')
                    date = email_meta.get('date', '')
                    snippet = email_meta.get('snippet', '')

                    if full_body:
                        try:
                            full = get_email(acct, msg_id)
                            body = full.get('body', '')
                            if body:
                                body = _strip_html(body)[:MAX_BODY_LENGTH]
                            else:
                                body = snippet
                        except Exception as e:
                            log.warning(f"Failed to get full body for {msg_id}: {e}")
                            body = snippet
                    else:
                        body = snippet

                    content = f"Email from {sender}: {subject}\n{body}"

                    metadata = {
                        'from': sender,
                        'subject': subject,
                        'date': date,
                        'message_id': msg_id,
                        'thread_id': email_meta.get('threadId'),
                        'account': acct,
                    }

                    result = capture_thought(
                        content=content,
                        source='gmail',
                        agent=None,
                        metadata=metadata,
                        generate_embedding=False,  # Deferred — batch embed below
                        conn=conn,
                    )

                    if 'error' in result:
                        log.error(f"Capture error for {msg_id}: {result['error']}")
                        stats["errors"] += 1
                    elif result.get('duplicate'):
                        stats["skipped"] += 1
                    else:
                        _log_capture(conn, 'gmail', msg_id, acct, result['id'])
                        new_thought_ids.append(result['id'])
                        stats["captured"] += 1
                        log.info(f"  Captured: {subject[:60]}")

                conn.commit()
            except Exception as e:
                log.error(f"Gmail [{acct}] error: {e}")
                stats["errors"] += 1

        # Batch embed all new thoughts in one API call
        if new_thought_ids:
            embed_result = batch_embed_thoughts(new_thought_ids, conn=conn)
            log.info(f"Batch embedded {embed_result.get('embedded', 0)} Gmail thoughts")
    finally:
        conn.close()

    return stats


# ─── Google Drive Capture ─────────────────────────────────────────────────────

def poll_drive(account: str = None, limit: int = DRIVE_POLL_LIMIT) -> dict:
    """
    Poll Google Drive for recently modified documents and capture them.

    Only captures Google Docs and text files (not images, videos, folders).
    Detects updated documents by comparing modifiedTime.

    Args:
        account: 'personal', 'rbs', or None for both
        limit: Max files to check per account

    Returns:
        {captured: int, skipped: int, updated: int, errors: int}
    """
    from google_helper import get_service

    accounts = [account] if account else ['personal', 'rbs']
    stats = {"captured": 0, "skipped": 0, "updated": 0, "errors": 0, "accounts": accounts}
    new_thought_ids = []

    # MIME types we want to capture content from
    CAPTURABLE_MIMES = {
        'application/vnd.google-apps.document',     # Google Docs
        'application/vnd.google-apps.spreadsheet',  # Google Sheets (titles only)
        'text/plain',
        'text/markdown',
        'text/csv',
    }

    conn = _get_db()
    try:
        for acct in accounts:
            try:
                drive_svc = get_service(acct, 'drive', 'v3')

                # List recently modified files (exclude folders and trashed)
                api_result = drive_svc.files().list(
                    q="trashed=false and mimeType != 'application/vnd.google-apps.folder'",
                    pageSize=limit,
                    fields='files(id,name,mimeType,size,modifiedTime,createdTime)',
                    orderBy='modifiedTime desc',
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ).execute()

                files = api_result.get('files', [])
                log.info(f"Drive [{acct}]: {len(files)} recent files")

                for f in files:
                    file_id = f['id']
                    mime = f.get('mimeType', '')
                    modified = f.get('modifiedTime', '')
                    name = f.get('name', 'Untitled')

                    # Check if already captured with same modifiedTime
                    existing = conn.execute(
                        """SELECT source_modified FROM capture_log
                           WHERE source_type = 'drive' AND source_id = ? AND account = ?""",
                        (file_id, acct)
                    ).fetchone()

                    if existing:
                        if existing[0] == modified:
                            stats["skipped"] += 1
                            continue
                        # File was updated — re-capture
                        stats["updated"] += 1

                    # Get content for capturable types
                    body = ""
                    if mime == 'application/vnd.google-apps.document':
                        try:
                            docs_svc = get_service(acct, 'docs', 'v1')
                            doc = docs_svc.documents().get(documentId=file_id).execute()
                            text_parts = []
                            for element in doc.get('body', {}).get('content', []):
                                if 'paragraph' in element:
                                    for pe in element['paragraph'].get('elements', []):
                                        tr = pe.get('textRun')
                                        if tr:
                                            text_parts.append(tr.get('content', ''))
                            body = ''.join(text_parts)[:MAX_BODY_LENGTH]
                        except Exception as e:
                            log.warning(f"Failed to read doc {file_id}: {e}")
                            body = ""
                    elif mime == 'application/vnd.google-apps.spreadsheet':
                        body = f"(Google Spreadsheet — {f.get('name', 'untitled')})"
                    elif mime in CAPTURABLE_MIMES:
                        try:
                            import io
                            from googleapiclient.http import MediaIoBaseDownload
                            req = drive_svc.files().get_media(fileId=file_id)
                            buf = io.BytesIO()
                            downloader = MediaIoBaseDownload(buf, req)
                            done = False
                            while not done:
                                _, done = downloader.next_chunk()
                            body = buf.getvalue().decode('utf-8', errors='replace')[:MAX_BODY_LENGTH]
                        except Exception as e:
                            log.warning(f"Failed to download {file_id}: {e}")
                    else:
                        # Non-text file — capture metadata only
                        size = f.get('size', 'unknown')
                        body = f"({mime}, size: {size})"

                    content = f"Drive: {name}\n{body}" if body else f"Drive: {name}"

                    metadata = {
                        'file_id': file_id,
                        'name': name,
                        'mimeType': mime,
                        'modifiedTime': modified,
                        'account': acct,
                    }

                    cap_result = capture_thought(
                        content=content,
                        source='drive',
                        agent=None,
                        metadata=metadata,
                        generate_embedding=False,  # Deferred — batch embed below
                        conn=conn,
                    )

                    if 'error' in cap_result:
                        log.error(f"Capture error for {name}: {cap_result['error']}")
                        stats["errors"] += 1
                    elif cap_result.get('duplicate'):
                        stats["skipped"] += 1
                    else:
                        _log_capture(conn, 'drive', file_id, acct, cap_result['id'], modified)
                        new_thought_ids.append(cap_result['id'])
                        stats["captured"] += 1
                        log.info(f"  Captured: {name[:60]}")

                conn.commit()
            except Exception as e:
                log.error(f"Drive [{acct}] error: {e}")
                stats["errors"] += 1

        # Batch embed all new thoughts in one API call
        if new_thought_ids:
            embed_result = batch_embed_thoughts(new_thought_ids, conn=conn)
            log.info(f"Batch embedded {embed_result.get('embedded', 0)} Drive thoughts")
    finally:
        conn.close()

    return stats


# ─── Slack Capture ─────────────────────────────────────────────────────────────

# Read Slack bot token from OpenClaw config
_SLACK_TOKEN = None

def _get_slack_token() -> str:
    """Get Slack bot token from OpenClaw config."""
    global _SLACK_TOKEN
    if _SLACK_TOKEN:
        return _SLACK_TOKEN
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
        _SLACK_TOKEN = config.get("channels", {}).get("slack", {}).get("botToken", "")
    return _SLACK_TOKEN or ""


def poll_slack(limit: int = 50) -> dict:
    """
    Poll Slack channels/DMs for recent messages and capture them.

    Uses the Slack Web API via bot token from OpenClaw config.
    Only captures messages the bot has access to (channels it's in + DMs).

    Args:
        limit: Max messages to check per channel

    Returns:
        {captured: int, skipped: int, errors: int, channels: int}
    """
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
    except ImportError:
        return {"error": "slack_sdk not installed. Run: pip install slack_sdk"}

    token = _get_slack_token()
    if not token:
        return {"error": "No Slack bot token found in ~/.openclaw/openclaw.json"}

    client = WebClient(token=token)
    stats = {"captured": 0, "skipped": 0, "errors": 0, "channels": 0}
    new_thought_ids = []

    conn = _get_db()
    try:
        # Get list of channels/DMs — query each type separately for reliability
        try:
            channels = []
            for conv_type in ["im", "mpim", "public_channel", "private_channel"]:
                try:
                    result = client.conversations_list(types=conv_type, limit=100)
                    channels.extend(result.get("channels", []))
                except SlackApiError:
                    pass
            log.info(f"Slack: {len(channels)} accessible channels/DMs")
        except SlackApiError as e:
            log.error(f"Slack API error listing channels: {e.response['error']}")
            return {"error": f"Slack API: {e.response['error']}"}

        # Get user info cache for display names
        user_cache = {}

        def get_username(user_id):
            if user_id in user_cache:
                return user_cache[user_id]
            try:
                info = client.users_info(user=user_id)
                name = info["user"].get("real_name") or info["user"].get("name", user_id)
                user_cache[user_id] = name
                return name
            except Exception:
                user_cache[user_id] = user_id
                return user_id

        for channel in channels:
            channel_id = channel["id"]
            channel_name = channel.get("name", channel.get("user", channel_id))
            stats["channels"] += 1

            try:
                # Get recent messages
                history = client.conversations_history(
                    channel=channel_id,
                    limit=limit,
                )
                messages = history.get("messages", [])

                for msg in messages:
                    # Skip bot messages, join/leave, etc.
                    if msg.get("subtype") in ("channel_join", "channel_leave", "bot_message"):
                        continue
                    if not msg.get("text"):
                        continue

                    msg_ts = msg["ts"]
                    source_id = f"{channel_id}:{msg_ts}"

                    if _is_captured(conn, 'slack', source_id, 'default'):
                        stats["skipped"] += 1
                        continue

                    user_id = msg.get("user", "unknown")
                    username = get_username(user_id)
                    text = msg["text"][:MAX_BODY_LENGTH]
                    ts_dt = datetime.fromtimestamp(float(msg_ts))

                    content = f"Slack [{channel_name}] {username}: {text}"

                    metadata = {
                        'channel_id': channel_id,
                        'channel_name': channel_name,
                        'user_id': user_id,
                        'username': username,
                        'ts': msg_ts,
                        'date': ts_dt.isoformat(),
                    }

                    result = capture_thought(
                        content=content,
                        source='slack',
                        agent=None,
                        metadata=metadata,
                        generate_embedding=False,
                        conn=conn,
                    )

                    if 'error' in result:
                        log.error(f"Capture error for slack {source_id}: {result['error']}")
                        stats["errors"] += 1
                    elif result.get('duplicate'):
                        stats["skipped"] += 1
                    else:
                        _log_capture(conn, 'slack', source_id, 'default', result['id'])
                        new_thought_ids.append(result['id'])
                        stats["captured"] += 1

            except SlackApiError as e:
                if e.response['error'] == 'not_in_channel':
                    continue  # Skip channels bot isn't in
                log.error(f"Slack channel {channel_name}: {e.response['error']}")
                stats["errors"] += 1

        conn.commit()

        # Batch embed
        if new_thought_ids:
            embed_result = batch_embed_thoughts(new_thought_ids, conn=conn)
            log.info(f"Batch embedded {embed_result.get('embedded', 0)} Slack thoughts")

    except Exception as e:
        log.error(f"Slack error: {e}")
        stats["errors"] += 1
    finally:
        conn.close()

    return stats


# ─── Status & Stats ───────────────────────────────────────────────────────────

def capture_status() -> dict:
    """Get capture statistics from the log."""
    conn = _get_db()
    try:
        stats = {}
        rows = conn.execute(
            "SELECT source_type, account, COUNT(*) FROM capture_log GROUP BY source_type, account"
        ).fetchall()
        for source_type, account, count in rows:
            key = f"{source_type}:{account}" if account else source_type
            stats[key] = count

        stats["total"] = conn.execute("SELECT COUNT(*) FROM capture_log").fetchone()[0]

        latest = conn.execute(
            "SELECT source_type, account, MAX(captured_at) FROM capture_log GROUP BY source_type, account"
        ).fetchall()
        stats["latest"] = {
            f"{r[0]}:{r[1]}" if r[1] else r[0]: r[2] for r in latest
        }

        return stats
    finally:
        conn.close()


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog='capture-sources',
        description='Claw Recall — External Source Capture (Gmail, Drive)',
    )
    parser.add_argument('source', choices=['gmail', 'drive', 'slack', 'all', 'status'],
                        help='Which source to poll')
    parser.add_argument('--account', '-a', choices=['personal', 'rbs'],
                        help='Specific account (default: both)')
    parser.add_argument('--limit', '-n', type=int, default=50,
                        help='Max items to check per account')
    parser.add_argument('--full-body', action='store_true',
                        help='Gmail: fetch full email body (slower)')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='Minimal output')

    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    if args.source == 'status':
        stats = capture_status()
        print("Claw Recall — Capture Status")
        print(f"  Total captured: {stats.get('total', 0)}")
        for key, count in sorted(stats.items()):
            if key not in ('total', 'latest'):
                print(f"  {key}: {count}")
        if stats.get('latest'):
            print("\n  Latest captures:")
            for key, ts in stats['latest'].items():
                print(f"    {key}: {ts}")
        return

    results = {}
    if args.source in ('gmail', 'all'):
        print("Polling Gmail...")
        results['gmail'] = poll_gmail(
            account=args.account,
            limit=args.limit,
            full_body=args.full_body,
        )
        g = results['gmail']
        print(f"  Gmail: {g['captured']} captured, {g['skipped']} skipped, {g['errors']} errors")

    if args.source in ('drive', 'all'):
        print("Polling Google Drive...")
        results['drive'] = poll_drive(
            account=args.account,
            limit=args.limit,
        )
        d = results['drive']
        print(f"  Drive: {d['captured']} captured, {d['updated']} updated, "
              f"{d['skipped']} skipped, {d['errors']} errors")

    if args.source in ('slack', 'all'):
        print("Polling Slack...")
        results['slack'] = poll_slack(limit=args.limit)
        s = results['slack']
        if 'error' in s:
            print(f"  Slack: {s['error']}")
        else:
            print(f"  Slack: {s['captured']} captured, {s['skipped']} skipped, "
                  f"{s['errors']} errors ({s['channels']} channels)")

    # Summary
    total_captured = sum(r.get('captured', 0) for r in results.values())
    total_errors = sum(r.get('errors', 0) for r in results.values())
    if total_captured > 0 or total_errors > 0:
        print(f"\nTotal: {total_captured} new captures, {total_errors} errors")


if __name__ == "__main__":
    main()
