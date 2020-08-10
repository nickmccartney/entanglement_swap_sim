# This file uses NumPy style docstrings: https://github.com/numpy/numpy/blob/master/doc/HOWTO_DOCUMENT.rst.txt

"""Quantum network source for one or more qubits. A quantum source is used to model
a physical entity in the network capable of producing single or multiple (entangled) qubits.

"""
from enum import IntEnum
import math
import netsquid as ns
import netsquid.qubits.qubitapi as qapi
from netsquid.qubits import ketstates as ks
from netsquid.qubits.state_sampler import StateSampler
from netsquid.components.models.qerrormodels import QuantumErrorModel
from netsquid.components.models.delaymodels import DelayModel, FixedDelayModel
from netsquid.components.component import Component, Message
from netsquid.components.clock import Clock
from netsquid.util.constrainedmap import nonnegative_constr
from netsquid.util.simlog import warn_deprecated
from pydynaa import EventType, EventHandler

__all__ = [
    "SourceStatus",
    "QSource",
]


class SourceStatus(IntEnum):
    """Status of a quantum source.

    Attributes
    ----------
    OFF : int
        Quantum source does not respond to triggers.
    INTERNAL : int
        Quantum source will generate pulses when triggered by its internal clock.
    EXTERNAL : int
        Quantum source will generate pulses when triggered via its input port.

    """
    OFF = 0
    INTERNAL = 1
    EXTERNAL = 2


class QSource(Component):
    """A component that generates qubits.

    The generated qubits share a specified quantum state.

    Parameters
    ----------
    name : str
        Name of source which is also used to label qubits.
    status : :obj:`~netsquid.components.qsource.SourceStatus`, optional
        The status with which to initialize the source. This status will also be (re)set when calling the reset method.
    state_sampler : :obj:`~netsquid.qubits.state_sampler.StateSampler`, optional
        Object that contains states of which this source will sample when generating pulses. By default the state ``|0>`` will be used.
    frequency : float, optional
        Frequency at which the source creates qubits when in internal mode and no ``timing_model`` is given [Hz].
        Default value is 100 Hz or taken from the ``timing_model``.
    timing_model : :obj:`~netsquid.components.models.delaymodels.DelayModel` or None, optional
        Model used to calculate the time between triggers when in internal mode (overwrites frequency).
        When no model is given a fixed delay of 1 / frequency is used.
    trigger_delay : float, optional
        Delay between receiving a trigger and starting emission [ns].
        Can be used to tune pulse timing with a detector.
    models : dict or None, optional
        Models associated with this component. Will first specify them by their
        type and them set them.
        See the Models section to see which (key, Model) pair it recognizes.
    num_ports : int, optional
        Number of ports over which the generated qubits are distributed.
        The number of qubits in the state_sampler should be a multiple of the number of ports.
    qubit_port_mapping : list of int or None, optional
        Mapping of generated qubits to port numbers. When not None, the list should have the length of the number of
        qubits in the state_sampler. By default the qubits are equally distributed over the ports.
    output_meta : dict or None, optional
        Additional meta data to include in the qubit messages output from this source's ports.
    properties : dict or None, optional
        Extra properties associated with this component.
    \\*\\*meta_data
        Meta data to be added to the message.

    Properties
    ----------
    trigger_delay : float
        Delay between receiving a trigger and starting emission [ns].
        Can be used to tune pulse timing with a detector.

    Ports
    -----
    qout{0..N}
        Output ports of emitted qubits.
    trigger
        Port used to trigger the source when operating in
        :attr:`~netsquid.components.qsource.SourceStatus.EXTERNAL` mode.
        Will trigger upon receiving any message.

    Models
    ------
    emission_delay_model : :obj:`~netsquid.components.models.delaymodels.DelayModel`
        Model used to calculate the time it takes to generate a pulse. While a pulse is being generated, no new pulses
        can be handled and an error will be raised.
    emission_noise_model : :obj:`~netsquid.components.models.QuantumErrorModel`
        Model applied to qubits before putting them on output ports.

    Subcomponents
    -------------
    internal_clock : :obj:`~netsquid.components.clock.Clock`
        Clock used to trigger emission when set to
        :attr:`~netsquid.components.qsource.SourceStatus.INTERNAL` mode.

    Raises
    ------
    ValueError
        If the number of qubits in the ``state_sampler`` is not a multiple of the number of ports, the length of
        qubit_port_mapping does not equal this number of qubits and when the port index is < 0 or > (num_ports - 1).
    TypeError
        If the qubit_port_mapping contains anything other than integers.
    QSourceTriggerError
        If the QSource is triggered internally (externally) while not in internal (external) mode or when a trigger is received while generating a pulse. When
        triggered at the moment of emission, the trigger will be handled and no error will be raised.

    Examples
    --------

    Create a quantum source and use it in :attr:`~netsquid.components.qsource.SourceStatus.INTERNAL` mode:

    >>> import netsquid as ns
    >>> from netsquid.components import QSource, Clock
    >>> from netsquid.components.qsource import SourceStatus
    ...
    >>> ns.sim_reset()
    >>> # Define an output handler to store/check the source's output
    >>> #(alternatively you can connect the output port to another component's port).
    >>> port_output = list()
    ...
    >>> def store_output_from_port(message, storage=port_output):
    ...     # Store all messages send to a port in a list.
    ...     port_output.append(message)
    ...
    >>> def print_source_output_message(message):
    ...     # Print qubit, emission time and emission delay.
    ...     print("Qubits {} emitted at t={} with an emission delay of {} nano seconds"
    ...          .format(message.items, message.meta["emission_time"], message.meta["emission_delay"]))
    ...
    >>> # Create a source and connect its output port
    >>> frequency = 5e8
    >>> source = QSource("test_source", frequency=frequency)
    >>> source.ports["qout0"].bind_output_handler(lambda message: store_output_from_port(message))
    >>> # Start the source by changing its status
    >>> source.status = SourceStatus.INTERNAL
    >>> ns.sim_run(duration=1)
    >>> assert len(port_output) == 1  # Without trigger or emission delay, 1st pulse generated at t= 0.
    >>> ns.sim_run(duration=1e9 / frequency)
    >>> assert len(port_output) == 2  # After '1e9 / frequency' nano seconds, 2nd pulse is generated.
    >>> for message in port_output:
    ...     print_source_output_message(message)
    Qubits [Qubit('test_source-#1-0')] emitted at t=0.0 with an emission delay of 0 nano seconds
    Qubits [Qubit('test_source-#2-0')] emitted at t=2.0 with an emission delay of 0 nano seconds

    >>> # To change the source's frequency you can simply call `source.frequency = new frequency`
    >>> source.frequency = 1e9
    >>> ns.sim_run(duration=2)
    >>> assert len(port_output) == 3  # 3rd pulse still scheduled at old frequency (i.e. after 2 ns).
    >>> ns.sim_run(duration=1)
    >>> # From now on the new pulsed scheduled at the new frequency (i.e. every nano second.)
    >>> assert len(port_output) == 4

    Create a quantum source, use it in :attr:`~netsquid.components.qsource.SourceStatus.EXTERNAL` mode and connect an
    external clock.

    >>> ns.sim_reset()
    >>> # Create a source directly in external mode.
    >>> source = QSource("test_source", status=SourceStatus.EXTERNAL)
    >>> source.ports["qout0"].bind_output_handler(lambda message: print_source_output_message(message))
    >>> # Create a clock that will tick 3 times and connect it to the trigger port of the source.
    >>> clock = Clock("clock", frequency=1e9, max_ticks=3)
    >>> clock.ports["cout"].connect(source.ports["trigger"])
    >>> clock.start()
    >>> ns.sim_run()
    Qubits [Qubit('test_source-#1-0')] emitted at t=0.0 with an emission delay of 0 nano seconds
    Qubits [Qubit('test_source-#2-0')] emitted at t=1.0 with an emission delay of 0 nano seconds
    Qubits [Qubit('test_source-#3-0')] emitted at t=2.0 with an emission delay of 0 nano seconds
    SimStats()

    Create a quantum source and make it sample from a 3-qubit-state and dividing them over 2 output ports.

    >>> import numpy as np
    >>> from netsquid.qubits.state_sampler import StateSampler
    ...
    >>> ns.sim_reset()
    >>> num_ports = 2
    >>> SS_3_QUBITS = StateSampler(np.eye(2**3) / 2**3)
    >>> source = QSource("test_source", state_sampler=SS_3_QUBITS, num_ports=num_ports,
    ...          frequency=1e9, qubit_port_mapping=[0, 1, 1], status=SourceStatus.INTERNAL)
    >>> source.ports["qout0"].bind_output_handler(
    ...     lambda message: print("Message {} on 'qout0' at t={}".format(message.items, message.meta["emission_time"])))
    >>> source.ports["qout1"].bind_output_handler(
    ...     lambda message: print("Message {} on 'qout1' at t={}".format(message.items, message.meta["emission_time"])))
    >>> ns.sim_run(3)
    Message [Qubit('test_source-#1-0')] on 'qout0' at t=0.0
    Message [Qubit('test_source-#1-1'), Qubit('test_source-#1-2')] on 'qout1' at t=0.0
    Message [Qubit('test_source-#2-0')] on 'qout0' at t=1.0
    Message [Qubit('test_source-#2-1'), Qubit('test_source-#2-2')] on 'qout1' at t=1.0
    Message [Qubit('test_source-#3-0')] on 'qout0' at t=2.0
    Message [Qubit('test_source-#3-1'), Qubit('test_source-#3-2')] on 'qout1' at t=2.0
    SimStats()

    """
    _EVT_NEWPULSE = EventType("NEWPULSE", "Start of a new pulse.")

    def __init__(self, name, state_sampler=None, frequency=100, timing_model=None, trigger_delay=0,
                 models=None, status=SourceStatus.OFF, num_ports=1, qubit_port_mapping=None,
                 output_meta=None, properties=None, **kwargs):
        self._output_port_names = ["qout{}".format(idx) for idx in range(num_ports)]
        if properties is None:
            properties = {"is_number_state": False}
        if models is None:
            models = {}
        if "emission_delay_model" in kwargs:
            warn_deprecated("The emission_delay_model parameter is no longer supported. "
                            "Use the models dict with key 'emission_delay_model' to specify an emission delay model",
                            key="QSource.__init__.emission_delay_model")
            models["emission_delay_model"] = kwargs["emission_delay_model"]
        if "emission_noise_model" in kwargs:
            warn_deprecated("The emission_noise_model parameter is no longer supported. "
                            "Use the models dict with key 'emission_noise_model' to specify an emission noise model",
                            key="QSource.__init__.emission_noise_model")
            models["emission_noise_model"] = kwargs["emission_noise_model"]
        # Specify and add models
        self.specify_model("emission_delay_model", DelayModel)
        self.specify_model("emission_noise_model", QuantumErrorModel)
        if 'emission_delay_model' not in models:
            models['emission_delay_model'] = FixedDelayModel(delay=0)
        super().__init__(name=name, properties=properties, models=models,
                         port_names=self._output_port_names + ["trigger"])
        self.add_property("trigger_delay", value=trigger_delay, value_constraints=nonnegative_constr)
        # Add subcomponents
        self.add_subcomponent(Clock(name="internal_clock", frequency=frequency, models={"timing_model": timing_model}),
                              name="internal_clock")
        # Internal variables
        self._status = None
        self._initial_status = status  # status used when resetting this source.
        self._busy_until = -1
        self._port_messages = {}
        self._qubits_per_event = {}
        self._pulse_count = 0
        if state_sampler is None:
            state_sampler = StateSampler([ks.s0])
        self._state_sampler = state_sampler  # NOTE: set ports first to be able to check the num_qubits match num_ports.
        self._set_qubit_port_mapping(qubit_port_mapping)
        # Set-up connections
        self.ports["trigger"].bind_input_handler(self._external_tick_handler)
        self.subcomponents["internal_clock"].ports["cout"].bind_output_handler(self._internal_tick_handler)
        self._newpulse_handler = EventHandler(self._emit)
        # Set status after all connections have been made.
        self.status = status
        # Set additional meta_data for message
        if output_meta is not None and not isinstance(output_meta, dict):
            raise TypeError("output_meta parameter should be a dict or None")
        self._output_meta = output_meta if output_meta is not None else {}

    @property
    def status(self):
        """:obj:`~netsquid.components.qsource.SourceStatus`: The current state of the source."""
        return self._status

    @property
    def output_meta(self):
        """dict: additional meta data to include in qubit output messages."""
        return self._output_meta

    @status.setter
    def status(self, value):
        if value == self._status:
            # Do nothing
            pass
        elif value == SourceStatus.OFF:
            if self._status == SourceStatus.INTERNAL:
                self.subcomponents["internal_clock"].stop()
            self._dismiss(self._newpulse_handler, entity=self, event_type=self._EVT_NEWPULSE)
        else:
            if value == SourceStatus.EXTERNAL:
                if self.subcomponents["internal_clock"].is_running:
                    self.subcomponents["internal_clock"].stop()
            elif value == SourceStatus.INTERNAL:
                if self.subcomponents["internal_clock"].models["timing_model"] is None:
                    raise ValueError(
                        "When operating a QSource in internal mode, you have to provide at least a frequency or timing model.")
                self.subcomponents["internal_clock"].start()
            self._wait(self._newpulse_handler, entity=self, event_type=self._EVT_NEWPULSE)
        self._status = value

    @property
    def state_sampler(self):
        """:obj:`~netsquid.qubits.state_sampler.StateSampler`: Object that contains states of which this source will sample.

        When changing the state_sampler, the size of the state (i.e. number of qubits) should stay the same to keep the qubit-port
        mapping intact.

        """
        return self._state_sampler

    @state_sampler.setter
    def state_sampler(self, value):
        self._state_sampler = value
        self._set_qubit_port_mapping(self._qubit_port_mapping)

    @property
    def frequency(self):
        """float: frequency at which the source can create qubits in internal mode [Hz]."""
        return self.subcomponents["internal_clock"].frequency

    @frequency.setter
    def frequency(self, value):
        self.subcomponents["internal_clock"].frequency = value

    @property
    def prep_delay(self):
        """float: time it takes to create pulse after being triggered: a combination of trigger delay and emission
        delay [ns]."""
        return self.properties["trigger_delay"] + self.models["emission_delay_model"](**self.properties)

    @property
    def period(self):
        """float: time between pulses based on timing model [ns]."""
        return self.subcomponents["internal_clock"].get_period()

    def reset(self):
        """Reset this QSource and all its subcomponents and ports."""
        self.status = SourceStatus.OFF
        self._busy_until = -1
        super().reset()
        self.status = self._initial_status

    def trigger(self):
        """Trigger source to generate a single pulse."""
        # NOTE: Simply self._external_tick_handler() works as well.
        self.ports["trigger"].tx_input(Message([ns.sim_time()]))

    def _external_tick_handler(self, message=None):
        # Check mode and schedule new pulse.
        if self._status == SourceStatus.INTERNAL:
            raise QSourceTriggerError("A QSource cannot be triggered when it is not in external mode.")
        self._generate_new_pulse()

    def _internal_tick_handler(self, message=None):
        # Check mode and schedule new pulse.
        if self._status == SourceStatus.EXTERNAL:
            raise QSourceTriggerError("A QSource should not be triggered by its internal clock while in external mode.")
        self._generate_new_pulse()

    def _generate_new_pulse(self):
        # Generate a new pulse.
        if self.status == SourceStatus.OFF:
            return
        curr_time = ns.sim_time()
        if not math.isclose(curr_time, self._busy_until) and curr_time < self._busy_until:
            raise QSourceTriggerError(
                "QSource is triggered while busy generating a pulse (t_now = {}. busy until t = {}."
                .format(curr_time, self._busy_until))
        # Use a single call to prep_delay to make sure the same emission_delay is passed to the emission noise model:
        local_prep_delay = self.prep_delay
        self._busy_until = curr_time + local_prep_delay
        # Schedule emission.
        event = self._schedule_after(local_prep_delay, self._EVT_NEWPULSE)
        # Generate pulse messages.
        self._pulse_count += 1
        sysname = "{}-#{}-".format(self.name, self._pulse_count)
        qubits = qapi.create_qubits(self._state_sampler.num_qubits, system_name=sysname, no_state=True)
        qs_repr, _, _ = self._state_sampler.sample()
        qapi.assign_qstate(qubits, qs_repr)
        # FIXME: MODIFIED (NEW LOGIC) 
        if self.properties["is_number_state"] is not None:
            for qubit in qubits:
                qubit.is_number_state = self.properties["is_number_state"]
        # Distribute qubits over ports according to qubit_port_mapping
        qubits_per_port = {}
        self._qubits_per_event[event] = qubits
        for qubit_idx, qubit in enumerate(qubits):
            port_idx = self._qubit_port_mapping[qubit_idx]
            qubits_per_port.setdefault(self._output_port_names[port_idx], []).append(qubit)
        # Use only the emission delay part of the prep_delay to pass to emission noise model.
        emission_delay = local_prep_delay - self.properties["trigger_delay"]
        for port_name in self._output_port_names:
            self._port_messages[(event, port_name)] = Message(qubits_per_port[port_name],
                                                              emission_time=curr_time + local_prep_delay,
                                                              emission_delay=emission_delay,
                                                              **self._output_meta)

    def _emit(self, event):
        # emit pulse on ports.
        if self.status == SourceStatus.OFF:
            return
        # Apply noise to all generated qubits at once.
        if self.models["emission_noise_model"] is not None:
            qubits = self._qubits_per_event[event]
            message = self._port_messages[
                (event, self._output_port_names[0])]  # Take one message to get the emission delay for all qubits.
            emission_delay = message.meta["emission_delay"]
            self.models["emission_noise_model"](qubits, delta_time=emission_delay, **self.properties)
        for port_name in self._output_port_names:
            # Use pop to remove entry from dict and prevent memory leakage.
            output = self._port_messages.pop((event, port_name))
            self.ports[port_name].tx_output(output)
        # Reset internal dict to prevent memory leakage.
        self._qubits_per_event = {}

    def _set_qubit_port_mapping(self, qubit_port_mapping):
        # Perform checks on and set qubit_port_mapping, if None, set an evenly distributed mapping.
        num_output_ports = len(self._output_port_names)
        if qubit_port_mapping is None:
            if self._state_sampler.num_qubits % num_output_ports != 0:
                raise ValueError(("Number of qubits ({}) should be a multiple of the number of ports ({})".
                                  format(self._state_sampler.num_qubits, num_output_ports)))
            else:
                # Equally distribute qubits over ports.
                num_qubits_per_port = int(self._state_sampler.num_qubits / num_output_ports)
                list_of_lists = [[i] * num_qubits_per_port for i in range(num_output_ports)]
                self._qubit_port_mapping = [item for sublist in list_of_lists for item in sublist]  # Flattened list
        elif len(qubit_port_mapping) != self._state_sampler.num_qubits:
            raise ValueError(("Number of qubits ({}) should match the length of qubit_port_mapping ({})".
                              format(self._state_sampler.num_qubits, len(qubit_port_mapping))))
        elif max(qubit_port_mapping) > (num_output_ports - 1) or min(qubit_port_mapping) < 0:
            raise ValueError("qubit_port_mapping should only contain values between 0 and num_ports - 1 ({}), not {} - "
                             "{}".format(num_output_ports - 1, min(qubit_port_mapping), max(qubit_port_mapping)))
        elif not all([isinstance(port_idx, int) for port_idx in qubit_port_mapping]):
            raise TypeError("qubit_port_mapping should only contain integers, not {}".format(qubit_port_mapping))
        else:
            self._qubit_port_mapping = qubit_port_mapping


class QSourceError(Exception):
    # General QSource error.
    pass


class QSourceTriggerError(QSourceError):
    # Exception raised on errors related to QSource being triggered incorrectly.
    pass
