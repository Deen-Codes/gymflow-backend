#!/bin/bash
# PUSH_LATEST.command — one-click backend push.
#
# ──────────────────────────────────────────────────────────────────
# FIRST-TIME SETUP (one terminal command — run once, ever):
#   chmod +x ~/Documents/gymflow-backend/PUSH_LATEST.command
# ──────────────────────────────────────────────────────────────────
#
# After that, double-click this file in Finder to:
#   1. cd into ~/Documents/gymflow-backend
#   2. Show what's pending (git status)
#   3. If there are uncommitted changes, stage them all, commit
#      with a timestamped message, and push
#   4. Otherwise just push any local commits that haven't gone up yet
#
# First run: macOS will prompt for security ("can't be opened, unidentified
# developer"). To allow: System Settings → Privacy & Security → click
# "Open Anyway" next to the warning. Subsequent runs work without prompt.
#
# This file lives in the repo so it travels with the project; if you want
# a desktop shortcut, drag it to Desktop while holding Cmd+Option.

cd "$(dirname "$0")" || {
    echo "Couldn't cd into the backend directory. Aborting."
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
}

echo "── GymFlow backend ─────────────────────────────────────"
echo "Working directory: $(pwd)"
echo ""
echo "Git status:"
git status --short
echo ""

# Detect whether there are uncommitted changes.
if [ -n "$(git status --porcelain)" ]; then
    echo "── Uncommitted changes detected. Staging + committing… ──"
    git add -A
    TS=$(date "+%Y-%m-%d %H:%M")
    git commit -m "Auto-bundle from PUSH_LATEST.command — $TS"
    echo ""
fi

# Always try to push; if origin's already up-to-date this is a no-op.
echo "── Pushing to origin… ────────────────────────────────"
git push 2>&1
PUSH_EXIT=$?
echo ""

if [ $PUSH_EXIT -eq 0 ]; then
    echo "✓ Push complete."
    echo ""
    echo "Latest commit:"
    git log --oneline -1
else
    echo "✗ Push failed with exit code $PUSH_EXIT."
    echo "Common causes:"
    echo "  • Auth: macOS Keychain may need to re-auth GitHub. Run a"
    echo "    push from Terminal once, enter creds, then re-try this."
    echo "  • Conflict: another machine pushed since your last pull."
    echo "    Run 'git pull --rebase origin main' from Terminal first."
fi

echo ""
read -n 1 -s -r -p "Press any key to close..."
echo ""
