'''
@file utils.py
@brief Collection of functions and tooling intended for general usage.

'''


import datetime
import os
import secrets
import subprocess
from contextlib import contextmanager
from pathlib import Path
from socket import gethostname
from typing import List, NamedTuple, Optional, Tuple, Union, Generator
from prefect.artifacts import create_markdown_artifact
from prefect.logging import get_run_logger
from prefect import get_client
from prefect.client.schemas.objects import FlowRun
from prefect.client.schemas.filters import FlowRunFilter
import asyncio

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
    resource: str = None
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

def run_a_srun_process(
        shell_cmd : list, 
        srunargs : list = [], 
        add_output_to_log : bool = False,
        logger = None, 
        ) -> subprocess.Popen:
    '''runs a srun process given by the shell command. If given a logger and asked to append, adds to the logger

    Returns:
        subprocess.Popen: new proccess spawned by the shell_cmd 
    '''
    wrappername = secrets.token_hex(12)
    wrappercmd = ['#!/bin/bash', 
                  'export OMP_PLACES=cores', 
                  'export OMP_MAX_ACTIVE_LEVELS=4']
    with open(wrappername, 'w') as f:
        for cmd in wrappercmd:
            f.write(cmd+'\n')
        f.write(' '.join(shell_cmd)+'\n')
    os.chmod(wrappername, 0o777)
    newcmd = []
    newcmd += ['srun']+srunargs
    newcmd += ['./'+wrappername]
    process = run_a_process(newcmd, logger, add_output_to_log)
    os.remove(wrappername)
    return process 

def run_a_process(
        shell_cmd : list, 
        add_output_to_log : bool = False, 
        logger = None, 
        ):
    '''runs a process given by the shell command. If given a logger and asked to append, adds to the logger

    Returns:
        subprocess: new proccess spawned by the shell_cmd 
    '''
    process = subprocess.run(shell_cmd, capture_output=add_output_to_log, text=add_output_to_log)
    if add_output_to_log and logger != None:
        logger.info(process.stdout)
    return process

async def save_artifact(data, 
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
    await create_markdown_artifact(
        key=key,
        markdown=f"```json\n{data}\n```",
        description=description
    )

async def get_flow_runs(
        flow_run_filter : FlowRunFilter, 
        sort : str = '-start_time', 
        limit : int = 100
        ) -> List[FlowRun]:
    
    async with get_client() as client:
        flow_runs = await client.read_flow_runs(
            flow_run_filter=flow_run_filter,
            # sort=sort,
            # limit=limit,
        )
    return flow_runs 

class EventFile:
    '''
    @brief simple class to create a file for a given event.  
    '''
    event_loc: str
    '''directory where to store file event locks'''
    sampling: float
    '''how often to check for event file'''
    identifer : str
    '''unique identifer'''
    event_time : str 
    '''Time of event creation'''
    
    def __init__(self, name : str, loc : str, sampling : float = 0.01): 
        self.event_loc = loc
        self.event_name = name 
        self.identifer = secrets.token_hex(12)
        self.fname = self.event_loc+'/'+ self.event_name + '.' + self.identifer + '.txt'
        self.sampling = sampling
        self.event_time = ''

    def set(self) -> None:
        if self.event_time == '':
            current_time = datetime.datetime.now() 
            self.event_time = current_time.strftime('%Y-%m-%D::%H:%M:%S')
            with open(self.fname, "w") as f:
                f.write(self.event_time)
        else:
            # need to throw exception 
            pass
        
    async def wait(self) -> None:
        while not os.path.isfile(self.fname):
            await asyncio.sleep(self.sampling)
        with open(self.fname, "r") as f:
            time = f.readline().strip('\n')
        correct = (time == self.event_time)
        # need to throw exception if not true 

    def clean(self) -> None:
        os.remove(self.fname)
        self.event_time = ''


