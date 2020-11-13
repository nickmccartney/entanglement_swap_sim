import numpy as np
import netsquid as ns
from netsquid.protocols import NodeProtocol, Signals
from netsquid.components import QuantumProgram, instructions as instr

__all__ = [
    "RepeaterProtocol",
    "MemoryRouting",
    "MemoryAccess",
]

class RepeaterProtocol(NodeProtocol):
    """Logic of "Repeater" node

    * Identifies when qubit pair is availble
    * Performs Bell State Measurement on pair

    Parameters
    ----------
    node : :py:class:`~netsquid.nodes.node.Node`
        Node to function as central repeater
    mem_config : dict
        mem_config['probability_detection']
        mem_config['reset_period_cycles']
        mem_config['reset_duration_cycles']
            Parameter assignments allowing for simplified configuration, passed on to proper protocols
    name : str
        Configuration of unique name for RepeaterProtocol instance

    Subprotocols
    ------------
    route_node_A : class:'ManageRouting'
        Manages routing qubits incoming from node_A to available memory slot
    route_node_B : class:'ManageRouting'
        Manages routing qubits incoming from node_B to available memory slot

    """
    def __init__(self, node, mem_config, name=None):
        super().__init__(node, name=name)
        self.port_names = list(dict(self.node.ports).keys())                                                            # ports to end nodes generated in "setup_network"
        self.slots_A = self.node.qmemory.get_matching_positions(
            'origin', value='node_A')                                                                                   # slots designated for storing qubits from node_B
        self.slots_B = self.node.qmemory.get_matching_positions(
            'origin', value='node_B')                                                                                   # slots designated for storing qubits from node_B
        self._add_subprotocols(node,
                               self.port_names[0],
                               self.slots_A,
                               self.port_names[1],
                               self.slots_B,
                               mem_config)

        self.use_memory = mem_config.pop('use_memory')                                                                  # flag configured by sim_params
        self._bsm_results = [(0, 0), (0, 1), (1, 0), (1, 1)]                                                            # Bell state measurement results, indexed by output of BellStateMeasurement QProgram

    def _add_subprotocols(self, node, port_name_A, slots_A, port_name_B, slots_B, mem_config):
        self.add_subprotocol(MemoryRouting(node, port_name_A, slots_A, mem_config, 'route_node_A'))
        self.add_subprotocol(MemoryRouting(node, port_name_B, slots_B, mem_config, 'route_node_B'))

    def run(self):
        self.node.subcomponents["Clock_{}".format(self.node.name)].start()
        if self.use_memory is False:
            yield from self._run_no_mem()                                                                               # switch to run no memory logic if not supposed to use memory (zero memory slot case)
        else:
            self.start_subprotocols()
            while True:
                yield self.await_signal(self.subprotocols['route_node_A'], "STORED") | \
                      self.await_signal(self.subprotocols['route_node_B'], "STORED")                                    # await output from either subprotocol indicating that an incoming qubit was stored in memory

                slot_A = None                                                                                           # will take the value of the first FILLED slot in slots_A
                slot_B = None                                                                                           # will take the value of the first FILLED slot in slots_B
                for slot in self.slots_A:
                    status = self.node.qmemory.mem_positions[slot].properties['status']
                    if status == "FILLED":
                        slot_A = slot                                                                                   # store index of first FILLED slot
                        break
                for slot in self.slots_B:
                    status = self.node.qmemory.mem_positions[slot].properties['status']
                    if status == "FILLED":
                        slot_B = slot                                                                                   # store index of first FILLED slot
                        break

                if slot_A and slot_B:                                                                                   # if both changed from None to FILLED
                    # FIXME: Initial start towards reimplementing actual BSM
                    # prog = BellMeasurementProgram()
                    # if self.node.qmemory.busy:
                    #     yield self.await_program(self.node.qmemory)
                    # self.node.qmemory.execute_program(prog, qubit_mapping=[slot_A, slot_B])
                    # yield self.await_program(self.node.qmemory)
                    # idx, = prog.output['BellStateIndex']
                    # print(f"AFTER MEASURE : {self._bsm_results[idx]}")
                    q1, = self.node.qmemory.pop(slot_A)                                                                 # retreive current qubit at slot_A
                    q2, = self.node.qmemory.pop(slot_B)                                                                 # retreive current qubit at slot_A
                    result = {
                        'qubits': [q1, q2]
                    }
                    self.send_signal(Signals.SUCCESS, result=result)


                    self.node.qmemory.mem_positions[slot_A].properties['status'] = "RESET"                              # flag slot for RESET after counted for measurement
                    self.node.qmemory.mem_positions[slot_B].properties['status'] = "RESET"                              # flag slot for RESET after counted for measurement


    def _run_no_mem(self):                                                                                              # special run case for when memory should not store qubit for any measurable time
        while True:
            expr = yield self.await_port_input(self.node.ports[self.port_names[0]]) | \
                         self.await_port_input(self.node.ports[self.port_names[1]])                                     # wait until qubits arrive at same time

            q1, = self.node.qmemory.pop(0)                                                                              # retreive anything that was just routed to slot 0
            q2, = self.node.qmemory.pop(1)                                                                              # retreive anything that was just routed to slot 0

            if q1 and q2:                                                                                               # if both slots had qubits routed to them, indicates joint arrival
                result = {
                    'qubits': [q1, q2]
                }
                self.send_signal(Signals.SUCCESS, result=result)


class MemoryRouting(NodeProtocol):
    """Subprotocol of "RepeaterProtocol": manages routing incoming qubits

    * Determines location of available slot (if any)
    * Places qubit into memory slot
    * Emits "SUCCESS" signal along with newly allocated index number

    Parameters
    ----------
    node : :py:class:'~netsquid.nodes.node.Node'
        node functioning as central repeater
    port_name : :py:class: str
        specify appropriate input port
    slots : list
        list of accessible slots for this connection
    mem_config : dict
        mem_config['probability_detection']
            used by this protocol to simulate detector error
        mem_config['reset_period_cycles']
        mem_config['reset_duration_cycles']
            Parameter assignments allowing for simplified configuration, passed on to proper protocols
    name : :py:class: str
        specify name of this subprotocol

    """

    def __init__(self, node, port_name, slots, mem_config, name):
        super().__init__(node, name=name)
        self.probability_detection = mem_config['probability_detection']                                                # probability that qubit is detected after interacting with memory

        self.input_port = port_name


        self.node_name = self.name.replace('route_','')
        if self.node_name == 'node_A':
            self.storage_port = 'qin0'                                                                                  # port initially receiving qubits from node_A
            self.storage_idx = 0                                                                                        # slot that input is routed to, not actually accessible for measurement, only temporary storage for QProgram
        elif self.node_name == 'node_B':
            self.storage_port = 'qin1'                                                                                  # port initially receiving qubits from node_B
            self.storage_idx = 1                                                                                        # slot that input is routed to, not actually accessible for measurement, only temporary storage for QProgram

        self.slots = slots                                                                                              # accessible slots for this connection

        self.signal = "STORED"
        self.add_signal(self.signal)

        self._add_subprotocols(node, slots, mem_config)

    def _add_subprotocols(self, node, slots, mem_config):
        self.add_subprotocol(MemoryAccess(node, slots, mem_config, name='access_'+self.node_name))

    def _get_target_slot(self):
        for slot in self.slots:
            status = self.node.qmemory.mem_positions[slot].properties['status']
            if status == "TARGET":
                return slot                                                                                             # retrieve current TARGET slot
        return None                                                                                                     # if no slots have that status, return NONE, unable to store qubit


    def run(self):
        self.start_subprotocols()
        while True:
            yield self.await_port_input(self.node.ports[self.input_port])                                               # await incoming qubit

            target_slot = self._get_target_slot()                                                                       # determine up to date TARGET assignment

            if self.node.qmemory.peek(self.storage_idx) is not None:                                                    # verify that a qubit was actually input on the port
                if target_slot != None:                                                                                 # verify that a TARGET slot is assigned
                    detected = True if np.random.random_integers(1,100) <= self.probability_detection else False        # determine by random sampling if the detector will successful sense qubit after interacting with NiV memory
                    prog = MemoryBehavior()                                                                             # intitialize program to perform realisitc storage of incoming qubit state
                    prog.set_detected(detected)                                                                         # indicate if storage will result in known FILLED slot
                    if self.node.qmemory.busy:
                        yield self.await_program(self.node.qmemory)
                    self.node.qmemory.execute_program(prog, qubit_mapping=[self.storage_idx, target_slot])
                    yield self.await_program(self.node.qmemory)
                    self.node.qmemory.pop(self.storage_idx)                                                             # clear out temporary bin for input qubits (unecessary as input would overwrite)
                    if detected:                                                                                        # indicate slot as FILLED if photon detected
                        self.node.qmemory.mem_positions[target_slot].properties['status'] = "FILLED"
                        self.send_signal(self.signal, result=target_slot)



class MemoryAccess(NodeProtocol):
    """Subprotocol of "RepeaterProtocol": manages reset and status assignments of slots

    * Times the reset period and duration for each slot
    * Assigns new TARGET slot when none is assigned

    Parameters
    ----------
    node : :py:class:'~netsquid.nodes.node.Node'
        node functioning as central repeater
    slots : list
        list of accessible slots for this connection
    mem_config : dict
        mem_config['reset_period_cycles']
            Period of reset timing for each slot, measured in number of clock cycles
        mem_config['reset_duration_cycles']
            Duration of reset status for each slot, measured in number of clock cycles
    name : :py:class: str
        specify name of this subprotocol

    """

    def __init__(self, node, slots, mem_config, name):
        super().__init__(node, name=name)
        self.slots = slots                                                                                              # accessible slots corresponding to this port
        self.reset_period_cycles = mem_config['reset_period_cycles']                                                    # number of periods to wait until forcing current index to reset
        self.reset_duration_cycles = mem_config['reset_duration_cycles']                                                # number of periods to force slot inactive during reset phase

        self.reset_timer = dict((slot, self.reset_duration_cycles) for idx, slot in enumerate(slots))                   # dict of reset timers for each individual slot, indexed using valid memory slot from "slots"
        self.reset_trigger_timer = dict((slot, self.reset_period_cycles) for idx, slot in enumerate(slots))             # dict of countdowns towards reset for the slot that is targeted for storage

    def _reset_state(self, slot):
        q_default, = ns.qubits.create_qubits(1, no_state=True)
        ns.qubits.assign_qstate([q_default], ns.h0)                                                                     # create initial default known state to reinitialize slot
        self.node.qmemory.put(q_default, positions=[slot])                                                              # reset qubit to known state
        self.node.qmemory.mem_positions[slot].properties['status'] = "IDLE"                                             # assign slot IDLE status, available for use

    def _get_new_target(self):                                                                                          # find slot to assign as TARGET
        for slot in self.slots:                                                                                         # check if there is existing target
            status = self.node.qmemory.mem_positions[slot].properties['status']
            if status == "TARGET":
                return

        for slot in self.slots:                                                                                         # check if there is was no TARGET, try to assign first IDLE slot as TARGET
            status = self.node.qmemory.mem_positions[slot].properties['status']
            if status == "IDLE":
                self.node.qmemory.mem_positions[slot].properties['status'] = "TARGET"                                   # assign new target slot
                return

    def run(self):
        target_slot = self.slots[0]
        self.node.qmemory.mem_positions[target_slot].properties['status'] = "TARGET"                                    # indicates to "MemoryRouting" protocol which slot to attempt to store on

        while True:

            yield self.await_port_output(self.node.subcomponents["Clock_{}".format(self.node.name)].ports['cout'])

            for slot in self.slots:
                status = self.node.qmemory.mem_positions[slot].properties['status']
                if status == "RESET":
                    if self.reset_timer[slot] == 0:
                        self._reset_state(slot)                                                                         # call function to handle reset to default "known" qubit state and IDLE status
                        self.reset_timer[slot] = self.reset_duration_cycles                                             # restore reset timer for next time
                    else:
                        self.reset_timer[slot] -= 1
                elif status == "TARGET":
                    if self.reset_trigger_timer[slot] == 0:
                        self.node.qmemory.mem_positions[slot].properties['status'] = "RESET"                            # flag slot as RESET
                        self.reset_trigger_timer[slot] = self.reset_period_cycles                                       # reset timer for next occurrence
                    else:
                        self.reset_trigger_timer[slot] -= 1

                elif status == "IDLE":
                    if self.reset_trigger_timer[slot] == 0:
                        self.node.qmemory.mem_positions[slot].properties['status'] = "RESET"                            # flag slot as RESET
                        self.reset_trigger_timer[slot] = self.reset_period_cycles                                       # reset timer for next occurrence
                    else:
                        self.reset_trigger_timer[slot] -= 1

                elif status == "FILLED":
                    if self.reset_trigger_timer[slot] == 0:
                        self.node.qmemory.mem_positions[slot].properties['status'] = "RESET"                            # flag slot as RESET
                        self.reset_trigger_timer[slot] = self.reset_period_cycles                                       # reset timer for next occurrence
                    else:
                        self.reset_trigger_timer[slot] -= 1

                self._get_new_target()                                                                                  # call function to handle TARGET assignment


class MemoryBehavior(QuantumProgram):
    """Program to model physical interaction of photon passing state to NiV memory unit.

    Results in input qubit state being passed to a quantum memory subject to decoherence

    """
    default_num_qubits = 2

    def set_detected(self, detected):
        self._detected = detected

    def program(self, **kwargs):
        q0,q1 = self.get_qubit_indices(2)
        self.apply(instr.INSTR_CZ, [q0,q1])                                                                             # perform CPHASE or conditional Z gate, control = photon
        self.apply(instr.INSTR_MEASURE_X, [q0], output_key='measure_X')                                                 # perform X basis measurement on photon, with result accessible by 'measure_X' output key
        yield self.run()

        if self._detected:                                                                                              # ignore these steps if we fail to recognize input
            if self.output['measure_X'] == 1:
                self.apply(instr.INSTR_H, [q1])                                                                         # perform Hadamard gate on memory
                self.apply(instr.INSTR_Z, [q1])                                                                         # perform Z gate conditional on X measurement on memory
            else:
                self.apply(instr.INSTR_H, [q1])                                                                         # perform Hadamard gate on memory
            yield self.run()


class BellMeasurementProgram(QuantumProgram):
    """Program to perform a Bell measurement on two qubits.

    Measurement results are stored in output key "BellStateIndex""

    """
    default_num_qubits = 2

    def program(self):
        q1, q2 = self.get_qubit_indices(2)
        self.apply(instr.INSTR_MEASURE_BELL, [q1, q2],
                   inplace=True,
                   output_key='BellStateIndex')                                                                         # use built in BSM instruction, output is an index associated with each possible measurement result
        yield self.run()