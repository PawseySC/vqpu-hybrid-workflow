#!/bin/bash

function kill_pids_of_command()
{
    if [[ ! -z $1 ]]; then 
        pids=($(ps x | grep "$1" | awk '{print $1}'))
        for p in ${pids[@]}; do kill ${p}; done 
    fi 
}

echo "Closing prefect (uvicorn) and local postgres server running in singularity"
kill_pids_of_command "python -m uvicorn"
kill_pids_of_command "postgres -c max_connections=1000 -c shared_buffers=1024MB" 

