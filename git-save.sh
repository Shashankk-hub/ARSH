#!/bin/bash

git add .

if git diff --cached --quiet; then
    echo "No changes to commit."
    exit 0
fi

git commit -m "Auto update: $(date '+%Y-%m-%d %H:%M:%S')"

git push
