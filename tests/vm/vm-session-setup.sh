#!/usr/bin/env bash
# TEST VM ONLY (never part of install.sh): run inside the MAGI test VM after install.sh.
#  - GDM autologin straight into the Hyprland session, so screenshots need no typing
#  - simulated Wi-Fi (mac80211_hwsim radios + hostapd) with made-up networks, so the
#    eva-wifi picker has something to show in a VM that only has virtual ethernet
set -euo pipefail
U=${1:-pilot}
# mac80211_hwsim lives in kernel-modules-internal, not in the default kernel modules
sudo dnf -y -q install hostapd "kernel-modules-internal-$(uname -r)" >/dev/null

# autologin into Hyprland
sudo tee /etc/gdm/custom.conf >/dev/null <<EOF
[daemon]
AutomaticLoginEnable=True
AutomaticLogin=$U
EOF
sudo mkdir -p /var/lib/AccountsService/users
sudo tee /var/lib/AccountsService/users/$U >/dev/null <<EOF
[User]
Session=hyprland
XSession=hyprland
SystemAccount=false
EOF

# fake Wi-Fi: wlan0/wlan1 are access points, NetworkManager scans with wlan2
sudo tee /etc/modules-load.d/magi-hwsim.conf >/dev/null <<<'mac80211_hwsim'
sudo tee /etc/modprobe.d/magi-hwsim.conf >/dev/null <<<'options mac80211_hwsim radios=3'
sudo tee /etc/NetworkManager/conf.d/90-magi-hwsim.conf >/dev/null <<'EOF'
[keyfile]
unmanaged-devices=interface-name:wlan0;interface-name:wlan1
EOF
sudo mkdir -p /etc/hostapd
sudo tee /etc/hostapd/magi-wlan0.conf >/dev/null <<'EOF'
interface=wlan0
driver=nl80211
ssid=Home-5G
hw_mode=g
channel=6
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
wpa_passphrase=third-impact
EOF
sudo tee /etc/hostapd/magi-wlan1.conf >/dev/null <<'EOF'
interface=wlan1
driver=nl80211
ssid=Cafe-Guest
hw_mode=g
channel=11
EOF
sudo tee /etc/systemd/system/magi-fake-ap@.service >/dev/null <<'EOF'
[Unit]
Description=MAGI test VM: simulated Wi-Fi access point on %i
After=sys-subsystem-net-devices-%i.device NetworkManager.service
[Service]
ExecStartPre=/usr/sbin/modprobe mac80211_hwsim radios=3
ExecStart=/usr/sbin/hostapd /etc/hostapd/magi-%i.conf
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable magi-fake-ap@wlan0 magi-fake-ap@wlan1 >/dev/null 2>&1
echo "VM session setup done: reboot to autologin into Hyprland"
