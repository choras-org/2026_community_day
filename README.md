# CHORAS Community Day workshop

Ansible playbooks that run one [CHORAS](https://github.com/choras-org/CHORAS) instance
per participant on a single machine, behind Nginx with a password login. Built for a
SURF Research Cloud (SRC) workspace; the same playbooks run on a plain Ubuntu machine.

How it works:

- One shared static frontend, built with `VITE_API_URL=/api`, served by Nginx.
- Per participant a compose project with Postgres and a backend, published only on
  `127.0.0.1:<port>`. Simulation images are built once and shared.
- Nginx (basic auth) maps each login to its own backend: `/api/` goes to
  `127.0.0.1:<port>` of the logged-in user, other logins get a 403.
- Requests are rate-limited per login name (10/s, bursts of 100) against password
  guessing, and uploads are capped at 500 MB.

## Layout

| Path | What it is |
|---|---|
| `playbooks/choras_workshop.yml` | The SRC component: build, instances, Nginx config |
| `playbooks/local_prepare.yml` | Only outside SRC: Docker, git and an Nginx stand-in for SRC-Nginx |
| `playbooks/group_vars/all.yml` | Defaults for all parameters |
| `playbooks/roles/choras_build` | Clones CHORAS at `choras_version`, builds the images, copies the frontend build to `/var/www/choras` |
| `playbooks/roles/choras_instances` | Per participant: secrets, compose file, start |
| `playbooks/roles/choras_nginx` | Passwords, htpasswd, username → port map, rate limit, CHORAS locations |
| `playbooks/roles/nginx_standin` | HTTPS server block like SRC-Nginx's, with a self-signed certificate |
| `playbooks/check_templates.yml` | Check that the map and the compose files agree on the ports |
| `local-test/` | The first manual end-to-end test on a Mac (2026-11-19), without Ansible |

## Parameters

On SRC these are component parameters (strings, no quotes; lists comma-separated).
Defaults are in `playbooks/group_vars/all.yml`.

| Parameter | Default | Meaning |
|---|---|---|
| `choras_version` | `218636b…` (CHORAS `dev`, tested 2026-11-19) | CHORAS commit or tag |
| `choras_participants` | `user01,user02` | Logins, one instance each, in this order (ports 8101, 8102, …) |
| `choras_methods` | `de-method` | Simulation images to build (services of CHORAS's `docker-compose.yml`) |
| `choras_sim_cpus` / `choras_sim_mem_limit` | `2` / `4g` | Limits per simulation container |
| `choras_backend_cpus` / `choras_backend_mem_limit` | `2` / `4g` | Limits per backend container (meshing, geometry repair) |
| `choras_web_concurrency` | `2` | Gunicorn workers per backend |
| `choras_start_pause` | `30` | Seconds between starting two instances |
| `choras_public_url` | `https://<fqdn>` | Public URL; the backends get `<url>/api` as `PUBLIC_API_URL` |

## On SRC

Catalog item order: SRC-OS, SRC-CO, SRC-Nginx, SRC-External, Docker, then this
repository's `playbooks/choras_workshop.yml` as an Ansible component. The playbook
stops early if `/etc/nginx/app-location-conf.d` (SRC-Nginx) or Docker is missing.

After the workspace is up, the logins are in `/root/choras_credentials.txt` (one
`user password` line per participant), readable only by root. The workshop URL is
the workspace's: `https://<hostname>.<collaboration>.src.surf-hosted.nl`.

## On a plain Ubuntu machine

```sh
sudo apt install ansible-core
git clone <this repository> && cd choras_community_day_workshop
sudo ansible-playbook playbooks/local_prepare.yml
sudo ansible-playbook playbooks/choras_workshop.yml -e choras_participants=user01,user02
```

## Re-running

The playbook can run again on the same machine: passwords and instance secrets are
kept, images rebuild from cache, and only changed instances restart. Adding a
participant at the end of `choras_participants` adds an instance; removing or
reordering participants changes ports and isn't handled.

## Check

```sh
ansible-playbook playbooks/check_templates.yml
```
