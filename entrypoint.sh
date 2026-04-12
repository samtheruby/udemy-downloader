#!/bin/bash
set -e

if [ -z "$COURSE_URL" ]; then
    echo "ERROR: COURSE_URL is required"
    exit 1
fi

CMD="python main.py -c \"$COURSE_URL\""

[ -n "$UDEMY_BEARER" ]          && CMD="$CMD -b \"$UDEMY_BEARER\""
[ "$USE_COOKIES" = "true" ]     && CMD="$CMD --cookies"
[ -n "$QUALITY" ]               && CMD="$CMD -q $QUALITY"
[ -n "$LANG" ]                  && CMD="$CMD -l $LANG"
[ -n "$CONCURRENT_DOWNLOADS" ]  && CMD="$CMD -cd $CONCURRENT_DOWNLOADS"
[ -n "$PARALLEL_LECTURES" ]     && CMD="$CMD -pl $PARALLEL_LECTURES"
[ "$DOWNLOAD_CAPTIONS" = "true" ] && CMD="$CMD --download-captions"
[ "$DOWNLOAD_ASSETS" = "true" ] && CMD="$CMD --download-assets"
[ "$DOWNLOAD_QUIZZES" = "true" ] && CMD="$CMD --download-quizzes"
[ "$USE_MKV" = "true" ]         && CMD="$CMD --use-mkv"
[ "$KEEP_SUBTITLES" = "true" ]  && CMD="$CMD --keep-subtitles"
[ "$KEEP_VTT" = "true" ]        && CMD="$CMD --keep-vtt"
[ "$SKIP_LECTURES" = "true" ]   && CMD="$CMD --skip-lectures"
[ "$SKIP_HLS" = "true" ]        && CMD="$CMD --skip-hls"
[ -n "$CHAPTER" ]               && CMD="$CMD --chapter \"$CHAPTER\""
[ "$LOAD_FROM_FILE" = "true" ]  && CMD="$CMD --load-from-file"
[ "$SAVE_TO_FILE" = "true" ]    && CMD="$CMD --save-to-file"
[ -n "$LOG_LEVEL" ]             && CMD="$CMD --log-level $LOG_LEVEL"

echo "Running: $CMD"
eval $CMD
