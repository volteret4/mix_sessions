#!/usr/bin/env bash
# Publica una grabación de Mixxx: WAV/FLAC -> MP3 320k, sube el MP3 como release de GitHub,
# copia el .cue al repo, actualiza sessions.json y hace push.
# Uso: ./publish.sh /ruta/a/2026-10-27_13h52m59s.{wav,flac} ["Título de la sesión"]
#      ./publish.sh    (sin argumentos: elige entre las grabaciones de Mixxx sin publicar)
set -euo pipefail

repo_dir=$(cd "$(dirname "$0")" && pwd)
cd "$repo_dir"
rec_dir=${MIXXX_RECORDINGS:-$HOME/Música/Mixxx/Recordings}

# Los prompts se imprimen con printf (no read -p) para que se vean también
# cuando stdin es un pipe, como pasa con `atuin scripts run`.
if [[ $# -eq 0 ]]; then
    shopt -s nullglob
    pending=()
    for f in "$rec_dir"/*.wav "$rec_dir"/*.flac; do
        rid=$(basename "${f%.*}")
        [[ -f ${f%.*}.cue ]] || continue
        jq -e --arg id "$rid" 'any(.[]; .id == $id)' sessions.json >/dev/null && continue
        pending+=("$f")
    done
    [[ ${#pending[@]} -gt 0 ]] || { echo "No hay sesiones sin publicar en $rec_dir"; exit 0; }
    mapfile -t pending < <(printf '%s\n' "${pending[@]}" | sort)

    echo "Sesiones sin publicar en $rec_dir:"
    for i in "${!pending[@]}"; do
        f=${pending[$i]}
        secs=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f" | cut -d. -f1)
        tracks=$(grep -c '^ *TRACK' "${f%.*}.cue" || true)
        printf '  %d) %s  %3d:%02d  %2d temas  %s\n' $((i + 1)) "$(basename "$f")" $((secs / 60)) $((secs % 60)) "$tracks" "$(du -h "$f" | cut -f1)"
    done
    printf "Número de la sesión: "; read -r n
    [[ $n =~ ^[0-9]+$ && $n -ge 1 && $n -le ${#pending[@]} ]] || { echo "Opción no válida" >&2; exit 1; }
    printf "Título (vacío = automático): "; read -r t
    set -- "${pending[$((n - 1))]}" "$t"
fi

wav=$(realpath "$1")
[[ $wav =~ \.(wav|flac)$ ]] || { echo "Formato no soportado: $wav (usa .wav o .flac)" >&2; exit 1; }
cue="${wav%.*}.cue"
id=$(basename "${wav%.*}")

[[ -f $wav ]] || { echo "No existe $wav" >&2; exit 1; }
[[ -f $cue ]] || { echo "No existe $cue" >&2; exit 1; }

# 2026-10-27_13h52m59s -> 2026-10-27T13:52:59
if [[ $id =~ ^([0-9]{4}-[0-9]{2}-[0-9]{2})_([0-9]{2})h([0-9]{2})m([0-9]{2})s$ ]]; then
    date="${BASH_REMATCH[1]}T${BASH_REMATCH[2]}:${BASH_REMATCH[3]}:${BASH_REMATCH[4]}"
else
    date=$(date -r "$wav" +%Y-%m-%dT%H:%M:%S)
fi
title=${2:-"Sesión ${date%T*} ${date:11:5}"}

repo=$(gh repo view --json nameWithOwner -q .nameWithOwner)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mp3="$work/$id.mp3"

echo "→ Convirtiendo a MP3 320k..."
ffmpeg -hide_banner -loglevel error -i "$wav" -c:a libmp3lame -b:a 320k \
    -metadata title="$title" -metadata date="${date%T*}" \
    -id3v2_version 3 "$mp3"
duration=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$mp3")
echo "  $(du -h "$mp3" | cut -f1) ($(du -h "$wav" | cut -f1) el original)"

echo "→ Subiendo release $id..."
if gh release view "$id" -R "$repo" >/dev/null 2>&1; then
    gh release upload "$id" "$mp3" -R "$repo" --clobber
else
    gh release create "$id" "$mp3" -R "$repo" --title "$title" --notes "Sesión grabada el ${date/T/ }"
fi
audio="https://github.com/$repo/releases/download/$id/$id.mp3"

echo "→ Actualizando sessions.json..."
sed "s/^FILE .*/FILE \"$id.mp3\" MP3/" "$cue" > "sessions/$id.cue"
jq --arg id "$id" --arg title "$title" --arg date "$date" \
   --argjson duration "$duration" --arg audio "$audio" \
   '[.[] | select(.id != $id)] + [{id: $id, title: $title, date: $date, duration: $duration, audio: $audio}]
    | sort_by(.date) | reverse' sessions.json > "$work/sessions.json"
mv "$work/sessions.json" sessions.json

git add sessions.json "sessions/$id.cue"
git commit -m "Sesión $id: $title"
git push

echo "✓ Publicada: https://${repo%%/*}.github.io/${repo#*/}/#$id"
