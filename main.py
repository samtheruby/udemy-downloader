# -*- coding: utf-8 -*-
import argparse
import json
import logging
import logging.handlers as _log_handlers
import queue as _queue
import threading
import math
import os
import re
import subprocess
import sys
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import IO, Union

import demoji
import m3u8
import requests
from curl_cffi import requests as requests2
import yt_dlp
from coloredlogs import ColoredFormatter
from dotenv import load_dotenv
from pathvalidate import sanitize_filename
from requests.exceptions import ConnectionError as conn_error
from tqdm import tqdm

from constants import *
from tls import SSLCiphers
from utils import extract_kid
from vtt_to_srt import convert

DOWNLOAD_DIR = os.path.join(os.getcwd(), "out_dir")
MAIN_SCRIPT_PATH = os.path.dirname(os.path.abspath(__file__))

retry = 3
downloader = None
logger: logging.Logger = None
dl_assets = False
dl_captions = False
dl_quizzes = False
skip_lectures = False
caption_locale = "en"
quality = None
bearer_token = None
portal_name = None
course_name = None
keep_vtt = False
skip_hls = False
concurrent_downloads = 10
save_to_file = None
load_from_file = None
course_url = None
info = None
id_as_course_name = False
is_subscription_course = False
use_h265 = False
h265_crf = 28
h265_preset = "medium"
use_nvenc = False
browser = None
use_continuous_lecture_numbers = False
chapter_filter = None
parallel_lectures = 1
use_mkv = False
keep_subtitles = False
_udemy_instance = None  # set in parse_new so _process_one_lecture threads can access it

keys = {}
_keys_lock = threading.Lock()
udemy_session = None  # shared session used for Widevine license requests

WVD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "device.wvd")
WVD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wvkeys")
_wvd_files = []
_wvd_index = 0
_wvd_lock = threading.Lock()


def _load_wvd_files():
    """Scan wvkeys/ for .wvd files. Falls back to device.wvd if none found."""
    global _wvd_files
    import glob as _glob
    found = sorted(_glob.glob(os.path.join(WVD_DIR, "*.wvd")))
    if found:
        _wvd_files = found
    elif os.path.exists(WVD_PATH):
        _wvd_files = [WVD_PATH]
    else:
        _wvd_files = []


def _get_wvd_path():
    """Return the next WVD file in round-robin order."""
    global _wvd_index
    if not _wvd_files:
        return None
    with _wvd_lock:
        path = _wvd_files[_wvd_index % len(_wvd_files)]
        _wvd_index += 1
    return path


def deEmojify(inputStr: str):
    return demoji.replace(inputStr, "")


# from https://stackoverflow.com/a/21978778/9785713
def log_subprocess_output(prefix: str, pipe: IO[bytes]):
    if pipe:
        for line in pipe:
            logger.debug("[%s]: %s", prefix, line.decode("utf8", errors="replace").rstrip())


def parse_chapter_filter(chapter_str: str):
    """
    Given a string like "1,3-5,7,9-11", return a set of chapter numbers.
    """
    chapters = set()
    for part in chapter_str.split(","):
        if "-" in part:
            try:
                start, end = part.split("-")
                start = int(start.strip())
                end = int(end.strip())
                chapters.update(range(start, end + 1))
            except ValueError:
                logger.error("Invalid range in --chapter argument: %s", part)
        else:
            try:
                chapters.add(int(part.strip()))
            except ValueError:
                logger.error("Invalid chapter number in --chapter argument: %s", part)
    return chapters


# this is the first function that is called, we parse the arguments, setup the logger, and ensure that required directories exist
def pre_run():
    global dl_assets, dl_captions, dl_quizzes, skip_lectures, caption_locale, quality, bearer_token, course_name, keep_vtt, skip_hls, concurrent_downloads, load_from_file, save_to_file, bearer_token, course_url, info, logger, keys, id_as_course_name, LOG_LEVEL, use_h265, h265_crf, h265_preset, use_nvenc, browser, is_subscription_course, DOWNLOAD_DIR, use_continuous_lecture_numbers, chapter_filter, parallel_lectures, use_mkv, keep_subtitles, udemy_session

    # make sure the logs directory exists
    if not os.path.exists(LOG_DIR_PATH):
        os.makedirs(LOG_DIR_PATH, exist_ok=True)

    parser = argparse.ArgumentParser(description="Udemy Downloader")
    parser.add_argument(
        "-c",
        "--course-url",
        dest="course_url",
        type=str,
        help="The URL of the course to download",
        required=True,
    )
    parser.add_argument(
        "-b",
        "--bearer",
        dest="bearer_token",
        type=str,
        help="The Bearer token to use",
    )
    parser.add_argument(
        "-q",
        "--quality",
        dest="quality",
        type=int,
        help="Download specific video quality. If the requested quality isn't available, the closest quality will be used. If not specified, the best quality will be downloaded for each lecture",
    )
    parser.add_argument(
        "-l",
        "--lang",
        dest="lang",
        type=str,
        help="The language to download for captions, specify 'all' to download all captions (Default is 'en')",
    )
    parser.add_argument(
        "-cd",
        "--concurrent-downloads",
        dest="concurrent_downloads",
        type=int,
        help="The number of maximum concurrent downloads for segments (HLS and DASH, must be a number 1-30)",
    )
    parser.add_argument(
        "-pl",
        "--parallel-lectures",
        dest="parallel_lectures",
        type=int,
        default=1,
        help="Number of lectures to download in parallel per chapter (default 1, max 5)",
    )
    parser.add_argument(
        "--use-mkv",
        dest="use_mkv",
        action="store_true",
        help="Output MKV instead of MP4, with all downloaded subtitles embedded as tracks",
    )
    parser.add_argument(
        "--keep-subtitles",
        dest="keep_subtitles",
        action="store_true",
        help="Copy all subtitles to out_dir/subtitles/{course}/{language}/{chapter}/ for easy AI summarization",
    )
    parser.add_argument(
        "--skip-lectures",
        dest="skip_lectures",
        action="store_true",
        help="If specified, lectures won't be downloaded",
    )
    parser.add_argument(
        "--download-assets",
        dest="download_assets",
        action="store_true",
        help="If specified, lecture assets will be downloaded",
    )
    parser.add_argument(
        "--download-captions",
        dest="download_captions",
        action="store_true",
        help="If specified, captions will be downloaded",
    )
    parser.add_argument(
        "--download-quizzes",
        dest="download_quizzes",
        action="store_true",
        help="If specified, quizzes will be downloaded",
    )
    parser.add_argument(
        "--keep-vtt",
        dest="keep_vtt",
        action="store_true",
        help="If specified, .vtt files won't be removed",
    )
    parser.add_argument(
        "--skip-hls",
        dest="skip_hls",
        action="store_true",
        help="If specified, hls streams will be skipped (faster fetching) (hls streams usually contain 1080p quality for non-drm lectures)",
    )
    parser.add_argument(
        "--info",
        dest="info",
        action="store_true",
        help="If specified, only course information will be printed, nothing will be downloaded",
    )
    parser.add_argument(
        "--id-as-course-name",
        dest="id_as_course_name",
        action="store_true",
        help="If specified, the course id will be used in place of the course name for the output directory. This is a 'hack' to reduce the path length",
    )
    parser.add_argument(
        "-sc",
        "--subscription-course",
        dest="is_subscription_course",
        action="store_true",
        help="Mark the course as a subscription based course, use this if you are having problems with the program auto detecting it",
    )
    parser.add_argument(
        "--save-to-file",
        dest="save_to_file",
        action="store_true",
        help="If specified, course content will be saved to a file that can be loaded later with --load-from-file, this can reduce processing time (Note that asset links expire after a certain amount of time)",
    )
    parser.add_argument(
        "--load-from-file",
        dest="load_from_file",
        action="store_true",
        help="If specified, course content will be loaded from a previously saved file with --save-to-file, this can reduce processing time (Note that asset links expire after a certain amount of time)",
    )
    parser.add_argument(
        "--log-level",
        dest="log_level",
        type=str,
        help="Logging level: one of DEBUG, INFO, ERROR, WARNING, CRITICAL (Default is INFO)",
    )
    parser.add_argument(
        "--cookies",
        dest="browser",
        action="store_const",
        const="file",
        help="Load cookies from cookies.txt (Netscape format)",
    )
    parser.add_argument(
        "--use-h265",
        dest="use_h265",
        action="store_true",
        help="If specified, videos will be encoded with the H.265 codec",
    )
    parser.add_argument(
        "--h265-crf",
        dest="h265_crf",
        type=int,
        default=28,
        help="Set a custom CRF value for H.265 encoding. FFMPEG default is 28",
    )
    parser.add_argument(
        "--h265-preset",
        dest="h265_preset",
        type=str,
        default="medium",
        help="Set a custom preset value for H.265 encoding. FFMPEG default is medium",
    )
    parser.add_argument(
        "--use-nvenc",
        dest="use_nvenc",
        action="store_true",
        help="Whether to use the NVIDIA hardware transcoding for H.265. Only works if you have a supported NVIDIA GPU and ffmpeg with nvenc support",
    )
    parser.add_argument(
        "--out",
        "-o",
        dest="out",
        type=str,
        help="Set the path to the output directory",
    )
    parser.add_argument(
        "--continue-lecture-numbers",
        "-n",
        dest="use_continuous_lecture_numbers",
        action="store_true",
        help="Use continuous lecture numbering instead of per-chapter",
    )
    parser.add_argument(
        "--chapter",
        dest="chapter_filter_raw",
        type=str,
        help="Download specific chapters. Use comma separated values and ranges (e.g., '1,3-5,7,9-11').",
    )
    # parser.add_argument("-v", "--version", action="version", version="You are running version {version}".format(version=__version__))

    args = parser.parse_args()
    if args.download_assets:
        dl_assets = True
    if args.lang:
        caption_locale = args.lang
    if args.download_captions:
        dl_captions = True
    if args.download_quizzes:
        dl_quizzes = True
    if args.skip_lectures:
        skip_lectures = True
    if args.quality:
        quality = args.quality
    if args.keep_vtt:
        keep_vtt = args.keep_vtt
    if args.skip_hls:
        skip_hls = args.skip_hls
    if args.concurrent_downloads:
        concurrent_downloads = args.concurrent_downloads

        if concurrent_downloads <= 0:
            # if the user gave a number that is less than or equal to 0, set cc to default of 10
            concurrent_downloads = 10
        elif concurrent_downloads > 30:
            # if the user gave a number thats greater than 30, set cc to the max of 30
            concurrent_downloads = 30
    if args.load_from_file:
        load_from_file = args.load_from_file
    if args.save_to_file:
        save_to_file = args.save_to_file
    if args.bearer_token:
        bearer_token = args.bearer_token
    if args.course_url:
        course_url = args.course_url
    if args.info:
        info = args.info
    if args.use_h265:
        use_h265 = True
    if args.h265_crf:
        h265_crf = args.h265_crf
    if args.h265_preset:
        h265_preset = args.h265_preset
    if args.use_nvenc:
        use_nvenc = True
    if args.log_level:
        if args.log_level.upper() == "DEBUG":
            LOG_LEVEL = logging.DEBUG
        elif args.log_level.upper() == "INFO":
            LOG_LEVEL = logging.INFO
        elif args.log_level.upper() == "ERROR":
            LOG_LEVEL = logging.ERROR
        elif args.log_level.upper() == "WARNING":
            LOG_LEVEL = logging.WARNING
        elif args.log_level.upper() == "CRITICAL":
            LOG_LEVEL = logging.CRITICAL
        else:
            print(f"Invalid log level: {args.log_level}; Using INFO")
            LOG_LEVEL = logging.INFO
    if args.id_as_course_name:
        id_as_course_name = args.id_as_course_name
    if args.is_subscription_course:
        is_subscription_course = args.is_subscription_course
    if args.browser:
        browser = args.browser
    if args.out:
        DOWNLOAD_DIR = os.path.abspath(args.out)
    if args.use_continuous_lecture_numbers:
        use_continuous_lecture_numbers = args.use_continuous_lecture_numbers
    parallel_lectures = max(1, min(5, args.parallel_lectures))
    if args.use_mkv:
        use_mkv = True
    if args.keep_subtitles:
        keep_subtitles = True

    # setup a logger
    logging.root.setLevel(LOG_LEVEL)

    # Thread-safe logging: all threads enqueue records; one listener writes them serially
    _log_queue = _queue.Queue(-1)

    # create a colored formatter for the console
    console_formatter = ColoredFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    # create a regular non-colored formatter for the log file
    file_formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # create a handler for console logging
    stream = logging.StreamHandler()
    stream.setLevel(LOG_LEVEL)
    stream.setFormatter(console_formatter)

    # create a handler for file logging
    file_handler = logging.FileHandler(LOG_FILE_PATH)
    file_handler.setFormatter(file_formatter)

    # QueueListener writes serially from the queue — no interleaving between threads
    _listener = _log_handlers.QueueListener(_log_queue, stream, file_handler, respect_handler_level=True)
    _listener.start()

    # construct the logger
    logger = logging.getLogger("udemy-downloader")
    logger.setLevel(LOG_LEVEL)
    logger.handlers.clear()
    logger.addHandler(_log_handlers.QueueHandler(_log_queue))
    logger.propagate = False

    _load_wvd_files()
    if _wvd_files:
        logger.info(f"> Loaded {len(_wvd_files)} WVD device(s) for key rotation: {[os.path.basename(p) for p in _wvd_files]}")
    else:
        logger.warning("> No WVD files found in wvkeys/ and no device.wvd fallback — DRM decryption will fail")

    logger.info(f"Output directory set to {DOWNLOAD_DIR}")

    Path(DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
    Path(SAVED_DIR).mkdir(parents=True, exist_ok=True)

    # Clear and reset the keyfile on every run so stale keys don't mask
    # license request failures.  Fresh keys are fetched and re-cached as
    # each DRM lecture is processed.
    keys = {}
    with open(KEY_FILE_PATH, encoding="utf8", mode="w") as keyfile:
        keyfile.write("{}")

    # Process the chapter filter
    if args.chapter_filter_raw:
        chapter_filter = parse_chapter_filter(args.chapter_filter_raw)
        logger.info("Chapter filter applied: %s", sorted(chapter_filter))


class Udemy:
    def __init__(self, bearer_token):
        self.session = None
        self.bearer_token = bearer_token
        self.auth = UdemyAuth(cache_session=False)

    def authenticate(self, portal_name):
        if not self.session:
            if self.bearer_token:
                self.session = self.auth.authenticate(bearer_token=self.bearer_token)
            else:
                if browser == None:
                    logger.error(
                        "No bearer token provided and --cookies not specified. Pass -b <token> or --cookies."
                    )
                    sys.exit(1)

                logger.warning(
                    "No bearer token provided, authenticating via cookies.txt."
                )

                self.session = self.auth._session

                cj = MozillaCookieJar("cookies.txt")
                cj.load()

                self.session._session.cookies.update(cj)

                # Strip Android-specific headers — enterprise portals (e.g. gale.udemy.com)
                # reject API requests that carry both browser cookies and mobile client headers.
                for h in ["authorization", "x-udemy-client-secret", "x-udemy-client-id",
                          "x-mobile-visit-enabled", "x-version-name", "x-client-name"]:
                    self.session._session.headers.pop(h, None)
        else:
            # remove the authentication header
            del self.session._session.headers["authorization"]

    def _get_quiz(self, quiz_id):
        # self.session._headers.update(
        #     {
        #         "Host": "{portal_name}.udemy.com".format(portal_name=portal_name),
        #         "Referer": "https://{portal_name}.udemy.com/course/{course_name}/learn/quiz/{quiz_id}".format(
        #             portal_name=portal_name, course_name=course_name, quiz_id=quiz_id
        #         ),
        #     }
        # )
        url = URLS.QUIZ.format(portal_name=portal_name, quiz_id=quiz_id)
        return self._handle_pagination(url, None).get("results")

    def _get_elem_value_or_none(self, elem, key):
        return elem[key] if elem and key in elem else "(None)"

    def _get_quiz_with_info(self, quiz_id):
        resp = {"_class": None, "_type": None, "contents": None}
        quiz_json = self._get_quiz(quiz_id)
        is_only_one = len(quiz_json) == 1 and quiz_json[0]["_class"] == "assessment"
        is_coding_assignment = quiz_json[0]["assessment_type"] == "coding-problem"

        resp["_class"] = quiz_json[0]["_class"]

        if is_only_one and is_coding_assignment:
            assignment = quiz_json[0]
            prompt = assignment["prompt"]

            resp["_type"] = assignment["assessment_type"]

            resp["contents"] = {
                "instructions": self._get_elem_value_or_none(prompt, "instructions"),
                "tests": self._get_elem_value_or_none(prompt, "test_files"),
                "solutions": self._get_elem_value_or_none(prompt, "solution_files"),
            }

            resp["hasInstructions"] = (
                False if resp["contents"]["instructions"] == "(None)" else True
            )
            resp["hasTests"] = (
                False if isinstance(resp["contents"]["tests"], str) else True
            )
            resp["hasSolutions"] = (
                False if isinstance(resp["contents"]["solutions"], str) else True
            )
        else:  # Normal quiz
            resp["_type"] = "normal-quiz"
            resp["contents"] = quiz_json

        return resp

    def _extract_supplementary_assets(self, supp_assets, lecture_counter):
        _temp = []
        for entry in supp_assets:
            title = sanitize_filename(entry.get("title"))
            filename = entry.get("filename")
            download_urls = entry.get("download_urls")
            external_url = entry.get("external_url")
            asset_type = entry.get("asset_type").lower()
            id = entry.get("id")
            if asset_type == "file":
                if download_urls and isinstance(download_urls, dict):
                    extension = filename.rsplit(".", 1)[-1] if "." in filename else ""
                    download_url = download_urls.get("File", [])[0].get("file")
                    _temp.append(
                        {
                            "type": "file",
                            "title": title,
                            "filename": "{0:03d} ".format(lecture_counter) + filename,
                            "extension": extension,
                            "download_url": download_url,
                            "id": id,
                        }
                    )
            elif asset_type == "sourcecode":
                if download_urls and isinstance(download_urls, dict):
                    extension = filename.rsplit(".", 1)[-1] if "." in filename else ""
                    download_url = download_urls.get("SourceCode", [])[0].get("file")
                    _temp.append(
                        {
                            "type": "source_code",
                            "title": title,
                            "filename": "{0:03d} ".format(lecture_counter) + filename,
                            "extension": extension,
                            "download_url": download_url,
                            "id": id,
                        }
                    )
            elif asset_type == "externallink":
                _temp.append(
                    {
                        "type": "external_link",
                        "title": title,
                        "filename": "{0:03d} ".format(lecture_counter) + filename,
                        "extension": "txt",
                        "download_url": external_url,
                        "id": id,
                    }
                )
        return _temp

    def _extract_article(self, asset, id):
        return [
            {
                "type": "article",
                "body": asset.get("body"),
                "extension": "html",
                "id": id,
            }
        ]

    def _extract_ppt(self, asset, lecture_counter):
        _temp = []
        download_urls = asset.get("download_urls")
        filename = asset.get("filename")
        id = asset.get("id")
        if download_urls and isinstance(download_urls, dict):
            extension = filename.rsplit(".", 1)[-1] if "." in filename else ""
            download_url = download_urls.get("Presentation", [])[0].get("file")
            _temp.append(
                {
                    "type": "presentation",
                    "filename": "{0:03d} ".format(lecture_counter) + filename,
                    "extension": extension,
                    "download_url": download_url,
                    "id": id,
                }
            )
        return _temp

    def _extract_file(self, asset, lecture_counter):
        _temp = []
        download_urls = asset.get("download_urls")
        filename = asset.get("filename")
        id = asset.get("id")
        if download_urls and isinstance(download_urls, dict):
            extension = filename.rsplit(".", 1)[-1] if "." in filename else ""
            download_url = download_urls.get("File", [])[0].get("file")
            _temp.append(
                {
                    "type": "file",
                    "filename": "{0:03d} ".format(lecture_counter) + filename,
                    "extension": extension,
                    "download_url": download_url,
                    "id": id,
                }
            )
        return _temp

    def _extract_ebook(self, asset, lecture_counter):
        _temp = []
        download_urls = asset.get("download_urls")
        filename = asset.get("filename")
        id = asset.get("id")
        if download_urls and isinstance(download_urls, dict):
            extension = filename.rsplit(".", 1)[-1] if "." in filename else ""
            download_url = download_urls.get("E-Book", [])[0].get("file")
            _temp.append(
                {
                    "type": "ebook",
                    "filename": "{0:03d} ".format(lecture_counter) + filename,
                    "extension": extension,
                    "download_url": download_url,
                    "id": id,
                }
            )
        return _temp

    def _extract_audio(self, asset, lecture_counter):
        _temp = []
        download_urls = asset.get("download_urls")
        filename = asset.get("filename")
        id = asset.get("id")
        if download_urls and isinstance(download_urls, dict):
            extension = filename.rsplit(".", 1)[-1] if "." in filename else ""
            download_url = download_urls.get("Audio", [])[0].get("file")
            _temp.append(
                {
                    "type": "audio",
                    "filename": "{0:03d} ".format(lecture_counter) + filename,
                    "extension": extension,
                    "download_url": download_url,
                    "id": id,
                }
            )
        return _temp

    def _extract_sources(self, sources, skip_hls):
        _temp = []
        if sources and isinstance(sources, list):
            for source in sources:
                label = source.get("label")
                download_url = source.get("file")
                if not download_url:
                    continue
                if label.lower() == "audio":
                    continue
                height = label if label else None
                if height == "2160":
                    width = "3840"
                elif height == "1440":
                    width = "2560"
                elif height == "1080":
                    width = "1920"
                elif height == "720":
                    width = "1280"
                elif height == "480":
                    width = "854"
                elif height == "360":
                    width = "640"
                elif height == "240":
                    width = "426"
                else:
                    width = "256"
                if (
                    source.get("type") == "application/x-mpegURL"
                    or "m3u8" in download_url
                ):
                    if not skip_hls:
                        out = self._extract_m3u8(download_url)
                        if out:
                            _temp.extend(out)
                else:
                    _type = source.get("type")
                    _temp.append(
                        {
                            "type": "video",
                            "height": height,
                            "width": width,
                            "extension": _type.replace("video/", ""),
                            "download_url": download_url,
                        }
                    )
        return _temp

    def _extract_media_sources(self, sources):
        _temp = []
        if sources and isinstance(sources, list):
            for source in sources:
                _type = source.get("type")
                src = source.get("src")

                if _type == "application/dash+xml":
                    out = self._extract_mpd(src)
                    if out:
                        _temp.extend(out)
        return _temp

    def _extract_subtitles(self, tracks):
        _temp = []
        if tracks and isinstance(tracks, list):
            for track in tracks:
                if not isinstance(track, dict):
                    continue
                if track.get("_class") != "caption":
                    continue
                download_url = track.get("url")
                if not download_url or not isinstance(download_url, str):
                    continue
                lang = (
                    track.get("language")
                    or track.get("srclang")
                    or track.get("label")
                    or track["locale_id"].split("_")[0]
                )
                ext = "vtt" if "vtt" in download_url.rsplit(".", 1)[-1] else "srt"
                _temp.append(
                    {
                        "type": "subtitle",
                        "language": lang,
                        "extension": ext,
                        "download_url": download_url,
                    }
                )
        return _temp

    def _extract_m3u8(self, url):
        """extracts m3u8 streams"""
        asset_id_re = re.compile(r"assets/(?P<id>\d+)/")
        _temp = []

        # get temp folder
        temp_path = Path(Path.cwd(), "temp")

        # ensure the folder exists
        temp_path.mkdir(parents=True, exist_ok=True)

        # # extract the asset id from the url
        asset_id = asset_id_re.search(url).group("id")

        m3u8_path = Path(temp_path, f"index_{asset_id}.m3u8")

        try:
            r = self.session._get(url)
            r.raise_for_status()
            raw_data = r.text

            # write to temp file for later
            with open(m3u8_path, "w") as f:
                f.write(r.text)

            m3u8_object = m3u8.loads(raw_data)
            playlists = m3u8_object.playlists
            seen = set()
            for pl in playlists:
                resolution = pl.stream_info.resolution
                codecs = pl.stream_info.codecs

                if not resolution:
                    continue
                if not codecs:
                    continue
                width, height = resolution

                if height in seen:
                    continue

                # we need to save the individual playlists to disk also
                playlist_path = Path(
                    temp_path, f"index_{asset_id}_{width}x{height}.m3u8"
                )

                with open(playlist_path, "w") as f:
                    r = self.session._get(pl.uri)
                    r.raise_for_status()
                    f.write(r.text)

                seen.add(height)
                _temp.append(
                    {
                        "type": "hls",
                        "height": height,
                        "width": width,
                        "extension": "mp4",
                        "download_url": playlist_path.as_uri(),
                    }
                )
        except Exception as error:
            logger.error(f"Udemy Says : '{error}' while fetching hls streams..")
        return _temp

    def _extract_mpd(self, url):
        """extracts mpd streams"""
        _temp = {}

        try:
            ytdl = yt_dlp.YoutubeDL(
                {
                    "quiet": True,
                    "no_warnings": True,
                    "allow_unplayable_formats": True,
                }
            )
            results = ytdl.extract_info(
                url, download=False, force_generic_extractor=True
            )
            formats = results.get("formats", [])
            best_audio = next(
                f for f in formats if (f["acodec"] != "none" and f["vcodec"] == "none")
            )
            # filter formats to remove any audio only formats
            formats = [
                f for f in formats if f["vcodec"] != "none" and f["acodec"] == "none"
            ]
            if not best_audio:
                raise ValueError("No suitable audio format found in MPD")
            audio_format_id = best_audio.get("format_id")

            for format in formats:
                video_format_id = format.get("format_id")
                extension = format.get("ext")
                height = format.get("height")
                width = format.get("width")
                tbr = format.get("tbr", 0)

                # add to dict based on height
                if height not in _temp:
                    _temp[height] = []

                _temp[height].append(
                    {
                        "type": "dash",
                        "height": str(height),
                        "width": str(width),
                        "format_id": f"{video_format_id},{audio_format_id}",
                        "extension": extension,
                        "download_url": url,
                        "tbr": round(tbr),
                    }
                )
            # for each resolution, use only the highest bitrate
            _temp2 = []
            for height, formats in _temp.items():
                if formats:
                    # sort by tbr and take the first one
                    formats.sort(key=lambda x: x["tbr"], reverse=True)
                    _temp2.append(formats[0])
                else:
                    del _temp[height]

            _temp = _temp2
        except Exception:
            logger.exception(f"Error fetching MPD streams")

        # We don't delete the mpd file yet because we can use it to download later
        return _temp

    def extract_course_name(self, url):
        """
        @author r0oth3x49
        """
        obj = re.search(
            r"(?i)(?://(?P<portal_name>.+?).udemy.com/(?:course(/draft)*/)?(?P<name_or_id>[a-zA-Z0-9_-]+))",
            url,
        )
        if obj:
            return obj.group("portal_name"), obj.group("name_or_id")

    def extract_portal_name(self, url):
        obj = re.search(r"(?i)(?://(?P<portal_name>.+?).udemy.com)", url)
        if obj:
            return obj.group("portal_name")

    def _handle_pagination(self, initial_url, initial_params=None):
        """Helper function to handle paginated requests and return all results

        Args:
            initial_url (str): The initial URL to fetch from
            initial_params (dict, optional): Query parameters for the initial request. Defaults to None.

        Returns:
            dict: Combined results from all pages
        """
        page = 1
        try:
            data = self.session._get(initial_url, initial_params).json()
        except conn_error as error:
            logger.fatal(f"Connection error: {error}")
            time.sleep(0.8)
            sys.exit(1)
        else:
            _next = data.get("next")
            _count = data.get("count")

            if _count is None:
                logger.warning(f"API Response missing 'count'. Data: {data}")
                return data.get("results", []) if "results" in data else []

            est_page_count = math.ceil(_count / 100)  # 100 is the max results per page

            while _next:
                logger.info(f"> Downloading data page {page + 1}/{est_page_count}")
                try:
                    resp = self.session._get(_next)
                    if not resp.ok:
                        logger.error(f"Failed to fetch page {page + 1}, retrying...")
                        continue
                    resp = resp.json()
                except conn_error as error:
                    logger.fatal(f"Connection error: {error}")
                    time.sleep(0.8)
                    sys.exit(1)
                else:
                    _next = resp.get("next")
                    results = resp.get("results")
                    if results and isinstance(results, list):
                        for item in resp["results"]:
                            data["results"].append(item)
                        page = page + 1
            return data

    def _get_subscribed_courses(self, portal_name):
        """
        Fetches the list of courses the user is subscribed to.
        """
        url = URLS.MY_COURSES.format(portal_name=portal_name)
        res = self._handle_pagination(url)
        return res["results"] if res and isinstance(res, dict) else []

    def _get_subscription_course_enrollments(self, portal_name):
        """
        Fetches the list of courses the user is subscribed to.
        """
        url = URLS.SUBSCRIPTION_COURSES.format(portal_name=portal_name)
        res = self._handle_pagination(url)
        return res["results"] if res and isinstance(res, dict) else []

    def _get_courses(self, portal_name):
        a = self._get_subscribed_courses(portal_name)
        b = self._get_subscription_course_enrollments(portal_name)
        return a + b

    def _extract_course_info_json(self, url, course_id):
        # self.session._headers.update({"Referer": url})
        url = URLS.COURSE.format(portal_name=portal_name, course_id=course_id)
        try:
            resp = self.session._get(url).json()
        except conn_error as error:
            logger.fatal(f"Connection error: {error}")
            time.sleep(0.8)
            sys.exit(1)
        else:
            return resp

    def _extract_course_curriculum(self, url, course_id, portal_name):
        # self.session._headers.update({"Referer": url})
        url = URLS.CURRICULUM_ITEMS.format(portal_name=portal_name, course_id=course_id)
        return self._handle_pagination(url, CURRICULUM_ITEMS_PARAMS)

    def _extract_course(self, response, course_name):
        _temp = {}
        if response:
            for entry in response:
                course_id = str(entry.get("id"))
                published_title = entry.get("published_title")
                if course_name in (published_title, course_id):
                    _temp = entry
                    break
        return _temp

    def _subscribed_collection_courses(self, portal_name):
        url = URLS.COLLECTION.format(portal_name=portal_name)
        courses_lists = []
        try:
            webpage = self.session._get(url).json()
        except conn_error as error:
            logger.fatal(f"Connection error: {error}")
            time.sleep(0.8)
            sys.exit(1)
        except (ValueError, Exception) as error:
            logger.fatal(f"{error}")
            time.sleep(0.8)
            sys.exit(1)
        else:
            results = webpage.get("results", [])
            if results:
                [
                    courses_lists.extend(courses.get("courses", []))
                    for courses in results
                    if courses.get("courses", [])
                ]
        return courses_lists

    def _archived_courses(self, portal_name):
        results = []
        try:
            url = URLS.MY_COURSES.format(portal_name=portal_name)
            url = f"{url}&is_archived=true"
            webpage = self.session._get(url).json()
        except conn_error as error:
            logger.fatal(f"Connection error: {error}")
            time.sleep(0.8)
            sys.exit(1)
        except (ValueError, Exception) as error:
            logger.fatal(f"{error}")
            time.sleep(0.8)
            sys.exit(1)
        else:
            results = webpage.get("results", [])
        return results


    def _extract_course_info(self, url):
        global portal_name
        portal_name, course_name = self.extract_course_name(url)
        course = {"portal_name": portal_name}

        # get all the courses
        results = self._get_courses(portal_name=portal_name)
        # find the course that matches the url slug
        course = self._extract_course(response=results, course_name=course_name)
        if not course:
            # try archived courses
            results = self._archived_courses(portal_name=portal_name)
            course = self._extract_course(response=results, course_name=course_name)

        # if not course or is_subscription_course:
        #     course_id = self._extract_subscription_course_info(url)
        #     course = self._extract_course_info_json(url, course_id)

        if course:
            return course.get("id"), course
        if not course:
            logger.fatal("Failed to find the course, are you enrolled?")
            # self.session.terminate()

            sys.exit(1)

    def _parse_lecture(self, lecture: dict):
        retVal = []

        index = lecture.get("index")  # this is lecture_counter
        lecture_data = lecture.get("data")
        asset = lecture_data.get("asset")
        supp_assets = lecture_data.get("supplementary_assets")

        if isinstance(asset, dict):
            asset_type = (
                asset.get("asset_type").lower() or asset.get("assetType").lower()
            )
            if asset_type == "article":
                retVal.extend(self._extract_article(asset, index))
            elif asset_type == "video":
                pass
            elif asset_type == "e-book":
                retVal.extend(self._extract_ebook(asset, index))
            elif asset_type == "file":
                retVal.extend(self._extract_file(asset, index))
            elif asset_type == "presentation":
                retVal.extend(self._extract_ppt(asset, index))
            elif asset_type == "audio":
                retVal.extend(self._extract_audio(asset, index))
            else:
                logger.warning(f"Unknown asset type: {asset_type}")

            if isinstance(supp_assets, list) and len(supp_assets) > 0:
                retVal.extend(self._extract_supplementary_assets(supp_assets, index))

        if asset != None:
            stream_urls = asset.get("stream_urls")
            if stream_urls != None:
                # not encrypted
                if stream_urls and isinstance(stream_urls, dict):
                    sources = stream_urls.get("Video")
                    tracks = asset.get("captions")
                    # duration = asset.get("time_estimation")
                    sources = self._extract_sources(sources, skip_hls)
                    subtitles = self._extract_subtitles(tracks)
                    sources_count = len(sources)
                    subtitle_count = len(subtitles)
                    lecture.pop("data")  # remove the raw data object after processing
                    lecture = {
                        **lecture,
                        "assets": retVal,
                        "assets_count": len(retVal),
                        "sources": sources,
                        "subtitles": subtitles,
                        "subtitle_count": subtitle_count,
                        "sources_count": sources_count,
                        "is_encrypted": False,
                        "asset_id": asset.get("id"),
                        "type": asset.get("asset_type"),
                    }
                else:
                    lecture.pop("data")  # remove the raw data object after processing
                    lecture = {
                        **lecture,
                        "html_content": asset.get("body"),
                        "extension": "html",
                        "assets": retVal,
                        "assets_count": len(retVal),
                        "subtitle_count": 0,
                        "sources_count": 0,
                        "is_encrypted": False,
                        "asset_id": asset.get("id"),
                        "type": asset.get("asset_type"),
                    }
            else:
                # encrypted
                media_sources = asset.get("media_sources")
                if media_sources and isinstance(media_sources, list):
                    sources = self._extract_media_sources(media_sources)
                    tracks = asset.get("captions")
                    # duration = asset.get("time_estimation")
                    subtitles = self._extract_subtitles(tracks)
                    sources_count = len(sources)
                    subtitle_count = len(subtitles)
                    lecture.pop("data")  # remove the raw data object after processing
                    lecture = {
                        **lecture,
                        # "duration": duration,
                        "assets": retVal,
                        "assets_count": len(retVal),
                        "video_sources": sources,
                        "subtitles": subtitles,
                        "subtitle_count": subtitle_count,
                        "sources_count": sources_count,
                        "is_encrypted": True,
                        "asset_id": asset.get("id"),
                        "type": asset.get("asset_type"),
                        "media_license_token": asset.get("media_license_token"),
                    }

                else:
                    lecture.pop("data")  # remove the raw data object after processing
                    lecture = {
                        **lecture,
                        "html_content": asset.get("body"),
                        "extension": "html",
                        "assets": retVal,
                        "assets_count": len(retVal),
                        "subtitle_count": 0,
                        "sources_count": 0,
                        "is_encrypted": False,
                        "asset_id": asset.get("id"),
                        "type": asset.get("asset_type"),
                    }
        else:
            lecture = {
                **lecture,
                "assets": retVal,
                "assets_count": len(retVal),
                "asset_id": lecture_data.get("id"),
                "type": lecture_data.get("type"),
            }

        return lecture


class Session(object):
    def __init__(self):
        self._session = requests2.Session(impersonate="chrome120")
        headers = HEADERS.copy()
        if "User-Agent" in headers:
            del headers["User-Agent"]
        self._session.headers.update(headers)

    def visit(self, portal_name: str) -> bool:
        """
        makes a visit request to get the cloudflare bot cookies and shit
        """
        try:
            url = URLS.VISIT.format(portal_name=portal_name)
            logger.info(f"Visiting {url} to clear Cloudflare...")

            r = self._session.get(url)

            if (
                "challenge-platform" in r.text
                or "<title>Just a moment...</title>" in r.text
            ):
                logger.error("Cloudflare Challenge triggered. Fingerprint failed.")
                return False

            if r.ok:
                logger.info("Visit request successful")
                return True

            logger.error(f"Visit request failed: {r.status_code}")
            return False
        except Exception as e:
            logger.error(f"Request Exception: {e}")
            return False

    def _set_auth_headers(self, bearer_token=""):
        self._session.headers["Authorization"] = "Bearer {}".format(bearer_token)
        self._session.headers["X-Udemy-Authorization"] = "Bearer {}".format(
            bearer_token
        )

    def _get(self, url, data=None, **kwargs):
        if data:
            kwargs["params"] = data

        # This fixes the "Operation timed out after 30002 milliseconds" error
        if "timeout" not in kwargs:
            kwargs["timeout"] = 120

        return self._session.get(url, **kwargs)

    def _post(self, url, data=None, **kwargs):
        if data:
            kwargs["data"] = data
        return self._session.post(url, **kwargs)

    def terminate(self):
        self._session.close()


class UdemyAuth(object):
    def __init__(self, username="", password="", cache_session=False):
        self.username = username
        self.password = password
        self._cache = cache_session
        self._session = Session()

    def authenticate(self, bearer_token=None):
        if bearer_token:
            self._session._set_auth_headers(bearer_token)
            return self._session
        else:
            return None


def durationtoseconds(period):
    """
    @author Jayapraveen
    """

    # Duration format in PTxDxHxMxS
    if period[:2] == "PT":
        period = period[2:]
        day = int(period.split("D")[0] if "D" in period else 0)
        hour = int(period.split("H")[0].split("D")[-1] if "H" in period else 0)
        minute = int(period.split("M")[0].split("H")[-1] if "M" in period else 0)
        second = period.split("S")[0].split("M")[-1]
        # logger.debug("Total time: " + str(day) + " days " + str(hour) + " hours " +
        #       str(minute) + " minutes and " + str(second) + " seconds")
        total_time = float(
            str(
                (day * 24 * 60 * 60)
                + (hour * 60 * 60)
                + (minute * 60)
                + (int(second.split(".")[0]))
            )
            + "."
            + str(int(second.split(".")[-1]))
        )
        return total_time

    else:
        logger.error("Duration Format Error")
        return None


def mux_process(
    video_filepath: str,
    audio_filepath: str,
    video_title: str,
    output_path: str,
    audio_key: Union[str | None] = None,
    video_key: Union[str | None] = None,
):
    codec = "hevc_nvenc" if use_nvenc else "libx265"
    transcode = "-hwaccel cuda -hwaccel_output_format cuda" if use_nvenc else ""
    audio_decryption_arg = (
        f"-decryption_key {audio_key}" if audio_key is not None else ""
    )
    video_decryption_arg = (
        f"-decryption_key {video_key}" if video_key is not None else ""
    )

    if os.name == "nt":
        if use_h265:
            command = f'ffmpeg {transcode} -y {video_decryption_arg} -i "{video_filepath}" {audio_decryption_arg} -i "{audio_filepath}" -c:v {codec} -vtag hvc1 -crf {h265_crf} -preset {h265_preset} -c:a copy -fflags +bitexact -shortest -map_metadata -1 -metadata title="{video_title}" -metadata comment="Downloaded with Udemy-Downloader by Puyodead1 (https://github.com/Puyodead1/udemy-downloader)" "{output_path}"'
        else:
            command = f'ffmpeg -y {video_decryption_arg} -i "{video_filepath}" {audio_decryption_arg} -i "{audio_filepath}" -c copy -fflags +bitexact -shortest -map_metadata -1 -metadata title="{video_title}" -metadata comment="Downloaded with Udemy-Downloader by Puyodead1 (https://github.com/Puyodead1/udemy-downloader)" "{output_path}"'
    else:
        if use_h265:
            command = f'nice -n 7 ffmpeg {transcode} -y {video_decryption_arg} -i "{video_filepath}" {audio_decryption_arg} -i "{audio_filepath}" -c:v {codec} -vtag hvc1 -crf {h265_crf} -preset {h265_preset} -c:a copy -fflags +bitexact -shortest -map_metadata -1 -metadata title="{video_title}" -metadata comment="Downloaded with Udemy-Downloader by Puyodead1 (https://github.com/Puyodead1/udemy-downloader)" "{output_path}"'
        else:
            command = f'nice -n 7 ffmpeg -y {video_decryption_arg} -i "{video_filepath}" {audio_decryption_arg} -i "{audio_filepath}" -c copy -fflags +bitexact -shortest -map_metadata -1 -metadata title="{video_title}" -metadata comment="Downloaded with Udemy-Downloader by Puyodead1 (https://github.com/Puyodead1/udemy-downloader)" "{output_path}"'

    process = subprocess.Popen(command, shell=True)
    log_subprocess_output("FFMPEG-STDOUT", process.stdout)
    log_subprocess_output("FFMPEG-STDERR", process.stderr)
    ret_code = process.wait()
    if ret_code != 0:
        raise Exception("Muxing returned a non-zero exit code")

    return ret_code


def _jwt_user_agent(token):
    """Return the user_agent claim from a JWT payload without verifying the signature."""
    try:
        import base64 as _b64, json as _j
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        return _j.loads(_b64.urlsafe_b64decode(payload)).get('user_agent')
    except Exception:
        return None


def _refresh_license_token(asset_id):
    """Re-fetch a fresh media_license_token for asset_id from the Udemy API."""
    if udemy_session is None or not asset_id:
        return None
    url = (
        f"https://{portal_name}.udemy.com/api-2.0/assets/{asset_id}/"
        f"?fields[asset]=media_license_token"
    )
    try:
        resp = udemy_session._get(url)
        if resp.ok:
            token = resp.json().get("media_license_token")
            if token:
                logger.info(f"> Refreshed license token for asset {asset_id}")
                return token
        logger.warning(f"> Failed to refresh license token: {resp.status_code}")
    except Exception as e:
        logger.debug(f"> Exception refreshing license token: {e}")
    return None


def fetch_widevine_key(mpd_url, content_id, license_token=None, asset_id=None):
    """Fetch a Widevine content key automatically using pywidevine and a WVD device file.

    Falls back to constructing the Udemy license URL from license_token when
    the MPD does not embed a LaURL (which is the standard Udemy behaviour).
    Keys are cached in keyfile.json and shared across threads via _keys_lock.
    """
    try:
        import xml.etree.ElementTree as ET
        from pywidevine.cdm import Cdm
        from pywidevine.device import Device
        from pywidevine.pssh import PSSH
    except ImportError:
        logger.error("> pywidevine not installed. Run: pip install pywidevine")
        return None

    wvd_path = _get_wvd_path()
    if not wvd_path:
        logger.error("> No WVD files available. Add .wvd files to the wvkeys/ folder.")
        return None
    logger.info(f"> Using WVD: {os.path.basename(wvd_path)}")

    try:
        import requests as _requests
        # Udemy pre-signed MPD URLs embed auth in the URL itself, so a plain
        # GET works even when udemy_session is not available.
        if udemy_session is not None:
            mpd_resp = udemy_session._get(mpd_url)
        else:
            logger.warning("> udemy_session is not initialized, falling back to plain requests for MPD fetch")
            mpd_resp = _requests.get(mpd_url, timeout=120)
        if not mpd_resp.ok:
            logger.error(f"> Failed to fetch MPD: {mpd_resp.status_code}")
            return None

        root = ET.fromstring(mpd_resp.text)
        WV_SYSTEM_ID = "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"

        pssh_b64 = None
        license_url = None

        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "ContentProtection":
                if elem.get("schemeIdUri", "").lower() == WV_SYSTEM_ID.lower():
                    for child in elem:
                        child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if child_tag.lower() in ("laurl", "licenseurl"):
                            license_url = child.text
                        elif child_tag.lower() == "pssh":
                            pssh_b64 = child.text

        if not pssh_b64:
            logger.error("> No Widevine PSSH found in MPD")
            return None
        if not license_url:
            if license_token:
                license_url = (
                    f"https://{portal_name}.udemy.com/api-2.0/media-license-server/"
                    f"validate-auth-token/?drm_type=widevine&auth_token={license_token}"
                )
                logger.info("> No license URL in MPD, using token-based URL")
            else:
                logger.error("> No license URL found in MPD and no license token provided")
                return None

        logger.info(f"> Requesting Widevine license from: {license_url}")

        device = Device.load(wvd_path)
        cdm = Cdm.from_device(device)
        cdm_session = cdm.open()

        pssh = PSSH(pssh_b64)
        challenge = cdm.get_license_challenge(cdm_session, pssh)

        def _build_lic_headers(token):
            hdrs = {"Content-Type": "application/octet-stream"}
            if bearer_token:
                hdrs["Authorization"] = f"Bearer {bearer_token}"
            # Mirror the user_agent embedded in the JWT — the license server
            # validates it matches the request User-Agent header.
            if token:
                ua = _jwt_user_agent(token)
                if ua:
                    hdrs["User-Agent"] = ua
                    logger.debug(f"> Using JWT user_agent for license request: {ua}")
            return hdrs

        def _post_license(url, hdrs):
            if udemy_session is not None:
                return udemy_session._session.post(url, data=challenge, headers=hdrs)
            return _requests.post(url, data=challenge, headers=hdrs, timeout=120)

        lic_resp = _post_license(license_url, _build_lic_headers(license_token))

        if not lic_resp.ok:
            body = lic_resp.text[:300]
            logger.error(f"> License request failed: {lic_resp.status_code} — {body}")

            # If the token expired mid-run, fetch a fresh one and retry once.
            if lic_resp.status_code == 401 and "expired" in body.lower() and asset_id:
                logger.info("> Token expired — fetching fresh token and retrying...")
                fresh_token = _refresh_license_token(asset_id)
                if fresh_token:
                    license_url = (
                        f"https://{portal_name}.udemy.com/api-2.0/media-license-server/"
                        f"validate-auth-token/?drm_type=widevine&auth_token={fresh_token}"
                    )
                    lic_resp = _post_license(license_url, _build_lic_headers(fresh_token))
                    if not lic_resp.ok:
                        logger.error(f"> Retry failed: {lic_resp.status_code} — {lic_resp.text[:300]}")

            if not lic_resp.ok:
                cdm.close(cdm_session)
                return None

        cdm.parse_license(cdm_session, lic_resp.content)

        content_key = None
        for key in cdm.get_keys(cdm_session):
            if key.type == "CONTENT":
                content_key = key.key.hex()
                break

        cdm.close(cdm_session)

        if not content_key:
            logger.error("> No CONTENT key returned from license server")
            return None

        logger.info(f"> Successfully fetched key for {content_id}")
        with _keys_lock:
            keys[content_id] = content_key
            with open(KEY_FILE_PATH, "w") as f:
                json.dump(keys, f, indent=4)

        return content_key

    except Exception as e:
        logger.error(f"> Error fetching Widevine key: {e}")
        return None


def handle_segments(url, format_id, lecture_id, video_title, output_path, chapter_dir, license_token=None, asset_id=None):
    import glob as _glob

    video_filepath_enc = os.path.join(chapter_dir, lecture_id + ".encrypted.mp4")
    audio_filepath_enc = os.path.join(chapter_dir, lecture_id + ".encrypted.m4a")
    temp_output_path = os.path.join(chapter_dir, lecture_id + ".mp4")

    def _cleanup():
        # Remove all encrypted/partial files produced for this lecture,
        # including aria2c fragment files (.part, .part-FragN).
        for f in _glob.glob(os.path.join(chapter_dir, f"{lecture_id}.encrypted.*")):
            try:
                os.remove(f)
            except OSError:
                pass
        # If mux never completed the rename, remove the incomplete temp file.
        if os.path.exists(temp_output_path):
            try:
                os.remove(temp_output_path)
            except OSError:
                pass
        if url.startswith("file://"):
            try:
                os.unlink(url[7:])
            except OSError:
                pass

    logger.info("> Downloading Lecture Tracks...")
    args = [
        "yt-dlp",
        "--force-generic-extractor",
        "--allow-unplayable-formats",
        "--concurrent-fragments",
        f"{concurrent_downloads}",
        "--downloader",
        "aria2c",
        "--downloader-args",
        'aria2c:"--disable-ipv6"',
        "--fixup",
        "never",
        "-k",
        "-o",
        os.path.join(chapter_dir, f"{lecture_id}.encrypted.%(ext)s"),
        "-f",
        format_id,
        f"{url}",
    ]
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Read both pipes in threads to avoid deadlock when buffers fill
    import threading
    t_out = threading.Thread(target=log_subprocess_output, args=("YTDLP-STDOUT", process.stdout), daemon=True)
    t_err = threading.Thread(target=log_subprocess_output, args=("YTDLP-STDERR", process.stderr), daemon=True)
    t_out.start()
    t_err.start()
    ret_code = process.wait()
    t_out.join()
    t_err.join()
    logger.info("> Lecture Tracks Downloaded")

    if ret_code != 0:
        logger.warning("Return code from the downloader was non-0 (error), skipping!")
        _cleanup()
        return

    try:
        video_kid = extract_kid(video_filepath_enc)
        logger.info("KID for video file is: " + video_kid)
        audio_kid = extract_kid(audio_filepath_enc)
        logger.info("KID for audio file is: " + audio_kid)

        audio_key = None
        video_key = None

        if audio_kid is not None:
            if audio_kid not in keys:
                logger.info(f"> Key not in keyfile for {audio_kid}, attempting auto-fetch via Widevine...")
                fetched = fetch_widevine_key(url, audio_kid, license_token=license_token, asset_id=asset_id)
                if fetched is None:
                    logger.error(f"Audio key not found for {audio_kid} and auto-fetch failed.")
                    return
            audio_key = keys.get(audio_kid)

        if video_kid is not None:
            if video_kid not in keys:
                logger.info(f"> Key not in keyfile for {video_kid}, attempting auto-fetch via Widevine...")
                fetched = fetch_widevine_key(url, video_kid, license_token=license_token, asset_id=asset_id)
                if fetched is None:
                    logger.error(f"Video key not found for {video_kid} and auto-fetch failed.")
                    return
            video_key = keys.get(video_kid)

        logger.info("> Merging video and audio, this might take a minute...")
        mux_process(
            video_filepath_enc,
            audio_filepath_enc,
            video_title,
            temp_output_path,
            audio_key,
            video_key,
        )
        logger.info("> Merging complete, renaming final file...")
        os.rename(temp_output_path, output_path)

    except Exception as e:
        logger.exception(f"Error processing lecture segments: {e}")
    finally:
        _cleanup()


def check_for_aria():
    try:
        subprocess.Popen(
            ["aria2c", "-v"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).wait()
        return True
    except FileNotFoundError:
        return False
    except Exception:
        logger.exception(
            "> Unexpected exception while checking for Aria2c, please tell the program author about this! "
        )
        return True


def check_for_ffmpeg():
    try:
        subprocess.Popen(
            ["ffmpeg"], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL
        ).wait()
        return True
    except FileNotFoundError:
        return False
    except Exception:
        logger.exception(
            "> Unexpected exception while checking for FFMPEG, please tell the program author about this! "
        )
        return True


def check_for_mkvmerge():
    try:
        subprocess.Popen(
            ["mkvmerge", "--version"],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        ).wait()
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return True


def download(url, path, filename):
    """
    @author Puyodead1
    """
    file_size = int(requests.head(url).headers["Content-Length"])
    if os.path.exists(path):
        first_byte = os.path.getsize(path)
    else:
        first_byte = 0
    if first_byte >= file_size:
        return file_size
    header = {"Range": "bytes=%s-%s" % (first_byte, file_size)}
    pbar = tqdm(
        total=file_size, initial=first_byte, unit="B", unit_scale=True, desc=filename
    )
    res = requests.get(url, headers=header, stream=True)
    res.raise_for_status()
    with open(path, encoding="utf8", mode="ab") as f:
        for chunk in res.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)
                pbar.update(1024)
    pbar.close()
    return file_size


def download_aria(url, file_dir, filename):
    """
    @author Puyodead1
    """
    args = [
        "aria2c",
        url,
        "-o",
        filename,
        "-d",
        file_dir,
        "-j16",
        "-s20",
        "-x16",
        "-c",
        "--auto-file-renaming=false",
        "--summary-interval=0",
        "--disable-ipv6",
        "--follow-torrent=false",
    ]
    process = subprocess.Popen(args)
    log_subprocess_output("ARIA2-STDOUT", process.stdout)
    log_subprocess_output("ARIA2-STDERR", process.stderr)
    ret_code = process.wait()
    if ret_code != 0:
        raise Exception("Return code from the downloader was non-0 (error)")
    return ret_code


def process_caption(caption, lecture_title, lecture_dir, tries=0):
    filename = f"%s_%s.%s" % (
        sanitize_filename(lecture_title),
        caption.get("language"),
        caption.get("extension"),
    )
    filename_no_ext = f"%s_%s" % (
        sanitize_filename(lecture_title),
        caption.get("language"),
    )
    filepath = os.path.join(lecture_dir, filename)

    if os.path.isfile(filepath):
        logger.info("    > Caption '%s' already downloaded." % filename)
    else:
        logger.info(f"    >  Downloading caption: '%s'" % filename)
        try:
            ret_code = download_aria(caption.get("download_url"), lecture_dir, filename)
            logger.debug(f"      > Download return code: {ret_code}")
        except Exception as e:
            if tries >= 3:
                logger.error(
                    f"    > Error downloading caption: {e}. Exceeded retries, skipping."
                )
                return None
            else:
                logger.error(
                    f"    > Error downloading caption: {e}. Will retry {3 - tries} more times."
                )
                return process_caption(caption, lecture_title, lecture_dir, tries + 1)

    if caption.get("extension") == "vtt":
        try:
            logger.info("    > Converting caption to SRT format...")
            convert(lecture_dir, filename_no_ext)
            logger.info("    > Caption conversion complete.")
            if not keep_vtt:
                os.remove(filepath)
        except Exception:
            logger.exception(f"    > Error converting caption")

    srt_path = os.path.join(lecture_dir, filename_no_ext + ".srt")
    return srt_path if os.path.isfile(srt_path) else None


def process_lecture(lecture, lecture_path, chapter_dir):
    lecture_id = lecture.get("id")
    lecture_title = lecture.get("lecture_title")
    is_encrypted = lecture.get("is_encrypted")
    lecture_sources = lecture.get("video_sources")
    media_license_token = lecture.get("media_license_token")
    asset_id = lecture.get("asset_id")

    if is_encrypted:
        if len(lecture_sources) > 0:
            source = lecture_sources[-1]  # last index is the best quality
            if isinstance(quality, int):
                source = min(
                    lecture_sources, key=lambda x: abs(int(x.get("height")) - quality)
                )
            logger.info(
                f"      > Lecture '{lecture_title}' has DRM, attempting to download. Selected quality: {source.get('height')}"
            )
            handle_segments(
                source.get("download_url"),
                source.get("format_id"),
                str(lecture_id),
                lecture_title,
                lecture_path,
                chapter_dir,
                license_token=media_license_token,
                asset_id=asset_id,
            )
        else:
            logger.info(f"      > Lecture '{lecture_title}' is missing media links")
            logger.debug(f"Lecture source count: {len(lecture_sources)}")
    else:
        sources = lecture.get("sources")
        sources = sorted(sources, key=lambda x: int(x.get("height")), reverse=True)
        if sources:
            if not os.path.isfile(lecture_path):
                logger.info(
                    "      > Lecture doesn't have DRM, attempting to download..."
                )
                source = sources[0]  # first index is the best quality
                if isinstance(quality, int):
                    source = min(
                        sources, key=lambda x: abs(int(x.get("height")) - quality)
                    )
                try:
                    logger.info(
                        "      ====== Selected quality: %s %s",
                        source.get("type"),
                        source.get("height"),
                    )
                    url = source.get("download_url")
                    source_type = source.get("type")
                    if source_type == "hls":
                        temp_filepath = lecture_path.replace(".mp4", ".%(ext)s")
                        cmd = [
                            "yt-dlp",
                            "--enable-file-urls",
                            "--force-generic-extractor",
                            "--concurrent-fragments",
                            f"{concurrent_downloads}",
                            "--downloader",
                            "aria2c",
                            "--downloader-args",
                            'aria2c:"--disable-ipv6"',
                            "-o",
                            f"{temp_filepath}",
                            f"{url}",
                        ]
                        process = subprocess.Popen(cmd)
                        log_subprocess_output("YTDLP-STDOUT", process.stdout)
                        log_subprocess_output("YTDLP-STDERR", process.stderr)
                        ret_code = process.wait()
                        if ret_code == 0:
                            tmp_file_path = lecture_path + ".tmp"
                            logger.info("      > HLS Download success")
                            if use_h265:
                                codec = "hevc_nvenc" if use_nvenc else "libx265"
                                transcode = (
                                    "-hwaccel cuda -hwaccel_output_format cuda".split(
                                        " "
                                    )
                                    if use_nvenc
                                    else []
                                )
                                cmd = [
                                    "ffmpeg",
                                    *transcode,
                                    "-y",
                                    "-i",
                                    lecture_path,
                                    "-c:v",
                                    codec,
                                    "-c:a",
                                    "copy",
                                    "-f",
                                    "mp4",
                                    "-metadata",
                                    'comment="Downloaded with Udemy-Downloader by Puyodead1 (https://github.com/Puyodead1/udemy-downloader)"',
                                    tmp_file_path,
                                ]
                                process = subprocess.Popen(cmd)
                                log_subprocess_output("FFMPEG-STDOUT", process.stdout)
                                log_subprocess_output("FFMPEG-STDERR", process.stderr)
                                ret_code = process.wait()
                                if ret_code == 0:
                                    os.unlink(lecture_path)
                                    os.rename(tmp_file_path, lecture_path)
                                    logger.info("      > Encoding complete")
                                else:
                                    logger.error(
                                        "      > Encoding returned non-zero return code"
                                    )
                    else:
                        ret_code = download_aria(
                            url, chapter_dir, lecture_title + ".mp4"
                        )
                        logger.debug(f"      > Download return code: {ret_code}")
                except Exception:
                    logger.exception(f">        Error downloading lecture")
            else:
                logger.info(
                    f"      > Lecture '{lecture_title}' is already downloaded, skipping..."
                )
        else:
            logger.error("      > Missing sources for lecture", lecture)


def process_quiz(udemy: Udemy, lecture, chapter_dir):
    quiz = udemy._get_quiz_with_info(lecture.get("id"))
    if quiz["_type"] == "coding-problem":
        process_coding_assignment(quiz, lecture, chapter_dir)
    else:  # Normal quiz
        process_normal_quiz(quiz, lecture, chapter_dir)


def process_normal_quiz(quiz, lecture, chapter_dir):
    lecture_title = lecture.get("lecture_title")
    lecture_index = lecture.get("lecture_index")
    lecture_file_name = sanitize_filename(lecture_title + ".html")
    lecture_path = os.path.join(chapter_dir, lecture_file_name)

    logger.info(f"  > Processing quiz {lecture_index}")
    template_path = os.path.join(MAIN_SCRIPT_PATH, "templates", "quiz_template.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()
        quiz_data = {
            "id": lecture["data"].get("id"),
            "title": lecture["data"].get("title"),
            "description": lecture["data"].get("description"),
            "pass_score": lecture.get("data").get("pass_percent"),
            "assessments": quiz["contents"],
        }
        html = html.replace("%%TITLE%%", lecture["data"].get("title"))
        html = html.replace("%%QUIZ_JSON%%", json.dumps(quiz_data))
        with open(lecture_path, "w", encoding="utf-8") as f:
            f.write(html)


def process_coding_assignment(quiz, lecture, chapter_dir):
    lecture_title = lecture.get("lecture_title")
    lecture_index = lecture.get("lecture_index")
    lecture_file_name = sanitize_filename(lecture_title + ".html")
    lecture_path = os.path.join(chapter_dir, lecture_file_name)

    logger.info(f"  > Processing quiz {lecture_index} (coding assignment)")

    template_path = os.path.join(
        MAIN_SCRIPT_PATH, "templates", "coding_assignment_template.html"
    )
    with open(template_path, "r") as f:
        html = f.read()
        quiz_data = {
            "title": lecture_title,
            "hasInstructions": quiz["hasInstructions"],
            "hasTests": quiz["hasTests"],
            "hasSolutions": quiz["hasSolutions"],
            "instructions": quiz["contents"]["instructions"],
            "tests": quiz["contents"]["tests"],
            "solutions": quiz["contents"]["solutions"],
        }
        html = html.replace("__data_placeholder__", json.dumps(quiz_data))
        with open(lecture_path, "w") as f:
            f.write(html)


def _process_one_lecture(lecture, chapter_dir, total_lectures):
    clazz = lecture.get("_class")
    if clazz == "quiz":
        return

    index = lecture.get("index")
    lecture_title = lecture.get("lecture_title")
    parsed_lecture = _udemy_instance._parse_lecture(lecture)

    lecture_extension = parsed_lecture.get("extension")
    extension = "mp4"
    if lecture_extension is not None:
        extension = lecture_extension
    lecture_file_name = sanitize_filename(lecture_title + "." + extension)
    lecture_file_name = deEmojify(lecture_file_name)
    lecture_path = os.path.join(chapter_dir, lecture_file_name)

    if not skip_lectures:
        logger.info(f"  > Processing lecture {index} of {total_lectures}")

        mkv_path = lecture_path.replace(".mp4", ".mkv")
        if os.path.isfile(lecture_path) or (use_mkv and os.path.isfile(mkv_path)):
            logger.info("      > Lecture '%s' is already downloaded, skipping..." % lecture_title)
        else:
            if extension == "html":
                if (parsed_lecture.get("html_content") is not None
                        and parsed_lecture.get("html_content") != ""):
                    html_content = (parsed_lecture.get("html_content")
                                    .encode("utf8", "ignore").decode("utf8"))
                    lecture_path = os.path.join(chapter_dir,
                                                "{}.html".format(sanitize_filename(lecture_title)))
                    try:
                        with open(lecture_path, encoding="utf8", mode="w") as f:
                            f.write(html_content)
                    except Exception:
                        logger.exception("    > Failed to write html file")
            else:
                process_lecture(parsed_lecture, lecture_path, chapter_dir)

    # download subtitles for this lecture
    is_video = extension == "mp4"
    subtitles = parsed_lecture.get("subtitles")
    downloaded_srt_paths = []  # [(lang, path)] collected for MKV embedding
    _seen_caption_langs = set()
    if dl_captions and subtitles is not None and lecture_extension is None:
        logger.info("Processing {} caption(s)...".format(len(subtitles)))
        for subtitle in subtitles:
            lang = subtitle.get("language")
            if lang == caption_locale or caption_locale == "all":
                if lang in _seen_caption_langs:
                    logger.debug(f"    > Skipping duplicate caption language '{lang}'")
                    continue
                _seen_caption_langs.add(lang)
                srt_path = process_caption(subtitle, lecture_title, chapter_dir)
                if srt_path and os.path.isfile(srt_path):
                    downloaded_srt_paths.append((lang, srt_path))
                    if keep_subtitles:
                        import shutil
                        chapter_name = os.path.basename(chapter_dir)
                        course_dir_name = os.path.basename(os.path.dirname(chapter_dir))
                        sub_dir = os.path.join(DOWNLOAD_DIR, "subtitles", course_dir_name, lang, chapter_name)
                        os.makedirs(sub_dir, exist_ok=True)
                        dest = os.path.join(sub_dir, os.path.basename(srt_path))
                        if not os.path.isfile(dest):
                            shutil.copy2(srt_path, dest)

    # mux into MKV with embedded subtitle tracks if requested
    if use_mkv and is_video:
        final_path = lecture_path.replace(".mp4", ".mkv")

        # Run mkvmerge only when the source .mp4 exists and the .mkv does not
        # yet (avoids re-muxing on re-runs).
        if os.path.isfile(lecture_path) and not os.path.isfile(final_path):
            mkv_args = ["mkvmerge", "-q", "-o", final_path, "--title", lecture_title, lecture_path]
            for lang, srt_path in downloaded_srt_paths:
                mkv_args += ["--language", f"0:{lang}", "--track-name", f"0:{lang}", srt_path]
            ret = subprocess.Popen(mkv_args).wait()
            if ret != 0:
                logger.warning("> mkvmerge returned non-zero, keeping source files")

        # Whenever the .mkv exists — whether just created or from a prior run —
        # clean up the source .mp4 and loose .srt files.  This covers:
        #   • normal completion (mkvmerge just ran successfully)
        #   • re-runs where .mkv already existed but .mp4 was re-downloaded
        #   • re-runs where captions were re-downloaded alongside an existing .mkv
        if os.path.isfile(final_path):
            for path in [lecture_path] + [p for _, p in downloaded_srt_paths]:
                if os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    if dl_assets:
        assets = parsed_lecture.get("assets")
        logger.info("    > Processing {} asset(s) for lecture...".format(len(assets)))
        for asset in assets:
            asset_type = asset.get("type")
            filename = asset.get("filename")
            download_url = asset.get("download_url")

            if asset_type == "article":
                body = asset.get("body")
                lecture_path = os.path.join(chapter_dir,
                                            "{}.html".format(sanitize_filename(lecture_title)))
                try:
                    template_path = os.path.join(MAIN_SCRIPT_PATH, "templates", "article_template.html")
                    with open(template_path, "r") as f:
                        content = f.read()
                        content = content.replace("__title_placeholder__", lecture_title[4:])
                        content = content.replace("__data_placeholder__", body)
                        with open(lecture_path, encoding="utf8", mode="w") as f:
                            f.write(content)
                except Exception as e:
                    print("Failed to write html file: ", e)
            elif asset_type == "video":
                logger.warning("Unhandled asset type 'video' — please report at https://github.com/Puyodead1/udemy-downloader/issues")
            elif asset_type in ("audio", "e-book", "file", "presentation", "ebook", "source_code"):
                try:
                    ret_code = download_aria(download_url, chapter_dir, filename)
                    logger.debug(f"      > Download return code: {ret_code}")
                except Exception:
                    logger.exception("> Error downloading asset")
            elif asset_type == "external_link":
                file_path = os.path.join(chapter_dir, f"{filename}.url")
                with open(file_path, "w") as f:
                    f.write("[InternetShortcut]\n")
                    f.write(f"URL={download_url}")
                savedirs, name = os.path.split(os.path.join(chapter_dir, filename))
                ext_links_file = os.path.join(savedirs, "external-links.txt")
                file_data = []
                if os.path.isfile(ext_links_file):
                    file_data = [i.strip().lower() for i in open(ext_links_file, encoding="utf-8", errors="ignore") if i]
                if name.lower() not in file_data:
                    with open(ext_links_file, "a", encoding="utf-8", errors="ignore") as f:
                        f.write("\n{}\n{}\n".format(name, download_url))


def parse_new(udemy: Udemy, udemy_object: dict):
    global _udemy_instance
    _udemy_instance = udemy

    total_chapters = udemy_object.get("total_chapters")
    total_lectures = udemy_object.get("total_lectures")
    logger.info(f"Chapter(s) ({total_chapters})")
    logger.info(f"Lecture(s) ({total_lectures})")

    course_name = (
        str(udemy_object.get("course_id"))
        if id_as_course_name
        else udemy_object.get("course_title")
    )
    course_dir = os.path.join(DOWNLOAD_DIR, course_name)
    if not os.path.exists(course_dir):
        os.mkdir(course_dir)

    for chapter in udemy_object.get("chapters"):
        current_chapter_index = int(chapter.get("chapter_index"))
        # Skip chapters not in the filter if a filter is provided
        if chapter_filter is not None and current_chapter_index not in chapter_filter:
            logger.info(
                "Skipping chapter %s as it is not in the specified filter",
                current_chapter_index,
            )
            continue

        chapter_title = chapter.get("chapter_title")
        chapter_index = chapter.get("chapter_index")
        chapter_dir = os.path.join(course_dir, chapter_title)
        if not os.path.exists(chapter_dir):
            os.mkdir(chapter_dir)
        logger.info(
            f"======= Processing chapter {chapter_index} of {total_chapters} ======="
        )

        lectures = [l for l in chapter.get("lectures") if l.get("_class") != "quiz"]
        quizzes = [l for l in chapter.get("lectures") if l.get("_class") == "quiz"]

        for quiz in quizzes:
            if dl_quizzes:
                process_quiz(udemy, quiz, chapter_dir)

        if parallel_lectures <= 1:
            for lecture in lectures:
                _process_one_lecture(lecture, chapter_dir, total_lectures)
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            logger.info(f"> Downloading chapter with {parallel_lectures} parallel lectures")

            def _run_named(lecture, chapter_dir, total_lectures):
                short = lecture.get("lecture_title", "?")[:40]
                threading.current_thread().name = f"Lec[{short}]"
                _process_one_lecture(lecture, chapter_dir, total_lectures)

            with ThreadPoolExecutor(max_workers=parallel_lectures) as executor:
                futures = {
                    executor.submit(_run_named, lecture, chapter_dir, total_lectures): lecture
                    for lecture in lectures
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        lec = futures[future]
                        logger.exception(f"> Error processing lecture: {lec.get('lecture_title', '?')}")


def _print_course_info(udemy: Udemy, udemy_object: dict):
    course_title = udemy_object.get("title")
    chapter_count = udemy_object.get("total_chapters")
    lecture_count = udemy_object.get("total_lectures")

    if lecture_count > 100:
        logger.warning(
            "This course has a lot of lectures! Fetching all the information can take a long time as well as spams Udemy's servers. It is NOT recommended to continue! Are you sure you want to do this?"
        )
        yn = input("(y/n): ")
        if yn.lower() != "y":
            logger.info(
                "Probably wise. Please remove the --info argument and try again."
            )
            sys.exit(0)

    logger.info("> Course: {}".format(course_title))
    logger.info("> Total Chapters: {}".format(chapter_count))
    logger.info("> Total Lectures: {}".format(lecture_count))
    logger.info("\n")

    chapters = udemy_object.get("chapters")
    for chapter in chapters:
        current_chapter_index = int(chapter.get("chapter_index"))
        # Skip chapters not in the filter if a filter is provided
        if chapter_filter is not None and current_chapter_index not in chapter_filter:
            continue

        chapter_title = chapter.get("chapter_title")
        chapter_index = chapter.get("chapter_index")
        chapter_lecture_count = chapter.get("lecture_count")
        chapter_lectures = chapter.get("lectures")

        logger.info(
            "> Chapter: {} ({} of {})".format(
                chapter_title, chapter_index, chapter_count
            )
        )

        for lecture in chapter_lectures:
            lecture_index = lecture.get(
                "lecture_index"
            )  # this is the raw object index from udemy
            lecture_title = lecture.get("lecture_title")
            parsed_lecture = udemy._parse_lecture(lecture)

            lecture_sources = parsed_lecture.get("sources")
            lecture_is_encrypted = parsed_lecture.get("is_encrypted", None)
            lecture_extension = parsed_lecture.get("extension")
            lecture_asset_count = parsed_lecture.get("assets_count")
            lecture_subtitles = parsed_lecture.get("subtitles")
            lecture_video_sources = parsed_lecture.get("video_sources")
            lecture_type = parsed_lecture.get("type")

            lecture_qualities = []

            if lecture_sources:
                lecture_sources = sorted(
                    lecture_sources, key=lambda x: int(x.get("height")), reverse=True
                )
            if lecture_video_sources:
                lecture_video_sources = sorted(
                    lecture_video_sources,
                    key=lambda x: int(x.get("height")),
                    reverse=True,
                )

            if lecture_is_encrypted and lecture_video_sources != None:
                lecture_qualities = [
                    "{}@{}x{}".format(x.get("type"), x.get("width"), x.get("height"))
                    for x in lecture_video_sources
                ]
            elif lecture_is_encrypted == False and lecture_sources != None:
                lecture_qualities = [
                    "{}@{}x{}".format(x.get("type"), x.get("height"), x.get("width"))
                    for x in lecture_sources
                ]

            if lecture_extension:
                continue

            logger.info(
                "  > Lecture: {} ({} of {})".format(
                    lecture_title, lecture_index, chapter_lecture_count
                )
            )
            logger.info("    > Type: {}".format(lecture_type))
            if lecture_is_encrypted != None:
                logger.info("    > DRM: {}".format(lecture_is_encrypted))
            if lecture_asset_count:
                logger.info("    > Asset Count: {}".format(lecture_asset_count))
            if lecture_subtitles:
                logger.info(
                    "    > Captions: {}".format(
                        ", ".join([x.get("language") for x in lecture_subtitles])
                    )
                )
            if lecture_qualities:
                logger.info("    > Qualities: {}".format(lecture_qualities))

        if chapter_index != chapter_count:
            logger.info("==========================================")


def main():
    global bearer_token, portal_name
    aria_ret_val = check_for_aria()
    if not aria_ret_val:
        logger.fatal("> Aria2c is missing from your system or path!")
        sys.exit(1)

    ffmpeg_ret_val = check_for_ffmpeg()
    if not ffmpeg_ret_val and not skip_lectures:
        logger.fatal("> FFMPEG is missing from your system or path!")
        sys.exit(1)

    if use_mkv and not check_for_mkvmerge():
        logger.fatal("> mkvmerge (MKVToolNix) is missing but --use-mkv was specified. Install MKVToolNix and ensure mkvmerge is in your PATH.")
        sys.exit(1)

    if load_from_file:
        logger.info(
            "> 'load_from_file' was specified, data will be loaded from json files instead of fetched"
        )
    if save_to_file:
        logger.info("> 'save_to_file' was specified, data will be saved to json files")

    load_dotenv()
    if bearer_token:
        bearer_token = bearer_token
    else:
        bearer_token = os.getenv("UDEMY_BEARER")

    global udemy_session
    udemy = Udemy(bearer_token)
    portal_name = udemy.extract_portal_name(course_url)
    visit_status = udemy.auth._session.visit(portal_name)
    if not visit_status:
        logger.fatal("> Visit request failed")
        sys.exit(1)

    udemy.authenticate(portal_name)
    udemy_session = udemy.session  # Session object; expose to fetch_widevine_key

    # if bearer_token:
    #     udemy.session._session.headers.update(
    #         {
    #             "x-udemyandroid-skip-local-cache": "true",
    #             "cache-control": "no-cache",
    #             "x-udemy-bearer-token": bearer_token,
    #             "authorization": f"Bearer {bearer_token}",
    #         }
    #     )
    # else:
    #     logger.fatal("> use a bearer token")
    #     sys.exit(1)

    logger.info("> Fetching course information, this may take a minute...")
    if not load_from_file:
        course_id, course_info = udemy._extract_course_info(course_url)
        logger.info("> Course information retrieved!")
        if course_info and isinstance(course_info, dict):
            title = sanitize_filename(course_info.get("title"))
            course_title = course_info.get("published_title")

    logger.info("> Fetching course curriculum, this may take a minute...")
    if load_from_file:
        course_json = json.loads(
            open(
                os.path.join(os.getcwd(), "saved", "course_content.json"),
                encoding="utf8",
                mode="r",
            ).read()
        )
        title = course_json.get("title")
        course_title = course_json.get("published_title")
        portal_name = course_json.get("portal_name")
    else:
        course_json = udemy._extract_course_curriculum(
            course_url, course_id, portal_name
        )
        course_json["portal_name"] = portal_name

    if save_to_file:
        with open(
            os.path.join(os.getcwd(), "saved", "course_content.json"),
            encoding="utf8",
            mode="w",
        ) as f:
            f.write(json.dumps(course_json))

    logger.info("> Course curriculum retrieved!")
    course = course_json.get("results")
    resource = course_json.get("detail")

    if load_from_file:
        udemy_object = json.loads(
            open(
                os.path.join(os.getcwd(), "saved", "_udemy.json"),
                encoding="utf8",
                mode="r",
            ).read()
        )
        if info:
            _print_course_info(udemy, udemy_object)
        else:
            parse_new(udemy, udemy_object)
    else:
        udemy_object = {}
        udemy_object["bearer_token"] = bearer_token
        udemy_object["course_id"] = course_id
        udemy_object["title"] = title
        udemy_object["course_title"] = course_title
        udemy_object["chapters"] = []
        chapter_index_counter = -1

        # if resource:
        #     logger.info("> Terminating Session...")
        #     udemy.session.terminate()
        #     logger.info("> Session Terminated.")

        if course:
            logger.info("> Processing course data, this may take a minute. ")
            lecture_counter = 0
            lectures = []

            for entry in course:
                clazz = entry.get("_class")

                if clazz == "chapter":
                    # reset lecture tracking
                    if not use_continuous_lecture_numbers:
                        lecture_counter = 0
                    lectures = []

                    chapter_index = entry.get("object_index")
                    chapter_title = "{0:02d} - ".format(
                        chapter_index
                    ) + sanitize_filename(entry.get("title"))

                    if chapter_title not in udemy_object["chapters"]:
                        udemy_object["chapters"].append(
                            {
                                "chapter_title": chapter_title,
                                "chapter_id": entry.get("id"),
                                "chapter_index": chapter_index,
                                "lectures": [],
                            }
                        )
                        chapter_index_counter += 1
                elif clazz == "lecture":
                    lecture_counter += 1
                    lecture_id = entry.get("id")
                    if len(udemy_object["chapters"]) == 0:
                        # dummy chapters to handle lectures without chapters
                        chapter_index = entry.get("object_index")
                        chapter_title = "{0:02d} - ".format(
                            chapter_index
                        ) + sanitize_filename(entry.get("title"))
                        if chapter_title not in udemy_object["chapters"]:
                            udemy_object["chapters"].append(
                                {
                                    "chapter_title": chapter_title,
                                    "chapter_id": lecture_id,
                                    "chapter_index": chapter_index,
                                    "lectures": [],
                                }
                            )
                            chapter_index_counter += 1
                    if lecture_id:
                        logger.info(
                            f"Processing {course.index(entry) + 1} of {len(course)}"
                        )

                        lecture_index = entry.get("object_index")
                        lecture_title = "{0:03d} ".format(
                            lecture_counter
                        ) + sanitize_filename(entry.get("title"))

                        lectures.append(
                            {
                                "index": lecture_counter,
                                "lecture_index": lecture_index,
                                "lecture_title": lecture_title,
                                "_class": entry.get("_class"),
                                "id": lecture_id,
                                "data": entry,
                            }
                        )
                    else:
                        logger.debug("Lecture: ID is None, skipping")
                elif clazz == "quiz":
                    lecture_counter += 1
                    lecture_id = entry.get("id")
                    if len(udemy_object["chapters"]) == 0:
                        # dummy chapters to handle lectures without chapters
                        chapter_index = entry.get("object_index")
                        chapter_title = "{0:02d} - ".format(
                            chapter_index
                        ) + sanitize_filename(entry.get("title"))
                        if chapter_title not in udemy_object["chapters"]:
                            udemy_object["chapters"].append(
                                {
                                    "chapter_title": chapter_title,
                                    "chapter_id": lecture_id,
                                    "chapter_index": chapter_index,
                                    "lectures": [],
                                }
                            )
                            chapter_index_counter += 1

                    if lecture_id:
                        logger.info(
                            f"Processing {course.index(entry) + 1} of {len(course)}"
                        )

                        lecture_index = entry.get("object_index")
                        lecture_title = "{0:03d} ".format(
                            lecture_counter
                        ) + sanitize_filename(entry.get("title"))

                        lectures.append(
                            {
                                "index": lecture_counter,
                                "lecture_index": lecture_index,
                                "lecture_title": lecture_title,
                                "_class": entry.get("_class"),
                                "id": lecture_id,
                                "data": entry,
                            }
                        )
                    else:
                        logger.debug("Quiz: ID is None, skipping")

                udemy_object["chapters"][chapter_index_counter]["lectures"] = lectures
                udemy_object["chapters"][chapter_index_counter]["lecture_count"] = len(
                    lectures
                )

            udemy_object["total_chapters"] = len(udemy_object["chapters"])
            udemy_object["total_lectures"] = sum(
                [
                    entry.get("lecture_count", 0)
                    for entry in udemy_object["chapters"]
                    if entry
                ]
            )

        if save_to_file:
            with open(
                os.path.join(os.getcwd(), "saved", "_udemy.json"),
                encoding="utf8",
                mode="w",
            ) as f:
                # remove "bearer_token" from the object before writing
                udemy_object.pop("bearer_token")
                udemy_object["portal_name"] = portal_name
                f.write(json.dumps(udemy_object))
            logger.info("> Saved parsed data to json")

        if info:
            _print_course_info(udemy, udemy_object)
        else:
            parse_new(udemy, udemy_object)


if __name__ == "__main__":
    # pre run parses arguments, sets up logging, and creates directories
    pre_run()
    # run main program
    main()
