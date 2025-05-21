# assembler_state.py v1.26
"""
Manages the state of the assembler during passes, including Location Counter (LC),
Position Counter (PC), current block, base, listing flags, etc.
[...]
v1.25: - Added pre_loc_block_name to track the block active before a LOC.
       - Modified advance_lc and force_upper in Pass 1 to correctly increment
         the size of pre_loc_block_name when lc_is_absolute_due_to_loc is True.
v1.26: - Refined deferred_force_upper_pending logic. handle_force_upper now
         conditionally applies the LC increment based on this flag if it's
         being called in a context where the increment should be deferred
         (e.g., before an EQU * or label definition on the next line).
         The actual deferral decision point is in pass1/pass2 processing.
"""
from typing import Optional, Dict, List, Tuple, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from errors import ErrorReporter
    from output_generator import OutputGenerator
    from crass import Assembler

def handle_force_upper(assembler_state: 'AssemblerState', output_gen: Optional['OutputGenerator'], error_reporter: 'ErrorReporter', line_num: int, consume_deferred_flag: bool = True):
    """
    Forces the current word to be completed, advancing LC and resetting PC.
    If consume_deferred_flag is True (default), it will also reset
    assembler_state.deferred_force_upper_pending.
    The actual LC increment might be skipped if deferred_force_upper_pending was true
    AND this call is specifically for the "deferred execution" of the force.
    This function primarily handles the mechanics; the decision to defer
    the LC increment for symbol definition happens in the pass processing logic.
    """
    if not hasattr(assembler_state, 'position_counter') or \
       not hasattr(assembler_state, 'pass_number') or \
       not hasattr(assembler_state, 'debug_mode') or \
       not hasattr(assembler_state, 'location_counter') or \
       not hasattr(assembler_state, 'force_upper'):
        if hasattr(assembler_state, 'error_reporter') and assembler_state.error_reporter:
            assembler_state.error_reporter.add_error("Internal: AssemblerState missing attributes needed by handle_force_upper.", line_num, code='F')
        else:
            print(f"FATAL L{line_num}: AssemblerState missing attributes needed by handle_force_upper.")
        return

    if assembler_state.position_counter != 0:
        if assembler_state.debug_mode:
            lc_type = "AbsLC" if assembler_state.pass_number == 2 or assembler_state.lc_is_absolute_due_to_loc else "RelLC"
            print(f">>> DEBUG LC: L{line_num} handle_force_upper: Forcing upper from PC={assembler_state.position_counter}, DeferredPending={assembler_state.deferred_force_upper_pending}, ConsumeFlag={consume_deferred_flag}")
            print(f"    State Before: {lc_type}={assembler_state.location_counter:o}, PC={assembler_state.position_counter}, Block={assembler_state.current_block}, LOC_Abs={assembler_state.lc_is_absolute_due_to_loc}, PreLOCBlk={assembler_state.pre_loc_block_name}")

        if assembler_state.pass_number == 2 and output_gen:
            if hasattr(output_gen, 'flush_binary_word'):
                 output_gen.flush_binary_word(pad_with_noops=True)

        # The core LC increment happens here. The "deferral" for symbol definition
        # means that the pass logic calls this *after* the symbol is defined using the old LC.
        assembler_state.force_upper() # force_upper itself will increment LC and reset PC

    if consume_deferred_flag:
        if assembler_state.deferred_force_upper_pending and assembler_state.debug_mode:
            print(f">>> DEBUG LC: L{line_num} handle_force_upper: Consuming deferred_force_upper_pending flag.")
        assembler_state.deferred_force_upper_pending = False


class AssemblerState:
    """Tracks the assembler's current state."""
    def __init__(self):
        self.location_counter: int = 0
        self.position_counter: int = 0
        self.current_block: str = '*ABS*'
        self.current_base: str = 'D'
        self.current_code: str = 'D'
        self.listing_flags: Dict[str, bool] = {
            'B': True, 'C': True, 'D': True, 'E': True, 'F': True, 'G': True,
            'M': True, 'N': True, 'R': True, 'S': True, 'X': True
        }
        self.current_qualifier: Optional[str] = None
        self.pass_number: int = 0
        self.conditional_stack: List[bool] = [(True)]
        self.end_statement_processed: bool = False
        self.current_line_number: int = 0
        self.line_start_address: int = 0
        self.line_start_position_bits: int = 0
        self.program_start_symbol: Optional[str] = None
        self.program_start_address: Optional[int] = None
        self.current_title: str = ""
        self.current_ttl_title: str = ""
        self.skip_count: int = 0
        self.is_defining: Optional[str] = None # "MACRO", "OPDEF"
        self.current_definition_name: Optional[str] = None
        self.current_definition_params: List[str] = []
        self.current_definition_lines: List[str] = []
        self.current_remote_block_name: Optional[str] = None # For RMT/HERE
        self.block_lcs: Dict[str, int] = {'*ABS*': 0}
        self.block_order: List[str] = []
        self.lc_is_absolute_due_to_loc: bool = False
        self.pre_loc_block_name: Optional[str] = None # Stores block active before LOC
        self.error_reporter = None
        self.symbol_table = None
        self.debug_mode = False
        self.assembler: Optional['Assembler'] = None
        self.last_significant_mnemonic: Optional[str] = None
        self.last_significant_mnemonic_lc: Optional[int] = None
        self.deferred_force_upper_pending: bool = False # True if a special op didn't fill word
        self.first_title_processed: bool = False


    def set_pass(self, pass_num):
        self.pass_number = pass_num
        if pass_num == 1:
            self.block_lcs = {'*ABS*': 0}
            self.block_order = []
            self.lc_is_absolute_due_to_loc = False
            self.pre_loc_block_name = None
            self.current_title = ""
            self.current_ttl_title = ""
            self.last_significant_mnemonic = None
            self.last_significant_mnemonic_lc = None
            self.deferred_force_upper_pending = False
            self.first_title_processed = False
            self.current_remote_block_name = None


    def reset_for_pass2(self):
        literal_size = 0
        if self.symbol_table:
            literal_size = self.symbol_table.get_literal_block_size()

        initial_absolute_lc = literal_size
        self.location_counter = initial_absolute_lc
        self.position_counter = 0
        self.conditional_stack = [(True)]
        self.end_statement_processed = False
        self.current_qualifier = None
        self.current_block = "*ABS*"
        self.current_base = 'D'
        self.current_code = 'D'
        self.skip_count = 0
        self.is_defining = None
        self.current_definition_name = None
        self.current_definition_params = []
        self.current_definition_lines = []
        self.current_remote_block_name = None
        self.listing_flags = {
            'B': True, 'C': True, 'D': True, 'E': True, 'F': True, 'G': True,
            'M': True, 'N': True, 'R': True, 'S': True, 'X': True
        }
        self.lc_is_absolute_due_to_loc = False
        self.pre_loc_block_name = None # Reset for Pass 2
        self.current_title = ""
        self.current_ttl_title = ""
        self.last_significant_mnemonic = None
        self.last_significant_mnemonic_lc = None
        self.deferred_force_upper_pending = False # Reset for Pass 2
        self.first_title_processed = False
        self.set_pass(2)
        if self.debug_mode:
            print(f">>> DEBUG LC: Reset for Pass 2")
            print(f"    Literal Block Size = {literal_size}")
            print(f"    Initial Absolute LC set to {initial_absolute_lc:o}")

    def advance_lc(self, bits):
        if bits <= 0: return
        lc_before = self.location_counter
        pc_before = self.position_counter

        new_pc_val = pc_before + bits
        words_advanced_this_call = new_pc_val // 60
        final_pc_val = new_pc_val % 60

        self.location_counter = lc_before + words_advanced_this_call
        self.position_counter = final_pc_val

        if self.pass_number == 1 and words_advanced_this_call > 0:
            block_to_increment_size = self.current_block
            if self.lc_is_absolute_due_to_loc and self.pre_loc_block_name:
                block_to_increment_size = self.pre_loc_block_name

            if block_to_increment_size not in self.block_lcs:
                self.block_lcs[block_to_increment_size] = 0
            self.block_lcs[block_to_increment_size] += words_advanced_this_call

        if self.debug_mode:
            lc_type = "AbsLC" if self.pass_number == 2 or (self.pass_number == 1 and self.lc_is_absolute_due_to_loc) else "RelLC"
            print(f">>> DEBUG LC: advance_lc({bits}) -> words_advanced_this_call={words_advanced_this_call}")
            print(f"    State Before: {lc_type}={lc_before:o}, PC={pc_before}, Block='{self.current_block}', LOC_Abs={self.lc_is_absolute_due_to_loc}, PreLOCBlk={self.pre_loc_block_name}, DeferredPending={self.deferred_force_upper_pending}")
            print(f"    State After : {lc_type}={self.location_counter:o}, PC={self.position_counter}, Block='{self.current_block}', LOC_Abs={self.lc_is_absolute_due_to_loc}, PreLOCBlk={self.pre_loc_block_name}, DeferredPending={self.deferred_force_upper_pending}")
            if self.pass_number == 1:
                target_block_for_size_debug = self.pre_loc_block_name if self.lc_is_absolute_due_to_loc and self.pre_loc_block_name else self.current_block
                print(f"    Block '{target_block_for_size_debug}' size is now: {self.block_lcs.get(target_block_for_size_debug, 0)}")


    def force_upper(self):
        """
        Increments LC if PC is non-zero, and resets PC.
        Returns the number of NOOP bits that would be needed (for listing).
        This method itself *always* increments LC if PC > 0.
        The "deferral" of LC increment for symbol definition is handled by callers.
        """
        noop_bits_needed = 0
        pc_before_force = self.position_counter
        lc_before_force = self.location_counter

        if self.position_counter != 0:
            noop_bits_needed = 60 - self.position_counter
            self.location_counter += 1
            self.position_counter = 0

            if self.pass_number == 1:
                block_to_increment_size = self.current_block
                if self.lc_is_absolute_due_to_loc and self.pre_loc_block_name:
                    block_to_increment_size = self.pre_loc_block_name

                if block_to_increment_size not in self.block_lcs:
                     self.block_lcs[block_to_increment_size] = 0
                self.block_lcs[block_to_increment_size] += 1

            if self.debug_mode:
                lc_type = "AbsLC" if self.pass_number == 2 or (self.pass_number == 1 and self.lc_is_absolute_due_to_loc) else "RelLC"
                print(f">>> DEBUG LC: force_upper (executed from PC={pc_before_force})")
                print(f"    State Before: {lc_type}={lc_before_force:o}, PC={pc_before_force}, Block='{self.current_block}', LOC_Abs={self.lc_is_absolute_due_to_loc}, PreLOCBlk={self.pre_loc_block_name}, DeferredPending={self.deferred_force_upper_pending}")
                print(f"    State After : {lc_type}={self.location_counter:o}, PC={self.position_counter}, Block='{self.current_block}', LOC_Abs={self.lc_is_absolute_due_to_loc}, PreLOCBlk={self.pre_loc_block_name}, DeferredPending={self.deferred_force_upper_pending}") # DeferredPending state doesn't change here
                if self.pass_number == 1:
                    target_block_for_size_debug = self.pre_loc_block_name if self.lc_is_absolute_due_to_loc and self.pre_loc_block_name else self.current_block
                    print(f"    Block '{target_block_for_size_debug}' size is now: {self.block_lcs.get(target_block_for_size_debug, 0)}")
        return noop_bits_needed

    def set_location_counter(self, new_lc, new_pc=0, is_loc_directive: bool = False):
        lc_before = self.location_counter
        pc_before = self.position_counter

        self.location_counter = new_lc
        self.position_counter = new_pc

        if self.position_counter < 0 or self.position_counter >= 60:
             if self.error_reporter: self.error_reporter.add_error(f"Invalid position counter value set: {new_pc}", self.current_line_number, code='F')
             else: print(f"Error: Invalid position counter value set: {new_pc}")
             self.position_counter = 0

        if is_loc_directive: # This method is called by LOC handler
            self.lc_is_absolute_due_to_loc = True
            self.pre_loc_block_name = self.current_block # Store block active before LOC
            if self.debug_mode:
                print(f">>> DEBUG LC: LOC directive. Stored pre_loc_block_name='{self.pre_loc_block_name}'")
            # A LOC directive implies any pending deferred force is now irrelevant for the new LC.
            self.deferred_force_upper_pending = False


        if self.debug_mode:
            lc_type = "AbsLC" if self.pass_number == 2 or (self.pass_number == 1 and self.lc_is_absolute_due_to_loc) else "RelLC"
            print(f">>> DEBUG LC: set_location_counter({new_lc:o}, {new_pc}, is_loc={is_loc_directive})")
            print(f"    State Before: {lc_type}={lc_before:o}, PC={pc_before}, Block={self.current_block}, LOC_Abs={self.lc_is_absolute_due_to_loc}, PreLOCBlk={self.pre_loc_block_name}, DeferredPending={self.deferred_force_upper_pending}")
            print(f"    State After : {lc_type}={self.location_counter:o}, PC={self.position_counter}, Block={self.current_block}, LOC_Abs={self.lc_is_absolute_due_to_loc}, PreLOCBlk={self.pre_loc_block_name}, DeferredPending={self.deferred_force_upper_pending}")

    def switch_block(self, new_block_name: str):
        if self.pass_number == 1 and new_block_name == self.current_block and not self.lc_is_absolute_due_to_loc:
            return

        old_block = self.current_block
        old_lc = self.location_counter
        old_pc = self.position_counter
        old_loc_abs_flag = self.lc_is_absolute_due_to_loc
        old_pre_loc_block = self.pre_loc_block_name

        if self.debug_mode:
            print(f">>> DEBUG LC: switch_block('{new_block_name}') (Pass {self.pass_number})")
            lc_type_old = "AbsLC" if self.pass_number == 2 or (self.pass_number == 1 and old_loc_abs_flag) else "RelLC"
            print(f"    State Before: {lc_type_old}={old_lc:o}, PC={old_pc}, Block='{old_block}', LOC_Abs={old_loc_abs_flag}, PreLOCBlk={old_pre_loc_block}, DeferredPending={self.deferred_force_upper_pending}")

        # Switching block always makes LC relative to the new block (or absolute if *ABS*)
        # and thus clears the "absolute due to LOC" state.
        self.lc_is_absolute_due_to_loc = False
        self.pre_loc_block_name = None # Clear pre_loc_block_name as LOC context ends
        # Switching block also implies any pending deferred force is now irrelevant for the new block's LC.
        self.deferred_force_upper_pending = False


        if self.pass_number == 1:
            if new_block_name not in self.block_lcs:
                self.block_lcs[new_block_name] = 0
                if new_block_name != '*ABS*' and new_block_name not in self.block_order:
                    self.block_order.append(new_block_name)

            self.location_counter = 0 # LC is relative to start of new block
            self.position_counter = 0
            self.current_block = new_block_name

        elif self.pass_number == 2:
            if self.assembler and hasattr(self.assembler, 'block_base_addresses'):
                block_base = self.assembler.block_base_addresses.get(new_block_name)
                if block_base is None:
                    if self.error_reporter: self.error_reporter.add_error(f"Internal: Base address for block '{new_block_name}' not found in Pass 2.", self.current_line_number, 'F')
                    block_base = 0
                self.location_counter = block_base # LC is absolute, set to block's base
                self.position_counter = 0
                self.current_block = new_block_name
            else:
                if self.error_reporter: self.error_reporter.add_error(f"Internal: Cannot access block base addresses during Pass 2 block switch.", self.current_line_number, 'F')
                self.location_counter = 0; self.position_counter = 0; self.current_block = new_block_name

        if self.debug_mode:
            lc_type_new = "AbsLC" if self.pass_number == 2 or (self.pass_number == 1 and self.lc_is_absolute_due_to_loc) else "RelLC"
            print(f"    State After : {lc_type_new}={self.location_counter:o}, PC={self.position_counter}, Block='{self.current_block}', LOC_Abs={self.lc_is_absolute_due_to_loc}, PreLOCBlk={self.pre_loc_block_name}, DeferredPending={self.deferred_force_upper_pending}")
            if self.pass_number == 1:
                 print(f"    Block '{self.current_block}' current size is: {self.block_lcs.get(self.current_block, 0)}")
            elif self.pass_number == 2:
                 print(f"    (Using Base Address: {self.assembler.block_base_addresses.get(new_block_name, 'Not Found'):o})")


    def get_current_lc(self) -> int:
        return self.location_counter

    def get_current_relative_lc(self) -> int:
        if self.pass_number == 1:
            if self.lc_is_absolute_due_to_loc:
                return self.location_counter
            return self.location_counter
        else:
            if self.assembler and hasattr(self.assembler, 'block_base_addresses'):
                 base = self.assembler.block_base_addresses.get(self.current_block, 0)
                 return self.location_counter - base
            return self.location_counter

    def get_current_absolute_lc(self) -> int:
        if self.pass_number == 1:
            if self.lc_is_absolute_due_to_loc:
                return self.location_counter
            if self.current_block == '*ABS*':
                return self.location_counter
            # For named blocks in Pass 1 without LOC, an "absolute LC" is not well-defined yet.
            # This method is less meaningful in that specific P1 context.
            # Returning the relative LC as a placeholder.
            return self.location_counter
        else:
            return self.location_counter

    def get_current_lc_for_listing(self) -> int:
         if self.pass_number == 1:
             return self.location_counter
         else:
             return self.location_counter

    def set_base(self, base_char):
        base_char = base_char.upper()
        if base_char in ['D', 'O', 'M', 'H']:
            self.current_base = base_char
        else:
            if self.error_reporter:
                self.error_reporter.add_error(f"Invalid base specified: {base_char}", self.current_line_number, code='V')
            else:
                print(f"Error: Invalid base specified: {base_char}")

    def set_code(self, code_char):
        code_char = code_char.upper()
        if code_char in ['D', 'A', 'I', 'E']:
             self.current_code = code_char
        else:
            if self.error_reporter:
                self.error_reporter.add_error(f"Invalid code specified: {code_char}", self.current_line_number, code='V')
            else:
                print(f"Error: Invalid code specified: {code_char}")

    def update_listing_flags(self, flags_str: str, turn_on: bool):
        flags_str = flags_str.upper()
        target_flags = []
        if flags_str == 'ALL':
            target_flags = list(self.listing_flags.keys())
        else:
            target_flags = [flag.strip() for flag in flags_str.split(',') if flag.strip()]

        for flag in target_flags:
            if flag in self.listing_flags:
                self.listing_flags[flag] = turn_on
            else:
                if self.error_reporter:
                    self.error_reporter.add_warning(f"Unknown listing flag '{flag}' encountered.", self.current_line_number, code='W')
                else:
                    print(f"Warning: Unknown listing flag '{flag}' encountered.")

# assembler_state.py v1.26
