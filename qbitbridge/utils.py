"""
@file utils.py
@brief Collection of functions and tooling intended for general usage. The key functionality to explore here is the EventFile class.
"""

# from curses import echo
import datetime
import functools
from getpass import getpass
import json
import importlib
import logging
import os
import numpy as np
import secrets
import subprocess
import select
import time
import threading
from contextlib import contextmanager
from pathlib import Path
from socket import gethostname, socket
from typing import (
    List,
    Any,
    Dict,
    NamedTuple,
    Optional,
    Tuple,
    Callable,
)

# from nbconvert import export
from prefect.artifacts import create_markdown_artifact, Artifact
from prefect.logging import get_run_logger
from prefect import get_client
from prefect.client.schemas.objects import FlowRun
from prefect.client.schemas.filters import FlowRunFilter
from prefect.context import TaskRunContext, get_run_context
import asyncio
import argparse
import base64
from uuid import UUID

_SUPPORTED_IMAGE_TYPES: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".svg"}
)

_SUPPORTED_SCHEDULERS: frozenset[str] = frozenset(
    {"SLURMCluster", "PBSCluster", "KuberCluster"}
)

_DASK_CLUSTER_CLASSES = {
    "SLURMCluster": "dask_jobqueue.SLURMCluster",
    "PBSCluster": "dask_jobqueue.PBSCluster",
    "KuberCluster": "dask_kubernetes.classic.KubeCluster",
}
_DASK_SCHEDULER_MODULE = {
    "SLURMCluster": "dask_jobqueue",
    "PBSCluster": "dask_jobqueue",
    "KuberCluster": "dask_kubernetes.classic",
}


def _command_exists(cmd: str) -> bool:
    """Check whether a command is available on the PATH.

    Args:
        cmd (str): name of the command to look for

    Returns:
        bool: True if the command is found on the PATH
    """
    from shutil import which

    return which(cmd) is not None


def _path_exists(path: str) -> bool:
    """Check whether a filesystem path exists.

    Args:
        path (str): path to check

    Returns:
        bool: True if the path exists
    """
    return os.path.exists(path)


class PrefectConfiguration(NamedTuple):
    """Simple class to store prefect launcher information"""

    home: str
    """Path to the prefect home directory"""
    # version: int = 3
    # """The major version of prefect"""
    # hostname: str = "0.0.0.0"
    # """The hostname of the prefect server"""
    web_concurrency: int = 16
    """Number of workers for prefect webserver (uvicorn under the hood)"""
    sqlalchemy_pool_size: int = 5
    """The pool size for the sqlalchemy connection"""
    sqlalchemy_max_overflow: int = 10
    """The max overflow for the sqlalchemy connection"""
    port: int = 4200
    """The port for the prefect server"""
    timeout_keep_alive: int = 10
    """The timeout for the prefect server"""
    limit_max_requests: int = 4096
    """The limit for the prefect server"""
    timeout_graceful_shutdown: int = 7200
    """The timeout for the prefect server"""
    dry_run: bool = False
    """If True, do not actually launch the prefect server, just print the command"""
    delay_time: int = 20
    """The delay in seconds to wait before starting the prefect server after starting postgres"""
    database_reset : bool = False
    """Whether to reset the database before launching"""
    profile : str | None = None
    """Wehther to create a profile. If name provided create new profile and use"""


class PostgresConfiguration(NamedTuple):
    """Simple class to store postgres launcher information"""

    # hostname: str
    # """The hostname running the postgres database"""
    scratch: str 
    """The scratch directory for the postgres database"""
    # version: int = 18
    # """The major version of postgres"""
    user: str = "postgres"
    """The user for the postgres database"""
    db: str = "orion"
    """The database name for the postgres database"""
    password: str = "qbitbridge_test"
    """The password for the postgres database"""
    port: int = 5432
    """The port for postgres database"""
    max_connections: int = 1000
    """The maximum number of connections for the postgres database"""
    shared_buffers: str = "1024MB"
    """The shared buffers for the postgres database."""
    container: str = "postgres_latest.sif"
    """The container image used to run postgres """
    container_engine: str = "singularity"
    """The container engine used to run the postgres container"""
    container_engine_args: Optional[str] = None
    """The container engine arguments used to run the postgres container"""
    dry_run: bool = False
    """If True, do not actually launch the postgres container, just print the command"""
    delay_time: int = 20
    """The delay in seconds to wait before starting the prefect server after starting postgres"""


class QBitBridgeLauncher:
    logger = logging.getLogger(__name__)
    """Simple class to store qbitbridge launcher information and start postgres and prefect services"""

    def __init__(self, config: Dict):
        self.log_level = config.get("log_level", "INFO").upper()

        self.logger.setLevel(self.log_level)
        self.logger.info(f"QBitBridgeLauncher initialized")
        self.logger.debug(f"with config: {config}")
        import socket

        self.hostname = socket.gethostname()
        self.delay_time: int = config.get("delay_time", 10)
        self.postgres = PostgresConfiguration(**config.get("postgres", {}))
        self.prefect = PrefectConfiguration(**config.get("prefect", {}))
        self.versions : Dict[str, int] = {"POSTGRES": -1, "PREFECT": -1}
        self.procs: Dict[str, Any] = {"POSTGRES": None, "PREFECT": None}
        self.pids: Dict[str, Any] = {"POSTGRES": None, "PREFECT": None}
        self.logging_threads: Dict[str, Any] = {"POSTGRES": None, "PREFECT": None}
        self.scheduler: SchedulerInfo | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()

    def _stream_logger(self, pipe, log_func, describ: str = "") -> None:
        """Reads lines from a pipe and logs them using the provided log function."""
        for line in iter(pipe.readline, ""):
            log_func(describ + " | " + line.rstrip())
        pipe.close()

    def _add_logging(self, proc_name: str, proc : subprocess.Popen | None = None) -> None:
        if proc is None:
            proc = self.procs[proc_name]
        self.logging_threads[proc_name] = {
            "out": threading.Thread(
                target=self._stream_logger,
                args=(proc.stdout, self.logger.info, proc_name),
            ),
            "warning": threading.Thread(
                target=self._stream_logger,
                args=(proc.stderr, self.logger.info, proc_name),
            ),
        }
        for k, v in self.logging_threads[proc_name].items():
            v.start()

    def _launch_postgres(self) -> subprocess.Popen | None:
        """Launch the postgres service using the configuration"""
        # define postres environment variables
        base_env = os.environ.copy()
        my_env = {}
        my_env["POSTGRES_PASSWORD"] = self.postgres.password
        my_env["POSTGRES_ADDR"] = self.hostname
        my_env["POSTGRES_USER"] = self.postgres.user
        my_env["POSTGRES_DB"] = self.postgres.db
        my_env["POSTGRES_SCRATCH"] = self.postgres.scratch

        # determine postgres version
        cmd = [
            self.postgres.container_engine, 
            "run",
            self.postgres.container,
            "--version",
        ]
        proc = subprocess.run(
                        cmd,
                        capture_output=True, text=True
                        )
        self.versions["POSTGRES"] = int(proc.stdout.split("(PostgreSQL) ")[1].split(" ")[0].split(".")[0])
        version = self.versions["POSTGRES"]

        # pass to singularity by defining appropriate environment variables
        if self.postgres.container_engine == "singularity":
            my_env["SINGULARITYENV_POSTGRES_PASSWORD"] = self.postgres.password
            my_env["SINGULARITYENV_POSTGRES_DB"] = self.postgres.db
            # my_env["SINGULARITYENV_PGDATA"] = f"{self.postgres.scratch}/pgdata"
            if "SINGULARITY_BINDPATH" not in base_env:
                my_env["SINGULARITY_BINDPATH"] = ""
            else:
                my_env["SINGULARITY_BINDPATH"] = base_env["SINGULARITY_BINDPATH"]
            my_env[
                "SINGULARITY_BINDPATH"
            ] += f",{self.postgres.scratch}/pgrun/:/var/run/postgresql/"
            if version < 18:
                my_env[
                    "SINGULARITY_BINDPATH"
                ] += f",{self.postgres.scratch}/pgdata/:/var/lib/postgresql/data"
            else:
                my_env[
                    "SINGULARITY_BINDPATH"
                ] += f",{self.postgres.scratch}/{version}/:/var/lib/postgresql/{version}"

        from pathlib import Path

        if not self.postgres.dry_run:
            # Define your directory path
            dir_path = Path(f"{self.postgres.scratch}/pgrun")
            dir_path.mkdir(parents=True, exist_ok=True)
            if version < 18:
                dir_path = Path(f"{self.postgres.scratch}/pgdata")
                dir_path.mkdir(parents=True, exist_ok=True)
            else:
                dir_path = Path(f"{self.postgres.scratch}/{version}")
                dir_path.mkdir(parents=True, exist_ok=True)
        # set the singularity arguments
        singargs = []
        if self.postgres.container_engine_args is not None:
            singargs += self.postgres.container_engine_args.split()

        cmd = (
            [self.postgres.container_engine, "run"]
            + singargs
            + [
                self.postgres.container,
                "-c",
                f"max_connections={self.postgres.max_connections}",
                "-c",
                f"shared_buffers={self.postgres.shared_buffers}",
                "-p",
                f"{self.postgres.port}",
            ]
        )
        if not self.postgres.dry_run:
            self.logger.info(f"Launching POSTGRES {version}... ")
            self.logger.debug(f"With command \n {cmd}")
            self.logger.debug(f"With env \n {my_env}")
            # checking container image
            container_image = Path(self.postgres.container)
            if not container_image.is_file():
                raise FileNotFoundError(
                    f"Postgres container image not found: {container_image}"
                )
            proc = subprocess.Popen(
                cmd,
                env=my_env | base_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )
            return proc
        else:
            self.logger.info(
                "Dry run: launching POSTGRES with the following configuration:"
            )
            self.logger.info(f"{self.postgres}")
            envinfo: str = f"Environment related to POSTGRES\n"
            for k, v in my_env.items():
                if "POSTGRES" in k:
                    envinfo += f"export {k}={v}\n"
            self.logger.info(envinfo)
            envinfo: str = (
                f"Environment related to container engine {self.postgres.container_engine.upper()}\n"
            )
            for k, v in my_env.items():
                if self.postgres.container_engine.upper() in k:
                    envinfo += f"export {k}={v}\n"
            self.logger.info(envinfo)
            from pathlib import Path
            self.logger.info(
                f"POSTGRES container image to be used: {self.postgres.container}. Exists? {Path(self.postgres.container).is_file()}."
            )
            self.logger.info(f"Launching POSTGRES {version} with command: {' '.join(cmd)}")
            return None

    def _launch_prefect(self) -> subprocess.Popen | None:
        """Launch the prefect service using the configuration"""
        if not self.prefect.dry_run:
            from pathlib import Path

            # Define your directory path
            dir_path = Path(f"{self.prefect.home}")
            # Create the directory safely
            dir_path.mkdir(parents=True, exist_ok=True)

        # determine prefect version
        cmd = [
            "prefect",
            "--version",
        ]
        proc = subprocess.run(
                        cmd,
                        capture_output=True, text=True
                        )
        self.versions["PREFECT"] = int(proc.stdout.split(".")[0])
        version = self.versions["PREFECT"]

        base_env = os.environ.copy()
        my_env = dict()

        if self.prefect.profile is not None:
            self.logger.info(f"Creating PREFECT profile {self.prefect.profile}")
            cmd =[
                "prefect",
                "profile",
                "create",
                self.prefect.profile,
            ]
            proc = subprocess.Popen(
                cmd,
                env=base_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )
            self._add_logging("PREFECT_CREATE_PROFILE", proc)
            proc.wait()
            cmd =[
                "prefect",
                "profile",
                "use",
                self.prefect.profile,
            ]
            proc = subprocess.Popen(
                cmd,
                env=base_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )
            self._add_logging("PREFECT_USE_PROFILE", proc)
            proc.wait()

        # set postgres environment
        my_env["POSTGRES_PASSWORD"] = self.postgres.password
        my_env["POSTGRES_ADDR"] = self.hostname
        my_env["POSTGRES_USER"] = self.postgres.user
        my_env["POSTGRES_DB"] = self.postgres.db
        my_env["POSTGRES_SCRATCH"] = self.postgres.scratch

        # set the prefect home directory
        my_env["PREFECT_HOME"] = self.prefect.home
        # set the prefect host
        my_env["PREFECT_ORION_HOST"] = self.hostname

        # set the prefect web concurrency
        my_env["PREFECT_ORION_WEB_CONCURRENCY"] = str(self.prefect.web_concurrency)
        # set the sqlalchemy pool size
        my_env["PREFECT_ORION_SQLALCHEMY_POOL_SIZE"] = str(
            self.prefect.sqlalchemy_pool_size
        )
        # set the sqlalchemy max overflow
        my_env["PREFECT_ORION_SQLALCHEMY_MAX_OVERFLOW"] = str(
            self.prefect.sqlalchemy_max_overflow
        )
        # set the prefect port
        my_env["PREFECT_API_URL"] = f"http://{self.hostname}:{self.prefect.port}/api"
        my_env["PREFECT_SERVER_API_HOST"] = "127.0.0.1" #self.hostname
        my_env["PREFECT_API_DATABASE_CONNECTION_URL"] = (
            f"postgresql+asyncpg://{self.postgres.user}:{self.postgres.password}@{self.hostname}:{self.postgres.port}/{self.postgres.db}"
        )
        #postgresql+asyncpg://$POSTGRES_USER:$POSTGRES_PASS@$POSTGRES_ADDR:5432/$POSTGRES_DB
        my_env["WEB_CONCURRENCY"] = str(self.prefect.web_concurrency)
        my_env["PREFECT_SQLALCHEMY_POOL_SIZE"] = str(self.prefect.sqlalchemy_pool_size)
        my_env["PREFECT_SQLALCHEMY_MAX_OVERFLOW"] = str(
            self.prefect.sqlalchemy_max_overflow
        )
        my_env["PREFECT_API_URL"] = f"http://{self.hostname}:4200/api"

        if self.prefect.database_reset and not self.prefect.dry_run:
            self.logger.info("Resetting PREFECT Database ... ")
            cmd = [
                "prefect", 
                "server", 
                "database", 
                "reset", 
                "-y"
            ]
            proc = subprocess.Popen(
                cmd,
                env=my_env | base_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )
            self._add_logging("PREFECT_DATABASE", proc)
            proc.wait()
        import sys
        cmd = list()
        # if self.prefect.version == 3:
        #     cmd += [
        #         sys.executable,
        #         "-m",
        #         "uvicorn",
        #         "--factory",
        #         "prefect.server.api.server:create_app",
        #     ]
        # else:
        #     cmd += ["prefect", "server", "start"] 

        cmd += [
            sys.executable,
            "-m",
            "uvicorn",
            "--factory",
            "prefect.server.api.server:create_app",
        ]
        cmd += ["--host", self.hostname]
        cmd += ["--port", str(self.prefect.port)]
        cmd += ["--timeout-keep-alive", str(self.prefect.timeout_keep_alive)]
        cmd += ["--limit-max-requests", str(self.prefect.limit_max_requests)]
        cmd += [
            "--timeout-graceful-shutdown",
            str(self.prefect.timeout_graceful_shutdown),
        ]
        # if self.prefect.version == 3:
        #     cmd += ["--timeout-keep-alive", str(self.prefect.timeout_keep_alive)]
        #     cmd += ["--limit-max-requests", str(self.prefect.limit_max_requests)]
        #     cmd += [
        #         "--timeout-graceful-shutdown",
        #         str(self.prefect.timeout_graceful_shutdown),
        #     ]
        # else:
        #     cmd += ["--keep-alive-timeout", str(self.prefect.timeout_keep_alive)]
        #     #cmd += ["--workers", str(self.prefect.limit_max_requests)]
        #     cmd += ["--workers", "1"]
        cmd += ["--log-level", self.log_level.lower()]

        # Run the app using Uvicorn
        if not self.prefect.dry_run:
            self.logger.info(f"Launching PREFECT {version}... ")
            self.logger.info(f"To view prefect UI, open an ssh tunnel")
            self.logger.info(
                f"ssh -N -f -L {self.prefect.port}:{self.hostname}:{self.prefect.port} <user>@<remote_host>"
            )
            self.logger.info(f"Before launching prefect jobs, copy the following")
            self.logger.info(f"export PREFECT_API_URL=http://{self.hostname}:4200/api")
            proc = subprocess.Popen(
                cmd,
                env=my_env | base_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )
            return proc
        else:
            self.logger.info(
                "Dry run: launching PREFECT with the following configuration:"
            )
            self.logger.info(f"{self.prefect}")
            envinfo: str = f"Environment related to PREFECT\n"
            for k, v in my_env.items():
                if "PREFECT" in k:
                    envinfo += f"export {k}={v}\n"
            self.logger.info(envinfo)
            envinfo = f"Environment related to POSTGRES\b"
            for k, v in my_env.items():
                if "POSTGRES" in k:
                    envinfo += f"export {k}={v}\n"
            self.logger.info(envinfo)
            self.logger.info(f"Launching PREFECT {version} with command: {' '.join(cmd)}")
            return None

    def launch(self) -> None:
        """Launch the postgres and prefect services using the configuration"""
        # launch postgres
        pname = "POSTGRES"
        self.procs[pname] = self._launch_postgres()
        if not self.postgres.dry_run:
            self._add_logging(pname)
            self.logger.info(
                f"Delay of {self.postgres.delay_time}s to ensure {pname} launched"
            )
            time.sleep(self.postgres.delay_time)
            # because using container for postgres, we need to wait for it to be ready before launching prefect
            # also need to grab the postgres pid using psutil
            import getpass, psutil

            username = getpass.getuser()
            # Iterate over all running processes to find first postgres owned by user
            for proc in psutil.process_iter(["pid", "name", "username"]):
                if (
                    proc.info["name"] == "postgres"
                    and proc.info["username"] == username
                ):
                    self.logger.debug(
                        f"POSTGRES Process ID: {proc.info['pid']}, Name: {proc.info['name']}, User: {proc.info['username']}"
                    )
                    self.pids[pname] = proc.info["pid"]
                    break
            self.logger.info(f"{pname} launched with {self.pids[pname]}")

        # pause between services
        self.logger.info(
            f"Waiting {self.delay_time} before continuing launch of other services"
        )
        time.sleep(self.delay_time)

        # launch prefect
        pname = "PREFECT"
        self.procs[pname] = self._launch_prefect()
        if not self.prefect.dry_run:
            self._add_logging(pname)
            self.logger.info(
                f"Delay of {self.prefect.delay_time}s to ensure {pname} launched"
            )
            time.sleep(self.prefect.delay_time)
            # because prefect is not launched in a container, just need the process id
            if self.procs[pname] is not None:
                self.pids[pname] = self.procs["PREFECT"].pid
            self.logger.info(f"{pname} launched")
        time.sleep(self.delay_time)
        self.logger.info("QBitBridgeLauncher launch complete")
        running_pids = []
        for k, v in self.pids.items():
            if v is not None:
                running_pids.append(v)
        if len(running_pids) > 0:
            self.logger.info("To stop the services, use the following commands:")
            self.logger.info(f"kill {running_pids}")

        # get scheduler
        self.scheduler = probe_cluster_scheduler()
        if self.scheduler is None:
            raise RuntimeError(
                f"No supported cluster job scheduler detected. Supported {_SUPPORTED_SCHEDULERS}. Exiting"
            )
        self.logger.info(
            f"SCHEDULER | Cluster interface {self.scheduler.scheduler} found "
            f"(dask cluster class: {self.scheduler.dask_cluster_class}, "
            f"python interface available: {self.scheduler.python_interface_available})"
        )
        self.logger.debug(f"Evidence: {self.scheduler.evidence}")
        return 

    def shutdown(self) -> None:
        """Shutdown process and logging threads"""
        self.logger.info("QBitBridgeLauncher shutting down ... ")
        import signal

        for k in ["PREFECT", "POSTGRES"]:
            pid = self.pids[k]
            if pid is not None:
                os.kill(pid, signal.SIGTERM)
                for k2, v in self.logging_threads[k].items():
                    v.join()
        self.logger.info("QBitBridgeLauncher shutdown")


def load_config(config_path: str, log_level: str = "INFO") -> QBitBridgeLauncher:
    """Load a YAML configuration file for launching all relevant services and return it as a QBitBridgeLauncher object.
    Args:
        config_path (str): Path to the YAML configuration file.
        log_level (str): Logging level.
    Returns:
        QBitBridgeLauncher: An instance of QBitBridgeLauncher initialized with the loaded configuration.
    """
    import yaml

    logging.basicConfig(level=log_level)

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, "r") as file:
        try:
            config = yaml.safe_load(file)
        except yaml.YAMLError as e:
            raise ValueError(f"Error parsing YAML configuration: {e}")

    return QBitBridgeLauncher(config)


def check_file_can_be_created(filename: str) -> bool:
    """check if file can be created

    Args:
        filename (str): filename to check

    Returns:
        bool if creatable
    """
    base_dir = os.path.dirname(filename)
    return (
        not os.path.exists(filename)
        and os.path.isdir(base_dir)
        and os.access(base_dir, os.W_OK)
    )


def check_python_installation(library: str):
    """Check if library present and otherwise catch ImporError
    and report missing library

    Args:
        library (str): name of library to check

    Returns:
        bool of whether library can be imported
    """
    try:
        importlib.import_module(library)
        return True
    except ImportError:
        logging.warning(f"{library} is not installed.")
        return False


def _printtostr(thingtoprint: Any) -> str:
    """Print something to string rather than stdout

    Args:
        thingtoprint (Any) : print to a string

    Returns:
        str of the thing to print
    """
    from io import StringIO

    f = StringIO()
    print(thingtoprint, file=f)
    result = f.getvalue()
    f.close()
    return result


def get_environment_variable(
    variable: str | None = None, default: str | None = None
) -> str | None:
    """Get the value of an environment variable if it exists. If it does not
    a None is returned.

    Args:
        variable (str|None): The variable to lookup. If it starts with `$` it is removed. If `None` is provided `None` is returned.
        default (Optional[str], optional): If the variable lookup is not resolved this is returned. Defaults to None.

    Returns:
        str|None: Value of environment variable if it exists. None if it does not.
    """
    if variable is None:
        return None

    variable = variable.lstrip("$")
    value = os.getenv(variable)

    value = default if value is None and default is not None else value

    return value


class SlurmInfo(NamedTuple):
    """Simple class to store slurm information"""

    hostname: str
    """The hostname of the slurm job"""
    resource: str | None = None
    """The slurm resource request"""
    job_id: str | None = None
    """The job ID of the slurm job"""
    task_id: str | None = None
    """The task ID of the slurm job"""
    time: str | None = None
    """The time time the job information was gathered"""


class PBSInfo(NamedTuple):
    """Simple class to store pbs information"""

    hostname: str
    """The hostname of the slurm job"""
    resource: str | None = None
    """The slurm resource request"""
    job_id: str | None = None
    """The job ID of the slurm job"""
    task_id: str | None = None
    """The task ID of the slurm job"""
    time: str | None = None
    """The time time the job information was gathered"""


class SchedulerInfo(NamedTuple):
    """Simple class to store the result of probing for a cluster scheduler"""

    scheduler: str
    """Name of the scheduler that was probed (one of SUPPORTED_SCHEDULERS)"""
    detected: bool
    """Whether evidence for the scheduler was found on this cluster"""
    evidence: List[str]
    """Human readable strings describing the evidence found"""
    dask_cluster_class: str
    """The dask cluster class string to use for this scheduler"""
    python_interface_available: bool
    """Whether the python packages needed to talk to this scheduler are importable"""


def _probe_slurm() -> Tuple[bool, List]:
    """Probe the local environment for evidence that the cluster is
    managed by SLURM.

    Returns:
        bool : Whether evidence for SLURM was found
        evidence : the evidence proving SLURM available.
    """
    evidence = []

    for var in ("SLURM_JOB_ID", "SLURM_PROCID", "SLURM_NTASKS", "SLURM_NODELIST"):
        if os.environ.get(var):
            evidence.append(f"environment variable {var} is set")

    for cmd in ("sbatch", "srun", "sinfo"):
        if _command_exists(cmd):
            evidence.append(f"command {cmd} is available on the PATH")

    for path in ("/var/spool/slurmctld", "/etc/slurm/slurm.conf"):
        if _path_exists(path):
            evidence.append(f"path {path} exists")
    return (len(evidence) > 0), evidence


def _probe_pbs() -> Tuple[bool, List]:
    """Probe the local environment for evidence that the cluster is
    managed by PBSworks (or a PBS/Torque/Unicorn compatible scheduler).

    Returns:
        bool : Whether evidence for PBS was found
        evidence : the evidence proving PBS available.
    """
    evidence = []

    for var in ("PBS_JOBID", "PBS_NODEFILE", "PBS_NP", "PBS_QUEUE", "PBS_SERVER"):
        if os.environ.get(var):
            evidence.append(f"environment variable {var} is set")

    # generic PBS commands as well as PBSworks specific commands
    for cmd in ("qsub", "qstat", "pbsnodes", "showq", "showcfg", "showbnodes"):
        if _command_exists(cmd):
            evidence.append(f"command {cmd} is available on the PATH")

    for path in ("/var/spool/pbs", "/var/spool/torque"):
        if _path_exists(path):
            evidence.append(f"path {path} exists")
    return (len(evidence) > 0), evidence


def _probe_kubernetes() -> Tuple[bool, List]:
    """Probe the local environment for evidence that the cluster is
    managed by Kubernetes.

    Returns:
        bool : Whether evidence for Kubernetes was found
        evidence : the evidence proving Kubernetes available.
    """
    evidence = []

    for var in (
        "KUBERNETES_SERVICE_HOST",
        "KUBERNETES_SERVICE_PORT",
        "KUBERNETES_PORT",
    ):
        if os.environ.get(var):
            evidence.append(f"environment variable {var} is set")

    for cmd in ("kubectl",):
        if _command_exists(cmd):
            evidence.append(f"command {cmd} is available on the PATH")

    for path in ("/var/run/secrets/kubernetes.io/serviceaccount",):
        if _path_exists(path):
            evidence.append(f"path {path} exists")
    return (len(evidence) > 0), evidence


def probe_cluster_scheduler() -> SchedulerInfo:
    """Probe the local environment for evidence that the cluster has a specific scheduler

    Returns:
        SchedulerInfo : schedulerprobe containing evidence for a particular scheduler running on the system
    """
    probing: Dict[str, Callable] = {
        "SLURMCluster": _probe_slurm,
        "PBSCluster": _probe_pbs,
        "KubeCluster": _probe_kubernetes,
    }
    # check all the known allowed schedulers
    for k, v in probing.items():
        found, evidence = v()
        if found:
            return SchedulerInfo(
                scheduler=k,
                detected=len(evidence) > 0,
                evidence=evidence,
                dask_cluster_class=_DASK_CLUSTER_CLASSES[k],
                python_interface_available=check_python_installation(
                    _DASK_SCHEDULER_MODULE[k]
                ),
            )
    # if nothing is found raise exception
    raise RuntimeError(
        f"No viable cluster schedulers detected. Allowed schedulers are {_SUPPORTED_SCHEDULERS}."
    )


def get_slurm_info() -> SlurmInfo:
    """Collect key slurm attributes of a job

    Returns:
        SlurmInfo: Collection of slurm items from the job environment
    """

    hostname = gethostname()
    job_id = get_environment_variable("SLURM_JOB_ID")
    task_id = get_environment_variable("SLURM_ARRAY_TASK_ID")
    now = str(datetime.datetime.now())

    return SlurmInfo(hostname=hostname, job_id=job_id, task_id=task_id, time=now)


def get_pbs_info() -> PBSInfo:
    """Collect key PBS attributes of a job

    Returns:
        SlurmInfo: Collection of slurm items from the job environment
    """

    hostname = gethostname()
    job_id = get_environment_variable("PBS_JOBID")
    task_id = get_environment_variable("PBS_ARRAY_INDEX")
    now = str(datetime.datetime.now())

    return PBSInfo(hostname=hostname, job_id=job_id, task_id=task_id, time=now)


def get_job_info(mode: str = "slurm") -> SlurmInfo | PBSInfo:
    """Get the job information for the supplied mode

    Args:
        mode (str, optional): Which mode to poll information for. Defaults to "slurm".

    Raises:
        ValueError: Raised if the mode is not supported

    Returns:
        SlurmInfo|PBSInfo: The specified mode
    """
    # TODO: Add other modes? Return a default?
    modes = ("slurm", "pbs")

    if mode.lower() == "slurm":
        job_info = get_slurm_info()
    elif mode.lower() == "pbs":
        job_info = get_pbs_info()
    else:
        raise ValueError(f"{mode} not supported. Supported {modes} ")

    return job_info


def get_argparse_args(
    arguments: str, parser: argparse.ArgumentParser
) -> argparse.Namespace:
    """Parse a string based on an argparser and also strip out _ from an argument

    Args:
        arguments (str): string of arguments
        parser (argparse.ArgumentParser): parser that processes list of strings

    Return:
        The argparser namespace
    """
    import shlex

    # split the string
    args_list = shlex.split(arguments)
    # if string contains a __ replace it with a space
    args_list = [a.replace("__", " ") for a in args_list]
    # Parse the arguments from our string
    return parser.parse_args(args_list)


def log_job_environment(
    logger: logging.Logger, scheduler: SchedulerInfo
) -> SlurmInfo | PBSInfo:
    if scheduler.scheduler == "slurm":
        return log_slurm_job_environment(logger)
    elif scheduler.scheduler == "pbs":
        return log_pbs_job_environment(logger)


def log_slurm_job_environment(logger) -> SlurmInfo:
    """Log components of the slurm environment.

    Returns:
        SlurmInfo: Collection of slurm items from the job environment
    """
    # TODO: Expand this to allow potentially other job queue systems
    slurm_info = get_slurm_info()

    logger.info(f"Running on {slurm_info.hostname}")
    logger.info(f"Slurm job id is {slurm_info.job_id}")
    logger.info(f"Slurm task id is {slurm_info.task_id}")

    return slurm_info


def log_pbs_job_environment(logger) -> PBSInfo:
    """Log components of the pbs environment.

    Returns:
        PBSInfo: Collection of slurm items from the job environment
    """
    # TODO: Expand this to allow potentially other job queue systems
    pbs_info = get_pbs_info()

    logger.info(f"Running on {pbs_info.hostname}")
    logger.info(f"Slurm job id is {pbs_info.job_id}")
    logger.info(f"Slurm task id is {pbs_info.task_id}")

    return pbs_info


def run_a_srun_process(
    shell_cmd: list,
    srunargs: list = [],
    add_output_to_log: bool = False,
    logger: logging.Logger | None = None,
) -> subprocess.Popen:
    """runs a srun process given by the shell command.
    If given a logger and asked to append, adds to the logger.

    Returns:
        subprocess.Popen: new proccess spawned by the shell_cmd
    """
    wrappername = secrets.token_hex(12)
    wrappercmd = [
        "#!/bin/bash",
        "export OMP_PLACES=cores",
        "export OMP_MAX_ACTIVE_LEVELS=4",
    ]
    with open(wrappername, "w") as f:
        for cmd in wrappercmd:
            f.write(cmd + "\n")
        f.write(" ".join(shell_cmd) + "\n")
    os.chmod(wrappername, 0o777)
    newcmd = []
    newcmd += ["srun"] + srunargs
    newcmd += ["./" + wrappername]
    process = run_a_process(newcmd, logger, add_output_to_log)
    os.remove(wrappername)
    return process


def run_a_process(
    shell_cmd: list,
    add_output_to_log: bool = False,
    logger=None,
):
    """Runs a process given by the shell command.
    If given a logger and asked to append, adds to the logger.

    Returns:
        subprocess: new proccess spawned by the shell_cmd
    """
    process = subprocess.run(
        shell_cmd, capture_output=add_output_to_log, text=add_output_to_log
    )
    if add_output_to_log and logger != None:
        logger.info(process.stdout)
    return process


def run_a_process_bg(
    shell_cmd: list,
    add_output_to_log: bool = False,
    sleeplength: float = 5,
    logger: logging.Logger | None = None,
) -> None:
    """Runs a process given by the shell command.
    If given a logger and asked to append, adds to the logger.

    Returns:
        subprocess: new proccess spawned by the shell_cmd
    """

    process = subprocess.run(
        shell_cmd, capture_output=add_output_to_log, text=add_output_to_log
    )
    time.sleep(sleeplength)
    reads = [process.stdout.fileno(), process.stderr.fileno()]
    ret = select.select(reads, [], [])
    for fd in ret[0]:
        if fd == process.stdout.fileno():
            output = process.stdout.readline()
            if output and logger is not None:
                logger.info(f"{output.strip()}")
        elif fd == process.stderr.fileno():
            error_output = process.stderr.readline()
            if error_output and logger is not None:
                logger.info(f"{error_output.strip()}")


def get_num_gpus() -> Tuple[int, str]:
    """Poll node for number of gpus on host

    Returns:
        int number of gpus on a node and the type
    """
    cmd = ["lspci"]
    process = subprocess.run(cmd, capture_output=True, text=True)
    lines = process.stdout.strip().split("\n")
    gputypes = ["NVIDIA", "AMD", "INTEL"]
    gpucmds = {
        "NVIDIA": ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
        "AMD": ["rocm-smi", "--showtopo", "--csv"],
    }
    gpucmd = list()
    gputypefound = False
    for l in lines:
        if "PCI bridge:" in l:
            for gt in gputypes:
                if gt in l:
                    gpucmd = gpucmds[gt]
                    gputype = gt
                    gputypefound = True
                    break
        if gputypefound:
            break
    process = subprocess.run(["hostname"], capture_output=True, text=True)
    process = subprocess.run(gpucmd, capture_output=True, text=True)
    numgpu = len(process.stdout.strip().split("\n"))
    if gputype == "AMD":
        numgpu -= 1
    return numgpu, gt


def multinodenumberofgpus():
    """Get the number of gpus per host"""
    pass


async def async_create_markdown_artifcat(key, markdown, description) -> None:
    """create a markdown artifact in a asynchronous fashion.
    Wrapper allows more complexity to be added."""
    await create_markdown_artifact(key=key, markdown=markdown, description=description)


async def save_artifact(
    data: Any, key: str = "key", description: str = "Data to be shared between subflows"
) -> None:
    """Use this to save data between workflows and tasks. Best used for small artifacts

    Args:
        data (): data to be saved
        key (str): key for accessing the data
        description (str) : description of the data

    Returns :
        a markdown artifact to transmit data between workflows
    """
    await async_create_markdown_artifcat(
        key=key, markdown=f"```json\n{data}\n```", description=description
    )


async def upload_image_as_artifact(
    image_path: Path,
    key: str = "",
    description: str | None = None,
) -> None:
    """Create and submit a markdown artifact tracked by prefect for an
    input image. Currently supporting png formatted images.

    The input image is converted to a base64 encoding, and embedded directly
    within the markdown string. Therefore, be mindful of the image size as this
    is tracked in the postgres database.

    Args:
        image_path (Path): Path to the image to upload
        key (str): A key. Defaults to filename with lower_case.
        description (Optional[str], optional): A description passed to the markdown artifact. Defaults to None.

    """
    logger = get_run_logger()
    image_type = image_path.suffix
    assert image_path.exists(), f"{image_path} does not exist"
    assert (
        image_type in _SUPPORTED_IMAGE_TYPES
    ), f"{image_path} has type {image_type}, and is not supported. Supported types are {_SUPPORTED_IMAGE_TYPES}"

    with open(image_path, "rb") as open_image:
        logger.info(f"Encoding {image_path} in base64")
        image_base64 = base64.b64encode(open_image.read()).decode()

    logger.info("Creating markdown tag")
    markdown = f"![{image_path.stem}](data:image/{image_type};base64,{image_base64})"

    logger.info("Registering artifact")
    if key == "":
        key = (
            image_path.name.lower()
            .split(image_path.suffix)[0]
            .replace(".", "")
            .replace("_", "")
            .replace("-", "")
        )
    await async_create_markdown_artifcat(
        key=key,
        markdown=markdown,
        description=description,
    )
    logger.info(f"Image saved as artifcat with key = {key}")
    # artifact = await Artifact.get(key=key)
    # logger.info(artifact)


def get_task_run_id() -> str:
    """Get the Task ID of the task calling this function. If there is no context, then the task_run_id is set to a descriptive, non-unique value"""
    if TaskRunContext.get():
        context = get_run_context()
        task_run_id = context.task_run.id
    else:
        task_run_id = "not_a_task"
    return task_run_id


async def get_flow_runs(
    flow_run_filter: FlowRunFilter, sort: str = "-start_time", limit: int = 100
) -> List[FlowRun]:
    """Get list of flow runs that satisfy some filter"""
    async with get_client() as client:
        flow_runs = await client.read_flow_runs(
            flow_run_filter=flow_run_filter,
            # sort=sort,
            # limit=limit,
        )
    return flow_runs


class EventFile:
    """Simple class to create a file for a given event."""

    def __init__(
        self,
        name: str,
        loc: str,
        sampling: float = 0.01,
        id: str | None = None,
        etime: str | None = None,
        eset: int | None = None,
    ):
        self.event_loc: str = ""
        """directory where to store file event locks"""
        self.event_name: str = ""
        """The name of the event"""
        self.fname: str = ""
        """File name where event will be saved"""
        self.sampling: float = 0.01
        """how often to check for event file"""
        self.identifer: str = ""
        """unique identifer"""
        self.event_time: str = ""
        """Time of event creation"""
        self.event_set: int = 0
        """Counter for number of times set"""

        # now set values
        self.event_loc = loc
        self.event_name = name
        if id == None:
            self.identifer = secrets.token_hex(12)
        else:
            self.identifer = id
        self.fname = (
            self.event_loc + "/" + self.event_name + "." + self.identifer + ".txt"
        )
        self.sampling = sampling
        # if etime != None:
        #     self.event_time = etime
        # if eset != None:
        #     self.event_set = eset

    def __str__(self):
        message: str = (
            f"Event {self.event_name} with id={self.identifer} saved to {self.fname} : "
        )
        if not os.path.isfile(self.fname):
            message += f"- not set\n"
        else:
            with open(self.fname, "r") as f:
                data = f.readline().strip().split(", ")
                eset = int(data[0])
                etime = data[1]
            message += f"- set at {etime} with {eset}\n"
        return message

    def set(self, meta_data: str | Dict | List | None = None) -> None:
        """Set the event by creating a file. If already set,
        read the file and return an exception.
        @todo Might want to add explicit lock to file when writing to it.

        Raises:
            RunTimeError saying event has already been set.
        """
        if not os.path.isfile(self.fname):
            current_time = datetime.datetime.now()
            self.event_time = current_time.strftime("%Y-%m-%D::%H:%M:%S")
            self.event_set += 1
            with open(self.fname, "w") as f:
                f.write(f"{self.event_set}, {self.event_time}\n")
                if meta_data != None:
                    f.write(f"{meta_data}")
        else:
            # need to throw exception
            eset: int
            etime: str
            with open(self.fname, "r") as f:
                data = f.readline().strip().split(", ")
                eset = int(data[0])
                etime = data[1]
            message: str = (
                f"Event {self.event_name} id={self.identifer} has already been set at {etime} and {eset} is being requested to be set again."
            )
            raise RuntimeError(message)

    async def wait(self) -> None | str:
        """Wait till file indicating event set to exist
        and then return. Function should be called with await.
        """
        meta_data = None
        while not os.path.isfile(self.fname):
            await asyncio.sleep(self.sampling)
        with open(self.fname, "r") as f:
            data = f.readline().strip("\n").split(", ")
            eset = int(data[0])
            etime = data[1]
            line = f.readline()
            if not line:  # Empty string indicates end of file
                meta_data = line.strip()
        if etime != self.event_time or eset != self.event_set:
            self.event_time = etime
            self.event_set = eset
        return meta_data

    def clean(self) -> None:
        """Clean the file if present, unsetting the event"""
        # remove the file as a lock
        if os.path.isfile(self.fname):
            os.remove(self.fname)
        self.event_time = ""
        self.event_set = 0
        # # if local dask runner copy has been used to call clean then
        # # also reduce the event time and set
        # if self.event_set > 0:
        #     self.event_time = ''
        #     self.event_set -= 1

    def to_dict(self) -> Dict:
        """Converts class to dictionary for serialisation"""
        return {
            "EventFile": {
                "name": self.event_name,
                "loc": self.event_loc,
                "sampling": self.sampling,
                "id": self.identifer,
                "etime": self.event_time,
                "eset": self.event_set,
            }
        }

    @classmethod
    def from_dict(cls, data: Dict):
        """Create an object from a dictionary"""
        if "EventFile" not in list(data.keys()):
            raise ValueError("Not an EventFile dictionary")
        data = data["EventFile"]
        return cls(
            name=data["name"],
            loc=data["loc"],
            sampling=data["sampling"],
            id=data["id"],
            etime=data["etime"],
            eset=data["eset"],
        )


# Decorators


def validate_keys(allowed_keys):
    """Ensure dictionary data passed to a function only contains specific keys"""

    def decorator(func):
        """decorator"""

        def wrapper(*args, **kwargs):
            """wrapper to the function"""
            # Assume first argument is the dictionary to validate
            if args and isinstance(args[0], dict):
                data = args[0]
                invalid_keys = set(data.keys()) - set(allowed_keys)
                if invalid_keys:
                    raise KeyError(
                        f"Invalid keys: {invalid_keys}. Allowed keys: {allowed_keys}"
                    )
            return func(*args, **kwargs)

        return wrapper

    return decorator


def measure_time(func):
    """measure the time taken by a function"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        print(f"Execution time : {end - start:.6f} s")
        return result

    return wrapper
