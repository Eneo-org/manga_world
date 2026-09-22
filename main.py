"""MangaWorld Downloader: finestra desktop per scaricare volumi in PDF."""

from __future__ import annotations

import io
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

import scraper

THUMB_SIZE = (90, 135)
COLUMNS = 5


def default_folder() -> Path:
    downloads = Path.home() / "Downloads"
    return (downloads if downloads.exists() else Path.home()) / "MangaWorld"


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class ScrollFrame(ttk.Frame):
    """Frame con barra di scorrimento verticale."""

    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        # rotellina del mouse (Windows/macOS e Linux)
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120) or -int(e.delta), "units"))
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))

    def clear(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MangaWorld Downloader")
        self.geometry("820x720")
        self.minsize(600, 500)

        self.events: queue.Queue = queue.Queue()  # messaggi dai thread verso la finestra
        self.manga: scraper.Manga | None = None
        self.vol_vars: list[tk.BooleanVar] = []
        self.thumbs: dict[int, ImageTk.PhotoImage] = {}
        self.thumb_labels: dict[int, ttk.Label] = {}
        self.folder = default_folder()
        self.busy = False

        self._build()
        self.after(100, self._poll_events)

    # ---------- interfaccia ----------

    def _build(self):
        style = ttk.Style(self)
        style.configure("Title.TLabel", font=("TkDefaultFont", 16, "bold"))
        style.configure("Big.TButton", font=("TkDefaultFont", 12, "bold"), padding=8)

        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="Incolla il link del manga da MangaWorld:").pack(anchor="w")
        row = ttk.Frame(top)
        row.pack(fill="x", pady=(4, 0))
        self.url_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.url_var, font=("TkDefaultFont", 11))
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<Return>", lambda e: self.search())
        entry.focus()
        self.search_btn = ttk.Button(row, text="Cerca", command=self.search)
        self.search_btn.pack(side="left", padx=(8, 0))

        self.title_var = tk.StringVar()
        ttk.Label(self, textvariable=self.title_var, style="Title.TLabel", padding=(12, 4)).pack(anchor="w")

        tools = ttk.Frame(self, padding=(12, 0))
        tools.pack(fill="x")
        self.all_btn = ttk.Button(tools, text="Seleziona tutti", command=lambda: self.set_all(True))
        self.none_btn = ttk.Button(tools, text="Nessuno", command=lambda: self.set_all(False))
        self.count_var = tk.StringVar()
        ttk.Label(tools, textvariable=self.count_var).pack(side="right")

        self.list = ScrollFrame(self)
        self.list.pack(fill="both", expand=True, padx=12, pady=8)

        bottom = ttk.Frame(self, padding=12)
        bottom.pack(fill="x")
        frow = ttk.Frame(bottom)
        frow.pack(fill="x")
        ttk.Label(frow, text="Salva in:").pack(side="left")
        self.folder_var = tk.StringVar(value=str(self.folder))
        ttk.Label(frow, textvariable=self.folder_var, foreground="#555").pack(side="left", padx=6)
        ttk.Button(frow, text="Cambia…", command=self.choose_folder).pack(side="right")
        ttk.Button(frow, text="Apri cartella", command=lambda: open_folder(self.folder)).pack(side="right", padx=6)

        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var).pack(anchor="w", pady=(10, 2))
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x")
        self.download_btn = ttk.Button(bottom, text="Scarica", style="Big.TButton", command=self.download, state="disabled")
        self.download_btn.pack(fill="x", pady=(10, 0))

    def choose_folder(self):
        path = filedialog.askdirectory(initialdir=self.folder)
        if path:
            self.folder = Path(path)
            self.folder_var.set(path)

    def update_count(self):
        if self.manga and self.manga.volumes:
            n = sum(v.get() for v in self.vol_vars)
            self.count_var.set(f"{n} di {len(self.vol_vars)} selezionati")
            ok = n > 0
        else:
            self.count_var.set("")
            ok = self.manga is not None
        self.download_btn.configure(state="normal" if ok and not self.busy else "disabled")

    def set_all(self, value: bool):
        for v in self.vol_vars:
            v.set(value)
        self.update_count()

    # ---------- ricerca ----------

    def search(self):
        url = self.url_var.get().strip()
        if not url or self.busy:
            return
        self.search_btn.configure(state="disabled")
        self.status_var.set("Cerco il manga…")

        def work():
            try:
                self.events.put(("manga", scraper.fetch_manga(url)))
            except Exception as exc:
                self.events.put(("search_error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def show_manga(self, manga: scraper.Manga):
        self.manga = manga
        self.title_var.set(manga.title)
        self.list.clear()
        self.vol_vars = []
        self.thumbs.clear()
        self.thumb_labels.clear()

        if manga.volumes:
            self.all_btn.pack(side="left")
            self.none_btn.pack(side="left", padx=6)
            for i, vol in enumerate(manga.volumes):
                var = tk.BooleanVar(value=False)
                self.vol_vars.append(var)
                card = ttk.Frame(self.list.inner, padding=6)
                card.grid(row=i // COLUMNS, column=i % COLUMNS, sticky="n")
                img = ttk.Label(card, text="…", width=12, anchor="center", cursor="hand2")
                img.pack()
                img.bind("<Button-1>", lambda e, v=var: (v.set(not v.get()), self.update_count()))
                self.thumb_labels[i] = img
                ttk.Checkbutton(card, text=vol.name, variable=var, command=self.update_count).pack()
                ttk.Label(card, text=f"{len(vol.chapters)} capitoli", foreground="#777").pack()
            self.load_thumbs([v.cover for v in manga.volumes])
            self.status_var.set("Scegli i volumi (clicca sulle copertine) e premi Scarica.")
        else:
            self.all_btn.pack_forget()
            self.none_btn.pack_forget()
            names = [c.name for c in manga.loose_chapters]
            box = ttk.Frame(self.list.inner, padding=10)
            box.pack(anchor="w")
            ttk.Label(box, text="Questo manga non è diviso in volumi.\n"
                                "Scegli da quale a quale capitolo scaricare (verrà creato un unico PDF):").pack(anchor="w")
            r = ttk.Frame(box)
            r.pack(anchor="w", pady=10)
            self.ch_from = ttk.Combobox(r, values=names, state="readonly", width=22)
            self.ch_to = ttk.Combobox(r, values=names, state="readonly", width=22)
            ttk.Label(r, text="Da").pack(side="left")
            self.ch_from.pack(side="left", padx=6)
            ttk.Label(r, text="A").pack(side="left", padx=(12, 0))
            self.ch_to.pack(side="left", padx=6)
            if names:
                self.ch_from.current(0)
                self.ch_to.current(len(names) - 1)
            self.status_var.set("Scegli i capitoli e premi Scarica.")
        self.update_count()

    def load_thumbs(self, urls: list[str | None]):
        def fetch(i_url):
            i, url = i_url
            if not url:
                return
            try:
                im = Image.open(io.BytesIO(scraper.get(url, retries=2).content)).convert("RGB")
                im.thumbnail(THUMB_SIZE)
                self.events.put(("thumb", (i, im)))
            except Exception:
                pass

        def work():
            with ThreadPoolExecutor(6) as pool:
                list(pool.map(fetch, enumerate(urls)))

        threading.Thread(target=work, daemon=True).start()

    # ---------- download ----------

    def download(self):
        manga = self.manga
        if not manga or self.busy:
            return
        items = []  # (nome, capitoli, copertina)
        if manga.volumes:
            for var, vol in zip(self.vol_vars, manga.volumes):
                if var.get():
                    items.append((f"{manga.title} - {vol.name}", vol.chapters, vol.cover))
        else:
            a, b = sorted((self.ch_from.current(), self.ch_to.current()))
            chapters = manga.loose_chapters[a : b + 1]
            label = chapters[0].name if len(chapters) == 1 else f"{chapters[0].name} - {chapters[-1].name}"
            items.append((f"{manga.title} - {label}", chapters, manga.cover))
        if not items:
            return

        self.busy = True
        self.download_btn.configure(state="disabled")
        self.search_btn.configure(state="disabled")
        dest = self.folder / scraper.safe_filename(manga.title)
        threading.Thread(target=self._download_worker, args=(items, dest), daemon=True).start()

    def _download_worker(self, items, dest: Path):
        errors = []
        for n, (name, chapters, cover) in enumerate(items, 1):
            prefix = f"[{n}/{len(items)}] {name}"

            def progress(done, total, message, prefix=prefix):
                text = f"{prefix}: {message}" + (f" {done}/{total}" if total else "")
                self.events.put(("progress", (done, total, text)))

            try:
                scraper.build_pdf(name, chapters, cover, dest / f"{scraper.safe_filename(name)}.pdf", progress)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        self.events.put(("done", (len(items), errors, dest)))

    def finish(self, count: int, errors: list[str], dest: Path):
        self.busy = False
        self.search_btn.configure(state="normal")
        self.update_count()
        ok = count - len(errors)
        self.status_var.set(f"Finito! {ok} PDF salvati in {dest}")
        if errors:
            messagebox.showwarning("Alcuni download non sono riusciti", "\n".join(errors))
        if ok and messagebox.askyesno("Fatto!", f"{ok} PDF pronti.\nVuoi aprire la cartella?"):
            open_folder(dest)

    # ---------- eventi dai thread ----------

    def _poll_events(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "manga":
                    self.search_btn.configure(state="normal")
                    self.show_manga(data)
                elif kind == "search_error":
                    self.search_btn.configure(state="normal")
                    self.status_var.set("")
                    messagebox.showerror("Errore", data)
                elif kind == "thumb":
                    i, im = data
                    if i in self.thumb_labels:
                        self.thumbs[i] = ImageTk.PhotoImage(im)
                        self.thumb_labels[i].configure(image=self.thumbs[i], text="", width=0)
                elif kind == "progress":
                    done, total, text = data
                    self.status_var.set(text)
                    self.progress.configure(maximum=max(total, 1), value=done)
                elif kind == "done":
                    self.finish(*data)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)


if __name__ == "__main__":
    App().mainloop()
