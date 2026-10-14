"""Process-owned file lock prevents a crashed backup from leaving maintenance stuck."""
from contextlib import contextmanager
from pathlib import Path
import os

@contextmanager
def backup_lock(data_dir):
    path=Path(data_dir)/'.backup.lock'
    stream=path.open('a+b')
    stream.seek(0,2)
    if stream.tell()==0: stream.write(b'0');stream.flush()
    stream.seek(0)
    acquired=False
    try:
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            acquired=True
        except OSError: pass
        yield acquired
    finally:
        if acquired:
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_UN)
        stream.close()

def maintenance_active(conn):
    if not conn.execute("SELECT 1 FROM runtime_state WHERE key='maintenance'").fetchone(): return False
    dbfile=conn.execute('PRAGMA database_list').fetchone()[2]
    with backup_lock(Path(dbfile).parent) as acquired:
        if not acquired: return True
        conn.execute("DELETE FROM runtime_state WHERE key='maintenance'")
        return False
