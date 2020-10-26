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

    def __init__(self, node_A, node_B, node_R, mem_config):
        super().__init__(nodes={'A': node_A, 'B': node_B, 'R': node_R}, name='Simulation Protocol')
        self._add_subprotocols(node_A,node_B,node_R, mem_config)

    def _add_subprotocols(self, node_A, node_B, node_R, mem_config):
        self.add_subprotocol(SourceProtocol(node_A, name='source_A'))
        self.add_subprotocol(SourceProtocol(node_B, name='source_B'))
        self.add_subprotocol(RepeaterProtocol(node_R, mem_config, name='repeater_R'))

    def run(self):
        self.start_subprotocols()
        while True:
            yield self.await_signal(self.subprotocols['repeater_R'], Signals.SUCCESS)

            repeater_result = self.subprotocols['repeater_R'].get_signal_result(label=Signals.SUCCESS, receiver=self)
            slots = repeater_result['slots']
            # FIXME: ISSUE WITH q1, q2 coming in as None (likely due to being removed by mem access protocol (SHOULD THIS CONDITIONAL BE NEEDED?)
            q1,q2 = self.subprotocols['repeater_R'].node.qmemory.pop(slots)                                             # grab/remove qubits after measurement FIXME: Figure out actual interpretation of results
            if q1 is not None and q2 is not None:
                fid_q1 = qapi.fidelity(q1, ks.y0, squared=True)
                fid_q2 = qapi.fidelity(q2, ks.y0, squared=True)
                fid_joint = qapi.fidelity([q1,q2], ks.y00, squared=True)
                result = {
                    'fid_q1': fid_q1,
                    'pos_A': slots[0],
                    'fid_q2': fid_q2,
                    'pos_B': slots[1],
                    'fid_joint': fid_joint
                }
                self.send_signal(Signals.SUCCESS, result=result)
