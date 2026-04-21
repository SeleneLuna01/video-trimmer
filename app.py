from flask import Flask, request, render_template, send_file, jsonify
import os
import subprocess
import json
import shutil

app = Flask(__name__)
DOWNLOAD_FOLDER = 'downloads'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

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

    result = subprocess.run(
        ['yt-dlp', '--no-playlist', '-J', url],
        capture_output=True, text=True
    )

    resolutions = ['4320', '2160', '1440', '1080', '720', '480', '360']
    sizes = {}
    available = []

    try:
        info = json.loads(result.stdout)
        formats_list = info.get('formats', [])

        for res in resolutions:
            candidates = [
                f for f in formats_list
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
                estimated = int(BITRATE_FALLBACK[res] * duration / 8)
                sizes[res] = {'size': format_size(estimated), 'exact': False}

    except:
        available = resolutions
        for res in resolutions:
            estimated = int(BITRATE_FALLBACK[res] * duration / 8)
            sizes[res] = {'size': format_size(estimated), 'exact': False}

    return jsonify({'sizes': sizes, 'available': available})

@app.route('/download', methods=['POST'])
def download():
    url = request.form.get('url')
    start = request.form.get('start')
    end = request.form.get('end')
    quality = request.form.get('quality', 'bestvideo+bestaudio')

    # Nombre del archivo
    download_name = request.form.get('filename', '').strip()
    if not download_name:
        download_name = 'clip.mp4'
    elif not download_name.endswith('.mp4'):
        download_name += '.mp4'

    duration = time_to_seconds(end) - time_to_seconds(start)

    # Descargar video con yt-dlp
    output_path = os.path.join(DOWNLOAD_FOLDER, '%(title)s.%(ext)s')
    result = subprocess.run(
        ['yt-dlp', '-o', output_path, '--merge-output-format', 'mp4', '--no-playlist', '-f', quality, url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return jsonify({'error': 'Download failed: ' + result.stderr[-200:]}), 400

    # Encontrar el archivo descargado
    files = [f for f in os.listdir(DOWNLOAD_FOLDER) if not f.startswith('trimmed')]
    if not files:
        return jsonify({'error': 'Download failed: no file found'}), 400

    downloaded = os.path.join(DOWNLOAD_FOLDER, files[0])
    trimmed = os.path.join(DOWNLOAD_FOLDER, 'trimmed_output.mp4')

    # Recortar con ffmpeg
    result = subprocess.run([
        'ffmpeg',
        '-ss', start,
        '-i', downloaded,
        '-t', str(duration),
        '-c:v', 'libx264', '-c:a', 'aac', '-preset', 'fast', trimmed, '-y'
    ], capture_output=True, text=True)

    if result.returncode != 0:
        return jsonify({'error': 'Trim failed: ' + result.stderr[-200:]}), 400

    # Limpiar el video descargado original, dejar el trimmed
    try:
        os.remove(downloaded)
    except:
        pass

    return send_file(trimmed, as_attachment=True, download_name=download_name)

if __name__ == '__main__':
    app.run(debug=True)