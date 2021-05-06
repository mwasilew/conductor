# Copyright 2021 Foundries.io
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from datetime import datetime, timedelta
from django.test import TestCase
from unittest.mock import patch, MagicMock, PropertyMock

from conductor.core.models import Project, Build, Run, LAVADeviceType, LAVADevice, LAVAJob, PDUAgent
from conductor.core.tasks import create_build_run, create_ota_job, device_pdu_action, check_ota_completed


DEVICE_DETAILS = """
{
    "device_type": "bcm2711-rpi-4-b",
    "device_version": null,
    "physical_owner": null,
    "physical_group": null,
    "description": "Created automatically by LAVA.",
    "tags": [],
    "state": "Idle",
    "health": "Unknown",
    "last_health_report_job": 1,
    "worker_host": "rpi-01-worker",
    "is_synced": true
}
"""

DEVICE_DICT = """# in milliseconds
character_delays:
      boot: 5
      test: 5
constants:

  # POSIX os (not AOSP)
  posix:
    lava_test_sh_cmd: /bin/sh
    lava_test_results_dir: /lava-%s
    lava_test_shell_file: ~/.bashrc

  # bootloader specific
  barebox:
    interrupt-prompt: 'Hit m for menu or any other key to stop autoboot'
    interrupt-character: '
'
    final-message: 'Starting kernel'
    error-messages:
      - '### ERROR ### Please RESET the board ###'
      - 'ERROR: .*'
      - '.*: Out of memory'
  u-boot:
    interrupt-prompt: 'Hit any key to stop autoboot'
    interrupt-character: ' '
    interrupt_ctrl_list: []
    interrupt-newline: True
    final-message: 'Starting kernel'
    error-messages:
      - 'Resetting CPU'
      - 'Must RESET board to recover'
      - 'TIMEOUT'
      - 'Retry count exceeded'
      - 'Retry time exceeded; starting again'
      - 'ERROR: The remote end did not respond in time.'
      - 'File not found'
      - 'Bad Linux ARM64 Image magic!'
      - 'Wrong Ramdisk Image Format'
      - 'Ramdisk image is corrupt or invalid'
      - 'ERROR: Failed to allocate'
      - 'TFTP error: trying to overwrite reserved memory'
      - 'Invalid partition'
    dfu-download: 'DOWNLOAD \.\.\. OK\r\nCtrl\+C to exit \.\.\.'
  grub:
    interrupt-prompt: 'for a command-line'
    interrupt-character: 'c'
    interrupt-newline: False
    error-messages:
      - "error: missing (.*) symbol."
  grub-efi:
    interrupt-prompt: 'for a command-line'
    interrupt-character: 'c'
    error-messages:
      - 'Undefined OpCode Exception PC at'
      - 'Synchronous Exception at'
      - "error: missing (.*) symbol."
  ipxe:
    interrupt-prompt: 'Press Ctrl-B for the iPXE command line'
    interrupt_ctrl_list: ['b']
    error-messages:
      - 'No configuration methods succeeded'
      - 'Connection timed out'

  # OS shutdown message
  # Override: set as the shutdown-message parameter of an Action.
  # SHUTDOWN_MESSAGE
  shutdown-message: 'The system is going down for reboot NOW'

  # Kernel starting message
  kernel-start-message: 'Linux version [0-9]'

  # Default shell prompt for AutoLogin
  # DEFAULT_SHELL_PROMPT
  default-shell-prompt: 'lava-test: # '

  # pexpect.spawn maxread
  # SPAWN_MAXREAD - in bytes, quoted as a string
  # 1 to turn off buffering, pexpect default is 2000
  # maximum may be limited by platform issues to 4092
  # avoid setting searchwindowsize:
  # Data before searchwindowsize point is preserved, but not searched.
  spawn_maxread: '4092'
commands:
    connections:
        uart0:
            connect: telnet ser2net 7002
            tags:
            - primary
            - telnet
    hard_reset: /usr/local/bin/eth008_control -a 192.168.0.21 -r 1 -s offon -d 5
    power_off: ['/usr/local/bin/eth008_control -a 192.168.0.21 -r 1 -s off', '/usr/local/bin/eth008_control -a 192.168.0.21 -r 2 -s off', '/usr/local/bin/eth008_control -a 192.168.0.21 -r 3 -s off']
    power_on: /usr/local/bin/eth008_control -r 1 -s on
device_info: [{'board_id': Undefined}]
parameters:
  # interfaces or device_ip or device_mac

  pass: # sata_uuid_sd_uuid_usb_uuid

  image:
    kernel: '0x40480000'
    ramdisk: '0x44000000'
    dtb: '0x43000000'
    tee: '0x83000000'
  booti:
    kernel: '0x40480000'
    ramdisk: '0x44000000'
    dtb: '0x43000000'
    tee: '0x83000000'
  uimage:
    kernel: '0x40480000'
    ramdisk: '0x44000000'
    dtb: '0x43000000'
    tee: '0x83000000'
  bootm:
    kernel: '0x40480000'
    ramdisk: '0x44000000'
    dtb: '0x43000000'
    tee: '0x83000000'
  zimage:
    kernel: '0x40480000'
    ramdisk: '0x44000000'
    dtb: '0x43000000'
    tee: '0x83000000'
  bootz:
    kernel: '0x40480000'
    ramdisk: '0x44000000'
    dtb: '0x43000000'
    tee: '0x83000000'

        
adb_serial_number: "0000000000"
fastboot_serial_number: "0000000000"
fastboot_options: ['-i', '0x0525']
# This attribute identifies whether a device should get into fastboot mode by
# interrupting uboot and issuing commands at the bootloader prompt.
fastboot_via_uboot: True

actions:
  deploy:
    parameters:
      add_header: u-boot
      mkimage_arch: arm64 # string to pass to mkimage -A when adding UBoot headers
      append_dtb: False
      use_xip: False
    connections:
      lxc:
      fastboot:
      serial:
    methods:
      flasher:
        commands: ['/usr/local/bin/eth008_control -a 192.168.0.21 -r 1 -s off', '/usr/local/bin/eth008_control -a 192.168.0.21 -r 2 -s on', '/usr/local/bin/eth008_control -a 192.168.0.21 -r 3 -s on', '/usr/local/bin/eth008_control -a 192.168.0.21 -r 1 -s on', 'sleep 1', 'flash-imx8.sh -i "{IMAGE}" -b "{BOOTLOADER}" -u "{UBOOT}" -s "{SITIMG}" -p "1:2"', '/usr/local/bin/eth008_control -a 192.168.0.21 -r 1 -s off', '/usr/local/bin/eth008_control -a 192.168.0.21 -r 2 -s off', '/usr/local/bin/eth008_control -a 192.168.0.21 -r 3 -s off']
      image:
      lxc:
      overlay:
      usb:
      tftp:
      nbd:
      ssh:
        options:
          - '-o'
          - 'Compression=yes'
          - '-o'
          - 'PasswordAuthentication=no'
          - '-o'
          - 'LogLevel=FATAL'

        host: ""
        port: 22
        user: "root"
        identity_file: "dynamic_vm_keys/lava"
      fastboot:
      u-boot:
        parameters:
          bootloader_prompt: ""
          interrupt_prompt: ""
          interrupt_char: ""
          fastboot:
            commands:
              - fastboot 0

  boot:
    connections:
      lxc:
      fastboot:
      serial:
    methods:
      minimal:
      ssh:
      dfu:
        implementation: u-boot
        reset_works: False
        parameters:
          enter-commands:
          command: dfu-util
      fastboot: ['reboot']
      u-boot:
        parameters:
          bootloader_prompt: =>
          interrupt_prompt: Hit any key to stop autoboot
          interrupt_char: ""
          needs_interrupt: True


        # method specific stanza
        ums:
          commands:
          - "ums 0 mmc 1"
        nfs:
          commands:
          - "setenv autoload no"
          - "setenv initrd_high 0xffffffff"
          - "setenv fdt_high 0xffffffff"
          - "dhcp"
          - "setenv serverip {SERVER_IP}"
          - "tftp {KERNEL_ADDR} {KERNEL}"
          - "tftp {RAMDISK_ADDR} {RAMDISK}"
          - "tftp {TEE_ADDR} {TEE}"
          - "setenv initrd_size ${filesize}"
          - "tftp {DTB_ADDR} {DTB}"
          # Always quote the entire string if the command includes a colon to support correct YAML.
          - "setenv bootargs 'console=ttymxc1,115200 earlycon=ec_imx6q,0x30890000,115200n8 root=/dev/nfs rw nfsroot={NFS_SERVER_IP}:{NFSROOTFS},tcp,hard,v3  ip=dhcp'"
          - "{BOOTX}"
        nbd:
          commands:
          - "setenv autoload no"
          - "setenv initrd_high 0xffffffff"
          - "setenv fdt_high 0xffffffff"
          - "dhcp"
          - "setenv serverip {SERVER_IP}"
          - "tftp {KERNEL_ADDR} {KERNEL}"
          - "tftp {RAMDISK_ADDR} {RAMDISK}"
          - "tftp {TEE_ADDR} {TEE}"
          - "setenv initrd_size ${filesize}"
          - "tftp {DTB_ADDR} {DTB}"
          # Always quote the entire string if the command includes a colon to support correct YAML.
          - "setenv bootargs 'console=ttymxc1,115200 earlycon=ec_imx6q,0x30890000,115200n8 rw nbd.server={NBDSERVERIP} nbd.port={NBDSERVERPORT} root=/dev/ram0 ramdisk_size=16384 rootdelay=7   ip=dhcp verbose earlyprintk systemd.log_color=false ${extraargs} rw'"
          - "{BOOTX}"
        ramdisk:
          commands:
          - "setenv autoload no"
          - "setenv initrd_high 0xffffffff"
          - "setenv fdt_high 0xffffffff"
          - "dhcp"
          - "setenv serverip {SERVER_IP}"
          - "tftp {KERNEL_ADDR} {KERNEL}"
          - "tftp {RAMDISK_ADDR} {RAMDISK}"
          - "tftp {TEE_ADDR} {TEE}"
          - "setenv initrd_size ${filesize}"
          - "tftp {DTB_ADDR} {DTB}"
          - "setenv bootargs 'console=ttymxc1,115200 earlycon=ec_imx6q,0x30890000,115200n8 root=/dev/ram0  ip=dhcp'"
          - "{BOOTX}"
        usb:
          commands:
          - "usb start"
          - "setenv autoload no"
          - "load usb :{ROOT_PART} {KERNEL_ADDR} {KERNEL}"
          - "load usb :{ROOT_PART} {RAMDISK_ADDR} {RAMDISK}"
          - "setenv initrd_size ${filesize}"
          - "load usb :{ROOT_PART} {DTB_ADDR} {DTB}"
          - "console=ttymxc1,115200 earlycon=ec_imx6q,0x30890000,115200n8 root={ROOT}  ip=dhcp"
          - "{BOOTX}"
        sata:
          commands:
          - "scsi scan"
          - "setenv autoload no"
          - "load scsi {ROOT_PART} {KERNEL_ADDR} {KERNEL}"
          - "load scsi {ROOT_PART} {RAMDISK_ADDR} {RAMDISK}; setenv initrd_size ${filesize}"
          - "load scsi {ROOT_PART} {DTB_ADDR} {DTB}"
          - "setenv bootargs 'console=ttymxc1,115200 earlycon=ec_imx6q,0x30890000,115200n8 root={ROOT}  ip=dhcp'"
          - "{BOOTX}"
      uuu:
        options:
          usb_otg_path: ""
          corrupt_boot_media_command: 
          docker_image: ""
          remote_options: ""


timeouts:
  actions:
    apply-overlay-image:
      minutes: 2
    dd-image:
      minutes: 10
    download-retry:
      minutes: 5
    http-download:
      minutes: 5
    lava-test-shell:
      minutes: 3
    nfs-deploy:
      minutes: 10
    power-off:
      seconds: 10
    bootloader-commands:
      minutes: 3
    bootloader-interrupt:
      seconds: 30
    u-boot-interrupt:
      seconds: 30
    umount-retry:
      seconds: 45
    auto-login-action:
      minutes: 2
    bootloader-action:
      minutes: 3
    uboot-action:
      minutes: 3
    uboot-commands:
      minutes: 3
    bootloader-retry:
      minutes: 3
    boot-qemu-image:
      minutes: 2
    boot-image-retry:
      minutes: 2
    flash-uboot-ums:
      minutes: 20
    musca-deploy:
      minutes: 3
    musca-boot:
      minutes: 1
    unmount-musca-usbmsd:
      seconds: 30
    pdu-reboot:
      seconds: 30
    reset-device:
      seconds: 30
  connections:
    dd-image:
      minutes: 10
    uboot-commands:
      seconds: 30
    bootloader-commands:
      seconds: 30
    auto-login-action:
      minutes: 2
    bootloader-interrupt:
      seconds: 30
    u-boot-interrupt:
      seconds: 30
    lava-test-shell:
      seconds: 10
    lava-docker-test-shell:
      seconds: 10
"""

TARGET_DICT={"aktualizr-toml": "[logger]\nloglevel = 2\n\n[p11]\nmodule = \"\"\npass = \"\"\nuptane_key_id = \"\"\ntls_ca_id = \"\"\ntls_pkey_id = \"\"\ntls_clientcert_id = \"\"\n\n[tls]\nserver = \"https://ota-lite.foundries.io:8443\"\nserver_url_path = \"\"\nca_source = \"file\"\npkey_source = \"file\"\ncert_source = \"file\"\n\n[provision]\nserver = \"https://ota-lite.foundries.io:8443\"\np12_password = \"\"\nexpiry_days = \"36000\"\nprovision_path = \"\"\ndevice_id = \"\"\nprimary_ecu_serial = \"\"\nprimary_ecu_hardware_id = \"imx8mmevk\"\necu_registration_endpoint = \"https://ota-lite.foundries.io:8443/director/ecus\"\nmode = \"DeviceCred\"\n\n[uptane]\npolling_sec = 300\ndirector_server = \"https://ota-lite.foundries.io:8443/director\"\nrepo_server = \"https://ota-lite.foundries.io:8443/repo\"\nkey_source = \"file\"\nkey_type = \"RSA2048\"\nforce_install_completion = False\nsecondary_config_file = \"\"\nsecondary_preinstall_wait_sec = 600\n\n[pacman]\ntype = \"ostree+compose_apps\"\nos = \"\"\nsysroot = \"\"\nostree_server = \"https://ota-lite.foundries.io:8443/treehub\"\nimages_path = \"/var/sota/images\"\npackages_file = \"/usr/package.manifest\"\nfake_need_reboot = False\ncallback_program = \"/var/sota/aklite-callback.sh\"\ncompose_apps_root = \"/var/sota/compose-apps\"\ntags = \"master\"\n\n[storage]\ntype = \"sqlite\"\npath = \"/var/sota/\"\nsqldb_path = \"sql.db\"\nuptane_metadata_path = \"/var/sota/metadata\"\nuptane_private_key_path = \"ecukey.der\"\nuptane_public_key_path = \"ecukey.pub\"\ntls_cacert_path = \"root.crt\"\ntls_pkey_path = \"pkey.pem\"\ntls_clientcert_path = \"client.pem\"\n\n[import]\nbase_path = \"/var/sota/import\"\nuptane_private_key_path = \"\"\nuptane_public_key_path = \"\"\ntls_cacert_path = \"/var/sota/root.crt\"\ntls_pkey_path = \"/var/sota/pkey.pem\"\ntls_clientcert_path = \"/var/sota/client.pem\"\n\n[telemetry]\nreport_network = True\nreport_config = True\n\n[bootloader]\nrollback_mode = \"uboot_masked\"\nreboot_sentinel_dir = \"/var/run/aktualizr-session\"\nreboot_sentinel_name = \"need_reboot\"\nreboot_command = \"/sbin/reboot\"\n\n", "hardware-info": {"capabilities": {"cp15_barrier": True, "setend": True, "smp": "Symmetric Multi-Processing", "swp": True, "tagged_addr_disabled": True}, "children": [{"children": [{"businfo": "cpu@0", "capabilities": {"aes": "AES instructions", "asimd": "Advanced SIMD", "cpufreq": "CPU Frequency scaling", "cpuid": True, "crc32": "CRC extension", "evtstrm": "Event stream", "fp": "Floating point instructions", "pmull": "PMULL instruction", "sha1": "SHA1 instructions", "sha2": "SHA2 instructions"}, "capacity": 1800000000, "claimed": True, "class": "processor", "description": "CPU", "id": "cpu:0", "physid": "0", "product": "cpu", "size": 1800000000, "units": "Hz"}, {"businfo": "cpu@1", "capabilities": {"aes": "AES instructions", "asimd": "Advanced SIMD", "cpufreq": "CPU Frequency scaling", "cpuid": True, "crc32": "CRC extension", "evtstrm": "Event stream", "fp": "Floating point instructions", "pmull": "PMULL instruction", "sha1": "SHA1 instructions", "sha2": "SHA2 instructions"}, "capacity": 1800000000, "claimed": True, "class": "processor", "description": "CPU", "id": "cpu:1", "physid": "1", "product": "cpu", "size": 1800000000, "units": "Hz"}, {"businfo": "cpu@2", "capabilities": {"aes": "AES instructions", "asimd": "Advanced SIMD", "cpufreq": "CPU Frequency scaling", "cpuid": True, "crc32": "CRC extension", "evtstrm": "Event stream", "fp": "Floating point instructions", "pmull": "PMULL instruction", "sha1": "SHA1 instructions", "sha2": "SHA2 instructions"}, "capacity": 1800000000, "claimed": True, "class": "processor", "description": "CPU", "id": "cpu:2", "physid": "2", "product": "cpu", "size": 1800000000, "units": "Hz"}, {"businfo": "cpu@3", "capabilities": {"aes": "AES instructions", "asimd": "Advanced SIMD", "cpufreq": "CPU Frequency scaling", "cpuid": True, "crc32": "CRC extension", "evtstrm": "Event stream", "fp": "Floating point instructions", "pmull": "PMULL instruction", "sha1": "SHA1 instructions", "sha2": "SHA2 instructions"}, "capacity": 1800000000, "claimed": True, "class": "processor", "description": "CPU", "id": "cpu:3", "physid": "3", "product": "cpu", "size": 1800000000, "units": "Hz"}, {"businfo": "cpu@4", "claimed": True, "class": "processor", "description": "CPU", "disabled": True, "id": "cpu:4", "physid": "4", "product": "idle-states"}, {"businfo": "cpu@5", "claimed": True, "class": "processor", "description": "CPU", "disabled": True, "id": "cpu:5", "physid": "5", "product": "l2-cache0"}, {"claimed": True, "class": "memory", "description": "System memory", "id": "memory", "physid": "6", "size": 2045693952, "units": "bytes"}], "claimed": True, "class": "bus", "description": "Motherboard", "id": "core", "physid": "0"}, {"children": [{"businfo": "mmc@0:0001:1", "capabilities": {"sdio": True}, "claimed": True, "class": "generic", "description": "SDIO Device", "id": "device", "logicalname": "mmc0:0001:1", "physid": "0", "serial": "0"}], "claimed": True, "class": "bus", "description": "MMC Host", "id": "mmc0", "logicalname": "mmc0", "physid": "1"}, {"claimed": True, "class": "bus", "description": "MMC Host", "id": "mmc1", "logicalname": "mmc1", "physid": "2"}, {"children": [{"businfo": "mmc@2:0001", "capabilities": {"mmc": True}, "children": [{"claimed": True, "class": "generic", "id": "interface:0", "logicalname": "/dev/mmcblk2rpmb", "physid": "1"}, {"capabilities": {"partitioned": "Partitioned disk", "partitioned:dos": "MS-DOS partition table"}, "children": [{"capabilities": {"bootable": "Bootable partition (active)", "fat": "Windows FAT", "initialized": "initialized volume", "primary": "Primary partition"}, "capacity": 87240704, "class": "volume", "configuration": {"FATs": "2", "filesystem": "fat", "label": "boot"}, "description": "Windows FAT volume", "id": "volume:0", "physid": "1", "serial": "5b3c-9223", "size": 87238656, "vendor": "mkfs.fat", "version": "FAT16"}, {"capabilities": {"dir_nlink": "directories with 65000+ subdirs", "ext2": "EXT2/EXT3", "ext4": True, "extended_attributes": "Extended Attributes", "extents": "extent-based allocation", "huge_files": "16TB+ files", "initialized": "initialized volume", "journaled": True, "large_files": "4GB+ files", "primary": "Primary partition", "recover": "needs recovery"}, "capacity": 15665725440, "claimed": True, "class": "volume", "configuration": {"created": "2021-01-28 11:27:02", "filesystem": "ext4", "label": "otaroot", "lastmountpoint": "/rootfs", "modified": "2021-01-29 15:17:42", "mount.fstype": "ext4", "mount.options": "rw,relatime", "mounted": "2021-01-29 15:17:42", "state": "mounted"}, "description": "EXT4 volume", "dev": "179:2", "id": "volume:1", "logicalname": ["/dev/mmcblk2p2", "/sysroot", "/", "/boot", "/usr", "/var"], "physid": "2", "serial": "c6183363-9b9b-498c-b7d8-eb849672408f", "size": 15665725440, "vendor": "Linux", "version": "1.0"}], "claimed": True, "class": "generic", "configuration": {"logicalsectorsize": "512", "sectorsize": "512", "signature": "f24cd3de"}, "id": "interface:1", "logicalname": "/dev/mmcblk2", "physid": "2", "size": 15758000128}], "claimed": True, "class": "generic", "date": "08/2020", "description": "SD/MMC Device", "id": "device", "physid": "1", "product": "DG4016", "serial": "448766742", "vendor": "Unknown (69)"}], "claimed": True, "class": "bus", "description": "MMC Host", "id": "mmc2", "logicalname": "mmc2", "physid": "3"}, {"claimed": True, "class": "multimedia", "description": "imxspdif", "id": "sound:0", "logicalname": ["card0", "/dev/snd/controlC0", "/dev/snd/pcmC0D0c", "/dev/snd/pcmC0D0p"], "physid": "4"}, {"claimed": True, "class": "multimedia", "description": "imxaudiomicfil", "id": "sound:1", "logicalname": ["card1", "/dev/snd/controlC1", "/dev/snd/pcmC1D0c"], "physid": "5"}, {"claimed": True, "class": "multimedia", "description": "wm8524audio", "id": "sound:2", "logicalname": ["card2", "/dev/snd/controlC2", "/dev/snd/pcmC2D0p"], "physid": "6"}, {"capabilities": {"platform": True}, "claimed": True, "class": "input", "id": "input:0", "logicalname": ["input0", "/dev/input/event0"], "physid": "7", "product": "30370000.snvs:snvs-powerkey"}, {"capabilities": {"platform": True}, "claimed": True, "class": "input", "id": "input:1", "logicalname": ["input1", "/dev/input/event1"], "physid": "8", "product": "bd718xx-pwrkey"}, {"capabilities": {"1000bt-fd": "1Gbit/s (full duplex)", "100bt": "100Mbit/s", "100bt-fd": "100Mbit/s (full duplex)", "10bt": "10Mbit/s", "10bt-fd": "10Mbit/s (full duplex)", "autonegotiation": "Auto-negotiation", "ethernet": True, "mii": "Media Independant Interface", "physical": "Physical interface", "tp": "twisted pair"}, "capacity": 1000000000, "claimed": True, "class": "network", "configuration": {"autonegotiation": "on", "broadcast": "yes", "driver": "fec", "driverversion": "Revision: 1.0", "duplex": "full", "ip": "192.168.0.40", "link": "yes", "multicast": "yes", "port": "MII", "speed": "1Gbit/s"}, "description": "Ethernet interface", "id": "network", "logicalname": "eth0", "physid": "9", "serial": "00:04:9f:06:e9:1f", "size": 1000000000, "units": "bit/s"}], "claimed": True, "class": "system", "description": "Computer", "id": "imx8mmevk", "product": "FSL i.MX8MM EVK board", "width": 64}, "updates": [{"correlation-id": "17-ed4c4efa-1f03-4a83-9bda-0c51e5b78238", "target": "imx8mmevk-lmp-17", "version": "17", "time": "2021-01-29T15:15:35Z"}, {"correlation-id": "14-07511851-67a1-4b0e-9165-1411713f6532", "target": "imx8mmevk-lmp-14", "version": "14", "time": "2021-01-29T12:44:59Z"}], "active-config": {"created-at": "2021-01-29T12:44:54", "applied-at": "2021-01-29T12:44:55", "reason": "Set Wireguard pubkey from fioconfig", "files": [{"name": "wireguard-client", "value": "enabled=0\n\npubkey=bERQE8Eq9vvlhIhq8atxsLt+qrZU9YYqnMYBOk8Nkx0=", "unencrypted": True}]}, "uuid": "2afef1d6-11a1-4d04-84c2-6d273789dccf", "owner": "600e91e5a6034fee7f021221", "factory": "milosz-rpi3", "name": "imx8mm-01", "created-at": "2021-01-29T12:44:54+00:00", "last-seen": "2021-02-01T09:50:02+00:00", "ostree-hash": "90b8cb57dd02c331b8450c846d1f3411458800eb02978b4d9a70132e63dc2f63", "target-name": "imx8mmevk-lmp-17", "current-update": "", "device-tags": ["master"], "tag": "master", "docker-apps": ["fiotest", "shellhttpd"], "network-info": {"hostname": "imx8mmevk", "local_ipv4": "192.168.0.40", "mac": "00:04:9f:06:e9:1f"}, "up-to-date": False, "public-key": "-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEoHMYa/0a8k+s0hwSkTI1wenGz1/E\nknpdM+dcpoR/0qmU8reKGsB+hD/lcb+H9r40gz1tFQREoF23tNK1Im6XIw==\n-----END PUBLIC KEY-----\n", "is-wave": False}


class ProjectTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="testProject1",
            secret="webhooksecret",
            lava_url="http://lava.example.com/api/v0.2/",
            lava_api_token="lavatoken",
        )

    @patch('requests.post')
    def test_submit_lava_job(self, post_mock):
        definition = "lava test definition"
        response_mock = MagicMock()
        response_mock.status_code = 201
        response_mock.json.return_value = {'job_ids': ['123']}
        post_mock.return_value = response_mock

        ret_list = self.project.submit_lava_job(definition)
        post_mock.assert_called()
        self.assertEqual(ret_list, ['123'])


class LAVADeviceTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="testProject1",
            secret="webhooksecret",
            lava_url="http://lava.example.com/api/v0.2/",
            lava_api_token="lavatoken",
        )
        self.pduagent1 = PDUAgent.objects.create(
            name="pduagent1",
            state=PDUAgent.STATE_ONLINE,
            version="1.0",
            token="token"
        )
        self.device_type1 = LAVADeviceType.objects.create(
            name="device-type-1",
            net_interface="eth0",
            project=self.project,
        )
        self.lava_device1 = LAVADevice.objects.create(
            device_type = self.device_type1,
            name = "device-type-1-1",
            auto_register_name = "ota_device_1",
            project = self.project,
            pduagent=self.pduagent1
        )

    @patch("requests.put")
    @patch("requests.get")
    def test_request_maintenance(self, get_mock, put_mock):
        response_mock = MagicMock()
        response_mock.status_code = 200
        response_mock.text = DEVICE_DICT
        get_mock.return_value = response_mock
        put_response_mock = MagicMock()
        put_response_mock.status_code = 200
        put_mock.return_value = response_mock

        self.lava_device1.request_maintenance()
        self.lava_device1.refresh_from_db()
        self.assertIsNotNone(self.lava_device1.ota_started)
        self.assertEqual(self.lava_device1.controlled_by, LAVADevice.CONTROL_PDU)
        get_mock.assert_called()
        put_mock.assert_called()

    @patch("requests.put")
    @patch("requests.get")
    def test_request_online(self, get_mock, put_mock):
        response_mock = MagicMock()
        response_mock.status_code = 200
        response_mock.text = DEVICE_DICT
        get_mock.return_value = response_mock
        put_response_mock = MagicMock()
        put_response_mock.status_code = 200
        put_mock.return_value = response_mock

        self.lava_device1.request_online()
        self.lava_device1.refresh_from_db()
        self.assertEqual(self.lava_device1.controlled_by, LAVADevice.CONTROL_LAVA)
        get_mock.assert_called()
        put_mock.assert_called()

    @patch("requests.get")
    def test_get_current_target(self, get_mock):
        response_mock = MagicMock()
        response_mock.status_code = 200
        response_mock.json.return_value = TARGET_DICT
        get_mock.return_value = response_mock
        target = self.lava_device1.get_current_target()
        get_mock.assert_called()
        self.assertEqual(target, TARGET_DICT)

    @patch("requests.delete")
    def test_remove_from_factory(self, delete_mock):
        response_mock = MagicMock()
        response_mock.status_code = 200
        delete_mock.return_value = response_mock
        self.lava_device1.remove_from_factory()
        delete_mock.assert_called()


class TaskTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="testProject1",
            secret="webhooksecret",
            lava_url="http://lava.example.com/api/v0.2/",
            lava_api_token="lavatoken",
        )
        self.previous_build = Build.objects.create(
            url="https://example.com/build/1/",
            project=self.project,
            build_id="1"
        )
        self.pduagent1 = PDUAgent.objects.create(
            name="pduagent1",
            state=PDUAgent.STATE_ONLINE,
            version="1.0",
            token="token"
        )
        self.previous_build_run_1 = Run.objects.create(
            build=self.previous_build,
            device_type="device-type-1",
            ostree_hash="previousHash",
            run_name="device-type-1"
        )
        self.build = Build.objects.create(
            url="https://example.com/build/2/",
            project=self.project,
            build_id="2"
        )
        self.build_run = Run.objects.create(
            build=self.build,
            device_type="device-type-1",
            ostree_hash="currentHash",
            run_name="device-type-1"
        )

        self.device_type1 = LAVADeviceType.objects.create(
            name="device-type-1",
            net_interface="eth0",
            project=self.project,
        )
        self.lava_device1 = LAVADevice.objects.create(
            device_type = self.device_type1,
            name = "device-type-1-1",
            auto_register_name = "ota_device_1",
            project = self.project,
            pduagent=self.pduagent1
        )

    @patch('conductor.core.tasks._get_os_tree_hash', return_value="someHash1")
    @patch('conductor.core.models.Project.submit_lava_job', return_value=[123])
    @patch('conductor.core.tasks.create_ota_job')
    def test_create_build_run(self, ota_job_mock, submit_lava_job_mock, get_hash_mock):
        run_name = "device-type-1"
        run_url = f"{self.build.url}runs/{run_name}/"
        create_build_run(self.build.id, run_url, run_name)
        submit_lava_job_mock.assert_called_once()
        get_hash_mock.assert_called_once()
        ota_job_mock.assert_called_once()

    @patch('conductor.core.tasks._get_os_tree_hash', return_value="someHash1")
    @patch('conductor.core.models.Project.submit_lava_job', return_value=[123])
    @patch('conductor.core.tasks.create_ota_job')
    def test_create_build_run_ota(self, ota_job_mock, submit_lava_job_mock, get_hash_mock):
        run_name = "device-type-1"
        run_url = f"{self.build.url}runs/{run_name}/"
        create_build_run(self.build.id, run_url, run_name, LAVAJob.JOB_OTA)
        submit_lava_job_mock.assert_called_once()
        get_hash_mock.assert_called_once()
        ota_job_mock.assert_not_called()

    @patch('conductor.core.tasks.create_build_run')
    def test_create_ota_job(self, create_build_run_mock):
        run_name = "device-type-1"
        previous_run_url = f"{self.previous_build.url}runs/{run_name}/"
        run_url = f"{self.build.url}runs/{run_name}/"
        create_ota_job(self.build.id, run_url, run_name)
        create_build_run_mock.assert_called_with(
            self.previous_build.id,
            previous_run_url,
            run_name,
            lava_job_type=LAVAJob.JOB_OTA
        )

    #def test_update_build_commit_id(self):
    @patch("requests.get")
    @patch("conductor.core.models.PDUAgent.save")
    def test_device_pdu_action_on(self, save_mock, get_mock):
        response_mock = MagicMock()
        response_mock.status_code = 200
        response_mock.text = DEVICE_DICT
        get_mock.return_value = response_mock
        device_pdu_action(self.lava_device1.pk)
        save_mock.assert_called()

    @patch("requests.get")
    @patch("conductor.core.models.PDUAgent.save")
    def test_device_pdu_action_off(self, save_mock, get_mock):
        response_mock = MagicMock()
        response_mock.status_code = 200
        response_mock.text = DEVICE_DICT
        get_mock.return_value = response_mock
        device_pdu_action(self.lava_device1.pk, power_on=False)
        save_mock.assert_called()

    @patch("conductor.core.tasks.report_test_results")
    @patch("conductor.core.tasks.device_pdu_action")
    @patch("conductor.core.models.LAVADevice.get_current_target", return_value=TARGET_DICT)
    @patch("conductor.core.models.LAVADevice.request_online")
    def test_check_ota_completed(
            self,
            request_online_mock,
            get_current_target_mock,
            device_pdu_action_mock,
            report_test_results_mock):
        self.lava_device1.controlled_by = LAVADevice.CONTROL_PDU
        ota_started_datetime = datetime.now() - timedelta(minutes=31)
        self.lava_device1.ota_started = ota_started_datetime
        self.lava_device1.save()
        check_ota_completed()
        self.lava_device1.refresh_from_db()
        self.assertEqual(self.lava_device1.controlled_by, LAVADevice.CONTROL_LAVA)
        request_online_mock.assert_called()
        device_pdu_action_mock.assert_called()
        report_test_results_mock.assert_called()
