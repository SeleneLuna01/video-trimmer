from flask import Flask, request, render_template, send_file, jsonify, Response
import os
import subprocess
import json
import re
import time

app = Flask(__name__)
DOWNLOAD_FOLDER = 'downloads'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Format info cache: { url: { 'info': {...}, 'ts': timestamp } }
_formats_cache = {}
CACHE_TTL = 300  # 5 minutes

def time_to_seconds(t):
    parts = t.split(':')
    parts = [float(x) for x in parts]
    if len(parts) == 3:
        return parts[0]*3600 + parts[1]*60 + parts[2]
    elif len(parts) == 2:
        return parts[0]*60 + parts[1]
    return parts[0]

def format_size(bytes_val):
    if bytes_val < 1024*1024:
        return f"{bytes_val/1024:.0f} KB"
    return f"{bytes_val/(1024*1024):.0f} MB"

BITRATE_FALLBACK = {
    '4320': 50_000_000,
    '2160': 15_000_000,
    '1440': 8_000_000,
    '1080': 5_000_000,
    '720':  2_500_000,
    '480':  1_000_000,
    '360':    500_000,
}

def get_formats_info(url):
    """Fetch format info from yt-dlp, with 5-minute in-memory cache."""
    now = time.time()
    cached = _formats_cache.get(url)
    if cached and (now - cached['ts']) < CACHE_TTL:
        return cached['info']

    result = subprocess.run(
        ['yt-dlp', '--no-playlist', '-J', url],
        capture_output=True, text=True, timeout=20
    )
    try:
        info = json.loads(result.stdout)
        _formats_cache[url] = {'info': info, 'ts': now}
        return info
    except:
        return None


def pick_best_format(info, target_res=None):
    """
    Given fresh yt-dlp info, pick the best format string.
    Strategy:
      1. Prefer HLS combined (video+audio in one stream) — most reliable for --download-sections
      2. Fallback: HLS separate video + HLS audio
      3. Fallback: DASH video + DASH audio (fresh IDs)
      4. Last resort: 'bestvideo+bestaudio'
    Returns (format_string, type_string)
    """
    fmt_list = info.get('formats', [])

    # HLS formats that have both video and audio
    hls_combined = [
        f for f in fmt_list
        if f.get('protocol', '') == 'm3u8'
        and f.get('vcodec', 'none') != 'none'
        and f.get('acodec', 'none') != 'none'
        and f.get('height')
    ]

    # HLS video-only
    hls_video_only = [
        f for f in fmt_list
        if f.get('protocol', '') == 'm3u8'
        and f.get('vcodec', 'none') != 'none'
        and f.get('acodec', 'none') == 'none'
        and f.get('height')
    ]

    # HLS audio-only
    hls_audio_only = [
        f for f in fmt_list
        if f.get('protocol', '') == 'm3u8'
        and f.get('acodec', 'none') != 'none'
        and f.get('vcodec', 'none') == 'none'
    ]

    if hls_combined:
        candidates = [f for f in hls_combined if str(f.get('height', '')) == str(target_res)] if target_res else hls_combined
        if not candidates:
            candidates = hls_combined
        best = max(candidates, key=lambda f: f.get('tbr') or f.get('vbr') or 0)
        return best['format_id'], 'hls_combined'

    if hls_video_only and hls_audio_only:
        v_candidates = [f for f in hls_video_only if str(f.get('height', '')) == str(target_res)] if target_res else hls_video_only
        if not v_candidates:
            v_candidates = hls_video_only
        best_v = max(v_candidates, key=lambda f: f.get('tbr') or f.get('vbr') or 0)
        best_a = max(hls_audio_only, key=lambda f: f.get('tbr') or f.get('abr') or 0)
        return f"{best_v['format_id']}+{best_a['format_id']}", 'hls_separate'

    # DASH fallback with fresh IDs
    dash_video = [
        f for f in fmt_list
        if f.get('vcodec', 'none') != 'none'
        and f.get('acodec', 'none') == 'none'
        and f.get('height')
    ]
    dash_audio = [
        f for f in fmt_list
        if f.get('acodec', 'none') != 'none'
        and f.get('vcodec', 'none') == 'none'
    ]

    if dash_video and dash_audio:
        v_candidates = [f for f in dash_video if str(f.get('height', '')) == str(target_res)] if target_res else dash_video
        if not v_candidates:
            v_candidates = dash_video
        best_v = max(v_candidates, key=lambda f: f.get('tbr') or f.get('vbr') or 0)
        best_a = max(dash_audio, key=lambda f: f.get('tbr') or f.get('abr') or 0)
        return f"{best_v['format_id']}+{best_a['format_id']}", 'dash'

    return 'bestvideo+bestaudio', 'fallback'


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/formats', methods=['POST'])
def formats():
    url = request.json.get('url')
    start = request.json.get('start', '0:00')
    end = request.json.get('end', '0:00')

    try:
        duration = time_to_seconds(end) - time_to_seconds(start)
        if duration <= 0:
            duration = 1
    except:
        return jsonify({'error': 'Invalid time format'})

    info = get_formats_info(url)

    resolutions = ['4320', '2160', '1440', '1080', '720', '480', '360']
    sizes = {}
    available = []

    if info:
        fmt_list = info.get('formats', [])
        for res in resolutions:
            candidates = [
                f for f in fmt_list
                if f.get('height') and str(f['height']) == res
            ]
            if not candidates:
                continue
            available.append(res)
            best = max(candidates, key=lambda f: f.get('tbr') or 0)
            filesize = best.get('filesize') or best.get('filesize_approx')
            if filesize:
                estimated = int(filesize / info.get('duration', 1) * duration)
                sizes[res] = {'size': format_size(estimated), 'exact': True}
            else:
                estimated = int(BITRATE_FALLBACK.get(res, 1_000_000) * duration / 8)
                sizes[res] = {'size': format_size(estimated), 'exact': False}

    if not available:
        available = resolutions
        for res in resolutions:
            estimated = int(BITRATE_FALLBACK.get(res, 1_000_000) * duration / 8)
            sizes[res] = {'size': format_size(estimated), 'exact': False}

    # best_format is not stored — it is resolved fresh at download time
    return jsonify({'sizes': sizes, 'available': available, 'best_format': None})


def process_clip(url, start, end, quality, download_name, trimmed_path, use_sections=False):
    duration = time_to_seconds(end) - time_to_seconds(start)
    start_secs = time_to_seconds(start)
    end_secs = time_to_seconds(end)

    yield f"data: {json.dumps({'phase': 'downloading', 'progress': 0})}\n\n"

    if use_sections:
        # Resolve fresh format just before downloading
        yield f"data: {json.dumps({'phase': 'downloading', 'progress': 2})}\n\n"
        info = get_formats_info(url)
        if not info:
            yield f"data: {json.dumps({'error': 'Could not fetch video info.'})}\n\n"
            return

        target_res = quality if (quality and quality.isdigit()) else None
        fmt_str, fmt_type = pick_best_format(info, target_res)

        video_tmp = os.path.join(DOWNLOAD_FOLDER, '_tmp_video.mp4')
        audio_tmp = os.path.join(DOWNLOAD_FOLDER, '_tmp_audio.m4a')
        for f in [video_tmp, audio_tmp]:
            if os.path.exists(f):
                os.remove(f)

        if fmt_type == 'hls_combined':
            # Single stream with video+audio — one download
            cmd = [
                'yt-dlp', '-o', video_tmp, '--no-playlist',
                '-f', fmt_str,
                '--download-sections', f'*{start_secs}-{end_secs}',
                '--force-keyframes-at-cuts', '--newline', url
            ]
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace'
            )
            for line in process.stdout:
                match = re.search(r'\b(\d{1,3}\.?\d*)%', line)
                if match:
                    pct = float(match.group(1))
                    yield f"data: {json.dumps({'phase': 'downloading', 'progress': round(pct)})}\n\n"
            process.wait()

            if process.returncode != 0 or not os.path.exists(video_tmp):
                yield f"data: {json.dumps({'error': 'Download failed.'})}\n\n"
                return

            yield f"data: {json.dumps({'phase': 'trimming', 'progress': 0})}\n\n"
            if os.path.exists(trimmed_path):
                os.remove(trimmed_path)

            ffmpeg_process = subprocess.Popen(
                ['ffmpeg', '-i', video_tmp,
                 '-c:v', 'libx264', '-c:a', 'aac', '-preset', 'fast',
                 '-pix_fmt', 'yuv420p', '-progress', 'pipe:1',
                 trimmed_path, '-y'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1
            )

        else:
            # Separate streams: download video and audio independently
            video_fmt, audio_fmt = (fmt_str.split('+', 1) if '+' in fmt_str else (fmt_str, None))

            cmd_v = [
                'yt-dlp', '-o', video_tmp, '--no-playlist',
                '-f', video_fmt,
                '--download-sections', f'*{start_secs}-{end_secs}',
                '--force-keyframes-at-cuts', '--newline', url
            ]
            proc_v = subprocess.Popen(
                cmd_v, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace'
            )
            for line in proc_v.stdout:
                match = re.search(r'\b(\d{1,3}\.?\d*)%', line)
                if match:
                    pct = float(match.group(1)) * 0.5
                    yield f"data: {json.dumps({'phase': 'downloading', 'progress': round(pct)})}\n\n"
            proc_v.wait()

            if proc_v.returncode != 0 or not os.path.exists(video_tmp):
                yield f"data: {json.dumps({'error': 'Video stream download failed.'})}\n\n"
                return

            has_audio = False
            if audio_fmt:
                cmd_a = [
                    'yt-dlp', '-o', audio_tmp, '--no-playlist',
                    '-f', audio_fmt,
                    '--download-sections', f'*{start_secs}-{end_secs}',
                    '--force-keyframes-at-cuts', '--newline', url
                ]
                proc_a = subprocess.Popen(
                    cmd_a, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace'
                )
                for line in proc_a.stdout:
                    match = re.search(r'\b(\d{1,3}\.?\d*)%', line)
                    if match:
                        pct = 50 + float(match.group(1)) * 0.5
                        yield f"data: {json.dumps({'phase': 'downloading', 'progress': round(pct)})}\n\n"
                proc_a.wait()
                has_audio = proc_a.returncode == 0 and os.path.exists(audio_tmp)

            yield f"data: {json.dumps({'phase': 'trimming', 'progress': 0})}\n\n"
            if os.path.exists(trimmed_path):
                os.remove(trimmed_path)

            ffmpeg_inputs = ['-i', video_tmp]
            if has_audio:
                ffmpeg_inputs += ['-i', audio_tmp]

            ffmpeg_process = subprocess.Popen(
                ['ffmpeg'] + ffmpeg_inputs +
                ['-c:v', 'libx264', '-c:a', 'aac', '-preset', 'fast',
                 '-pix_fmt', 'yuv420p', '-progress', 'pipe:1',
                 trimmed_path, '-y'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1
            )

        for line in ffmpeg_process.stdout:
            if line.startswith('out_time_ms='):
                try:
                    ms = int(line.strip().split('=')[1])
                    secs = ms / 1_000_000
                    pct = min(100, round(secs / duration * 100))
                    yield f"data: {json.dumps({'phase': 'trimming', 'progress': pct})}\n\n"
                except:
                    pass
        ffmpeg_process.wait()

        for f in [video_tmp, audio_tmp]:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except:
                pass

        if ffmpeg_process.returncode != 0:
            yield f"data: {json.dumps({'error': 'Processing failed.'})}\n\n"
            return

        yield f"data: {json.dumps({'phase': 'done', 'progress': 100, 'filename': download_name})}\n\n"
        return

    # --- YouTube / no use_sections: full download + ffmpeg trim ---
    output_path = os.path.join(DOWNLOAD_FOLDER, '%(title)s.%(ext)s')

    cmd = ['yt-dlp', '-o', output_path, '--merge-output-format', 'mp4',
           '--no-playlist', '-f', quality, '--newline', url]

    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace'
    )
    for line in process.stdout:
        match = re.search(r'\b(\d{1,3}\.?\d*)%', line)
        if match:
            pct = float(match.group(1))
            yield f"data: {json.dumps({'phase': 'downloading', 'progress': round(pct)})}\n\n"
    process.wait()

    if process.returncode != 0:
        yield f"data: {json.dumps({'error': 'Could not download this URL.'})}\n\n"
        return

    files = [f for f in os.listdir(DOWNLOAD_FOLDER)
             if not f.startswith('trimmed') and not f.startswith('_tmp')]
    if not files:
        yield f"data: {json.dumps({'error': 'No file found after download'})}\n\n"
        return

    downloaded = os.path.join(DOWNLOAD_FOLDER, files[0])

    yield f"data: {json.dumps({'phase': 'trimming', 'progress': 0})}\n\n"

    ffmpeg_process = subprocess.Popen(
        ['ffmpeg', '-ss', start, '-i', downloaded,
         '-t', str(duration),
         '-c:v', 'libx264', '-c:a', 'aac', '-preset', 'fast',
         '-pix_fmt', 'yuv420p', '-progress', 'pipe:1',
         trimmed_path, '-y'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1
    )
    for line in ffmpeg_process.stdout:
        if line.startswith('out_time_ms='):
            try:
                ms = int(line.strip().split('=')[1])
                secs = ms / 1_000_000
                pct = min(100, round(secs / duration * 100))
                yield f"data: {json.dumps({'phase': 'trimming', 'progress': pct})}\n\n"
            except:
                pass
    ffmpeg_process.wait()

    try:
        os.remove(downloaded)
    except:
        pass

    if ffmpeg_process.returncode != 0:
        yield f"data: {json.dumps({'error': 'Trim failed'})}\n\n"
        return

    yield f"data: {json.dumps({'phase': 'done', 'progress': 100, 'filename': download_name})}\n\n"


@app.route('/download', methods=['POST'])
def download():
    url = request.form.get('url')
    start = request.form.get('start')
    end = request.form.get('end')
    quality = request.form.get('quality', 'bestvideo+bestaudio')

    download_name = request.form.get('filename', '').strip()
    if not download_name:
        download_name = 'clip.mp4'
    elif not download_name.endswith('.mp4'):
        download_name += '.mp4'

    use_sections = request.form.get('use_sections') == 'true'
    trimmed = os.path.join(DOWNLOAD_FOLDER, 'trimmed_output.mp4')

    return Response(
        process_clip(url, start, end, quality, download_name, trimmed, use_sections),
        mimetype='text/event-stream'
    )


@app.route('/get_trimmed')
def get_trimmed():
    name = request.args.get('name', 'clip.mp4')
    trimmed = os.path.join(DOWNLOAD_FOLDER, 'trimmed_output.mp4')
    return send_file(trimmed, as_attachment=True, download_name=name)


@app.route('/download_queue', methods=['POST'])
def download_queue():
    clips = request.json.get('clips', [])
    if not clips:
        return jsonify({'error': 'No clips in queue'}), 400

    trimmed_files = []

    for i, clip in enumerate(clips):
        url = clip.get('url')
        start = clip.get('start')
        end = clip.get('end')
        quality = clip.get('quality', 'bestvideo+bestaudio')
        filename = clip.get('filename', '').strip()
        if not filename:
            filename = f'clip_{i+1}.mp4'
        elif not filename.endswith('.mp4'):
            filename += '.mp4'

        duration = time_to_seconds(end) - time_to_seconds(start)
        output_path = os.path.join(DOWNLOAD_FOLDER, '%(title)s.%(ext)s')

        info = get_formats_info(url)
        if info:
            fmt_str, _ = pick_best_format(info)
            format_to_use = fmt_str
        else:
            format_to_use = quality

        result = subprocess.run(
            ['yt-dlp', '-o', output_path, '--merge-output-format', 'mp4',
             '--no-playlist', '-f', format_to_use, url],
            capture_output=True, text=True
        )

        if result.returncode != 0:
            continue

        files = [f for f in os.listdir(DOWNLOAD_FOLDER)
                 if not f.startswith('trimmed') and not f.startswith('_tmp')]
        if not files:
            continue

        downloaded = os.path.join(DOWNLOAD_FOLDER, files[0])
        trimmed = os.path.join(DOWNLOAD_FOLDER, f'trimmed_{i}.mp4')

        subprocess.run([
            'ffmpeg', '-ss', start, '-i', downloaded,
            '-t', str(duration),
            '-c:v', 'libx264', '-c:a', 'aac', '-preset', 'fast', trimmed, '-y'
        ], capture_output=True, text=True)

        try:
            os.remove(downloaded)
        except:
            pass

        if os.path.exists(trimmed):
            trimmed_files.append((trimmed, filename))

    if not trimmed_files:
        return jsonify({'error': 'All clips failed'}), 400

    if len(trimmed_files) == 1:
        path, name = trimmed_files[0]
        return send_file(path, as_attachment=True, download_name=name)

    return jsonify({'files': [{'path': path, 'name': name} for path, name in trimmed_files]})


@app.route('/get_file')
def get_file():
    path = request.args.get('path')
    name = os.path.basename(path)
    return send_file(path, as_attachment=True, download_name=name)


if __name__ == '__main__':
    app.run(debug=True)