import hashlib
import struct
from pathlib import Path

ELF_MAGIC = b'\x7fELF'
APPIMAGE_TYPE1_MAGIC = b'AI\x01'
APPIMAGE_TYPE2_MAGIC = b'AI\x02'
APPIMAGE_MAGIC_PREFIX = b'AI'
SQUASHFS_MAGIC = b'hsqs'
DWARFS_MAGIC = b'DWARFS'

ARCH_MAP = {
    3: 'i386',
    62: 'x86_64',
    40: 'aarch64',
    183: 'arm',
    50: 'ia64',
    20: 'ppc',
    21: 'ppc64le',
    243: 'riscv64',
    258: 'loongarch64',
}


class AppImageMetadata:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        self.type = 0
        self.ei_class = 0
        self.ei_data = 0
        self.machine = 0
        self.architecture = 'unknown'
        self.payload_offset = 0
        self.fs_type = 'unknown'
        self.sha256 = ''
        self.update_info = None
        self.parse()

    def parse(self):
        with open(self.path, 'rb') as f:
            header = f.read(64)

        if header[:4] != ELF_MAGIC:
            raise ValueError(f"Not an ELF file: {self.path}")

        if len(header) < 64:
            raise ValueError(f"File too small for ELF header: {self.path}")
        self.ei_class = header[4]
        self.ei_data = header[5]
        is_64 = self.ei_class == 2
        endian = '<' if self.ei_data == 1 else '>'

        ai_magic = header[8:11]
        if ai_magic == APPIMAGE_TYPE2_MAGIC:
            self.type = 2
        elif ai_magic == APPIMAGE_TYPE1_MAGIC:
            self.type = 1
        elif ai_magic[:2] == APPIMAGE_MAGIC_PREFIX:
            self.type = 2
        else:
            raise ValueError(f"Not an AppImage (no AI magic): {self.path}")

        self.machine = struct.unpack(endian + 'H', header[18:20])[0]
        if is_64:
            shoff = struct.unpack(endian + 'Q', header[40:48])[0]
            shentsize = struct.unpack(endian + 'H', header[58:60])[0]
            shnum = struct.unpack(endian + 'H', header[60:62])[0]
        else:
            shoff = struct.unpack(endian + 'I', header[32:36])[0]
            shentsize = struct.unpack(endian + 'H', header[46:48])[0]
            shnum = struct.unpack(endian + 'H', header[48:50])[0]

        self.architecture = ARCH_MAP.get(self.machine, 'unknown')
        self.payload_offset = shoff + shnum * shentsize

        with open(self.path, 'rb') as f:
            f.seek(self.payload_offset)
            payload_magic = f.read(8)

        if payload_magic[:4] == SQUASHFS_MAGIC:
            self.fs_type = 'squashfs'
        elif payload_magic[:6] == DWARFS_MAGIC:
            self.fs_type = 'dwarfs'

        sha = hashlib.sha256()
        with open(self.path, 'rb') as f:
            f.seek(self.payload_offset)
            while True:
                data = f.read(65536)
                if not data:
                    break
                sha.update(data)
        self.sha256 = sha.hexdigest()

        self.update_info = self._extract_upd_info(is_64, endian, shoff, shentsize, shnum)

    def _extract_upd_info(self, is_64, endian, shoff, shentsize, shnum):
        if shnum == 0 or shentsize == 0:
            return None

        with open(self.path, 'rb') as f:
            header = f.read(64)

        if is_64:
            shstrndx = struct.unpack(endian + 'H', header[62:64])[0]
        else:
            shstrndx = struct.unpack(endian + 'H', header[50:52])[0]

        with open(self.path, 'rb') as f:
            f.seek(shoff)
            sections = f.read(shnum * shentsize)

        if shstrndx >= shnum:
            return None
        strtab_entry = sections[shstrndx * shentsize:(shstrndx + 1) * shentsize]
        if is_64:
            strtab_offset = struct.unpack(endian + 'Q', strtab_entry[24:32])[0]
            strtab_size = struct.unpack(endian + 'Q', strtab_entry[32:40])[0]
        else:
            strtab_offset = struct.unpack(endian + 'I', strtab_entry[16:20])[0]
            strtab_size = struct.unpack(endian + 'I', strtab_entry[20:24])[0]

        with open(self.path, 'rb') as f:
            f.seek(strtab_offset)
            strtab = f.read(strtab_size)

        for i in range(shnum):
            entry = sections[i * shentsize:(i + 1) * shentsize]
            if len(entry) < shentsize:
                continue

            if is_64:
                sh_name_idx = struct.unpack(endian + 'I', entry[0:4])[0]
                name_end = strtab.find(b'\x00', sh_name_idx)
                name = strtab[sh_name_idx:name_end].decode('ascii', errors='ignore') if name_end > sh_name_idx else ''
                if name == '.upd_info':
                    sh_offset = struct.unpack(endian + 'Q', entry[24:32])[0]
                    sh_size = struct.unpack(endian + 'Q', entry[32:40])[0]
                    with open(self.path, 'rb') as f:
                        f.seek(sh_offset)
                        return f.read(sh_size).decode('utf-8', errors='ignore').strip()
            else:
                sh_name_idx = struct.unpack(endian + 'I', entry[0:4])[0]
                name_end = strtab.find(b'\x00', sh_name_idx)
                name = strtab[sh_name_idx:name_end].decode('ascii', errors='ignore') if name_end > sh_name_idx else ''
                if name == '.upd_info':
                    sh_offset = struct.unpack(endian + 'I', entry[16:20])[0]
                    sh_size = struct.unpack(endian + 'I', entry[20:24])[0]
                    with open(self.path, 'rb') as f:
                        f.seek(sh_offset)
                        return f.read(sh_size).decode('utf-8', errors='ignore').strip()

        return None

    def get_update_info(self):
        if self.update_info and self.update_info.strip('\x00').strip():
            return self.update_info.strip('\x00').strip()
        return None

    def get_display_name(self):
        return Path(self.path).stem


def is_appimage(path):
    try:
        AppImageMetadata(path)
        return True
    except (ValueError, OSError):
        return False
