#!/bin/bash
set -e

IMAGE_NAME="imgtour"
CONTAINER_NAME="imgtour"
PORT="${PORT:-8000}"
IMAGE_DIR="${IMAGE_DIR:-./test_images}"

cleanup() {
  docker stop "$CONTAINER_NAME" 2>/dev/null || true
  docker rm "$CONTAINER_NAME" 2>/dev/null || true
}

cleanup

mkdir -p "$(pwd)/export"

docker build -t "$IMAGE_NAME" .

docker run -d \
  --name "$CONTAINER_NAME" \
  -p "${PORT}:8000" \
  -v "$IMAGE_DIR:/images:ro" \
  -v "$(pwd)/data:/data" \
  -v "$(pwd)/export:/export:rw" \
  -e IMAGE_FOLDERS=/images \
  -e EXPORT_FOLDER=/export \
  -e RESET=1 \
  --restart unless-stopped \
  "$IMAGE_NAME"

echo "imgtour running at http://localhost:${PORT}"
echo "Serving images from: $IMAGE_DIR"
echo "Data stored at: $(pwd)/data"
echo "Exports at: $(pwd)/export"
echo ""
echo "Logs: docker logs $CONTAINER_NAME"
echo "Stop:  docker stop $CONTAINER_NAME"
