# 02_configure

---

## About
This directory contains the Ansible playbooks and configurations to provision the Gentoo-based platform.

> ⚠️ **Disclaimer:** This is a tailored homelab setup. Some configuration templates (like the VM profiles) have hardcoded hardware addresses (e.g., my specific GPU PCI IDs). If you run this blindly on your hardware, expect things to break beautifully.

---

## Usage
### Playbook Examples

Run only the tasks targeted at the `gentoo_box` host group:
```sh
ansible-playbook -i hosts.yaml site.yaml --limit "gentoo_box"
```

Run specific tags:
```sh
ansible-playbook -i hosts.yaml site.yaml --tags "tag1,tag2,etc"
```

Skip specific tags:
```sh
ansible-playbook -i hosts.yaml site.yaml --skip-tags "skipped_tag1,skipped_tag2"
```

If you do not have the SSH keys configured, append the `-k` parameter to prompt for the password:
```sh
ansible-playbook -k -i hosts.yaml site.yaml --limit "gentoo_box"
```

### Secret Management (Ansible Vault)
To edit encrypted variables (like passwords or API keys), use ansible-vault with your preferred text editor:
```sh
EDITOR=/usr/bin/nano ansible-vault edit secrets.yaml
```
