import netsquid as ns
from netsquid.protocols import LocalProtocol, Signals
from netsquid.qubits import qubitapi as qapi, ketstates as ks
from RepeaterProtocol import RepeaterProtocol
from SourceProtocol import SourceProtocol


__all__ = [
    "SimulationProtocol",
]
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
    source_config : dict
        source_config['probability_emission'] : float
            Parameter assignment allowing for simplified configuration
    mem_config : dict
        mem_config['probability_detection']
        mem_config['reset_period_cycles']
        mem_config['reset_duration_cycles']
            Parameter assignments allowing for simplified configuration, passed on to proper protocols

    Subprotocols
    ------------
    source__A : class:'SourceProtocol'
        Controls probabilistic emission of source in node_A
`   source__B : class:'SourceProtocol'
        Controls probabilistic emission of source in node_B
    repeater_R : class:'RepeaterProtocol'
        Manages identification of qubit pairs and measurement reporting

    """

    def __init__(self, node_A, node_B, node_R, source_config, memory_config):
        super().__init__(nodes={'A': node_A, 'B': node_B, 'R': node_R}, name='Simulation Protocol')
        self._add_subprotocols(node_A,
                               node_B,
                               node_R,
                               source_config,
                               memory_config)

    def _add_subprotocols(self, node_A, node_B, node_R, source_config, memory_config):
        self.add_subprotocol(SourceProtocol(node_A, source_config, name='source_A'))
        self.add_subprotocol(SourceProtocol(node_B, source_config, name='source_B'))
        self.add_subprotocol(RepeaterProtocol(node_R, memory_config, name='repeater_R'))

    def run(self):
        self.start_subprotocols()
        while True:
            yield self.await_signal(self.subprotocols['repeater_R'], Signals.SUCCESS)

            repeater_result = self.subprotocols['repeater_R'].get_signal_result(label=Signals.SUCCESS, receiver=self)
            q1,q2 = repeater_result['qubits']                                                                           # retrieve qubits that were popped from memory by RepeaterProtocol


            fid_joint = qapi.fidelity([q1,q2], ks.s11, squared=True)                                                    # measure joint fidelity (compared to emitted 's1' state) of qubits once they were marked for measurement
            result = {
                'pos_A': None,                                                                                          # FIXME: Useful for extra statistics to plot, would need to pass which slot was used for measurement
                'pos_B': None,                                                                                          # FIXME: Useful for extra statistics to plot, would need to pass which slot was used for measurement
                'fid_joint': fid_joint
            }
            self.send_signal(Signals.SUCCESS, result=result)
