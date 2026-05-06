# ✂️ Video Trimmer

A web app to download and trim video clips from YouTube and other supported sites, or trim local video files directly.

## Features

- Paste any video URL (YouTube, YouTube Shorts, Twitter, Vimeo, and more)
- Drag & drop or choose a local video file to trim without downloading anything
- Set start and end times to extract only the clip you need
- Interactive player with timeline, draggable markers, and clip preview loop
- Keyboard shortcuts: `S` to set start, `E` to set end, `←` / `→` to scrub (+ `Shift` for ±5s)
- Automatically detects available resolutions for YouTube (360p to 8K) with estimated file size
- Full video download toggle — skip trimming and download the entire video
- Queue system — add multiple clips from the same or different URLs and download all at once
- Custom filename for your clip
- Clean dark UI
- Format selector — choose MP4 (video) or MP3 (audio only) before downloading

## Requirements

- Python 3.11+
- ffmpeg installed and in PATH
- yt-dlp

## Installation

```
pip install flask yt-dlp
```

## Usage

Run locally with `run.bat` or:

```
py -3.11 -m flask run
```
