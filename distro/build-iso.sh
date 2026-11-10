#!/usr/bin/env bash
# Build the MAGI installer ISO, rootless (podman only):
#   Fedora 44 netinstall boot media (downloaded, GPG + sha256 checked)
#   + the MAGI image as an OCI layout on the ISO (/magi), so the install needs no network
#   + a kickstart that installs it (distro/iso/magi.ks) and then tracks --ref for updates
#   + installer branding in images/product.img (product name MAGI, no Fedora logos)
# assembled by mkksiso (lorax) in a tools container.
#
#   distro/build-iso.sh [--image localhost/magi:latest] [--ref ghcr.io/pankajgarkoti/magi:latest]
#                       [--ks distro/iso/magi.ks] [--boot-iso FILE] [--out magi-44-x86_64.iso]
#                       [--work DIR]
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
IMAGE=localhost/magi:latest REF=ghcr.io/pankajgarkoti/magi:latest KS=$repo/distro/iso/magi.ks
BOOT_ISO= OUT=$PWD/magi-44-x86_64.iso WORK=${TMPDIR:-/var/tmp}/magi-iso FEDORA=44
while [ $# -gt 0 ]; do
  case "$1" in
    --image) IMAGE=$2; shift ;; --ref) REF=$2; shift ;; --ks) KS=$(readlink -f "$2"); shift ;;
    --boot-iso) BOOT_ISO=$(readlink -f "$2"); shift ;; --out) OUT=$(readlink -m "$2"); shift ;;
    --work) WORK=$(readlink -m "$2"); shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "build-iso.sh: unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
say() { printf '\e[1;36m==> %s\e[0m\n' "$*"; }
mkdir -p "$WORK"
rm -rf "$WORK/root" "$WORK/product" "$WORK/out.iso"
mkdir -p "$WORK/root/images" "$WORK/product"

# 1. boot media
if [ -z "$BOOT_ISO" ]; then
  BOOT_ISO=$WORK/netinst.iso
  if [ ! -s "$BOOT_ISO" ]; then
    base="https://download.fedoraproject.org/pub/fedora/linux/releases/$FEDORA/Everything/x86_64/iso"
    say "downloading the Fedora $FEDORA netinstall boot media"
    idx=$(curl -fsSL "$base/")
    iso=$(grep -o "Fedora-Everything-netinst-x86_64-$FEDORA-[0-9.]*\.iso" <<< "$idx" | sort -u | tail -1)
    sums=$(grep -o "Fedora-Everything-$FEDORA-[0-9.]*-x86_64-CHECKSUM" <<< "$idx" | sort -u | tail -1)
    curl -fsSL -o "$WORK/CHECKSUM" "$base/$sums"
    curl -fsSL -o "$WORK/fedora.gpg" https://fedoraproject.org/fedora.gpg
    gpgv --keyring "$WORK/fedora.gpg" "$WORK/CHECKSUM"
    curl -fL --retry 3 -o "$BOOT_ISO.part" "$base/$iso"
    want=$(sed -n "s/^SHA256 ($iso) = //p" "$WORK/CHECKSUM")
    echo "$want  $BOOT_ISO.part" | sha256sum -c -
    mv "$BOOT_ISO.part" "$BOOT_ISO"
  fi
fi

# 2. the image, as an OCI layout
say "exporting $IMAGE to an OCI layout"
podman push --quiet "$IMAGE" "oci:$WORK/root/magi"
du -sh "$WORK/root/magi" | sed 's/^/    /'

# 3 + 4. kickstart, branding, ISO
sed "s|@IMAGE_REF@|$REF|g" "$KS" > "$WORK/ks.cfg"
cp "$repo/distro/iso/make-branding.py" "$WORK/"
say "building the tools container"
podman build -q -t localhost/magi-isotools -f "$repo/distro/iso/Containerfile.tools" "$repo/distro/iso" >/dev/null
say "assembling the ISO (mkksiso)"
podman run --rm --security-opt label=disable -v "$WORK:/w" -v "$BOOT_ISO:/boot.iso:ro" localhost/magi-isotools bash -euc '
python3 /w/make-branding.py /w/product /usr/share/fonts/dejavu-sans-mono-fonts/DejaVuSansMono-Bold.ttf
printf "[Main]\nProduct=MAGI\nVersion='"$FEDORA"'\nBugURL=https://github.com/pankajgarkoti/magi/issues\nIsFinal=True\nUUID=$(date +%Y%m%d%H%M).x86_64\nVariant=\n" > /w/product/.buildstamp
(cd /w/product && find . | cpio --quiet -o -H newc | gzip -9) > /w/root/images/product.img
mkksiso --ks /w/ks.cfg -a /w/root/magi -a /w/root/images -V MAGI-'"$FEDORA"' \
  -R "Install Fedora '"$FEDORA"'" "Install MAGI '"$FEDORA"'" \
  -R "Test this media & install Fedora '"$FEDORA"'" "Test this media & install MAGI '"$FEDORA"'" \
  -R "Fedora '"$FEDORA"' in basic graphics mode" "MAGI '"$FEDORA"' in basic graphics mode" \
  -R "Rescue a Fedora system" "Rescue a MAGI system" \
  --skip-mkefiboot --no-md5sum --tmp /w /boot.iso /w/stage.iso
# The UEFI boot image is an appended FAT partition. mkksiso rebuilds it with a loop device,
# which a rootless container lacks, so patch its grub.cfg with mtools instead.
read -r a b < <(xorriso -indev /boot.iso -report_el_torito as_mkisofs 2>/dev/null |
  sed -n "s/^-append_partition 2 [0-9a-f]* --interval:local_fs:\([0-9]*\)d-\([0-9]*\)d.*/\1 \2/p")
dd if=/boot.iso of=/w/efiboot.img bs=512 skip="$a" count=$((b - a + 1)) status=none
xorriso -osirrox on -indev /w/stage.iso -extract /EFI/BOOT/grub.cfg /w/grub-efi.cfg 2>/dev/null
mcopy -o -i /w/efiboot.img /w/grub-efi.cfg ::/EFI/BOOT/grub.cfg
mcopy -o -i /w/efiboot.img /w/grub-efi.cfg ::/EFI/BOOT/BOOT.conf
xorriso -indev /w/stage.iso -outdev /w/out.iso -boot_image any replay \
  -append_partition 2 0xef /w/efiboot.img 2>&1 | grep -iE "error|fail" || true
rm -f /w/stage.iso /w/efiboot.img /w/grub-efi.cfg
implantisomd5 /w/out.iso >/dev/null
xorriso -indev /w/out.iso -ls / /images 2>/dev/null | tr "\n" " "; echo
grep -m2 -E "menuentry|stage2" /w/../w/ks.cfg >/dev/null 2>&1 || true
'
mv "$WORK/out.iso" "$OUT"
rm -rf "$WORK/root" "$WORK/product"
say "ISO: $OUT ($(du -h "$OUT" | cut -f1))"
