"""
@file vqpucudaq.py
@brief Collection of CUDAQ oriented tasks, flows and interfaces

"""

import argparse
import time, datetime, subprocess, os, select, psutil, copy
import functools
import json
from pathlib import Path
import importlib
import numpy as np
from typing import (
    List,
    Any,
    Dict,
    NamedTuple,
    Optional,
    Tuple,
    Union,
    Generator,
    Callable,
)
from .clusters import get_dask_runners
from .utils import (
    check_python_installation,
    get_argparse_args,
    save_artifact,
    run_a_srun_process,
    run_a_process,
    run_a_process_bg,
    get_task_run_id,
    get_job_info,
    get_flow_runs,
    upload_image_as_artifact,
    SlurmInfo,
    EventFile,
)
from .vqpubase import (
    QPUMetaData,
    HybridQuantumWorkflowBase,
)
import yaml
import asyncio
from prefect_dask import DaskTaskRunner
from prefect import flow, task, get_client, pause_flow_run
from prefect.logging import get_run_logger
from prefect.artifacts import Artifact
from prefect.context import get_run_context, TaskRunContext
from prefect.serializers import Serializer, JSONSerializer

# AWS imports: Import CUDAQ modules
libcheck = check_python_installation("cudaq")
if not libcheck:
    raise ImportError(
        "Missing CUDAQ library, cannot with cudaq library nor use QPUs via cudaq backends"
    )
import cudaq

cudaq_allowed_devices: List[str] = [
    "quantinuum",
    "ionq",
    "iqm",
    "oqc",
    "simulator",
]


def cudaq_check_credentials() -> str:
    """Check CUDAQ Credentials
    Returns:
        string of relevant information
    Raises:
        RuntimeErorr if credentials not defined
        ValueError if device not in list allowed devices.
    """
    report: str = ""

    for device in cudaq_allowed_devices:
        avail, message = cudaq_check_device_credentials(f"--cudaqdevice={device}")
        report += f"{device} : Available = {avail}, {message} \n"
    return report


def cudaq_check_device_credentials(
    arguments: str | argparse.Namespace, raise_error: bool = False
) -> Tuple[bool, str]:
    """Check CUDAQ Credentials for a given device
    Args:
        device (str) : device name to check if credentials in place
        raise_error (str) : raise error if device not accessible
    Returns:
        string of relevant information
    Raises:
        RuntimeErorr if credentials not defined
        ValueError if device not in list allowed devices.
    """

    message: str = ""
    avail: bool = True
    if isinstance(arguments, str):
        args = cudaq_parse_args(arguments)
    else:
        args = arguments
    device = args.cudaqdevice
    if device not in cudaq_allowed_devices:
        raise ValueError(f"Device {device} not in allowed list of devices.")
    if device == "simulator":
        message = "Simulator available."
    if device == "quantinuum":
        homeval = os.getenv("HOME")
        config_file = f"{homeval}/.quantinuum_config"
        if not os.path.exists(config_file):
            message = f"Quantinuum configuration file missing. Please ensure {config_file} exists with correct info"
            avail = False
            if raise_error:
                raise RuntimeError(message)
        else:
            with open(config_file, "r") as f:
                lines = f.readlines()
                if len(lines) != 2:
                    message = f"Quantinuum config malformed. Check {config_file}"
                    avail = False
                    if raise_error:
                        raise RuntimeError(message)
        message = "Quantinuum configured"
    elif device == "ionq":
        ionq_env = "IONQ_API_KEY"
        if ionq_env not in os.environ:
            message = f"IonQ environment variable missing. Please ensure {ionq_env} exists with correct info"
            avail = False
            if raise_error:
                raise RuntimeError(message)
        message = "IONQ configured"
    elif device == "iqm":
        iqm_env = "IQM_TOKENS_FILE"
        if iqm_env not in os.environ:
            message = f"IQM environment variable missing. Please ensure {iqm_env} exists with correct info"
            avail = False
            if raise_error:
                raise RuntimeError(message)
        if not os.path.exists(os.getenv(iqm_env)):
            message = f"IQM configuration file missing. Please ensure the file pointed to by {iqm_env} exists with correct info"
            avail = False
            if raise_error:
                raise RuntimeError(message)
        message = "IQM configured"
    elif device == "oqc":
        oqc_env = ["OQC_URL", "OQC_EMAIL", "OQC_PASSWORD"]
        for s in oqc_env:
            if s not in os.environ:
                message = f"OQC environment variable missing. Please ensure {s} exists with correct info"
                avail = False
                if raise_error:
                    raise RuntimeError(message)
        message = "OQC configured"
    return (avail, message)


def cudaq_parse_args(arguments: str) -> argparse.Namespace:
    """Parse arguments related to aws braket qpus
    Args:
        args (str) : string of arguments
    Retursn:
        Returns the parsed args
    """
    # Create the parser
    parser = argparse.ArgumentParser(description="Process CUDAQ args")
    # Add device
    parser.add_argument(
        "--cudaqdevice",
        type=str,
        help="CUDAQ Device/Technology. ",
        required=True,
    )
    parser.add_argument(
        "--cudaqmachine",
        type=str,
        help="CUDAQ machine for a given set of technologies.  ",
        required=True,
    )
    # Add something else
    # parser.add_argument('--device', type=str, help='AWS Device')
    return get_argparse_args(arguments=arguments, parser=parser)


async def cudaq_check_qpu(arguments: str | argparse.Namespace) -> Tuple[bool, str]:
    """Wrapper to check if aws qpu is available.
    Args:
        args (str) : arguments to parse
    Returns:
        bool which is true if available.
    """
    # split the args string
    if isinstance(arguments, str):
        args = cudaq_parse_args(arguments)  # type: ignore
    else:
        args = arguments
    message: str = ""
    avail = True
    # how to check availability of a specific device/machine combo?

    return (avail, message)


async def cudaq_get_metadata(arguments: str | argparse.Namespace) -> QPUMetaData:
    """Return the metadata about the QPU
    Args:
        arguments (str|argparse.Namespace): arguments that contain the name of the device
    """
    if isinstance(arguments, str):
        args = cudaq_parse_args(arguments)
    else:
        args = arguments
    # need to figure out how best to use cudaq to talk to QPU
    meta_data = QPUMetaData(
        name=f"{args.cudaqdevice}-{args.cudaqmachine}",
        qubit_type="CUDAQ",
        qubit_count=100,
        cost=0,
        shot_speed=0,
    )

    # need to figure out how to parse the meta data
    return meta_data


@task(
    retries=5,
    retry_delay_seconds=100,
    timeout_seconds=600,
    task_run_name="Task-Launch-CUDAQ-QPU-{qpu_id}",
    # result_serializer=HybridQuantumWorkflowSerializer(),
)
async def launch_cudaq_qpu(
    myqpuworkflow: HybridQuantumWorkflowBase,
    qpu_id: int,
    arguments: str,
    profile_info: Tuple[str, str] | None = None,
) -> None:
    """Base task that checks for a CUDAQ qpu.
    Should have minimal retries and a wait between retries
    once qpu is launched set event so subsequent circuit tasks can run

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages events, yamls, etc when launching vqpu
        qpu_id (int): The qpu id
        arguments (str): Arguments that could be used
        profile_info (Tuple[str,str]): if passed, set the AWS_PROFILE environment variable and AWS_DEFAULT_REGION
    """

    # get flow run information and slurm job information
    logger = get_run_logger()
    logger.info(f"Getting CUDAQ QPU-{qpu_id}")
    args = cudaq_parse_args(arguments=arguments)
    avail, message = cudaq_check_device_credentials(args, True)
    logger.info(message)
    qpu_data = await cudaq_get_metadata(arguments=args)
    await myqpuworkflow.launch_qpu(
        qpu_id=qpu_id,
        qpu_data=qpu_data,
    )
    cudaq.set_target(args.cudaqdevice, machine=args.cudaqmachine)
    logger.info(
        f"Running CUDAQ QPU-{qpu_id} {args.cudaqdevice}-{args.cudaqmachine} ... "
    )


@task(
    retries=0,
    task_run_name="Task-Run-CUDAQ-{qpu_id}",
)
async def run_cudaq_qpu(
    myqpuworkflow: HybridQuantumWorkflowBase,
    qpu_id: int,
    arguments: str,
    walltime: float = 86400,  # one day
    sampling: float = 100.0,
) -> None:
    """Runs a task that waits to keep flow active
    so long as there are circuits to be run or have not exceeded the walltime

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages events to run vqpu till shutdown signal generated
        qpu_id (int): The qpu id
        arguments (str): arguments to pass to the check function that returns if the aws qpu is online
        walltime (float): Walltime to wait before shutting down qpu circuit submission
        sampling (float): how often to poll the cudaq service to see if device is online

    """
    logger = get_run_logger()
    logger.info(
        f"QPU-{qpu_id} running and will keep running till: circuits complete, walltime or qpu down ... "
    )
    # generate a list of asyncio tasks to determine when to proceed and shutdown the vqpu
    tasks = [
        asyncio.create_task(myqpuworkflow.task_circuitcomplete(vqpu_id=qpu_id)),
        asyncio.create_task(myqpuworkflow.task_walltime(walltime=walltime)),
        asyncio.create_task(
            myqpuworkflow.task_check_available(
                qpu_id=qpu_id,
                check=cudaq_check_qpu,
                arguments=arguments,
                sampling=sampling,
            )
        ),
    ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    results = f"QPU-{qpu_id} can be freed from workflow. Reason: "
    reasons = list()
    for d in done:
        results += d.result()
        reasons.append(d.result())
    for remaining in pending:
        remaining.cancel()
    # if offline then perhaps retry or try something
    if "Offline" in reasons:
        pass
    logger.info(results)


@task(
    retries=5,
    retry_delay_seconds=2,
    timeout_seconds=600,
    task_run_name="Task-Shutdown-QPU-{qpu_id}",
    # result_serializer=HybridQuantumWorkflowSerializer(),
)
async def shutdown_cudaq_qpu(
    myqpuworkflow: HybridQuantumWorkflowBase,
    qpu_id: int,
) -> None:
    """Shuts down CUDAQ QPU access

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages shutdown of vqpu
        qpu_id (int): The qpu id
    """
    logger = get_run_logger()
    logger.info(f"Shutdown CUDAQ QPU-{qpu_id} flow access")
    await myqpuworkflow.shutdown_qpu(qpu_id=qpu_id)
    logger.info("CUDAQ QPU access shutdown")


@flow(
    name="Launch vQPU Flow",
    flow_run_name="launch_cudaq_qpu_{qpu_id}_flow_on-{date:%Y-%m-%d:%H:%M:%S}",
    description="Launching CUDAQ QPU access with the appropriate task runner",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
    # result_serializer=HybridQuantumWorkflowSerializer(),
)
async def launch_cudaq_qpu_workflow(
    myqpuworkflow: HybridQuantumWorkflowBase,
    qpu_id: int,
    arguments: str,
    sampling: float = 100.0,
    walltime: float = 86400,
    date: datetime.datetime = datetime.datetime.now(),
) -> None:
    """Flow for running CUDQ QPU

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages vqpu workflow
        qpu_id (int): qpu id
        arguments (str): string of arguments to pass extra options to launching of vqpu
        sampling (float) : how often to check QPU is still available
        walltime (float) : walltime to keep running the qpu access
    """
    # clean up before launch
    myqpuworkflow.cleanupbeforestart(vqpu_id=qpu_id)
    logger = get_run_logger()
    logger.info(f"Launching CUDAQ QPU-{qpu_id}")
    creds = cudaq_check_credentials()
    logger.info(creds["message"])

    # now launch
    future = await launch_cudaq_qpu.submit(
        myqpuworkflow=myqpuworkflow,
        qpu_id=qpu_id,
        arguments=arguments,
    )
    await future.result()

    # now run it
    # qpu_data = myqpuworkflow.active_qpus[qpu_id]
    qpu_data = await myqpuworkflow.getqpudata(qpu_id)
    logger.info(
        f"CUDAQ QPU-{qpu_id} {qpu_data.name} online and will keep running till circuits complete, offline or hit walltime ... "
    )
    future = await run_cudaq_qpu.submit(
        myqpuworkflow=myqpuworkflow,
        qpu_id=qpu_id,
        arguments=arguments,
        sampling=sampling,
        walltime=walltime,
    )
    await future.result()

    # once the run has finished, shut it down
    future = await shutdown_cudaq_qpu.submit(myqpuworkflow=myqpuworkflow, qpu_id=qpu_id)
    await future.result()
