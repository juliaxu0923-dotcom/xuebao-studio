#!/bin/sh
set -eu

container=xuebao-studio
repository=juliaxu0923-dotcom/xuebao-studio
image="ghcr.io/$repository:latest"
image_source="https://github.com/$repository"

wait_for_health() {
  printf 'Waiting for the new Studio container health check.\n'
  checks=0
  while [ "$checks" -lt 60 ]; do
    state=$(docker container inspect --format \
      '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$1") || return 1
    case "$state" in
      'running healthy') return 0 ;;
      'running starting'|'restarting '*) ;;
      *)
        printf 'The new container is not healthy (%s).\n' "$state" >&2
        return 1
        ;;
    esac
    checks=$((checks + 1))
    sleep 3
  done
  printf 'The new container did not become healthy in time.\n' >&2
  return 1
}

remove_old_images() {
  if ! docker image prune --force --filter "label=org.opencontainers.image.source=$image_source"; then
    printf 'Could not clean up dangling Studio images.\n' >&2
  fi

  # The immediately previous image may predate the registry's source label.
  if tags=$(docker image inspect --format '{{len .RepoTags}}' "$old_image_id" 2>/dev/null) &&
     [ "$tags" = 0 ] &&
     users=$(docker container ls --all --quiet --filter "ancestor=$old_image_id") &&
     [ -z "$users" ]; then
    if ! docker image rm --no-prune "$old_image_id"; then
      printf 'Could not remove the previous Studio image.\n' >&2
    fi
  fi
}

old_id=$(docker container inspect --format '{{.Id}}' "$container")
data_source=$(docker container inspect --format \
  '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$old_id")
if [ -z "$data_source" ]; then
  printf 'The existing container has no /data mount. Update stopped.\n' >&2
  exit 1
fi

old_image_id=$(docker container inspect --format '{{.Image}}' "$old_id")
docker pull "$image"
docker container stop --time 45 "$old_id"
old_short_id=$(printf '%.12s' "$old_id")
if ! docker container rename "$old_id" "${container}-previous-${old_short_id}"; then
  docker container start "$old_id"
  exit 1
fi

new_id=
if new_id=$(docker container create \
  --name "$container" \
  --restart unless-stopped \
  --init \
  --shm-size=512m \
  --publish 127.0.0.1:8000:8000 \
  --volumes-from "$old_id" \
  "$image") && docker container start "$new_id" && wait_for_health "$new_id"; then
  docker container rm "$old_id"
  remove_old_images
  docker container ls --filter "name=$container"
  exit 0
fi

if [ -n "$new_id" ]; then
  docker container stop --time 45 "$new_id" || true
  docker container rm "$new_id"
fi
docker container rename "$old_id" "$container"
docker container start "$old_id"
printf 'The new container failed to start or pass its health check. The previous container was restarted.\n' >&2
exit 1
