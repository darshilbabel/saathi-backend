# Shikshalokam Mohini Service – Local Setup
---

## Prerequisites

* macOS
* Homebrew installed
* Python 3.10
* Git

---

## 1. Install Python 3.10

```bash
brew install python@3.10
```

Verify installation:

```bash
python3.10 --version
```

---

## 2. Install uv and Set Up Virtual Environment

### Step 1: Install uv

Install uv using the official installer. Follow the instructions at:
https://docs.astral.sh/uv/getting-started/installation/

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart your shell or run `source $HOME/.local/bin/env` to make `uv` available.

### Step 2: Go to the project directory

```bash
cd /path/to/saathi-backend
```

### Step 3: Create the virtual environment and install dependencies

`uv sync` will automatically create a `.venv` directory and install all dependencies:

```bash
uv sync
```

> After this, refer to [system_dependencies.md](system_dependencies.md) and follow the steps there to install required system-level binaries (poppler, ffmpeg, etc.).

### Step 4: Activate the virtual environment

```bash
source .venv/bin/activate
```

---

## 3. Install Project Dependencies

Dependencies are installed automatically when you run `uv sync` in Step 2. Re-run it any time `pyproject.toml` or `uv.lock` changes.

---

## 4. Load Environment Variables and Secrets

Make sure you have a `.env` file in the project root, and a `secrets.json` file inside the `config/` folder at the project root (i.e. `config/secrets.json`). Both files contain sensitive credentials and are not committed to version control — get them from the team.

Create the `config/` folder and an empty `secrets.json` if they don't exist yet:

```bash
mkdir -p config && touch config/secrets.json
```

Export env vars for the current shell session:

```bash
export $(cat .env | xargs)
```

> ⚠️ Note: This exports variables only for the current shell session.

---

## 4a. Create Logs Directory

Create a `logs/` folder in the project root with the required log files:

```bash
mkdir -p logs && touch logs/info.log logs/error.log logs/debug.log
```

---

## 5. Set Up Local PostgreSQL Database

### 5.1 Install PostgreSQL

Using Homebrew:

```bash
brew install postgresql@17
```

Start PostgreSQL:

```bash
brew services start postgresql@17
```

Verify it’s running:

```bash
psql --version
```

---

### 5.2 Create Database, User, and Schema

Login to Postgres:

```bash
psql -d postgres
```

Create a database user:

```sql
CREATE USER mitra_user WITH PASSWORD ‘mitra_password’;
```

Create the database:

```sql
CREATE DATABASE saathi OWNER mitra_user;
```

Grant privileges:

```sql
GRANT ALL PRIVILEGES ON DATABASE saathi TO mitra_user;
```

Connect to the new database:

```sql
\c saathi
```

Create the `shikshalokam` schema:

```sql
CREATE SCHEMA shikshalokam;
GRANT ALL ON SCHEMA shikshalokam TO mitra_user;
```

Exit psql:

```sql
\q
```

---

### 5.3 Update `.env` File

Add or update the following variables in your `.env` file:

```env
DATABASE_NAME=saathi
DATABASE_USER=mitra_user
DATABASE_PASSWORD=mitra_password
DATABASE_HOST=localhost
DATABASE_PORT=5432
```

---

### 5.4 Run Django Migrations and Seed Data

Ensure your virtual environment is active and env vars are loaded:

```bash
export $(cat .env | xargs)
```

Run the `prepare_db` command — this handles everything in one shot: creates the `shikshalokam` PostgreSQL schema, runs all migrations, and seeds the initial Company ("Shikshalokam" / slug `shikshalokamstaging`) and AI profile (`ai@shikshalokam.org`):

```bash
python3 manage.py prepare_db
```

(Optional) Create a superuser:

```bash
python3 manage.py createsuperuser
```

When prompted:
1. **Username**: enter `admin`
2. **Email**: press Enter to skip
3. **Password**: enter any password
4. **Password (again)**: re-enter the same password
5. If asked to bypass password validation, enter `y` and press Enter

---

## Common Issues

**Postgres not starting**

```bash
brew services restart postgresql@17
```

**Role does not exist**

```bash
psql postgres
\du
```

**Port conflict**

```bash
lsof -i :5432
```


## 6. Run the Application Server

```bash
uvicorn shikshalokam_mohini.asgi:application \
  --host 0.0.0.0 \
  --port 9000 \
  --workers 4 \
  --ws-ping-interval 30 \
  --ws-ping-timeout 300 \
  --reload
```

---

## 7. Run Celery Worker

Open a new terminal (with the same virtual environment activated):

```bash
celery -A shikshalokam_mohini worker --pool=threads
```

---

## Notes

* Ensure Redis or any other required backing services are running before starting Celery.
* Always activate the virtual environment before running server or worker commands.

---

## 8. Set Up Redis (Local, IF celery gives error)

Redis is required for Celery and background task processing.

---

### 8.1 Install Redis

Using Homebrew:

```bash
brew install redis
```

---

### 8.2 Start Redis Server

Start Redis as a background service:

```bash
brew services start redis
```
---

### 8.3 Verify Redis Is Running

```bash
redis-cli ping
```

Expected output:

```text
PONG
```

---

## Common Redis Issues

**Redis not running**

```bash
brew services restart redis
```

**Port already in use**

```bash
lsof -i :6379
```

---

## 9. Git Workflow — Working on the Right Branch

Before writing any code, make sure you are branching off the correct base branch.

### Step 1: Check your remote

```bash
git remote -v
```

This shows what remote repositories you are pointing to. If you do not see the Elevate GitHub repository listed, add it as a remote named `elevate`:

```bash
git remote add elevate https://github.com/<org>/<repo>.git
```

Replace `<org>/<repo>` with the actual repository path. Then verify it was added:

```bash
git remote -v
```

### Step 2: Fetch all remote branches

```bash
git fetch --all
```

This updates your local knowledge of all remote branches without changing your working directory.

### Step 3: List remote branches to find the latest official branch

```bash
git branch -r
```

Look for the latest release branch (e.g. `origin/release-1.0.0`) or whatever branch the team is currently working from. Confirm with your team if unsure.

### Step 4: Create your local branch from the official branch

It is better to branch off the upstream remote (e.g. `elevate`) rather than `origin`, so your base is always the canonical source of truth. Check `git remote -v` to confirm which remote name points to the upstream repo.

```bash
git checkout -b your-feature-branch elevate/release-1.0.0
```

If your upstream remote is named differently (e.g. `origin`), replace `elevate` with that name:

```bash
git checkout -b your-feature-branch origin/release-1.0.0
```

Replace `release-1.0.0` with the actual latest branch name if it differs. Confirm with your team which branch is currently active.
