# 01_install

---

## About
This directory contains the bootstrap script responsible for preparing the bare-metal storage layer and launching a temporary environment for Ansible.

> ⚠️ **WARNING:** This script can perform **destructive operations** (Secure Erase, disk partitioning, and formatting). Double-check your `config.json` before running it.

---

## What it does
1. **Disk Sanitization (Optional):** Performs hardware-level `ATA Security Erase` (SATA) or `Block Erase` (NVMe) in parallel.
2. **Storage Setup:**
   * Partitions the boot drive (EFI + Boot + OS).
   * Sets up **LUKS Encryption** (optional) and **LVM Thin Provisioning**.
   * Creates a customized **ZFS Pool (`rpool`)** with optimized datasets for Gentoo (`/var/db/repos` cache tweaks, compressed datasets, etc.).
3. **Stage3 Bootstrap:** Downloads the latest Hardened/SELinux Stage3 tarball and extracts it.
4. **Ansible Gateway:** Configures local network access, injects your SSH authorized keys, and fires up a temporary `sshd` instance on port `2222` inside the chroot.

---

## Usage
### 1. Configure your environment
Create or edit the `config.json` file in this directory. Specify your target disk IDs (found under `/dev/disk/by-id/`), encryption preferences, and your public SSH key.

### 2. Boot the live environment
Boot your machine into the Gentoo Minimal Installation CD (or any modern LiveISO with ZFS and LVM support).

### 3. Run the installer
Ensure you have network access, then clone this repo and run the main script:
```sh
python main.py
```

### 4. Hand off to Ansible
Once the script prints that sshd is listening on port 2222, move over to the 02_configure directory on your local machine to deploy the host playbooks.
