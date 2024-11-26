"""
Let's define some clusters. Here the configuration is for Setonix
"""

from dask.distributed import Client
from dask_jobqueue import SLURMCluster

work_kwargs = {
    "queue": "work",
    "cores": 128,  
    "memory": "256 GB",
    "shebang": "#!/bin/bash",
    "account": "pawsey0001",
    "walltime": "24:00:00",
    "job_mem": "0",
    "job_script_prologue": ["source ~/.bashrc"],
    "job_directives_skip": [],  
    "job_extra_directives": [],  
}

gpu_kwargs = {
    "queue": "gpu",
    "cores": 64,  
    "memory": "256 GB",
    "shebang": "#!/bin/bash",
    "account": "pawsey0001-gpu",
    "walltime": "24:00:00",
    "job_mem": "0",
    "job_script_prologue": ["source ~/.bashrc"],
    "job_directives_skip": [],  
    "job_extra_directives": [],  
}

work = SLURMCluster(**work_kwargs)
gpu = SLURMCluster(**gpu_kwargs)

# setonix_clusters = {'work': work, 'gpu': gpu}

# for k in setonix_clusters.keys():
#     print(k, setonix_clusters[k])

