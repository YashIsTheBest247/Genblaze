"""
One-time YouTube OAuth authorization.

Regenerates secrets/youtube_token.json (with a fresh refresh_token) from your
existing secrets/youtube_client_secret.json. Run this whenever uploads fail with
'invalid_grant' (an expired/revoked refresh token).

Usage:
    # from the backend/ directory, with the venv active
    python authorize_youtube.py

    # force a fresh sign-in, e.g. to publish to a DIFFERENT channel
    python authorize_youtube.py --force

It will open a browser for you to grant access, then save the token. After it
finishes, the auto-publish feature will work again.

Uploads go to whichever channel you select during the consent flow. To switch
channels, run with --force: a valid existing token is otherwise reused and you
are never asked. If the target channel is a Brand Account on the same Google
account, Google shows a channel picker after you choose the account; if it
lives under a different Google account, sign in with that account instead.

Tip: if the OAuth consent screen is in "Testing" mode, Google expires refresh
tokens after 7 days. Set the consent screen to "In production" to avoid having
to re-run this every week.
"""
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# Full youtube scope (covers videos.insert). Must match what the app expects.
SCOPES = ["https://www.googleapis.com/auth/youtube"]

BASE_DIR = Path(__file__).resolve().parent
CLIENT_SECRET_FILE = BASE_DIR / "secrets" / "youtube_client_secret.json"
TOKEN_FILE = BASE_DIR / "secrets" / "youtube_token.json"


def _write_client_secret_from_token() -> bool:
    """
    Rebuild secrets/youtube_client_secret.json from an existing token.

    A stored token already embeds the client_id and client_secret it was issued
    against, so a lost client-secret file can be reconstructed without going
    back to the Cloud Console. Reads YOUTUBE_TOKEN_JSON or the token file.
    """
    import json
    import os

    raw = (os.getenv("YOUTUBE_TOKEN_JSON") or "").strip()
    if not raw and TOKEN_FILE.exists():
        raw = TOKEN_FILE.read_text(encoding="utf-8")
    if not raw:
        return False

    try:
        token = json.loads(raw)
        client_id = token["client_id"]
        client_secret = token["client_secret"]
    except Exception:
        return False

    print("WARNING: rebuilding the client secret from the EXISTING token, so it "
          "reuses the OLD OAuth client\n"
          "         (project " + client_id.split("-")[0] + "). If you are moving "
          "to a different Google account,\n"
          "         delete secrets/youtube_token.json first and drop the newly "
          "downloaded client JSON in place.")

    CLIENT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    CLIENT_SECRET_FILE.write_text(json.dumps({
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": token.get("token_uri", "https://oauth2.googleapis.com/token"),
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }, indent=2), encoding="utf-8")
    print(f"Rebuilt {CLIENT_SECRET_FILE} from the stored token's client credentials.")
    return True


def main() -> int:
    force = "--force" in sys.argv or "-f" in sys.argv

    if not CLIENT_SECRET_FILE.exists():
        if not _write_client_secret_from_token():
            print(f"ERROR: client secret not found at {CLIENT_SECRET_FILE}")
            print("Download an OAuth 'Desktop app' client from Google Cloud "
                  "Console and save it there.")
            return 1

    creds = None

    # Reuse a still-valid token if one exists — unless --force, which is how you
    # switch to a different channel (otherwise you are never re-prompted).
    if TOKEN_FILE.exists() and not force:
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            creds = None

    if creds and creds.valid:
        print("Existing token is already valid. Nothing to do.")
        print("To publish to a DIFFERENT channel, re-run with --force.")
        return 0

    if creds and creds.expired and creds.refresh_token:
        try:
            print("Refreshing existing token...")
            creds.refresh(Request())
        except Exception as exc:
            print(f"Refresh failed ({exc}); starting a fresh authorization flow.")
            creds = None

    if not creds or not creds.valid:
        print("Opening browser for YouTube authorization...")
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
        # prompt='consent' + access_type='offline' guarantees a refresh_token.
        creds = flow.run_local_server(
            port=0,
            access_type="offline",
            prompt="consent",
        )

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    print(f"Saved token to {TOKEN_FILE}")

    # Confirm it works by reading the channel.
    try:
        from googleapiclient.discovery import build
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        resp = youtube.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items", [])
        if items:
            print(f"Authorized channel: {items[0]['snippet']['title']}")
        else:
            print("Authorized, but no channel found on this account.")
    except Exception as exc:
        print(f"Token saved, but verification call failed: {exc}")
        return 1

    print("Done. Auto-publish is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
