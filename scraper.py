"""Scraping di MangaWorld e creazione dei PDF."""

from __future__ import annotations

import io
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import img2pdf
import requests
from bs4 import BeautifulSoup
from PIL import Image

BASE_HOST = "mangaworld"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
PAGE_WORKERS = 6
JPEG_QUALITY = 90

_local = threading.local()


def session() -> requests.Session:
    """Una sessione HTTP per thread (requests.Session non è thread-safe)."""
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT, "Referer": "https://www.mangaworld.mx/"})
        _local.session = s
    return s


def get(url: str, retries: int = 4, **kwargs) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            r = session().get(url, timeout=30, **kwargs)
            if r.status_code == 200:
                return r
            last_exc = RuntimeError(f"HTTP {r.status_code} per {url}")
        except requests.RequestException as exc:
            last_exc = exc
        time.sleep(1.5 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


@dataclass
class Chapter:
    name: str
    url: str


@dataclass
class Volume:
    name: str
    cover: str | None
    chapters: list[Chapter] = field(default_factory=list)


@dataclass
class Manga:
    title: str
    url: str
    cover: str | None
    volumes: list[Volume]
    # Capitoli non assegnati a un volume (manga senza volumi, o capitoli "sciolti")
    loose_chapters: list[Chapter]

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "cover": self.cover,
            "volumes": [
                {
                    "name": v.name,
                    "cover": v.cover,
                    "chapters": [c.name for c in v.chapters],
                }
                for v in self.volumes
            ],
            "loose_chapters": [c.name for c in self.loose_chapters],
        }


def normalize_manga_url(url: str) -> str:
    """Accetta anche link a un capitolo e restituisce la pagina del manga."""
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    if BASE_HOST not in parsed.netloc:
        raise ValueError("Il link deve essere di mangaworld (es. https://www.mangaworld.mx/manga/2144/hunter-x-hunter)")
    m = re.match(r"(/manga/\d+/[^/?#]+)", parsed.path)
    if not m:
        raise ValueError("Link non valido: deve essere nel formato /manga/<id>/<nome>")
    return f"{parsed.scheme}://{parsed.netloc}{m.group(1)}"


def _parse_chapters(container) -> list[Chapter]:
    chapters = []
    for a in container.select("a.chap"):
        span = a.find("span")
        name = (span.get_text(strip=True) if span else a.get_text(strip=True)) or "Capitolo"
        chapters.append(Chapter(name=name, url=a["href"]))
    chapters.reverse()  # il sito li elenca dal più recente
    return chapters


def fetch_manga(url: str) -> Manga:
    url = normalize_manga_url(url)
    soup = BeautifulSoup(get(url).text, "html.parser")

    h1 = soup.select_one("h1.name")
    title = h1.get_text(strip=True) if h1 else "Manga"
    og = soup.find("meta", property="og:image")
    cover = og["content"] if og and og.get("content") else None

    wrapper = soup.select_one(".chapters-wrapper")
    if wrapper is None:
        raise ValueError("Nessun capitolo trovato in questa pagina.")

    volumes: list[Volume] = []
    for el in wrapper.select(".volume-element"):
        name_el = el.select_one(".volume-name")
        name = name_el.get_text(strip=True) if name_el else "Volume"
        vol_cover = None
        icon = el.select_one("[data-volume-image]")
        if icon:
            m = re.search(r"src=[\"']?([^\s\"'>]+)", icon["data-volume-image"])
            if m:
                vol_cover = m.group(1)
        chapters_el = el.select_one(".volume-chapters") or el
        volumes.append(Volume(name=name, cover=vol_cover, chapters=_parse_chapters(chapters_el)))
    volumes.reverse()

    # Capitoli direttamente dentro il wrapper, fuori da qualsiasi volume
    loose = []
    for div in wrapper.find_all("div", class_="chapter", recursive=False):
        loose.extend(_parse_chapters(div))
    loose.reverse()

    # Il volume in corso spesso non ha ancora una copertina: usiamo quella del manga
    for v in volumes:
        v.cover = v.cover or cover

    return Manga(title=title, url=url, cover=cover, volumes=volumes, loose_chapters=loose)


def chapter_pages(chapter_url: str) -> list[str]:
    soup = BeautifulSoup(get(chapter_url, params={"style": "list"}).text, "html.parser")
    imgs = soup.select("img.page-image")

    def order(img):
        m = re.search(r"(\d+)$", img.get("id", ""))
        return int(m.group(1)) if m else 0

    urls = [img.get("src") or img.get("data-src") for img in sorted(imgs, key=order)]
    return [u for u in urls if u]


def _save_as_jpeg(data: bytes, dest: Path) -> None:
    with Image.open(io.BytesIO(data)) as im:
        if im.format == "JPEG" and im.mode in ("RGB", "L"):
            dest.write_bytes(data)  # già JPEG: nessuna ricompressione
            return
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.save(dest, "JPEG", quality=JPEG_QUALITY, optimize=True)


def download_image(url: str, dest: Path) -> Path:
    _save_as_jpeg(get(url).content, dest)
    return dest


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "", name).strip()
    return re.sub(r"\s+", " ", name) or "manga"


def build_pdf(
    title: str,
    chapters: list[Chapter],
    cover_url: str | None,
    out_path: Path,
    progress=None,
) -> Path:
    """Scarica le pagine dei capitoli e crea un unico PDF con la copertina in prima pagina.

    `progress(done, total, message)` viene chiamata durante l'avanzamento.
    """
    progress = progress or (lambda *a: None)
    tmp = Path(tempfile.mkdtemp(prefix="mangaworld_"))
    try:
        progress(0, 0, "Recupero elenco pagine…")
        jobs: list[tuple[str, Path]] = []
        if cover_url:
            jobs.append((cover_url, tmp / "0000_cover.jpg"))
        for ci, ch in enumerate(chapters):
            progress(0, 0, f"Recupero elenco pagine: {ch.name} ({ci + 1}/{len(chapters)})")
            for pi, page_url in enumerate(chapter_pages(ch.url)):
                jobs.append((page_url, tmp / f"{ci + 1:04d}_{pi:04d}.jpg"))

        total = len(jobs)
        if total == 0:
            raise RuntimeError("Nessuna pagina trovata.")
        done = 0
        lock = threading.Lock()
        progress(0, total, "Download pagine…")

        def work(job):
            nonlocal done
            try:
                download_image(*job)
            except Exception:
                # la copertina è facoltativa, le pagine no
                if job[1].name.startswith("0000_cover"):
                    return
                raise
            with lock:
                done += 1
                progress(done, total, "Download pagine…")

        with ThreadPoolExecutor(PAGE_WORKERS) as pool:
            list(pool.map(work, jobs))

        progress(total, total, "Creazione PDF…")
        files = sorted(str(p) for p in tmp.glob("*.jpg"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(img2pdf.convert(files, title=title))
        return out_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
