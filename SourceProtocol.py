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
    def __init__(self, node, name):
        super().__init__(node=node, name=name)


    def run(self):
        self.node.subcomponents['Clock_{}'.format(self.node.name)].start()
        while True:
            yield self.await_port_output(self.node.subcomponents['QSource_{}'.format(self.node.name)].ports['qout0'])
            self.send_signal(Signals.SUCCESS, result=ns.sim_time())