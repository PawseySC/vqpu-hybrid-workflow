"""
@file vqpuqiskit.py
@brief Collection of Qiskit oriented tasks, flows and interfaces

"""

import argparse
import time, datetime, subprocess, os, select, psutil, copy
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

# Qiskit imports: Import Qiskit bloqade modules
libcheck = check_python_installation("qiskit")
if not libcheck:
    raise ImportError("Missing Qiskit library, cannot interact with IBM QPUs")
libcheck = check_python_installation("qiskit_ibm_runtime")
if not libcheck:
    raise ImportError("Missing Qiskit Runtime library, cannot interact with IBM QPUs")
# import bloqade digital and analogue
import qiskit
from qiskit_ibm_runtime import QiskitRuntimeService, IBMInputValueError
from qiskit_ibm_runtime.accounts.exceptions import AccountNotFoundError


def qiskit_check_credentials(
    account_info: Tuple[str, str] | None = None, report_keys: bool = False
) -> Dict[str, str]:
    """Print the Qiskit Credentials
    Args:
        account_info (Tuple[str,str]): token and name of account to use. If not provided, use default account
        report_keys (bool) : report access and secret keys. NOT recommended unless debugging. Do not activate when running production logging
    Returns:
        Tuple of relevant information : message, and relevant access information like access_key
    Raises:
        RuntimeErorr if access keys not defined
    """
    # what do grab to check for qiskit access?
    # The standard mechanism is to save account information
    # using the following, with the key thing to provide being the token
    # grabbed from the ibm dashboard.
    """
    service = QiskitRuntimeService.save_account(
        token="token", # IBM Cloud API key.
        # Your token is confidential. Do not share your token in public code.
        instance="<IBM Cloud CRN or instance name>", # Optionally specify the instance to use.
        plans_preference="['open', 'premium']", # Optionally set the types of plans to prioritize.  This is ignored if the instance is specified.
        # Additionally, instances of a certain plan type are excluded if the plan name is not specified.
        region="us-east", # Optionally set the region to prioritize. Accepted values are 'us-east' or 'eu-de'. This is ignored if the instance is specified.
        name="<account-name>", # Optionally name this set of account credentials. 
        set_as_default=True, # Optionally set these as your default credentials.
    )
    """
    token = None
    name = None
    if account_info is not None:
        token, name = account_info
    # currently just using a try/catch of initialising a runtime service.
    if token is not None:
        try:
            qiskit_service = QiskitRuntimeService(token=token, name=name)
        except IBMInputValueError or AccountNotFoundError:
            if report_keys:
                message = (
                    f"IBM Qiskit account error using {token}. Please check token used."
                )
            else:
                message = "IBM Qiskit account error using a passed token. Please check token used."
            raise RuntimeError(message)
        qiskit_service.save_account(token=token, name=name, set_as_default=True)
    else:
        try:
            qiskit_service = QiskitRuntimeService()
        except IBMInputValueError or AccountNotFoundError:
            message = (
                "IBM Qiskit account error using default token stored via saved account."
            )
            message += "Please check saved account information."
            raise RuntimeError(message)

    # get account information.
    result = qiskit_service.active_account()
    # likely need to parse account info dictionary so token is not reported unless desired.
    return result


def qiskit_parse_args(arguments: str) -> argparse.Namespace:
    """Parse arguments related to qiskit access
    Args:
        args (str) : string of arguments
    Retursn:
        Returns the parsed args
    """
    # Create the parser
    parser = argparse.ArgumentParser(description="Process Qiskit args")
    service = QiskitRuntimeService()
    # Add device
    parser.add_argument(
        "--qiskitdevice",
        type=str,
        required=True,
        help=f"Device ({service.backends()})",
    )
    parser.add_argument(
        "--qiskitsimulatedevice",
        default=False,
        type=bool,
        required=False,
        help=f"Include simulator (y/n)",
    )
    return get_argparse_args(arguments=arguments, parser=parser)


async def qiskit_check_qpu(arguments: str | argparse.Namespace) -> Tuple[bool, str]:
    """Wrapper to check if qiskit qpu is available.
    Args:
        args (str | argparse.Namespace) : arguments
    Returns:
        bool which is true if available.
    """
    # split the args string
    if isinstance(arguments, str):
        args = qiskit_parse_args(arguments)
    else:
        args = arguments
    service = QiskitRuntimeService()
    backend = service.backend(
        args.qiskitdevice, simulator=args.qiskitimulateddevice, operational=True
    )
    if backend is not None:
        avail = True
    else:
        avail = False
    # need to look at how to poll whether device is available
    return avail


async def qiskit_get_metadata(arguments: str | argparse.Namespace) -> QPUMetaData:
    """Return the metadata about the QPU
    Args:
        arguments (str|argparse.Namespace): arguments that contain the name of the device
    Returns:
        QPUMetaData of desired device
    """
    if isinstance(arguments, str):
        args = qiskit_parse_args(arguments)
    else:
        args = arguments
    qiskit_service = QiskitRuntimeService()
    qiskit_devices: List = []
    for backend in qiskit_service.backends():
        qiskit_devices.append(backend.config_name)
    if args.qiskitdevice not in qiskit_devices:
        raise ValueError(
            f"Device {args.qiskitdevice} not in list of available devices ({qiskit_devices})."
        )
    backend = qiskit_service.backend(args.qiskitdevice)
    meta_data = QPUMetaData(
        name=backend.name,
        qubit_type="IBM",
        qubit_count=backend.num_qubits,
        cost=(0.0, "pershot"),
        shot_speed=0,
    )
    return meta_data


@task(
    retries=5,
    retry_delay_seconds=100,
    timeout_seconds=600,
    task_run_name="Task-Launch-QISKIT-QPU-{qpu_id}",
    # result_serializer=HybridQuantumWorkflowSerializer(),
)
async def launch_qiskit_qpu(
    myqpuworkflow: HybridQuantumWorkflowBase,
    qpu_id: int,
    arguments: str,
) -> None:
    """Base task that checks for a Qiskit qpu.
    Should have minimal retries and a wait between retries
    once qpu is launched set event so subsequent circuit tasks can run

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages events, yamls, etc when launching vqpu
        qpu_id (int): The qpu id
        arguments (str): Arguments that could be used
    """

    # get flow run information and slurm job information
    logger = get_run_logger()
    logger.info(f"Getting Qiskit QPU-{qpu_id}")
    args = qiskit_parse_args(arguments=arguments)
    avail, message = await qiskit_check_qpu(arguments=args)
    if not avail:
        raise RuntimeError(message)
    logger.info(message)
    qpu_data = await qiskit_get_metadata(arguments=args)
    await myqpuworkflow.launch_qpu(
        qpu_id=qpu_id,
        qpu_data=qpu_data,
    )
    logger.info(f"Running Qiskit QPU-{qpu_id} {args.qiskitdevice} ... ")


@task(
    retries=0,
    task_run_name="Task-Run-qiskit-QPU-{qpu_id}",
)
async def run_qiskit_qpu(
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
        arguments (str): arguments to pass to the check function that returns if the qiskit qpu is online
        walltime (float): Walltime to wait before shutting down qpu circuit submission
        sampling (float): how often to poll the qiskit service to see if device is online

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
                check=qiskit_check_qpu,
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
async def shutdown_qiskit_qpu(
    myqpuworkflow: HybridQuantumWorkflowBase,
    qpu_id: int,
) -> None:
    """Shuts down qpu

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages shutdown of vqpu
        qpu_id (int): The qpu id
    """
    logger = get_run_logger()
    logger.info(f"Shutdown Qiskit QPU-{qpu_id} flow access")
    await myqpuworkflow.shutdown_qpu(qpu_id=qpu_id)
    logger.info("Qiskit QPU access shutdown")


@flow(
    name="Launch vQPU Flow",
    flow_run_name="launch_qiskit_qpu_{qpu_id}_flow_on-{date:%Y-%m-%d:%H:%M:%S}",
    description="Launching Qiskit QPU access with the appropriate task runner",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
    # result_serializer=HybridQuantumWorkflowSerializer(),
)
async def launch_qiskit_qpu_workflow(
    myqpuworkflow: HybridQuantumWorkflowBase,
    qpu_id: int,
    arguments: str,
    sampling: float = 100.0,
    walltime: float = 86400,
    date: datetime.datetime = datetime.datetime.now(),
) -> None:
    """Flow for running vqpu

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
    logger.info(f"Launching Qiskit QPU-{qpu_id}")
    creds = qiskit_check_credentials()
    logger.info(creds["message"])

    # now launch
    future = await launch_qiskit_qpu.submit(
        myqpuworkflow=myqpuworkflow,
        qpu_id=qpu_id,
        arguments=arguments,
    )
    await future.result()

    # now run it
    # qpu_data = myqpuworkflow.active_qpus[qpu_id]
    qpu_data = await myqpuworkflow.getqpudata(qpu_id)
    logger.info(
        f"QISKIT QPU-{qpu_id} {qpu_data.name} online and will keep running till circuits complete, offline or hit walltime ... "
    )
    future = await run_qiskit_qpu.submit(
        myqpuworkflow=myqpuworkflow,
        qpu_id=qpu_id,
        arguments=arguments,
        sampling=sampling,
        walltime=walltime,
    )
    await future.result()

    # once the run has finished, shut it down
    future = await shutdown_qiskit_qpu.submit(
        myqpuworkflow=myqpuworkflow, qpu_id=qpu_id
    )
    await future.result()
