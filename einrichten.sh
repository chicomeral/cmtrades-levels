#!/bin/bash
# Einmalig: öffentliches GitHub-Repo „cmtrades-levels“ anlegen, Databento-Key als Secret speichern, Testlauf starten
set -e
GH=~/Trading-Daten/bin/gh
cd ~/cmtrades-levels
printf "__pycache__/\n.env\n" > .gitignore
[ -d .git ] || git init -q -b main
git config user.name "chicomeral"
git config user.email "chicomeral@users.noreply.github.com"
git add -A
git commit -qm "cMTrades Levels" || true
$GH repo create chicomeral/cmtrades-levels --public --source . --push
echo ""
echo ">>> Jetzt deinen Databento-Key einfügen (steht in ~/Trading-Daten/.env, beginnt mit db-) und Enter drücken:"
$GH secret set DATABENTO_API_KEY -R chicomeral/cmtrades-levels
$GH workflow run Levels -R chicomeral/cmtrades-levels
echo ""
echo "Fertig. Testlauf läuft. Link für jeden Morgen:"
echo "https://github.com/chicomeral/cmtrades-levels/blob/main/levels/heute.md"
