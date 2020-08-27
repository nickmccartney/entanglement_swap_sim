from netsquid.protocols import NodeProtocol, Signals
from netsquid.components import QuantumProgram, instructions as instr

__all__ = [
    "RepeaterProtocol",
    "RouteQubits",
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
    route_node_A : class:'RouteQubits'
        Manages routing qubits incoming from node_A to available memory slot
    route_node_B : class:'RouteQubits'
        Manages routing qubits incoming from node_B to available memory slot

    """
    def __init__(self, node, use_memory=True, name=None):
        super().__init__(node, name=name)
        self.use_memory = use_memory
        self.result = None
        self.qubits_A = []                                                                                              # memory positions of stored qubits from A->R channel
        self.qubits_B = []                                                                                              # memory positions of stored qubits from B->R channel
        self._bsm_results = [(0, 0), (0, 1), (1, 0), (1, 1)]                                                            # Bell state measurement results
        self.port_names = list(dict(self.node.ports).keys())                                                            # Ports to end nodes generated in "setup_network"
        self._add_subprotocols(node,
                               self.port_names[0],
                               self.port_names[1])

    def _add_subprotocols(self, node, port_name_A, port_name_B):
        self.add_subprotocol(RouteQubits(node, port_name_A, name='route_node_A'))
        self.add_subprotocol(RouteQubits(node, port_name_B, name='route_node_B'))

    def run(self):
        if self.use_memory is False:
            yield from self._run_no_mem()
        else:
            self.start_subprotocols()
            while True:
                expr = yield self.await_signal(self.subprotocols['route_node_A'], Signals.SUCCESS) | \
                             self.await_signal(self.subprotocols['route_node_B'], Signals.SUCCESS)                      # wait for incoming qubit(s) to be successfully stored
                if expr.first_term.value:                                                                               # update used memory slots with result from either signal
                    mem_pos = self.subprotocols['route_node_A'].get_signal_result(
                        label=Signals.SUCCESS, receiver=self)
                    self.qubits_A.append(mem_pos)
                if expr.second_term.value:
                    mem_pos = self.subprotocols['route_node_B'].get_signal_result(
                        label=Signals.SUCCESS, receiver=self)
                    self.qubits_B.append(mem_pos)

                while (len(self.qubits_A) > 0) and (len(self.qubits_B) > 0):                                            # both qubits from node_A and node_B are available in qmemory
                    result = {
                         'slots': [self.qubits_A.pop(0), self.qubits_B.pop(0)]                                            # FIXME: FIFO vs FILO
                    }
                    self.send_signal(Signals.SUCCESS, result=result)
                    # measure_program = BellMeasurementProgram()
                    # self.node.qmemory.execute_program(measure_program, [self.qubits_A[-1], self.qubits_B[-1]])
                    # yield self.await_program(self.node.qmemory)
                    #
                    # m, = measure_program.output["BellStateIndex"]
                    # m0, m1 = self._bsm_results[m]
                    # measurement = [m0, m1]
                    # result = {
                    #     "meas": measurement,
                    # #     "slots": [self.qubits_A.pop(), self.qubits_B.pop()]
                    # # }
                    # self.send_signal(Signals.SUCCESS,result=result)                                                   # FIXME: Could this cause issues if keeping track of elapsed time is important?

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
                # measure_program = BellMeasurementProgram()
                # self.node.qmemory.execute_program(measure_program, [0, 1])
                # yield self.await_program(self.node.qmemory)
                # m, = measure_program.output["BellStateIndex"]
                # m0, m1 = self._bsm_results[m]
                # measurement = [m0, m1]
                # result = {
                #     "meas": measurement,
                #     "slots": [0,1]                                                                                    # "no_mem" always accessed slots [0,1] for use with qprocessor (non-physical to avoid qmemory error)
                # }
                # self.send_signal(Signals.SUCCESS, result=result)  # FIXME: Could this cause issues if keeping track of elapsed time is important?


class RouteQubits(NodeProtocol):
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
    def __init__(self, node, port_name, name):
        super().__init__(node, name=name)
        self.port = self.node.ports[port_name]

    def _get_unused_slot(self):                                                                                    # check for first available memory slot
        for position in self.node.qmemory.get_matching_positions(
                'origin', value='{}'.format(self.name.strip('route_'))):

            if not self.node.qmemory.get_position_used(position):
                return position
        return None

    def run(self):
        while True:
            yield self.await_port_input(self.port)                                                                      # wait for incoming qubit
            message = self.port.rx_input()
            qubit, = message.items
            mem_pos = self._get_unused_slot()
            if mem_pos is not None:
                self.node.qmemory.put(qubit, positions=mem_pos)
                self.send_signal(Signals.SUCCESS, result=mem_pos)


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