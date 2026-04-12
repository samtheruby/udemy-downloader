# Udemy Downloader with DRM support

[![forthebadge](https://forthebadge.com/images/badges/built-with-love.svg)](https://forthebadge.com)
[![forthebadge](https://forthebadge.com/images/badges/made-with-python.svg)](https://forthebadge.com)
![GitHub forks](https://img.shields.io/github/forks/samtheruby/udemy-downloader?style=for-the-badge)
![GitHub Repo stars](https://img.shields.io/github/stars/samtheruby/udemy-downloader?style=for-the-badge)
![GitHub](https://img.shields.io/github/license/samtheruby/udemy-downloader?style=for-the-badge)

> [!IMPORTANT]
> Downloading courses is against Udemy's Terms of Service. Use at your own risk — the authors are not responsible for account suspensions or any legal issues resulting from use of this program.

# Description

Downloads Udemy courses including Widevine DRM-protected videos. DRM keys are acquired automatically at download time using a Widevine L3 device file (`.wvd`) — no manual key extraction required.

Fork of [Puyodead1/udemy-downloader](https://github.com/Puyodead1/udemy-downloader) with the following additions:
- **Automatic Widevine key fetching** via pywidevine + WVD device files
- **WVD device rotation** — place multiple `.wvd` files in `wvkeys/` and they are used round-robin
- **Parallel lecture downloads** per chapter (`--parallel-lectures`)
- **MKV output** with embedded subtitle tracks (`--use-mkv`)
- **Subtitle export** to an organized directory for AI summarization (`--keep-subtitles`)
- **Thread-safe logging** — clean output even during parallel downloads
- **Enterprise portal support** — works with custom Udemy portals (e.g. `company.udemy.com`) via cookie-based auth

# Requirements

### System tools (must be in PATH)

| Tool | Purpose | Install |
|------|---------|---------|
| [Python 3.12+](https://python.org/) | Runtime | — |
| [ffmpeg](https://github.com/yt-dlp/FFmpeg-Builds/releases/tag/latest) | Muxing | `apt install ffmpeg` or download |
| [aria2](https://github.com/aria2/aria2/) | Segment downloading | `apt install aria2` |
| [MKVToolNix](https://mkvtoolnix.download/) | MKV muxing (optional, for `--use-mkv`) | `apt install mkvtoolnix` |

### Python packages

```
pip install -r requirements.txt
```

# Setup

## 1. Widevine device files (required for DRM courses)

Obtain one or more Widevine L3 `.wvd` device files using [KeyDive](https://github.com/hyugogirubato/KeyDive) on an Android emulator:

```bash
# Install KeyDive in its own venv (avoids dependency conflicts)
python -m venv C:\keydive-env
C:\keydive-env\Scripts\pip install keydive

# Start your Android Studio AVD (API 33, Google APIs — NOT Google Play)
# Push and start frida-server, then:
C:\keydive-env\Scripts\keydive -kw -a player
# In the emulator: tap Provision Widevine → Refresh → Test DRM Playback
```

Place the resulting `.wvd` files in the `wvkeys/` directory (any filename works):

```
wvkeys/
  google_sdk_gphone64_x86_64_17.0.0_abc123_l3.wvd
  google_sdk_gphone64_x86_64_17.0.0_def456_l3.wvd
```

Keys fetched during downloads are cached in `keyfile.json` automatically.

## 2. Authentication

### Bearer token (standard Udemy accounts)

Get your Bearer token from browser DevTools (Network tab → any `api-2.0` request → `Authorization: Bearer ...` header).

Store it in `.env` (copy `.env.sample` → `.env`) or pass via `-b`.

### Browser cookies (enterprise / subscription accounts)

Export cookies from your browser in Netscape format to `cookies.txt`, then use `--browser file`.

For Chrome/Brave (app-bound encryption blocks automatic extraction), use a browser extension like [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc).

## 3. Environment file

```bash
cp .env.sample .env
# Edit .env and set UDEMY_BEARER if using bearer token auth
```

# Usage

```
python main.py -c <COURSE_URL> [options]
```

### Authentication options

```
-b, --bearer TOKEN          Bearer token
--browser {chrome,firefox,opera,edge,brave,chromium,vivaldi,safari,file}
                            Extract cookies from browser (use 'file' for cookies.txt)
```

### Download options

```
-q, --quality INT           Video quality (closest match used if unavailable)
-l, --lang LANG             Caption language, or 'all' (default: en)
-cd, --concurrent-downloads INT
                            Segments downloaded in parallel per lecture (1-30, default 10)
-pl, --parallel-lectures INT
                            Lectures downloaded in parallel per chapter (1-5, default 1)
--download-assets           Download lecture assets
--download-captions         Download captions
--download-quizzes          Download quizzes
--skip-lectures             Skip video lectures (useful with --download-captions)
--skip-hls                  Skip HLS streams (faster info fetching)
```

### Output options

```
--use-mkv                   Output MKV with subtitle tracks embedded (requires mkvmerge)
--keep-subtitles            Copy subtitles to out_dir/subtitles/{course}/{lang}/{chapter}/
--keep-vtt                  Keep original .vtt files alongside .srt
-o, --out PATH              Custom output directory (default: out_dir/)
--id-as-course-name         Use course ID instead of title for the output folder
-n, --continue-lecture-numbers
                            Use continuous lecture numbering across chapters
```

### Filtering

```
--chapter CHAPTERS          Download specific chapters e.g. "1,3-5,7"
```

### Performance / encoding

```
--use-h265                  Re-encode to H.265 (smaller files, slower)
--h265-crf INT              H.265 CRF value (default 28)
--h265-preset PRESET        H.265 preset (default medium)
--use-nvenc                 Use NVIDIA GPU for H.265 encoding
```

### Misc

```
--info                      Print course info without downloading
--save-to-file              Cache course structure to disk
--load-from-file            Load course structure from cache (skips API fetch)
--log-level LEVEL           DEBUG / INFO / WARNING / ERROR / CRITICAL (default INFO)
```

## Examples

```bash
# Standard download — best quality, English captions, MKV output
python main.py -c https://www.udemy.com/course/my-course/ -b <token> \
  --download-captions --use-mkv -q 1080

# Enterprise portal with cookie auth, parallel downloads, subtitle export
python main.py -c https://company.udemy.com/course/my-course/ \
  --browser file -q 1080 --download-captions --use-mkv \
  --keep-subtitles --parallel-lectures 3

# Download only captions (no video)
python main.py -c <URL> -b <token> --skip-lectures --download-captions -l all

# Specific chapters only
python main.py -c <URL> -b <token> --chapter "1,3-5,8"

# Use cached course structure on re-run
python main.py -c <URL> -b <token> --save-to-file   # first run
python main.py -c <URL> -b <token> --load-from-file  # subsequent runs
```

# Docker

Mount your sensitive files as volumes — they are excluded from the image by `.dockerignore`.

```bash
# Build
docker compose build

# Run
COURSE_URL=https://www.udemy.com/course/my-course/ docker compose run udemy-downloader \
  python main.py -c $COURSE_URL -b <token> --download-captions --use-mkv -q 1080
```

**Required volumes** (configured in `docker-compose.yml`):

| Host path | Container path | Purpose |
|-----------|---------------|---------|
| `./output/` | `/app/out_dir/` | Downloaded files |
| `./wvkeys/` | `/app/wvkeys/` | Widevine device files |
| `./cookies.txt` | `/app/cookies.txt` | Browser cookies (if using `--browser file`) |
| `./keyfile.json` | `/app/keyfile.json` | Key cache (auto-written, pre-create as `{}`) |

# Credits

- [Puyodead1/udemy-downloader](https://github.com/Puyodead1/udemy-downloader) — upstream project
- [hyugogirubato/KeyDive](https://github.com/hyugogirubato/KeyDive) — Widevine CDM extraction
- [alastairmccormack/pywvpssh](https://github.com/alastairmccormack/pywvpssh) — PSSH extraction
- [alastairmccormack/pymp4parse](https://github.com/alastairmccormack/pymp4parse) — MP4 box parsing
- [lbrayner/vtt-to-srt](https://github.com/lbrayner/vtt-to-srt) — VTT to SRT conversion
- [r0oth3x49/udemy-dl](https://github.com/r0oth3x49/udemy-dl) — Udemy API reference

## License

MIT
