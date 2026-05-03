"""
kernel.fs.ext2 — Pure-Python ext2 (rev 1) read/write driver.

Conforms to the async FS protocol defined de-facto by `kernel.fs.tmpfs`:
    Filesystem.root() -> FSNode
    FSNode.stat / read / write / truncate / readdir / lookup / create / unlink

Scope
-----
* Regular files and directories (read + write).
* Symlinks: inline (target stored in i_block as bytes when size <= 60) read-only —
  not creatable from this driver yet.
* Inode + block allocator: bitmap-scan first-fit. No fragmentation policy.
* Indirect blocks: single + double indirect handled. Triple indirect is
  recognised in the on-disk layout but read/write through it raises
  NotImplementedError.
* No journal (it's ext2). No xattrs / quotas / ACLs / htree.

The driver expects a block-device adapter implementing the small async API:

    async read_sector(lba: int) -> bytes        # 512-byte sector
    async write_sector(lba: int, data: bytes)   # 512-byte sector
    num_sectors: int

Internally we batch sector I/O into block-sized (typically 4096) reads/writes.
"""

from __future__ import annotations

import asyncio
import struct
from typing import Optional

from kernel.fs.vfs import FSNode, Filesystem, Stat, InodeType


# ── On-disk constants ────────────────────────────────────────────────────────

EXT2_SUPER_MAGIC = 0xEF53
SUPERBLOCK_OFFSET = 1024              # bytes from start of partition

# Inode number constants
EXT2_ROOT_INO = 2

# i_mode bits
EXT2_S_IFSOCK = 0xC000
EXT2_S_IFLNK  = 0xA000
EXT2_S_IFREG  = 0x8000
EXT2_S_IFBLK  = 0x6000
EXT2_S_IFDIR  = 0x4000
EXT2_S_IFCHR  = 0x2000
EXT2_S_IFIFO  = 0x1000
EXT2_S_IFMT   = 0xF000

# directory entry file_type (when filetype incompat feature is set)
EXT2_FT_UNKNOWN  = 0
EXT2_FT_REG_FILE = 1
EXT2_FT_DIR      = 2
EXT2_FT_CHRDEV   = 3
EXT2_FT_BLKDEV   = 4
EXT2_FT_FIFO     = 5
EXT2_FT_SOCK     = 6
EXT2_FT_SYMLINK  = 7

# Feature flags we care about
EXT2_FEATURE_INCOMPAT_FILETYPE = 0x0002
EXT2_FEATURE_INCOMPAT_SUPPORTED = EXT2_FEATURE_INCOMPAT_FILETYPE  # everything else = bail
EXT2_FEATURE_RO_COMPAT_SPARSE_SUPER = 0x0001
EXT2_FEATURE_RO_COMPAT_LARGE_FILE   = 0x0002
EXT2_FEATURE_RO_COMPAT_BTREE_DIR    = 0x0004
EXT2_FEATURE_RO_COMPAT_SUPPORTED = (
    EXT2_FEATURE_RO_COMPAT_SPARSE_SUPER
    | EXT2_FEATURE_RO_COMPAT_LARGE_FILE
    | EXT2_FEATURE_RO_COMPAT_BTREE_DIR
)

# Indirect block layout
EXT2_NDIR_BLOCKS  = 12
EXT2_IND_BLOCK    = EXT2_NDIR_BLOCKS
EXT2_DIND_BLOCK   = EXT2_IND_BLOCK + 1
EXT2_TIND_BLOCK   = EXT2_DIND_BLOCK + 1
EXT2_N_BLOCKS     = EXT2_TIND_BLOCK + 1

SECTOR_SIZE = 512


# ── struct formats ───────────────────────────────────────────────────────────

# Superblock (first 1024 bytes; rev 1 extends to 264 fields, we pull the
# fields we need rather than parse the whole monster).
_SB_FMT = "<IIIIIIIIIIIIIIIIIIIIHHHHHHIIIIHHIHHIIIIIIHH"
# 0  s_inodes_count        u32
# 1  s_blocks_count        u32
# 2  s_r_blocks_count      u32
# 3  s_free_blocks_count   u32
# 4  s_free_inodes_count   u32
# 5  s_first_data_block    u32
# 6  s_log_block_size      u32
# 7  s_log_frag_size       u32
# 8  s_blocks_per_group    u32
# 9  s_frags_per_group     u32
#10  s_inodes_per_group    u32
#11  s_mtime               u32
#12  s_wtime               u32
#13  s_mnt_count           u16  → packed as u32 actually (next two are u16)
# Easier: parse field by field.

# Block group descriptor — 32 bytes (rev 0 layout, default for 4K blocks)
_BGD_FMT = "<IIIHHHH"
_BGD_SIZE = struct.calcsize(_BGD_FMT) + 12   # 32 bytes (12 reserved)
assert _BGD_SIZE == 32

# inode (rev 1, 256 bytes — we only touch the first 128)
_INODE_BASE_FMT = "<HHIIIIIHHII"
# i_mode u16, i_uid u16, i_size u32, i_atime u32, i_ctime u32, i_mtime u32,
# i_dtime u32, i_gid u16, i_links_count u16, i_blocks u32, i_flags u32
_INODE_BASE_SIZE = struct.calcsize(_INODE_BASE_FMT)
# After: i_osd1 u32, then i_block[15] u32, then i_generation u32, ...
# We'll just pull fields by offset where needed.


# ── Block device adapter (host-side, file-backed) ────────────────────────────

class FileBlockDevice:
    """Async block device backed by a host file.  For host-side tests."""

    def __init__(self, path: str, *, readonly: bool = False) -> None:
        self._path = path
        self._mode = "rb" if readonly else "r+b"
        self._fp = open(path, self._mode)
        self._fp.seek(0, 2)
        size = self._fp.tell()
        if size % SECTOR_SIZE:
            raise ValueError(f"image size {size} not a multiple of {SECTOR_SIZE}")
        self._num_sectors = size // SECTOR_SIZE

    @property
    def num_sectors(self) -> int:
        return self._num_sectors

    async def read_sector(self, lba: int) -> bytes:
        if lba < 0 or lba >= self._num_sectors:
            raise IndexError(f"lba {lba} out of range")
        self._fp.seek(lba * SECTOR_SIZE)
        return self._fp.read(SECTOR_SIZE)

    async def write_sector(self, lba: int, data: bytes) -> None:
        if len(data) != SECTOR_SIZE:
            raise ValueError("sector write must be 512 bytes")
        if lba < 0 or lba >= self._num_sectors:
            raise IndexError(f"lba {lba} out of range")
        self._fp.seek(lba * SECTOR_SIZE)
        self._fp.write(data)
        self._fp.flush()

    def close(self) -> None:
        self._fp.close()


# ── Driver ───────────────────────────────────────────────────────────────────

class _Superblock:
    """Mutable in-memory mirror of the parts of the superblock we need."""

    __slots__ = (
        "inodes_count", "blocks_count",
        "free_blocks_count", "free_inodes_count",
        "first_data_block",
        "log_block_size",
        "blocks_per_group", "inodes_per_group",
        "rev_level", "first_ino", "inode_size",
        "feature_compat", "feature_incompat", "feature_ro_compat",
        "raw",
    )

    @classmethod
    def parse(cls, blob: bytes) -> "_Superblock":
        sb = cls.__new__(cls)
        sb.raw = bytearray(blob)
        u = lambda off, fmt: struct.unpack_from(fmt, blob, off)[0]
        sb.inodes_count       = u(0,   "<I")
        sb.blocks_count       = u(4,   "<I")
        sb.free_blocks_count  = u(12,  "<I")
        sb.free_inodes_count  = u(16,  "<I")
        sb.first_data_block   = u(20,  "<I")
        sb.log_block_size     = u(24,  "<I")
        sb.blocks_per_group   = u(32,  "<I")
        sb.inodes_per_group   = u(40,  "<I")
        magic                 = u(56,  "<H")
        if magic != EXT2_SUPER_MAGIC:
            raise ValueError(f"bad ext2 magic {magic:#x}")
        sb.rev_level          = u(76,  "<I")
        sb.first_ino          = u(84,  "<I") if sb.rev_level >= 1 else 11
        sb.inode_size         = u(88,  "<H") if sb.rev_level >= 1 else 128
        sb.feature_compat     = u(92,  "<I") if sb.rev_level >= 1 else 0
        sb.feature_incompat   = u(96,  "<I") if sb.rev_level >= 1 else 0
        sb.feature_ro_compat  = u(100, "<I") if sb.rev_level >= 1 else 0
        return sb

    def block_size(self) -> int:
        return 1024 << self.log_block_size

    def num_groups(self) -> int:
        return (self.blocks_count - self.first_data_block + self.blocks_per_group - 1) // self.blocks_per_group

    def serialize_counters(self) -> None:
        struct.pack_into("<I", self.raw, 12, self.free_blocks_count)
        struct.pack_into("<I", self.raw, 16, self.free_inodes_count)


class Ext2FS:
    """ext2 filesystem driver.

    Construct via `await Ext2FS.mount(blockdev)`.
    """

    def __init__(self, blockdev) -> None:
        self._dev = blockdev
        self._sb: Optional[_Superblock] = None
        self._block_size = 0
        self._sectors_per_block = 0
        self._bgdt: list[bytearray] = []   # one bytearray per group descriptor (32 bytes)
        # Caches (write-through; not strictly necessary but reduces dev I/O)
        self._block_cache: dict[int, bytearray] = {}
        self._lock = asyncio.Lock()

    # ── Mount / unmount ──────────────────────────────────────────────────────

    @classmethod
    async def mount(cls, blockdev) -> "Ext2FS":
        self = cls(blockdev)
        await self._read_superblock()
        await self._read_bgdt()
        return self

    async def _read_superblock(self) -> None:
        # Superblock is exactly 1024 bytes at byte offset 1024.
        sec0 = await self._dev.read_sector(2)   # bytes 1024..1535
        sec1 = await self._dev.read_sector(3)   # bytes 1536..2047
        blob = sec0 + sec1
        self._sb = _Superblock.parse(blob)
        if self._sb.feature_incompat & ~EXT2_FEATURE_INCOMPAT_SUPPORTED:
            raise NotImplementedError(
                f"unsupported incompat features: {self._sb.feature_incompat:#x}"
            )
        if self._sb.feature_ro_compat & ~EXT2_FEATURE_RO_COMPAT_SUPPORTED:
            raise NotImplementedError(
                f"unsupported ro_compat features: {self._sb.feature_ro_compat:#x}"
            )
        self._block_size = self._sb.block_size()
        if self._block_size < SECTOR_SIZE or self._block_size % SECTOR_SIZE:
            raise ValueError(f"bad block size {self._block_size}")
        self._sectors_per_block = self._block_size // SECTOR_SIZE

    async def _read_bgdt(self) -> None:
        # Block group descriptor table starts at the block AFTER the superblock.
        # For block_size > 1024 the SB lives in block 0 (at byte offset 1024),
        # so BGDT is in block 1.  For block_size == 1024 SB is in block 1, BGDT
        # in block 2.
        bgdt_block = self._sb.first_data_block + 1 if self._block_size == 1024 else 1
        ngroups = self._sb.num_groups()
        bgdt_bytes = await self._read_block(bgdt_block)
        # If BGDT spans more than one block, read the rest.
        total_bytes = ngroups * _BGD_SIZE
        cursor = self._block_size
        extra = bytearray()
        while cursor < total_bytes:
            extra += await self._read_block(bgdt_block + len(extra) // self._block_size + 1)
            cursor += self._block_size
        full = bytes(bgdt_bytes) + bytes(extra)
        self._bgdt = [bytearray(full[i * _BGD_SIZE:(i + 1) * _BGD_SIZE]) for i in range(ngroups)]
        self._bgdt_block = bgdt_block

    # ── Block I/O ────────────────────────────────────────────────────────────

    async def _read_block(self, block_no: int) -> bytearray:
        if block_no in self._block_cache:
            return self._block_cache[block_no]
        sectors = []
        for s in range(self._sectors_per_block):
            sectors.append(await self._dev.read_sector(block_no * self._sectors_per_block + s))
        buf = bytearray(b"".join(sectors))
        self._block_cache[block_no] = buf
        return buf

    async def _write_block(self, block_no: int, data: bytes | bytearray) -> None:
        if len(data) != self._block_size:
            raise ValueError(f"block write must be {self._block_size} bytes, got {len(data)}")
        for s in range(self._sectors_per_block):
            chunk = bytes(data[s * SECTOR_SIZE:(s + 1) * SECTOR_SIZE])
            await self._dev.write_sector(block_no * self._sectors_per_block + s, chunk)
        # Update cache (own the bytes — make our own copy)
        self._block_cache[block_no] = bytearray(data)

    def _zero_block(self) -> bytearray:
        return bytearray(self._block_size)

    # ── BGDT helpers ─────────────────────────────────────────────────────────

    def _bgd_get(self, group: int) -> tuple[int, int, int, int, int, int]:
        """Returns (block_bitmap, inode_bitmap, inode_table, free_blocks,
        free_inodes, used_dirs)."""
        d = self._bgdt[group]
        bb, ib, it = struct.unpack_from("<III", d, 0)
        fb, fi, ud = struct.unpack_from("<HHH", d, 12)
        return bb, ib, it, fb, fi, ud

    def _bgd_set_counters(self, group: int, free_blocks: int, free_inodes: int, used_dirs: int) -> None:
        d = self._bgdt[group]
        struct.pack_into("<HHH", d, 12, free_blocks, free_inodes, used_dirs)

    async def _flush_bgdt(self) -> None:
        # Reassemble and write
        full = b"".join(bytes(d) for d in self._bgdt)
        # Pad to block size
        nblocks = (len(full) + self._block_size - 1) // self._block_size
        for bi in range(nblocks):
            chunk = full[bi * self._block_size:(bi + 1) * self._block_size]
            if len(chunk) < self._block_size:
                chunk = chunk + b"\x00" * (self._block_size - len(chunk))
            await self._write_block(self._bgdt_block + bi, chunk)

    async def _flush_superblock(self) -> None:
        self._sb.serialize_counters()
        # SB lives at byte offset 1024 → sector 2 (and into sector 3).
        await self._dev.write_sector(2, bytes(self._sb.raw[0:512]))
        await self._dev.write_sector(3, bytes(self._sb.raw[512:1024]))

    # ── Bitmap allocation ────────────────────────────────────────────────────

    async def _alloc_in_bitmap(self, bitmap_block: int, count: int) -> int:
        """Find first clear bit < count, set it, write back. Returns bit index.
        Raises OSError if full."""
        bm = await self._read_block(bitmap_block)
        for byte_i in range((count + 7) // 8):
            byte = bm[byte_i]
            if byte == 0xFF:
                continue
            for bit in range(8):
                idx = byte_i * 8 + bit
                if idx >= count:
                    break
                if not (byte & (1 << bit)):
                    bm[byte_i] = byte | (1 << bit)
                    await self._write_block(bitmap_block, bm)
                    return idx
        raise OSError("bitmap full")

    async def _free_in_bitmap(self, bitmap_block: int, idx: int) -> None:
        bm = await self._read_block(bitmap_block)
        byte_i, bit = divmod(idx, 8)
        if not (bm[byte_i] & (1 << bit)):
            return  # already free; tolerate
        bm[byte_i] &= ~(1 << bit) & 0xFF
        await self._write_block(bitmap_block, bm)

    async def _alloc_block(self) -> int:
        """Allocate one data block.  Returns absolute block number."""
        sb = self._sb
        for g in range(sb.num_groups()):
            bb, ib, it, fb, fi, ud = self._bgd_get(g)
            if fb == 0:
                continue
            # Number of blocks in this group
            group_blocks = sb.blocks_per_group
            if g == sb.num_groups() - 1:
                # last group may be short
                used = sb.first_data_block + g * sb.blocks_per_group
                group_blocks = sb.blocks_count - used
            local = await self._alloc_in_bitmap(bb, group_blocks)
            self._bgd_set_counters(g, fb - 1, fi, ud)
            sb.free_blocks_count -= 1
            await self._flush_bgdt()
            await self._flush_superblock()
            return sb.first_data_block + g * sb.blocks_per_group + local
        raise OSError("no free blocks")

    async def _free_block(self, block_no: int) -> None:
        sb = self._sb
        rel = block_no - sb.first_data_block
        g, local = divmod(rel, sb.blocks_per_group)
        bb, ib, it, fb, fi, ud = self._bgd_get(g)
        await self._free_in_bitmap(bb, local)
        self._bgd_set_counters(g, fb + 1, fi, ud)
        sb.free_blocks_count += 1
        await self._flush_bgdt()
        await self._flush_superblock()

    async def _alloc_inode(self, *, is_dir: bool) -> int:
        sb = self._sb
        for g in range(sb.num_groups()):
            bb, ib, it, fb, fi, ud = self._bgd_get(g)
            if fi == 0:
                continue
            local = await self._alloc_in_bitmap(ib, sb.inodes_per_group)
            new_ud = ud + 1 if is_dir else ud
            self._bgd_set_counters(g, fb, fi - 1, new_ud)
            sb.free_inodes_count -= 1
            await self._flush_bgdt()
            await self._flush_superblock()
            return g * sb.inodes_per_group + local + 1   # inodes are 1-based
        raise OSError("no free inodes")

    async def _free_inode(self, ino: int, *, was_dir: bool) -> None:
        sb = self._sb
        zero = ino - 1
        g, local = divmod(zero, sb.inodes_per_group)
        bb, ib, it, fb, fi, ud = self._bgd_get(g)
        await self._free_in_bitmap(ib, local)
        new_ud = ud - 1 if was_dir else ud
        self._bgd_set_counters(g, fb, fi + 1, new_ud)
        sb.free_inodes_count += 1
        await self._flush_bgdt()
        await self._flush_superblock()

    # ── Inode read/write ─────────────────────────────────────────────────────

    def _inode_location(self, ino: int) -> tuple[int, int]:
        sb = self._sb
        zero = ino - 1
        g, local = divmod(zero, sb.inodes_per_group)
        _bb, _ib, it, _fb, _fi, _ud = self._bgd_get(g)
        byte_off = local * sb.inode_size
        block = it + byte_off // self._block_size
        offset_in_block = byte_off % self._block_size
        return block, offset_in_block

    async def _read_inode(self, ino: int) -> bytearray:
        block, off = self._inode_location(ino)
        buf = await self._read_block(block)
        return bytearray(buf[off:off + self._sb.inode_size])

    async def _write_inode(self, ino: int, raw: bytes | bytearray) -> None:
        if len(raw) != self._sb.inode_size:
            raise ValueError("inode raw size mismatch")
        block, off = self._inode_location(ino)
        buf = await self._read_block(block)
        buf[off:off + self._sb.inode_size] = raw
        await self._write_block(block, buf)

    # ── Inode field accessors ────────────────────────────────────────────────

    @staticmethod
    def _i_mode(raw: bytes) -> int:        return struct.unpack_from("<H", raw, 0)[0]
    @staticmethod
    def _i_size(raw: bytes) -> int:        return struct.unpack_from("<I", raw, 4)[0]
    @staticmethod
    def _i_links(raw: bytes) -> int:       return struct.unpack_from("<H", raw, 26)[0]
    @staticmethod
    def _i_blocks(raw: bytes) -> int:      return struct.unpack_from("<I", raw, 28)[0]
    @staticmethod
    def _i_block_n(raw: bytes, n: int) -> int:
        # i_block[15] starts at offset 40
        return struct.unpack_from("<I", raw, 40 + n * 4)[0]
    @staticmethod
    def _set_i_block_n(raw: bytearray, n: int, val: int) -> None:
        struct.pack_into("<I", raw, 40 + n * 4, val)
    @staticmethod
    def _set_i_mode(raw: bytearray, mode: int) -> None:
        struct.pack_into("<H", raw, 0, mode)
    @staticmethod
    def _set_i_size(raw: bytearray, size: int) -> None:
        struct.pack_into("<I", raw, 4, size & 0xFFFFFFFF)
        # large_file: high 32 bits of size for regular files live in i_dir_acl
        # at offset 108. For dirs we never need this.
    @staticmethod
    def _set_i_links(raw: bytearray, n: int) -> None:
        struct.pack_into("<H", raw, 26, n)
    @staticmethod
    def _set_i_blocks(raw: bytearray, n: int) -> None:
        struct.pack_into("<I", raw, 28, n)
    @staticmethod
    def _set_i_dtime(raw: bytearray, t: int) -> None:
        struct.pack_into("<I", raw, 20, t)
    @staticmethod
    def _set_i_mtime(raw: bytearray, t: int) -> None:
        struct.pack_into("<I", raw, 16, t)
    @staticmethod
    def _set_i_ctime(raw: bytearray, t: int) -> None:
        struct.pack_into("<I", raw, 12, t)
    @staticmethod
    def _set_i_atime(raw: bytearray, t: int) -> None:
        struct.pack_into("<I", raw, 8, t)

    # ── Block-pointer walker (read) ──────────────────────────────────────────

    def _ptrs_per_block(self) -> int:
        return self._block_size // 4

    async def _resolve_block(self, raw: bytes, file_block: int) -> int:
        """Return the physical block number for logical file_block.
        Returns 0 if hole (block not allocated)."""
        ppb = self._ptrs_per_block()
        if file_block < EXT2_NDIR_BLOCKS:
            return self._i_block_n(raw, file_block)
        rel = file_block - EXT2_NDIR_BLOCKS
        if rel < ppb:
            ind = self._i_block_n(raw, EXT2_IND_BLOCK)
            if ind == 0:
                return 0
            blk = await self._read_block(ind)
            return struct.unpack_from("<I", blk, rel * 4)[0]
        rel -= ppb
        if rel < ppb * ppb:
            dind = self._i_block_n(raw, EXT2_DIND_BLOCK)
            if dind == 0:
                return 0
            blk = await self._read_block(dind)
            l1, l2 = divmod(rel, ppb)
            ind = struct.unpack_from("<I", blk, l1 * 4)[0]
            if ind == 0:
                return 0
            blk2 = await self._read_block(ind)
            return struct.unpack_from("<I", blk2, l2 * 4)[0]
        raise NotImplementedError("triple-indirect blocks not supported")

    async def _allocate_block_for(self, raw: bytearray, file_block: int) -> int:
        """Ensure logical file_block has a backing block; return the phys block.
        Updates raw inode in-place (caller must persist)."""
        ppb = self._ptrs_per_block()
        if file_block < EXT2_NDIR_BLOCKS:
            phys = self._i_block_n(raw, file_block)
            if phys == 0:
                phys = await self._alloc_block()
                # zero
                await self._write_block(phys, self._zero_block())
                self._set_i_block_n(raw, file_block, phys)
                self._set_i_blocks(raw, self._i_blocks(raw) + self._sectors_per_block)
            return phys
        rel = file_block - EXT2_NDIR_BLOCKS
        if rel < ppb:
            ind = self._i_block_n(raw, EXT2_IND_BLOCK)
            if ind == 0:
                ind = await self._alloc_block()
                await self._write_block(ind, self._zero_block())
                self._set_i_block_n(raw, EXT2_IND_BLOCK, ind)
                self._set_i_blocks(raw, self._i_blocks(raw) + self._sectors_per_block)
            blk = await self._read_block(ind)
            phys = struct.unpack_from("<I", blk, rel * 4)[0]
            if phys == 0:
                phys = await self._alloc_block()
                await self._write_block(phys, self._zero_block())
                struct.pack_into("<I", blk, rel * 4, phys)
                await self._write_block(ind, blk)
                self._set_i_blocks(raw, self._i_blocks(raw) + self._sectors_per_block)
            return phys
        rel -= ppb
        if rel < ppb * ppb:
            dind = self._i_block_n(raw, EXT2_DIND_BLOCK)
            if dind == 0:
                dind = await self._alloc_block()
                await self._write_block(dind, self._zero_block())
                self._set_i_block_n(raw, EXT2_DIND_BLOCK, dind)
                self._set_i_blocks(raw, self._i_blocks(raw) + self._sectors_per_block)
            l1, l2 = divmod(rel, ppb)
            dblk = await self._read_block(dind)
            ind = struct.unpack_from("<I", dblk, l1 * 4)[0]
            if ind == 0:
                ind = await self._alloc_block()
                await self._write_block(ind, self._zero_block())
                struct.pack_into("<I", dblk, l1 * 4, ind)
                await self._write_block(dind, dblk)
                self._set_i_blocks(raw, self._i_blocks(raw) + self._sectors_per_block)
            iblk = await self._read_block(ind)
            phys = struct.unpack_from("<I", iblk, l2 * 4)[0]
            if phys == 0:
                phys = await self._alloc_block()
                await self._write_block(phys, self._zero_block())
                struct.pack_into("<I", iblk, l2 * 4, phys)
                await self._write_block(ind, iblk)
                self._set_i_blocks(raw, self._i_blocks(raw) + self._sectors_per_block)
            return phys
        raise NotImplementedError("triple-indirect blocks not supported")

    async def _free_all_blocks(self, raw: bytearray) -> None:
        """Free every data block referenced by this inode (direct + indirect)."""
        ppb = self._ptrs_per_block()
        # Direct
        for n in range(EXT2_NDIR_BLOCKS):
            b = self._i_block_n(raw, n)
            if b:
                await self._free_block(b)
                self._set_i_block_n(raw, n, 0)
        # Single indirect
        ind = self._i_block_n(raw, EXT2_IND_BLOCK)
        if ind:
            blk = await self._read_block(ind)
            for i in range(ppb):
                p = struct.unpack_from("<I", blk, i * 4)[0]
                if p:
                    await self._free_block(p)
            await self._free_block(ind)
            self._set_i_block_n(raw, EXT2_IND_BLOCK, 0)
        # Double indirect
        dind = self._i_block_n(raw, EXT2_DIND_BLOCK)
        if dind:
            dblk = await self._read_block(dind)
            for i in range(ppb):
                ind2 = struct.unpack_from("<I", dblk, i * 4)[0]
                if ind2:
                    blk2 = await self._read_block(ind2)
                    for j in range(ppb):
                        p = struct.unpack_from("<I", blk2, j * 4)[0]
                        if p:
                            await self._free_block(p)
                    await self._free_block(ind2)
            await self._free_block(dind)
            self._set_i_block_n(raw, EXT2_DIND_BLOCK, 0)
        # Triple indirect: not supported for write; skip free.
        self._set_i_blocks(raw, 0)

    # ── File-data read/write ─────────────────────────────────────────────────

    async def _file_size(self, raw: bytes) -> int:
        size = self._i_size(raw)
        # large_file: i_size_high lives at offset 108 (i_dir_acl) for regular files
        mode = self._i_mode(raw)
        if (mode & EXT2_S_IFMT) == EXT2_S_IFREG:
            high = struct.unpack_from("<I", raw, 108)[0]
            size |= high << 32
        return size

    def _set_file_size(self, raw: bytearray, size: int) -> None:
        struct.pack_into("<I", raw, 4, size & 0xFFFFFFFF)
        mode = self._i_mode(raw)
        if (mode & EXT2_S_IFMT) == EXT2_S_IFREG:
            struct.pack_into("<I", raw, 108, (size >> 32) & 0xFFFFFFFF)

    async def _read_file_data(self, ino: int, offset: int, n: int) -> bytes:
        raw = await self._read_inode(ino)
        size = await self._file_size(raw)
        if offset >= size:
            return b""
        end = min(offset + n, size)
        out = bytearray()
        bs = self._block_size
        cur = offset
        while cur < end:
            fb = cur // bs
            in_blk = cur % bs
            chunk = min(bs - in_blk, end - cur)
            phys = await self._resolve_block(raw, fb)
            if phys == 0:
                out += b"\x00" * chunk
            else:
                blk = await self._read_block(phys)
                out += blk[in_blk:in_blk + chunk]
            cur += chunk
        return bytes(out)

    async def _write_file_data(self, ino: int, offset: int, data: bytes) -> int:
        raw = await self._read_inode(ino)
        bs = self._block_size
        cur = offset
        end = offset + len(data)
        di = 0
        while cur < end:
            fb = cur // bs
            in_blk = cur % bs
            chunk = min(bs - in_blk, end - cur)
            phys = await self._allocate_block_for(raw, fb)
            blk = await self._read_block(phys)
            blk[in_blk:in_blk + chunk] = data[di:di + chunk]
            await self._write_block(phys, blk)
            cur += chunk
            di += chunk
        if end > await self._file_size(raw):
            self._set_file_size(raw, end)
        await self._write_inode(ino, raw)
        return len(data)

    async def _truncate_file(self, ino: int, size: int) -> None:
        raw = await self._read_inode(ino)
        cur_size = await self._file_size(raw)
        if size >= cur_size:
            self._set_file_size(raw, size)
            await self._write_inode(ino, raw)
            return
        # Free blocks past `size`. Cheap approach: free everything past the
        # block containing `size`, then zero tail of the partial block.
        bs = self._block_size
        ppb = self._ptrs_per_block()
        last_kept = (size + bs - 1) // bs   # exclusive
        # iterate all currently-allocated logical blocks beyond last_kept
        max_blocks = (cur_size + bs - 1) // bs
        for fb in range(last_kept, max_blocks):
            phys = await self._resolve_block(raw, fb)
            if phys != 0:
                await self._free_block(phys)
                # Clear the pointer in-place
                if fb < EXT2_NDIR_BLOCKS:
                    self._set_i_block_n(raw, fb, 0)
                elif fb - EXT2_NDIR_BLOCKS < ppb:
                    ind = self._i_block_n(raw, EXT2_IND_BLOCK)
                    blk = await self._read_block(ind)
                    struct.pack_into("<I", blk, (fb - EXT2_NDIR_BLOCKS) * 4, 0)
                    await self._write_block(ind, blk)
                else:
                    rel = fb - EXT2_NDIR_BLOCKS - ppb
                    dind = self._i_block_n(raw, EXT2_DIND_BLOCK)
                    dblk = await self._read_block(dind)
                    l1, l2 = divmod(rel, ppb)
                    ind = struct.unpack_from("<I", dblk, l1 * 4)[0]
                    iblk = await self._read_block(ind)
                    struct.pack_into("<I", iblk, l2 * 4, 0)
                    await self._write_block(ind, iblk)
                self._set_i_blocks(raw, max(0, self._i_blocks(raw) - self._sectors_per_block))
        # Zero tail of partial block if the new size lies inside an allocated block
        if size > 0 and (size % bs) != 0:
            fb = size // bs
            phys = await self._resolve_block(raw, fb)
            if phys != 0:
                blk = await self._read_block(phys)
                tail = size % bs
                for i in range(tail, bs):
                    blk[i] = 0
                await self._write_block(phys, blk)
        self._set_file_size(raw, size)
        await self._write_inode(ino, raw)

    # ── Directory handling ───────────────────────────────────────────────────

    async def _read_dir_entries(self, ino: int) -> list[tuple[str, int, int]]:
        """Returns list of (name, inode, file_type)."""
        raw = await self._read_inode(ino)
        size = await self._file_size(raw)
        out: list[tuple[str, int, int]] = []
        bs = self._block_size
        for fb in range(0, (size + bs - 1) // bs):
            phys = await self._resolve_block(raw, fb)
            if phys == 0:
                continue
            blk = await self._read_block(phys)
            off = 0
            while off < bs:
                ino_e, rec_len, name_len, file_type = struct.unpack_from("<IHBB", blk, off)
                if rec_len == 0:
                    break
                if ino_e != 0:
                    name = bytes(blk[off + 8:off + 8 + name_len]).decode("utf-8", errors="replace")
                    out.append((name, ino_e, file_type))
                off += rec_len
        return out

    @staticmethod
    def _dirent_size(name_len: int) -> int:
        # 8-byte fixed header + name, rounded up to 4
        return ((8 + name_len) + 3) & ~3

    async def _add_dir_entry(self, dir_ino: int, name: str, child_ino: int, file_type: int) -> None:
        name_b = name.encode("utf-8")
        if not name_b or len(name_b) > 255:
            raise ValueError("bad name")
        need = self._dirent_size(len(name_b))
        raw = await self._read_inode(dir_ino)
        size = await self._file_size(raw)
        bs = self._block_size
        # Walk existing blocks looking for room
        for fb in range((size + bs - 1) // bs):
            phys = await self._resolve_block(raw, fb)
            if phys == 0:
                continue
            blk = await self._read_block(phys)
            off = 0
            while off < bs:
                ino_e, rec_len, nlen, ftyp = struct.unpack_from("<IHBB", blk, off)
                if rec_len == 0:
                    break
                actual = self._dirent_size(nlen) if ino_e != 0 else 0
                slack = rec_len - actual
                if slack >= need:
                    # Split: shrink current to actual, add new entry in slack.
                    if ino_e != 0:
                        struct.pack_into("<H", blk, off + 4, actual)
                        new_off = off + actual
                    else:
                        new_off = off
                        actual = 0
                    struct.pack_into("<IHBB", blk, new_off, child_ino, rec_len - actual, len(name_b), file_type)
                    blk[new_off + 8:new_off + 8 + len(name_b)] = name_b
                    # Pad with zeros
                    pad_start = new_off + 8 + len(name_b)
                    pad_end = new_off + (rec_len - actual)
                    for i in range(pad_start, pad_end):
                        blk[i] = 0
                    await self._write_block(phys, blk)
                    return
                off += rec_len
        # No room — append a new block (a directory block always ends with one
        # entry whose rec_len fills to end of block).
        new_fb = (size // bs)
        phys = await self._allocate_block_for(raw, new_fb)
        blk = await self._read_block(phys)
        # Single entry filling whole block
        struct.pack_into("<IHBB", blk, 0, child_ino, bs, len(name_b), file_type)
        blk[8:8 + len(name_b)] = name_b
        for i in range(8 + len(name_b), bs):
            blk[i] = 0
        await self._write_block(phys, blk)
        new_size = (new_fb + 1) * bs
        self._set_file_size(raw, new_size)
        await self._write_inode(dir_ino, raw)

    async def _remove_dir_entry(self, dir_ino: int, name: str) -> tuple[int, int]:
        """Returns (child_ino, file_type) of the removed entry."""
        name_b = name.encode("utf-8")
        raw = await self._read_inode(dir_ino)
        size = await self._file_size(raw)
        bs = self._block_size
        for fb in range((size + bs - 1) // bs):
            phys = await self._resolve_block(raw, fb)
            if phys == 0:
                continue
            blk = await self._read_block(phys)
            off = 0
            prev_off = -1
            while off < bs:
                ino_e, rec_len, nlen, ftyp = struct.unpack_from("<IHBB", blk, off)
                if rec_len == 0:
                    break
                if ino_e != 0 and nlen == len(name_b) and bytes(blk[off + 8:off + 8 + nlen]) == name_b:
                    if prev_off >= 0:
                        # Merge into previous entry
                        prev_ino, prev_rl, prev_nl, prev_ft = struct.unpack_from("<IHBB", blk, prev_off)
                        struct.pack_into("<H", blk, prev_off + 4, prev_rl + rec_len)
                    else:
                        # First entry: zero out inode but keep rec_len
                        struct.pack_into("<I", blk, off, 0)
                    await self._write_block(phys, blk)
                    return ino_e, ftyp
                prev_off = off
                off += rec_len
        raise FileNotFoundError(name)

    async def _dir_is_empty(self, dir_ino: int) -> bool:
        for name, _ino, _ft in await self._read_dir_entries(dir_ino):
            if name not in (".", ".."):
                return False
        return True

    # ── Public root ──────────────────────────────────────────────────────────

    def root(self) -> "Ext2Node":
        return Ext2Node(self, EXT2_ROOT_INO)


# ── Node ─────────────────────────────────────────────────────────────────────

def _mode_to_inode_type(mode: int) -> InodeType:
    fmt = mode & EXT2_S_IFMT
    if fmt == EXT2_S_IFREG:  return InodeType.FILE
    if fmt == EXT2_S_IFDIR:  return InodeType.DIR
    if fmt == EXT2_S_IFLNK:  return InodeType.SYMLINK
    if fmt in (EXT2_S_IFCHR, EXT2_S_IFBLK): return InodeType.DEVICE
    if fmt == EXT2_S_IFIFO:  return InodeType.PIPE
    return InodeType.FILE

def _inode_type_to_mode(t: InodeType, perm: int) -> int:
    if t == InodeType.DIR:     return EXT2_S_IFDIR | (perm & 0o7777)
    if t == InodeType.SYMLINK: return EXT2_S_IFLNK | (perm & 0o7777)
    return EXT2_S_IFREG | (perm & 0o7777)

def _inode_type_to_filetype(t: InodeType) -> int:
    if t == InodeType.DIR:     return EXT2_FT_DIR
    if t == InodeType.SYMLINK: return EXT2_FT_SYMLINK
    return EXT2_FT_REG_FILE


class Ext2Node:
    """FSNode backed by an ext2 inode."""

    def __init__(self, fs: Ext2FS, ino: int) -> None:
        self._fs = fs
        self._ino = ino

    # ── stat ────────────────────────────────────────────────────────────────

    async def stat(self) -> Stat:
        raw = await self._fs._read_inode(self._ino)
        mode = self._fs._i_mode(raw)
        return Stat(
            inode_type=_mode_to_inode_type(mode),
            size=await self._fs._file_size(raw),
            mode=mode & 0o7777,
            nlink=self._fs._i_links(raw),
        )

    # ── read / write / truncate ─────────────────────────────────────────────

    async def read(self, offset: int, n: int) -> bytes:
        raw = await self._fs._read_inode(self._ino)
        mode = self._fs._i_mode(raw)
        kind = mode & EXT2_S_IFMT
        if kind == EXT2_S_IFDIR:
            raise IsADirectoryError
        if kind == EXT2_S_IFLNK:
            size = await self._fs._file_size(raw)
            if size <= 60:
                # Inline target stored in the i_block array (60 bytes)
                target = bytes(raw[40:40 + size])
                return target[offset:offset + n]
            # External symlink target stored as data blocks (rare for short links)
            return await self._fs._read_file_data(self._ino, offset, n)
        return await self._fs._read_file_data(self._ino, offset, n)

    async def write(self, offset: int, data: bytes) -> int:
        async with self._fs._lock:
            raw = await self._fs._read_inode(self._ino)
            mode = self._fs._i_mode(raw)
            if (mode & EXT2_S_IFMT) != EXT2_S_IFREG:
                raise IsADirectoryError
            return await self._fs._write_file_data(self._ino, offset, data)

    async def truncate(self, size: int = 0) -> None:
        async with self._fs._lock:
            raw = await self._fs._read_inode(self._ino)
            mode = self._fs._i_mode(raw)
            if (mode & EXT2_S_IFMT) != EXT2_S_IFREG:
                raise IsADirectoryError
            await self._fs._truncate_file(self._ino, size)

    # ── directory ops ───────────────────────────────────────────────────────

    async def readdir(self) -> list[str]:
        raw = await self._fs._read_inode(self._ino)
        if (self._fs._i_mode(raw) & EXT2_S_IFMT) != EXT2_S_IFDIR:
            raise NotADirectoryError
        entries = await self._fs._read_dir_entries(self._ino)
        return [name for name, _i, _t in entries]

    async def lookup(self, name: str) -> "Ext2Node":
        if name in ("", "."):
            return self
        raw = await self._fs._read_inode(self._ino)
        if (self._fs._i_mode(raw) & EXT2_S_IFMT) != EXT2_S_IFDIR:
            raise NotADirectoryError
        for n, ino, _ft in await self._fs._read_dir_entries(self._ino):
            if n == name:
                return Ext2Node(self._fs, ino)
        raise FileNotFoundError(name)

    async def create(self, name: str, inode_type: InodeType) -> "Ext2Node":
        async with self._fs._lock:
            raw = await self._fs._read_inode(self._ino)
            if (self._fs._i_mode(raw) & EXT2_S_IFMT) != EXT2_S_IFDIR:
                raise NotADirectoryError
            # Disallow duplicates
            for n, _i, _ft in await self._fs._read_dir_entries(self._ino):
                if n == name:
                    raise FileExistsError(name)

            is_dir = inode_type == InodeType.DIR
            new_ino = await self._fs._alloc_inode(is_dir=is_dir)
            new_raw = bytearray(self._fs._sb.inode_size)

            perm = 0o755 if is_dir else 0o644
            mode = _inode_type_to_mode(inode_type, perm)
            self._fs._set_i_mode(new_raw, mode)
            self._fs._set_i_size(new_raw, 0)
            self._fs._set_i_links(new_raw, 1)
            self._fs._set_i_blocks(new_raw, 0)
            now = 0   # we don't have a clock in tests; e2fsck doesn't care
            self._fs._set_i_atime(new_raw, now)
            self._fs._set_i_ctime(new_raw, now)
            self._fs._set_i_mtime(new_raw, now)

            await self._fs._write_inode(new_ino, new_raw)

            if is_dir:
                # Initialise '.' and '..' entries.
                await self._fs._add_dir_entry(new_ino, ".", new_ino, EXT2_FT_DIR)
                await self._fs._add_dir_entry(new_ino, "..", self._ino, EXT2_FT_DIR)
                # nlink = 2 (self + '.'), then we'll bump parent for '..'
                new_raw = await self._fs._read_inode(new_ino)
                self._fs._set_i_links(new_raw, 2)
                await self._fs._write_inode(new_ino, new_raw)
                # Bump parent nlink for the '..' back-reference
                parent_raw = await self._fs._read_inode(self._ino)
                self._fs._set_i_links(parent_raw, self._fs._i_links(parent_raw) + 1)
                await self._fs._write_inode(self._ino, parent_raw)

            ftype = _inode_type_to_filetype(inode_type)
            await self._fs._add_dir_entry(self._ino, name, new_ino, ftype)
            return Ext2Node(self._fs, new_ino)

    async def unlink(self, name: str) -> None:
        """Remove a child entry. Handles BOTH files (unlink) and directories
        (rmdir — but only when empty)."""
        async with self._fs._lock:
            raw = await self._fs._read_inode(self._ino)
            if (self._fs._i_mode(raw) & EXT2_S_IFMT) != EXT2_S_IFDIR:
                raise NotADirectoryError
            # Locate entry first to see what kind it is
            target_ino = None
            target_ft = None
            for n, ino, ft in await self._fs._read_dir_entries(self._ino):
                if n == name:
                    target_ino, target_ft = ino, ft
                    break
            if target_ino is None:
                raise FileNotFoundError(name)
            child_raw = await self._fs._read_inode(target_ino)
            child_mode = self._fs._i_mode(child_raw)
            child_kind = child_mode & EXT2_S_IFMT
            is_dir = (child_kind == EXT2_S_IFDIR)

            if is_dir:
                if not await self._fs._dir_is_empty(target_ino):
                    raise OSError(f"directory not empty: {name}")

            # Remove the dir entry from this directory.
            await self._fs._remove_dir_entry(self._ino, name)

            if is_dir:
                # Drop child's '.' and '..' (we just throw away the inode);
                # parent loses a link from the disappearing '..'.
                parent_raw = await self._fs._read_inode(self._ino)
                self._fs._set_i_links(parent_raw, max(0, self._fs._i_links(parent_raw) - 1))
                await self._fs._write_inode(self._ino, parent_raw)
                # Free child blocks then zero the inode entirely. We zero
                # rather than just clearing nlink+setting dtime because e2fsck
                # treats an unallocated inode that still has data fields set
                # as a corrupted orphan-list entry.
                await self._fs._free_all_blocks(child_raw)
                zero = bytearray(self._fs._sb.inode_size)
                await self._fs._write_inode(target_ino, zero)
                await self._fs._free_inode(target_ino, was_dir=True)
            else:
                # Decrement nlink; if zero, free.
                new_nlink = self._fs._i_links(child_raw) - 1
                self._fs._set_i_links(child_raw, max(0, new_nlink))
                if new_nlink <= 0:
                    await self._fs._free_all_blocks(child_raw)
                    zero = bytearray(self._fs._sb.inode_size)
                    await self._fs._write_inode(target_ino, zero)
                    await self._fs._free_inode(target_ino, was_dir=False)
                else:
                    await self._fs._write_inode(target_ino, child_raw)
