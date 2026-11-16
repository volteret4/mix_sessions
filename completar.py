#!/usr/bin/env python3
"""Completa los tracklists publicados. Corre a diario en pepecono (cron).

Por cada tema de sessions/*.cue:
  - Año y sello: los del lanzamiento ORIGINAL según Discogs (el primer master
    oficial que contiene el tema, sin recopilatorios ni directos; de él, su
    lanzamiento principal). Sustituyen a los de las etiquetas, que suelen ser
    de reediciones o remasters.
  - Género y portada, solo si faltan: primero la db de music-fuzzy, después Discogs.

Las consultas a Discogs se guardan en .cache/completar.json: un tema encontrado
no se vuelve a consultar; uno sin resultado se reintenta pasados REINTENTAR_DIAS.
Al terminar hace commit y push si algo cambió.

Uso: completar.py [--sin-push] [--rehacer]
"""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import requests

from enrich import REPO, q, save_cover

MFUZZ = Path.home() / "gits/pollo/music-fuzzy"
MFUZZ_DB = MFUZZ / "db/sqlite/musica_local.sqlite"
MFUZZ_CFG = MFUZZ / "config/config_database_creator.json"
ESTADO = REPO / ".cache/completar.json"
REINTENTAR_DIAS = 30
PAUSA = 1.1  # Discogs: 60 peticiones/min con token
# Formatos que no son un lanzamiento original
FORMATOS_MALOS = {"Compilation", "Unofficial Release", "DVD", "DVD-Video", "VHS", "Blu-ray", "Mixed"}
CAMPOS = ("GENRE", "LABEL", "DATE", "COVER")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\s*\(\d+\)", "", s)  # 'Dion (3)' de Discogs
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def limpiar_titulo(t):
    """Quita añadidos entre paréntesis/corchetes: (Remastered), (original, Giorgio Moroder)..."""
    return re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", t).strip() or t


def limpiar_sello(s):
    return re.sub(r"\s*\(\d+\)$", "", s).strip()


def token_discogs():
    def buscar(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "discogs_token" and v:
                    return v
                if (r := buscar(v)):
                    return r
        elif isinstance(o, list):
            for v in o:
                if (r := buscar(v)):
                    return r
    return buscar(json.loads(MFUZZ_CFG.read_text()))


class Discogs:
    def __init__(self, token):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "mix-sessions/1.0", "Authorization": f"Discogs token={token}"})

    def get(self, url, **params):
        for intento in range(4):
            time.sleep(PAUSA * (intento + 1))
            r = self.s.get(url if url.startswith("http") else "https://api.discogs.com" + url, params=params, timeout=20)
            if r.status_code == 429:
                time.sleep(30 * (intento + 1))
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Discogs no responde: {url}")

    def candidatos(self, artista, titulo, **extra):
        artistas = [artista] + [a for a in re.split(r"\s*/\s*|\s+(?:&|feat\.?|ft\.?|with)\s+", artista) if a and a != artista]
        for a in artistas[:3]:
            for tipo in ("master", "release"):
                r = self.get("/database/search", type=tipo, artist=a, track=titulo, per_page=50, **extra) or {}
                res = []
                for x in r.get("results", []):
                    if not str(x.get("year", "")).isdigit() or FORMATOS_MALOS & set(x.get("format", [])):
                        continue
                    autor = norm(x.get("title", "").split(" - ")[0])
                    if norm(a) not in autor and autor not in norm(a):
                        continue
                    res.append(x)
                if res:
                    return res
        return []

    def original(self, artista, titulo, año_ref=None):
        """año_ref: año de las etiquetas. El original no puede ser posterior: si Discogs
        no da nada de ese año o antes, se busca solo en ese año; si tampoco, no se fía."""
        res = self.candidatos(artista, limpiar_titulo(titulo))
        if año_ref and (not res or min(int(x["year"]) for x in res) > int(año_ref)):
            res = self.candidatos(artista, limpiar_titulo(titulo), year=año_ref)
        if not res:
            return None
        primero = min(int(x["year"]) for x in res)
        mejor = None
        for x in [x for x in res if int(x["year"]) == primero][:3]:
            if x["type"] == "master":
                m = self.get(f"/masters/{x['id']}")
                rel = m and self.get(f"/releases/{m['main_release']}")
            else:
                rel = self.get(f"/releases/{x['id']}")
            if not rel:
                continue
            # Una fecha con solo el año no gana a una completa del mismo año
            fecha = (rel.get("released") or str(primero)).replace("-00", "")
            fecha = fecha + "-99" * (2 - fecha.count("-"))
            if mejor is None or fecha < mejor[0]:
                mejor = (fecha, rel, x)
        if not mejor:
            return None
        fecha, rel, x = mejor
        sellos = list(dict.fromkeys(limpiar_sello(l["name"]) for l in rel.get("labels", []) if l.get("name") != "Not On Label"))
        imagenes = rel.get("images") or []
        portada = next((i["uri"] for i in imagenes if i.get("type") == "primary"), imagenes[0]["uri"] if imagenes else x.get("cover_image"))
        return {
            "year": str(primero),
            "label": sellos[0] if sellos else None,
            "genre": ", ".join(rel.get("styles") or rel.get("genres") or []) or None,
            "cover_url": portada,
            "url": rel.get("uri"),
        }


def mfuzz(db, artista, titulo):
    """Género y portada del tema en la db de music-fuzzy (sus años y sellos son de la edición que hay en casa)."""
    if db is None:
        return {}
    filas = db.execute(
        """select s.artist, s.title, s.genre, al.genre, al.album_art_path, s.album_art_path_denorm
           from songs s left join artists ar on ar.name = s.album_artist
           left join albums al on al.artist_id = ar.id and al.name = s.album
           where s.title like ?""", (limpiar_titulo(titulo)[:40] + "%",)).fetchall()
    for a, t, g1, g2, p1, p2 in filas:
        if norm(a) == norm(artista) and norm(limpiar_titulo(t)) == norm(limpiar_titulo(titulo)):
            portada = next((p for p in (p1, p2) if p and Path(p).is_file()), None)
            return {"genre": g1 or g2, "cover_path": portada}
    return {}


def leer_bloques(lineas):
    """Separa el .cue en cabecera y un bloque por TRACK."""
    cab, bloques = [], []
    for l in lineas:
        if l.strip().startswith("TRACK "):
            bloques.append([l])
        elif bloques:
            bloques[-1].append(l)
        else:
            cab.append(l)
    return cab, bloques


def datos_bloque(b):
    d = {"rem": {}}
    for l in b[1:]:
        s = l.strip()
        if m := re.match(r'^REM (GENRE|LABEL|DATE|COVER) "?(.*?)"?$', s):
            d["rem"][m[1]] = m[2]
        elif m := re.match(r'^(TITLE|PERFORMER) "(.*)"$', s):
            d[m[1].lower()] = m[2]
    return d


def escribir_bloque(b, rem):
    resto = [l for l in b[1:] if not re.match(r"^\s*REM (GENRE|LABEL|DATE|COVER) ", l)]
    nuevas = []
    for k in CAMPOS:
        if rem.get(k):
            v = rem[k] if k == "DATE" else f'"{q(rem[k])}"'
            nuevas.append(f"    REM {k} {v}")
    return [b[0]] + nuevas + resto


def bajar_portada(url, s):
    r = s.get(url, timeout=30)
    r.raise_for_status()
    return save_cover(r.content)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-push", action="store_true", help="no hacer commit ni push")
    ap.add_argument("--rehacer", action="store_true", help="volver a consultar Discogs aunque haya caché")
    args = ap.parse_args()

    if not args.sin_push:
        subprocess.run(["git", "-C", REPO, "pull", "-q", "--rebase"], check=True)

    estado = json.loads(ESTADO.read_text()) if ESTADO.exists() else {}
    dg = Discogs(token_discogs())
    db = sqlite3.connect(f"file:{MFUZZ_DB}?mode=ro", uri=True) if MFUZZ_DB.exists() else None
    ahora = datetime.now()

    for cue in sorted((REPO / "sessions").glob("*.cue")):
        cab, bloques = leer_bloques(cue.read_text().splitlines())
        print(cue.name)
        for i, b in enumerate(bloques):
            d = datos_bloque(b)
            artista, titulo, rem = d.get("performer", ""), d.get("title", ""), dict(d["rem"])
            clave = f"{norm(artista)}|{norm(titulo)}"
            e = estado.get(clave)
            if args.rehacer or e is None or (e["resultado"] is None and
                                             datetime.fromisoformat(e["fecha"]) < ahora - timedelta(days=REINTENTAR_DIAS)):
                try:
                    e = {"fecha": ahora.isoformat(timespec="seconds"), "resultado": dg.original(artista, titulo, d["rem"].get("DATE"))}
                    estado[clave] = e
                    ESTADO.parent.mkdir(exist_ok=True)
                    ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=1))
                except Exception as ex:  # red o servicio caído: se reintenta en la próxima pasada
                    print(f"  ! {artista} — {titulo}: {ex}")
                    e = None
            res = (e or {}).get("resultado")
            if res:
                rem["DATE"] = res["year"]
                if res["label"]:
                    rem["LABEL"] = res["label"]
            if not rem.get("GENRE") or not rem.get("COVER"):
                mf = mfuzz(db, artista, titulo)
                if not rem.get("GENRE"):
                    rem["GENRE"] = mf.get("genre") or (res or {}).get("genre")
                if not rem.get("COVER"):
                    try:
                        if mf.get("cover_path"):
                            rem["COVER"] = save_cover(Path(mf["cover_path"]).read_bytes())
                        elif res and res.get("cover_url"):
                            rem["COVER"] = bajar_portada(res["cover_url"], dg.s)
                    except Exception as ex:
                        print(f"  ! portada de {artista} — {titulo}: {ex}")
            cambios = {k: (d["rem"].get(k), rem.get(k)) for k in CAMPOS if (d["rem"].get(k) or None) != (rem.get(k) or None)}
            if cambios:
                print(f"  ~ {artista} — {titulo}: " + "; ".join(f"{k} {a!r} → {n!r}" for k, (a, n) in cambios.items()))
            bloques[i] = escribir_bloque(b, rem)
        cue.write_text("\n".join(cab + [l for b in bloques for l in b]) + "\n")

    if args.sin_push:
        return
    git = lambda *a: subprocess.run(["git", "-C", REPO, *a], check=True, capture_output=True, text=True).stdout
    git("add", "sessions", "covers")
    if not git("diff", "--cached", "--name-only").strip():
        print("Sin cambios")
        return
    git("commit", "-q", "-m", "Completar tracklists: año y sello originales (Discogs), géneros y portadas")
    git("push", "-q")
    print("✓ Cambios subidos")


if __name__ == "__main__":
    sys.exit(main())
