# MangaWorld Downloader

Programma con finestra per scaricare i volumi da [MangaWorld](https://www.mangaworld.mx) come PDF, con la copertina del volume in prima pagina.

## Come si usa

1. Vai nella pagina **Releases** di questa repo e scarica lo zip per il tuo computer:
   - Windows → `MangaWorldDownloader-Windows.zip`
   - Mac → `MangaWorldDownloader-macOS.zip`
2. Estrai lo zip e fai doppio clic su **MangaWorldDownloader**.
3. Incolla il link del manga (es. `https://www.mangaworld.mx/manga/2144/hunter-x-hunter`) e premi **Cerca**.
4. Clicca sulle copertine dei volumi che vuoi e premi **Scarica**.
5. I PDF vengono salvati in `Download/MangaWorld/<nome del manga>`.

Per i manga non divisi in volumi scegli un intervallo di capitoli: verrà creato un unico PDF.

### Primo avvio
- **Windows**: se compare "Windows ha protetto il PC", clicca *Ulteriori informazioni* → *Esegui comunque*.
- **Mac**: la prima volta fai clic destro sull'app → *Apri* → *Apri*.

Il programma non è firmato digitalmente, per questo il sistema chiede conferma.

## Da sorgente

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Su Linux serve Tkinter: `sudo apt install python3-tk`.

## Pubblicare una nuova versione

```bash
git tag v1.0
git push --tags
```

GitHub Actions crea gli eseguibili e li allega alla Release.
