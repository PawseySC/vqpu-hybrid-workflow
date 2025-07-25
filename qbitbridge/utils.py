"""
Collection of functions and tooling intended for general usage.
The key functionality to explore here is the EventFile class.
"""

import datetime
import functools
import json
import importlib
import os
import secrets
import subprocess
import select
import time
from contextlib import contextmanager
from pathlib import Path
from socket import gethostname
from typing import (
    List,
    Any,
    Dict,
    NamedTuple,
    Optional,
    Tuple,
    Union,
)
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

SUPPORTED_IMAGE_TYPES = [".jpg", ".jpeg", ".png", ".gif", ".svg"]

def check_file_can_be_created(filename : str) -> bool:
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
        print(f"{library} is not installed.")
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
    variable: Union[str, None], default: Optional[str] = None
) -> Union[str, None]:
    """Get the value of an environment variable if it exists. If it does not
    a None is returned.

    Args:
        variable (Union[str,None]): The variable to lookup. If it starts with `$` it is removed. If `None` is provided `None` is returned.
        default (Optional[str], optional): If the variable lookup is not resolved this is returned. Defaults to None.

    Returns:
        Union[str,None]: Value of environment variable if it exists. None if it does not.
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
    resource: str = None
    """The slurm resource request"""
    job_id: Optional[str] = None
    """The job ID of the slurm job"""
    task_id: Optional[str] = None
    """The task ID of the slurm job"""
    time: Optional[str] = None
    """The time time the job information was gathered"""


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


def get_job_info(mode: str = "slurm") -> Union[SlurmInfo]:
    """Get the job information for the supplied mode

    Args:
        mode (str, optional): Which mode to poll information for. Defaults to "slurm".

    Raises:
        ValueError: Raised if the mode is not supported

    Returns:
        Union[SlurmInfo]: The specified mode
    """
    # TODO: Add other modes? Return a default?
    modes = ("slurm",)

    if mode.lower() == "slurm":
        job_info = get_slurm_info()
    else:
        raise ValueError(f"{mode=} not supported. Supported {modes=} ")

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


def log_slurm_job_environment(logger) -> SlurmInfo:
    """Log components of the slurm environment.

    Returns:
        SlurmInfo: Collection of slurm items from the job environment
    """
    # TODO: Expand this to allow potentially other job queue systems
    slurm_info = get_slurm_info()

    logger.info(f"Running on {slurm_info.hostname=}")
    logger.info(f"Slurm job id is {slurm_info.job_id}")
    logger.info(f"Slurm task id is {slurm_info.task_id}")

    return slurm_info


def run_a_srun_process(
    shell_cmd: list,
    srunargs: list = [],
    add_output_to_log: bool = False,
    logger=None,
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
    logger=None,
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
            if output:
                logger.info(f"{output.strip()}")
        elif fd == process.stderr.fileno():
            error_output = process.stderr.readline()
            if error_output:
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
    process = subprocess.run(['hostname'], capture_output=True, text=True)
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
):
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
        image_type in SUPPORTED_IMAGE_TYPES
    ), f"{image_path} has type {image_type}, and is not supported. Supported types are {SUPPORTED_IMAGE_TYPES}"

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
