import os, shutil, gzip
from datetime import datetime

BACKUP_DIR = os.path.join("stock_cache", "master", "backups")
ROLLING_PATH = os.path.join("backend", "rolling.json.gz")

def restore_latest_backup():
    backups = [
        os.path.join(BACKUP_DIR, f)
        for f in os.listdir(BACKUP_DIR)
        if f.endswith(".gz")
    ]
    if not backups:
        print("⚠️ No rolling backups found.")
        return
    latest = max(backups, key=os.path.getmtime)
    print(f"🔍 Latest backup: {latest}")
    shutil.copy2(latest, ROLLING_PATH)
    print(f"✅ Restored rolling.json.gz from {latest}")

if __name__ == "__main__":
    restore_latest_backup()
