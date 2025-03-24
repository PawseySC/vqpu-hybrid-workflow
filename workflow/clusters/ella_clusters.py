"""
Let's define some clusters. Here the configuration is for ella
"""

from dask.distributed import Client
from dask_jobqueue import SLURMCluster

def GetEllaClusters():
    # this path is currently not global so need to add it when deploying a vqpu job
    vqpu_mod_path = "/software/ella/2025.02/modules/custom/"
    vqpu_mod = "vQPU-QB/1.7.0"

    gpu_kwargs = {
        "queue": "gpu",
        "cores": 70,  
        "memory": "480 GB",
        "shebang": "#!/bin/bash",
        "account": "pawsey0001-gpu",
        "walltime": "24:00:00",
        "job_mem": "0",
        "job_script_prologue": ["source ~/.bashrc"],
        "job_directives_skip": [],  
        "job_extra_directives": ["--gres=gpu:1"],  
    }

    cpu_kwargs = {
        "queue": "gpu",
        "cores": 70,  
        "memory": "480 GB",
        "shebang": "#!/bin/bash",
        "account": "pawsey0001-gpu",
        "walltime": "24:00:00",
        "job_script_prologue": ["source ~/.bashrc"],
        "job_directives_skip": [],  
        "job_extra_directives": [""],  
    }

    vqpu_kwargs = {
        "queue": "gpu",
        "cores": 70,  
        "memory": "480 GB",
        "shebang": "#!/bin/bash",
        "account": "pawsey0001-gpu",
        "walltime": "24:00:00",
        "job_mem": "0",
        "job_script_prologue": ["source ~/.bashrc", "module use "+vqpu_mod_path, "module load "+vqpu_mod],
        "job_directives_skip": [],  
        "job_extra_directives": ["--gres=gpu:1"],  
    }

    gpu = SLURMCluster(**gpu_kwargs)
    cpu = SLURMCluster(**cpu_kwargs)
    vqpu = SLURMCluster(**vqpu_kwargs)

    clusters = {'gpu': gpu, 'vqpu': vqpu, 'cpu': cpu}

    for k in clusters.keys():
        print(k, clusters[k].job_script())
    return clusters

