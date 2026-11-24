#!/bin/sh
cd -- "$(dirname -- "$0")" || exit 1
python3 install.py --wizard
printf '%s\n' 'Press Return to close.'
read -r answer
