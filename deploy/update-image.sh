#!/usr/bin/env bash
set -euo pipefail

container=xuebao-studio
image=ghcr.io/juliaxu0923-dotcom/xuebao-studio:latest

old_id=$(docker container inspect --format '{{.Id}}' "$container")
data_source=$(docker container inspect --format \
  '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$old_id")
if [[ -z "$data_source" ]]; then
  printf 'The existing container has no /data mount. Update stopped.\n' >&2
  exit 1
fi

docker pull "$image"
docker container stop --time 45 "$old_id"
docker container rename "$old_id" "${container}-previous-${old_id:0:12}"

if new_id=$(docker container create \
  --name "$container" \
  --restart unless-stopped \
  --init \
  --shm-size=512m \
  --publish 127.0.0.1:8000:8000 \
  --volumes-from "$old_id" \
  "$image") && docker container start "$new_id"; then
  docker container rm "$old_id"
  docker container ls --filter "name=$container"
  exit 0
fi

if [[ -n "$new_id" ]]; then
  docker container rm "$new_id"
fi
docker container rename "$old_id" "$container"
docker container start "$old_id"
printf 'The new container could not start. The previous container was restarted.\n' >&2
exit 1
