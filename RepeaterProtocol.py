import numpy as np
import netsquid as ns
from netsquid.protocols import NodeProtocol, Signals
from netsquid.components import QuantumProgram, instructions as instr

__all__ = [
    "RepeaterProtocol",
    "ManageRouting",
]

class RepeaterProtocol(NodeProtocol):
    """Logic of "Repeater" node

    * Identifies when qubit pair is availble
    * Performs Bell State Measurement on pair

    Parameters
    ----------
    node : :py:class:`~netsquid.nodes.node.Node`
        Node to function as central repeater
    use_memory : bool
        Allow handling of no memory case
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
        self.use_memory = mem_config.pop('use_memory')
        self.mem_config = mem_config
        self.result = None
        self._bsm_results = [(0, 0), (0, 1), (1, 0), (1, 1)]                                                            # Bell state measurement results
        self.port_names = list(dict(self.node.ports).keys())                                                            # Ports to end nodes generated in "setup_network"
        self._add_subprotocols(node,
                               self.port_names[0],
                               self.port_names[1],
                               self.mem_config)

        self.slots_A = self.node.qmemory.get_matching_positions(
            'origin', value='node_A')
        self.slots_B = self.node.qmemory.get_matching_positions(
            'origin', value='node_B')

        self.qubits_A = dict((slot, False) for slot in self.slots_A)                                                    # FIXME: a bit weird implementation
        self.qubits_B = dict((slot, False) for slot in self.slots_B)

    def _add_subprotocols(self, node, port_name_A, port_name_B, mem_config):
        self.add_subprotocol(ManageMemory(node, port_name_A, mem_config, 'manage_slots_node_A'))
        self.add_subprotocol(ManageMemory(node, port_name_B, mem_config, 'manage_slots_node_B'))

    def run(self):
        self.node.subcomponents["Clock_{}".format(self.node.name)].start()
        if self.use_memory is False:
            yield from self._run_no_mem()
        else:
            self.start_subprotocols()
            while True:
                expr = yield self.await_signal(self.subprotocols['manage_slots_node_A'], "OPERATION") | \
                             self.await_signal(self.subprotocols['manage_slots_node_B'], "OPERATION")

                # manage instructions for memory A
                if expr.first_term.value:
                    operation = self.subprotocols['manage_slots_node_A'].get_signal_result("OPERATION", receiver=self)
                    if operation["op"] == "ADD":
                        self.qubits_A[operation["slot"]] = True
                    elif operation["op"] == "REMOVE":
                        self.qubits_A[operation["slot"]] = False

                # manage instruction for memory B
                else:
                    operation = self.subprotocols['manage_slots_node_B'].get_signal_result("OPERATION", receiver=self)
                    if operation["op"] == "ADD":
                        self.qubits_B[operation["slot"]] = True
                    elif operation["op"] == "REMOVE":
                        self.qubits_B[operation["slot"]] = False

                # FIXME: MUST BE BETTER WAY TO DO THIS PAIR  CHECKING
                for idx_A in self.slots_A:
                    if self.qubits_A[idx_A] is True:
                        for idx_B in self.slots_B:
                            if self.qubits_B[idx_B] is True:
                                result = {
                                     'slots': [idx_A, idx_B]
                                }
                                self.qubits_A[idx_A] = False
                                self.qubits_B[idx_B] = False
                                self.send_signal(Signals.SUCCESS, result=result)


    def _run_no_mem(self):                                                                                              # special run case for when memory should not store qubit for any measurable time
        while True:
            expr = yield self.await_port_input(self.node.ports[self.port_names[0]]) & \
                         self.await_port_input(self.node.ports[self.port_names[1]])                                     # wait until qubits arrive at same time FIXME: possibly give some lenience

            rx1 = self.node.ports[self.port_names[0]].rx_input()
            rx2 = self.node.ports[self.port_names[1]].rx_input()
            if rx1 is not None and rx2 is not None:
                if rx1.items[0] is not None and rx2.items[0] is not None:                                               # FIXME: FIGURE OUT WHY THIS IS NEEDED TO AVOID RX2 SENDING [None]
                    q1, = rx1.items
                    q2, = rx2.items
                    self.node.qmemory.put([q1,q2], positions=[0,1])
                    result = {
                        'slots': [0, 1]
                    }
                    self.send_signal(Signals.SUCCESS, result=result)


class ManageMemory(NodeProtocol):
    def __init__(self, node, port_name, mem_config, name):
        super().__init__(node, name=name)
        self.operation = "OPERATION"
        self.add_signal(self.operation)
        # determine which slots will be managed
        self.port_name = port_name
        self.slots = self.node.qmemory.get_matching_positions(
            'origin', value='{}'.format(self.name.replace('manage_slots_','')))
        self._add_subprotocols(node, mem_config)
        self.prob_detect = mem_config['prob_detection']

    def _add_subprotocols(self, node, mem_config):
        self.add_subprotocol(ManageRouting(node, self.port_name, self.slots, name='route'))
        self.add_subprotocol(MemoryAccess(node, self.slots, mem_config, name='access'))

    def run(self):
        self.start_subprotocols()
        while True:
            expr = yield self.await_signal(self.subprotocols['route'], "ADDED") | \
                         self.await_signal(self.subprotocols['access'], "REMOVED")

            if expr.first_term.value:
                if np.random.random_integers(1,100) <= self.prob_detect:                                                                         # FIXME: hacky attempt to force losses of counting qubits in memory
                    slot = self.subprotocols['route'].get_signal_result(label="ADDED", receiver=self)
                    operation = {"op": "ADD", "slot": slot}
                    self.send_signal(self.operation, operation)
            else:
                slot = self.subprotocols['access'].get_signal_result(label="REMOVED", receiver=self)
                operation = {"op": "REMOVE", "slot": slot}
                self.send_signal(self.operation, operation)



class MemoryAccess(NodeProtocol):

    def __init__(self, node, slots, mem_config, name):
        super().__init__(node, name=name)
        self.slots = slots
        self.slots_status = [True] * len(self.slots)
        self.reset_period_cycles = mem_config['reset_period_cycles']
        self.reset_duration_cycles = mem_config['reset_duration_cycles']
        self.slots_reset_timer = [self.reset_duration_cycles] * len(self.slots)

        self.removed_qubit = "REMOVED"
        self.add_signal(self.removed_qubit)

    def run(self):
        while True:
            yield self.await_port_output(self.node.subcomponents["Clock_{}".format(self.node.name)].ports['cout'])

            for idx, count in enumerate(self.slots_reset_timer):
                if self.slots_status[idx] is False:
                    self.slots_reset_timer[idx] -= 1
                    if self.slots_reset_timer[idx] == 0:
                        self.slots_status[idx] = True
                        self.slots_reset_timer[idx] = self.reset_duration_cycles
                        self.node.qmemory.mem_positions[self.slots[idx]].reset()
                        self.node.qmemory.set_position_used(False, idx)

            if self.node.subcomponents["Clock_{}".format(self.node.name)].num_ticks % self.reset_period_cycles == 0:
                for idx, status in enumerate(self.slots_status):
                    if status is True:
                        self.slots_status[idx] = False
                        self.node.qmemory.set_position_used(True, idx)
                        self.send_signal(self.removed_qubit, self.slots[idx])
                        break


class ManageRouting(NodeProtocol):
    """Subprotocol of "RepeaterProtocol": manages routing incoming qubits

    * Determines location of available slot (if any)
    * Places qubit into memory slot
    * Emits "SUCCESS" signal along with newly allocated index number

    Parameters
    ----------
    node : :py:class:'~netsquid.nodes.node.Node'
        node functioning as central repeater
    port : :py:class: str
        specify appropriate input port
    name : :py:class: str
        specify name of subprotocol

    """
    def __init__(self, node, port_name, slots, name):
        super().__init__(node, name=name)
        self.port = self.node.ports[port_name]
        self.slots = slots
        self.new_qubit = "ADDED"
        self.add_signal(self.new_qubit)

    def _get_unused_slot(self):                                                                                    # check for first available memory slot
        for slot in self.slots:
            if not self.node.qmemory.get_position_used(slot):
                return slot
        return None

    def run(self):
        while True:
            yield self.await_port_input(self.port)                                                           # wait for incoming qubit
            message = self.port.rx_input()
            qubit, = message.items
            mem_pos = self._get_unused_slot()
            if mem_pos is not None:
                self.node.qmemory.put(qubit, positions=mem_pos)
                self.send_signal(self.new_qubit, result=mem_pos)


class BellMeasurementProgram(QuantumProgram):
    """Program to perform a Bell measurement on two qubits.

    Measurement results are stored in output key "BellStateIndex""

    """
    default_num_qubits = 2

    def program(self):
        q1, q2 = self.get_qubit_indices(2)
        self.apply(instr.INSTR_MEASURE_BELL, [q1, q2],
                   inplace=True,
                   output_key='BellStateIndex')                                                                         # "inplace"-> True: program won't discard qubits, allow SimProtocol to inspect and then discard
        yield self.run()