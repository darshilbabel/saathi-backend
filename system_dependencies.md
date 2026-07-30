# System Dependencies

This project uses media processing libraries that rely on **system-level binaries**
(not installable via `requirements.txt`). These dependencies are required for
PDF previews, video/audio processing, and optional high-fidelity document rendering.
They must be installed on **all machines running Django or Celery workers**.

## Installation (Ubuntu / Debian)

```bash
# PDF preview (required)
sudo apt update
sudo apt install -y poppler-utils

# Video & audio processing (required)
sudo apt install -y ffmpeg

# Verification
pdfinfo -h
ffmpeg -version
```

## Gotenberg — PDF rendering fonts (required for non-English PDFs)

The chatbot's document PDFs (e.g. the MIP "Improvement Plan") are rendered by a
**separate Gotenberg service** (headless Chromium), reached via `GOTENBERG_URL`
(`.../forms/chromium/convert/html`). The HTML template + localized labels live in
the DB (`PDFTemplates`, `template_name='MIP'`), not in the repo.

**Non-Latin labels (Tamil/Hindi/Kannada/Odia) render as tofu boxes (□) unless BOTH
of the following are done.** English-only PDFs work without either.

### 1. Install Indic fonts INSIDE the Gotenberg container (not the Django/Celery host)

Chromium runs inside Gotenberg, so fonts must exist there. `fonts-noto-core`
covers Tamil, Devanagari (hi), Kannada (kn), and Oriya (or).

**Recommended — bake into the Gotenberg image** (`Dockerfile.gotenberg`):

```dockerfile
FROM gotenberg/gotenberg:8
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-core \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*
USER gotenberg
```

**Quick / one-off** (installs into a running container; redo if the container is `rm`ed):

```bash
docker exec -u root gotenberg sh -c \
  "apt-get update && apt-get install -y --no-install-recommends fonts-noto-core && fc-cache -f"
docker restart gotenberg   # so Chromium re-enumerates the fonts
```

### 2. The DB `MIP` template MUST name the fonts in its CSS font-family stacks

Gotenberg's Chromium does **not** auto-fallback to installed fonts — it only uses a
font the CSS explicitly names. Installing the font (step 1) is necessary but NOT
sufficient. The `PDFTemplates.template` (name=`MIP`) font stacks must be:

- sans: `"Arial", "Noto Sans Tamil", "Noto Sans Devanagari", "Noto Sans Kannada", "Noto Sans Oriya", sans-serif`
- serif: `"Georgia", "Times New Roman", "Noto Serif Tamil", "Noto Serif Devanagari", "Noto Serif Kannada", "Noto Sans Oriya", serif`

Latin still renders in Arial/Georgia; only Indic characters fall through to Noto.

### Verification

```bash
# fonts present in the Gotenberg container?
docker exec gotenberg fc-list | grep -iE "tamil|devanagari|kannada|oriya"

# does a rendered PDF actually embed Noto (not just Liberation)?  tofu = only Liberation.
curl -s -o out.pdf -F 'files=@test.html;filename=index.html' "$GOTENBERG_URL"
strings out.pdf | grep -oiE "(Liberation|Noto)[A-Za-z]*" | sort -u
```
