import argparse
import logging

from qbitbridge.utils import load_config


def main() -> None:
    """
    The main entry point for the service.
    Handles argument parsing, configuration loading, and app startup.
    """
    parser = argparse.ArgumentParser(description="Start the QBitBridge service.")

    # Give a good default for development
    parser.add_argument(
        "--config",
        type=str,
        help="Path to the YAML configuration file (default: config.yaml)",
        default="config.yaml",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        help="Logging level (default: INFO [DEBUG, INFO, WARNING, ERROR, CRITICAL])",
        default="INFO",
    )
    parser.add_argument(
        "--test-shutdown",
        type=bool,
        help="Test the shutdown sequence of the qbitbridge",
        default=False,
    )

    args = parser.parse_args()

    # --- Load and validate configuration ---
    try:
        logging.basicConfig(level=args.log_level.upper())
        if args.test_shutdown:
            with load_config(args.config) as qbb:
                qbb.launch()
        else:
            qbb = load_config(args.config)
            qbb.launch()

    except Exception as e:
        logging.error(f"Failed to start service. Exception: {str(e)}")
        raise


if __name__ == "__main__":
    main()
