"""A damaged backup must be rejected before stopping writers or changing data."""

import asyncio
import sys


async def command(*args):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "scripts/run-env.py",
        sys.executable,
        "scripts/database.py",
        *map(str, args),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    return process.returncode, output.decode()


async def test_damaged_native_backup_cannot_replace_database(owner, tmp_path):
    before = (await owner.get("/api/v1/me")).json()
    code, output = await command("backup", tmp_path)
    assert code == 0, output
    archive = next(tmp_path.glob("*.dump"))
    assert archive.stat().st_mode & 0o077 == 0
    assert archive.with_suffix(".dump.list").is_file()
    with archive.open("ab") as corrupt:
        corrupt.write(b"damaged-backup")
    code, output = await command("restore", archive, "--confirm-replace-database")
    assert code != 0 and "Backup checksum mismatch" in output, output
    after = await owner.get("/api/v1/me")
    assert after.status_code == 200 and after.json() == before
