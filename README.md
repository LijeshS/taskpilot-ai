# TaskPilot AI — Windows 11 local system dashboard

## Included in this starter
- System overview: CPU, RAM, system drive, network totals, boot time and process count
- Process inventory: PID, parent PID, user, status, memory and start time
- Performance endpoints and live dashboard sampling
- Read-only startup Registry Run entries
- Current user and interactive sessions
- Read-only Windows service inventory
- Installed application inventory
- Local process-presence history in SQLite
- FastAPI WebSocket telemetry endpoint

## Requirements
Windows 11, Python 3.11+, Node.js LTS.

## Start backend
Open PowerShell in `backend`:
```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\run.ps1
```
API docs: http://127.0.0.1:8000/docs

## Start frontend
Open a second terminal:
```powershell
cd frontend
npm install
npm run dev
```
Open http://127.0.0.1:5173

## Safety
This starter is intentionally read-only. It does not terminate processes, modify startup entries, or control services. The API binds to loopback (127.0.0.1) only. Do not expose it to your network. Process names, usernames and executable metadata can be sensitive; do not upload collected runtime data to GitHub.

App history currently records process presence every 10 seconds after the backend starts. It is not exact foreground-app usage and is not retroactive. Startup inventory currently reads Registry Run keys only; Startup folders and Task Scheduler entries are future work. GPU metrics, service control, task CRUD, foreground usage and AI insights are future milestones.


## Added in the extended starter
- Personal task CRUD: create, list, update status/fields and delete tasks in SQLite
- Explainable local resource recommendations based on CPU, memory and disk thresholds
- Process termination endpoint with confirmation in the UI, critical-process protection and no force-kill fallback
- Windows service start/stop/restart endpoint with UI confirmation, service-name validation, protected service list and Administrator requirement

### Important controls warning
Process termination can close applications with unsaved work. Service control can disrupt Windows or installed software. The UI asks for confirmation; the backend also blocks a small set of critical process/service names. This is not a complete Windows safety policy. Use only for services you understand, and keep backups/recovery options. Service controls require launching the backend elevated; do not run elevated for ordinary monitoring or task management. The backend remains bound to localhost.


## Local machine-learning and Windows error intelligence

TaskPilot collects a local telemetry sample every 30 seconds (CPU, memory, system-drive disk usage and process count) into `backend/taskpilot.db`.

1. Start the backend and frontend as described above.
2. Open **AI Assistant** and leave TaskPilot running until at least 30 samples have been collected (about 15 minutes).
3. Select **Train / update ML model**. The backend trains an unsupervised `IsolationForest` model using your laptop's own telemetry and saves it locally as `backend/taskpilot_anomaly_model.pkl`.
4. Select **Collect Windows error logs** to import recent Critical, Error and Warning events from the Windows Application and System event logs (last 24 hours).
5. Refresh the page to review detected unusual telemetry and stored Windows events.

The model learns patterns in resource usage; it is not a supervised fault classifier and cannot prove that a detected anomaly is a hardware/software failure. Windows event records are separate evidence and may include routine warnings. Log collection is read-only and may be limited by Windows event-log permissions. All records and the model remain on this computer. No cloud AI service is called.

New endpoints: `GET /api/ai/model`, `POST /api/ai/train`, `GET /api/ai/anomalies`, `POST /api/logs/collect`, and `GET /api/logs`.


## Task Center: executable system checks

The Tasks page now contains real system actions:
- System health scan: checks CPU, RAM and system-drive pressure.
- Windows error scan: reads recent Windows Critical/Error events and stores them.
- Service inventory scan: inspects Windows services (stopped services are not automatically assumed to be faults).
- AI system analysis: runs the current intelligence analysis.
Execution history is persisted in the `task_runs` SQLite table. Personal reminders remain separate below the system actions.

## Automated Gmail alerts

TaskPilot can send local automated email notifications for critical resource pressure, newly collected Windows Critical/Error events, and ML anomalies.

1. In Gmail, enable Google 2-Step Verification.
2. Create a Google App Password from your Google Account security settings. Use the 16-character App Password, not your normal Gmail password.
3. Start TaskPilot and open **Settings**.
4. Enter the sending Gmail address, recipient address, and App Password. Save, then select **Send test email**.
5. Keep the TaskPilot backend running. It checks CPU/RAM/disk every minute, checks Windows Event Logs approximately every five minutes, and the telemetry worker evaluates ML anomalies every 30 seconds after a model is trained.

Notifications are deduplicated and throttled (typically one email per alert type per hour; Windows event fingerprints are throttled for 24 hours). No email is sent for a healthy check. The Alerts page shows Windows errors, anomalies and successfully sent notification records.

Sender and recipient addresses are stored in `backend/.env` (excluded from Git); the App Password is stored through Python keyring in Windows Credential Manager. Do not commit `.env` or share your App Password. For production deployments, consider OAuth-based Gmail API authentication.

New endpoints:
- `POST /api/tasks/run/{health_check|windows_errors|service_scan|ml_analysis}`
- `GET /api/tasks/runs`
- `GET /api/alerts`
- `GET /api/settings/email`
- `POST /api/settings/email`
- `POST /api/settings/email/test`

Important: the backend must be running for scheduled checks and email alerts. This is a local monitoring agent, not a Windows service yet; it will not monitor while the laptop is off or TaskPilot is closed.


## One-command deploy (Windows 11)

Double-click `deploy.cmd`, or in PowerShell from the project folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\deploy.ps1                  # install, build, start, open http://127.0.0.1:8000
.\deploy.ps1 -InstallStartup  # also start automatically when you sign in
.\deploy.ps1 -RemoveStartup   # remove the sign-in task
.\deploy.ps1 -Stop            # stop the backend
```

It installs Python 3.12 and Node.js LTS with winget if they are missing, creates `backend\.venv`, installs the packages, builds the React dashboard and starts ONE process that serves both the API and the dashboard on `127.0.0.1:8000`. Logs: `backend\taskpilot.log`. Re-running it is safe.
The sign-in task runs without Administrator rights, so Windows service control stays unavailable until you start the backend from an elevated PowerShell on purpose.
Development mode (hot reload) still works: `backend\run.ps1` plus `npm run dev` in `frontend` (http://127.0.0.1:5173).

## Changes in this revision
- Fixed: the saved `.env` file contained a literal `\n` instead of a line break, so the alert recipient could be lost on restart.
- Fixed: alert emails showed literal `\n` characters instead of line breaks.
- Fixed: the service scan never matched stopped services (broken regular expression).
- Fixed: `vite.config.js` was missing (the React plugin is required), and frontend dependencies were `latest`; they are now pinned.
- The backend now starts on non-Windows systems (registry and service pages return empty lists), which makes CI testing possible.
- Design: new instrument-panel look with a live CPU / memory / drive gauge cluster showing the alert thresholds, severity-coloured badges, sentence-case labels, larger text, an offline-capable bundled font, and in-page notifications instead of browser pop-ups.

## Light and dark mode
The header has a **Light / Dark / System** switch. **System** (the default) follows the Windows setting under *Settings → Personalization → Colors → Choose your mode* and changes when Windows changes. A choice of Light or Dark is remembered in that browser (`localStorage` key `taskpilot-theme`). The saved theme is applied before the page draws, so it does not flash the wrong colours on load. All colours are CSS variables in `frontend/src/style.css` (`[data-theme="dark"]` and `[data-theme="light"]` blocks), and the charts read the same variables.

To get the new UI on an existing install, run `.\deploy.ps1` again. It rebuilds the dashboard and restarts the backend.
