#!/usr/bin/env python3
"""Iguala el volumen de los temas de una playlist de Mixxx (una sesión).

Mide la sonoridad de cada tema con el mismo método para todos (EBU R128 con
ffmpeg, como el ReplayGain 2.0 de Mixxx) y guarda en la db de Mixxx la ganancia
que lleva cada uno a la referencia (-18 LUFS por defecto). Con la normalización
ReplayGain activada en Mixxx, todos suenan al mismo volumen.

Solo toca library.replaygain / replaygain_peak de la db de Mixxx (no los archivos),
pide confirmación, hace copia de la db antes de escribir y se niega si Mixxx está abierto.

Con --sin-rg (u opción 0 del menú) mide todos los temas de la biblioteca que no
tienen ReplayGain: los que nunca se han cargado en un deck, que al previsualizarlos
desde las cajas suenan con el refuerzo por defecto en vez de normalizados.

Uso: replaygain_playlist.py [playlist (nombre o id) | --sin-rg] [--objetivo -18] [--simular]
"""
import argparse
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

DB = Path(os.environ.get("MIXXX_DB", Path.home() / ".mixxx/mixxxdb.sqlite"))


def preguntar(texto):
    # printf + input en vez de prompt de readline: así se ve también con `atuin scripts run`
    print(texto, end="", flush=True)
    return sys.stdin.readline().strip()


def medir(ruta):
    """(LUFS integrados, pico de muestra en dBFS) o None si falla."""
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", ruta, "-map", "0:a:0",
                        "-af", "ebur128=peak=sample", "-f", "null", "-"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    resumen = r.stderr.rsplit("Summary:", 1)[-1]
    i = re.search(r"I:\s*(-?[\d.]+) LUFS", resumen)
    p = re.search(r"Peak:\s*(-?[\d.]+|-inf) dBFS", resumen)
    if r.returncode or not i:
        return None
    return float(i[1]), (float(p[1]) if p and p[1] != "-inf" else None)


SIN_RG = "coalesce(l.replaygain, 0) = 0 and l.mixxx_deleted = 0 and tl.fs_deleted = 0"
COLUMNAS = """l.id, l.artist, l.title, tl.location, l.replaygain
              from library l join track_locations tl on tl.id = l.location"""


def temas_sin_rg(db):
    return db.execute(f"select {COLUMNAS} where {SIN_RG} order by l.artist, l.title").fetchall()


def temas_playlist(db, pid):
    return db.execute(f"""select {COLUMNAS} join PlaylistTracks pt on pt.track_id = l.id
                           where pt.playlist_id = ? order by pt.position""", (pid,)).fetchall()


def elegir(db, arg, sin_rg):
    """(nombre, temas) de la playlist elegida o de los temas sin ReplayGain."""
    if sin_rg:
        return "temas sin ReplayGain", temas_sin_rg(db)
    filas = db.execute("""select p.id, p.name, p.date_modified, count(pt.id)
                          from playlists p left join PlaylistTracks pt on pt.playlist_id = p.id
                          where p.hidden = 0 group by p.id order by p.date_modified desc""").fetchall()
    if arg:
        for f in filas:
            if str(f[0]) == arg or f[1].lower() == arg.lower():
                return f[1], temas_playlist(db, f[0])
        sys.exit(f"No hay ninguna playlist «{arg}»")
    num_sin = db.execute(f"select count(*) from library l join track_locations tl on tl.id = l.location where {SIN_RG}").fetchone()[0]
    print("Playlists de Mixxx (las más recientes primero):")
    print(f"   0) temas de la biblioteca sin ReplayGain ({num_sin})")
    for n, (pid, nombre, fecha, num) in enumerate(filas[:20], 1):
        print(f"  {n:2d}) {nombre}  ({num} temas, {fecha[:10]})")
    n = preguntar("Número de la playlist: ")
    if n == "0":
        return "temas sin ReplayGain", temas_sin_rg(db)
    if not n.isdigit() or not 1 <= int(n) <= min(20, len(filas)):
        sys.exit("Opción no válida")
    f = filas[int(n) - 1]
    return f[1], temas_playlist(db, f[0])


def db_gain(lineal):
    return 20 * math.log10(lineal) if lineal and lineal > 0 else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("playlist", nargs="?", help="nombre o id de la playlist (sin él, menú)")
    ap.add_argument("--sin-rg", action="store_true", help="todos los temas de la biblioteca sin ReplayGain")
    ap.add_argument("--objetivo", type=float, default=-18.0, help="sonoridad de referencia en LUFS (-18 = Mixxx)")
    ap.add_argument("--simular", action="store_true", help="medir y mostrar, sin guardar")
    args = ap.parse_args()

    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    nombre, temas = elegir(db, args.playlist, args.sin_rg)
    db.close()
    if not temas:
        sys.exit("No hay temas que medir")

    print(f"→ Midiendo {len(temas)} temas de «{nombre}»...")
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        medidas = list(ex.map(lambda t: medir(t[3]) if Path(t[3]).exists() else None, temas))

    cambios = []
    print(f"\n  {'#':>2}  {'LUFS':>6}  {'antes':>6}  {'ahora':>6}  tema")
    for n, (t, m) in enumerate(zip(temas, medidas), 1):
        tid, artista, titulo, ruta, rg = t
        antes = db_gain(rg)
        antes_txt = f"{antes:+6.1f}" if antes is not None else "     –"
        if not m:
            print(f"  {n:2d}  {'?':>6}  {antes_txt}  {'?':>6}  {artista} — {titulo}  ⚠ no se pudo medir")
            continue
        lufs, pico = m
        ganancia = args.objetivo - lufs
        print(f"  {n:2d}  {lufs:6.1f}  {antes_txt}  {ganancia:+6.1f}  {artista} — {titulo}")
        cambios.append((10 ** (ganancia / 20), 10 ** (pico / 20) if pico is not None else 0, tid))
    print(f"\n  (ganancias en dB para llevar cada tema a {args.objetivo:g} LUFS)")

    if args.simular or not cambios:
        return
    if subprocess.run(["pgrep", "-x", "mixxx"], capture_output=True).returncode == 0:
        sys.exit("\nMixxx está abierto: ciérralo y vuelve a lanzar el script para guardar.")
    if preguntar(f"\n¿Guardar el ReplayGain de {len(cambios)} temas en la db de Mixxx? [s/N] ").lower() not in ("s", "si", "sí", "y"):
        print("No se ha guardado nada.")
        return

    copia = DB.with_name(f"{DB.name}.bak-replaygain-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(DB, copia)
    with sqlite3.connect(DB) as db:
        db.executemany("update library set replaygain = ?, replaygain_peak = ? where id = ?", cambios)
    print(f"✓ Guardado. Copia de la db anterior: {copia}")


if __name__ == "__main__":
    main()
