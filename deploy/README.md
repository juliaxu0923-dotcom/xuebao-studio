# Linux Server Deployment

The root Docker Compose file runs Studio, the canvas renderer and the Jimeng
gateway in one container. Only Studio is published on the host loopback address;
nginx exposes it on port 80. The renderer and account gateway are private.

## Install and Start

Install Docker Engine, the Docker Compose plugin and nginx for your Linux
distribution. Allow roughly 10 GB of free disk space for the first image build
and build cache, plus storage for uploaded/generated media. Check with `df -h`
and `docker system df` before building. Transfer this repository to a dedicated
directory, then run:

```sh
cp .env.example .env
docker compose up -d --build
docker compose ps
docker compose logs --tail=100
```

The image installs Linux Node dependencies matching the archived canvas runtime,
Python dependencies, FFmpeg and Playwright's Chromium headless shell. Runtime
files are restored and checksum-verified during the build.

The default administrator is `admin`. On a fresh installation, retrieve the
random initial password privately:

```sh
docker compose exec studio cat /data/admin-initial-password.txt
```

Existing users and passwords are kept if the data volume is reused. Setting
`ADMIN_PASSWORD` only configures the administrator on first initialization.
API keys and account sessions are configured in the Studio admin page after login.

QR login waits for the login control and a loaded QR image, instead of requiring
the entire Jimeng home page to finish loading. If the server cannot reach Jimeng
or its Douyin/Feishu login page, configure `JIMENG2API_HTTP_PROXY` in `.env` and
recreate the container. The QR browser and generation API share this proxy.
Use a proxy reachable from inside the container; `127.0.0.1` refers to the
container itself. A blank QR area is never treated as a ready login code.

## IP Access

`deploy/nginx.conf` is an HTTP virtual host template. Set `server_name` to the
server IP before enabling it. Inspect existing nginx sites and back up affected
configuration before changing them; do not overwrite unrelated sites.

The template proxies to `127.0.0.1:8000`. If the host already uses port 8000,
change `STUDIO_PORT` in `.env` and the template's `proxy_pass` together. Validate
with `nginx -t` before reloading nginx. Allow inbound TCP 80 in the cloud firewall
and host firewall; ports 8000, 8001 and 8300 do not need public access.

Use `COOKIE_SECURE=0` for HTTP IP access. After adding a domain and a valid HTTPS
certificate, set `COOKIE_SECURE=1` and restart with `docker compose up -d`.

## Storage and Updates

The `studio-data` named volume holds both databases, uploaded/generated files and
encryption keys. Back up the complete volume together. Do not upload it to GitHub.
For a consistent filesystem backup, stop this Compose project's container first,
back up its volume, then start it again. Do not use `docker compose down -v`
unless you explicitly intend to delete all application data.

To update code after backing up data:

```sh
git pull --ff-only
docker compose up -d --build
docker compose ps
```

Supervisor restarts crashed child services. Docker restarts the container after
a host reboot. The health check requires Studio, the renderer and gateway to
respond. If the container stays unhealthy, inspect its logs before resubmitting
generation requests.

## Verification

Check the login page from an external browser, log in, create a canvas, upload a
reference, save and reload it, and open the account pool page. Creating an actual
image or video also requires a configured API or valid account; a successful
deployment health check does not prove that an upstream account can generate.
