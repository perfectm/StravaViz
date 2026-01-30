# Deployment Guide - Hostinger Ubuntu VPS

This guide covers deploying StravaViz on a Hostinger Ubuntu VPS with nginx, systemd, and SSL.

## Prerequisites

- Ubuntu VPS (20.04 or later)
- Domain name pointed to your VPS IP
- SSH access to your server
- Strava API credentials

## Server Setup

### 1. Initial Server Connection

```bash
# SSH into your VPS
ssh root@your-server-ip

# Update system packages
apt update && apt upgrade -y
```

### 2. Create Application User

```bash
# Create a non-root user for running the app
adduser deployuser
usermod -aG sudo deployuser

# Switch to the new user
su - deployuser
```

### 3. Install Required Software

```bash
# Install Python 3.11+ and pip
sudo apt install -y python3 python3-pip python3-venv

# Install nginx
sudo apt install -y nginx

# Install git
sudo apt install -y git

# Install certbot for SSL (Let's Encrypt)
sudo apt install -y certbot python3-certbot-nginx
```

## Application Deployment

### 1. Clone Repository

```bash
# Clone to /opt directory
sudo mkdir -p /opt/StravaViz
sudo chown deployuser:deployuser /opt/StravaViz
git clone https://github.com/perfectm/StravaViz.git /opt/StravaViz
cd /opt/StravaViz
```

### 2. Set Up Python Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment Variables

```bash
# Copy example env file
cp .env.example .env

# Edit with your credentials
nano .env
```

Add your Strava API credentials:
```bash
STRAVA_CLIENT_ID=your_actual_client_id
STRAVA_CLIENT_SECRET=your_actual_client_secret
STRAVA_REFRESH_TOKEN=your_actual_refresh_token
STRAVA_CLUB_ID=1577284
```

**Important**: Update your Strava App settings at https://developers.strava.com/:
- **Authorization Callback Domain**: Add your domain (e.g., `stravadashboard.com`)
- **Website**: Your site URL

### 4. Test the Application

```bash
# Test run (should start successfully)
uvicorn strava_fastapi:app --host 0.0.0.0 --port 8002

# Press Ctrl+C to stop after verifying it works
```

## systemd Service Setup

### 1. Create systemd Service File

```bash
sudo nano /etc/systemd/system/stravaviz.service
```

Add the following content:
```ini
[Unit]
Description=StravaViz FastAPI Application
After=network.target

[Service]
Type=simple
User=deployuser
Group=deployuser
WorkingDirectory=/opt/StravaViz
Environment="PATH=/opt/StravaViz/venv/bin"
ExecStart=/opt/StravaViz/venv/bin/uvicorn strava_fastapi:app --host 127.0.0.1 --port 8002
Restart=always
RestartSec=3

StandardOutput=append:/opt/StravaViz/production.log
StandardError=append:/opt/StravaViz/error.log

[Install]
WantedBy=multi-user.target
```

### 2. Enable and Start Service

```bash
# Reload systemd to recognize new service
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable stravaviz

# Start the service
sudo systemctl start stravaviz

# Check status
sudo systemctl status stravaviz
```

### 3. Verify Service is Running

```bash
# Check if app is listening on port 8002
curl http://localhost:8002

# View logs
tail -f /opt/StravaViz/production.log
```

## Nginx Configuration

### 1. Create Nginx Site Configuration

```bash
sudo nano /etc/nginx/sites-available/stravaviz
```

Add the following (replace `yourdomain.com` with your actual domain):
```nginx
server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;

    # Logging
    access_log /var/log/nginx/stravaviz_access.log;
    error_log /var/log/nginx/stravaviz_error.log;

    # Proxy to FastAPI app
    location / {
        proxy_pass http://127.0.0.1:8002;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support (if needed later)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Timeout settings
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # Static file caching (optional)
    location ~* \.(jpg|jpeg|png|gif|ico|css|js)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
}
```

### 2. Enable Site and Test Configuration

```bash
# Create symbolic link to enable site
sudo ln -s /etc/nginx/sites-available/stravaviz /etc/nginx/sites-enabled/

# Remove default nginx site (optional)
sudo rm /etc/nginx/sites-enabled/default

# Test nginx configuration
sudo nginx -t

# If test passes, reload nginx
sudo systemctl reload nginx
```

### 3. Configure Firewall

```bash
# Allow nginx through firewall
sudo ufw allow 'Nginx Full'

# Allow SSH (important!)
sudo ufw allow OpenSSH

# Enable firewall
sudo ufw enable

# Check status
sudo ufw status
```

## SSL Certificate Setup (Let's Encrypt)

### 1. Obtain SSL Certificate

```bash
# Run certbot for nginx
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com

# Follow prompts:
# - Enter email address
# - Agree to terms
# - Choose whether to redirect HTTP to HTTPS (recommended: yes)
```

### 2. Test Auto-Renewal

```bash
# Dry run to test renewal
sudo certbot renew --dry-run
```

Certbot will automatically renew certificates before expiry.

### 3. Verify SSL Configuration

Visit `https://yourdomain.com` in your browser - you should see a secure connection with a valid certificate.

## Database Setup

### 1. Initialize Database

```bash
cd /opt/StravaViz

# Activate virtual environment
source venv/bin/activate

# The database will be created automatically on first run
# But you can verify:
python3 -c "from strava_fastapi import init_db; init_db()"
```

### 2. Set Proper Permissions

```bash
# Ensure database file is writable
chmod 644 strava_activities.db

# Ensure directory is writable
chmod 755 /opt/StravaViz
```

## Post-Deployment Tasks

### 1. Update Strava OAuth Callback URL

Go to https://developers.strava.com/ and update your app settings:
- **Authorization Callback Domain**: `yourdomain.com`
- Update `.env` if implementing multi-user (Phase 2):
  ```bash
  OAUTH_REDIRECT_URI=https://yourdomain.com/auth/callback
  ```

### 2. Test All Endpoints

```bash
# Test main dashboard
curl https://yourdomain.com/

# Test club dashboard
curl https://yourdomain.com/club
```

### 3. Monitor Logs

```bash
# Application logs
tail -f /opt/StravaViz/production.log

# Nginx access logs
sudo tail -f /var/log/nginx/stravaviz_access.log

# Nginx error logs
sudo tail -f /var/log/nginx/stravaviz_error.log

# System logs for the service
sudo journalctl -u stravaviz -f
```

## Maintenance

### Update Application

```bash
# SSH into server
ssh deployuser@your-server-ip

# Navigate to app directory
cd /opt/StravaViz

# Pull latest changes
git pull origin main

# Activate virtual environment
source venv/bin/activate

# Update dependencies if needed
pip install -r requirements.txt

# Restart service
sudo systemctl restart stravaviz

# Check status
sudo systemctl status stravaviz
```

### Backup Database

```bash
# Create backup script
nano ~/backup_strava.sh
```

Add:
```bash
#!/bin/bash
BACKUP_DIR="/opt/StravaViz/backups"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR
cp /opt/StravaViz/strava_activities.db $BACKUP_DIR/strava_activities_$DATE.db

# Keep only last 7 backups
ls -t $BACKUP_DIR/strava_activities_*.db | tail -n +8 | xargs -r rm
```

Make executable and add to crontab:
```bash
chmod +x ~/backup_strava.sh

# Edit crontab
crontab -e

# Add daily backup at 2 AM
0 2 * * * /home/deployuser/backup_strava.sh
```

### Monitor Disk Space

```bash
# Check disk usage
df -h

# Check database size
du -h /opt/StravaViz/strava_activities.db

# Check log file sizes
du -h /opt/StravaViz/*.log
```

### Rotate Logs

```bash
# Create logrotate config
sudo nano /etc/logrotate.d/stravaviz
```

Add:
```
/opt/StravaViz/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 deployuser deployuser
    postrotate
        systemctl reload stravaviz > /dev/null 2>&1 || true
    endscript
}
```

## Security Hardening

### 1. Secure SSH

```bash
sudo nano /etc/ssh/sshd_config
```

Recommended settings:
```
PermitRootLogin no
PasswordAuthentication no  # Use SSH keys only
Port 2222  # Change default SSH port (optional)
```

Restart SSH:
```bash
sudo systemctl restart ssh
```

### 2. Install Fail2Ban

```bash
# Install fail2ban to prevent brute force attacks
sudo apt install -y fail2ban

# Start and enable
sudo systemctl start fail2ban
sudo systemctl enable fail2ban
```

### 3. Keep System Updated

```bash
# Enable automatic security updates
sudo apt install -y unattended-upgrades

# Configure
sudo dpkg-reconfigure -plow unattended-upgrades
```

## Troubleshooting

### Service Won't Start

```bash
# Check service status
sudo systemctl status stravaviz

# View detailed logs
sudo journalctl -u stravaviz -n 50 --no-pager

# Check if port is already in use
sudo lsof -i :8002

# Test app manually
cd /opt/StravaViz
source venv/bin/activate
uvicorn strava_fastapi:app --host 127.0.0.1 --port 8002
```

### Nginx Issues

```bash
# Test nginx configuration
sudo nginx -t

# Check nginx status
sudo systemctl status nginx

# View nginx error logs
sudo tail -f /var/log/nginx/error.log

# Restart nginx
sudo systemctl restart nginx
```

### Database Permissions

```bash
# If you see permission errors
cd /opt/StravaViz
sudo chown deployuser:deployuser strava_activities.db
chmod 644 strava_activities.db
```

### SSL Certificate Issues

```bash
# Check certificate status
sudo certbot certificates

# Renew manually
sudo certbot renew

# Check nginx SSL configuration
sudo nano /etc/nginx/sites-available/stravaviz
```

## Performance Optimization

### 1. Enable Gzip Compression in Nginx

```bash
sudo nano /etc/nginx/nginx.conf
```

Ensure this is uncommented in the `http` block:
```nginx
gzip on;
gzip_vary on;
gzip_proxied any;
gzip_comp_level 6;
gzip_types text/plain text/css text/xml text/javascript application/json application/javascript application/xml+rss;
```

### 2. Increase Uvicorn Workers (for more traffic)

Edit the systemd service:
```bash
sudo nano /etc/systemd/system/stravaviz.service
```

Change ExecStart line to:
```
ExecStart=/opt/StravaViz/venv/bin/uvicorn strava_fastapi:app --host 127.0.0.1 --port 8002 --workers 4
```

Reload and restart:
```bash
sudo systemctl daemon-reload
sudo systemctl restart stravaviz
```

## Monitoring

### 1. Install htop for System Monitoring

```bash
sudo apt install -y htop

# Run to view system resources
htop
```

### 2. Monitor Application

```bash
# Real-time log monitoring
tail -f /opt/StravaViz/production.log

# Check service resource usage
systemctl status stravaviz

# View all service logs
sudo journalctl -u stravaviz --since today
```

## Quick Command Reference

```bash
# Restart application
sudo systemctl restart stravaviz

# View application logs
tail -f /opt/StravaViz/production.log

# Restart nginx
sudo systemctl restart nginx

# Pull latest code
cd /opt/StravaViz && git pull && sudo systemctl restart stravaviz

# Check application status
sudo systemctl status stravaviz

# Check nginx status
sudo systemctl status nginx

# Renew SSL certificate
sudo certbot renew

# Backup database
cp /opt/StravaViz/strava_activities.db ~/backup_$(date +%Y%m%d).db
```

## Cost Optimization

For a basic VPS on Hostinger:
- **Minimum Requirements**: 1GB RAM, 1 CPU core, 20GB storage
- **Recommended**: 2GB RAM, 2 CPU cores, 40GB storage
- **For Multi-User (Future)**: 4GB RAM, 2-4 CPU cores, 80GB+ storage

## Support

If you encounter issues:
1. Check the logs (application, nginx, system)
2. Verify all services are running
3. Check firewall settings
4. Verify domain DNS is pointing to correct IP
5. Test locally first before diagnosing server issues

## Next Steps

After deployment:
1. Test all endpoints from multiple devices
2. Monitor logs for any errors
3. Set up monitoring/alerting (optional: UptimeRobot, Pingdom)
4. Plan for implementing multi-user features (see MULTI_USER_PLAN.md)
5. Configure regular backups
6. Document any custom configurations
