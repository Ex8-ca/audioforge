#!/usr/bin/env python3
"""audioforge — MiniMax music generation proxy + Cloudinary history."""
import http.server
import urllib.request
import urllib.error
import os
import json
import time
import io
import base64
import cloudinary
import cloudinary.uploader
import cloudinary.api

# MiniMax config — loads from minimax-speech .env
def _load_minimax_key():
    env_path = os.path.expanduser("~/.hermes/skills/minimax-speech/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("MINIMAX_API_KEY=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("MINIMAX_API_KEY", "")

MINIMAX_API_KEY = _load_minimax_key()
MINIMAX_MUSIC_URL = "https://api.minimax.io/v1/music_generation"
MINIMAX_LYRICS_URL = "https://api.minimax.io/v1/lyrics_generation"

# Cloudinary config — loads from .env
def _load_cloudinary():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                key, val = line.split("=", 1)
                os.environ[f"CLOUDINARY_{key.upper()}"] = val.strip()
    cloudinary.config(
        cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME", ""),
        api_key=os.environ.get("CLOUDINARY_API_KEY", ""),
        api_secret=os.environ.get("CLOUDINARY_API_SECRET", ""),
        secure=True,
    )

_load_cloudinary()

FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))

class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def do_GET(self):
        if self.path == "/cloudinary/history":
            self.serve_cloudinary_history()
        elif self.path.startswith("/cloudinary/download/"):
            public_id = self.path[len("/cloudinary/download/"):]
            self.handle_cloudinary_download(public_id)
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/music/generate":
            self.handle_music_generate()
        elif self.path == "/music/lyrics":
            self.handle_lyrics_generate()
        elif self.path == "/cloudinary/upload":
            self.handle_cloudinary_upload()
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/cloudinary/delete/"):
            public_id = self.path[len("/cloudinary/delete/"):]
            self.handle_cloudinary_delete(public_id)
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ================================================================
    # MiniMax: generate music
    # ================================================================
    def handle_music_generate(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len))
            prompt = body.get("prompt", "")
            lyrics = body.get("lyrics", "")
            model = body.get("model", "music-2.6")
            is_instrumental = body.get("is_instrumental", False)
            auto_lyrics = body.get("auto_generate_lyrics", False)

            if not prompt:
                self.send_json_error(400, "Missing style prompt")
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
                payload["auto_generate_lyrics"] = True

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

            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(audio_bytes))
            self.send_header("X-Duration", str(extra.get("music_duration", 0)))
            self.send_header("X-Size", str(extra.get("music_size", 0)))
            self.end_headers()
            self.wfile.write(audio_bytes)

            self.log_message("Generated %d bytes, %dms", len(audio_bytes), extra.get("music_duration", 0))

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

            # Lyrics can be at top level or in data
            lyrics = result.get("lyrics", "") or result.get("data", {}).get("lyrics", "")
            self.send_json_ok({"lyrics": lyrics})

        except Exception as e:
            self.log_message("Lyrics error: %s", str(e))
            self.send_json_error(500, str(e))

    # ================================================================
    # Cloudinary: upload audio
    # ================================================================
    def handle_cloudinary_upload(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len))
            audio_b64 = body.get("base64", "")
            title = body.get("title", "")
            prompt = body.get("prompt", "")
            model = body.get("model", "music-2.6")

            if not audio_b64:
                self.send_json_error(400, "Missing base64 audio")
                return

            audio_bytes = base64.b64decode(audio_b64)

            result = cloudinary.uploader.upload(
                io.BytesIO(audio_bytes),
                folder="audioforge",
                resource_type="auto",
                public_id=f"audioforge_{int(time.time())}",
            )

            self.send_json_ok({
                "success": True,
                "url": result.get("secure_url", ""),
                "public_id": result.get("public_id", ""),
                "title": title,
                "prompt": prompt,
                "model": model,
                "created_at": result.get("created_at", ""),
            })

        except Exception as e:
            self.log_message("Cloudinary upload error: %s", str(e))
            self.send_json_error(500, str(e))

    # ================================================================
    # Cloudinary: list recent tracks
    # ================================================================
    def serve_cloudinary_history(self):
        try:
            result = cloudinary.api.resources(
                type="upload",
                prefix="audioforge/",
                max_results=50,
                sort_by=[("created_at", "desc")],
            )

            tracks = []
            for res in result.get("resources", []):
                meta = res.get("context", {}).get("custom", {})
                tracks.append({
                    "url": res.get("secure_url", ""),
                    "public_id": res.get("public_id", ""),
                    "title": meta.get("title", res.get("public_id", "").replace("audioforge_", "")),
                    "prompt": meta.get("prompt", ""),
                    "model": meta.get("model", "music-2.6"),
                    "created_at": res.get("created_at", ""),
                    "duration": res.get("duration", 0),
                })

            self.send_json_ok({"tracks": tracks})

        except Exception as e:
            self.log_message("Cloudinary history error: %s", str(e))
            self.send_json_ok({"tracks": [], "error": str(e)})

    # ================================================================
    # Cloudinary: download audio
    # ================================================================
    def handle_cloudinary_download(self, public_id):
        try:
            decoded_id = urllib.parse.unquote(public_id)
            resource = cloudinary.api.resource(decoded_id)
            audio_url = resource.get("secure_url", "")

            req = urllib.request.Request(audio_url)
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()

            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(data))
            self.end_headers()
            self.wfile.write(data)

        except Exception as e:
            self.log_message("Cloudinary download error: %s", str(e))
            self.send_error(500)

    # ================================================================
    # Cloudinary: delete
    # ================================================================
    def handle_cloudinary_delete(self, public_id):
        try:
            decoded_id = urllib.parse.unquote(public_id)
            result = cloudinary.uploader.destroy(decoded_id, resource_type="auto")
            self.send_json_ok({"success": result.get("result") == "ok"})
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
        if not self.path.startswith("/cloudinary/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass

if __name__ == "__main__":
    import sys
    import urllib.parse
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8989
    server = http.server.HTTPServer(("0.0.0.0", port), ProxyHandler)
    print(f"audioforge running at http://192.168.1.3:{port}")
    print(f"MiniMax music: {'configured' if MINIMAX_API_KEY else 'NOT configured'}")
    print(f"Cloudinary uploads -> dol2t3l5x")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
        server.shutdown()
