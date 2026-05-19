# Deploying WebReach to sms2cartdemo.com (Hostinger VPS)

## Architecture
- Existing: `sms2cartdemo.com` → Next.js app (port 3000)
- New:      `outreach.sms2cartdemo.com` → Flask/WebReach app (port 5001)

Both run on the same VPS. Nginx routes each subdomain to the right app.

---

## Step 1 — SSH into your VPS

In your terminal:
```bash
ssh root@YOUR_VPS_IP
```
(Get IP from Hostinger → VPS → Overview)

---

## Step 2 — Install Python on the VPS

```bash
apt update && apt install -y python3 python3-pip python3-venv
```

---

## Step 3 — Upload the project

From your Windows machine, open a new terminal and run:
```bash
scp -r "C:\Users\Hamza Khan\Desktop\Upwork\14 - Nicholas 2" root@YOUR_VPS_IP:/var/www/webreach
```

Or use FileZilla (SFTP) to drag the folder to `/var/www/webreach`.

---

## Step 4 — Set up Python environment on VPS

```bash
cd /var/www/webreach
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gunicorn
```

---

## Step 5 — Create the .env file on VPS

```bash
cp .env.example .env
nano .env
```

Fill in the same values as your local `.env`. Change `APP_URL`:
```
APP_URL=https://outreach.sms2cartdemo.com
```

---

## Step 6 — Test it runs

```bash
source venv/bin/activate
gunicorn -w 2 -b 127.0.0.1:5001 app:app
# Press Ctrl+C after confirming it starts
```

---

## Step 7 — Create a systemd service (auto-start on reboot)

```bash
nano /etc/systemd/system/webreach.service
```

Paste this:
```ini
[Unit]
Description=WebReach Outreach System
After=network.target

[Service]
User=root
WorkingDirectory=/var/www/webreach
Environment="PATH=/var/www/webreach/venv/bin"
ExecStart=/var/www/webreach/venv/bin/gunicorn -w 2 -b 127.0.0.1:5001 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
systemctl daemon-reload
systemctl enable webreach
systemctl start webreach
systemctl status webreach   # should say "active (running)"
```

---

## Step 8 — Add DNS record for subdomain

In Hostinger → DNS Zone for `sms2cartdemo.com`, add:
```
Type: A
Name: outreach
Value: YOUR_VPS_IP
TTL: 3600
```

Wait 5–10 minutes for DNS to propagate.

---

## Step 9 — Configure Nginx

```bash
nano /etc/nginx/sites-available/webreach
```

Paste:
```nginx
server {
    listen 80;
    server_name outreach.sms2cartdemo.com;

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }
}
```

Enable it:
```bash
ln -s /etc/nginx/sites-available/webreach /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

---

## Step 10 — Add SSL (HTTPS)

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d outreach.sms2cartdemo.com
```

Follow the prompts. Certbot adds HTTPS automatically.

---

## Step 11 — Update GHL Webhook URL

In GHL → Settings → Webhooks, update the webhook URL to:
```
https://outreach.sms2cartdemo.com/api/webhooks/ghl
```

Also update `APP_URL` in `/var/www/webreach/.env`:
```
APP_URL=https://outreach.sms2cartdemo.com
```

Then restart:
```bash
systemctl restart webreach
```

---

## Result

| URL | What it is |
|-----|-----------|
| `https://sms2cartdemo.com` | Existing sms2cart/cartcloser app |
| `https://outreach.sms2cartdemo.com` | WebReach outreach dashboard |

---

## Updating the app after changes

From your Windows machine, upload changed files via SCP/FileZilla, then on the VPS:
```bash
systemctl restart webreach
```
