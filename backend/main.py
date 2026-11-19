import os, platform, sqlite3, subprocess, re, asyncio, json, pickle, time
try:
    import winreg
except ImportError:  # not Windows: registry-based pages return empty lists
    winreg = None
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import psutil
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

def reload_local_email_config():
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).with_name(".env"), override=True)
    except ImportError:
        pass


DB = Path(__file__).with_name("taskpilot.db")

def system_drive():
    """C:\\ on Windows, / elsewhere (so the API can be tested off-Windows)."""
    return os.environ.get("SystemDrive", "C:") + "\\" if platform.system() == "Windows" else "/"

app = FastAPI(title="TaskPilot AI Local Agent", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173","http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

def connect():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

@app.on_event("startup")
async def startup():
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS app_observations(
          process_name TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, observed_seconds INTEGER DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS tasks(
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT DEFAULT '',
          priority TEXT DEFAULT 'Medium', status TEXT DEFAULT 'Pending', due_date TEXT DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS telemetry(
          id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL,
          cpu REAL, memory REAL, disk REAL, process_count INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS system_logs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, event_id TEXT, level TEXT,
          timestamp TEXT, message TEXT, UNIQUE(source,event_id,timestamp,message))""")
        c.execute("""CREATE TABLE IF NOT EXISTS ai_anomalies(
          id INTEGER PRIMARY KEY AUTOINCREMENT, detected_at TEXT, telemetry_id INTEGER,
          score REAL, severity TEXT, explanation TEXT, acknowledged INTEGER DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS task_runs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, task_name TEXT, status TEXT,
          started_at TEXT, finished_at TEXT, summary TEXT, details TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS notification_state(
          fingerprint TEXT PRIMARY KEY, last_sent TEXT, last_message TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS kv_store(
          key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    asyncio.create_task(history_worker())
    asyncio.create_task(telemetry_worker())
    asyncio.create_task(alert_monitor_worker())
    asyncio.create_task(daily_digest_worker())

async def history_worker():
    while True:
        now=datetime.now(timezone.utc).isoformat()
        names=set()
        for p in psutil.process_iter(["name"]):
            try:
                if p.info["name"]: names.add(p.info["name"])
            except (psutil.NoSuchProcess, psutil.AccessDenied): pass
        with connect() as c:
            for name in names:
                c.execute("""INSERT INTO app_observations VALUES(?,?,?,10)
                  ON CONFLICT(process_name) DO UPDATE SET last_seen=excluded.last_seen,
                  observed_seconds=app_observations.observed_seconds+10""",(name,now,now))
        await asyncio.sleep(10)


# ---------- Local telemetry learning and Windows error log intelligence ----------
MODEL_FILE = Path(__file__).with_name("taskpilot_anomaly_model.pkl")
MODEL_FEATURES = ["cpu", "memory", "disk", "process_count"]

async def telemetry_worker():
    """Collect a small local system snapshot every 30 seconds."""
    while True:
        try:
            cpu = psutil.cpu_percent(interval=0.2)
            memory = psutil.virtual_memory().percent
            drive = system_drive()
            disk = psutil.disk_usage(drive).percent
            count = len(psutil.pids())
            with connect() as c:
                cur = c.execute(
                    "INSERT INTO telemetry(captured_at,cpu,memory,disk,process_count) VALUES(?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(), cpu, memory, disk, count))
                row_id = cur.lastrowid
            # Run inference only after a model has been trained.
            if MODEL_FILE.exists():
                try:
                    with MODEL_FILE.open("rb") as f:
                        model = pickle.load(f)
                    import numpy as np
                    features = np.array([[cpu, memory, disk, count]], dtype=float)
                    prediction = int(model.predict(features)[0])
                    score = float(model.decision_function(features)[0])
                    if prediction == -1:
                        severity = "High" if score < -0.15 else "Medium"
                        explanation = (f"Unusual telemetry: CPU {cpu:.1f}%, RAM {memory:.1f}%, "
                                       f"disk {disk:.1f}%, processes {count}.")
                        with connect() as c:
                            c.execute("INSERT INTO ai_anomalies(detected_at,telemetry_id,score,severity,explanation) VALUES(?,?,?,?,?)",
                                      (datetime.now(timezone.utc).isoformat(), row_id, score, severity, explanation))
                        ml_rows = [
                            ("Type",      "ML anomaly detected"),
                            ("Severity",  severity),
                            ("CPU",       f"{cpu:.1f}%"),
                            ("RAM",       f"{memory:.1f}%"),
                            ("Disk",      f"{disk:.1f}%"),
                            ("Processes", str(count)),
                            ("Score",     f"{score:.4f}"),
                            ("Detected",  datetime.now(timezone.utc).isoformat()),
                        ]
                        await asyncio.to_thread(
                            alert_once, "ML_ANOMALY", "Unusual laptop activity detected",
                            explanation + "\n\nOpen TaskPilot AI to inspect the latest telemetry and process list.",
                            60, ml_rows)
                except Exception:
                    pass
        except Exception:
            pass
        await asyncio.sleep(30)

@app.post("/api/logs/collect")
def collect_windows_logs():
    try:
        return collect_recent_error_events()
    except subprocess.TimeoutExpired:
        raise HTTPException(504,"Windows Event Log query timed out.")
    except json.JSONDecodeError:
        raise HTTPException(500,"Could not parse Windows Event Log output.")
    except Exception as e:
        raise HTTPException(500,str(e)[:300])

@app.get("/api/logs")
def get_system_logs(limit: int = 100):
    limit = max(1, min(limit, 500))
    with connect() as c:
        rows = c.execute("SELECT * FROM system_logs ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
    return {"count":len(rows), "logs":[dict(r) for r in rows]}

@app.get("/api/ai/model")
def ai_model_status():
    with connect() as c:
        count = c.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
        logs = c.execute("SELECT COUNT(*) FROM system_logs").fetchone()[0]
        anomalies = c.execute("SELECT COUNT(*) FROM ai_anomalies").fetchone()[0]
    trained = MODEL_FILE.exists()
    return {"model":"Isolation Forest" if trained else "Not trained",
            "trained":trained,"telemetry_samples":count,"stored_windows_events":logs,
            "detected_anomalies":anomalies,"minimum_samples":30,
            "note":"Unsupervised anomaly detection; it flags unusual patterns, not confirmed faults."}

@app.post("/api/ai/train")
def train_anomaly_model():
    try:
        import numpy as np
        from sklearn.ensemble import IsolationForest
    except ImportError:
        raise HTTPException(500, "ML libraries missing. Run pip install -r requirements.txt.")
    with connect() as c:
        rows = c.execute("SELECT cpu,memory,disk,process_count FROM telemetry ORDER BY id").fetchall()
    if len(rows) < 30:
        raise HTTPException(400, f"Need at least 30 telemetry samples; currently have {len(rows)}. Keep TaskPilot running to collect data.")
    x = np.array([[r["cpu"],r["memory"],r["disk"],r["process_count"]] for r in rows], dtype=float)
    model = IsolationForest(n_estimators=150, contamination="auto", random_state=42)
    model.fit(x)
    with MODEL_FILE.open("wb") as f:
        pickle.dump(model, f)
    # Re-score historical samples and persist anomalies.
    predictions = model.predict(x)
    scores = model.decision_function(x)
    with connect() as c:
        historical = c.execute("SELECT id,captured_at,cpu,memory,disk,process_count FROM telemetry ORDER BY id").fetchall()
        for i, row in enumerate(historical):
            if predictions[i] == -1:
                detail = (f"Historical unusual sample: CPU {row['cpu']:.1f}%, RAM {row['memory']:.1f}%, "
                          f"disk {row['disk']:.1f}%, processes {row['process_count']}.")
                c.execute("""INSERT INTO ai_anomalies(detected_at,telemetry_id,score,severity,explanation)
                             SELECT ?,?,?,?,? WHERE NOT EXISTS
                             (SELECT 1 FROM ai_anomalies WHERE telemetry_id=? AND explanation=?)""",
                          (row["captured_at"],row["id"],float(scores[i]),
                           "Medium" if scores[i] >= -0.15 else "High",detail,row["id"],detail))
    return {"trained":True,"model":"Isolation Forest","samples_used":len(rows),
            "features":MODEL_FEATURES,"message":"Model trained on your locally collected telemetry."}

@app.get("/api/ai/anomalies")
def list_anomalies():
    with connect() as c:
        rows=c.execute("SELECT * FROM ai_anomalies ORDER BY detected_at DESC LIMIT 100").fetchall()
    return {"count":len(rows),"anomalies":[dict(r) for r in rows]}


# ---------- Actionable system tasks and automated Gmail alerts ----------
import smtplib
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ---- Default alert thresholds (overridable via /api/settings/thresholds) ----
DEFAULT_THRESHOLDS = {"cpu": 90, "memory": 90, "disk": 95}

def get_thresholds():
    with connect() as c:
        row = c.execute("SELECT value FROM kv_store WHERE key='alert_thresholds'").fetchone()
    if row:
        try:
            return {**DEFAULT_THRESHOLDS, **json.loads(row["value"])}
        except Exception:
            pass
    return dict(DEFAULT_THRESHOLDS)

# ---- HTML email builder ----
def _html_email(subject: str, body_rows: list[tuple[str, str]], title_color: str = "#c2352a") -> str:
    rows_html = "".join(
        f'<tr><td style="padding:6px 12px;color:#555;width:38%;border-bottom:1px solid #eee;font-size:13px">'
        f'{label}</td><td style="padding:6px 12px;border-bottom:1px solid #eee;font-size:13px">'
        f'<strong>{value}</strong></td></tr>'
        for label, value in body_rows
    )
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 16px">
<table width="560" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;
  box-shadow:0 2px 8px rgba(0,0,0,.12)">
  <tr><td style="background:{title_color};padding:20px 24px">
    <span style="color:#fff;font-size:18px;font-weight:bold">&#9888; TaskPilot AI</span>
    <span style="color:rgba(255,255,255,.75);font-size:13px;float:right;line-height:1.6">{subject}</span>
  </td></tr>
  <tr><td style="padding:0"><table width="100%" cellpadding="0" cellspacing="0">{rows_html}</table></td></tr>
  <tr><td style="padding:16px 24px;background:#f9fafb;border-top:1px solid #eee">
    <span style="color:#888;font-size:12px">TaskPilot AI &mdash; local system monitor &mdash;
    open the dashboard at <a href="http://127.0.0.1:8000">http://127.0.0.1:8000</a> for details.</span>
  </td></tr>
</table></td></tr></table></body></html>"""

def send_gmail_html(subject: str, plain: str, html: str):
    """Send a multipart email (HTML + plain-text fallback)."""
    cfg = email_config_status()
    if not cfg["configured"]:
        return {"sent": False, "reason": "Gmail is not configured"}
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[TaskPilot AI] {subject}"
    msg["From"]    = cfg["sender"]
    msg["To"]      = cfg["recipient"]
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html,  "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
        server.login(cfg["sender"], get_gmail_app_password())
        server.send_message(msg)
    return {"sent": True, "recipient": cfg["recipient"]}

def get_gmail_app_password():
    try:
        import keyring
        return keyring.get_password("TaskPilotAI", "gmail_app_password")
    except Exception:
        return None

def email_config_status():
    return {
        "configured": bool(os.getenv("TASKPILOT_GMAIL_USER") and
                           get_gmail_app_password() and
                           os.getenv("TASKPILOT_ALERT_EMAIL")),
        "recipient": os.getenv("TASKPILOT_ALERT_EMAIL", ""),
        "sender": os.getenv("TASKPILOT_GMAIL_USER", "")
    }

def send_gmail_alert(subject: str, body: str, rows: list[tuple[str,str]] | None = None):
    """Send alert email. If rows provided, sends HTML+plain; otherwise plain only."""
    cfg = email_config_status()
    if not cfg["configured"]:
        return {"sent":False, "reason":"Gmail is not configured"}
    if rows:
        html = _html_email(subject, rows)
        return send_gmail_html(subject, body, html)
    msg = EmailMessage()
    msg["Subject"] = f"[TaskPilot AI] {subject}"
    msg["From"] = cfg["sender"]
    msg["To"] = cfg["recipient"]
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
        server.login(cfg["sender"], get_gmail_app_password())
        server.send_message(msg)
    return {"sent":True, "recipient":cfg["recipient"]}

def alert_once(fingerprint: str, subject: str, body: str, cooldown_minutes: int = 60,
               rows: list[tuple[str,str]] | None = None):
    now = datetime.now(timezone.utc)
    with connect() as c:
        row = c.execute("SELECT last_sent FROM notification_state WHERE fingerprint=?", (fingerprint,)).fetchone()
    if row and row["last_sent"]:
        try:
            last = datetime.fromisoformat(row["last_sent"])
            if (now-last).total_seconds() < cooldown_minutes*60:
                return {"sent":False,"reason":"Cooldown active"}
        except ValueError:
            pass
    result = send_gmail_alert(subject, body, rows)
    if result.get("sent"):
        with connect() as c:
            c.execute("""INSERT INTO notification_state(fingerprint,last_sent,last_message) VALUES(?,?,?)
                         ON CONFLICT(fingerprint) DO UPDATE SET last_sent=excluded.last_sent,last_message=excluded.last_message""",
                      (fingerprint, now.isoformat(), subject))
    return result

def perform_health_check():
    thr = get_thresholds()
    cpu = psutil.cpu_percent(interval=.25)
    memory = psutil.virtual_memory()
    drive = system_drive()
    disk = psutil.disk_usage(drive)
    findings = []
    if cpu >= thr["cpu"]:
        findings.append({"severity":"High","code":"HIGH_CPU","title":"CPU usage critically high",
                         "evidence":f"CPU utilization is {cpu:.1f}% (threshold {thr['cpu']}%)."})
    if memory.percent >= thr["memory"]:
        findings.append({"severity":"High","code":"HIGH_MEMORY","title":"Memory usage critically high",
                         "evidence":f"RAM utilization is {memory.percent:.1f}% (threshold {thr['memory']}%)."})
    if disk.percent >= thr["disk"]:
        findings.append({"severity":"High","code":"LOW_DISK_SPACE","title":"System drive almost full",
                         "evidence":f"System drive is {disk.percent:.1f}% used (threshold {thr['disk']}%)."})
    return {"status":"PROBLEM" if findings else "HEALTHY",
            "checked_at":datetime.now(timezone.utc).isoformat(),
            "metrics":{"cpu_percent":cpu,"memory_percent":memory.percent,"disk_percent":disk.percent},
            "thresholds":thr,
            "findings":findings}

async def alert_monitor_worker():
    """Periodic local checks; sends email only for actionable high-severity problems."""
    last_event_scan = 0
    while True:
        try:
            report = await asyncio.to_thread(perform_health_check)
            for finding in report["findings"]:
                plain = (f"TaskPilot detected a potential system problem.\n\n"
                         f"Problem: {finding['title']}\nEvidence: {finding['evidence']}\n"
                         f"Time: {report['checked_at']}\n\nOpen TaskPilot AI to inspect processes and system health.")
                rows = [
                    ("Problem", finding["title"]),
                    ("Severity", finding["severity"]),
                    ("Evidence", finding["evidence"]),
                    ("CPU", f"{report['metrics']['cpu_percent']:.1f}%"),
                    ("RAM", f"{report['metrics']['memory_percent']:.1f}%"),
                    ("Disk", f"{report['metrics']['disk_percent']:.1f}%"),
                    ("Detected at", report["checked_at"]),
                ]
                await asyncio.to_thread(alert_once, finding["code"], finding["title"], plain, 60, rows)
            # Collect Windows critical/error events every 5 minutes.
            if platform.system() == "Windows" and time.time()-last_event_scan >= 300:
                last_event_scan = time.time()
                await asyncio.to_thread(collect_recent_error_events, True)
        except Exception:
            # Do not crash the monitoring loop; API health and logs remain available.
            pass
        await asyncio.sleep(60)

def collect_recent_error_events(automatic=False):
    if platform.system() != "Windows":
        return {"read":0,"new_saved":0}
    script = r"""
    $since=(Get-Date).AddMinutes(-6)
    $events=foreach($log in @('Application','System')) {
      Get-WinEvent -FilterHashtable @{LogName=$log; StartTime=$since; Level=1,2} -MaxEvents 100 -ErrorAction SilentlyContinue |
      Select-Object @{n='source';e={$_.LogName}}, @{n='event_id';e={$_.Id.ToString()}},
        @{n='level';e={if($_.Level -eq 1){'Critical'}else{'Error'}}},
        @{n='timestamp';e={$_.TimeCreated.ToUniversalTime().ToString('o')}},
        @{n='message';e={ $m=($_.Message -replace '\s+',' '); $m.Substring(0,[Math]::Min(1200,$m.Length)) }}
    }
    $events | ConvertTo-Json -Depth 3 -Compress
    """
    result = subprocess.run(["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-Command",script],
                            capture_output=True,text=True,timeout=35)
    raw=result.stdout.strip()
    events=json.loads(raw) if raw else []
    if isinstance(events,dict): events=[events]
    added_events=[]
    with connect() as c:
        for e in events:
            cur=c.execute("""INSERT OR IGNORE INTO system_logs(source,event_id,level,timestamp,message)
                             VALUES(?,?,?,?,?)""",
                          (e.get("source","Windows"),str(e.get("event_id","")),e.get("level","Error"),
                           e.get("timestamp",""),e.get("message","")))
            if cur.rowcount:
                added_events.append(e)
    for e in added_events:
        body=(f"New Windows {e.get('level')} event detected.\n\n"
              f"Log: {e.get('source')}\nEvent ID: {e.get('event_id')}\n"
              f"Time: {e.get('timestamp')}\nMessage: {e.get('message')}\n\n"
              "Review the Windows Event Viewer and TaskPilot AI for context.")
        fingerprint=f"WIN_EVENT:{e.get('source')}:{e.get('event_id')}"
        win_rows = [
            ("Level",      e.get("level", "")),
            ("Log source", e.get("source", "")),
            ("Event ID",   str(e.get("event_id", ""))),
            ("Time",       e.get("timestamp", "")),
            ("Message",    (e.get("message") or "")[:400]),
        ]
        alert_once(fingerprint, f"Windows {e.get('level')} event {e.get('event_id')}", body, 1440, win_rows)
    return {"read":len(events),"new_saved":len(added_events)}

@app.get("/api/settings/email")
def get_email_settings():
    return email_config_status()

class EmailSettings(BaseModel):
    sender: str = Field(min_length=5,max_length=254)
    recipient: str = Field(min_length=5,max_length=254)
    app_password: str = Field(min_length=8,max_length=100)

@app.post("/api/settings/email")
def save_email_settings(settings: EmailSettings):
    # Store secrets in a local .env file, never in SQLite or frontend storage.
    env_path=Path(__file__).with_name(".env")
    values={
        "TASKPILOT_GMAIL_USER":settings.sender.strip(),
        "TASKPILOT_ALERT_EMAIL":settings.recipient.strip()
    }
    try:
        import keyring
        keyring.set_password("TaskPilotAI","gmail_app_password",settings.app_password.replace(" ",""))
    except Exception as e:
        raise HTTPException(500,"Could not save the App Password to Windows Credential Manager: "+str(e)[:180])
    env_path.write_text("\n".join(f'{k}="{v.replace(chr(34), "")}"' for k,v in values.items())+"\n",encoding="utf-8")
    try:
        os.chmod(env_path,0o600)
    except OSError:
        pass
    os.environ.update(values)
    return {"saved":True,"configured":True,"sender":values["TASKPILOT_GMAIL_USER"],
            "recipient":values["TASKPILOT_ALERT_EMAIL"]}

@app.post("/api/settings/email/test")
def test_email():
    try:
        result=send_gmail_alert("Test notification","TaskPilot AI Gmail notifications are configured correctly.")
        if not result.get("sent"):
            raise HTTPException(400,result.get("reason","Gmail not configured"))
        return {"sent":True,"message":"Test email sent."}
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(400,"Gmail authentication failed. Check the Gmail address and 16-character Google App Password.")
    except Exception as e:
        raise HTTPException(502,f"Email delivery failed: {str(e)[:250]}")

@app.post("/api/tasks/run/{task_name}")
def run_system_task(task_name: str):
    allowed={"health_check","windows_errors","service_scan","ml_analysis"}
    if task_name not in allowed:
        raise HTTPException(404,"Unknown system task")
    started=datetime.now(timezone.utc).isoformat()
    status="COMPLETED"
    try:
        if task_name=="health_check":
            result=perform_health_check()
            if result["findings"]:
                for f in result["findings"]:
                    body=f"{f['title']}\n{f['evidence']}\nDetected at {result['checked_at']}"
                    alert_once(f["code"],f["title"],body,60)
            summary=f"{result['status']}: {len(result['findings'])} issue(s) found."
        elif task_name=="windows_errors":
            result=collect_recent_error_events()
            summary=f"Read {result['read']} events; saved {result['new_saved']} new error events."
        elif task_name=="service_scan":
            raw=subprocess.run(["sc.exe","query","state=","all"],capture_output=True,text=True,timeout=20).stdout
            stopped=re.findall(r"SERVICE_NAME:\s*(.+?)\s+.*?STATE\s*:\s*\d+\s+(STOPPED)",raw,re.S)
            result={"services_checked":len(re.findall(r"SERVICE_NAME:",raw)),"stopped_services":stopped}
            summary=f"Checked {result['services_checked']} services; {len(stopped)} stopped (some may be intentionally stopped)."
        else:
            result=recommendations()
            summary=f"AI analysis completed: {len(result['recommendations'])} finding(s)."
        finished=datetime.now(timezone.utc).isoformat()
        with connect() as c:
            cur=c.execute("INSERT INTO task_runs(task_name,status,started_at,finished_at,summary,details) VALUES(?,?,?,?,?,?)",
                          (task_name,status,started,finished,summary,json.dumps(result,default=str)))
            run_id=cur.lastrowid
        return {"id":run_id,"task_name":task_name,"status":status,"summary":summary,"result":result}
    except Exception as e:
        finished=datetime.now(timezone.utc).isoformat()
        with connect() as c:
            c.execute("INSERT INTO task_runs(task_name,status,started_at,finished_at,summary,details) VALUES(?,?,?,?,?,?)",
                      (task_name,"FAILED",started,finished,str(e),"{}"))
        raise HTTPException(500,str(e))

@app.get("/api/tasks/runs")
def list_task_runs():
    with connect() as c:
        rows=c.execute("SELECT * FROM task_runs ORDER BY id DESC LIMIT 50").fetchall()
    return {"runs":[dict(r) for r in rows]}

@app.get("/api/health")
def health():
    return {"status":"ok","platform":platform.platform()}

@app.get("/api/system/overview")
def overview():
    m=psutil.virtual_memory(); d=psutil.disk_usage(system_drive())
    n=psutil.net_io_counters()
    return {"hostname":platform.node(),"platform":platform.platform(),
      "cpu_percent":psutil.cpu_percent(interval=.15),"cpu_count_logical":psutil.cpu_count(),
      "cpu_count_physical":psutil.cpu_count(logical=False),"memory_total_gb":round(m.total/1024**3,2),
      "memory_used_gb":round(m.used/1024**3,2),"memory_percent":m.percent,
      "disk_total_gb":round(d.total/1024**3,2),"disk_used_gb":round(d.used/1024**3,2),
      "disk_percent":d.percent,"network_sent_mb":round(n.bytes_sent/1024**2,1),
      "network_received_mb":round(n.bytes_recv/1024**2,1),"process_count":len(psutil.pids()),
      "boot_time":datetime.fromtimestamp(psutil.boot_time()).astimezone().isoformat()}

@app.get("/api/processes")
def processes():
    out=[]
    for p in psutil.process_iter(["pid","ppid","name","username","status","memory_info","create_time"]):
        try:
            x=p.info; mi=x["memory_info"]
            try:
                cpu_value=p.cpu_percent(interval=None)
            except (psutil.NoSuchProcess,psutil.AccessDenied):
                cpu_value=0
            out.append({"pid":x["pid"],"ppid":x["ppid"],"name":x["name"] or "Unknown",
              "username":x["username"],"status":x["status"],"cpu_percent":cpu_value,
              "memory_mb":round(mi.rss/1024**2,1) if mi else 0,
              "started_at":datetime.fromtimestamp(x["create_time"]).astimezone().isoformat()})
        except (psutil.NoSuchProcess,psutil.AccessDenied,psutil.ZombieProcess): pass
    out.sort(key=lambda x:x["memory_mb"],reverse=True)
    return {"total":len(out),"processes":out}

@app.get("/api/performance/cpu")
def cpu(): return {"overall":psutil.cpu_percent(interval=.2),"per_core":psutil.cpu_percent(interval=.2,percpu=True)}

@app.get("/api/performance/memory")
def memory():
    m=psutil.virtual_memory(); s=psutil.swap_memory()
    return {"total_gb":round(m.total/1024**3,2),"used_gb":round(m.used/1024**3,2),
      "available_gb":round(m.available/1024**3,2),"percent":m.percent,"pagefile_percent":s.percent}

@app.get("/api/startup")
def startup_apps():
    entries=[]
    if winreg is None:
        return {"entries":[],"note":"Registry startup entries are only available on Windows."}
    locations=[(winreg.HKEY_CURRENT_USER,r"Software\Microsoft\Windows\CurrentVersion\Run","Current user"),
      (winreg.HKEY_LOCAL_MACHINE,r"Software\Microsoft\Windows\CurrentVersion\Run","All users"),
      (winreg.HKEY_LOCAL_MACHINE,r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run","32-bit startup")]
    for hive,path,source in locations:
        try:
            with winreg.OpenKey(hive,path) as k:
                for i in range(winreg.QueryInfoKey(k)[1]):
                    try:
                        name,value,_=winreg.EnumValue(k,i)
                        entries.append({"name":name,"command":str(value),"source":source})
                    except OSError: pass
        except OSError: pass
    return {"entries":entries,"note":"Read-only Registry Run entries; startup folders and scheduled tasks are not included yet."}

@app.get("/api/users")
def users():
    sessions=[]
    for u in psutil.users():
        sessions.append({"name":u.name,"terminal":u.terminal,"host":u.host,
          "started":datetime.fromtimestamp(u.started).astimezone().isoformat()})
    return {"current_user":os.environ.get("USERNAME","Unknown"),"sessions":sessions}

@app.get("/api/services")
def services():
    if platform.system()!="Windows":
        return {"count":0,"services":[]}
    raw=subprocess.run(["sc.exe","query","state=","all"],capture_output=True,text=True,timeout=15).stdout
    items=[]
    for b in re.split(r"(?=SERVICE_NAME:)",raw):
        n=re.search(r"SERVICE_NAME:\s*(.+)",b); d=re.search(r"DISPLAY_NAME:\s*(.+)",b)
        s=re.search(r"STATE\s*:\s*\d+\s+(\w+)",b)
        if n: items.append({"name":n.group(1).strip(),"display_name":d.group(1).strip() if d else "",
                            "status":s.group(1) if s else "UNKNOWN"})
    return {"count":len(items),"services":items}

@app.get("/api/applications")
def applications():
    apps=[]
    if winreg is None:
        return {"count":0,"applications":[]}
    roots=[(winreg.HKEY_LOCAL_MACHINE,r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
      (winreg.HKEY_LOCAL_MACHINE,r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
      (winreg.HKEY_CURRENT_USER,r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive,path in roots:
        try:
            with winreg.OpenKey(hive,path) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root,winreg.EnumKey(root,i)) as k:
                            name=winreg.QueryValueEx(k,"DisplayName")[0]
                            def get(v):
                                try:return str(winreg.QueryValueEx(k,v)[0])
                                except OSError:return ""
                            apps.append({"name":str(name),"version":get("DisplayVersion"),
                                         "publisher":get("Publisher"),"install_date":get("InstallDate")})
                    except OSError: pass
        except OSError: pass
    unique={a["name"].lower():a for a in apps}
    result=sorted(unique.values(),key=lambda a:a["name"].lower())
    return {"count":len(result),"applications":result}

@app.get("/api/apps/history")
def history():
    with connect() as c:
        rows=c.execute("SELECT * FROM app_observations ORDER BY observed_seconds DESC LIMIT 200").fetchall()
    return {"history":[dict(r) for r in rows],
      "note":"Process presence observed every 10 seconds since TaskPilot started; not exact foreground usage time."}

@app.get("/api/alerts")
def get_alerts():
    with connect() as c:
        logs=c.execute("SELECT id,source,event_id,level,timestamp,message FROM system_logs WHERE level IN ('Critical','Error') ORDER BY timestamp DESC LIMIT 50").fetchall()
        anomalies=c.execute("SELECT id,detected_at,severity,explanation,score FROM ai_anomalies ORDER BY detected_at DESC LIMIT 50").fetchall()
        notifications=c.execute("SELECT fingerprint,last_sent,last_message FROM notification_state ORDER BY last_sent DESC LIMIT 50").fetchall()
    return {"windows_errors":[dict(r) for r in logs],
            "anomalies":[dict(r) for r in anomalies],
            "emails_sent":[dict(r) for r in notifications]}

# ---------- Custom alert thresholds ----------
class ThresholdSettings(BaseModel):
    cpu:    int = Field(ge=50, le=100)
    memory: int = Field(ge=50, le=100)
    disk:   int = Field(ge=50, le=100)

@app.get("/api/settings/thresholds")
def get_threshold_settings():
    return get_thresholds()

@app.post("/api/settings/thresholds")
def save_threshold_settings(s: ThresholdSettings):
    val = json.dumps({"cpu": s.cpu, "memory": s.memory, "disk": s.disk})
    with connect() as c:
        c.execute("INSERT INTO kv_store(key,value) VALUES('alert_thresholds',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (val,))
    return get_thresholds()

# ---------- Acknowledge anomaly ----------
@app.post("/api/ai/anomalies/{anomaly_id}/acknowledge")
def acknowledge_anomaly(anomaly_id: int):
    with connect() as c:
        cur = c.execute("UPDATE ai_anomalies SET acknowledged=1 WHERE id=?", (anomaly_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Anomaly not found")
    return {"acknowledged": True, "id": anomaly_id}

# ---------- Daily digest ----------
async def daily_digest_worker():
    """Send a daily summary email at ~08:00 local time if Gmail is configured."""
    import calendar
    while True:
        now = datetime.now()
        # Sleep until next 08:00 local time
        target = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= target:
            target = target.replace(day=now.day+1) if now.day < calendar.monthrange(now.year, now.month)[1] \
                     else target.replace(month=now.month % 12 + 1, day=1)
        wait_secs = (target - now).total_seconds()
        await asyncio.sleep(max(wait_secs, 60))
        try:
            await asyncio.to_thread(_send_daily_digest)
        except Exception:
            pass

def _send_daily_digest():
    cfg = email_config_status()
    if not cfg["configured"]:
        return
    report = perform_health_check()
    thr = get_thresholds()
    with connect() as c:
        anomaly_count = c.execute("SELECT COUNT(*) FROM ai_anomalies WHERE acknowledged=0 AND detected_at > datetime('now','-1 day')").fetchone()[0]
        error_count   = c.execute("SELECT COUNT(*) FROM system_logs WHERE level IN ('Critical','Error') AND timestamp > datetime('now','-1 day')").fetchone()[0]
        email_count   = c.execute("SELECT COUNT(*) FROM notification_state WHERE last_sent > datetime('now','-1 day')").fetchone()[0]
        top_procs_rows = c.execute("SELECT process_name, observed_seconds FROM app_observations ORDER BY observed_seconds DESC LIMIT 5").fetchall()

    top_procs = ", ".join(f"{r['process_name']} ({r['observed_seconds']//60}m)" for r in top_procs_rows) or "none"
    status_color = "#137a50" if report["status"] == "HEALTHY" else "#c2352a"
    rows = [
        ("System status",   report["status"]),
        ("CPU",             f"{report['metrics']['cpu_percent']:.1f}% (alert ≥ {thr['cpu']}%)"),
        ("RAM",             f"{report['metrics']['memory_percent']:.1f}% (alert ≥ {thr['memory']}%)"),
        ("Disk",            f"{report['metrics']['disk_percent']:.1f}% (alert ≥ {thr['disk']}%)"),
        ("ML anomalies (24h)", str(anomaly_count)),
        ("Windows errors (24h)", str(error_count)),
        ("Alert emails sent (24h)", str(email_count)),
        ("Most-seen processes", top_procs),
        ("Report time",     datetime.now().strftime("%Y-%m-%d %H:%M")),
    ]
    plain = (f"TaskPilot AI — daily digest\n\n"
             f"Status: {report['status']}\nCPU: {report['metrics']['cpu_percent']:.1f}%  "
             f"RAM: {report['metrics']['memory_percent']:.1f}%  Disk: {report['metrics']['disk_percent']:.1f}%\n"
             f"ML anomalies (24h): {anomaly_count}  Windows errors: {error_count}  Emails sent: {email_count}\n"
             f"Top processes: {top_procs}")
    send_gmail_html("Daily system digest", plain, _html_email("Daily system digest", rows, status_color))

@app.post("/api/settings/email/digest")
def send_digest_now():
    """Trigger the daily digest immediately (for testing)."""
    try:
        _send_daily_digest()
        return {"sent": True, "message": "Daily digest sent."}
    except Exception as e:
        raise HTTPException(502, f"Digest failed: {str(e)[:250]}")

@app.websocket("/ws/system")
async def live(ws:WebSocket):
    await ws.accept()
    try:
        while True:
            m=psutil.virtual_memory()
            await ws.send_text(json.dumps({"cpu_percent":psutil.cpu_percent(),"memory_percent":m.percent,
                "process_count":len(psutil.pids()),"timestamp":datetime.now().isoformat()}))
            await asyncio.sleep(2)
    except WebSocketDisconnect: pass


# ---------- Personal task CRUD ----------
class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    priority: str = Field(default="Medium", pattern="^(Low|Medium|High)$")
    due_date: str = Field(default="", max_length=40)

class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    priority: str | None = Field(default=None, pattern="^(Low|Medium|High)$")
    status: str | None = Field(default=None, pattern="^(Pending|In Progress|Completed)$")
    due_date: str | None = Field(default=None, max_length=40)

@app.get("/api/tasks")
def list_tasks():
    with connect() as c:
        rows=c.execute("SELECT * FROM tasks ORDER BY CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, id DESC").fetchall()
    return {"tasks":[dict(r) for r in rows]}

@app.post("/api/tasks")
def create_task(task: TaskCreate):
    now=datetime.now(timezone.utc).isoformat()
    with connect() as c:
        cur=c.execute("""INSERT INTO tasks(title,description,priority,status,due_date,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?)""",(task.title.strip(),task.description,task.priority,"Pending",task.due_date,now,now))
        row=c.execute("SELECT * FROM tasks WHERE id=?",(cur.lastrowid,)).fetchone()
    return dict(row)

@app.put("/api/tasks/{task_id}")
def update_task(task_id: int, task: TaskUpdate):
    values=task.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(400,"No fields supplied")
    values["updated_at"]=datetime.now(timezone.utc).isoformat()
    with connect() as c:
        existing=c.execute("SELECT id FROM tasks WHERE id=?",(task_id,)).fetchone()
        if not existing: raise HTTPException(404,"Task not found")
        assignments=", ".join(f"{k}=?" for k in values)
        c.execute(f"UPDATE tasks SET {assignments} WHERE id=?",(*values.values(),task_id))
        row=c.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone()
    return dict(row)

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    with connect() as c:
        cur=c.execute("DELETE FROM tasks WHERE id=?",(task_id,))
        if cur.rowcount==0: raise HTTPException(404,"Task not found")
    return {"deleted":True,"id":task_id}

# ---------- Explicitly confirmed local process control ----------
PROTECTED_PIDS={0,4,os.getpid()}
PROTECTED_PROCESS_NAMES={"system","registry","smss.exe","csrss.exe","wininit.exe","services.exe",
                         "lsass.exe","winlogon.exe","fontdrvhost.exe","svchost.exe","taskhostw.exe","dwm.exe"}
@app.post("/api/processes/{pid}/terminate")
def terminate_process(pid: int):
    if pid in PROTECTED_PIDS:
        raise HTTPException(403,"This system/TaskPilot process is protected")
    try:
        proc=psutil.Process(pid)
        name=(proc.name() or "").lower()
        if name in PROTECTED_PROCESS_NAMES:
            raise HTTPException(403,"Windows critical process is protected")
        proc.terminate()
        try:
            proc.wait(timeout=3)
            return {"success":True,"pid":pid,"name":name,"message":"Process terminated"}
        except psutil.TimeoutExpired:
            raise HTTPException(409,"Process did not exit in 3 seconds; no force-kill was attempted")
    except psutil.NoSuchProcess:
        raise HTTPException(404,"Process no longer exists")
    except psutil.AccessDenied:
        raise HTTPException(403,"Access denied. Some processes require elevated permissions")

# ---------- Windows service controls ----------
SERVICE_NAME_RE=re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
PROTECTED_SERVICES={"rpcss","dcomlaunch","plugplay","eventlog","winmgmt","samss","lsm","power"}
@app.post("/api/services/{name}/{action}")
def control_service(name: str, action: str):
    if platform.system()!="Windows":
        raise HTTPException(501,"Windows service controls are available only on Windows")
    if not SERVICE_NAME_RE.fullmatch(name):
        raise HTTPException(400,"Invalid service name")
    if name.lower() in PROTECTED_SERVICES:
        raise HTTPException(403,"This essential Windows service is protected")
    if action not in {"start","stop","restart"}:
        raise HTTPException(400,"Action must be start, stop or restart")
    import ctypes
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise HTTPException(403,"Service control requires an elevated TaskPilot backend. Run as Administrator only when needed.")
    def run_sc(command):
        result=subprocess.run(["sc.exe",command,name],capture_output=True,text=True,timeout=20)
        if result.returncode!=0:
            raise HTTPException(409,(result.stderr or result.stdout or "Service control failed")[-1000:])
    if action=="restart":
        run_sc("stop")
        # Wait briefly for service transition before starting it again.
        import time
        time.sleep(2)
        run_sc("start")
    else:
        run_sc(action)
    return {"success":True,"service":name,"action":action,
            "message":f"Requested {action} for {name}. Check service status to confirm."}

# ---------- Explainable local system recommendations ----------
@app.get("/api/ai/recommendations")
def recommendations():
    m=psutil.virtual_memory()
    disk=psutil.disk_usage(system_drive())
    cpu=psutil.cpu_percent(interval=.25)
    top=[]
    for p in psutil.process_iter(["pid","name","memory_info"]):
        try:
            mi=p.info["memory_info"]
            top.append({"pid":p.info["pid"],"name":p.info["name"] or "Unknown",
                        "memory_mb":round(mi.rss/1024**2,1) if mi else 0})
        except (psutil.NoSuchProcess,psutil.AccessDenied): pass
    top.sort(key=lambda x:x["memory_mb"],reverse=True)
    findings=[]
    if cpu>=85:
        findings.append({"severity":"High","title":"High CPU utilization",
          "evidence":f"Current CPU utilization is {cpu:.1f}%.",
          "recommendation":"Inspect the Processes page, sort by CPU in a future detailed view, and identify the workload before closing anything."})
    if m.percent>=85:
        findings.append({"severity":"High","title":"High memory utilization",
          "evidence":f"Memory usage is {m.percent:.1f}% ({m.used/1024**3:.1f} GB used).",
          "recommendation":"Review memory-heavy applications and browser tabs. Save work before closing applications."})
    if disk.percent>=90:
        findings.append({"severity":"High","title":"Low system-drive free space",
          "evidence":f"The system drive is {disk.percent:.1f}% used.",
          "recommendation":"Review large files and installed applications. Back up important files before deleting anything."})
    if not findings:
        findings.append({"severity":"Info","title":"No critical resource threshold detected",
          "evidence":f"CPU {cpu:.1f}%, memory {m.percent:.1f}%, system drive {disk.percent:.1f}% used.",
          "recommendation":"Continue monitoring. These are threshold-based checks, not a trained predictive AI model."})
    return {"generated_at":datetime.now().astimezone().isoformat(),
            "method":"Explainable rule-based analysis (not an ML prediction)",
            "metrics":{"cpu_percent":cpu,"memory_percent":m.percent,"disk_percent":disk.percent},
            "top_memory_processes":top[:5],"recommendations":findings}


# ---------- Optional: serve the built React app (single-process deployment) ----------
# Created by `npm run build`. Mounted last so every /api route above takes priority.
from fastapi.staticfiles import StaticFiles
DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
