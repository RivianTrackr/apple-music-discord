#!/usr/bin/env python3
"""
Apple Music -> Discord Rich Presence (macOS)

Shows whatever Apple Music is playing as a "Listening to" status on Discord,
with album art, a progress bar, and a "Listen on Apple Music" button.

No third party packages needed. Uses only the Python standard library.

See README.md for setup, or just run ./install.sh
"""

import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "PASTE_YOUR_APPLICATION_ID_HERE")
POLL_SECONDS = 5           # how often to check Apple Music
RECONNECT_SECONDS = 15     # how often to retry if Discord isn't open
FALLBACK_IMAGE = "applemusic"  # optional asset key uploaded in the Developer Portal

# What the one-line status in the member list shows after "Listening to":
#   0 = app name ("Apple Music"), 1 = artist, 2 = song title
STATUS_DISPLAY = int(os.environ.get("DISCORD_STATUS_DISPLAY", "1"))

SEP = "\x1f"  # unit separator, safe against song titles with tabs or commas

APPLESCRIPT = """
if application "Music" is running then
  tell application "Music"
    if player state is playing then
      try
        set t to current track
        set d to (character id 31)
        return (name of t) & d & (artist of t) & d & (album of t) & d & ¬
          ((duration of t) as text) & d & ((player position) as text)
      on error
        return "UNKNOWN"
      end try
    else
      return "PAUSED"
    end if
  end tell
else
  return "STOPPED"
end if
"""


def log(msg):
    print(time.strftime("[%H:%M:%S]"), msg, flush=True)


# ---------------------------------------------------------------- Apple Music

def get_now_playing():
    """Returns a dict for the playing track, or None if nothing is playing."""
    try:
        out = subprocess.run(
            ["osascript", "-e", APPLESCRIPT],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except subprocess.TimeoutExpired:
        return None

    if not out or out in ("STOPPED", "PAUSED", "UNKNOWN"):
        return None

    parts = out.split(SEP)
    if len(parts) != 5:
        return None

    name, artist, album, duration, position = parts

    def num(s):
        try:
            return float(s.replace(",", "."))  # handles locales with decimal commas
        except ValueError:
            return 0.0

    return {
        "name": name,
        "artist": artist,
        "album": album,
        "duration": num(duration),
        "position": num(position),
    }


# ------------------------------------------------------- Album art via iTunes

_art_cache = {}


def lookup_artwork(track):
    """Finds album art and an Apple Music link using Apple's free search API."""
    key = (track["artist"], track["album"], track["name"])
    if key in _art_cache:
        return _art_cache[key]

    result = (None, None)
    query = urllib.parse.urlencode({
        "term": f'{track["artist"]} {track["name"]}',
        "entity": "song",
        "limit": 5,
    })
    try:
        with urllib.request.urlopen(f"https://itunes.apple.com/search?{query}", timeout=5) as r:
            items = json.load(r).get("results", [])
        # Prefer a result on the same album, otherwise take the first hit
        match = next(
            (i for i in items if i.get("collectionName", "").lower() == track["album"].lower()),
            items[0] if items else None,
        )
        if match:
            art = match.get("artworkUrl100", "").replace("100x100bb", "600x600bb") or None
            result = (art, match.get("trackViewUrl"))
    except Exception as e:
        log(f"Artwork lookup failed: {e}")

    _art_cache[key] = result
    return result


# ------------------------------------------------------------- Discord IPC

def _discord_temp_dirs():
    dirs = []
    if os.environ.get("TMPDIR"):
        dirs.append(os.environ["TMPDIR"])
    try:  # launchd agents sometimes don't get TMPDIR, so ask macOS directly
        d = subprocess.run(["getconf", "DARWIN_USER_TEMP_DIR"],
                           capture_output=True, text=True).stdout.strip()
        if d:
            dirs.append(d)
    except Exception:
        pass
    dirs.append("/tmp")
    return dirs


class DiscordIPC:
    def __init__(self, client_id):
        self.client_id = client_id
        self.sock = None

    def connect(self):
        for base in _discord_temp_dirs():
            for i in range(10):
                path = os.path.join(base, f"discord-ipc-{i}")
                if not os.path.exists(path):
                    continue
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.settimeout(5)
                    s.connect(path)
                    self.sock = s
                    self._send(0, {"v": 1, "client_id": self.client_id})
                    _, resp = self._recv()
                    if resp.get("evt") == "READY":
                        user = resp.get("data", {}).get("user", {}).get("username", "?")
                        log(f"Connected to Discord as {user}")
                        return
                    raise ConnectionError(resp)
                except OSError:
                    continue
        raise ConnectionError("Discord isn't running (no IPC socket found)")

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _send(self, op, payload):
        data = json.dumps(payload).encode("utf-8")
        self.sock.sendall(struct.pack("<II", op, len(data)) + data)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Discord closed the connection")
            buf += chunk
        return buf

    def _recv(self):
        op, length = struct.unpack("<II", self._recv_exact(8))
        return op, json.loads(self._recv_exact(length))

    def set_activity(self, activity):
        self._send(1, {
            "cmd": "SET_ACTIVITY",
            "args": {"pid": os.getpid(), "activity": activity},
            "nonce": str(uuid.uuid4()),
        })
        _, resp = self._recv()
        if resp.get("evt") == "ERROR":
            log(f"Discord rejected the update: {resp.get('data')}")


# ------------------------------------------------------------- Presence

def _fit(text, fallback="Unknown"):
    """Discord fields must be 2 to 128 characters."""
    text = (text or fallback).strip() or fallback
    if len(text) < 2:
        text += " "
    return text[:128]


def build_activity(track):
    art, url = lookup_artwork(track)
    activity = {
        "type": 2,                    # 2 = "Listening to"
        "status_display_type": STATUS_DISPLAY,
        "details": _fit(track["name"]),
        "state": _fit(track["artist"]),
        "assets": {
            "large_image": art or FALLBACK_IMAGE,
            "large_text": _fit(track["album"]),
        },
    }
    if track["duration"] > 0:
        start = time.time() - track["position"]
        activity["timestamps"] = {
            "start": int(start * 1000),
            "end": int((start + track["duration"]) * 1000),
        }
    if url:
        activity["buttons"] = [{"label": "Listen on Apple Music", "url": url}]
    return activity


def main():
    if not CLIENT_ID.isdigit():
        sys.exit("Set CLIENT_ID (your Discord Application ID) at the top of the script first.")

    rpc = DiscordIPC(CLIENT_ID)
    last_key = None
    last_start = None
    cleared = True

    while True:
        if rpc.sock is None:
            try:
                rpc.connect()
                last_key, cleared = None, True
            except ConnectionError as e:
                log(f"{e}. Retrying in {RECONNECT_SECONDS}s")
                time.sleep(RECONNECT_SECONDS)
                continue

        try:
            track = get_now_playing()

            if track is None:
                if not cleared:
                    rpc.set_activity(None)
                    log("Paused or stopped. Cleared status")
                    cleared = True
                    last_key = None
            else:
                key = (track["name"], track["artist"], track["album"])
                start = time.time() - track["position"]
                seeked = last_start is not None and abs(start - last_start) > 3
                if key != last_key or seeked or cleared:
                    rpc.set_activity(build_activity(track))
                    log(f"Now playing: {track['name']} by {track['artist']}")
                    last_key, cleared = key, False
                last_start = start

        except (OSError, ConnectionError, struct.error) as e:
            log(f"Lost connection to Discord ({e})")
            rpc.close()
            continue

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
