#!/usr/bin/env python

import glob
import json
import os
import re
import secrets
import shutil
import subprocess
import string
import sys
import time
import threading
import urllib.request

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

class Config:
  def __init__(self):
    self._path = Path(__file__).parent / "config.json"
    self._data = self._load_config()
  def _load_config(self):
    if not self._path.exists():
      print(f"[ERROR] Error: Config file not found: {self._path}", file=sys.stderr)
      sys.exit(1)
    return json.loads(self._path.read_text())
  def get(self, key, default=None):
      return self._data.get(key, default)

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

  with ThreadPoolExecutor() as executor:
    for d in cfg.get("disks", []):
      if not d.get("sanitize"):
        continue

      device_id = d.get("id")
      connection = d.get("connection")
      disk_type = d.get("type", "").lower()

      if connection == "sata" or disk_type == "sata":
        is_ssd = (disk_type == "ssd")
        f = executor.submit(sanitize_sata, device_id, is_ssd)
        futures.append(f)
      elif disk_type == "nvme" or connection == "nvme":
        f = executor.submit(sanitize_nvme, device_id)
        futures.append(f)
      else:
        print(f"[WARNING] Warning: Device {device_id} ignored. Unknown combo (conn: {connection}, type: {disk_type})")

    results = [f.result() for f in futures]

  if not all(results):
    print("[ERROR] Some devices failed or were skipped (check logs above).")
    sys.exit(1)

  print("[INFO] All SATA and NVMe devices sanitized successfully!")

def setup_storage(cfg):
  rootfs_disk = next((d for d in cfg.get("disks", []) if d.get("rootfs")), None)
  cryptroot = next((d for d in cfg.get("disks", []) if d.get("rootfs") and d.get("cryptroot")), None)
  if not rootfs_disk:
    print("[ERROR] No rootfs devices found")
    return

  dev_path = Path("/dev/disk/by-id") / rootfs_disk["id"]
  real_dev = str(dev_path.resolve())

  sep = "p" if real_dev[-1].isdigit() else ""

  p_efi  = f"{real_dev}{sep}1"
  p_boot = f"{real_dev}{sep}2"
  p_os   = f"{real_dev}{sep}3"

  if cfg.get("partition_table") == "efi":
    efi_size = cfg.get("efi_size", "1024MiB")
    boot_size = cfg.get("boot_size", "4096MiB")

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
    print(f"[+] Encrypting {p_os} with LUKS, you may be asked for a password...")
    subprocess.run(["cryptsetup", "luksFormat", str(p_os)], check=True)
    print(f"[+] Opening {p_os} as {luks_mapper}, you may be asked for a password...")
    subprocess.run(["cryptsetup", "open", str(p_os), "gentoo_install"], check=True)
    p_os = luks_mapper
  else:
    subprocess.run(["sgdisk", "-n", "3:0:0", "-t", "3:8e00", "-c", "3:lvm_thin", real_dev], check=True)

  if cfg.get("lvm_type") == "thin":
    rand_id = ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(4))
    vg_name = f"vg_{rand_id}"

    print(f"[INFO] Configuring LVM VG {vg_name} on {p_os}...")
    subprocess.run(["vgcreate", vg_name, p_os], check=True)
    subprocess.run(["lvcreate", "--type", "thin-pool", "-n", "lv_thin0", "-l", "95%FREE", vg_name], check=True)
    subprocess.run(["lvcreate", "-n", "gentoo", "-V", "400G", "--thinpool", "lv_thin0", vg_name], check=True)

  real_root = Path("/dev") / vg_name / "gentoo"

  print("[INFO] Loading ZFS module")
  subprocess.run(["modprobe", "zfs"], check=True)

  print("[INFO] Generating ZFS Host ID")
  subprocess.run(["zgenhostid", "-f"], check=True)

  print(f"[INFO] Creating ZFS Pool on {real_root}")

  subprocess.run([
    "zpool", "create", "-f",
    "-o", "ashift=12",
    "-o", "autotrim=on",
    "-O", "acltype=posixacl",
    "-O", "xattr=sa",
    "-O", "relatime=on",
    "-O", "compression=lz4",
    "-m", "none",
    "rpool", str(real_root)
  ], check=True)

  # 2. Container datasets (no mountpoints)
  subprocess.run(["zfs", "create", "-o", "mountpoint=none", "rpool/ROOT"], check=True)
  subprocess.run(["zfs", "create", "-o", "mountpoint=none", "rpool/data"], check=True)

  # 3. The OS Root (Cleanly isolated)
  subprocess.run(["zfs", "create", "-o", "mountpoint=/", "-o", "canmount=noauto", "rpool/ROOT/gentoo"], check=True)

  # 4. User Data (Independent of OS upgrades/snapshots)
  subprocess.run(["zfs", "create", "-o", "mountpoint=/home", "rpool/data/home"], check=True)

  # Optional: Separate root user home so you can always log in if /home is unmounted
  subprocess.run(["zfs", "create", "-o", "mountpoint=/root", "rpool/data/root"], check=True)

  # 5. Set the Boot Dataset
  subprocess.run(["zpool", "set", "bootfs=rpool/ROOT/gentoo", "rpool"], check=True)

  # A dataset for Distfiles (massive archive files -> no point in compressing them twice)
  subprocess.run(["zfs", "create", "-o", "mountpoint=/var/cache/distfiles", "-o", "compression=off", "rpool/data/distfiles"], check=True)

  # 1. Base dataset for ALL repositories (Overlays + Main Tree)
  subprocess.run([
    "zfs", "create",
    "-o", "mountpoint=/var/db/repos",
    "-o", "recordsize=4k",
    "rpool/data/repos"
  ], check=True)

  # 2. A separate nested dataset just for the volatile metadata cache
  # We turn off sync or tweak settings here because this data can always be regenerated safely.
  subprocess.run([
    "zfs", "create",
    "-o", "mountpoint=/var/db/repos/gentoo/metadata/md5-cache",
    "-o", "compression=lz4",
    "-o", "sync=disabled",  # Speed up syncs massively; safely losing this cache on power failure doesn't hurt
    "rpool/data/repos/gentoo-cache"
  ], check=True)

  print("[INFO] Mounting storage")

  subprocess.run([
    "zpool", "export", "rpool"
  ], check=True)

  subprocess.run([
    "zpool", "import", "-N", "-R", "/mnt/gentoo", "rpool"
  ], check=True)

  # actually mount the datasets
  datasets = ["rpool/ROOT/gentoo", "rpool/data/distfiles", "rpool/data/home", "rpool/data/repos", "rpool/data/repos/gentoo-cache", "rpool/data/root"]
  for dataset in datasets:
    subprocess.run(["zfs", "mount", dataset], check=True)

  subprocess.run([
    "mount", "--mkdir", str(p_efi), "/mnt/gentoo/efi"
  ], check=True)

  subprocess.run([
    "mount", "--mkdir", str(p_boot), "/mnt/gentoo/boot"
  ], check=True)

  print("[INFO] Storage setup finished.")

def stage3_download(cfg):
  url_txt = f"{cfg.get('gentoo_autobuilds_url')}/{cfg.get('gentoo_latest_stage3_txt')}"

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
    print("[ERROR] stage3 not found on the TXT file.")
    return False

  os.chdir("/mnt/gentoo")

  download_url = f"{cfg.get('gentoo_autobuilds_url')}/{stage3_file}"
  print(f"[ERROR] Using wget to download: {download_url}\n")

  result = subprocess.run(["wget", download_url])

  return result.returncode == 0

def stage3_prepare(cfg):
  os.chdir("/mnt/gentoo")
  stage3 = next(glob.iglob("stage3-*.tar.xz"), None)
  print("[INFO] Extracting stage3 to /mnt/gentoo")
  subprocess.run(["tar", "xpvf", stage3,"--xattrs-include='*.*'", "--numeric-owner", "-C", "/mnt/gentoo"], check=True)

  print("[INFO] Generating fstab")
  with open("/mnt/gentoo/etc/fstab", "w") as file:
    subprocess.run(["genfstab", "-U", "/mnt/gentoo"], stdout=file, check=True)

  print("[INFO] Copying resolv.conf to the chroot dir")
  subprocess.run(["cp", "--dereference", "/etc/resolv.conf", "/mnt/gentoo/etc/resolv.conf"], check=True)

  print("[ACTION] Set up your root password, Ansible may need use it to access via SSH.")
  subprocess.run(["arch-chroot", "/mnt/gentoo", "passwd"], check=True)

  print("[INFO] Generating machine SSH keys")
  subprocess.run(["arch-chroot", "/mnt/gentoo", "ssh-keygen", "-A"], check=True)

  print("[INFO] Writing root SSH keys")
  keys = cfg.get("root_authorized_keys", [])
  subprocess.run(["mkdir", "-p", "/mnt/gentoo/root/.ssh"], check=True)
  with open("/mnt/gentoo/root/.ssh/authorized_keys", "w") as file:
    for key in keys:
      file.write(key + "\n")

  sshd_process = subprocess.Popen(
    ["arch-chroot", "/mnt/gentoo", "/usr/bin/sshd", "-p", "2222", "-D", "-o", "PermitRootLogin=yes", "-o", "PasswordAuthentication=yes"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
  )

  while True:
    key = input("[INFO] SSHD is now listening inside the chroot on port 2222... Press q to stop.")
    if key == "q":
      print(f"pressed {key}, bye!")
      break

  sshd_process.terminate()

  subprocess.run(["umount", "-R", "/mnt/gentoo"], check=True)
  subprocess.run(["zpool", "export", "rpool"], check=True)

  print("[INFO] Now you can reboot the system.")

def main():
  cfg = Config()

  if cfg.get("sanitize_disks"):
    print("[INFO] Disk sanitization enabled, triggering SECURE ERASE commands")
    general_sanitizer(cfg)
  setup_storage(cfg)
  stage3_local = next(glob.iglob("stage3-*.tar.xz"), None)
  if not stage3_local:
    stage3_download(cfg)
  else:
    print(f"[INFO] Found local stage3 ({stage3_local}), copying to /mnt/gentoo...")
    shutil.copy(stage3_local, f"/mnt/gentoo/{stage3_local}")
  stage3_prepare(cfg)

if __name__ == "__main__":
  elevate_privileges()
  main()
