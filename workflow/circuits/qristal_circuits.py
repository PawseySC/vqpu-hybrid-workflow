    
'''
@brief some circuits
'''

from typing import List, Dict, Any

def _printtostr(thingtoprint: Any) -> str:
    from io import StringIO
    f = StringIO()
    print(thingtoprint, file=f)
    result = f.getvalue()
    f.close()
    return result 

def parse_qb_result(input : Any) -> Dict[str, int]:
    results = dict()
    outstr = _printtostr(input)
    lines = outstr.strip().split('\n')
    for l in lines:
        w = l.split(': ')
        results[w[0]] = int(w[1])
    return results

def simulator_setup(remote : str, arguments: str = ''):
    import qristal.core
    # Create a quantum computing session using Qristal
    my_sim = qristal.core.session()

    # Set up meaningful defaults for session parameters
    my_sim.init()
    # 2 qubits
    my_sim.qn = 2
    if '--nqubits=' in arguments:
        my_sim.qn = int(arguments.split('--nqubits=')[1].split(' ')[0])

    # Aer simulator selected
    my_sim.acc = "loopback"
    my_sim.remote_backend_database_path = remote

    # Set this to true to include noise
    my_sim.noise = True

    return my_sim

def noisy_circuit(remote : str, arguments : str) -> Dict[str, int]:
    import qristal.core
    my_sim = simulator_setup(remote, arguments)

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
    results = parse_qb_result(my_sim.results[0][0])
    # now return the dictionary of bit strings and counts 
    return results
