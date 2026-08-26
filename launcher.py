import argparse
import logging

from qbitbridge.utils import (
    load_config,
    probe_cluster_scheduler,
    _SUPPORTED_SCHEDULERS,
)


def log_cluster_interfaces() -> None:
    """Probe the cluster for the job scheduler that manages it and log
    which cluster interfaces are available.

    The probe is performed on the node that the qbitbridge services are
    launched from (see main). For each supported scheduler (SLURM,
    PBSworks, Kubernetes) the detected state, the dask cluster class that
    would be used for it, and the availability of the python interface are
    reported using the logging package.
    """
    scheduler = probe_cluster_scheduler()
    if scheduler is None:
        raise RuntimeError(
            f"No supported cluster job scheduler detected. Supported {_SUPPORTED_SCHEDULERS}. Exiting"
        )
    logging.info(
        f"Cluster interface {scheduler.scheduler} found "
        f"(dask cluster class: {scheduler.dask_cluster_class}, "
        f"python interface available: {scheduler.python_interface_available})"
    )
    for evidence in scheduler.evidence:
        logging.debug(f"  {scheduler.scheduler} evidence: {evidence}")


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
