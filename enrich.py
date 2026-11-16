#!/usr/bin/env python3
"""Añade género, sello, año y portada a cada tema de un .cue de sesión.

Busca cada tema en el historial de Mixxx del día de la sesión (la entrada con el
mismo artista y título más cercana a su momento en la grabación) para saber qué
archivo exacto sonó, y lee sus etiquetas. Escribe en el .cue, dentro de cada TRACK:
    REM GENRE "..."   REM LABEL "..."   REM DATE 1971   REM COVER "covers/<hash>.jpg"
Uso: enrich.py sessions/2026-11-16_18h20m33s.cue
"""
import hashlib
import io
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import mutagen
from PIL import Image

DB = Path(os.environ.get("MIXXX_DB", Path.home() / ".mixxx/mixxxdb.sqlite"))
REPO = Path(__file__).resolve().parent
COVERS = REPO / "covers"
COVER_PX = 300


def norm(s):
    s = (s or "").lower().replace("’", "'").replace("‐", "-")
    return re.sub(r"\s+", " ", s).strip()


def parse_cue(lines):
    tracks = []
    for i, line in enumerate(lines):
        l = line.strip()
        if l.startswith("TRACK "):
            tracks.append({"line": i, "title": "", "performer": "", "start": 0.0})
        elif tracks and (m := re.match(r'^(TITLE|PERFORMER) "(.*)"$', l)):
            tracks[-1][m[1].lower()] = m[2]
        elif tracks and (m := re.match(r"^INDEX 01 (\d+):(\d+):(\d+)", l)):
            tracks[-1]["start"] = int(m[1]) * 60 + int(m[2]) + int(m[3]) / 75
    return tracks


def session_start(sid):
    m = re.match(r"^(\d{4}-\d{2}-\d{2})_(\d{2})h(\d{2})m(\d{2})s$", sid)
    local = datetime.strptime(f"{m[1]} {m[2]}:{m[3]}:{m[4]}", "%Y-%m-%d %H:%M:%S").astimezone()
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def find_file(db, sid, t, start):
    """Archivo que sonó: entrada del historial más cercana; si no, cualquiera de la biblioteca."""
    target = start.timestamp() + t["start"]
    rows = db.execute(
        """select l.artist, l.title, tl.location, pt.pl_datetime_added
           from PlaylistTracks pt join playlists p on p.id = pt.playlist_id
           join library l on l.id = pt.track_id join track_locations tl on tl.id = l.location
           where p.hidden = 2 and p.name like ?""",
        (sid[:10] + "%",),
    ).fetchall()
    best = None
    for artist, title, loc, added in rows:
        if norm(artist) == norm(t["performer"]) and norm(title) == norm(t["title"]):
            d = abs(datetime.strptime(added, "%Y-%m-%d %H:%M:%S").timestamp() - target)
            if best is None or d < best[0]:
                best = (d, loc)
    if best:
        return best[1]
    for artist, title, loc in db.execute(
        "select l.artist, l.title, tl.location from library l join track_locations tl on tl.id = l.location"
    ):
        if norm(artist) == norm(t["performer"]) and norm(title) == norm(t["title"]) and Path(loc).exists():
            return loc
    return None


def first(tags, *keys):
    for k in keys:
        try:
            v = tags.get(k)
        except (ValueError, KeyError):  # p. ej. claves no ASCII en Vorbis
            continue
        if v:
            v = v.text if hasattr(v, "text") else v
            v = [str(x) for x in (v if isinstance(v, list) else [v]) if str(x).strip()]
            if v:
                return v
    return None


def read_meta(path):
    f = mutagen.File(path)
    tags = f.tags or {}
    genre = first(tags, "genre", "TCON", "©gen")
    label = first(tags, "label", "organization", "publisher", "TPUB", "----:com.apple.iTunes:LABEL")
    # El año más antiguo entre todas las fechas: en recopilatorios/reediciones
    # "originalyear" suele ser el del disco, no el de la canción.
    years = [y for k in ("originalyear", "originaldate", "TDOR", "date", "year", "TDRC", "©day")
             for v in (first(tags, k) or []) for y in re.findall(r"\b(1[89]\d\d|20\d\d)\b", v)]
    year = [min(years)] if years else None
    if not label and (m := re.search(r"\[([^\]]+)\]\.\w+$", path)):
        label = [m[1]]
    pic = None
    if getattr(f, "pictures", None):
        pic = f.pictures[0].data
    else:
        for k in tags.keys():
            if k.startswith("APIC"):
                pic = tags[k].data
                break
            if k == "covr":
                pic = bytes(tags[k][0])
                break
    if pic is None:
        for name in ("cover.jpg", "folder.jpg", "front.jpg", "cover.png"):
            p = Path(path).parent / name
            if p.exists():
                pic = p.read_bytes()
                break
    return {
        "genre": ", ".join(genre) if genre else None,
        "label": " / ".join(dict.fromkeys(x for l in label for x in re.split(r"\s*;\s*", l))) if label else None,
        "year": (m[0] if year and (m := re.search(r"\d{4}", year[0])) else None),
        "cover": save_cover(pic) if pic else None,
    }


def save_cover(data):
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((COVER_PX, COVER_PX))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85, optimize=True)
    out = buf.getvalue()
    COVERS.mkdir(exist_ok=True)
    name = f"covers/{hashlib.sha1(out).hexdigest()[:16]}.jpg"
    (REPO / name).write_bytes(out)
    return name


def q(s):
    return s.replace('"', "'")


def main(cue_path):
    cue_path = Path(cue_path)
    sid = cue_path.stem
    lines = [l for l in cue_path.read_text().splitlines() if not re.match(r"^\s*REM (GENRE|LABEL|DATE|COVER) ", l)]
    tracks = parse_cue(lines)
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    start = session_start(sid)
    for t in reversed(tracks):  # al revés para que insertar no mueva los índices pendientes
        path = find_file(db, sid, t, start)
        if not path or not Path(path).exists():
            print(f"  ✗ sin archivo: {t['performer']} — {t['title']}", file=sys.stderr)
            continue
        meta = read_meta(path)
        rem = []
        if meta["genre"]: rem.append(f'    REM GENRE "{q(meta["genre"])}"')
        if meta["label"]: rem.append(f'    REM LABEL "{q(meta["label"])}"')
        if meta["year"]: rem.append(f"    REM DATE {meta['year']}")
        if meta["cover"]: rem.append(f'    REM COVER "{meta["cover"]}"')
        lines[t["line"] + 1:t["line"] + 1] = rem
        print(f"  ✓ {t['performer']} — {t['title']}: {meta['genre']} | {meta['label']} | {meta['year']} | {'portada' if meta['cover'] else 'sin portada'}")
    cue_path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(p)
        main(p)
