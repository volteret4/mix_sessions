#!/usr/bin/env python3
"""Versión local de Mix Sessions: la misma web con un modo edición para poner título,
grupo y etiquetas a cada sesión. «Guardar y publicar» escribe sessions.json y hace
commit + push, así que la versión pública (GitHub Pages) se actualiza sola.

  python3 editor.py              # http://127.0.0.1:8796
  python3 editor.py --host 0.0.0.0 --port 8796   # para abrirlo desde otro equipo de casa

Sin dependencias. Solo cambia los campos title, group y tags de sesiones que ya existen.
"""
import argparse
import json
import subprocess
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent
SESSIONS = REPO / 'sessions.json'


def git(*args):
    r = subprocess.run(['git', *args], cwd=REPO, capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else f'git {args[0]} falló')
    return r.stdout


def apply(current, by_id):
    """Copia title, group y tags de las sesiones editadas. Devuelve cuántas cambian."""
    changed = 0
    for s in current:
        new = by_id.get(s['id'])
        if not new:
            continue
        before = json.dumps(s, sort_keys=True)
        title = str(new.get('title') or '').strip()
        if title:
            s['title'] = title[:200]
        group = str(new.get('group') or '').strip()[:80]
        if group:
            s['group'] = group
        else:
            s.pop('group', None)
        tags = [str(t).strip()[:40] for t in (new.get('tags') or []) if str(t).strip()]
        if tags:
            s['tags'] = list(dict.fromkeys(tags))[:20]
        else:
            s.pop('tags', None)
        changed += before != json.dumps(s, sort_keys=True)
    return changed


def save(incoming):
    by_id = {s.get('id'): s for s in incoming if isinstance(s, dict)}
    # pepecono también hace push (completar.py): primero lo suyo, como publish.sh
    git('pull', '-q', '--rebase', '--autostash')
    current = json.loads(SESSIONS.read_text())
    changed = apply(current, by_id)
    if not changed:
        return 'No había cambios.'
    SESSIONS.write_text(json.dumps(current, ensure_ascii=False, indent=2) + '\n')
    git('add', 'sessions.json')
    git('commit', '-q', '-m', f'Sesiones: título, grupo y etiquetas ({changed} cambiadas)')
    git('push', '-q')
    return f'Guardado y publicado: {changed} sesión(es). GitHub Pages tarda un minuto en actualizarse.'


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split('?')[0] in ('/', '/index.html'):
            html = (REPO / 'index.html').read_text()
            html = html.replace('<script src="app.js', '<script>window.MIX_EDITOR = true;</script><script src="app.js', 1)
            body = html.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        if self.path != '/api/sessions':
            self.send_error(404)
            return
        try:
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            out = {'ok': True, 'message': save(data if isinstance(data, list) else [])}
        except Exception as e:  # se enseña en la barra del editor
            out = {'ok': False, 'error': str(e)}
        body = json.dumps(out, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8796)
    a = ap.parse_args()
    print(f'Mix Sessions (edición): http://{a.host}:{a.port}/')
    ThreadingHTTPServer((a.host, a.port), partial(Handler, directory=str(REPO))).serve_forever()
