# Gotenberg Server Setup (PDF fonts for Indic languages)

## Overview

Chatbot document PDFs (e.g. the MIP "Improvement Plan") are rendered by a separate
**Gotenberg** service (headless Chromium). The Django app POSTs HTML to it via
`GOTENBERG_URL` (`.../forms/chromium/convert/html`). The HTML template and its
localized labels live in the **database** (`PDFTemplates`, `template_name='MIP'`),
not in the repo.

**Symptom this doc prevents:** non-English labels (Tamil / Hindi / Kannada / Odia)
render as tofu boxes (□) in the PDF while English renders fine. DOCX is unaffected.

## The rule: two things are BOTH required

Non-Latin text renders **only if both** of these are true. Fixing just one is not enough.

1. **The font is installed inside the Gotenberg container** (where Chromium runs — not
   on the Django/Celery host).
2. **The DB template's CSS `font-family` explicitly names that font.** This Gotenberg's
   Chromium does *not* auto-fallback to installed fonts; it only uses a font the CSS
   names. English works either way because the base fonts (Arial/Liberation, Georgia)
   are always present.

Fallback is per-character: Latin stays on Arial/Georgia; only Indic characters fall
through to the Noto font named later in the stack.

---

## 0. Find the running Gotenberg container name

The container name varies per server — it may be a fixed name from `docker-compose.yml`
(e.g. `saathi_gotenberg`), or an auto-generated Docker name (e.g. `jolly_brattain`) if
Gotenberg was started with a plain `docker run` without `--name`. `-a` includes
stopped containers, so you can also spot stale leftovers from earlier testing —
ignore anything `Created` (never started) or `Exited` long ago; only look at rows
marked `Up`:

```bash
sudo docker ps -a --format "{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}" | grep -i gotenberg
```

Auto-capture the running one into a variable — every command below uses `$GC`, so
there's nothing to manually type or mis-paste:

```bash
export GC=$(sudo docker ps --filter status=running --format "{{.Names}}" | grep -i gotenberg | head -1)
echo "Gotenberg container: $GC"
```

(If that `grep` matches more than one running container, inspect the list above and
set `GC` to the correct name yourself.)

Confirm it's running the font-baked image:

```bash
sudo docker inspect "$GC" --format '{{.Config.Image}}'   # expect gotenberg-noto:8
```

## 1. Install the fonts in the Gotenberg container

`fonts-noto-core` covers Tamil, Devanagari (hi), Kannada (kn), Oriya (or), Telugu, etc.

### Permanent — bake into a custom image (recommended)

Create the image once:

```bash
mkdir -p ~/gotenberg-custom
cat > ~/gotenberg-custom/Dockerfile <<'EOF'
FROM gotenberg/gotenberg:8
USER root
RUN rm -f /etc/apt/sources.list.d/*chrome* \
    && apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-core \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*
USER gotenberg
EOF

sudo docker build -t gotenberg-noto:8 ~/gotenberg-custom
```

> **Note:** the `rm -f /etc/apt/sources.list.d/*chrome*` drops the base image's
> Google Chrome apt repo before updating. That repo's signing key can be
> expired/rotated, which makes `apt-get update` fail outright (`NO_PUBKEY`,
> `not signed`) before the font packages are ever reached. It's safe to remove —
> Chromium is already installed as a binary in the base image; nothing re-installs
> it via apt.

Point the deployment at it. **Check first whether Gotenberg is managed by
docker-compose or a plain `docker run`** — the earlier `docker ps -a` output tells
you: an auto-generated name (e.g. `jolly_brattain`) means plain `docker run`, no
`--name` given; a fixed/meaningful name (e.g. `saathi_gotenberg`) usually means
compose or a scripted `docker run --name ...`.

**Case A — docker-compose manages it:**

Find the `docker-compose.yml` actually driving the container (path varies by server):

```bash
find / -maxdepth 4 -iname "docker-compose*.y*ml" 2>/dev/null
ls ~/saathi-backend/docker-compose*.y*ml   # usually here
```

Confirm it's the right file — its `gotenberg:` block should match the running
container's image and port mapping (edit the `COMPOSE_FILE` value first):

```bash
COMPOSE_FILE=~/saathi-backend/docker-compose.yml
grep -n -A10 "gotenberg:" "$COMPOSE_FILE"
```

Edit the gotenberg service in that file:

```yaml
gotenberg:
  image: gotenberg-noto:8        # was gotenberg/gotenberg:8
  restart: unless-stopped        # auto-restarts on host/daemon reboot
  ports:
    - "3006:3000"                # keep your existing mapping
```

Then recreate the container so it runs the new image:

```bash
cd ~/saathi-backend && sudo docker compose up -d gotenberg
```

> If the stack is auto-started by a boot script (e.g. a tmux session on EC2 boot),
> that script's `docker-compose up` recreates Gotenberg from the new image on the
> next reboot — no manual Compose call needed once the image is built and the
> `image:` line is edited.

**Case B — plain `docker run` (no compose file), e.g. started inside a tmux session:**

There's no config file to edit — you recreate the container directly. Capture its
current host port automatically (don't hand-type it), then recreate with an
explicit `--name` so future lookups don't depend on Docker's random name generator:

```bash
export PORT=$(sudo docker inspect "$GC" --format '{{(index (index .HostConfig.PortBindings "3000/tcp") 0).HostPort}}')
echo "Gotenberg host port: $PORT"

sudo docker rm -f "$GC"
sudo docker run -d --name gotenberg --restart unless-stopped -p "$PORT:3000" gotenberg-noto:8
export GC=gotenberg
```

Then find whatever started the *old* container (a tmux session, `~/.bash_history`
entry, cron `@reboot`, or systemd unit) and update it to launch `gotenberg-noto:8`
with `--name gotenberg` too — otherwise the next reboot may bring the stock,
font-less image back up alongside/instead of this one:

```bash
grep -rn "gotenberg/gotenberg\|docker run" ~/.bash_history /etc/systemd/system /etc/rc.local 2>/dev/null
crontab -l 2>/dev/null | grep -i gotenberg
```

### Quick / temporary (for immediate testing only)

Installs into the running container. **Lost when the container is recreated**
(`docker-compose down`, `--force-recreate`, or an image pull) — do NOT rely on this
in production:

```bash
sudo docker exec -u root "$GC" sh -c \
  "apt-get update && apt-get install -y --no-install-recommends fonts-noto-core && fc-cache -f"
sudo docker restart "$GC"
```

---

## 2. Name the fonts in the DB template CSS

The `PDFTemplates.template` (name=`MIP`) has font stacks like `"Arial", sans-serif`.
Add the Noto fonts after the Latin font so Indic characters resolve. One example:

```css
/* before */
.section-label { font-family: "Arial", sans-serif; }

/* after */
.section-label {
  font-family: "Arial", "Noto Sans Tamil", "Noto Sans Devanagari",
               "Noto Sans Kannada", "Noto Sans Oriya", sans-serif;
}
```

Apply the same pattern to every font stack in the template (sans stacks get the
`Noto Sans *` family; serif stacks get `Noto Serif *`, using `Noto Sans Oriya` for
Oriya since there is no serif variant). To add another language later (e.g. Telugu),
append `"Noto Sans Telugu"` / `"Noto Serif Telugu"` to the stacks.

Applying the edit from the server:

```bash
cd ~/saathi-backend && python manage.py shell
```
```python
from chatbot.models.company_models import PDFTemplates
t = PDFTemplates.objects.get(template_name='MIP')
t.template = (t.template
  .replace('"Arial", sans-serif',
           '"Arial", "Noto Sans Tamil", "Noto Sans Devanagari", "Noto Sans Kannada", "Noto Sans Oriya", sans-serif')
  .replace('"Georgia", "Times New Roman", serif',
           '"Georgia", "Times New Roman", "Noto Serif Tamil", "Noto Serif Devanagari", "Noto Serif Kannada", "Noto Sans Oriya", serif'))
t.save(update_fields=['template'])
print('patched:', 'Noto Sans Tamil' in t.template)
```

---

## 3. Verify

```bash
# fonts present in the container?
sudo docker exec "$GC" fc-list | grep -iE "tamil|devanagari|kannada|oriya"
```

End-to-end: trigger a non-English PDF download and confirm the labels render.

To confirm at the font level which font a rendered PDF actually used (tofu = only
Liberation embedded; working = Noto embedded). `GOTENBERG_URL` lives in the app's
`.env`, not the shell, so pull it from there:

```bash
export GOTENBERG_URL=$(grep -m1 '^GOTENBERG_URL=' ~/saathi-backend/.env | cut -d= -f2- | tr -d '"')
curl -s -o out.pdf -F 'files=@test.html;filename=index.html' "$GOTENBERG_URL"
strings out.pdf | grep -oiE "(Liberation|Noto)[A-Za-z]*" | sort -u
```

---

## Notes

- Fonts go in the **Gotenberg** container, never the Django/Celery host — Chromium
  runs inside Gotenberg.
- Both steps 1 and 2 are per-environment. Repeat on every server (dev/stage/prod).
- The label text itself is clean Unicode; this is purely a font-availability +
  font-naming issue, which is why DOCX (rendered by python-docx, not Chromium) is
  never affected.