# Managing the Docker Server over SSH

This guide lets other PCs on your LAN manage containers on the home server using
their own local `docker` CLI, tunneled over SSH. Nothing new is exposed on the
network — you reuse SSH, which is already encrypted and key-authenticated.

> ⚠️ **Docker access is root-equivalent.** Anyone who can run `docker` on the
> server can take over the whole machine. Only grant this to people you trust
> with root. See [Security notes](#security-notes).

---

## Overview

```
┌──────────────┐         SSH (port 22)        ┌────────────────────┐
│  Client PC   │  docker context: homeserver  │   Home Server      │
│  docker CLI  │ ───────────────────────────► │   Docker daemon    │
└──────────────┘                              └────────────────────┘
```

Each client keeps its own `docker` CLI but points it at the server with a
**Docker context**. Commands like `docker ps`, `docker run`, and
`docker compose up` then execute on the server.

---

## Part 1 — Server setup (do once)

Run these on the **home server**.

### 1. Confirm SSH is running

```bash
sudo systemctl status ssh
```

If it's not installed:

```bash
sudo apt update
sudo apt install -y openssh-server
sudo systemctl enable --now ssh
```

Find the server's LAN IP (note it for later):

```bash
ip -4 addr show | grep inet
```

### 2. Create an account for each user

```bash
sudo adduser alice
```

Repeat for each person who needs access.

### 3. Add each user to the `docker` group

This lets them use Docker without `sudo`.

```bash
sudo usermod -aG docker alice
```

Group membership takes effect on their **next login**.

### 4. Verify Docker works for that user

Have the user log in and run:

```bash
docker ps
```

If it prints a (possibly empty) container table with no permission error, the
group is applied correctly.

---

## Part 2 — SSH key setup (per user)

Password SSH works, but keys are more secure and avoid retyping passwords.

### On the client PC

Generate a key if the user doesn't already have one:

```bash
ssh-keygen -t ed25519 -C "alice@client-pc"
```

Copy the public key to the server:

```bash
# Linux/macOS/Git Bash
ssh-copy-id alice@<SERVER-IP>
```

On Windows PowerShell (no `ssh-copy-id`):

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh alice@<SERVER-IP> "mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys"
```

Test the login:

```bash
ssh alice@<SERVER-IP>
```

You should get a shell without a password prompt.

---

## Part 3 — Create the Docker context (per client PC)

On each **client PC**, create a context pointing at the server:

```bash
docker context create homeserver --docker "host=ssh://alice@<SERVER-IP>"
```

Switch to it:

```bash
docker context use homeserver
```

Verify it's talking to the server:

```bash
docker info
docker ps
```

`docker info` should show the server's hostname and its running containers.

### Switching back and forth

```bash
docker context ls              # list contexts (* marks the active one)
docker context use default     # back to the local Docker
docker context use homeserver  # back to the server
```

You can also target the server for a single command without switching:

```bash
docker --context homeserver ps
```

---

## Part 4 — Everyday use

Once the context is active, all normal Docker commands run on the server:

```bash
docker run -d --name whoami -p 8080:8080 traefik/whoami
docker ps
docker logs whoami
docker stop whoami && docker rm whoami
```

### Using Compose

`docker compose` respects the active context too. Put a `compose.yaml` on your
client PC and bring it up on the server:

```bash
docker context use homeserver
docker compose up -d
```

> **Bind mounts caveat:** paths in `volumes:` and `-v` resolve on the **server**,
> not your client PC. `-v ./data:/data` refers to a directory on the server. To
> ship files from your machine, bake them into the image or use named volumes.

---

## Part 5 — Faster connections with `~/.ssh/config` (optional)

Every Docker command over SSH opens a new connection. With **connection
multiplexing**, the first connection stays open and later commands reuse it —
noticeably snappier, and you can use a short alias instead of `user@ip`.

On the **client PC**, edit (or create) `~/.ssh/config`:

```
Host homeserver
    HostName 192.168.1.50        # your server's LAN IP
    User alice
    IdentityFile ~/.ssh/id_ed25519

    # Reuse one connection for many commands
    ControlMaster auto
    ControlPath ~/.ssh/cm-%r@%h:%p
    ControlPersist 10m

    # Keep the connection alive
    ServerAliveInterval 30
    ServerAliveCountInterval 3
```

> On Windows, the file lives at `C:\Users\<you>\.ssh\config`.
> The `ControlMaster`/`ControlPath` multiplexing options are **not supported by
> Windows' built-in OpenSSH** — keep the `Host`/`HostName`/`User` alias (that part
> works everywhere) and drop the three `Control*` lines there. Multiplexing works
> from Git Bash, WSL, Linux, and macOS.

With the alias defined, you can now:

```bash
# Plain SSH uses the alias
ssh homeserver

# And so does the Docker context — reference the alias instead of user@ip
docker context create homeserver --docker "host=ssh://homeserver"
docker context use homeserver
```

Create the `ControlPath` directory once if needed:

```bash
mkdir -p ~/.ssh
```

---

## Part 6 — Portainer: a web UI with per-user access control

If some users aren't comfortable on the command line — or you want to **limit who
can touch which containers** — run [Portainer](https://www.portainer.io/). It's a
web dashboard for containers, stacks, images, volumes, and networks, with real
team/role-based access control that plain Docker can't provide.

### Install (run on the server)

```bash
docker volume create portainer_data
docker run -d \
  --name portainer \
  --restart=always \
  -p 9443:9443 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v portainer_data:/data \
  portainer/portainer-ce:latest
```

### First-time setup

1. Browse to `https://<SERVER-IP>:9443` from any PC on the LAN.
2. Accept the self-signed certificate warning (it's your own server).
3. Create the **admin** account (do this promptly — the setup window closes for
   security after a few minutes; if it locks, restart the container to reopen it:
   `docker restart portainer`).
4. Choose **"Get Started"** to manage the local Docker environment.

### Give other users scoped access

1. In Portainer: **Settings → Users → Add user** — create a login for each person.
2. Optionally group them under **Settings → Teams**.
3. Use **environment access control** and **resource-level access** to restrict a
   user or team to specific stacks/containers, or to read-only.

This is the recommended path when you *don't* want everyone to have full
root-equivalent control of the box.

### Notes

- Portainer needs the Docker socket (`/var/run/docker.sock`), so the Portainer
  container itself is effectively root — protect the admin account.
- SSH contexts (Parts 1–5) and Portainer can coexist: CLI users use contexts,
  GUI users use Portainer, both managing the same daemon.
- To update: `docker pull portainer/portainer-ce:latest`, then remove and re-run
  the container — `portainer_data` preserves all users and settings.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `permission denied ... /var/run/docker.sock` | User isn't in the `docker` group, or hasn't logged out/in since being added. |
| `error during connect ... ssh` | SSH login itself fails — test `ssh alice@<SERVER-IP>` first. |
| `docker: 'compose' is not a docker command` (on server) | Server needs the Compose v2 plugin: `sudo apt install docker-compose-plugin`. |
| Context works but is slow | SSH multiplexing helps — add a `ControlMaster` block to `~/.ssh/config`. |
| Wrong host targeted | Check `docker context ls` for the active `*` context. |

---

## Security notes

- **`docker` group = root on the server.** There is no "containers only" mode
  with plain Docker. Only add trusted users.
- Keep access on the **LAN**. Do **not** port-forward SSH to the internet without
  hardening (key-only auth, `fail2ban`, non-default port, etc.).
- Prefer **SSH keys over passwords**, and consider disabling password auth in
  `/etc/ssh/sshd_config` (`PasswordAuthentication no`) once keys work.
- If you need **per-user restrictions** (limit who can touch which containers),
  plain Docker can't do it — run **Portainer** instead and give each person a
  scoped login.
- This server already runs the **River Monitor** stack (port 5743 + its
  PostgreSQL container). Anyone with this access can stop or change it. Use clear
  container/compose-project names to keep personal containers separate from the
  production stack.

---

## Quick reference

```bash
# Server (once, per user)
sudo usermod -aG docker <user>

# Client (once, per PC)
docker context create homeserver --docker "host=ssh://<user>@<SERVER-IP>"
docker context use homeserver

# Daily
docker ps
docker --context homeserver ps    # one-off without switching
docker context use default        # back to local
```
