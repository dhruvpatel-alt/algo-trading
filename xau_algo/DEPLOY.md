# Ubuntu Production Deployment Guide

Guide for deploying the XAU/USD Paper Trading System on an Ubuntu Linux VM using systemd and monitoring via `/health`.

---

## 1. Install System Dependencies

Update packages and install Python 3.10+, pip, git, and venv:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git curl
```

---

## 2. Clone Repository

Clone the project to the server (e.g., under `/home/ubuntu/AlgoTrading`):

```bash
cd /home/ubuntu
git clone <your-repository-url> AlgoTrading
cd AlgoTrading/xau_algo
```

---

## 3. Create Virtual Environment

Create and activate a virtual environment:

```bash
python3 -m venv /home/ubuntu/AlgoTrading/venv
source /home/ubuntu/AlgoTrading/venv/bin/activate
```

---

## 4. Install Project Requirements

Install required packages:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 5. Configure `.env`

Copy `.env.example` to `.env` and fill in API keys and Supabase credentials:

```bash
cp .env.example .env
nano .env
```

Ensure at least `TWELVE_DATA_API_KEY_1` is set:

```env
TWELVE_DATA_API_KEY_1=your_twelve_data_key_here
TWELVE_DATA_API_KEY_2=...
SUPABASE_DB_URL=https://your-project.supabase.co
SUPABASE_DB_KEY=your_supabase_key
SUPABASE_DB_DIRECT_URL=postgresql://...
HEALTH_API_PORT=8080
MAX_TICK_STALENESS_SECONDS=120
```

---

## 6. Test Application Manually

Verify that paper trading starts cleanly and the health endpoint responds:

```bash
python main.py --mode paper
```

In another terminal tab, test the health endpoint:

```bash
curl http://localhost:8080/health
```

Expected output (HTTP 200):
```json
{
  "status": "healthy",
  "application": "running",
  "twelve_data": "connected",
  "database": "connected",
  "market_data": "fresh",
  "symbol": "XAU/USD",
  "last_tick_seconds_ago": 2,
  "uptime_seconds": 15
}
```

Press `Ctrl+C` to cleanly stop the test process.

---

## 7. Install Systemd Service

Copy the systemd service unit file to `/etc/systemd/system/`:

```bash
sudo cp trading-bot.service /etc/systemd/system/trading-bot.service
```

If your installation path or user differs from `/home/ubuntu/AlgoTrading` and `ubuntu`, edit the file:

```bash
sudo nano /etc/systemd/system/trading-bot.service
```

---

## 8. Enable Service

Reload systemd daemon and enable the service to start automatically on VM boot:

```bash
sudo systemctl daemon-reload
sudo systemctl enable trading-bot
```

---

## 9. Start Service

Start the trading bot background service:

```bash
sudo systemctl start trading-bot
```

---

## 10. Check Service Status

Verify the service is active and running:

```bash
sudo systemctl status trading-bot
```

---

## 11. View Logs

Stream application logs in real-time using `journalctl`:

```bash
journalctl -u trading-bot -f
```

You can also view the application log file:

```bash
tail -f /home/ubuntu/AlgoTrading/xau_algo/logs/trading.log
```

---

## 12. Restart Service

To apply updates or restart the process manually:

```bash
sudo systemctl restart trading-bot
```

---

## 13. Stop Service

To stop the service gracefully:

```bash
sudo systemctl stop trading-bot
```

---

## 14. Test Public `/health` Endpoint & UptimeRobot Setup

Ensure port `8080` (or your configured `HEALTH_API_PORT`) is open in your cloud provider security group / firewall (e.g. AWS Security Group or ufw):

```bash
sudo ufw allow 8080/tcp
```

Test from a remote machine or browser:

```bash
curl http://<YOUR_SERVER_PUBLIC_IP>:8080/health
```

### UptimeRobot Configuration
1. Go to [UptimeRobot](https://uptimerobot.com/).
2. Create a new monitor:
   - **Monitor Type**: HTTP(s)
   - **Friendly Name**: XAU Trading Bot Health
   - **URL (or IP)**: `http://<YOUR_SERVER_PUBLIC_IP>:8080/health`
   - **Monitoring Interval**: Every 1 to 5 minutes
3. Save the monitor.

> **Note**: UptimeRobot is solely for alert monitoring. Operating system process recovery is automatically handled by systemd (`Restart=always` with `RestartSec=10`).

---

## 15. Deploying to Render (Blueprint)

You can easily deploy the Chart API (and optionally the Trading Engine) to [Render](https://render.com/) using the provided `render.yaml` Blueprint.

### Prerequisites
- A GitHub repository containing the source code.
- A Supabase database provisioned and accessible.

### Steps
1. In the project root, ensure you have the `render.yaml` file (it is already created for you).
2. Go to your [Render Dashboard](https://dashboard.render.com/).
3. Click on **New +** and select **Blueprint**.
4. Connect your GitHub repository.
5. Render will automatically detect the `render.yaml` Blueprint and propose creating two services:
   - **xau-chart-api**: A Web Service running `python main.py --mode api`.
   - **xau-trading-engine**: A Background Worker running `python main.py --mode paper`.
6. Click **Apply** to create the services.
7. Go to each service's **Environment** settings in the Render dashboard and securely input the missing values for the environment variables (`SUPABASE_DB_URL`, `SUPABASE_DB_KEY`, `SUPABASE_DB_DIRECT_URL`, `TWELVE_DATA_API_KEY_1`, etc.).
8. Trigger a manual deploy if necessary.

The API will automatically expose port `8000` to the internet (Render automatically routes web service traffic) and you can connect your frontend directly to the provided `*.onrender.com` domain.