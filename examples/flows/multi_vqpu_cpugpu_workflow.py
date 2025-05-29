"""
@brief This example shows how to structure a multi-vqpu workflow.

This workflow spins up two or more vqpus and then has a workflow that runs cpu/gpu flows that also then spawn circuit flows.

"""

import sys, os, re

# import qbitbridge
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../../")
# import circuits
from time import sleep
import datetime
from typing import List, Set, Callable, Tuple, Dict
from qbitbridge.options import vQPUWorkflow
from qbitbridge.vqpubase import HybridQuantumWorkflowBase
from qbitbridge.vqpuflow import (
    launch_vqpu_workflow,
    circuits_with_nqvpuqs_workflow,
    circuits_vqpu_workflow,
    cpu_workflow,
    gpu_workflow,
    postprocessing_histo_plot,
    run_cpu,
    run_circuits_once_vqpu_ready,
)
from qbitbridge.utils import EventFile, save_artifact
from workflow.circuits.qristal_circuits import simulator_setup, noisy_circuit
import asyncio
from prefect import flow
from prefect_dask import DaskTaskRunner
from prefect.logging import get_run_logger
import numpy as np


@flow(
    name="Example CPU flow with QPU-circuit sumbission",
    flow_run_name="cpu_qpu_flow_on_vqpu_{vqpu_ids_subset}-{date:%Y-%m-%d:%H:%M:%S}",
    description="Running CPU flows than can also create QPU-circuits for submission",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def cpu_with_random_qpu_workflow(
    myqpuworkflow: HybridQuantumWorkflowBase,
    vqpu_ids_subset: List[int],
    circuits: Dict[str, List[Callable | Tuple[Callable, Callable]]],
    circuitargs: str,
    cpuexecs: List[str],
    cpuargs: List[str],
    gpuexecs: List[str],
    gpuargs: List[str],
    max_num_gpu_launches: int = 4,
    date: datetime.datetime = datetime.datetime.now(),
) -> None:
    """Flow for running cpu based programs.

    arguments (str): string of arguments to pass extra options to run cpu
    """
    logger = get_run_logger()
    logger.info("Launching CPU Master with QPU flow")
    sleepval = 5.0 + np.random.uniform() * 10
    logger.info(f"Sleeping for {sleepval}")
    await asyncio.sleep(sleepval)
    # submit the task and wait for results
    futures = []
    for exec, args in zip(cpuexecs, cpuargs):
        logger.info(f"Running {exec} with {args}")
        futures.append(
            await run_cpu.submit(myqpuworkflow=myqpuworkflow, exec=exec, arguments=args)
        )
    for f in futures:
        await f.result()
    tasks = {
        "gpu": list(),
        "cpu": list(),
        "qpu": dict(),
    }
    for vqpu_id in vqpu_ids_subset:
        tasks["qpu"][vqpu_id] = list()

    circflow = circuits_vqpu_workflow.with_options(
        task_runner=myqpuworkflow.gettaskrunner("circuit"),
    )
    async with asyncio.TaskGroup() as tg:
        for i in range(max_num_gpu_launches):
            if np.random.uniform() > 0.5:
                tasks["gpu"].append(
                    tg.create_task(
                        gpu_workflow.with_options(
                            task_runner=myqpuworkflow.gettaskrunner("gpu")
                        )(
                            myqpuworkflow=myqpuworkflow,
                            execs=gpuexecs,
                            arguments=gpuargs,
                        )
                    )
                )
                if np.random.uniform() > 0.75:
                    tasks["cpu"].append(
                        tg.create_task(
                            cpu_workflow.with_options(
                                task_runner=myqpuworkflow.gettaskrunner("cpu")
                            )(
                                myqpuworkflow=myqpuworkflow,
                                execs=cpuexecs,
                                arguments=cpuargs,
                            )
                        )
                    )
        # either spin up real vqpu
        for vqpu_id in vqpu_ids_subset:
            if np.random.uniform() > 0.5:
                tasks["qpu"][vqpu_id] = tg.create_task(
                    circflow(
                        myqpuworkflow=myqpuworkflow,
                        vqpu_id=vqpu_id,
                        circuits=circuits[f"vqpu_{vqpu_id}"],
                        arguments=circuitargs,
                        circuits_complete=True,
                    )
                )
            else:
                # note that since the workflow is also designed to have qpu events,
                # if explicitly waiting for event, use qpu_<id>_*
                await myqpuworkflow.events[f"qpu_{vqpu_id}_launch"].wait()
                myqpuworkflow.events[f"qpu_{vqpu_id}_circuits_finished"].set()

    logger.info("Finished CPU with QPU flow")


@flow(
    name="Multi-vQPU Test",
    flow_run_name="Mulit-vQPU_test_{myqpuworkflow.vqpu_ids}_on-{date:%Y-%m-%d:%H:%M:%S}",
    description="Running a multi-(v)QPU+CPU+GPU hybrid workflow",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def workflow(
    myqpuworkflow: HybridQuantumWorkflowBase,
    circuitargs: str,
    cpuexecs: List[str],
    cpuargs: List[str],
    gpuexecs: List[str],
    gpuargs: List[str],
    vqpu_walltimes: List[float],
    date: datetime.datetime = datetime.datetime.now(),
):
    """
    @brief overall workflow for hydrid multi-(v)QPU+CPU+GPU
    """
    MAXWALLTIME: float = 86400.0
    if len(vqpu_walltimes) < len(myqpuworkflow.vqpu_ids):
        num_to_add = len(myqpuworkflow.vqpu_ids) - len(vqpu_walltimes)
        for i in range(num_to_add):
            vqpu_walltimes.append(MAXWALLTIME)

    logger = get_run_logger()
    logger.info("Running hybrid multi-(v)QPU workflow")

    vqpuflows = dict()
    circuits = dict()
    circuitflows = dict()
    for vqpu_id in myqpuworkflow.vqpu_ids:
        if vqpu_id > 1 and vqpu_id < 3:
            circuits[f"vqpu_{vqpu_id}"] = [noisy_circuit, noisy_circuit, noisy_circuit]
        elif vqpu_id > 3:
            circuits[f"vqpu_{vqpu_id}"] = [(noisy_circuit, postprocessing_histo_plot)]
        else:
            circuits[f"vqpu_{vqpu_id}"] = [(noisy_circuit, postprocessing_histo_plot)]

        # lets define the flows with the appropriate task runners
        # this would be for the real vqpu
        vqpuflows[f"vqpu_{vqpu_id}"] = launch_vqpu_workflow.with_options(
            task_runner=myqpuworkflow.gettaskrunner("vqpu"),
        )
    circuitflows = circuits_with_nqvpuqs_workflow.with_options(
        task_runner=myqpuworkflow.gettaskrunner("circuit"),
    )
    othercircuitflows = cpu_with_random_qpu_workflow.with_options(
        task_runner=myqpuworkflow.gettaskrunner("cpu"),
    )

    # silly change so that any vqpu_ids past 3 are
    # not provided to circuitflows but rather
    # set aside to the cpu flow that can spawn vqpus
    circ_vqpu_ids = list()
    other_vqpu_ids = list()
    for vqpu_id in myqpuworkflow.vqpu_ids:
        if vqpu_id >= 3:
            other_vqpu_ids.append(vqpu_id)
        else:
            circ_vqpu_ids.append(vqpu_id)

    async with asyncio.TaskGroup() as tg:
        # either spin up real vqpu
        for i in range(len(myqpuworkflow.vqpu_ids)):
            vqpu_id = myqpuworkflow.vqpu_ids[i]
            vqpu_walltime = vqpu_walltimes[i]
            tg.create_task(
                vqpuflows[f"vqpu_{vqpu_id}"](
                    myqpuworkflow=myqpuworkflow,
                    walltime=vqpu_walltime,
                    vqpu_id=vqpu_id,
                )
            )

        # silly change so that any vqpu_ids past 3 are
        # not provided to circuitflows but rather
        # set aside to the cpu flow that can spawn vqpus
        tg.create_task(
            circuitflows(
                myqpuworkflow=myqpuworkflow,
                circuits=circuits,
                vqpu_ids_subset=circ_vqpu_ids,
                arguments=circuitargs,
            )
        )
        tg.create_task(
            othercircuitflows(
                myqpuworkflow=myqpuworkflow,
                circuits=circuits,
                vqpu_ids_subset=other_vqpu_ids,
                circuitargs=circuitargs,
                cpuexecs=cpuexecs,
                cpuargs=cpuargs,
                gpuexecs=gpuexecs,
                gpuargs=gpuargs,
            )
        )

    for k in myqpuworkflow.events.keys():
        myqpuworkflow.events[k].clean()

    logger.info("Finished hybrid multi-(v)QPU workflow")


@flow(
    name="Multi-vQPU Test",
    flow_run_name="Mulit-vQPU_test_{myqpuworkflow.vqpu_ids}_on-{date:%Y-%m-%d:%H:%M:%S}",
    description="Running a multi-(v)QPU+CPU+GPU hybrid workflow",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def workflow2(
    myqpuworkflow: HybridQuantumWorkflowBase,
    circuitargs: str,
    date: datetime.datetime = datetime.datetime.now(),
):
    circflow = circuits_vqpu_workflow.with_options(
        task_runner=myqpuworkflow.gettaskrunner("circuit"),
    )

    vqpuflows = dict()
    circuits = dict()
    for vqpu_id in myqpuworkflow.vqpu_ids:
        vqpuflows[f"vqpu_{vqpu_id}"] = launch_vqpu_workflow.with_options(
            task_runner=myqpuworkflow.gettaskrunner("vqpu"),
        )
    for vqpu_id in myqpuworkflow.vqpu_ids:
        circuits[f"vqpu_{vqpu_id}"] = [noisy_circuit, noisy_circuit, noisy_circuit]

    async with asyncio.TaskGroup() as tg:
        for vqpu_id in myqpuworkflow.vqpu_ids:
            tg.create_task(
                vqpuflows[f"vqpu_{vqpu_id}"](
                    myqpuworkflow=myqpuworkflow,
                    walltime=200,
                    vqpu_id=vqpu_id,
                )
            )
        for vqpu_id in myqpuworkflow.vqpu_ids:
            tg.create_task(
                circflow(
                    myqpuworkflow=myqpuworkflow,
                    vqpu_id=vqpu_id,
                    circuits=circuits[f"vqpu_{vqpu_id}"],
                    arguments=circuitargs,
                    circuits_complete=True,
                )
            )


def wrapper_to_async_flow(
    yaml_template: str | None = None,
    script_template: str | None = None,
    cluster: str | None = None,
    circuitargs: str = "",
    cpuexecs: List[str] = [
        "/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp",
        "/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp",
    ],
    cpuargs: List[str] = [
        "134217728",
        "1073741824",
    ],
    gpuexecs: List[str] = [
        "/software/projects/pawsey0001/pelahi/profile_util/examples/gpu-openmp/bin/gpu-openmp",
        "/software/projects/pawsey0001/pelahi/profile_util/examples/gpu-openmp/bin/gpu-openmp",
        "/software/projects/pawsey0001/pelahi/profile_util/examples/gpu-openmp/bin/gpu-openmp",
    ],
    gpuargs: List[str] = ["134217728,10", "134217728,1", "1073741824,10"],
):
    """
    @brief run the workflow with the appropriate task runner
    """
    if yaml_template == None:
        yaml_template = f"{os.path.dirname(os.path.abspath(__file__))}/../../workflow/qb-vqpu/remote_vqpu_ella_template.yaml"
    if script_template == None:
        script_template = f"{os.path.dirname(os.path.abspath(__file__))}/../../workflow/qb-vqpu/vqpu_template_ella_qpu-1.7.0.sh"
    if cluster == None:
        cluster = "ella-qb-1.7.0-pypath"
    myflow = HybridQuantumWorkflowBase(
        cluster=cluster,
        vqpu_ids=[1, 2, 3, 16],
        vqpu_template_yaml=yaml_template,
        vqpu_template_script=script_template,
        eventloc=f"{os.path.dirname(os.path.abspath(__file__))}/events/",
    )

    # asyncio.run(workflow2(
    #     myqpuworkflow=myflow,
    #     circuitargs=circuitargs)
    # )

    asyncio.run(
        workflow(
            myqpuworkflow=myflow,
            circuitargs=circuitargs,
            cpuexecs=cpuexecs,
            cpuargs=cpuargs,
            gpuexecs=gpuexecs,
            gpuargs=gpuargs,
            vqpu_walltimes=[86400, 500, 1000, 1000],
        )
    )


if __name__ == "__main__":
    yaml_template = None
    script_template = None
    cluster = None
    res = [i for i in sys.argv if re.findall("--yaml=", i)]
    if len(res) > 0:
        yaml_template = res[0].split("=")[1]
    res = [i for i in sys.argv if re.findall("--script=", i)]
    if len(res) > 0:
        script_template = res[0].split("=")[1]
    res = [i for i in sys.argv if re.findall("--cluster=", i)]
    if len(res) > 0:
        cluster = res[0].split("=")[1]
    wrapper_to_async_flow(
        yaml_template=yaml_template,
        script_template=script_template,
        cluster=cluster,
    )
