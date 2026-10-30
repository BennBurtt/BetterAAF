#!/bin/bash
# Double-clique sur ce fichier pour fabriquer et installer BetterAAF.app
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
WORK="$HOME/Library/Application Support/BetterAAF-build"
echo ""
echo "=== Installation de BetterAAF (5 minutes environ, une seule fois) ==="
echo ""
mkdir -p "$WORK"
rm -rf "$WORK/betteraaf"
rm -rf "$WORK/assets"
cp -R "$SRC/betteraaf" "$SRC/assets" "$SRC/BetterAAF.py" "$SRC/requirements.txt" "$WORK/"
cd "$WORK"

if [ ! -x "$WORK/uv" ]; then
  echo "> Téléchargement de l'outil d'installation (uv)…"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$WORK" INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null
fi

echo "> Préparation de Python…"
"$WORK/uv" venv --quiet --allow-existing --python 3.12 .venv
echo "> Installation des composants…"
"$WORK/uv" pip install --quiet --python .venv/bin/python -r requirements.txt pyinstaller

echo "> Fabrication de l'appli…"
.venv/bin/pyinstaller --noconfirm --windowed --log-level ERROR --name BetterAAF \
  --osx-bundle-identifier fr.benjaminmartin.betteraaf --add-data "assets:assets" \
  --collect-all soundfile --collect-all _soundfile_data --hidden-import aaf2 \
  BetterAAF.py

DEST="/Applications"
[ -w "$DEST" ] || DEST="$HOME/Applications"
mkdir -p "$DEST"
rm -rf "$DEST/BetterAAF.app"
cp -R dist/BetterAAF.app "$DEST/"
echo ""
echo "=== Terminé : BetterAAF est dans $DEST ==="
open "$DEST/BetterAAF.app"
