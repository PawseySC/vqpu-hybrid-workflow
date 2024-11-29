'''
@file utils.py
@brief Collection of functions and tooling intended for general usage.

'''


import datetime
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from socket import gethostname
from typing import List, NamedTuple, Optional, Tuple, Union, Generator
from prefect.artifacts import create_markdown_artifact


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
    '''
    @brief simple class to store slurm information 
    '''
    hostname: str
    '''The hostname of the slurm job'''
    resource: str
    '''The slurm resource request'''
    job_id: Optional[str] = None
    '''The job ID of the slurm job'''
    task_id: Optional[str] = None
    '''The task ID of the slurm job'''
    time: Optional[str] = None
    '''The time time the job information was gathered'''


def get_slurm_info() -> SlurmInfo:
    '''Collect key slurm attributes of a job

    Returns:
        SlurmInfo: Collection of slurm items from the job environment
    '''

    hostname = gethostname()
    job_id = get_environment_variable("SLURM_JOB_ID")
    task_id = get_environment_variable("SLURM_ARRAY_TASK_ID")
    time = str(datetime.datetime.now())

    return SlurmInfo(hostname=hostname, job_id=job_id, task_id=task_id, time=time)


def get_job_info(mode: str = "slurm") -> Union[SlurmInfo]:
    '''Get the job information for the supplied mode

    Args:
        mode (str, optional): Which mode to poll information for. Defaults to "slurm".

    Raises:
        ValueError: Raised if the mode is not supported

    Returns:
        Union[SlurmInfo]: The specified mode
    '''
    # TODO: Add other modes? Return a default?
    modes = ("slurm",)

    if mode.lower() == "slurm":
        job_info = get_slurm_info()
    else:
        raise ValueError(f"{mode=} not supported. Supported {modes=} ")

    return job_info


def log_slurm_job_environment(logger) -> SlurmInfo:
    '''Log components of the slurm environment. Currently only support slurm

    Returns:
        SlurmInfo: Collection of slurm items from the job environment
    '''
    # TODO: Expand this to allow potentially other job queue systems
    slurm_info = get_slurm_info()

    logger.info(f"Running on {slurm_info.hostname=}")
    logger.info(f"Slurm job id is {slurm_info.job_id}")
    logger.info(f"Slurm task id is {slurm_info.task_id}")

    return slurm_info

def run_a_process(shell_cmd : list, logger = None, add_output_to_log : bool = True) -> subprocess.Popen:
    '''runs a process given by the shell command. If given a logger and asked to append, adds to the logger

    Returns:
        subprocess.Popen: new proccess spawned by the shell_cmd 
    '''
    process = subprocess.Popen(shell_cmd, stdout=subprocess.PIPE)
    if add_output_to_log and logger != None:
        for line in process.stdout:
            logger.info(line.decode())
    process.stdout.close()
    return_code = process.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, shell_cmd)
    return process

def save_artifact(data, 
                  key : str = 'key', 
                  description: str = 'Data to be shared between subflows'):
    '''
    @brief Use this to save data between workflows and tasks. Best used for small artifacts

    Args:
        data (): data to be saved 
        key (str): key for accessing the data 
        description (str) : description of the data 

    Returns : 
        a markdown artifact to transmit data between workflows
    '''
    create_markdown_artifact(
        key=key,
        markdown=f"```json\n{data}\n```",
        description=description
    )

