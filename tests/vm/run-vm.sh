#!/usr/bin/env bash
# Boot the MAGI test VM (Fedora 44 Workstation installed from tests/vm/ks.cfg) headless.
# GPU: virtio-vga-gl rendered off-screen (egl-headless; the localhost-only VNC display makes
# QEMU read frames back, so QMP screendump works), so Hyprland gets real GL and nothing
# opens on the host desktop. ssh: localhost:2222 (user pilot). QMP socket for screendumps.
#   VM_DIR=~/vm/magi-test tests/vm/run-vm.sh
set -euo pipefail
VM_DIR=${VM_DIR:-$HOME/vm/magi-test}
cd "$VM_DIR"
exec qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 4 -m 4096 \
  -drive file=disk.qcow2,if=virtio \
  -device virtio-vga-gl,xres=1920,yres=1080 -display egl-headless,rendernode=/dev/dri/renderD128 \
  -vnc 127.0.0.1:15 \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:2222-:22 -device virtio-net-pci,netdev=n0 \
  -device virtio-keyboard-pci -device virtio-tablet-pci \
  -qmp unix:"$VM_DIR/qmp.sock",server,nowait -serial file:"$VM_DIR/serial.log" \
  -name magi-test
