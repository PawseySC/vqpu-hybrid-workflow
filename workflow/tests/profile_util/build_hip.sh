#!/bin/bash 
cmake -B build-hip -DPU_ENABLE_HIP=ON -DPU_ENABLE_MPI=OFF -DCMAKE_CXX_COMPILER=hipcc 
cd build-hip
make -j
