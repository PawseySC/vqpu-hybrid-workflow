# Copyright (c) 2022 Quantum Brilliance Pty Ltd

# This example shows how to turn on noise in a simulation,
# and how to modify the default noise model used.

import sys
import qristal.core

def main(arguments):

  # Create a quantum computing session using Qristal
  my_sim = qristal.core.session()

  # Set up meaningful defaults for session parameters
  my_sim.init()

  # 2 qubits
  my_sim.qn = 2

  # Aer simulator selected
  my_sim.acc = "loopback"
  my_sim.remote_backend_database_path = "./remote_backends.yaml"

  # Set this to true to include noise
  my_sim.noise = True

  # Define the kernel
  my_sim.instring = '''
  __qpu__ void MY_QUANTUM_CIRCUIT(qreg q)
  {
     OPENQASM 2.0;
     include "qelib1.inc";
     creg c[2];
     h q[0];
     cx q[0],q[1];
     measure q[1] -> c[1];
     measure q[0] -> c[0];
  }
  '''

  # If a non-default noise model has been requested, create it. If you
  # just want to use default noise, the following is not needed.
  if "--noisier" or "--qdk" in arguments:

    # If the option "--qdk" is passed, attempt to use the noise model
    # "qb-qdk1" from the Qristal Emulator (must be installed).
    nm_name = "qb-qdk1" if "--qdk" in arguments else "default"

    # Create a noise model with 2 qubits.
    nm = qristal.core.NoiseModel(nm_name, my_sim.qn[0][0])

    # If the option "--noisier" is passed, overwrite the readout errors
    # on the first bit of the model with some very large values (for the sake of example).
    if "--noisier" in arguments:
      ro_error = qristal.core.ReadoutError()
      ro_error.p_01 = 0.5
      ro_error.p_10 = 0.5
      nm.set_qubit_readout_error(0, ro_error)

    # Hand over the noise model to the session.  Note that if this call
    # is not made, the default model will automatically get created
    # with the correct number of qubits and used.
    my_sim.noise_model = nm

  # Hit it.
  my_sim.run()

  # Lookee
  print(my_sim.results[0][0])

#Actual program launched on invocation
main(sys.argv)
