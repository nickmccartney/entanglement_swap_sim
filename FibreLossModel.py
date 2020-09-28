from netsquid.components.models import QuantumErrorModel
import netsquid.util.simtools as simtools
from netsquid.util.simlog import warn_deprecated
import numpy as np

__all__ = [
    "FibreLossModel",
]

class FibreLossModel(QuantumErrorModel):
    """Model for exponential photon loss on fibre optic channels.

    Uses length of transmitting channel to sample an
    exponential loss probability.

    Parameters
    ----------
    loss_init : float, optional
        Initial probability of losing a photon once it enters a channel [dB].
        e.g. due to frequency conversion.
    p_loss_length : float, optional
        Photon survival probability per channel length [dB/km].
    rng : :obj:`~numpy.random.RandomState` or None, optional
        Random number generator to use. If ``None`` then
        :obj:`~netsquid.util.simtools.get_random_state` is used.

    """
    def __init__(self, loss_init=0.2, p_loss_length=0.25, rng=None):
        super().__init__()
        self.loss_init = loss_init
        self.p_loss_length = p_loss_length
        self.rng = rng if rng else simtools.get_random_state()
        self.required_properties = ["length"]

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
    def loss_init(self):
        """float: initial probability of losing a photon when it enters channel."""
        return self.properties['loss_init']

    @loss_init.setter
    def loss_init(self, value):
        if value < 0:                                                                                                   # FIXME: Modified to accomadate newly expected range in dB
            raise ValueError
        self.properties['loss_init'] = value

    @property
    def p_loss_length(self):
        """float: photon survival probability per channel length [dB/km]."""
        return self.properties['p_loss_length']

    @p_loss_length.setter
    def p_loss_length(self, value):
        if value < 0:
            raise ValueError
        self.properties['p_loss_length'] = value

    def error_operation(self, qubits, delta_time=0, **kwargs):
        """Error operation to apply to qubits.

        Parameters
        ----------
        qubits : tuple of :obj:`~netsquid.qubits.qubit.Qubit`
            Qubits to apply noise to.
        delta_time : float, optional
            Time qubits have spent on a component [ns].

        """
        if 'channel' in kwargs:
            warn_deprecated("channel parameter is deprecated. "
                            "Pass length parameter directly instead.",
                            key="FibreLossModel.compute_model.channel")
            kwargs['length'] = kwargs['channel'].properties["length"]
            del kwargs['channel']
        #self.apply_loss(qubits, delta_time, **kwargs)
        for idx, qubit in enumerate(qubits):
            if qubit is None:
                continue
            prob_success_init = np.power(10, - self.loss_init / 10)
            prob_success_len = np.power(10, - kwargs['length'] * self.p_loss_length / 10)
            prob_loss = 1 - (prob_success_init * prob_success_len)         # FIXME: Modifed to use dB on 'loss_init'
            print(f"Prob loss = {prob_loss}")
            self.lose_qubit(qubits, idx, prob_loss, rng=self.properties['rng'])

    def prob_item_lost(self, item, delta_time=0, **kwargs):
        # DEPRECATED
        return 1 - (1 - self.loss_init) * np.power(10, - kwargs['length'] * self.p_loss_length / 10)