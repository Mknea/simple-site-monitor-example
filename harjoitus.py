import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, cast

import aiohttp
import aiosqlite
from quart import Quart, render_template

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

LOG_DB_FILE_PATH = "logs.db"
LOG_DB_TABLE_NAME = "monitoring_logs"

MONITORING_SERVER_DEFAULT_PORT = 5000

@dataclass(frozen=True)
class Target:
    url: str
    content_requirements: List[str]


@dataclass(frozen=True)
class Config:
    interval: int
    targets: List[Target]


class LogStatus(str, Enum):
    CONN_OK = "CONN_OK"
    CONN_NOK = "CONN_NOK"
    CONTENT_OK = "CONTENT_OK"
    CONTENT_NOK = "CONTENT_NOK"


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
    timestamp: Optional[datetime] = None
):
    if not timestamp:
        timestamp = datetime.now()
    print(timestamp, url, status, details)
    # FIXME: Would probably be more efficient to wrap this in class and reuse same conn
    async with aiosqlite.connect(LOG_DB_FILE_PATH) as db:
        await db.execute(f"""INSERT INTO '{LOG_DB_TABLE_NAME}'
                ('timestamp', 'url', 'duration', 'status', 'details')
                VALUES (?, ?, ?, ?, ?);
            """,
            (timestamp, url, duration, status, details)
        )
        await db.commit()


async def read_monitored_urls() -> List[str]:
    async with aiosqlite.connect(LOG_DB_FILE_PATH) as db:
        async with db.execute(f"SELECT DISTINCT(url) FROM {LOG_DB_TABLE_NAME}") as cur:
            return [ x[0] for x in await cur.fetchall()]


@dataclass
class MonitoringDetails:
    timestamp: datetime
    duration: int
    status: LogStatus
    details: str


async def get_monitoring_data(urls: List[str]) -> Dict[str, Optional[MonitoringDetails]]:
    data: Dict[str, Optional[MonitoringDetails]] = {}
    
    async with aiosqlite.connect(LOG_DB_FILE_PATH) as db:
        db.row_factory = aiosqlite.Row
        for url in urls:
            async with db.execute(f"""SELECT * FROM {LOG_DB_TABLE_NAME}
                WHERE url=? AND duration IS NOT NULL
                ORDER BY timestamp
                DESC LIMIT 1;""",
                (url,)
            ) as cur:
                latest_request_row = await cur.fetchone()
            if not latest_request_row:
                data[url] = None
                continue
            details = MonitoringDetails(
                timestamp=latest_request_row["timestamp"],
                duration=latest_request_row["duration"],
                status=latest_request_row["status"],
                details=latest_request_row["details"]
            )
            async with db.execute(f"""SELECT * FROM {LOG_DB_TABLE_NAME}
                WHERE url=? AND duration IS NULL AND timestamp > ?
                ORDER BY timestamp
                DESC LIMIT 1;""",
                (url, details.timestamp)
            ) as cur:
                latest_content_validation_row = await cur.fetchone()
            if latest_content_validation_row:
                details.status = latest_content_validation_row["status"]
                details.details = latest_content_validation_row["details"]
            data[url] = details
    return data


def read_config(path: str) -> dict:
    file_path = DEFAULT_CONFIG_PATH if not path else path
    with open(file_path, mode="r") as file:
        return json.load(file)


def parse_config(cmd_interval: Optional[int], config: dict):
    # Won't validate this too much. Could use some schemaparser but what's the point?
    targets = config.get("targets")
    if not isinstance(targets, list):
        raise TypeError("Config file not in correct format!")
    interval: Optional[int] = config.get("interval")
    if not cmd_interval and not interval:
        raise ValueError(
            "Required to pass check interval either in config file or through commandline"
        )
    if cmd_interval:
        interval = cmd_interval
    interval = cast(int, interval)
    parsed_targets = []
    for item in targets:
        parsed_targets.append(
            Target(item["url"], item["req"] if item.get("req") else [])
        )
    return Config(interval, parsed_targets)


# Inspiration from:
# https://dev.to/cheviana/monitoring-sync-and-async-network-calls-in-python-using-tig-stack-3al5


async def on_request_start(session, trace_config_ctx, params):
    trace_config_ctx.request_start = asyncio.get_event_loop().time()


async def on_request_end(session, trace_config_ctx, params: aiohttp.TraceRequestEndParams):
    elapsed_time = round(
        (asyncio.get_event_loop().time() - trace_config_ctx.request_start) * 1000
    )
    await write_log_db_entry(
        url=trace_config_ctx.trace_request_ctx["url"],
        status=LogStatus.CONN_OK,
        duration=elapsed_time,
    )

async def on_request_exception(session, trace_config_ctx, params: aiohttp.TraceRequestExceptionParams):
    elapsed_time = round(
        (asyncio.get_event_loop().time() - trace_config_ctx.request_start) * 1000
    )
    await write_log_db_entry(
        url=trace_config_ctx.trace_request_ctx["url"],
        status=LogStatus.CONN_NOK,
        duration=elapsed_time,
        details=str(params.exception)
    )


class Profiler(aiohttp.TraceConfig):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.on_request_start.append(on_request_start)
        self.on_request_end.append(on_request_end)
        self.on_request_exception.append(on_request_exception)


async def get_response_text(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    try:
        async with session.get(url, trace_request_ctx={"url": url}) as response:
            response.raise_for_status()
            return await response.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return None


async def get_and_validate_content(
    session: aiohttp.ClientSession, url: str, content_requirements: List[str]
):
    text = await get_response_text(session, url)
    if text is None:
        # When exception was thrown: no need to validate already failed requests content
        return
    if content_requirements:
        for expected_text in content_requirements:
            if expected_text not in text:
                await write_log_db_entry(
                    url=url,
                    status=LogStatus.CONTENT_NOK,
                    duration=None,
                    details=f"{expected_text} not found response content"
                )
                return
        await write_log_db_entry(
            url=url,
            status=LogStatus.CONTENT_OK,
            duration=None,
        )


async def monitor(config: Config):
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=float(config.interval)),
        trace_configs=[Profiler()],
    ) as session:
        while True:
            await asyncio.gather(
                *[
                    get_and_validate_content(
                        session, target.url, target.content_requirements
                    )
                    for target in config.targets
                ]
            )
            print("---------------")
            print(await read_monitored_urls())
            await asyncio.sleep(config.interval)


app = Quart(__name__, template_folder='templates')


@app.before_serving
async def startup():
    loop = asyncio.get_event_loop()
    await init_log_db()
    loop.create_task(monitor(app.config["monitoring_config"]))


@app.after_serving
async def shutdown():
    print("Closed event loop")


@app.get("/")
async def get_monitoring_page():
    urls = await read_monitored_urls()
    if not urls:
        # Probably possible to have problems if immediately accessed when DB empty on fresh start
        await asyncio.sleep(app.config["monitoring_config"].interval)
        urls = await read_monitored_urls()
    data = await get_monitoring_data(urls)
    return await render_template('index.html', monitored_items=data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Monitor the status and optionally content of given websites in given interval."
            "Pass the URLs and optionally content requirements in config file."
            ""
        )
    )
    parser.add_argument(
        "-i", "--interval", help="Check interval in seconds", type=int, required=False
    )
    parser.add_argument(
        "-f",
        "--file",
        help="Config file path, if omitted expected to be 'config.json' in the same dir as this file",
        type=str,
    )
    parser.add_argument(
        "--port",
        help=f"Monitoring server port. Default: {MONITORING_SERVER_DEFAULT_PORT}",
        type=int,
        default=MONITORING_SERVER_DEFAULT_PORT,
    )
    args = parser.parse_args()
    config = read_config(args.file)
    config = parse_config(args.interval, config)
    app.config["monitoring_config"] = config
    app.run(port=args.port)
    
