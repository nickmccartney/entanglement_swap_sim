import numpy as np
import netsquid as ns
from netsquid.protocols import NodeProtocol, Signals
from netsquid.components import QuantumProgram, instructions as instr

__all__ = [
    "RepeaterProtocol",
    "MemoryRouting",
    "MemoryAccess",
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
    route_node_A : class:'ManageRouting'
        Manages routing qubits incoming from node_A to available memory slot
    route_node_B : class:'ManageRouting'
        Manages routing qubits incoming from node_B to available memory slot

    """
    def __init__(self, node, mem_config, name=None):
        super().__init__(node, name=name)
        # generate assignments to pass to subprotocols
        self.port_names = list(dict(self.node.ports).keys())                                                                 # Ports to end nodes generated in "setup_network"
        self.slots_A = self.node.qmemory.get_matching_positions(
            'origin', value='node_A')
        self.slots_B = self.node.qmemory.get_matching_positions(
            'origin', value='node_B')
        self._add_subprotocols(node,
                               self.port_names[0],
                               self.slots_A,
                               self.port_names[1],
                               self.slots_B,
                               mem_config)

        self.use_memory = mem_config.pop('use_memory')
        self.qubits_A = dict((slot, False) for slot in self.slots_A)                                                    # FIXME: a bit weird implementation
        self.qubits_B = dict((slot, False) for slot in self.slots_B)
        self.result = None
        self._bsm_results = [(0, 0), (0, 1), (1, 0), (1, 1)]                                                            # Bell state measurement results

    def _add_subprotocols(self, node, port_name_A, slots_A, port_name_B, slots_B, mem_config):
        self.add_subprotocol(MemoryRouting(node, port_name_A, slots_A, mem_config, 'route_node_A'))
        self.add_subprotocol(MemoryRouting(node, port_name_B, slots_B, mem_config, 'route_node_B'))

    def run(self):
        self.node.subcomponents["Clock_{}".format(self.node.name)].start()
        if self.use_memory is False:
            yield from self._run_no_mem()
        else:
            self.start_subprotocols()
            while True:
                # FIXME: being bottlenecked somewhere I beleive, could it be due to waiting on signal that isnt sent often enough
                expr = yield self.await_signal(self.subprotocols['route_node_A'], "STORED") | \
                             self.await_signal(self.subprotocols['route_node_B'], "STORED")

                slot_A = None
                slot_B = None
                for slot in self.slots_A:
                    status = self.node.qmemory.mem_positions[slot].properties['status']
                    if status == "FILLED":
                        slot_A = slot
                        break
                for slot in self.slots_B:
                    status = self.node.qmemory.mem_positions[slot].properties['status']
                    if status == "FILLED":
                        slot_B = slot
                        break

                if slot_A and slot_B:
                    # prog = BellMeasurementProgram()
                    # if self.node.qmemory.busy:
                    #     yield self.await_program(self.node.qmemory)
                    # self.node.qmemory.execute_program(prog, qubit_mapping=[slot_A, slot_B])
                    # yield self.await_program(self.node.qmemory)
                    # idx, = prog.output['BellStateIndex']
                    # print(f"AFTER MEASURE : {self._bsm_results[idx]}")
                    q1, = self.node.qmemory.pop(slot_A)
                    q2, = self.node.qmemory.pop(slot_B)
                    result = {
                        'qubits': [q1, q2]
                    }
                    self.send_signal(Signals.SUCCESS, result=result)


                    self.node.qmemory.mem_positions[slot_A].properties['status'] = "RESET"
                    self.node.qmemory.mem_positions[slot_B].properties['status'] = "RESET"


    def _run_no_mem(self):                                                                                              # special run case for when memory should not store qubit for any measurable time
        while True:
            expr = yield self.await_port_input(self.node.ports[self.port_names[0]]) | \
                         self.await_port_input(self.node.ports[self.port_names[1]])                                     # wait until qubits arrive at same time FIXME: possibly give some lenience

            q1, = self.node.qmemory.pop(0)
            q2, = self.node.qmemory.pop(1)

            if q1 and q2:
                result = {
                    'qubits': [q1, q2]
                }
                self.send_signal(Signals.SUCCESS, result=result)
            # if expr.first_term.value and expr.second_term.value:
            #     print(expr.first_term.value, expr.second_term.value)
            #     q1, = self.node.qmemory.pop(0)
            #     q2, = self.node.qmemory.pop(1)
            #     result = {
            #         'qubits': [q1, q2]
            #     }
            #     self.send_signal(Signals.SUCCESS, result=result)


class MemoryRouting(NodeProtocol):
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

    def __init__(self, node, port_name, slots, mem_config, name):
        super().__init__(node, name=name)
        self.probability_detection = mem_config['probability_detection']                                                              # probability that qubit is detected after interacting with memory

        self.input_port = port_name


        self.node_name = self.name.replace('route_','')
        if self.node_name == 'node_A':                                                                                       # port initially receiving qubits, await input and trigger QProgram to store into current_slot_idx if available
            self.storage_idx = 0
            self.storage_port = 'qin0'
        elif self.node_name == 'node_B':                                                                                     # port initially receiving qubits, await input and trigger QProgram to store into current_slot_idx if available
            self.storage_port = 'qin1'
            self.storage_idx = 1

        self.slots = slots

        self.signal = "STORED"
        self.add_signal(self.signal)

        self._add_subprotocols(node, slots, mem_config)

    def _add_subprotocols(self, node, slots, mem_config):
        self.add_subprotocol(MemoryAccess(node, slots, mem_config, name='access_'+self.node_name))

    def _get_target_slot(self):
        for slot in self.slots:
            status = self.node.qmemory.mem_positions[slot].properties['status']
            if status == "TARGET":
                return slot
        return None


    def run(self):
        self.start_subprotocols()
        while True:
            expr = yield self.await_signal(self.subprotocols['access_'+self.node_name], "NEW_IDX") | \
                         self.await_port_input(self.node.ports[self.input_port])                                        # await either a new idx to store incoming qubits to, or incoming qubit FIXME : not using signal yet

            target_slot = self._get_target_slot()

            if self.node.qmemory.peek(self.storage_idx) is not None:
                if target_slot != None:                                                                         # realize -1 is only assigned to represent no available slots for storage
                    detected = True if np.random.random_integers(1,100) <= self.probability_detection else False
                    prog = MemoryBehavior()
                    prog.set_detected(detected)
                    if self.node.qmemory.busy:
                        yield self.await_program(self.node.qmemory)
                    self.node.qmemory.execute_program(prog, qubit_mapping=[self.storage_idx, target_slot])
                    yield self.await_program(self.node.qmemory)
                    self.node.qmemory.pop(self.storage_idx)
                    self.node.qmemory.mem_positions[target_slot].properties['status'] = "FILLED"
                    self.send_signal(self.signal, result=target_slot)



class MemoryAccess(NodeProtocol):
    #
    #   Report current idx
    #
    def __init__(self, node, slots, mem_config, name):
        super().__init__(node, name=name)
        self.slots = slots                                                                                              # accessible slots corresponding to this port
        self.reset_period_cycles = mem_config['reset_period_cycles']                                                    # number of periods to wait until forcing current index to reset
        self.reset_duration_cycles = mem_config['reset_duration_cycles']                                                # number of periods to force slot inactive during reset phase

        self.reset_timer = dict((slot, self.reset_duration_cycles) for idx, slot in enumerate(slots))                   # dict of reset timers for each individual slot, indexed using valid memory slot from "slots"
        self.reset_trigger_timer = dict((slot, self.reset_period_cycles) for idx, slot in enumerate(slots))             # dict of countdowns towards reset for the slot that is targeted for storage


        self.signal = "NEW_IDX"
        self.add_signal(self.signal)

    def _send_new_idx(self):
        for idx, status in enumerate(self.slots_reset_status):
            if status == False:
                self.send_signal(self.signal, result=self.slots[idx])                                                          # send slot idx of first available slot, used as current_slot_idx to store qubits
                return
        self.send_signal(self.signal, result=-1)                                                                               # if all slots are in reset, indicate that no index is available by sending -1

    def _reset_state(self, slot):
        q_default, = ns.qubits.create_qubits(1, no_state=True)
        ns.qubits.assign_qstate([q_default], ns.h0)                                                                     # create initial default known state to reinitialize slot
        self.node.qmemory.put(q_default, positions=[slot])                                                              # reset qubit to known state
        self.node.qmemory.mem_positions[slot].properties['status'] = "IDLE"                                             # assign slot IDLE status, available for use

    def _get_new_target(self):                                                                                          # find slot to assign as TARGET
        for slot in self.slots:                                                                                         # check if there is existing target
            status = self.node.qmemory.mem_positions[slot].properties['status']
            if status == "TARGET":
                return

        for slot in self.slots:                                                                                         # check if there is was no TARGET, try to assign first IDLE slot as TARGET
            status = self.node.qmemory.mem_positions[slot].properties['status']
            if status == "IDLE":
                self.node.qmemory.mem_positions[slot].properties['status'] = "TARGET"                                   # assign new target slot
                return

    def run(self):
        target_slot = self.slots[0]
        self.node.qmemory.mem_positions[target_slot].properties['status'] = "TARGET"                                    # indicates to "MemoryRouting" protocol which slot to attempt to store on

        while True:

            yield self.await_port_output(self.node.subcomponents["Clock_{}".format(self.node.name)].ports['cout'])

            for slot in self.slots:
                status = self.node.qmemory.mem_positions[slot].properties['status']
                if status == "RESET":
                    if self.reset_timer[slot] == 0:
                        self._reset_state(slot)
                        self.reset_timer[slot] = self.reset_duration_cycles                                             # restore reset timer for next time
                    else:
                        self.reset_timer[slot] -= 1
                elif status == "TARGET":
                    if self.reset_trigger_timer[slot] == 0:
                        self.node.qmemory.mem_positions[slot].properties['status'] = "RESET"                            # flag slot as RESET
                        self.reset_trigger_timer[slot] = self.reset_period_cycles                                             # reset timer for next occurrence
                    else:
                        self.reset_trigger_timer[slot] -= 1

                elif status == "IDLE":
                    if self.reset_trigger_timer[slot] == 0:
                        self.node.qmemory.mem_positions[slot].properties['status'] = "RESET"                            # flag slot as RESET
                        self.reset_trigger_timer[slot] = self.reset_period_cycles                                       # reset timer for next occurrence
                    else:
                        self.reset_trigger_timer[slot] -= 1

                elif status == "FILLED":
                    if self.reset_trigger_timer[slot] == 0:
                        self.node.qmemory.mem_positions[slot].properties['status'] = "RESET"                            # flag slot as RESET
                        self.reset_trigger_timer[slot] = self.reset_period_cycles                                       # reset timer for next occurrence
                    else:
                        self.reset_trigger_timer[slot] -= 1

                self._get_new_target()






            # for idx, count in enumerate(self.slots_reset_timer):
            #     if self.slots_reset_status[idx] is True:                                                                # iterate through all slots that are in reset phase
            #         if self.slots_reset_timer[idx] == 0:                                                                # check to see if slot has waited entire reset duration
            #             # print(f"RESETTING IDX {self.slots[idx]} on {self.name}")
            #             # FIXME: put into a helper function
            #             q_mem_init, = ns.qubits.create_qubits(1, no_state=True)
            #             ns.qubits.assign_qstate([q_mem_init], ns.h0)                                                      # create initial memory state to replace existing qubit
            #             # print(f"In {self.name} on idx={self.slots[idx]} before reset: {self.node.qmemory.peek(self.slots[idx], skip_noise=True)} --> {self.node.qmemory.mem_positions[self.slots[idx]].in_use}")
            #             self.node.qmemory.put(q_mem_init, positions=[self.slots[idx]])                                 # reset qubit to known state
            #             self.node.qmemory.set_position_used(False, self.slots[idx])                                                 # mark this slot as unused
            #             # print(f"In {self.name} on idx={self.slots[idx]} after reset: {self.node.qmemory.peek(self.slots[idx])} --> {self.node.qmemory.mem_positions[self.slots[idx]].in_use}")
            #             self.slots_reset_status[idx] = False                                                            # unflag reset
            #             self.slots_reset_timer[idx] = self.reset_duration_cycles                                        # restore reset timer for next time
            #             self._send_new_idx()                                                                            # realize this helps assign idx faster if nothing was available before
            #         else:
            #             self.slots_reset_timer[idx] -= 1                                                                # decrement countdown timer for each slot
            #             # print(self.slots_reset_timer)
            #
            # if (self.node.subcomponents["Clock_{}".format(self.node.name)].num_ticks
            #     % self.reset_period_cycles) == 0:                                                                       # slow clock to trigger a forced reset at desired rate
            #     for idx, reset_status in enumerate(self.slots_reset_status):
            #         # print(f"SLOT RESET STATUS: {self.slots_reset_status}")
            #         if reset_status is False:                                                                           # find first slot that is not in reset phase
            #             self.slots_reset_status[idx] = True                                                             # flag reset
            #             self.node.qmemory.set_position_used(True, self.slots[idx])                                                  # set as used, avoiding accidental use during reset
            #             self._send_new_idx()                                                                            # call helper function to get first possible idx to use as current_slot_idx in MemoryRouting
            #         else:
            #             continue
            #         break



# class MemoryRouting(NodeProtocol):
#     """Subprotocol of "RepeaterProtocol": manages routing incoming qubits
#
#     * Determines location of available slot (if any)
#     * Places qubit into memory slot
#     * Emits "SUCCESS" signal along with newly allocated index number
#
#     Parameters
#     ----------
#     node : :py:class:'~netsquid.nodes.node.Node'
#         node functioning as central repeater
#     port : :py:class: str
#         specify appropriate input port
#     name : :py:class: str
#         specify name of subprotocol
#
#     """
#
#     def __init__(self, node, port_name, slots, mem_config, name):
#         super().__init__(node, name=name)
#         self.probability_detection = mem_config['probability_detection']                                                              # probability that qubit is detected after interacting with memory
#
#         self.input_port = port_name
#         self.current_slot_idx = -1                                                                                      # current slot index to store qubits in, no slots available when -1     # FIXME: THIS FORCES SLOTS TO RESET BEFORE current_slot_idx is assigned
#
#         self.node_name = self.name.replace('route_','')
#         if self.node_name == 'node_A':                                                                                       # port initially receiving qubits, await input and trigger QProgram to store into current_slot_idx if available
#             self.storage_idx = 0
#             self.storage_port = 'qin0'
#         elif self.node_name == 'node_B':                                                                                     # port initially receiving qubits, await input and trigger QProgram to store into current_slot_idx if available
#             self.storage_port = 'qin1'
#             self.storage_idx = 1
#
#         self.signal = "STORED"
#         self.add_signal(self.signal)
#
#         self._add_subprotocols(node, slots, mem_config)
#
#     def _add_subprotocols(self, node, slots, mem_config):
#         self.add_subprotocol(MemoryAccess(node, slots, mem_config, name='access_'+self.node_name))
#
#
#     def run(self):
#         self.start_subprotocols()
#         while True:
#             expr = yield self.await_signal(self.subprotocols['access_'+self.node_name], "NEW_IDX") | \
#                          self.await_port_input(self.node.ports[self.input_port])                                        # await either a new idx to store incoming qubits to, or incoming qubit
#
#             if expr.first_term.value:
#                 self.current_slot_idx = self.subprotocols['access_'+self.node_name].get_signal_result(
#                     label="NEW_IDX", receiver=self)                                                                     # assign current_slot_idx designated by MemoryAccess protocol
#             else:
#                 if self.node.qmemory.peek(self.storage_idx) is not None:
#                     if self.current_slot_idx != -1:                                                                         # realize -1 is only assigned to represent no available slots for storage
#                         detected = True if np.random.random_integers(1,100) <= self.probability_detection else False
#                         prog = MemoryBehavior()
#                         prog.set_detected(detected)
#                         if self.node.qmemory.busy:
#                             yield self.await_program(self.node.qmemory)
#                         self.node.qmemory.execute_program(prog, qubit_mapping=[self.storage_idx, self.current_slot_idx])
#                         yield self.await_program(self.node.qmemory)
#                         self.node.qmemory.pop(self.storage_idx)
#                         self.send_signal(self.signal, result=self.current_slot_idx)
#                         self.current_slot_idx = -1                                                                          # will not immediately attempt to use same slot after successful storage
#
#
# class MemoryAccess(NodeProtocol):
#     #
#     #   Report current idx
#     #
#     def __init__(self, node, slots, mem_config, name):
#         super().__init__(node, name=name)
#         self.slots = slots                                                                                              # accessible slots corresponding to this port
#         self.slots_reset_status = [False] * len(self.slots)                                                             # flag indicating slots that are in reset phase
#         self.reset_period_cycles = mem_config['reset_period_cycles']                                                    # number of periods to wait until forcing current index to reset
#         self.reset_duration_cycles = mem_config['reset_duration_cycles']                                                # number of periods to force slot inactive during reset phase
#         self.slots_reset_timer = [self.reset_duration_cycles] * len(self.slots)                                         # list of reset timers for each individual slot corresponding to this port
#
#         self.signal = "NEW_IDX"
#         self.add_signal(self.signal)
#
#     def _send_new_idx(self):
#         for idx, status in enumerate(self.slots_reset_status):
#             if status == False:
#                 self.send_signal(self.signal, result=self.slots[idx])                                                          # send slot idx of first available slot, used as current_slot_idx to store qubits
#                 return
#         self.send_signal(self.signal, result=-1)                                                                               # if all slots are in reset, indicate that no index is available by sending -1
#
#     def run(self):
#         while True:
#             yield self.await_port_output(self.node.subcomponents["Clock_{}".format(self.node.name)].ports['cout'])
#             for idx, count in enumerate(self.slots_reset_timer):
#                 if self.slots_reset_status[idx] is True:                                                                # iterate through all slots that are in reset phase
#                     if self.slots_reset_timer[idx] == 0:                                                                # check to see if slot has waited entire reset duration
#                         # print(f"RESETTING IDX {self.slots[idx]} on {self.name}")
#                         # FIXME: put into a helper function
#                         q_mem_init, = ns.qubits.create_qubits(1, no_state=True)
#                         ns.qubits.assign_qstate([q_mem_init], ns.h0)                                                      # create initial memory state to replace existing qubit
#                         # print(f"In {self.name} on idx={self.slots[idx]} before reset: {self.node.qmemory.peek(self.slots[idx], skip_noise=True)} --> {self.node.qmemory.mem_positions[self.slots[idx]].in_use}")
#                         self.node.qmemory.put(q_mem_init, positions=[self.slots[idx]])                                 # reset qubit to known state
#                         self.node.qmemory.set_position_used(False, self.slots[idx])                                                 # mark this slot as unused
#                         # print(f"In {self.name} on idx={self.slots[idx]} after reset: {self.node.qmemory.peek(self.slots[idx])} --> {self.node.qmemory.mem_positions[self.slots[idx]].in_use}")
#                         self.slots_reset_status[idx] = False                                                            # unflag reset
#                         self.slots_reset_timer[idx] = self.reset_duration_cycles                                        # restore reset timer for next time
#                         self._send_new_idx()                                                                            # realize this helps assign idx faster if nothing was available before
#                     else:
#                         self.slots_reset_timer[idx] -= 1                                                                # decrement countdown timer for each slot
#                         # print(self.slots_reset_timer)
#
#             if (self.node.subcomponents["Clock_{}".format(self.node.name)].num_ticks
#                 % self.reset_period_cycles) == 0:                                                                       # slow clock to trigger a forced reset at desired rate
#                 for idx, reset_status in enumerate(self.slots_reset_status):
#                     # print(f"SLOT RESET STATUS: {self.slots_reset_status}")
#                     if reset_status is False:                                                                           # find first slot that is not in reset phase
#                         self.slots_reset_status[idx] = True                                                             # flag reset
#                         self.node.qmemory.set_position_used(True, self.slots[idx])                                                  # set as used, avoiding accidental use during reset
#                         self._send_new_idx()                                                                            # call helper function to get first possible idx to use as current_slot_idx in MemoryRouting
#                     else:
#                         continue
#                     break


class MemoryBehavior(QuantumProgram):
    # model quantum memory storage
    # performs CPHASE and X basis measurements
    # conditionally perform second half dependent on "detection"
    default_num_qubits = 2

    def set_detected(self, detected):
        self._detected = detected

    def program(self, **kwargs):
        q0,q1 = self.get_qubit_indices(2)
        self.apply(instr.INSTR_CZ, [q0,q1])
        self.apply(instr.INSTR_MEASURE_X, [q0], output_key='measure_X')
        yield self.run()

        if self._detected:
            if self.output['measure_X'] == 1:
                self.apply(instr.INSTR_H, [q1])
                self.apply(instr.INSTR_Z, [q1])
            else:
                self.apply(instr.INSTR_H, [q1])
            yield self.run()

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