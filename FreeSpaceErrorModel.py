import math
import numpy as np
from netsquid.qubits import qubitapi as qapi
from netsquid.components.models import QuantumErrorModel
from netsquid.util import simtools

__all__ = [
    "FreeSpaceErrorModel",
]

class FreeSpaceErrorModel(QuantumErrorModel):
    def __init__(self, length, static_loss_prob=0, damping_rate=0, rng=None):
        super().__init__()
        self._properties.update({'rng': rng, 'static_loss_prob': static_loss_prob, 'length': length,
                                 'damping_rate': damping_rate})


    @property
    def rng(self):
        """ :obj:`~numpy.random.RandomState`: Random number generator."""
        return self.properties['rng']

    @rng.setter
    def rng(self, value):
        if not isinstance(value, np.random.RandomState):
            raise TypeError("{} is not a valid numpy RandomState".format(value))
        self.properties['rng'] = value

    @property
    def static_loss_prob(self):
        return self._properties['static_loss_prob']

    @static_loss_prob.setter
    def static_loss_prob(self, value):
        self._properties['static_loss_prob'] = value

    @property
    def length(self):
        return self._properties['length']

    @length.setter
    def length(self, value):
        self._properties['length'] = value

    @property
    def damping_rate(self):
        return self._properties['damping_rate']

    @damping_rate.setter
    def damping_rate(self, value):
        self._properties['damping_rate'] = value

    @staticmethod
    def lose_qubit(qubits, qubit_index, prob_loss=1.0, rng=None):                                                       # override of "lose_qubit" method to consider both static qubit loss (using lose_qubit) and noise (using amplitude dampening)
        qubit = qubits[qubit_index]
        if rng is None:
            rng = simtools.get_random_state()
        if math.isclose(prob_loss, 1.0) or rng.random_sample() <= prob_loss:
            qapi.discard(qubit)
            qubits[qubit_index] = None

    def error_operation(self, qubits, delta_time=0, **kwargs):
        """Error operation to apply to qubits.

        Parameters
        ----------
        qubits : tuple of :obj:`~netsquid.qubits.qubit.Qubit`
            Qubits to apply noise to.
        delta_time : float, optional
            Time qubits have spent on a component [ns].

        """
        for idx, qubit in enumerate(qubits):
            if qubit is None:
                continue
            prob_loss = self.properties["static_loss_prob"]
            self.lose_qubit(qubits, idx, prob_loss, rng=self.properties["rng"])

        for idx, qubit in enumerate(qubits):
            if qubit is None:
                continue
            gamma = self.properties["damping_rate"] * self.properties["length"]                                  # FIXME: figure out reasonable function for this, behavior when gamma->1.0 ?
            qapi.amplitude_dampen(qubit, gamma=gamma)