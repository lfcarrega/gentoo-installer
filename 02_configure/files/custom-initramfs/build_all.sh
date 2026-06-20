#!/bin/bash
set -e

BASE_DIR="/usr/local/src/custom-initramfs"
BINARIES=(
    /usr/bin/busybox
    /usr/bin/cryptsetup
    /usr/bin/zfs /usr/bin/zpool
    /usr/bin/lvm
    /usr/bin/wg /usr/bin/dropbear
    /usr/bin/mount
    /usr/bin/ip /usr/bin/dhcpcd /usr/bin/curl
    /usr/bin/modprobe
    /usr/bin/dmsetup
)
MODULES=( dm-crypt dm_thin_pool wireguard e1000e zfs spl )

build_for_kernel() {
    local KERNEL_VER="$1"
    local DIR="$BASE_DIR/root-$KERNEL_VER"

    echo "=== Building initramfs for kernel: $KERNEL_VER ==="

    rm -rf "$DIR"
    mkdir -p "$DIR"/{bin,sbin,dev,proc,sys,etc,lib,lib64,mnt/root,run,usr/bin,usr/sbin,root}
    cp -a /dev/{null,console,tty,urandom,zero} "$DIR"/dev/

    for bin in "${BINARIES[@]}"; do
        if [ -f "$bin" ]; then
            lddtree --copy-to-tree "$DIR" "$bin"
        else
            echo "Warning: $bin not found!"
        fi
    done

    for mod in "${MODULES[@]}"; do
        MOD_PATH=$(modinfo -k "$KERNEL_VER" -F filename "$mod" 2>/dev/null)
        if [ -n "$MOD_PATH" ]; then
            mkdir -p "$DIR/$(dirname "$MOD_PATH")"
            cp "$MOD_PATH" "$DIR/$MOD_PATH"
            DEPS=$(modinfo -k "$KERNEL_VER" -F depends "$mod" 2>/dev/null | tr ',' ' ')
            for dep in $DEPS; do
                DEP_PATH=$(modinfo -k "$KERNEL_VER" -F filename "$dep" 2>/dev/null)
                if [ -n "$DEP_PATH" ]; then
                    mkdir -p "$DIR/$(dirname "$DEP_PATH")"
                    cp "$DEP_PATH" "$DIR/$(dirname "$DEP_PATH")/"
                fi
            done
        else
            echo "[!] Warning: module '$mod' not found for kernel $KERNEL_VER — skipping (initramfs will lack this module)."
        fi
    done

    MOD_DIR="$DIR/lib/modules/$KERNEL_VER"
    mkdir -p "$MOD_DIR"
    cp "/lib/modules/$KERNEL_VER"/modules.{order,builtin,builtin.modinfo} "$MOD_DIR/" 2>/dev/null || true
    depmod -b "$DIR" "$KERNEL_VER"

    cd "$DIR"/bin
    ln -sf ../usr/bin/busybox sh
    ln -sf ../usr/bin/busybox switch_root
    cd "$DIR"
    cp "$BASE_DIR/init.sh" "${DIR}/init"
    cp "${BASE_DIR}/post_init.sh" "${DIR}/sbin/post_init.sh"
    chmod +x "${DIR}/init"
    chmod +x "${DIR}/sbin/post_init.sh"

    mkdir -p "${DIR}/etc/wireguard"
    cp "$BASE_DIR/wg0.conf" "${DIR}/etc/wireguard/wg0.conf"
    mkdir -p "$DIR"/etc/dropbear
    mkdir -p "$DIR"/root/.ssh
    cp "$BASE_DIR/dropbear_authorized_keys" "$DIR"/etc/dropbear/authorized_keys
    cp "$BASE_DIR/dropbear_authorized_keys" "$DIR"/root/.ssh/authorized_keys
    chmod 700 "$DIR"/root
    chmod 700 "$DIR"/root/.ssh
    chmod 600 "$DIR"/root/.ssh/authorized_keys
    chmod 700 "$DIR"/etc/dropbear
    cp "$BASE_DIR"/dropbear_*_host_key "$DIR"/etc/dropbear/
    chmod 600 "$DIR"/etc/dropbear/dropbear_*_host_key
    chmod 600 "$DIR"/etc/dropbear/authorized_keys

    mkdir -p "$DIR"/etc/ssl/certs
    cp -L /etc/ssl/certs/ca-certificates.crt "$DIR"/etc/ssl/certs/
    echo "root:x:0:0:root:/root:/bin/sh" > "$DIR"/etc/passwd
    echo "root:x:0:" > "$DIR"/etc/group
    mkdir -p "$DIR"/lib64
    cp -a /lib64/libnss_files* "$DIR"/lib64/
    echo "passwd: files" > "$DIR"/etc/nsswitch.conf
    echo "group: files" >> "$DIR"/etc/nsswitch.conf
    mkdir -p "$DIR"/etc/lvm
    cp "$BASE_DIR/lvm.conf" "$DIR"/etc/lvm/lvm.conf
    cp -a /etc/hostid "$DIR"/etc/hostid

    cd "$DIR"
    find . -print0 | cpio --null --create --format=newc | zstd -19 -T0 > "/boot/initramfs-${KERNEL_VER}.img"
    echo "=== Generated /boot/initramfs-${KERNEL_VER}.img ==="
}

for kdir in /lib/modules/*/; do
    KVER=$(basename "$kdir")
    build_for_kernel "$KVER"
done