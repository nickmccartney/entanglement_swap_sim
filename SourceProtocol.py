import numpy as np
import netsquid as ns
from netsquid.protocols import NodeProtocol, Signals

__all__ = [
    "SourceProtocol",
]

class SourceProtocol(NodeProtocol):
    """Logic to track source emission

    Parameters
    ----------
    node : :py:class:`~netsquid.nodes.node.Node`
        Node to track source emission of
    name : str
        Assign unique name to distinguish SourceProtocol instances

    """
    def __init__(self, node, source_config, name):
        super().__init__(node=node, name=name)
        self.probability_emission = source_config['probability_emission']

    def run(self):
        self.node.subcomponents['Clock_{}'.format(self.node.name)].start()
        while True:
            yield self.await_port_output(self.node.subcomponents['Clock_{}'.format(self.node.name)].ports['cout'])
            emitted = True if np.random.random_integers(1, 100) <= self.probability_emission else False
            if emitted:
                self.node.subcomponents['QSource_{}'.format(self.node.name)].trigger()
                self.send_signal(Signals.SUCCESS, result=ns.sim_time())
