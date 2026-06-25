#!/usr/bin/env python3
"""audioforge — MiniMax music generation proxy + local NVMe storage."""
import http.server
import urllib.request
import urllib.error
import os
import json
import time
import uuid
import base64

# MiniMax config — set MINIMAX_API_KEY in your environment or a .env loader.
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_MUSIC_URL = "https://api.minimax.io/v1/music_generation"
MINIMAX_LYRICS_URL = "https://api.minimax.io/v1/lyrics_generation"

# Local storage config
MEDIA_DIR = os.path.expanduser("~/nvme-data/audioforge/media")
TRACKS_FILE = os.path.expanduser("~/nvme-data/audioforge/tracks.json")
os.makedirs(MEDIA_DIR, exist_ok=True)

def _load_tracks():
    if os.path.exists(TRACKS_FILE):
        with open(TRACKS_FILE) as f:
            return json.load(f)
    return {"tracks": []}

def _save_tracks(data):
    with open(TRACKS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def _create_track_record(title, prompt, model, duration_ms, filename):
    """Create a track record in tracks.json."""
    track_id = f"audioforge_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    track = {
        "id": track_id,
        "filename": filename,
        "title": title,
        "prompt": prompt,
        "model": model,
        "created_at": now,
        "duration": duration_ms / 1000.0 if duration_ms else 0,
    }
    tracks_data = _load_tracks()
    tracks_data["tracks"].insert(0, track)
    _save_tracks(tracks_data)
    return track_id

FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))

class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def do_GET(self):
        if self.path == "/tracks":
            self.serve_tracks_list()
        elif self.path.startswith("/tracks/") and not self.path.endswith("/metadata"):
            # /tracks/{id} — serve audio file
            track_id = self.path[len("/tracks/"):]
            self.handle_track_download(track_id)
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/music/generate":
            self.handle_music_generate()
        elif self.path == "/music/lyrics":
            self.handle_lyrics_generate()
        elif self.path.endswith("/metadata"):
            # /tracks/{id}/metadata — just register metadata (fallback)
            self.handle_track_metadata()
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/tracks/"):
            track_id = self.path[len("/tracks/"):]
            self.handle_track_delete(track_id)
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ================================================================
    # MiniMax: generate music (saves to local NVMe)
    # ================================================================
    def handle_music_generate(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len))
            prompt = body.get("prompt", "")
            lyrics = body.get("lyrics", "")
            title = body.get("title", "")
            model = body.get("model", "music-2.6")
            is_instrumental = body.get("is_instrumental", False)
            auto_lyrics = body.get("lyrics_optimizer", False)

            if not prompt:
                self.send_json_error(400, "Missing style prompt")
                return

            if len(prompt) > 2000:
                self.send_json_error(400, f"Style prompt too long ({len(prompt)} chars, max 2000)")
                return
            if lyrics and len(lyrics) > 3500:
                self.send_json_error(400, f"Lyrics too long ({len(lyrics)} chars, max 3500)")
                return

            if not MINIMAX_API_KEY:
                self.send_json_error(500, "MiniMax API key not configured")
                return

            payload = {
                "model": model,
                "prompt": prompt,
                "audio_setting": {
                    "sample_rate": 44100,
                    "bitrate": 256000,
                    "format": "mp3"
                }
            }

            if is_instrumental:
                payload["is_instrumental"] = True
            elif lyrics:
                payload["lyrics"] = lyrics
            elif auto_lyrics:
                payload["lyrics_optimizer"] = True

            req_data = json.dumps(payload).encode()
            req = urllib.request.Request(
                MINIMAX_MUSIC_URL,
                data=req_data,
                method="POST",
            )
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {MINIMAX_API_KEY}")

            self.log_message("MiniMax music: model=%s prompt='%s'...", model, prompt[:60])

            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read())

            base_resp = result.get("base_resp", {})
            if base_resp.get("status_code") != 0:
                self.send_json_error(500, f"MiniMax error: {base_resp.get('status_msg', 'Unknown')}")
                return

            audio_hex = result.get("data", {}).get("audio", "")
            if not audio_hex:
                self.send_json_error(500, "No audio returned from MiniMax")
                return

            audio_bytes = bytes.fromhex(audio_hex)
            extra = result.get("extra_info", {})
            duration_ms = extra.get("music_duration", 0)

            # Save to local NVMe drive
            filename = f"audioforge_{int(time.time())}_{uuid.uuid4().hex[:8]}.mp3"
            filepath = os.path.join(MEDIA_DIR, filename)
            with open(filepath, "wb") as f:
                f.write(audio_bytes)

            # Register track
            track_id = _create_track_record(title, prompt, model, duration_ms, filename)

            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(audio_bytes))
            self.send_header("X-Duration", str(duration_ms))
            self.send_header("X-Size", str(extra.get("music_size", 0)))
            self.send_header("X-Track-Id", track_id)
            self.end_headers()
            self.wfile.write(audio_bytes)

            self.log_message("Generated %d bytes, %dms, track=%s", len(audio_bytes), duration_ms, track_id)

        except urllib.error.HTTPError as e:
            err_body = e.read() if e.fp else b""
            self.log_message("MiniMax HTTP %d: %s", e.code, err_body.decode()[:300])
            self.send_json_error(e.code, f"MiniMax error: {err_body.decode()[:300]}")
        except Exception as e:
            self.log_message("Music error: %s", str(e))
            self.send_json_error(500, str(e))

    # ================================================================
    # MiniMax: generate lyrics
    # ================================================================
    def handle_lyrics_generate(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len))
            prompt = body.get("prompt", "")
            mode = body.get("mode", "write_full_song")

            if not prompt:
                self.send_json_error(400, "Missing prompt")
                return

            if not MINIMAX_API_KEY:
                self.send_json_error(500, "MiniMax API key not configured")
                return

            payload = {"mode": mode, "prompt": prompt}

            req_data = json.dumps(payload).encode()
            req = urllib.request.Request(
                MINIMAX_LYRICS_URL,
                data=req_data,
                method="POST",
            )
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {MINIMAX_API_KEY}")

            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())

            base_resp = result.get("base_resp", {})
            if base_resp.get("status_code") != 0:
                self.send_json_error(500, f"MiniMax error: {base_resp.get('status_msg', 'Unknown')}")
                return

            lyrics = result.get("lyrics", "") or result.get("data", {}).get("lyrics", "")
            if not lyrics:
                self.log_message("Lyrics generation returned empty. Response: %s", json.dumps(result)[:500])
            self.send_json_ok({"lyrics": lyrics})

        except Exception as e:
            self.log_message("Lyrics error: %s", str(e))
            self.send_json_error(500, str(e))

    # ================================================================
    # Local storage: register metadata (fallback)
    # ================================================================
    def handle_track_metadata(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len))
            track_id = self.path.split("/")[2]  # /tracks/{id}/metadata

            # Update existing track
            tracks_data = _load_tracks()
            for t in tracks_data.get("tracks", []):
                if t.get("id") == track_id:
                    t.update({k: v for k, v in body.items() if v})
                    _save_tracks(tracks_data)
                    self.send_json_ok({"id": track_id})
                    return

            self.send_json_error(404, "Track not found")
        except Exception as e:
            self.send_json_error(500, str(e))

    # ================================================================
    # Local storage: list all tracks
    # ================================================================
    def serve_tracks_list(self):
        try:
            tracks_data = _load_tracks()
            tracks = []
            for t in tracks_data.get("tracks", []):
                filepath = os.path.join(MEDIA_DIR, t.get("filename", ""))
                exists = os.path.exists(filepath)
                tracks.append({
                    "id": t.get("id", ""),
                    "title": t.get("title", "Untitled"),
                    "prompt": t.get("prompt", ""),
                    "model": t.get("model", "music-2.6"),
                    "created_at": t.get("created_at", ""),
                    "duration": t.get("duration", 0),
                    "exists": exists,
                })
            self.send_json_ok({"tracks": tracks})
        except Exception as e:
            self.log_message("Track list error: %s", str(e))
            self.send_json_ok({"tracks": [], "error": str(e)})

    # ================================================================
    # Local storage: download audio
    # ================================================================
    def handle_track_download(self, track_id):
        try:
            tracks_data = _load_tracks()
            track = None
            for t in tracks_data.get("tracks", []):
                if t.get("id") == track_id:
                    track = t
                    break

            if not track:
                self.send_error(404)
                return

            filepath = os.path.join(MEDIA_DIR, track.get("filename", ""))
            if not os.path.exists(filepath):
                self.send_error(404)
                return

            with open(filepath, "rb") as f:
                data = f.read()

            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(data))
            self.end_headers()
            self.wfile.write(data)

        except Exception as e:
            self.log_message("Track download error: %s", str(e))
            self.send_error(500)

    # ================================================================
    # Local storage: delete
    # ================================================================
    def handle_track_delete(self, track_id):
        try:
            tracks_data = _load_tracks()
            track = None
            for t in tracks_data.get("tracks", []):
                if t.get("id") == track_id:
                    track = t
                    break

            if not track:
                self.send_json_error(404, "Track not found")
                return

            filepath = os.path.join(MEDIA_DIR, track.get("filename", ""))
            if os.path.exists(filepath):
                os.remove(filepath)

            tracks_data["tracks"] = [t for t in tracks_data["tracks"] if t.get("id") != track_id]
            _save_tracks(tracks_data)

            self.send_json_ok({"success": True})
        except Exception as e:
            self.send_json_error(500, str(e))

    # ================================================================
    # Helpers
    # ================================================================
    def send_json_ok(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_json_error(self, code, msg):
        body = json.dumps({"error": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        if not self.path.startswith("/tracks/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8989
    server = http.server.HTTPServer(("0.0.0.0", port), ProxyHandler)
    print(f"audioforge running at http://192.168.1.3:{port}")
    print(f"MiniMax music: {'configured' if MINIMAX_API_KEY else 'NOT configured'}")
    print(f"Local storage -> {MEDIA_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
        server.shutdown()
