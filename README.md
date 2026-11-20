# TaskPilot AI

A local system monitoring and diagnostics dashboard for Windows 11. It watches CPU, memory, disk and processes, learns what's normal for your PC with machine learning, reads Windows error logs and emails you through Gmail when something goes wrong. It runs quietly in the system tray and can start automatically when you sign in.

Everything stays on your computer. The API listens only on `127.0.0.1`, and no cloud AI service is called.

## Features

| Area | What it does |
|---|---|
| **Dashboard** | Live CPU, memory and system-drive gauges with your alert thresholds marked, plus network totals, boot time and process count |
| **Processes** | PID, parent, user, status, memory and start time. You can end a process after confirming; critical Windows processes are protected |
| **Performance** | Overall and per-core CPU, and memory usage over time |
| **App History** | Which processes ran and for how long (sampled every 10 seconds) |
| **Tasks** | One-click system checks (health scan, Windows error scan, service scan, AI analysis) with run history, plus personal to-dos |
| **Startup Apps, Users, Services, Applications, Storage** | Inventories of what's installed and running. Services can be started, stopped or restarted (needs Administrator; core services are protected) |
| **AI Assistant** | Trains an Isolation Forest anomaly model on your own telemetry, flags unusual readings, collects Windows Critical/Error events and gives recommendations |
| **Alerts** | Windows errors, ML anomalies (with acknowledge) and sent notifications |
| **Settings** | Gmail alerts, adjustable CPU/memory/disk thresholds, a test email and a daily digest |
| **Theme** | Light, Dark or System (follows Windows), remembered per browser |
| **System tray** | Open the dashboard, restart the backend or quit, with a watchdog that restarts the backend if it stops |

## Tech stack

- **Frontend:** React 18, Vite 5, Recharts, lucide-react
- **Backend:** Python 3.11+, FastAPI, Uvicorn, psutil, SQLite
- **ML:** scikit-learn (Isolation Forest), numpy
- **Alerts:** Gmail SMTP with an App Password stored in Windows Credential Manager (keyring)
- **Desktop:** pystray tray app, packaged as an `.exe` with PyInstaller, auto-started by Task Scheduler

## Project layout

```
backend/            FastAPI app (main.py), requirements, dev runner
frontend/           React + Vite dashboard
tray/               System tray launcher (tray_app.py) and icon generator
install/            One-click .exe build/install script and Task Scheduler template
deploy.ps1 / .cmd   One-command setup without building an .exe
run.ps1             Production runner used by deploy.ps1
taskpilot.spec      PyInstaller build definition
```

## Requirements

Windows 11, Python 3.11+ and Node.js 18+. `deploy.ps1` installs Python and Node.js with winget if they're missing.

## Getting started

Choose **one** of the three ways below.

### Option 1: Quick setup (recommended to start)

Double-click `deploy.cmd`, or in PowerShell from the project folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\deploy.ps1                  # install, build, start and open http://127.0.0.1:8000
.\deploy.ps1 -InstallStartup  # also start automatically when you sign in
.\deploy.ps1 -RemoveStartup   # remove the sign-in task
.\deploy.ps1 -Stop            # stop the backend
```

This creates `backend\.venv`, installs the packages, builds the dashboard and runs one process that serves both the API and the dashboard on port 8000. It's safe to run again. Logs go to `backend\taskpilot.log`.

### Option 2: System tray app from source

```powershell
.\deploy.ps1 -NoBrowser    # first time only: sets up .venv and builds the dashboard
.\deploy.ps1 -Stop         # let the tray manage the backend instead
.\backend\.venv\Scripts\pythonw.exe .\tray\tray_app.py
```

### Option 3: Installed .exe with auto-start

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\deploy.ps1 -Stop
.\install\build_and_install.ps1
```

This builds `TaskPilotAI.exe` with PyInstaller, installs it to `C:\TaskPilotAI`, registers a Task Scheduler task that starts it at sign-in, and launches it.

> **Warning:** reinstalling deletes `C:\TaskPilotAI`, including `taskpilot.db` (your history, tasks and alerts) and the trained model. Back them up first if you want to keep them.

Use either `deploy.ps1 -InstallStartup` **or** the `.exe` installer for auto-start, not both, or two copies will compete for port 8000.

### Development mode (hot reload)

```powershell
# terminal 1
cd backend
.\run.ps1                 # API on http://127.0.0.1:8000 (docs at /docs)

# terminal 2
cd frontend
npm install
npm run dev               # dashboard on http://127.0.0.1:5173
```

## How the tray app works

- At start-up it checks `http://127.0.0.1:8000/api/health`. If a TaskPilot backend is already running (for example from `deploy.ps1`), it uses that one instead of starting a second copy.
- If another program holds port 8000, it shows one notification and doesn't keep retrying.
- A watchdog checks every 10 seconds and restarts the backend only if it's really unreachable. It allows 60 seconds for a slow start.
- In the packaged `.exe`, the backend runs on a thread inside the tray process. From source, it runs as a child Python process.
- Logs: `taskpilot-tray.log` and `backend.log`, next to the `.exe` or in the project folder.

## Machine learning and Windows error logs

TaskPilot records a telemetry sample every 30 seconds (CPU, memory, system-drive usage and process count) in `backend/taskpilot.db`.

1. Leave TaskPilot running until it has at least 30 samples (about 15 minutes).
2. Open **AI Assistant** and select **Train / update ML model**. The model is saved as `backend/taskpilot_anomaly_model.pkl`.
3. Select **Collect Windows error logs** to import Critical, Error and Warning events from the last 24 hours.
4. Review flagged anomalies on **AI Assistant** and **Alerts**, and acknowledge them once checked.

The model learns your normal resource usage. It flags unusual patterns but can't prove that a hardware or software fault caused them.

## Gmail alerts

1. Turn on Google 2-Step Verification, then create an **App Password** (16 characters) in your Google account. Don't use your normal Gmail password.
2. In TaskPilot, open **Settings**, enter the sending address, the recipient and the App Password, then select **Send test email**.
3. Optionally adjust the CPU, memory and disk alert thresholds (defaults: 90%, 90% and 95%).

While the backend runs, it checks resources every minute, Windows event logs about every five minutes, and ML anomalies every 30 seconds once a model is trained. Alerts are HTML emails, throttled so the same alert isn't repeated within an hour (24 hours for the same Windows event). A daily digest is sent at about 08:00.

The sender and recipient are stored in `backend/.env`; the App Password is stored in Windows Credential Manager. Both stay out of Git.

## API

Interactive docs are at `http://127.0.0.1:8000/docs` while the backend is running. Main groups:

- **System:** `/api/health`, `/api/system/overview`, `/api/processes`, `/api/performance/cpu`, `/api/performance/memory`, `/api/startup`, `/api/users`, `/api/services`, `/api/applications`, `/api/apps/history`
- **Controls:** `POST /api/processes/{pid}/terminate`, `POST /api/services/{name}/{start|stop|restart}`
- **Tasks:** `/api/tasks` (CRUD), `POST /api/tasks/run/{health_check|windows_errors|service_scan|ml_analysis}`, `/api/tasks/runs`
- **AI and logs:** `/api/ai/model`, `POST /api/ai/train`, `/api/ai/anomalies`, `POST /api/ai/anomalies/{id}/acknowledge`, `/api/ai/recommendations`, `POST /api/logs/collect`, `/api/logs`
- **Alerts and settings:** `/api/alerts`, `/api/settings/email`, `POST /api/settings/email/test`, `POST /api/settings/email/digest`, `/api/settings/thresholds`
- **Live:** WebSocket telemetry stream

## Safety and privacy

- The API binds to `127.0.0.1` only. Don't expose it to your network.
- Ending processes can lose unsaved work, and service control can disrupt Windows. The UI asks for confirmation and the backend blocks a small list of critical processes and services, but that isn't a complete safety policy.
- Service control needs the backend to run as Administrator. Don't run it elevated for normal monitoring; the sign-in task and tray app run without Administrator rights.
- Collected data (process names, usernames, event logs) can be sensitive. `.gitignore` keeps `.env`, `taskpilot.db`, the model, logs and build output out of the repo; don't commit them.
- TaskPilot only monitors while it's running. It doesn't watch the PC while it's off or after you quit the tray app.

## Changelog

### v6: System tray and .exe
- System tray app with Open Dashboard, Restart Backend and Quit, plus a watchdog
- PyInstaller build, one-click `install/build_and_install.ps1` and a Task Scheduler auto-start template
- The tray reuses a running backend and no longer loops when port 8000 is busy
- The packaged `.exe` runs the backend in-process (the old `-m uvicorn` launch couldn't work from an `.exe`)
- HTML alert emails, a daily digest, adjustable alert thresholds and anomaly acknowledgement
- Light/Dark/System theme, a new gauge-cluster dashboard and a bundled font
- One-command `deploy.ps1`, with the backend serving the built dashboard on one port
- Fixes: `.env` line breaks, `\n` in emails, the service-scan regex, the missing `vite.config.js`; frontend dependencies pinned; the backend starts on non-Windows systems for CI

### v2: Task Center and Gmail
- Task Center system checks with run history
- Gmail alerts for resource pressure, Windows errors and ML anomalies
- Isolation Forest anomaly detection and Windows event-log collection
- Personal task CRUD, process termination and service control with protections
