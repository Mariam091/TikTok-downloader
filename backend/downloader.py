import re
import asyncio
import time
import json
import random
from pathlib import Path
from typing import Optional
import yt_dlp
import httpx

DOWNLOAD_DIR = Path(__file__).resolve().parent.parent / "downloads"
COOKIES_FILE = DOWNLOAD_DIR.parent / "cookies.txt"
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

class DownloadProgress:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.percent = 0
        self.status = "pending"
        self.filename = ""
        self.total = 0
        self.completed = 0
        self.error = None

_progress_tracker: dict[str, DownloadProgress] = {}

def get_progress(task_id: str) -> Optional[DownloadProgress]:
    return _progress_tracker.get(task_id)

def extract_media_id(url: str) -> Optional[str]:
    patterns = [r'/video/(\d+)', r'/photo/(\d+)', r'/t/(\w+)']
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None

def extract_username(url: str) -> Optional[str]:
    m = re.search(r'tiktok\.com/@([\w.]+)', url)
    return m.group(1) if m else None

def is_photo_url(url: str) -> bool:
    return '/photo/' in url

def get_user_agent() -> str:
    return random.choice(USER_AGENTS)

async def run_ydl_async(ydl_opts: dict, url: str, download: bool = False):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=download))

def _base_ydl_opts() -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'http_headers': {'User-Agent': get_user_agent(), 'Referer': 'https://www.tiktok.com/'},
    }
    if COOKIES_FILE.exists():
        opts['cookiefile'] = str(COOKIES_FILE)
    return opts

async def get_media_info(url: str) -> dict:
    try:
        info = await run_ydl_async(_base_ydl_opts(), url, download=False)
        result = {
            'id': info.get('id', ''),
            'title': info.get('title', ''),
            'uploader': info.get('uploader', ''),
            'uploader_id': info.get('uploader_id', ''),
            'duration': info.get('duration', 0),
            'thumbnail': info.get('thumbnail', ''),
            'webpage_url': info.get('webpage_url', url),
            'formats': [],
        }
        if info.get('entries'):
            entry = info['entries'][0]
            result.update({
                'id': entry.get('id', ''),
                'title': entry.get('title', ''),
                'uploader': entry.get('uploader', ''),
                'uploader_id': entry.get('uploader_id', ''),
                'duration': entry.get('duration', 0),
                'thumbnail': entry.get('thumbnail', ''),
            })
        return result
    except Exception as e:
        return {'error': str(e)}

async def resolve_short_url(url: str) -> str:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            r = await client.get(url, headers={'User-Agent': get_user_agent()})
            return str(r.url)
    except Exception:
        return url

async def get_tiktok_api_data(media_id: str, max_retries: int = 3) -> Optional[dict]:
    api_urls = [
        f"https://api22-normal-c-alisg.tiktokv.com/aweme/v1/feed/?aweme_id={media_id}&iid=7318518857994389254&device_id=7318517321748022790&channel=googleplay&app_name=musical_ly&version_code=350103&device_platform=android&device_type=Pixel+7&os_version=13",
        f"https://api16-normal-c-alisg.tiktokv.com/aweme/v1/feed/?aweme_id={media_id}",
    ]
    for attempt in range(max_retries):
        for api_url in api_urls:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.get(api_url, headers={
                        'User-Agent': get_user_agent(),
                        'Referer': 'https://www.tiktok.com/',
                    })
                    if r.status_code == 200:
                        data = r.json()
                        if data.get('aweme_list'):
                            return data
                    elif r.status_code == 429:
                        wait = 5 * (attempt + 1)
                        await asyncio.sleep(wait)
                    else:
                        continue
            except Exception:
                continue
    return None

async def download_single_media(url: str, task_id: str, watermark: bool = False) -> dict:
    progress = DownloadProgress(task_id)
    _progress_tracker[task_id] = progress
    progress.status = "downloading"

    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            if total:
                progress.total = total
                progress.completed = downloaded
                progress.percent = int(downloaded / total * 100)
                progress.filename = d.get('filename', '')
        elif d['status'] == 'finished':
            progress.percent = 100
            progress.status = "processing"

    if 'vm.tiktok.com' in url or 'vt.tiktok.com' in url:
        url = await resolve_short_url(url)

    media_id = extract_media_id(url) or task_id
    username = extract_username(url) or "unknown"
    user_dir = DOWNLOAD_DIR / username
    user_dir.mkdir(parents=True, exist_ok=True)

    outtmpl = str(user_dir / f'{media_id}_%(title).50s.%(ext)s')
    ydl_opts = {**_base_ydl_opts(),
        'outtmpl': outtmpl,
        'progress_hooks': [progress_hook],
        'windowsfilenames': True,
    }

    if is_photo_url(url):
        ydl_opts['format'] = 'best'
    elif not watermark:
        ydl_opts['format'] = 'bestvideo+bestaudio/best'

    try:
        info = await run_ydl_async(ydl_opts, url, download=True)
        filename = yt_dlp.YoutubeDL(ydl_opts).prepare_filename(info) if info else None
        if filename:
            p = Path(filename)
            if p.exists():
                progress.filename = p.name
        progress.status = "completed"
        return {'success': True, 'task_id': task_id, 'filename': progress.filename, 'username': username}
    except Exception as ydl_err:
        err_msg = str(ydl_err)
        if '503' in err_msg or '403' in err_msg:
            progress.status = "retrying"
            api_data = await get_tiktok_api_data(media_id)
            if api_data and api_data.get('aweme_list'):
                aweme = api_data['aweme_list'][0]
                video = aweme.get('video', {})
                if not watermark:
                    download_url = (video.get('download_addr', {}) or video.get('play_addr', {})).get('url_list', [None])[0]
                else:
                    download_url = (video.get('play_addr', {}) or video.get('download_addr', {})).get('url_list', [None])[0]

                if download_url:
                    try:
                        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                            r = await client.get(download_url, headers={'User-Agent': get_user_agent()})
                            ext = 'mp4'
                            fname = f'{media_id}_{aweme.get("aweme_id", media_id)}.{ext}'
                            fpath = user_dir / fname
                            fpath.write_bytes(r.content)
                            progress.filename = fname
                            progress.status = "completed"
                            return {'success': True, 'task_id': task_id, 'filename': fname, 'username': username}
                    except Exception as dl_err:
                        progress.status = "error"
                        progress.error = str(dl_err)
                        return {'success': False, 'task_id': task_id, 'error': str(dl_err)}

        progress.status = "error"
        progress.error = err_msg
        return {'success': False, 'task_id': task_id, 'error': err_msg}

async def get_profile_media_list(username: str, task_id: str) -> list:
    progress = DownloadProgress(task_id)
    _progress_tracker[task_id] = progress
    progress.status = "scanning"
    url = f"https://www.tiktok.com/@{username}"

    ydl_opts = {**_base_ydl_opts(),
        'extract_flat': True,
        'playlistend': 100,
    }
    del ydl_opts['http_headers']

    try:
        info = await run_ydl_async(ydl_opts, url, download=False)
        entries = []
        if info.get('entries'):
            for e in info['entries']:
                if e:
                    entries.append({
                        'url': f"https://www.tiktok.com/@{username}/video/{e.get('id', '')}",
                        'id': e.get('id', ''),
                        'title': e.get('title', ''),
                    })
        progress.status = "completed"
        return entries
    except Exception as e:
        progress.status = "error"
        progress.error = str(e)
        return []

async def download_from_url_list(urls: list[str], task_id: str, watermark: bool = False) -> list:
    results = []
    for i, url in enumerate(urls):
        sub_task = f"{task_id}_{i}"
        result = await download_single_media(url, sub_task, watermark)
        results.append(result)
    return results
