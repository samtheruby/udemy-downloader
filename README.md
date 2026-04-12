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

# Setup

## 1. Widevine device files (required for DRM courses)

Place one or more Widevine L3 `.wvd` device files in the `wvkeys/` directory (any filename works):

```
wvkeys/
  device1.wvd
  device2.wvd
```

Multiple files are used in round-robin rotation. Keys fetched during downloads are cached in `keyfile.json` automatically.

> [!NOTE]
> Obtaining `.wvd` files is outside the scope of this documentation.

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

This project runs fully via Docker — no local Python or tool installation required.

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/samtheruby/udemy-downloader
cd udemy-downloader

# 2. Create required files
mkdir -p output wvkeys
echo '{}' > keyfile.json
touch cookies.txt   # populate if using --browser file

# 3. Copy your .wvd files into wvkeys/
cp /path/to/your/device.wvd wvkeys/

# 4. Build the image
docker compose build
```

## Running

```bash
docker compose run udemy-downloader python main.py -c <COURSE_URL> [options]
```

### Examples

```bash
# Bearer token auth — best quality, English captions, MKV output
docker compose run udemy-downloader python main.py \
  -c https://www.udemy.com/course/my-course/ \
  -b <token> -q 1080 --download-captions --use-mkv

# Enterprise portal with cookie auth and parallel downloads
docker compose run udemy-downloader python main.py \
  -c https://company.udemy.com/course/my-course/ \
  --browser file -q 1080 --download-captions \
  --use-mkv --keep-subtitles --parallel-lectures 3

# Specific chapters only
docker compose run udemy-downloader python main.py \
  -c <COURSE_URL> -b <token> --chapter "1,3-5,8"

# Captions only (no video)
docker compose run udemy-downloader python main.py \
  -c <COURSE_URL> -b <token> --skip-lectures --download-captions -l all
```

## Volumes

| Host path | Container path | Purpose |
|-----------|---------------|---------|
| `./output/` | `/app/out_dir/` | Downloaded files |
| `./wvkeys/` | `/app/wvkeys/` | Widevine device files (read-only) |
| `./cookies.txt` | `/app/cookies.txt` | Browser cookies for `--browser file` |
| `./keyfile.json` | `/app/keyfile.json` | Key cache (auto-written) |

## All options

```
-c, --course-url URL        Course URL (required)
-b, --bearer TOKEN          Bearer token
--browser {chrome,firefox,opera,edge,brave,chromium,vivaldi,safari,file}
                            Cookie source (use 'file' for cookies.txt)
-q, --quality INT           Video quality (closest match used if unavailable)
-l, --lang LANG             Caption language or 'all' (default: en)
-cd, --concurrent-downloads INT   Segments in parallel per lecture (1-30, default 10)
-pl, --parallel-lectures INT      Lectures in parallel per chapter (1-5, default 1)
--download-assets           Download lecture assets
--download-captions         Download captions
--download-quizzes          Download quizzes
--skip-lectures             Skip video lectures
--skip-hls                  Skip HLS streams (faster fetching)
--use-mkv                   Embed subtitles into MKV container
--keep-subtitles            Export subtitles to out_dir/subtitles/{course}/{lang}/{chapter}/
--keep-vtt                  Keep original .vtt files
-o, --out PATH              Custom output directory
--id-as-course-name         Use course ID as output folder name
-n, --continue-lecture-numbers    Continuous lecture numbering across chapters
--chapter CHAPTERS          Specific chapters, e.g. "1,3-5,7"
--use-h265                  Re-encode to H.265
--h265-crf INT              H.265 CRF value (default 28)
--h265-preset PRESET        H.265 preset (default medium)
--use-nvenc                 NVIDIA GPU H.265 encoding
--info                      Print course info only, no download
--save-to-file              Cache course structure to disk
--load-from-file            Load course structure from cache
--log-level LEVEL           DEBUG / INFO / WARNING / ERROR / CRITICAL
```

# Credits

- [Puyodead1/udemy-downloader](https://github.com/Puyodead1/udemy-downloader) — upstream project
- [hyugogirubato/KeyDive](https://github.com/hyugogirubato/KeyDive) — Widevine CDM extraction
- [alastairmccormack/pywvpssh](https://github.com/alastairmccormack/pywvpssh) — PSSH extraction
- [alastairmccormack/pymp4parse](https://github.com/alastairmccormack/pymp4parse) — MP4 box parsing
- [lbrayner/vtt-to-srt](https://github.com/lbrayner/vtt-to-srt) — VTT to SRT conversion
- [r0oth3x49/udemy-dl](https://github.com/r0oth3x49/udemy-dl) — Udemy API reference

## License

MIT
