#!/bin/bash

# Start - disse ting skal først gøres manuelt i raspi-config
#
# Aktiver SSH og I2C
#
# Efter dette er gjort kan dette script køres
#


# Formålet med dette script er at klargøre vores raspberry pi til at køre vores kode
# i chromium kiosk, med firewall setup og beskyttelse mod keyboard interupts

set -e  # Stop ved fejl

echo "Starter kiosk-mode script"

# Variabler der skal tilpasse til det ønskede

# Admin bruger - den man SSH'er til og som har sudo rettigheder og koden
ADMIN_USER="admin"

# Kiosk bruger - den bruger der viser Chromium og ikke har sudo rettigheder
KIOSK_USER="kiosk"

# FastAPI konfiguration
FASTAPI_WORKDIR="/home/$ADMIN_USER/PythonKode"

FASTAPI_PYTHON="/usr/bin/python3"

# URL til vores webserver
WEBAPP_URL="http://localhost:8000"

# WiFi AP interface - på RPi5 er det wlan0
WLAN_INTERFACE="wlan0"

# SSH port
SSH_PORT="22"

echo "Validerer konfiguration"

# Tjek at script køres som root
if [[ $EUID -ne 0 ]]; then
   echo "FEJL: Dette script skal køres som root"
   exit 1
fi

# Tjek at FastAPI directory eksisterer
if [ ! -d "$FASTAPI_WORKDIR" ]; then
    echo "FEJL: FastAPI directory eksisterer ikke: $FASTAPI_WORKDIR"
    exit 1
fi

# Tjek at main.py eksisterer
if [ ! -f "$FASTAPI_WORKDIR/main.py" ]; then
    echo "FEJL: main.py ikke fundet i: $FASTAPI_WORKDIR"
    exit 1
fi

# Tjek at Python eksisterer
if [ ! -f "$FASTAPI_PYTHON" ]; then
    echo "FEJL: Python executable ikke fundet: $FASTAPI_PYTHON"
    exit 1
fi

# Tjek at admin user eksisterer
if ! id "$ADMIN_USER" &>/dev/null; then
    echo "FEJL: Admin bruger '$ADMIN_USER' eksisterer ikke"
    exit 1
fi

# Så laver vi tracking til vores fejlhåndtering

PACKAGES_INSTALLED=false
KIOSK_USER_CREATED=false
FASTAPI_SERVICE_CREATED=false
AUTOLOGIN_CONFIGURED=false
KIOSK_SCRIPT_CREATED=false
FIREWALL_CONFIGURED=false
SSH_CONFIGURED=false

# Ved fejl rydder vi op

cleanup_on_error() {
    echo "FEJL OPSTÅET - Nulstiller"
    
    if [ "$SSH_CONFIGURED" = true ]; then
        echo "  Gendanner SSH config"
        cp /etc/ssh/sshd_config.backup /etc/ssh/sshd_config 2>/dev/null || true
        systemctl restart ssh 2>/dev/null || true
    fi
    
    if [ "$FIREWALL_CONFIGURED" = true ]; then
        echo "  Deaktiverer firewall"
        ufw --force disable 2>/dev/null || true
    fi
    
    if [ "$KIOSK_SCRIPT_CREATED" = true ]; then
        echo "  Fjerner kiosk scripts"
        rm -rf /home/$KIOSK_USER/.config 2>/dev/null || true
        rm -f /home/$KIOSK_USER/start-kiosk.sh 2>/dev/null || true
        rm -f /home/$KIOSK_USER/.xbindkeysrc 2>/dev/null || true
        rm -f /home/$KIOSK_USER/.xinitrc 2>/dev/null || true
        rm -f /home/$KIOSK_USER/.profile 2>/dev/null || true
    fi
    
    if [ "$AUTOLOGIN_CONFIGURED" = true ]; then
        echo "  Fjerner auto-login"
        rm -rf /etc/systemd/system/getty@tty1.service.d 2>/dev/null || true
    fi
    
    if [ "$FASTAPI_SERVICE_CREATED" = true ]; then
        echo "  Fjerner FastAPI service"
        systemctl stop fastapi-app.service 2>/dev/null || true
        systemctl disable fastapi-app.service 2>/dev/null || true
        rm -f /etc/systemd/system/fastapi-app.service 2>/dev/null || true
        systemctl daemon-reload 2>/dev/null || true
    fi
    
    if [ "$KIOSK_USER_CREATED" = true ]; then
        echo "  Sletter kiosk bruger"
        userdel -r $KIOSK_USER 2>/dev/null || true
    fi
    
    echo "Script nulstillet - ret fejl og prøv igen"
    exit 1
}

trap cleanup_on_error ERR

echo "Step 1/10: Opdaterer systemet og installerer nødvendige pakker"

sudo apt update
sudo apt upgrade -y
sudo apt install -y chromium unclutter xbindkeys ufw fail2ban xdotool openbox

PACKAGES_INSTALLED=true

echo "Step 2/10: Opretter kiosk user"

if id "$KIOSK_USER" &>/dev/null; then
    echo "  Bruger $KIOSK_USER eksisterer allerede"
else
    adduser --disabled-password --gecos "" "$KIOSK_USER"
    passwd -d "$KIOSK_USER"
    KIOSK_USER_CREATED=true
fi

echo "Step 3/10: Opretter FastAPI service"

cat > /etc/systemd/system/fastapi-app.service << EOF
[Unit]
Description=FastAPI Application
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$FASTAPI_USER
WorkingDirectory=$FASTAPI_WORKDIR
ExecStart=$FASTAPI_PYTHON main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
PrivateTmp=true
ReadOnlyPaths=/etc /usr
ProtectSystem=strict
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable fastapi-app.service

FASTAPI_SERVICE_CREATED=true

echo "Step 4/10: Indstiller auto-login"

# Getty auto-login konfiguration
mkdir -p /etc/systemd/system/getty@tty1.service.d/
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $KIOSK_USER --noclear %I \$TERM
EOF

# .profile til auto-start af X
cat > /home/$KIOSK_USER/.profile << 'EOF'
if [[ -z $DISPLAY ]] && [[ $(tty) = /dev/tty1 ]]; then
    startx -- -nocursor
fi
EOF

# .xinitrc til at starte kiosk script
cat > /home/$KIOSK_USER/.xinitrc << EOF
#!/bin/bash
exec /home/$KIOSK_USER/start-kiosk.sh
EOF

chmod +x /home/$KIOSK_USER/.xinitrc
chown $KIOSK_USER:$KIOSK_USER /home/$KIOSK_USER/.profile /home/$KIOSK_USER/.xinitrc

AUTOLOGIN_CONFIGURED=true

echo "Step 5/10: Opretter kiosk startup script"

cat > /home/$KIOSK_USER/start-kiosk.sh << EOF
#!/bin/bash

# Start openbox window manager
openbox --config-file /home/$KIOSK_USER/.config/openbox/rc.xml &
sleep 2

# Disable alle keyboards i X session
for device in \$(xinput list | grep -i "keyboard" | grep -v "Virtual" | grep -o 'id=[0-9]*' | cut -d= -f2); do
    xinput disable \$device 2>/dev/null
done

# Start xbindkeys som backup
xbindkeys 2>/dev/null &
sleep 1

# Vent på netværk
while ! ping -c 1 -W 1 localhost &> /dev/null; do 
    sleep 1
done

# Vent på FastAPI
for i in {1..60}; do
    if curl -s $WEBAPP_URL > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Konfigurer skærm - ingen dvale
xset s off
xset -dpms
xset s noblank

# Skjul musen
unclutter -idle 0 -root &

# Start Chromium i uendelig loop med ønsket indstillinger
while true; do
    chromium \\
        --kiosk \\
        --noerrdialogs \\
        --disable-infobars \\
        --no-first-run \\
        --disable-session-crashed-bubble \\
        --disable-translate \\
        --check-for-update-interval=2592000 \\
        --disable-features=TranslateUI \\
        --start-fullscreen \\
        --window-position=0,0 \\
        --disable-pinch \\
        --overscroll-history-navigation=0 \\
        $WEBAPP_URL
    
    # Hvis Chromium lukkes, vent og genstart
    sleep 2
done
EOF

chmod +x /home/$KIOSK_USER/start-kiosk.sh
chown $KIOSK_USER:$KIOSK_USER /home/$KIOSK_USER/start-kiosk.sh

echo "Step 6/10: Konfigurerer Openbox"

mkdir -p /home/$KIOSK_USER/.config/openbox
cat > /home/$KIOSK_USER/.config/openbox/rc.xml << 'EOF'
<?xml version="1.0"?>
<openbox_config>
  <keyboard>
    <chainQuitKey></chainQuitKey>
  </keyboard>
  <applications>
    <application class="*">
      <decor>no</decor>
      <fullscreen>yes</fullscreen>
    </application>
  </applications>
</openbox_config>
EOF

chown -R $KIOSK_USER:$KIOSK_USER /home/$KIOSK_USER/.config/

echo "Step 7/10: Konfigurerer xbindkeys"

cat > /home/$KIOSK_USER/.xbindkeysrc << 'EOF'
"true"
  Alt+F4
"true"
  Control+Alt+Delete
"true"
  Control+Alt+BackSpace
"true"
  Alt+Tab
"true"
  Super_L
"true"
  F11
EOF

chown $KIOSK_USER:$KIOSK_USER /home/$KIOSK_USER/.xbindkeysrc

KIOSK_SCRIPT_CREATED=true

echo "Step 8/10: Konfigurerer firewall"

# Sætter en baseline
ufw default deny incoming
ufw default deny forward
ufw default allow outgoing

# SSH
ufw allow $SSH_PORT/tcp

# MQTT på WiFi AP
ufw allow in on $WLAN_INTERFACE from any to any port 1883

# DNS for WiFi AP
ufw allow in on $WLAN_INTERFACE from any to any port 53

# DHCP for WiFi AP
ufw allow in on $WLAN_INTERFACE from any to any port 67 proto udp

# Localhost
ufw allow in on lo

# Aktiver firewall
ufw --force enable

FIREWALL_CONFIGURED=true

echo "Step 9/10: Tilpasser SSH"

# Backup original config
cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup

# Tilføj sikkerhedsindstillinger
cat >> /etc/ssh/sshd_config << EOF

# Kiosk setup sikkerhed
PermitRootLogin no
AllowUsers $ADMIN_USER
X11Forwarding no
ClientAliveInterval 300
ClientAliveCountMax 2
EOF

systemctl restart ssh

SSH_CONFIGURED=true

# Ekstra sikkerhed

echo "Step 10/10: Opsætter ekstra sikkerhed"

# Fail2ban - bruges til at opdage bots der prøver at brute-force
systemctl enable fail2ban
systemctl start fail2ban

# Slår VT Switching fra - dette gør at vi ikke kan køre flere virtuale terminaler (tty)
mkdir -p /etc/X11/xorg.conf.d/
cat > /etc/X11/xorg.conf.d/10-novtswitch.conf << 'EOF'
Section "ServerFlags"
    Option "DontVTSwitch" "true"
    Option "DontZap" "true"
EndSection
EOF

# Mask andre TTY'er
systemctl mask getty@tty2.service
systemctl mask getty@tty3.service
systemctl mask getty@tty4.service
systemctl mask getty@tty5.service
systemctl mask getty@tty6.service

# Automatiske sikkerhedsopdateringer
apt install -y unattended-upgrades
cat > /etc/apt/apt.conf.d/20auto-upgrades << 'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

# Fjern error trap - alt er gået godt
trap - ERR

echo "Installation komplet"
systemctl start fastapi-app.service
sleep 2

# Verificer at fastapi kører
if systemctl is-active --quiet fastapi-app.service; then
    echo "FastAPI kører"
else
    echo "ADVARSEL: FastAPI startede ikke korrekt"
    echo "Tjek logs efter reboot med:"
    echo "sudo journalctl -u fastapi-app.service"
fi

sleep 5

reboot
