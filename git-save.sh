#!/bin/bash
set -euo pipefail

if [[ "$#" -eq 0 ]]; then
    echo "Usage: $0 <file-or-directory> [...]"
    echo "Choose paths to stage explicitly, then review the diff before committing."
    exit 2
fi

git add -- "$@"

if git diff --cached --quiet; then
    echo "No changes to commit."
    exit 0
fi

git diff --cached --stat
git diff --cached
read -r -p "Commit these staged changes? [y/N] " answer
if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
    echo "Commit canceled. Staged changes were left for review."
    exit 1
fi

git commit -m "Update project: $(date '+%Y-%m-%d %H:%M:%S')"
echo "Review the commit, then push it manually with: git push"
