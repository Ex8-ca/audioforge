# audioforge

AI music generator web UI backed by [MiniMax Music 2.6](https://api.minimax.io).
Generates songs from a style prompt + optional lyrics, plays them in-browser, and
saves them to a local library on disk.

![demo](https://placeholder) <!-- TODO: add a screenshot -->

## Features

- Style prompt with quick tags and presets (Soulful Blues, Lo-Fi Chill, Synthwave, etc.)
- Auto-generate lyrics from the style prompt, or write your own with structure tags
- Instrumental-only mode
- In-browser player with queue, shuffle, repeat, volume
- Local library of generated tracks with download/delete

## Setup

**1. Install dependencies.** The server uses only Python stdlib — no `pip install` needed.
Python 3.7+.

**2. Get a MiniMax API key.** Sign up at https://api.minimax.io and grab your key.

**3. Configure your key.** Copy the example env file and fill in your key:

```bash
cp .env.example .env
# then edit .env and set MINIMAX_API_KEY
```

**4. Run it.**

```bash
./run.sh 8989
```

Open http://localhost:8989 in your browser.

`run.sh` sources `.env` and starts the server. To run without the wrapper:

```bash
set -a && source .env && set +a && python3 server.py 8989
```

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Web UI |
| `/music/generate` | POST | Generate a song |
| `/music/lyrics` | POST | Auto-write lyrics from a theme |
| `/tracks` | GET | List local tracks |
| `/tracks/{id}` | GET | Stream MP3 |
| `/tracks/{id}/metadata` | POST | Update track title/metadata |
| `/tracks/{id}` | DELETE | Delete track + file |

### Generate request body

```json
{
  "prompt": "Lo-Fi Hip Hop, chill, vinyl crackle, 85 BPM",
  "lyrics": "[Verse]\n...\n\n[Chorus]\n...",
  "title": "Optional song title",
  "model": "music-2.6",
  "is_instrumental": false,
  "lyrics_optimizer": false
}
```

- `model`: `music-2.6` (paid, recommended) or `music-2.6-free`
- `is_instrumental`: skip vocals (music-2.6 only)
- `lyrics_optimizer`: auto-write lyrics from `prompt` when `lyrics` is empty (music-2.6 only)

Returns the MP3 audio bytes with these response headers:
- `X-Track-Id`: stable track ID for library operations
- `X-Duration`: duration in milliseconds
- `X-Size`: file size in bytes

### Prompt tips

The `prompt` field is the only style control — there's no EQ, no bass boost, no
frequency knob. Describe what you want directly:

**Bad:** `"sad rock song"`

**Good:** `"Cinematic 90s grunge, raw male vocals with grit, distorted guitars,
thundering 808 sub-bass, minor key, builds from whisper-quiet verse to
explosive chorus"`

For heavy bass, stack these keywords (they're cumulative):
- `sub-bass`, `808 sub-bass`, `30Hz foundation`
- `chest-rattling low end`, `rumbling bass`
- `bass-forward mix`, `subwoofer-test mix`

Trademarked artist names get filtered. Describe the *sound*, not who made it.

## Lyrics structure tags

Place these on their own line in `[brackets]` to mark sections:

```
[Intro] [Verse] [Verse 1] [Pre Chorus] [Chorus] [Post Chorus]
[Hook] [Bridge] [Interlude] [Break] [Build Up] [Inst] [Solo]
[Transition] [Outro] [End]
```

Without tags, the model defaults to a flat verse/chorus shape with no arc.
**Always tag.**

## Limitations

- MiniMax free tier has lower rate limits. Paid tier (`music-2.6`) is faster.
- One generation often isn't enough to nail a vibe. Regenerate 3–5 times.
- No bass/EQ controls — push low-end via prompt keywords.
- Lyrics are required for vocal songs, unless `lyrics_optimizer: true` or
  `is_instrumental: true`.

## Storage

Tracks are saved to `~/nvme-data/audioforge/media/` (changeable in `server.py`,
constants `MEDIA_DIR` / `TRACKS_FILE`). MP3 files plus a `tracks.json` index.

## Running as a systemd service

```bash
cp audioforge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now audioforge
```

## License

MIT