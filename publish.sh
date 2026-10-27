#!/usr/bin/env bash
# Publica una grabación de Mixxx: WAV -> FLAC, sube el FLAC como release de GitHub,
# copia el .cue al repo, actualiza sessions.json y hace push.
# Uso: ./publish.sh /ruta/a/2026-10-27_13h52m59s.wav ["Título de la sesión"]
set -euo pipefail

[[ $# -ge 1 ]] || { echo "Uso: $0 sesion.wav [\"título\"]" >&2; exit 1; }

wav=$(realpath "$1")
cue="${wav%.wav}.cue"
id=$(basename "$wav" .wav)
repo_dir=$(cd "$(dirname "$0")" && pwd)
cd "$repo_dir"

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
flac="$work/$id.flac"

echo "→ Convirtiendo a FLAC..."
ffmpeg -hide_banner -loglevel error -i "$wav" -c:a flac -compression_level 8 \
    -metadata title="$title" -metadata date="${date%T*}" \
    -metadata CUESHEET="$(sed "s/^FILE .*/FILE \"$id.flac\" WAVE/" "$cue")" \
    "$flac"
duration=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$flac")
echo "  $(du -h "$flac" | cut -f1) ($(du -h "$wav" | cut -f1) en WAV)"

echo "→ Subiendo release $id..."
if gh release view "$id" -R "$repo" >/dev/null 2>&1; then
    gh release upload "$id" "$flac" -R "$repo" --clobber
else
    gh release create "$id" "$flac" -R "$repo" --title "$title" --notes "Sesión grabada el ${date/T/ }"
fi
audio="https://github.com/$repo/releases/download/$id/$id.flac"

echo "→ Actualizando sessions.json..."
sed "s/^FILE .*/FILE \"$id.flac\" WAVE/" "$cue" > "sessions/$id.cue"
jq --arg id "$id" --arg title "$title" --arg date "$date" \
   --argjson duration "$duration" --arg audio "$audio" \
   '[.[] | select(.id != $id)] + [{id: $id, title: $title, date: $date, duration: $duration, audio: $audio}]
    | sort_by(.date) | reverse' sessions.json > "$work/sessions.json"
mv "$work/sessions.json" sessions.json

git add sessions.json "sessions/$id.cue"
git commit -m "Sesión $id: $title"
git push

echo "✓ Publicada: https://${repo%%/*}.github.io/${repo#*/}/#$id"
