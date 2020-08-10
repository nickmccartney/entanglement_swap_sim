import pandas
import netsquid as ns
import pydynaa as pd
from netsquid.components import SourceStatus, GaussianDelayModel
from netsquid.components import QuantumChannel, FibreLossModel
from netsquid.components.models import QuantumErrorModel
from netsquid.components import instructions as instr
from netsquid.components import QuantumProcessor, QuantumProgram, DephaseNoiseModel, DepolarNoiseModel, T1T2NoiseModel, PhysicalInstruction
from netsquid.nodes import Network
from netsquid.protocols import LocalProtocol, NodeProtocol, Signals
from netsquid.qubits import StateSampler, QFormalism, ketstates as ks
from netsquid.qubits import qubitapi as qapi, operators as ops
from netsquid.util.datacollector import DataCollector

from qsource import QSource                                                                                             # use localy modified version of QSource


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
        self.port_names = ["conn|R<-A|", "conn|R<-B|"]                                                                  # Ports to end nodes generated in "setup_network"
        self._add_subprotocols(node,
                               self.port_names[0],
                               self._get_mem_slots("A"),
                               self.port_names[1],
                               self._get_mem_slots("B"))

    def _add_subprotocols(self, node, port_A, slots_A, port_B, slots_B):
        self.add_subprotocol(RouteQubits(node, port_A, slots_A, name="route_node_A"))
        self.add_subprotocol(RouteQubits(node, port_B, slots_B, name="route_node_B"))

    def _get_mem_slots(self, side):                                                                                     # allocate memory equally between each incoming port
        capacity = len(self.node.qmemory.mem_positions)
        if side == "A":
            mem_slots = list(range(0, int(capacity/2)))
            return mem_slots
        elif side == "B":
            mem_slots = list(range(int(capacity/2), capacity))
            return mem_slots

    def run(self):
        if self.use_memory is False:
            yield from self._run_no_mem()
        else:
            self.start_subprotocols()
            while True:
                self.result = {"meas": None}
                expr = yield self.await_signal(self.subprotocols["route_node_A"], Signals.SUCCESS) | \
                             self.await_signal(self.subprotocols["route_node_B"], Signals.SUCCESS)                      # wait for incoming qubit(s) to be successfully stored

                if expr.first_term.value:                                                                               # update used memory slots with result from either signal
                    mem_pos = self.subprotocols["route_node_A"].get_signal_result(label=Signals.SUCCESS, receiver=self)
                    self.qubits_A.append(mem_pos)
                if expr.second_term.value:
                    mem_pos = self.subprotocols["route_node_B"].get_signal_result(label=Signals.SUCCESS, receiver=self)
                    self.qubits_B.append(mem_pos)


                while (len(self.qubits_A) > 0) and (len(self.qubits_B) > 0):                                            # both qubits from node_A and node_B are available in qmemory
                    arr = list(range(0, len(self.node.qmemory.mem_positions)))
                    print(self.node.qmemory.peek()[0].qstate.dm)
                    print(self.node.qmemory.delta_time(positions=arr))                                                  # FIXME: For analysis only
                    measure_program = BellMeasurementProgram()
                    self.node.qmemory.execute_program(measure_program, [self.qubits_A[-1], self.qubits_B[-1]])
                    yield self.await_program(self.node.qmemory)
                    m, = measure_program.output["BellStateIndex"]
                    m0, m1 = self._bsm_results[m]
                    self.qubits_A.pop()
                    self.qubits_B.pop()
                    self.result = {"meas": [m0,m1]}
                    self.send_signal(Signals.SUCCESS,result=self.result)                                                # FIXME: Could this cause issues if keeping track of elapsed time is important?

    def _run_no_mem(self):                                                                                              # special run case for when memory should not store qubit for any measurable time
        while True:
            expr = yield self.await_port_input(self.node.ports[self.port_names[0]]) & \
                         self.await_port_input(self.node.ports[self.port_names[1]])                                     # wait until qubits arrive at same time FIXME: possibly give some lenience
            if expr.first_term.value & expr.second_term.value:
                rx1 = self.node.ports[self.port_names[0]].rx_input()
                q1 = rx1.items[0]
                rx2 = self.node.ports[self.port_names[1]].rx_input()
                q2 = rx2.items[0]
                self.node.qmemory.put([q1,q2], positions=[0,1])
                measure_program = BellMeasurementProgram()
                self.node.qmemory.execute_program(measure_program, [0, 1])
                yield self.await_program(self.node.qmemory)
                m, = measure_program.output["BellStateIndex"]
                m0, m1 = self._bsm_results[m]
                self.result = {"meas": [m0, m1]}
                self.send_signal(Signals.SUCCESS, result=self.result)                                                   # FIXME: Could this cause issues if keeping track of elapsed time is important?


class BellMeasurementProgram(QuantumProgram):
    """Program to perform a Bell measurement on two qubits.

    Measurement results are stored in output key "BellStateIndex""

    """
    default_num_qubits = 2

    def program(self):
        q1, q2 = self.get_qubit_indices(2)
        self.apply(instr.INSTR_MEASURE_BELL, [q1, q2],
                   inplace=False,
                   output_key="BellStateIndex")
        yield self.run()


class RouteQubits(NodeProtocol):
    """Subprotocol of "RepeaterProtocol": manages routing incoming qubits no memory

    * Determines location of available slot (if any)
    * Places qubit into memory slot
    * Emits "SUCCESS" signal along with newly allocated index number

    Parameters
    ----------
    node : :py:class:'~netsquid.nodes.node.Node'
        node functioning as central repeater
    port : :py:class: str
        specify appropriate input port
    mem_slots : :py:class: list[int]
        specify corresponding memory slots available
    name : :py:class: str
        specify name of subprotocol

    """
    def __init__(self, node, port, mem_slots, name):
        name = name
        super().__init__(node, name=name)
        self.port = self.node.ports[port]
        self.slots = mem_slots

    def _get_unused_slot(self, arr):                                                                                    # check for first available memory slot
        for i in arr:
            if not self.node.qmemory.get_position_used(i):
                return i
        return None

    def run(self):
        while True:
            yield self.await_port_input(self.port)                                                                      # wait for incoming qubit
            message = self.port.rx_input()
            qubit = message.items[0]
            mem_pos = self._get_unused_slot(self.slots)
            if mem_pos is not None:
                self.node.qmemory.put(qubit, positions=mem_pos)
                self.send_signal(Signals.SUCCESS, result=mem_pos)


class SourceProtocol(NodeProtocol):
    """Logic to track source emission

    Parameters
    ----------
    node : :py:class:`~netsquid.nodes.node.Node`
        Node to track source emission of
    name : str
        Assign unique name to distinguish SourceProtocol instances

    """
    def __init__(self, node, name):
        super().__init__(node=node, name=name)


    def run(self):
        while True:
            yield self.await_port_output(self.node.subcomponents['QSource_{}'.format(self.node.name)].ports['qout0'])
            self.send_signal(Signals.SUCCESS, result=ns.sim_time())


class SimulationProtocol(LocalProtocol):
    """Logic of simulation

    * Manages all node protocols, created for further extensions to data collection/analysis

    Parameters
    ----------
    node_A : :py:class:`~netsquid.nodes.node.Node`
        Node to function as source_A
    node_B : :py:class:`~netsquid.nodes.node.Node`
        Node to function as source_B
    node_R : :py:class:`~netsquid.nodes.node.Node`
        Node to function as central repeater
    use_memory : bool
        Allows for switching to _run_no_memory case in subprotocol 'repeater_R'

    Subprotocols
    ------------
    source__A : class:'SourceProtocol'
        Allows for tracking of source emission on node_A
`   source__B : class:'SourceProtocol'
        Allows for tracking of source emission on node_B
    repeater_R : class:'RepeaterProtocol'
        Manages identification of qubit pairs and measurement reporting

    """

    def __init__(self, node_A, node_B, node_R, use_memory):
        super().__init__(nodes={"A": node_A, "B": node_B, "R": node_R}, name="Simulation Protocol")
        self._add_subprotocols(node_A,node_B,node_R, use_memory)

    def _add_subprotocols(self, node_A, node_B, node_R, use_memory):
        self.add_subprotocol(SourceProtocol(node_A, name="source_A"))
        self.add_subprotocol(SourceProtocol(node_B, name="source_B"))
        self.add_subprotocol(RepeaterProtocol(node_R, use_memory, name="repeater_R"))

    def run(self):
        self.start_subprotocols()
        while True:
            yield self.await_signal(self.subprotocols["repeater_R"], Signals.SUCCESS)
            result = self.subprotocols["repeater_R"].get_signal_result(label=Signals.SUCCESS, receiver=self)
            self.send_signal(Signals.SUCCESS, result=result)


# class FreeSpaceErrorModel(QuantumErrorModel):                                                                           # FIXME: In development
#     def __init__(self, aperature_tx, aperature_rx, length, **kwargs):
#         super().__init__(**kwargs)
#         self._properties.update({'aperature_rx': aperature_rx, 'aperature_tx': aperature_tx, 'length': length})
#
#     @property
#     def aperature_rx(self):
#         return self._properties['aperature_rx']
#
#     @aperature_rx.setter
#     def aperature_rx(self, value):
#         self._properties['aperature_rx'] = value
#
#     @property
#     def aperature_tx(self):
#         return self._properties['aperature_tx']
#
#     @aperature_tx.setter
#     def aperature_tx(self, value):
#         self._properties['aperature_tx'] = value
#
#     @property
#     def length(self):
#         return self._properties['length']
#
#     @length.setter
#     def length(self, value):
#         self._properties['length'] = value



def setup_network(channel_model, channel_length, memory_model, memory_depth,
                  physical_instructions=None, source_model=None):

    network = Network("Entanglement_swap")
    node_a, node_b, node_r = network.add_nodes(["node_A", "node_B", "node_R"])

    state_sampler = StateSampler(qs_reprs=[ns.y0], probabilities=[1.0], formalism=QFormalism.DM)                                     # FIXME: Need KET or DM? DM better for noise supposedly

    # Setup end node A:                                                                                                 # for future versions can add qmemory/qproc here too!
    source_a = QSource(name="QSource_node_A",
                       state_sampler=state_sampler,
                       num_ports=1,
                       status=SourceStatus.INTERNAL,
                       timing_model=source_model,
                       output_meta={"qm_replace": False})                                                               # failsafe, preventing qubits in memory from being replaced by newer ones
    node_a.add_subcomponent(source_a)

    # Setup end node B:                                                                                                 # for future versions can add qmemory/qproc here too!
    source_b = QSource(name="QSource_node_B",
                       state_sampler=state_sampler,
                       num_ports=1,
                       status=SourceStatus.INTERNAL,
                       timing_model=source_model,
                       output_meta={"qm_replace": False})                                                               # failsafe, preventing qubits in memory from being replaced by newer ones
    node_b.add_subcomponent(source_b)

    # Setup midpoint repeater node R:
    if memory_depth == 0:                                                                                                   # special case, initialize nonphysical processor to simply handle measurement on simultaneous input
        qprocessor_r = QuantumProcessor(name="QProcessor_R",
                                  num_positions=2,
                                  fallback_to_nonphysical=True,
                                  phys_instructions=None,
                                  memory_noise_models=None)
    else:
        qprocessor_r = QuantumProcessor(name="QProcessor_R",
                                  num_positions=memory_depth*2,
                                  fallback_to_nonphysical=True,
                                  phys_instructions=physical_instructions,
                                  memory_noise_models=memory_model)
    node_r.add_subcomponent(qprocessor_r)

    # Setup quantum channels:
    qchannel_ar = QuantumChannel(name="QChannel_A->R",
                                 length=channel_length,
                                 models=channel_model)
    port_name_a, port_name_ra = network.add_connection(node_a, node_r, channel_to=qchannel_ar, label="quantum",
                                                       port_name_node1="conn|A->R|", port_name_node2="conn|R<-A|")
    qchannel_br = QuantumChannel(name="QChannel_B->R",
                                 length=channel_length,
                                 models=channel_model)
    port_name_b, port_name_rb = network.add_connection(node_b, node_r, channel_to=qchannel_br, label="quantum",
                                                       port_name_node1="conn|B->R|", port_name_node2="conn|R<-B|")
    # Setup Alice ports:
    node_a.subcomponents["QSource_node_A"].ports["qout0"].forward_output(node_a.ports[port_name_a])

    # Setup Bob ports:
    node_b.subcomponents["QSource_node_B"].ports["qout0"].forward_output(node_b.ports[port_name_b])

    # Realize setup of Repeater ports unnecessary, routing on IO ports handled in RepeaterProtocol
    return network


def sim_setup(network, memory_depth):
    simulation = SimulationProtocol(network.get_node("node_A"),
                                    network.get_node("node_B"),
                                    network.get_node("node_R"),
                                    memory_depth)

    def record_run(evexpr):
        # Record run
        protocol = evexpr.triggered_events[-1].source
        result = protocol.get_signal_result(Signals.SUCCESS)
        return result

    dc = DataCollector(record_run, include_time_stamp=False, include_entity_name=False)
    dc.collect_on(pd.EventExpression(source=simulation, event_type=Signals.SUCCESS.value))

    return simulation, dc


def run_simulation(duration, memory_depths):
    simulation_data = pandas.DataFrame()
    for memory_depth in memory_depths:
        ns.set_qstate_formalism(QFormalism.DM)                                                                          # set formalism to ensure noise/error is calculated effectively

        frequency = 1e9                                                                                                 # frequency in [Hz]
        period = (1/frequency)*1e9                                                                                      # period in [ns]
        source_model = GaussianDelayModel(delay_mean=period, delay_std=0.0)                                             # model for emission_delay, std determines uncertainty/error
        channel_length = 50                                                                                             # single channel length in [km]
        channel_model = {"quantum_loss_model": FibreLossModel(p_loss_init=0.1, p_loss_length=0.005)}                   # FIXME: using arbitrary loss model to force losses in channel
        # channel_model = {"quantum_loss_model": DephaseNoiseModel(dephase_rate=0.5, time_independent=True)}              # FIXME: using arbitrary loss model to force losses in channel
        # channel_model = {"quantum_loss_model": DepolarNoiseModel(depolar_rate=0.3, time_independent=True)}              # FIXME: using arbitrary loss model to force losses in channel

        # memory_model = DephaseNoiseModel(dephase_rate=0.2, time_independent=True)                                             # FIXME: using arbitrary noise model to apply noise to stored qubits
        memory_model = T1T2NoiseModel(T1=10, T2=8)
        # phys_instructions = [PhysicalInstruction(instr.INSTR_MEASURE_BELL, duration=5.0, parallel=True)]

        network = setup_network(channel_model=channel_model, channel_length=channel_length,
                                memory_model=memory_model, memory_depth=memory_depth, source_model=source_model)        # FIXME: Noise not actualy impacting qubits

        if memory_depth == 0:
            use_memory = False
        else:
            use_memory = True
        entangle_sim, dc = sim_setup(network, use_memory)
        entangle_sim.start()
        stats = ns.sim_run(duration=duration)
        if dc.dataframe.empty:
            df = pandas.DataFrame({"num_meas" : [0], "mem_depth" : [memory_depth]})
        else:
            num_meas = len(dc.dataframe["meas"])
            df = pandas.DataFrame({"num_meas": [num_meas], "mem_depth": [memory_depth]})
        simulation_data = simulation_data.append(df)
        ns.sim_reset()                                                                                                  # FIXME: Critical component, should it be a full reset though?
    return simulation_data


def repeat_simulation(iterations, duration, memory_depths):
    total_data = pandas.DataFrame()
    for i in range(iterations):
        simulation_data = run_simulation(duration, memory_depths)
        iteration_data = simulation_data.groupby("mem_depth")['num_meas'].agg(num_meas='mean').reset_index()
        print(f"ON ITERATION {i} MEASURED: ")
        print(f"{iteration_data} \n")
        total_data = total_data.append(iteration_data)
    return total_data


def create_plot():
    from matplotlib import pyplot as plt
    memory_depths = [0,1,2,3,4,5,6,7,8,9]
    iterations = 10
    duration = 100
    total_data = repeat_simulation(iterations, duration, memory_depths)
    data = total_data.groupby("mem_depth")['num_meas'].agg(num_meas='mean', sem='sem').reset_index()
    plt.errorbar('mem_depth', 'num_meas', yerr='sem', capsize=4, ecolor='k', fmt='bo-', markersize=4, data=data)
    plt.title('Number successful measurements vs memory depth')
    plt.xlabel('Memory Depth (per connection)')
    plt.ylabel('# Successful Measurements')
    plt.grid(True)
    plt.show()


create_plot()
ns.sim_reset()