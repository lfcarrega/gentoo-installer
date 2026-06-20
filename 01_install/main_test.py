#!/usr/bin/env python

import glob
import os
import re
import secrets
import shutil
import subprocess
import string
import sys
import time
import urllib.request
import sqlite3

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

class Config:
    def __init__(self):
        self._path = Path(__file__).parent / "config.sql"
        # Mantém o banco na memória RAM do LiveCD
        self.db = sqlite3.connect(":memory:")
        self._load_config()

    def _load_config(self):
        if not self._path.exists():
            print(f"[ERROR] Config file not found: {self._path}", file=sys.stderr)
            sys.exit(1)
        
        # O SQLite executa todo o seu arquivo .sql de uma vez só e monta o banco
        try:
            self.db.executescript(self._path.read_text())
        except sqlite3.Error as e:
            print(f"[ERROR] SQL Syntax Error in config.sql: {e}", file=sys.stderr)
            sys.exit(1)

    def get_setting(self, key, default=None):
        cursor = self.db.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else default

def elevate_privileges():
    if os.geteuid() != 0:
        print("[WARNING] Script not running as root. Requesting elevation...")
        os.execvp("sudo", ["sudo", sys.executable] + sys.argv)

def sanitize_sata(device, is_ssd):
    dev_path = Path("/dev/disk/by-id") / device
    out = subprocess.run(['hdparm', '-I', dev_path], capture_output=True, text=True).stdout

    if not re.search(r"not\s+frozen", out):
        print(f"[ERROR] SATA {device} is FROZEN. Aborting this drive.")
        return False

    erase_flag = "--security-erase" if re.search(r"not\s+supported:\s+enhanced erase", out) else "--security-erase-enhanced"

    print(f"[RUN] Starting SATA sanitize on: {device}")
    subprocess.run(["hdparm", "--user-master", "u", "--security-set-pass", "p", str(dev_path)], check=True)
    subprocess.run(["hdparm", "--user-master", "u", erase_flag, "p", str(dev_path)], check=True)

    if is_ssd:
        print(f"[RUN] Running post-erase blkdiscard on {device}...")
        subprocess.run(["blkdiscard", "-f", str(dev_path)], check=True)
    return True

def sanitize_nvme(device):
    dev_path = Path("/dev/disk/by-id") / device
    print(f"[RUN] Starting NVMe sanitize (Block Erase) on: {device}")
    subprocess.run(["nvme", "sanitize", str(dev_path), "-a", "2"], check=True)

    while True:
        log = subprocess.run(["nvme", "sanitize-log", "-H", str(dev_path)], capture_output=True, text=True).stdout
        if "Sanitize in Progress." not in log:
            break
        time.sleep(5)

    print(f"[INFO] NVMe {device} sanitize finished. Running blkdiscard...")
    subprocess.run(["blkdiscard", "-f", str(dev_path)], check=True)
    return True

def general_sanitizer(cfg):
    futures = []
    cursor = cfg.db.cursor()
    
    # PEGAR TODOS OS DISCOS QUE PRECISAM DE SANITIZE (Sem ifs ou filtros complexos)
    cursor.execute("SELECT id, type, connection FROM disks WHERE sanitize = 1")
    disks_to_sanitize = cursor.fetchall()

    with ThreadPoolExecutor() as executor:
        for device_id, disk_type, connection in disks_to_sanitize:
            disk_type = disk_type.lower()
            
            if connection == "sata" or disk_type == "sata":
                is_ssd = (disk_type == "ssd")
                f = executor.submit(sanitize_sata, device_id, is_ssd)
                futures.append(f)
            elif disk_type == "nvme" or connection == "nvme":
                f = executor.submit(sanitize_nvme, device_id)
                futures.append(f)
            else:
                print(f"[WARNING] Device {device_id} ignored. Unknown combo.")

        results = [f.result() for f in futures]

    if not all(results):
        print("[ERROR] Some devices failed or were skipped.")
        sys.exit(1)

    print("[INFO] All SATA and NVMe devices sanitized successfully!")

def setup_storage(cfg):
    cursor = cfg.db.cursor()
    
    # FILTRO DIRETO: Quem é o rootfs?
    cursor.execute("SELECT id, cryptroot FROM disks WHERE rootfs = 1")
    root_disk = cursor.fetchone()
    
    if not root_disk:
        print("[ERROR] No rootfs devices found")
        return

    rootfs_id, cryptroot = root_disk
    dev_path = Path("/dev/disk/by-id") / rootfs_id
    real_dev = str(dev_path.resolve())

    sep = "p" if real_dev[-1].isdigit() else ""
    p_efi  = f"{real_dev}{sep}1"
    p_boot = f"{real_dev}{sep}2"
    p_os   = f"{real_dev}{sep}3"

    if cfg.get_setting("partition_table") == "efi":
        efi_size = cfg.get_setting("efi_size", "1024MiB")
        boot_size = cfg.get_setting("boot_size", "4096MiB")

        print(f"[INFO] Creating EFI and boot partitions {real_dev}...")
        subprocess.run([
            "sgdisk", "--zap-all", real_dev,
            "-n", f"1:0:+{efi_size}", "-t", "1:ef00", "-c", "1:efi",
            "-n", f"2:0:+{boot_size}", "-t", "2:8300", "-c", "2:boot"
        ], check=True)

        subprocess.run(["mkfs.vfat", "-F", "32", p_efi], check=True)
        subprocess.run(["mkfs.ext4", p_boot], check=True)

    if cryptroot:
        luks_mapper = Path("/dev/mapper") / "gentoo_install"
        subprocess.run(["sgdisk", "-n", "3:0:0", "-t", "3:8300", "-c", "3:luks", real_dev], check=True)
        print(f"[+] Encrypting {p_os} with LUKS...")
        subprocess.run(["cryptsetup", "luksFormat", str(p_os)], check=True)
        subprocess.run(["cryptsetup", "open", str(p_os), "gentoo_install"], check=True)
        p_os = luks_mapper
    else:
        subprocess.run(["sgdisk", "-n", "3:0:0", "-t", "3:8e00", "-c", "3:lvm_thin", real_dev], check=True)

    if cfg.get_setting("lvm_type") == "thin":
        rand_id = ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(4))
        vg_name = f"vg_{rand_id}"

        subprocess.run(["vgcreate", vg_name, p_os], check=True)
        subprocess.run(["lvcreate", "--type", "thin-pool", "-n", "lv_thin0", "-l", "95%FREE", vg_name], check=True)
        subprocess.run(["lvcreate", "-n", "gentoo", "-V", "400G", "--thinpool", "lv_thin0", vg_name], check=True)

    real_root = Path("/dev") / vg_name / "gentoo"

    # [O restante dos comandos do ZFS e mounts permanecem iguais...]
    print("[INFO] Loading ZFS module")
    subprocess.run(["modprobe", "zfs"], check=True)
    subprocess.run(["zgenhostid", "-f"], check=True)
    subprocess.run(["zpool", "create", "-f", "-o", "ashift=12", "-o", "autotrim=on", "-O", "acltype=posixacl", "-O", "xattr=sa", "-O", "relatime=on", "-O", "compression=lz4", "-m", "none", "rpool", str(real_root)], check=True)
    subprocess.run(["zfs", "create", "-o", "mountpoint=none", "rpool/ROOT"], check=True)
    subprocess.run(["zfs", "create", "-o", "mountpoint=none", "rpool/data"], check=True)
    subprocess.run(["zfs", "create", "-o", "mountpoint=/", "-o", "canmount=noauto", "rpool/ROOT/gentoo"], check=True)
    subprocess.run(["zfs", "create", "-o", "mountpoint=/home", "rpool/data/home"], check=True)
    subprocess.run(["zfs", "create", "-o", "mountpoint=/root", "rpool/data/root"], check=True)
    subprocess.run(["zpool", "set", "bootfs=rpool/ROOT/gentoo", "rpool"], check=True)
    subprocess.run(["zfs", "create", "-o", "mountpoint=/var/cache/distfiles", "-o", "compression=off", "rpool/data/distfiles"], check=True)
    subprocess.run(["zfs", "create", "-o", "mountpoint=/var/db/repos", "-o", "recordsize=4k", "rpool/data/repos"], check=True)
    subprocess.run(["zfs", "create", "-o", "mountpoint=/var/db/repos/gentoo/metadata/md5-cache", "-o", "compression=lz4", "-o", "sync=disabled", "rpool/data/repos/gentoo-cache"], check=True)
    subprocess.run(["zpool", "export", "rpool"], check=True)
    subprocess.run(["zpool", "import", "-N", "-R", "/mnt/gentoo", "rpool"], check=True)
    
    datasets = ["rpool/ROOT/gentoo", "rpool/data/distfiles", "rpool/data/home", "rpool/data/repos", "rpool/data/repos/gentoo-cache", "rpool/data/root"]
    for dataset in datasets:
        subprocess.run(["zfs", "mount", dataset], check=True)

    subprocess.run(["mount", "--mkdir", str(p_efi), "/mnt/gentoo/efi"], check=True)
    subprocess.run(["mount", "--mkdir", str(p_boot), "/mnt/gentoo/boot"], check=True)
    print("[INFO] Storage setup finished.")

def stage3_download(cfg):
    url_txt = f"{cfg.get_setting('gentoo_autobuilds_url')}/{cfg.get_setting('gentoo_latest_stage3_txt')}"
    try:
        with urllib.request.urlopen(url_txt) as response:
            content = response.read().decode("utf-8")
    except Exception as e:
        print(f"[ERROR] Error during TXT download: {e}")
        return False

    stage3_file = None
    for line in content.splitlines():
        line = line.strip()
        if "stage3-" in line and ".tar.xz" in line and not line.startswith("#"):
            stage3_file = line.split()[0]
            break

    if not stage3_file:
        return False

    os.chdir("/mnt/gentoo")
    download_url = f"{cfg.get_setting('gentoo_autobuilds_url')}/{stage3_file}"
    result = subprocess.run(["wget", download_url])
    return result.returncode == 0

def stage3_prepare(cfg):
    os.chdir("/mnt/gentoo")
    stage3 = next(glob.iglob("stage3-*.tar.xz"), None)
    subprocess.run(["tar", "xpvf", stage3,"--xattrs-include='*.*'", "--numeric-owner", "-C", "/mnt/gentoo"], check=True)
    
    with open("/mnt/gentoo/etc/fstab", "w") as file:
        subprocess.run(["genfstab", "-U", "/mnt/gentoo"], stdout=file, check=True)

    subprocess.run(["cp", "--dereference", "/etc/resolv.conf", "/mnt/gentoo/etc/resolv.conf"], check=True)
    subprocess.run(["arch-chroot", "/mnt/gentoo", "passwd"], check=True)
    subprocess.run(["arch-chroot", "/mnt/gentoo", "ssh-keygen", "-A"], check=True)

    subprocess.run(["mkdir", "-p", "/mnt/gentoo/root/.ssh"], check=True)
    
    # BUSCAR VARIÁVEIS MULTILINHAS (Como chaves SSH)
    cursor = cfg.db.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'root_authorized_key'")
    keys = cursor.fetchall()
    
    with open("/mnt/gentoo/root/.ssh/authorized_keys", "w") as file:
        for key in keys:
            file.write(key[0] + "\n")

    sshd_process = subprocess.Popen(["arch-chroot", "/mnt/gentoo", "/usr/bin/sshd", "-p", "2222", "-D", "-o", "PermitRootLogin=yes", "-o", "PasswordAuthentication=yes"])
    while True:
        key = input("[INFO] SSHD on port 2222... Press q to stop.")
        if key == "q": break

    sshd_process.terminate()
    subprocess.run(["umount", "-R", "/mnt/gentoo"], check=True)
    subprocess.run(["zpool", "export", "rpool"], check=True)

def setup_secondary_pools(cfg):
    cursor = cfg.db.cursor()
    
    # 1. PEGA LOGO TODOS OS ZPOOLS DISTINTOS EXISTENTES (Eliminou o defaultdict!)
    cursor.execute("SELECT DISTINCT zpool FROM disks WHERE zpool IS NOT NULL AND rootfs = 0")
    pools = [row[0] for row in cursor.fetchall()]

    if not pools:
        print("[INFO] No secondary pools to configure.")
        return

    key_file = Path("/etc/cryptsetup-keys.d/secondary_pools.key")
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if not key_file.exists():
        with open(key_file, "wb") as f:
            f.write(os.urandom(64))
        key_file.chmod(0o400)

    dmcrypt_content = "# /etc/conf.d/dmcrypt \n\n"

    for pool_name in pools:
        print(f"\n[INFO] Configuring secondary pool: {pool_name}")
        lvm_lv_paths = []

        # 2. PEGA APENAS OS DISCOS DAQUELE POOL ESPECÍFICO (Filtro relacional puro!)
        cursor.execute("SELECT id, encrypt FROM disks WHERE zpool = ? AND rootfs = 0", (pool_name,))
        disks_in_pool = cursor.fetchall()

        for index, (dev_id, encrypt) in enumerate(disks_in_pool):
            phys_dev = f"/dev/disk/by-id/{dev_id}"
            luks_name = f"luks_{pool_name}_{index}"
            vg_name = f"vg_{pool_name}_{index}"
            level_name = f"lv_{pool_name}_{index}"
            
            mapper_luks = f"/dev/mapper/{luks_name}"
            mapper_lv = f"/dev/mapper/{vg_name}-{level_name}"

            if encrypt:
                print(f"  [+] Formating with LUKS {phys_dev}...")
                subprocess.run(["cryptsetup", "luksFormat", "--key-file", str(key_file), phys_dev], check=True)
                subprocess.run(["cryptsetup", "open", "--key-file", str(key_file), phys_dev, luks_name], check=True)
                target_dev = mapper_luks
                
                # Monta as strings do dmcrypt de forma direta
                dmcrypt_content += f"# Pool ZFS: {pool_name}\ntarget={luks_name}\nsource='{phys_dev}'\nkey='{str(key_file)}'\n\n"
            else:
                target_dev = phys_dev

            subprocess.run(["vgcreate", vg_name, target_dev], check=True)
            subprocess.run(["lvcreate", "-l", "100%FREE", "-n", level_name, vg_name], check=True)
            lvm_lv_paths.append(mapper_lv)

        target_mountpoint = f"/srv/storage/{pool_name}"
        trim_flag = ["-o", "autotrim=on"] if pool_name == "ssd" else []

        subprocess.run([
            "zpool", "create", "-f", "-o", "ashift=12", *trim_flag,
            "-R", "/mnt/gentoo", "-m", target_mountpoint,
            "-O", "acltype=posixacl", "-O", "xattr=sa", "-O", "relatime=on", "-O", "compression=lz4",
            pool_name
        ] + lvm_lv_paths, check=True)

    Path("/mnt/gentoo/etc/conf.d/dmcrypt").write_text(dmcrypt_content)

def main():
    cfg = Config()

    if cfg.get_setting("sanitize_disks") == "true":
        general_sanitizer(cfg)
        
    setup_storage(cfg)
    
    stage3_local = next(glob.iglob("stage3-*.tar.xz"), None)
    if not stage3_local:
        stage3_download(cfg)
    else:
        shutil.copy(stage3_local, f"/mnt/gentoo/{stage3_local}")
        
    stage3_prepare(cfg)
    setup_secondary_pools(cfg)

if __name__ == "__main__":
    elevate_privileges()
    main()