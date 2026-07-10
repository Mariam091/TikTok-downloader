import uuid
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

from .downloader import (
    get_media_info,
    download_single_media,
    get_profile_media_list,
    download_from_url_list,
    get_progress,
    extract_username,
    DOWNLOAD_DIR,
)

app = FastAPI(title="TikTok Downloader Web")

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "static")), name="static")
app.mount("/downloads", StaticFiles(directory=str(DOWNLOAD_DIR)), name="downloads")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/info")
async def api_get_media_info(url: str = Form(...)):
    info = await get_media_info(url)
    if 'error' in info:
        raise HTTPException(status_code=400, detail=info['error'])
    return info


@app.post("/api/download/single")
async def api_download_single(
    url: str = Form(...),
    watermark: bool = Form(False),
):
    task_id = str(uuid.uuid4())[:8]
    result = await download_single_media(url, task_id, watermark)
    return result


@app.post("/api/download/mass-username")
async def api_download_mass_username(
    username: str = Form(...),
    watermark: bool = Form(False),
):
    task_id = str(uuid.uuid4())[:8]
    entries = await get_profile_media_list(username, task_id)

    if not entries:
        raise HTTPException(status_code=400, detail="No media found for this username")

    urls = [e['url'] for e in entries]
    results = await download_from_url_list(urls, task_id, watermark)

    return {
        'task_id': task_id,
        'username': username,
        'total_found': len(entries),
        'downloaded': sum(1 for r in results if r.get('success')),
        'failed': sum(1 for r in results if not r.get('success')),
        'results': results,
    }


@app.post("/api/download/textfile")
async def api_download_textfile(
    file: UploadFile = File(...),
    watermark: bool = Form(False),
):
    content = await file.read()
    text = content.decode("utf-8")
    urls = [line.strip() for line in text.splitlines() if line.strip()]

    if not urls:
        raise HTTPException(status_code=400, detail="No valid URLs found in file")

    task_id = str(uuid.uuid4())[:8]
    results = await download_from_url_list(urls, task_id, watermark)

    return {
        'task_id': task_id,
        'total_urls': len(urls),
        'downloaded': sum(1 for r in results if r.get('success')),
        'failed': sum(1 for r in results if not r.get('success')),
        'results': results,
    }


@app.get("/api/progress/{task_id}")
async def api_get_progress(task_id: str):
    progress = get_progress(task_id)
    if not progress:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        'task_id': progress.task_id,
        'percent': progress.percent,
        'status': progress.status,
        'filename': progress.filename,
        'error': progress.error,
    }


@app.get("/api/profile/{username}")
async def api_get_profile(username: str):
    task_id = str(uuid.uuid4())[:8]
    entries = await get_profile_media_list(username, task_id)
    return {'username': username, 'media_count': len(entries), 'media': entries}


@app.get("/files/{username}")
async def list_user_files(username: str):
    user_dir = DOWNLOAD_DIR / username
    if not user_dir.exists():
        raise HTTPException(status_code=404, detail="No files found")
    files = []
    for f in user_dir.iterdir():
        if f.is_file():
            files.append({
                'name': f.name,
                'size': f.stat().st_size,
                'url': f'/downloads/{username}/{f.name}',
            })
    return {'username': username, 'files': files}


if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
