#!/bin/bash
set -a
source /home/marc/music-generator/.env
set +a
exec python3 /home/marc/music-generator/server.py "$@"