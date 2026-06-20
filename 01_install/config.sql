-- Estrutura da tabela de discos
CREATE TABLE IF NOT EXISTS disks (
    id TEXT PRIMARY KEY,
    type TEXT,
    connection TEXT,
    sanitize BOOLEAN,
    encrypt BOOLEAN,
    zpool TEXT,       -- Nome do pool ou 'none'/'NULL'
    rootfs BOOLEAN    -- True se for o disco do sistema
);

-- Limpa instalações anteriores se rodar o script de novo
DELETE FROM disks;

-- Seus discos reais convertidos em queries
INSERT INTO disks VALUES ('ata-FTM28N325H_AS21032201382', 'ssd', 'sata', 1, 1, 'ssd', 0);
INSERT INTO disks VALUES ('ata-SanDisk_SSD_PLUS_480_GB_174780487506', 'ssd', 'sata', 1, 1, 'ssd', 0);
INSERT INTO disks VALUES ('ata-ST500LM012_HN-M500MBB_S2YJJ9KD505099', 'hdd', 'sata', 1, 1, 'hdd', 0);
INSERT INTO disks VALUES ('ata-WDC_WD5000BEVT-75ZAT0_WD-WXB0A8922332', 'hdd', 'sata', 1, 1, 'hdd', 0);
INSERT INTO disks VALUES ('nvme-CT500P3PSSD8_23414428E08C', 'nvme', 'pcie', 1, 1, NULL, 1);

-- Tabela secundária para variáveis globais simples
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
DELETE FROM settings;
INSERT INTO settings VALUES ('partition_table', 'efi');
INSERT INTO settings VALUES ('efi_size', '1024MiB');
INSERT INTO settings VALUES ('gentoo_stage3_txt', 'latest-stage3-amd64-hardened-selinux-openrc.txt');