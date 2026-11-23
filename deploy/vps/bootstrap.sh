#!/usr/bin/env bash
# One-time preparation of a fresh Ubuntu 24.04 VPS, idempotent (safe to run again).
# Run as root once; AFTER that root and password logins are disabled and everything goes
# through the `deploy` user with sudo.
#
# Already applied by hand to the production server on 2026-11-22 (Contabo Cloud VPS 6: 6 vCPU,
# 12 GB RAM, 200 GB SSD, IPv4 <VPS_IP>): this file records what was done so the server
# can be rebuilt. Re-running it on that server changes nothing except possibly the
# unit/package refresh.
#
# Before running on a NEW machine:
#   - put the public SSH key of the owner into /root/.ssh/authorized_keys (the provider's
#     "add SSH key" does that); it is copied to the deploy user;
#   - in the provider panel firewall allow TCP 22, 80, 443 (and UDP 443 if HTTP/3 is wanted);
#   - DNS: A/AAAA for the domain, api and www (no wildcard), CAA letsencrypt.org.
# Not part of this script (by decision): Tailscale. SSH stays open on 22, key only.
#
# Usage: sudo bash deploy/vps/bootstrap.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

[[ "$(id -u)" -eq 0 ]] || { echo "run as root (sudo bash $0)" >&2; exit 1; }

echo "=== 1. deploy user (applied 2026-11-22)"
if ! id deploy >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" deploy
fi
usermod -aG sudo deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
if [[ ! -s /home/deploy/.ssh/authorized_keys && -s /root/.ssh/authorized_keys ]]; then
  install -m 600 -o deploy -g deploy /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
fi
[[ -s /home/deploy/.ssh/authorized_keys ]] || { echo "deploy has no SSH key: refusing to lock root out" >&2; exit 1; }
# Key-only account without a password: sudo without a password prompt.
echo 'deploy ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-deploy
chmod 440 /etc/sudoers.d/90-deploy
visudo -cf /etc/sudoers.d/90-deploy

echo "=== 2. sshd hardening (applied 2026-11-22)"
# sshd takes the first value it reads; 00- sorts before cloud-init's 50- file.
cat > /etc/ssh/sshd_config.d/00-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
AllowUsers deploy
X11Forwarding no
MaxAuthTries 3
EOF
sshd -t
systemctl reload ssh
# The image's default user: locked, not deleted.
if id ubuntu >/dev/null 2>&1; then
  usermod --lock --expiredate 1 ubuntu
fi

echo "=== 3. firewall, updates, fail2ban, time zone, swap (applied 2026-11-22)"
apt-get update -q
apt-get install -y -q ufw unattended-upgrades fail2ban ca-certificates curl

ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH (key only)'
ufw allow 80/tcp comment 'HTTP (ACME, redirect)'
ufw allow 443/tcp comment 'HTTPS'
ufw allow 443/udp comment 'HTTP/3 (also open it in the provider panel)'
ufw --force enable

# Security updates only; reboot at 05:00 UTC when needed.
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
cat > /etc/apt/apt.conf.d/52mars-unattended <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "05:00";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
EOF

cat > /etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled = true
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
EOF
systemctl enable --now fail2ban
systemctl restart fail2ban

timedatectl set-timezone UTC

if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10' > /etc/sysctl.d/90-swappiness.conf
sysctl -q -p /etc/sysctl.d/90-swappiness.conf

echo "=== 4. Docker CE + compose plugin (applied 2026-11-22: Docker 29, compose v5)"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck source=/dev/null
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
mkdir -p /etc/docker
new_daemon="$(mktemp)"
cat > "$new_daemon" <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "5" },
  "live-restore": true
}
EOF
if ! cmp -s "$new_daemon" /etc/docker/daemon.json; then
  install -m 644 "$new_daemon" /etc/docker/daemon.json
  systemctl restart docker
fi
rm -f "$new_daemon"
systemctl enable docker
usermod -aG docker deploy

echo "=== 5. directories"
install -d -m 750 -o deploy -g deploy /srv/mars
install -d -m 700 -o deploy -g deploy /srv/mars/backups /srv/mars/secrets

echo "=== 6. systemd timers (NOT part of the 2026-11-22 manual setup; installed from the repo clone)"
# Needs the clone in /srv/mars/repo (README, "VPS (Contabo) + Caddy", step 4). Skipped until
# it exists; run this script again afterwards, or copy the units by hand.
unit_dir=/srv/mars/repo/deploy/vps/systemd
if [[ -d "$unit_dir" ]]; then
  install -m 644 "$unit_dir"/*.service "$unit_dir"/*.timer /etc/systemd/system/
  systemctl daemon-reload
  # Timers are enabled by the owner once .env and the stack exist (README step 7).
  echo "units installed; enable with: systemctl enable --now mars-backup.timer mars-hostcheck.timer mars-verify-backup.timer"
else
  echo "no clone at /srv/mars/repo yet: units not installed"
fi

echo "=== summary"
ufw status verbose | head -n 12
sshd -T | grep -E "^(passwordauthentication|permitrootlogin|allowusers) "
docker --version
docker compose version
