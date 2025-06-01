.. _usage:

Using QBitBridge
################

The workflow makes use of `Prefect <https://www.prefect.io>`_, `Postgres <https://www.postgresql.org/>`_, 
`Slurm <https://slurm.schedmd.com/documentation.html>`_. 
We do not discuss setting a slurm service here as this service will likely be setup by the HPC centre.

.. topic::_running:

Running A Workflow
==================

To run a workflow, there a few key steps that are needed:

* Launch Prefect and Postgres services on a node that allows running long-running services. 
  These services (typically) do not require significant computational or memory resources. 
* Ensure you have set the PREFECT_API_URL to the appropriate port and hostname.
* Launch with `python <workflow.py>`

Services to run Prefect
=======================

Running postgres
----------------

The workflows will be best run with a `Prefect <https://www.prefect.io>`_ server running using ``uvicorn`` and a postgres database. 
The bundle includes two scripts located in ``workflow/scripts/`` designed to launch these services. 
By default, there is an assumption that both of these services run on the same host but that does not need to be the case. 

This can be started with ``start_postgres.sh`` script. This will launch the postgres database using the 
`Singularity <https://docs.sylabs.io/guides/latest/user-guide/>`_ container engine. 
This script makes use of several key ``POSTGRES`` environment variables (like ``POSTGRES_ADDR``). 
This will pull the latest postgres container image from docker hub to locally store it. 
This script could be altered to use other container engines as well. 

Running Prefect Server
----------------------

This can be started with ``start_prefect.sh``. This will launch prefect using

.. code-block::

    python -m uvicorn \
      --app-dir ${prefect_python_venv}/lib/python3.11/site-packages/ \
      --factory prefect.server.api.server:create_app \
      --host 0.0.0.0 \
      --port 4200 \
      --timeout-keep-alive 10 \
      --limit-max-requests 4096 \
      --timeout-graceful-shutdown 7200 & 

and when running on the command line with a simple workflow, one can follow the reported message 
and set ``export PREFECT_API_URL=http://${POSTGRES_ADDR}:4200/api`` where ``POSTGRES_ADD`` 
will be replaced with the host on which the postgres database will be run. 

.. note:: At the moment, scripts geared towards using Singularity and on the same host. 
   You will need to alter it to use other container engines. 

Prefect UI
----------

Once these services are running you can use your browser to load up the Prefect UI by using 127.0.0.1:4200
once you have setup an ssh tunnel `ssh -N -f -L 4200:<remote>:4200 <remote>`.

Setup cluster yaml file 
-----------------------

It is important to consider what resources a given flow will need. 
This will be set by the `DaskTaskRunner` used by the flow, which is defined in the 
cluster yaml file. This it is important to write an appropriate cluster configuration for the
HPC cluster and the available Slurm partitions. 
Examples of a cluster configurations can be found in :file:`workflow/clusters/`. 
This configuration needs a certain minimum set of named configurations: 

* `generic`: a small resource for running computationally simple tasks (such as launching some services)
* `vqpu`: for running gpu-accelerated vQPU service. 
* `circuit`: for running circuit submission service (likely need to ensure Python paths are correct)
* `cpu`: for moderate cpu-heavy tasks 
* `gpu`: for gpu tasks 

You will find in the examples that `generic-aws`, `circuit-aws` are also present to run `aws` tasks
which need environment variables setup to log in to the AWS CLI. Similarly there are `generic-quera`, 
`circuit-quera` that also need some environment variables set for access. 

Designing A Hybrid Workflow
===========================

There are several things to consider when designing a workflow. As a start point, we suggest looking at 
examples of some workflows in the :file:`examples/flows/` directory (see :ref:`examples`).

Let's start by using the :file:`tutorial_workflow.py`. This workflow starts with some key imports 

QbitBridge imports 
------------------

Load the key classes and other useful routines 

.. code-block:: python

   # import key classes from
   from qbitbridge.vqpubase import (
      QPUMetaData,
      HybridQuantumWorkflowBase,
   )

   # import useful utilities
   from qbitbridge.utils import (
      EventFile, # Event files 
      save_artifact, # to save prefect artificats
      get_num_gpus, # to get number of gpus 
   )

   # import basic flows and tasks  from the vqpuflow as desired
   from qbitbridge.vqpuflow import (
      # tasks
      run_cpu, # run a cpu task 
      run_gpu, # run a gpu task 
      # and here are some flows
      launch_vqpu_workflow, # launch a vqpu workflow 
      cpu_workflow, # launch a cpu workflow
      gpu_workflow, # launch a gpu workflow 
   )

Prefect imports
---------------

It will also be critical to import relevant prefect items 

.. code-block:: python

   import asyncio # for asynchronous tasks and flows
   from prefect import task, flow # task and flow decorators
   from prefect.logging import get_run_logger #logger

Define tasks and flows
----------------------

Then you can start defining some simple tasks. Here we have a standard Prefect task 
along with an ayncio async task. 

.. code-block:: python 

   # let's create some tasks
   @task(name="Example task", task_run_name="example_task-{date:%Y-%m-%d:%H:%M:%S}")
   def simple_task(
      date: datetime.datetime = datetime.datetime.now(),
   ):
      """Task"""
      pass


   @task(
      name="Example async task",
      task_run_name="example_async_task-{date:%Y-%m-%d:%H:%M:%S}",
   )
   async def simple_async_task(
      date: datetime.datetime = datetime.datetime.now(),
   ):
      """Async task"""
      pass

Flows can be simple and just submit several tasks and get the results or try to have some
dependency between tasks. We show a standard Prefect flow and an asynio asynchronous one. 

.. code-block:: python

   @flow(
      name="Example flow",
      flow_run_name="example_flow-{date:%Y-%m-%d:%H:%M:%S}",
   )
   def workflow(
      myqpuworkflow: HybridQuantumWorkflowBase,
      date: datetime.datetime = datetime.datetime.now(),
   ) -> None:
      """Example flow"""
      logger = get_run_logger()
      logger.info("Example flow")
      # let's submit a task to the flow
      future = simple_task.submit()
      # and then get results
      future.result()
      logger.info("Finished flow")


   @flow(
      name="Example async flow",
      flow_run_name="example_async_flow-{date:%Y-%m-%d:%H:%M:%S}",
   )
   async def async_workflow(
      myqpuworkflow: HybridQuantumWorkflowBase,
      date: datetime.datetime = datetime.datetime.now(),
   ) -> None:
      """Example async flow that can call asynchronous functions"""
      logger = get_run_logger()
      logger.info("Example async flow")
      # submit several tasks at once
      futures = []
      for i in range(10):
         futures.append(simple_task.submit())
      for f in futures:
         f.result()
      # We can also submit some asynchronous tasks
      async with asyncio.TaskGroup() as tg:
         for i in range(10):
               tg.create_task(simple_async_task.submit())
         # once the async taskgroup is finished all tasks have been submited
      # we can also just create a list of tasks
      tg = []
      for i in range(10):
         tg.append(asyncio.create_task(simple_async_task.submit()))
      done, pending = await asyncio.wait(tg)
      # once they are all done, let's get the results
      for d in done:
         d.result()

      logger.info("Finished async flow")

More details of a complex flow can be found in :ref:`examples`. 