.. _examples:

Example Workflow
################

There are example workflows in `examples/flows/` that could make use of the tasks and flows defined in **QBitBridge**. 
We discuss a multi-vqpu example here see :file:`examples/flows/multi_vqpu_cpugpu_workflow.py`. 
This flow uses some basic build-block tasks and flows defined in :file:`qbitbridge/vqpuflows.py`. 
The Prefect view is of this flow is shown below.

.. figure:: figs/example_multivqpuflow.png
   :width: 100%
   :align: center
   :alt: Workflow example

   An example of a multi-vQPU workflow as visualized by the Prefect UI.
   
This flow demonstrates running several vQPUs that await circuits being sent to them before being shutdown along with other 
vQPUs that are ideal and shutdown after a certain amount of time. It also spawns CPU-oriented and GPU-oriented flows and 
how to run these flows in an asynchronous fashion. 

We strongly suggest you alter the CPU and GPU commands before trialling this workflow if you would like to test it. The 
code as it stands also uses a cluster specific yaml file where the python path variable has been updated to include the 
absolute path of the :file:`workflow/` directory. 

This example showcases a few key things:

* Use of the `HybridQuantumWorkflowBase` class to manage a flow
* Use of basic flows like `gpu_workflow` being launched with a `DaskTaskRunner` that differs form the parent flow runner. 
* Use of asynchronous flows launched using `asyncio.TaskGroup`
* Multiple vQPUs being launched and awaiting circuits
* Circuits being sent to several different vQPUs from a single flow
