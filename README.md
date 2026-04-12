# Udemy Downloader

[![forthebadge](https://forthebadge.com/images/badges/built-with-love.svg)](https://forthebadge.com)
[![forthebadge](https://forthebadge.com/images/badges/made-with-python.svg)](https://forthebadge.com)
![GitHub forks](https://img.shields.io/github/forks/samtheruby/udemy-downloader?style=for-the-badge)
![GitHub Repo stars](https://img.shields.io/github/stars/samtheruby/udemy-downloader?style=for-the-badge)
![License](https://img.shields.io/badge/license-BSD%203--Clause-blue?style=for-the-badge)

> [!IMPORTANT]
> Downloading courses is against Udemy's Terms of Service. Use at your own risk.

Downloads Udemy courses including Widevine DRM-protected content. Runs entirely in Docker — no local dependencies to install.

Fork of [Puyodead1/udemy-downloader](https://github.com/Puyodead1/udemy-downloader) with automatic Widevine key fetching, parallel downloads, MKV output, subtitle export, and enterprise portal support.

---

# Quick Start

## Step 1 — Prerequisites

- Docker
- A Widevine L3 `.wvd` device file (see note below)
- Your Udemy course URL

> [!NOTE]
> Obtaining `.wvd` device files is outside the scope of this documentation.

---

## Step 2 — Clone and prepare

```bash
git clone https://github.com/samtheruby/udemy-downloader
cd udemy-downloader

# Create the required directories and files
mkdir -p output wvkeys
echo '{}' > keyfile.json
```

Place your `.wvd` file(s) in the `wvkeys/` folder — any filename is fine:

```
wvkeys/
  my_device.wvd
```

---

## Step 3 — Authentication

Choose **one** of the following methods:

### Option A — Bearer token (standard Udemy accounts)

1. Open your browser and log in to Udemy
2. Open DevTools (F12) → Network tab → filter for `api-2.0`
3. Click any request and find the `Authorization: Bearer <token>` header
4. Copy the token

Pass it at runtime with `-b <token>`, or store it permanently:

```bash
cp .env.sample .env
# Open .env and set: UDEMY_BEARER=your_token_here
```

### Option B — Cookies (enterprise portals / subscription accounts)

1. Log in to your Udemy portal in your browser
2. Export cookies in Netscape format to `cookies.txt` using the [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) extension
3. Place `cookies.txt` in the project folder

Pass `--cookies` when running.

---

## Step 4 — Build the Docker image

```bash
docker compose build
```

This only needs to be done once (and again after any code changes).

---

## Step 5 — Download a course

### With bearer token

```bash
docker compose run udemy-downloader python main.py \
  -c https://www.udemy.com/course/my-course/ \
  -b <your_token> \
  -q 1080 \
  --download-captions \
  --use-mkv
```

### With cookies (enterprise portal)

```bash
docker compose run udemy-downloader python main.py \
  -c https://company.udemy.com/course/my-course/ \
  --cookies \
  -q 1080 \
  --download-captions \
  --use-mkv
```

Downloaded files will appear in the `output/` folder.

---

## Step 6 — Re-running / resuming

The downloader skips lectures that are already downloaded. To avoid re-fetching the course structure from Udemy on every run, cache it on the first run and load from cache on subsequent ones:

```bash
# First run — fetch and cache course structure
docker compose run udemy-downloader python main.py -c <URL> -b <token> --save-to-file

# Subsequent runs — load from cache
docker compose run udemy-downloader python main.py -c <URL> -b <token> --load-from-file
```

---

# All Options

| Flag | Description |
|------|-------------|
| `-c, --course-url URL` | Course URL *(required)* |
| `-b, --bearer TOKEN` | Bearer token for auth |
| `--cookies` | Load cookies from `cookies.txt` |
| `-q, --quality INT` | Video quality e.g. `1080`, `720` (best available if omitted) |
| `-l, --lang LANG` | Caption language e.g. `en`, `es`, or `all` (default: `en`) |
| `-cd, --concurrent-downloads INT` | Segment download threads per lecture (1–30, default 10) |
| `-pl, --parallel-lectures INT` | Lectures downloaded in parallel per chapter (1–5, default 1) |
| `--download-captions` | Download subtitles |
| `--download-assets` | Download lecture assets (PDFs, source code, etc.) |
| `--download-quizzes` | Download quizzes as HTML |
| `--skip-lectures` | Skip video downloads (e.g. to grab captions only) |
| `--skip-hls` | Skip HLS streams — faster course fetching, may miss 1080p on non-DRM lectures |
| `--use-mkv` | Output MKV with subtitle tracks embedded (requires mkvmerge in Docker ✓) |
| `--keep-subtitles` | Also save subtitles to `output/subtitles/{course}/{lang}/{chapter}/` |
| `--keep-vtt` | Keep original `.vtt` files alongside `.srt` |
| `--chapter CHAPTERS` | Download specific chapters e.g. `1`, `1,3-5`, `2-4` |
| `--save-to-file` | Cache course structure to disk |
| `--load-from-file` | Load course structure from cache |
| `-o, --out PATH` | Custom output directory |
| `--id-as-course-name` | Use course ID instead of title as folder name |
| `-n, --continue-lecture-numbers` | Number lectures continuously across chapters |
| `--use-h265` | Re-encode video to H.265 (smaller files, slower) |
| `--h265-crf INT` | H.265 CRF value (default 28) |
| `--h265-preset PRESET` | H.265 preset (default `medium`) |
| `--use-nvenc` | Use NVIDIA GPU for H.265 encoding |
| `--info` | Print course info without downloading |
| `--log-level LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |

---

# Volume Reference

All sensitive files are mounted at runtime — they are never baked into the Docker image.

| Host path | Container path | Notes |
|-----------|---------------|-------|
| `./output/` | `/app/out_dir/` | Downloaded course files |
| `./wvkeys/` | `/app/wvkeys/` | Widevine device files |
| `./cookies.txt` | `/app/cookies.txt` | Required when using `--cookies` |
| `./keyfile.json` | `/app/keyfile.json` | Key cache — auto-written, start with `{}` |

---

# Credits

- [Puyodead1/udemy-downloader](https://github.com/Puyodead1/udemy-downloader) — upstream project
- [alastairmccormack/pywvpssh](https://github.com/alastairmccormack/pywvpssh) — PSSH extraction
- [alastairmccormack/pymp4parse](https://github.com/alastairmccormack/pymp4parse) — MP4 box parsing
- [lbrayner/vtt-to-srt](https://github.com/lbrayner/vtt-to-srt) — VTT to SRT conversion
- [r0oth3x49/udemy-dl](https://github.com/r0oth3x49/udemy-dl) — Udemy API reference

## License

BSD 3-Clause. Original work copyright (c) 2021 Puyodead1 (MIT), fork copyright (c) 2024 samtheruby.
