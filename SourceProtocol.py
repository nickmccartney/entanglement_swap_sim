import numpy as np
import netsquid as ns
from netsquid.protocols import NodeProtocol, Signals

__all__ = [
    "SourceProtocol",
]

class SourceProtocol(NodeProtocol):
    """Logic to control probabilistic source emission

    Parameters
    ----------
    node : :py:class:`~netsquid.nodes.node.Node`
        Node for which this protocol controls emission
    source_config : dict
        source_config['probability_emission'] : float
            Parameter assignment allowing for simplified configuration
    name : str
        Assign unique name to distinguish SourceProtocol instances

    """
    def __init__(self, node, source_config, name):
        super().__init__(node=node, name=name)
        self.probability_emission = source_config['probability_emission']                                               # probability of actually triggering QSource emission when clock ticks

    def run(self):
        self.node.subcomponents['Clock_{}'.format(self.node.name)].start()                                              # clock for this particular node to time its source emission
        while True:
            yield self.await_port_output(self.node.subcomponents['Clock_{}'.format(self.node.name)].ports['cout'])
            emitted = True if np.random.random_integers(1, 100) <= self.probability_emission else False                 # determine if source should actually emit a qubit this cycle by random sampling [1,100]
            if emitted:
                self.node.subcomponents['QSource_{}'.format(self.node.name)].trigger()                                  # manually trigger source when "emitted" is True for this cycle
                self.send_signal(Signals.SUCCESS, result=ns.sim_time())
