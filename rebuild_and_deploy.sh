#!/bin/bash
set -e
cd /mnt/usb-storage/Work/PortfolioPi
echo "=========================================="
echo "Starting rebuild and deploy at $(date -u)"
echo "=========================================="

echo "[1/2] Building fresh Docker images (no-cache)..."
docker compose build --no-cache

echo "[2/2] Deploying containers..."
docker compose up -d --remove-orphans

echo "=========================================="
echo "Deployment completed successfully at $(date -u)"
echo "=========================================="
