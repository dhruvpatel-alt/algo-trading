# Windows VM Production Deployment Guide (AWS EC2 / Windows Server)

Guide for deploying the **XAU/USD Paper Trading Bot** (and optional Chart API) on a **Windows Virtual Machine (AWS EC2)** to run **24/7 automatically**, auto-restart on crashes, and survive VM reboots.

---

## Overview of 24/7 Deployment Methods on Windows

| Method | Auto-start on Boot (No RDP required)? | Auto-restart on Crash? | Recommendation |
| :--- | :---: | :---: | :--- |
| **NSSM (Windows Service)** | Yes | Yes (Instant) | **Recommended (Best for Production)** |
| **Windows Task Scheduler** | Yes | Yes (Configurable) | Native Windows alternative |
| **PM2 (Node.js)** | Requires service wrapper | Yes | Good if PM2 is already used |

---

## 1. Prepare Python & Project Setup on Windows VM

### Step 1.1: Install Python 3.10+
1. Download Python installer for Windows from [python.org](https://www.python.org/downloads/).
2. Run the installer and **MUST CHECK**:
   `[X] Add python.exe to PATH`
3. Verify in Command Prompt (`cmd.exe`) or PowerShell:
   ```cmd
   python --version
   pip --version
   ```

### Step 1.2: Clone or Download Codebase
Open PowerShell or Command Prompt and navigate to your desired directory (e.g. `C:\TradingBot`):
```cmd
cd C:\
git clone <your-repository-url> TradingBot
cd C:\TradingBot\xau_algo
```

### Step 1.3: Set Up Virtual Environment & Dependencies
```cmd
python -m venv venv
venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 1.4: Create `.env` Configuration File
Copy `.env.example` to `.env`:
```cmd
copy .env.example .env
```
Open `.env` in Notepad:
```cmd
notepad .env
```
Fill in your API keys & credentials:
```env
TWELVE_DATA_API_KEY_1=your_twelve_data_key_here
TWELVE_DATA_API_KEY_2=...
SUPABASE_DB_URL=https://your-project.supabase.co
SUPABASE_DB_KEY=your_supabase_key
SUPABASE_DB_DIRECT_URL=postgresql://...
HEALTH_API_PORT=8080
MAX_TICK_STALENESS_SECONDS=120
```

### Step 1.5: Verify Manual Run
Test paper trading in terminal:
```cmd
python main.py --mode paper
```
Verify output logs appear cleanly. Press `Ctrl+C` to stop.

---

## 2. Method 1 (RECOMMENDED): Deploy 24/7 using NSSM (Non-Sucking Service Manager)

NSSM installs your Python bot as a native **Windows Service** that runs continuously in the background even when you log out of Remote Desktop (RDP).

### Step 2.1: Download NSSM
1. Download NSSM zip from [nssm.cc/download](https://nssm.cc/download) (or use Chocolatey: `choco install nssm`).
2. Extract `nssm.exe` (64-bit version from `win64` folder) into `C:\Windows\System32\` or `C:\TradingBot\`.

### Step 2.2: Install Service via Command Prompt (Admin)
Open **Command Prompt as Administrator** and run:
```cmd
nssm install XAUTradingBot
```

A graphical configuration window will pop up:
1. **Application Tab**:
   - **Path**: `C:\TradingBot\xau_algo\venv\Scripts\python.exe`
   - **Startup directory**: `C:\TradingBot\xau_algo`
   - **Arguments**: `main.py --mode paper`
2. **Details Tab**:
   - **Display name**: `XAU Trading Bot Service`
   - **Description**: `24/7 XAU/USD Paper Trading Engine`
   - **Startup type**: `Automatic`
3. **I/O Tab** (Logging):
   - **Output (stdout)**: `C:\TradingBot\xau_algo\logs\nssm_stdout.log`
   - **Error (stderr)**: `C:\TradingBot\xau_algo\logs\nssm_stderr.log`
4. Click **Install service**.

*Alternatively, install via single Command Line:*
```cmd
nssm install XAUTradingBot "C:\TradingBot\xau_algo\venv\Scripts\python.exe" "main.py --mode paper"
nssm set XAUTradingBot AppDirectory "C:\TradingBot\xau_algo"
nssm set XAUTradingBot AppStdout "C:\TradingBot\xau_algo\logs\nssm_stdout.log"
nssm set XAUTradingBot AppStderr "C:\TradingBot\xau_algo\logs\nssm_stderr.log"
nssm set XAUTradingBot Start SERVICE_AUTO_START
```

### Step 2.3: Start & Manage the Service
In Admin Command Prompt or PowerShell:
```cmd
nssm start XAUTradingBot
```

**Commands to Control Service:**
- Check Status: `nssm status XAUTradingBot` or `sc query XAUTradingBot`
- Stop Service: `nssm stop XAUTradingBot`
- Restart Service: `nssm restart XAUTradingBot`
- GUI Edit: `nssm edit XAUTradingBot`

*(Optional) If you also want to run the API server on Windows 24/7, create a second service:*
```cmd
nssm install XAUTradingAPI "C:\TradingBot\xau_algo\venv\Scripts\python.exe" "main.py --mode api"
nssm set XAUTradingAPI AppDirectory "C:\TradingBot\xau_algo"
nssm start XAUTradingAPI
```

---

## 3. Method 2: Deploy 24/7 using Windows Task Scheduler

If you cannot install NSSM, you can use Windows Task Scheduler to launch the script on Windows boot.

1. Open **Task Scheduler** (`taskschd.msc`).
2. Click **Create Task** (on the right pane).
3. **General Tab**:
   - Name: `XAU Trading Bot`
   - Check `[X] Run whether user is logged on or not`
   - Check `[X] Run with highest privileges`
   - Configure for: `Windows 10` / `Windows Server`
4. **Triggers Tab**:
   - New -> **At startup**.
5. **Actions Tab**:
   - New -> **Start a program**
   - Program/script: `C:\TradingBot\xau_algo\venv\Scripts\python.exe`
   - Add arguments: `main.py --mode paper`
   - Start in: `C:\TradingBot\xau_algo`
6. **Settings Tab**:
   - Check `[X] If the task fails, restart every:` -> `1 minute` (Attempt to restart up to 3 times or indefinitely).
   - Uncheck `[ ] Stop the task if it runs longer than: 3 days`.
7. Click **OK** and enter your Windows Administrator password.
8. Right-click the newly created task and click **Run**.

---

## 4. Configure AWS Security Group & Windows Firewall for `/health` Monitoring

To monitor your bot using UptimeRobot or access `/health` remotely:

### Step 4.1: AWS Security Group (AWS Console)
1. Go to **AWS EC2 Console** -> **Instances** -> Select your Windows VM.
2. Under the **Security** tab, click your **Security Group**.
3. Edit **Inbound Rules**:
   - **Type**: Custom TCP
   - **Port Range**: `8080` (and `8000` if API is enabled)
   - **Source**: `0.0.0.0/0` (or your IP / UptimeRobot IPs)
4. Save rules.

### Step 4.2: Windows Defender Firewall Rule
Run in **PowerShell (Administrator)** on the Windows VM:
```powershell
New-NetFirewallRule -DisplayName "XAU Bot Health Port" -Direction Inbound -LocalPort 8080 -Protocol TCP -Action Allow
New-NetFirewallRule -DisplayName "XAU Bot API Port" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
```

---

## 5. Verify & Setup Uptime Robot Monitoring

Test public access from your local machine or browser:
```bash
curl http://<YOUR_AWS_VM_PUBLIC_IP>:8080/health
```

Expected JSON response:
```json
{
  "status": "healthy",
  "application": "running",
  "twelve_data": "connected",
  "database": "connected",
  "market_data": "fresh",
  "symbol": "XAU/USD"
}
```

### Setup UptimeRobot Monitoring
1. Go to [UptimeRobot.com](https://uptimerobot.com/).
2. Add New Monitor:
   - **Type**: HTTP(s)
   - **Name**: AWS XAU Paper Trading Bot
   - **URL**: `http://<YOUR_AWS_VM_PUBLIC_IP>:8080/health`
   - **Interval**: 1 to 5 minutes
3. Save monitor to receive instant SMS/Email alerts if the server or WebSocket disconnects!
