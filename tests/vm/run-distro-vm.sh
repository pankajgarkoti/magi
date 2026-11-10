#!/usr/bin/env bash
# Boot the MAGI distro test VM headless (UEFI). Same display setup as run-vm.sh: virtio-vga-gl
# rendered off-screen, localhost-only VNC for screendumps, nothing on the host desktop.
#   VM_DIR=~/vm/magi-distro [XRES=2560 YRES=1600] tests/vm/run-distro-vm.sh [--iso FILE]   (--iso: boot the installer)
set -euo pipefail
VM_DIR=${VM_DIR:-$HOME/vm/magi-distro}
cd "$VM_DIR"
[ -f disk.qcow2 ] || qemu-img create -f qcow2 disk.qcow2 60G >/dev/null
[ -f OVMF_VARS.fd ] || cp /usr/share/edk2/ovmf/OVMF_VARS.fd OVMF_VARS.fd
cd_args=()
if [ "${1:-}" = --iso ]; then cd_args=(-drive "file=$2,media=cdrom,readonly=on" -boot once=d); fi
exec qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 4 -m 6144 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/edk2/ovmf/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=OVMF_VARS.fd \
  -drive file=disk.qcow2,if=virtio "${cd_args[@]}" \
  -device virtio-vga-gl,xres=${XRES:-1920},yres=${YRES:-1080} -display egl-headless,rendernode=/dev/dri/renderD128 \
  -vnc 127.0.0.1:16 \
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:2223-:22 -device virtio-net-pci,netdev=n0 \
  -device virtio-keyboard-pci -device virtio-tablet-pci \
  -audiodev none,id=snd0 -device intel-hda -device hda-duplex,audiodev=snd0 \
  -qmp unix:"$VM_DIR/qmp.sock",server,nowait -serial file:"$VM_DIR/serial.log" \
  -name magi-distro
