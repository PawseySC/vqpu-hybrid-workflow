#!/bin/bash 
cmake -B build-cuda -DPU_ENABLE_CUDA=ON -DPU_ENABLE_MPI=OFF -DCMAKE_CXX_COMPILER=nvc++ -DCMAKE_CXX_FLAGS="-cuda"
cd build-cuda
make -j
