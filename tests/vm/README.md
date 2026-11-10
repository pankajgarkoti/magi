# MAGI VM test

A full install on a real Fedora 44 Workstation, in QEMU/KVM, headless.

1. **Install Fedora 44 Workstation unattended** from the official netinstall ISO
   (`Fedora-Everything-netinst-x86_64-44-*.iso`; check it against the signed `CHECKSUM`):
   ```bash
   mkdir -p ~/vm/magi-test/{boot,ks} && cd ~/vm/magi-test
   7z e -oboot netinst.iso images/pxeboot/vmlinuz images/pxeboot/initrd.img
   cp <repo>/tests/vm/ks.cfg ks/ && genisoimage -V OEMDRV -o ks/oemdrv.iso ks/ks.cfg
   qemu-img create -f qcow2 disk.qcow2 40G
   qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 4 -m 4096 \
     -drive file=disk.qcow2,if=virtio -cdrom netinst.iso \
     -drive file=ks/oemdrv.iso,if=virtio,media=cdrom,readonly=on \
     -kernel boot/vmlinuz -initrd boot/initrd.img \
     -append "inst.stage2=hd:LABEL=Fedora-E-dvd-x86_64-44 inst.ks=hd:LABEL=OEMDRV:/ks.cfg inst.text console=ttyS0" \
     -netdev user,id=n0 -device virtio-net-pci,netdev=n0 -display none -serial file:install.log -no-reboot
   ```
   `ks.cfg` creates the user `pilot` (password `magi`, passwordless sudo; test VM only).
2. **Boot it**: `tests/vm/run-vm.sh` (virtio-vga-gl rendered off-screen with egl-headless,
   ssh on `localhost:2222`, QMP socket for `screendump`).
3. **Install MAGI** inside the VM, exactly as a user would:
   `bash <(curl -fsSL https://raw.githubusercontent.com/pankajgarkoti/magi/main/install.sh)`
4. **Session setup for screenshots** (test only): `tests/vm/vm-session-setup.sh` turns on GDM
   autologin into Hyprland and adds simulated Wi-Fi networks; then reboot.
5. Screenshots are taken **inside the VM** with `grim` over the VM's own Hyprland session.

## Login PATH regression test (both install paths)

`test-login-path.sh` runs in the VM as the desktop user after a real reboot and GDM
(auto)login. It checks that a command run through Hyprland's own exec (the path a key binding
takes) and waybar (whose on-click commands inherit its environment) both resolve `eva-wifi`,
`magi`, `magi-overview` and `wall-next`, and that the `eva-wifi`, `magi` and `magi-overview`
daemons are running. It prints the PATH GDM gave Hyprland.

- install.sh VM: sync a checkout to `~/.local/share/magi`, `./install.sh --update`, reboot, run.
  To see it fail, comment out the `hl.env("PATH", ...)` line and move
  `~/.config/environment.d/50-magi-path.conf` away, then reboot.
- Distro VM: the image links every MAGI tool into `/usr/bin`. To see it fail, build a variant
  without that (and without the PATH lines): `--build-arg MAGI_PATH_FIX=none` (or `usrbin`:
  /usr/bin links only), push it to the local registry, `sudo bootc switch` to it, reboot, run.

## MAGI distro VM

1. Build the image and a test ISO (`distro-ks.cfg`: unattended, user `pilot`/`magi`,
   GDM autologin, updates from the host's local registry):
   ```bash
   podman build -f distro/Containerfile -t localhost/magi:latest .
   podman run -d --name magi-registry -p 127.0.0.1:5000:5000 docker.io/library/registry:2
   podman push --tls-verify=false localhost/magi:latest 127.0.0.1:5000/magi:latest
   distro/build-iso.sh --ks tests/vm/distro-ks.cfg --ref 10.0.2.2:5000/magi:latest \
     --boot-iso ~/vm/magi-test/dl/netinst.iso --out ~/vm/magi-distro/magi-test.iso
   ```
2. Install: `tests/vm/run-distro-vm.sh --iso ~/vm/magi-distro/magi-test.iso` (powers off when
   done), then boot from disk: `tests/vm/run-distro-vm.sh` (ssh on `localhost:2223`).
3. Agent layer: copy `tests/agent/{agent-stub,install-stub.sh}` and `tests/vm/test-agent.sh`
   to `/var/tmp/magi-test/` in the VM, then `AGENT=claude test-agent.sh setup`, reboot,
   `AGENT=claude test-agent.sh reboot`; with the VM's link down (QMP `set_link n0 off`),
   `test-agent.sh offline`. `test-agent.sh reset` clears the agent layer, then repeat with
   `AGENT=opencode` (or `codex`). The stub stands in for each agent; nothing needs an account.

Without a VM, `tests/agent/test-adapters.sh` runs the same adapter checks (install, sign-in,
states, spawn, snapshot/restore, reaper) for all four adapters in a throwaway HOME, and
`tests/agent/test-opencode-real.sh` / `test-codex-real.sh` run the real binaries headless
against `tests/agent/fake-provider.py`.
4. Update and rollback: push a second build to `127.0.0.1:5000/magi:latest`, then in the VM
   `sudo bootc upgrade`, reboot, check `/usr/share/magi/IMAGE_VERSION`, `sudo bootc rollback`,
   reboot, check again.
