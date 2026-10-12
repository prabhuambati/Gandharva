#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
printf '\nGandharva first-admin setup\n\n'
python3 run.py setup-code
printf '\nOpen http://127.0.0.1:8765/setup in Strawberry.\nEnter the code above, then choose your own admin username and password.\nThe code expires in 15 minutes. Do not paste passwords into chat.\n\n'
read -r -p "Press Enter to close this window..." unused
