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

## 1. Install the fonts in the Gotenberg container

`fonts-noto-core` covers Tamil, Devanagari (hi), Kannada (kn), Oriya (or), Telugu, etc.

### Permanent — bake into a custom image (recommended)

Create the image once:

```bash
mkdir -p ~/gotenberg-custom
cat > ~/gotenberg-custom/Dockerfile <<'EOF'
FROM gotenberg/gotenberg:8
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-core \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*
USER gotenberg
EOF

sudo docker build -t gotenberg-noto:8 ~/gotenberg-custom
```

Point the deployment at it. In `docker-compose.yml`, the gotenberg service:

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

### Quick / temporary (for immediate testing only)

Installs into the running container. **Lost when the container is recreated**
(`docker-compose down`, `--force-recreate`, or an image pull) — do NOT rely on this
in production:

```bash
sudo docker exec -u root saathi_gotenberg sh -c \
  "apt-get update && apt-get install -y --no-install-recommends fonts-noto-core && fc-cache -f"
sudo docker restart saathi_gotenberg
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
sudo docker exec saathi_gotenberg fc-list | grep -iE "tamil|devanagari|kannada|oriya"
```

End-to-end: trigger a non-English PDF download and confirm the labels render.

To confirm at the font level which font a rendered PDF actually used (tofu = only
Liberation embedded; working = Noto embedded):

```bash
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