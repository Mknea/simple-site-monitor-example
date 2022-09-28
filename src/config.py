import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, cast

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


@dataclass(frozen=True)
class Target:
    url: str
    content_requirements: List[str]


@dataclass(frozen=True)
class Config:
    interval: int
    targets: List[Target]


def read_config(path: str) -> dict:
    file_path: Path | str = DEFAULT_CONFIG_PATH if not path else path
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
