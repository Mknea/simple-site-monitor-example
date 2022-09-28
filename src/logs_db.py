from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

import aiosqlite

LOG_DB_FILE_PATH = "logs.db"
LOG_DB_TABLE_NAME = "monitoring_logs"


class LogStatus(str, Enum):
    CONN_OK = "CONN_OK"
    CONN_NOK = "CONN_NOK"
    CONTENT_OK = "CONTENT_OK"
    CONTENT_NOK = "CONTENT_NOK"


@dataclass
class MonitoringDetails:
    timestamp: datetime
    duration: int
    status: LogStatus
    details: str


async def init_log_db():
    async with aiosqlite.connect(LOG_DB_FILE_PATH) as db:
        await db.execute(
            f"""CREATE TABLE IF NOT EXISTS {LOG_DB_TABLE_NAME} (
                    id INTEGER PRIMARY KEY,
                    timestamp timestamp,
                    url TEXT NOT NULL,
                    duration INTEGER,
                    status TEXT NOT NULL,
                    details TEXT NOT NULL
                );"""
            # Allow duration to be null when content is parsed
        )
        await db.commit()


async def write_log_db_entry(
    url: str,
    status: LogStatus,
    duration: Optional[int],
    details: str = "",
    timestamp: Optional[datetime] = None,
):
    if not timestamp:
        timestamp = datetime.now()
    print(timestamp, url, status, details)
    # FIXME: Would probably be more efficient to wrap this in class and reuse same conn
    async with aiosqlite.connect(LOG_DB_FILE_PATH) as db:
        await db.execute(
            f"""INSERT INTO '{LOG_DB_TABLE_NAME}'
                ('timestamp', 'url', 'duration', 'status', 'details')
                VALUES (?, ?, ?, ?, ?);
            """,
            (timestamp, url, duration, status, details),
        )
        await db.commit()


async def read_monitored_urls() -> List[str]:
    async with aiosqlite.connect(LOG_DB_FILE_PATH) as db:
        async with db.execute(f"SELECT DISTINCT(url) FROM {LOG_DB_TABLE_NAME}") as cur:
            return [x[0] for x in await cur.fetchall()]


async def get_monitoring_data(
    urls: List[str],
) -> Dict[str, Optional[MonitoringDetails]]:
    data: Dict[str, Optional[MonitoringDetails]] = {}

    async with aiosqlite.connect(LOG_DB_FILE_PATH) as db:
        db.row_factory = aiosqlite.Row
        for url in urls:
            async with db.execute(
                f"""SELECT * FROM {LOG_DB_TABLE_NAME}
                WHERE url=? AND duration IS NOT NULL
                ORDER BY timestamp
                DESC LIMIT 1;""",
                (url,),
            ) as cur:
                latest_request_row = await cur.fetchone()
            if not latest_request_row:
                data[url] = None
                continue
            details = MonitoringDetails(
                timestamp=latest_request_row["timestamp"],
                duration=latest_request_row["duration"],
                status=latest_request_row["status"],
                details=latest_request_row["details"],
            )
            async with db.execute(
                f"""SELECT * FROM {LOG_DB_TABLE_NAME}
                WHERE url=? AND duration IS NULL AND timestamp > ?
                ORDER BY timestamp
                DESC LIMIT 1;""",
                (url, details.timestamp),
            ) as cur:
                latest_content_validation_row = await cur.fetchone()
            if latest_content_validation_row:
                details.status = latest_content_validation_row["status"]
                details.details = latest_content_validation_row["details"]
            data[url] = details
    return data
