import argparse
import asyncio
import json
import logging
import signal
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional

import aiohttp
import aiosqlite

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

LOG_DB_FILE_PATH = "logs.db"
LOG_DB_TABLE_NAME = "monitoring_logs"

@dataclass(frozen=True)
class Target:
    url: str
    content_requirements: List[str]


@dataclass(frozen=True)
class Config:
    interval: int
    targets: List[Target]


class LogStatus(str, Enum):
    CONN_OK = "OK"
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
                    duration INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    details TEXT NOT NULL
                );"""
        )
        await db.commit()


async def write_log_db_entry(
    url: str,
    status: LogStatus,
    duration: int = 0,
    details: str = "",
    timestamp: Optional[datetime] = None
):
    if not timestamp:
        timestamp = datetime.now()
    # FIXME: Would probably be more efficient to wrap this in class and reuse same conn
    async with aiosqlite.connect(LOG_DB_FILE_PATH) as db:
        await db.execute(f"""INSERT INTO '{LOG_DB_TABLE_NAME}'
                ('timestamp', 'url', 'duration', 'status', 'details')
                VALUES (?, ?, ?, ?, ?);
            """,
            (timestamp, url, duration, status, details)
        )
        await db.commit()


def read_config(path: str) -> dict:
    file_path = DEFAULT_CONFIG_PATH if not path else path
    with open(file_path, mode="r") as file:
        return json.load(file)


def parse_config(cmd_interval: Optional[int], config: dict):
    # Won't validate this too much. Could use some schemaparser but what's the point?
    targets: list = config.get("targets")
    if not isinstance(targets, list):
        raise TypeError("Config file not in correct format!")
    interval: int | None = config.get("interval")
    if not cmd_interval and not interval:
        raise ValueError(
            "Required to pass check interval either in config file or through commandline"
        )
    if cmd_interval and interval:
        logger.info(
            f"Overriding interval {interval} from config file with commandline argument {cmd_interval}"
        )
    parsed_targets = []
    for item in targets:
        parsed_targets.append(
            Target(item["url"], item["req"] if item.get("req") else [])
        )
    return Config(interval, parsed_targets)


def save_trace(*args):
    print(args)


# Inspiration from:
# https://dev.to/cheviana/monitoring-sync-and-async-network-calls-in-python-using-tig-stack-3al5


async def on_request_start(session, trace_config_ctx, params):
    trace_config_ctx.request_start = asyncio.get_event_loop().time()


async def on_request_end(session, trace_config_ctx, params: aiohttp.TraceRequestEndParams):
    elapsed_time = round(
        (asyncio.get_event_loop().time() - trace_config_ctx.request_start) * 1000
    )
    save_trace(datetime.now(), elapsed_time, {"domain": params.url.raw_host})
    await write_log_db_entry(
        url=trace_config_ctx.trace_request_ctx["url"],
        status=LogStatus.CONN_OK,
        duration=elapsed_time,
    )

async def on_request_exception(session, trace_config_ctx, params: aiohttp.TraceRequestExceptionParams):
    elapsed_time = round(
        (asyncio.get_event_loop().time() - trace_config_ctx.request_start) * 1000
    )
    save_trace(
        datetime.now(),
        "aiohttp_request_exception",
        elapsed_time,
        {
            "domain": params.url.raw_host,
            "exception_class": params.exception.__class__.__name__,
        },
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
                save_trace(
                    datetime.now(),
                    f"Failed validation: {expected_text} not in response!",
                    url,
                )


async def monitor(config: Config):
    await init_log_db()
    logger.info("Starting monitoring")
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
            await asyncio.sleep(config.interval)


async def shutdown(signal, loop):
    # Finalize asyncio loop
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]
    print(f"Cancelling {len(tasks)} outstanding tasks")
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()
    print("Stopped loop")


def main(config):
    print("Starting event loop")
    loop = asyncio.new_event_loop()
    signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
    for s in signals:
        loop.add_signal_handler(s, lambda s=s: asyncio.create_task(shutdown(s, loop)))
    try:
        loop.create_task(monitor(config))
        loop.run_forever()
    finally:
        loop.close()
        print("Closed event loop")


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
        help="Config file path, if omitted expected to be config.json in the same dir as this file",
        type=str,
    )
    args = parser.parse_args()
    config = read_config(args.file)
    config = parse_config(args.interval, config)
    logger.info("Sucessfully parsed config")
    main(config)
