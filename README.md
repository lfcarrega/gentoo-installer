# About

Python scripts and some Ansible playbooks to create a Gentoo-based virtualization platform.

# Why

Gentoo is great, IaC is a cool concept... why not mix them both?

This is mostly a *PERSONAL* project to learn more about Ansible and Linux stuff.

# Structure

## Host
- Gentoo 'gentoo-pve' as the KVM and LXC host
- A Debian LXC container named 'atlas', which holds the Docker services I'm using
- Another Debian LXC container named 'pihole' responsible for DNS stuff
- A third Debian container just for the "Samba" services
- A couple of VMs with GPU passthru

## Repo
```text
01_installer/   # "Destructive" scripts for disk sanitizing, partitioning and Stage3 setup.
02_configure/   # Ansible playbooks, templates and system configuration files
```





