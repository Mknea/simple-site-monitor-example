import asyncio
from typing import List, Optional

import aiohttp
from quart import Quart, render_template

from src.config import Config
from src.logs_db import (
    LogStatus,
    get_monitoring_data,
    init_log_db,
    read_monitored_urls,
    write_log_db_entry,
)

MONITORING_SERVER_DEFAULT_PORT = 5000

app = Quart(__name__, template_folder="templates")

# Inspiration from:
# https://dev.to/cheviana/monitoring-sync-and-async-network-calls-in-python-using-tig-stack-3al5


async def on_request_start(session, trace_config_ctx, params):
    trace_config_ctx.request_start = asyncio.get_event_loop().time()


async def on_request_end(
    session, trace_config_ctx, params: aiohttp.TraceRequestEndParams
):
    elapsed_time = round(
        (asyncio.get_event_loop().time() - trace_config_ctx.request_start) * 1000
    )
    await write_log_db_entry(
        url=trace_config_ctx.trace_request_ctx["url"],
        status=LogStatus.CONN_OK,
        duration=elapsed_time,
    )


async def on_request_exception(
    session, trace_config_ctx, params: aiohttp.TraceRequestExceptionParams
):
    elapsed_time = round(
        (asyncio.get_event_loop().time() - trace_config_ctx.request_start) * 1000
    )
    await write_log_db_entry(
        url=trace_config_ctx.trace_request_ctx["url"],
        status=LogStatus.CONN_NOK,
        duration=elapsed_time,
        details=str(params.exception),
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
    except (aiohttp.ClientError, asyncio.TimeoutError):
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
                    details=f"{expected_text} not found response content",
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
            await asyncio.sleep(config.interval)


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
    return await render_template("index.html", monitored_items=data)
