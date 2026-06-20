#!/usr/bin/busybox sh

set -e

PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH

/usr/bin/mount -t proc none /proc
/usr/bin/mount -t sysfs none /sys
/usr/bin/mount -t devtmpfs none /dev
mkdir -p /dev/pts
/usr/bin/mount -t devpts devpts /dev/pts -o gid=5,mode=620

setsid sh -c 'exec sh < /dev/tty1 > /dev/tty1 2>&1' &

/usr/bin/modprobe dm_mod 2>/dev/null
/usr/bin/modprobe dm_thin_pool 2>/dev/null
/usr/bin/modprobe dm-crypt 2>/dev/null
/usr/bin/modprobe zfs 2>/dev/null
/usr/bin/modprobe spl 2>/dev/null
/usr/bin/modprobe e1000e 2>/dev/null
/usr/bin/modprobe wireguard 2>/dev/null

INTERFACE=$(ls /sys/class/net | grep -v -E "lo|wg" | head -n 1)

if [ -z "$INTERFACE" ]; then
    echo "no network interfaces found"
else

    /usr/bin/ip link set "$INTERFACE" up

    sleep 2

    /usr/bin/ip addr add 192.168.15.10/24 dev "$INTERFACE"
    /usr/bin/ip addr add fd5d:d97c:25d5:1::10/64 dev "$INTERFACE"

    /usr/bin/ip route add default via 192.168.15.1 dev "$INTERFACE"
    /usr/bin/ip -6 route add default via fe80::920a:62ff:fe4f:28d0 dev "$INTERFACE" onlink

    mkdir -p /etc
    echo "nameserver 1.1.1.1" > /etc/resolv.conf
    echo "nameserver 1.0.0.1" >> /etc/resolv.conf

    /usr/bin/ip link add dev wg0 type wireguard
    wg setconf wg0 /etc/wireguard/wg0.conf
    /usr/bin/ip address add 10.0.0.1/24 dev wg0
    /usr/bin/ip link set mtu 1420 up dev wg0

    setsid dropbear -R -p 2222 -s &
    DROPBEAR_PID=$!

   /usr/bin/curl -d "Gentoo-PVE is online! IP: 192.168.15.10 or 10.0.0.1. Waiting for SSH at port 2222." https://ntfy.sh/lfcarrega
fi

READY_FLAG="/ready_to_switch"
while [ ! -e "$READY_FLAG" ]; do
    sleep 1
done

if ! mountpoint -q /mnt/root 2>/dev/null; then
    exec /bin/sh
fi

kill "$DROPBEAR_PID" 2>/dev/null || true
ip link set wg0 down 2>/dev/null || true
ip link delete wg0 2>/dev/null || true

ip addr flush dev "$INTERFACE" 2>/dev/null || true
ip -6 addr flush dev "$INTERFACE" 2>/dev/null || true
ip link set "$INTERFACE" down 2>/dev/null || true
ip route flush dev "$INTERFACE" 2>/dev/null || true

mkdir -p /mnt/root/dev /mnt/root/proc /mnt/root/sys
mount --move /dev /mnt/root/dev
mount --move /proc /mnt/root/proc
mount --move /sys /mnt/root/sys

exec switch_root /mnt/root /sbin/init