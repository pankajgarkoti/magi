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
