import argparse

from src.config import parse_config, read_config
from src.monitor_app import MONITORING_SERVER_DEFAULT_PORT, app

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
