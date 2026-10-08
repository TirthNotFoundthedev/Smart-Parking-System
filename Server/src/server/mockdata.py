import random
import sqlite3
import uuid
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "parking.db"
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute(
    """
CREATE TABLE IF NOT EXISTS parkingslots (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    digitalstatus INTEGER NOT NULL,
    physicalstatus INTEGER NOT NULL,
    floor TEXT NOT NULL
)
"""
)

cur.execute(
    """CREATE TABLE IF NOT EXISTS logs (
    id TEXT PRIMARY KEY,
    slotid TEXT NOT NULL,
    starttime DATETIME NOT NULL,
    endtime DATETIME,
    userid TEXT NOT NULL
)""")


floors = ["B1","B2"]
digi_states = [0, 1]
phys_states = [0, 1]

# Build mock records
mock_slots = [
    (
        str(uuid.uuid4()),
        f"SLOT-{i+1:03d}",
        random.choice(digi_states),
        random.choice(phys_states),
        random.choice(floors),
    )
    for i in range(5)
]

# Bulk insert
cur.executemany(
    """
    INSERT INTO parkingslots (id, name, digitalstatus, physicalstatus, floor)
    VALUES (?, ?, ?, ?, ?)
""",
    mock_slots,
)

conn.commit()
conn.close()
print("5 test slots dumped into the database.")