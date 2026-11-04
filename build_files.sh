#!/bin/bash
# SONORA Vercel Deployment Build Script
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 manage.py collectstatic --no-input --clear

