#!/bin/bash

function kill_pids_of_command()
{
    ps x > .pids
    if [[ ! -z $1 ]]; then 
        
        pids=($(grep "$1" .pids | awk '{print $1}'))
        if [[ ${#pids[@]} -gt 0 ]]; then 
            echo "Cleanning up ${1} ... "
            for p in ${pids[@]}; do kill ${p}; done
        fi
    fi 
    rm .pids
}

echo "Closing prefect (uvicorn) and local postgres server running in singularity"
kill_pids_of_command "prefect"
kill_pids_of_command "python -m uvicorn"
kill_pids_of_command "postgres -c max_connections=1000 -c shared_buffers=1024MB" 

