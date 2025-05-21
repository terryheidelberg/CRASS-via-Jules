# pass2_processing.py v2.57 (already provided, no change for NameError as it was fixed in the version you ran)
# pass1_processing.py v2.60 (for EQU* refinement)
"""
Contains the line processing logic for Pass 1 of the CRASS assembler.
[...]
v2.59:  - Corrected handling of deferred_force_upper_pending at the beginning
          of line processing to ensure EQU* and '-' labels interact correctly
          with a pending force from the previous line.
v2.60:  - Refined EQU* value definition when a deferred force was pending
          from the previous line. It now correctly uses the LC of the word
          containing the special instruction, ignoring PC within that word for value.
          The subsequent deferred force correctly pads from that PC.
"""
import re
import traceback
from typing import TYPE_CHECKING, Dict, Any, Optional

if TYPE_CHECKING:
    from crass import Assembler
    from symbol_table import SymbolTable
    from instruction_table import InstructionTable
    from assembler_state import AssemblerState

from errors import AsmException, ErrorReporter
from expression import ExpressionError, evaluate_expression
from operand_parser import OperandParseError, parse_operands, SINGLE_REG_REGEX
from pseudo_op_handlers import handle_pseudo_op_pass_1, calculate_pseudo_op_size
from assembler_state import AssemblerState, handle_force_upper
from lexer import parse_line

DEFERRED_FORCE_MNEMONICS = {'JP', 'RJ', 'PS', 'XJ'}


def _is_operand_expression_like(operand_str: str) -> bool:
    # ... (same as v2.59)
    if not operand_str:
        return False
    if any(op in operand_str for op in ['+', '-', '*', '/']):
        return True
    temp_op_str = operand_str
    for reg_match in re.finditer(r'[ABX][0-7]', temp_op_str, re.IGNORECASE):
        temp_op_str = temp_op_str.replace(reg_match.group(0), '')
    temp_op_str = temp_op_str.replace(',', '').replace('$', '').strip()
    if temp_op_str and not temp_op_str.isdigit() and not SINGLE_REG_REGEX.match(temp_op_str):
        return True
    return False

def _estimate_instruction_width_pass1(
    assembler: 'Assembler',
    line_num: int,
    mnemonic_upper: str,
    instr_details_list: list,
    operand_str: str
) -> int:
    # ... (same as v2.59, but will need review for arithmetic ops later)
    state = assembler.state
    symbol_table = assembler.symbol_table
    debug_mode = assembler.debug_mode

    def create_temp_state_for_parse():
        temp_state = AssemblerState()
        temp_state.current_base = state.current_base
        temp_state.error_reporter = ErrorReporter()
        temp_state.symbol_table = symbol_table
        temp_state.assembler = assembler
        temp_state.pass_number = 1
        temp_state.debug_mode = debug_mode
        temp_state.current_qualifier = state.current_qualifier
        temp_state.location_counter = state.location_counter
        temp_state.position_counter = state.position_counter
        temp_state.lc_is_absolute_due_to_loc = state.lc_is_absolute_due_to_loc
        return temp_state

    defs_15bit = [d for d in instr_details_list if d.get('width') == 15]
    defs_30bit = [d for d in instr_details_list if d.get('width') == 30]
    defs_60bit = [d for d in instr_details_list if d.get('width') == 60]

    parsed_operands_for_15bit = None
    match_15bit_success = False
    parsed_fmt_15bit = ""
    last_15bit_parse_error = None

    if defs_15bit:
        for instr_def_15 in defs_15bit:
            fmt_attempt = instr_def_15.get('format', "").upper()
            try:
                temp_state_15 = create_temp_state_for_parse()
                parsed_operands_for_15bit = parse_operands(operand_str, fmt_attempt, symbol_table, temp_state_15, line_num, assembler, suppress_undefined_error=True)
                parsed_fmt_15bit = parsed_operands_for_15bit.get('parsed_fmt', "")
                match_15bit_success = True
                if debug_mode: print(f"DEBUG P1 WidthEst L{line_num}: Tentatively matched 15-bit fmt '{fmt_attempt}' (parsed as '{parsed_fmt_15bit}') for {mnemonic_upper} {operand_str}")
                break
            except (OperandParseError, ExpressionError, AsmException) as e:
                last_15bit_parse_error = e
                continue

    if match_15bit_success and defs_30bit:
        implies_K_field_for_15bit = (
            parsed_fmt_15bit == 'K' or
            parsed_fmt_15bit.endswith((',K', '+K', '-K')) or
            (parsed_fmt_15bit.startswith('-') and parsed_fmt_15bit.endswith('J') and not parsed_fmt_15bit.startswith('-X'))
        )
        is_typical_15bit_reg_format = (
            re.fullmatch(r"^[ABX]J,[ABX]K$", parsed_fmt_15bit) or
            re.fullmatch(r"^[ABX]J[+*/-][ABX]K$", parsed_fmt_15bit) or
            re.fullmatch(r"^-[ABX]K[+*/-][ABX]J$", parsed_fmt_15bit) or
            re.fullmatch(r"^[ABX][0-7]$", parsed_fmt_15bit) or
            parsed_fmt_15bit == "-XK" or
            parsed_fmt_15bit == "JK" or
            parsed_fmt_15bit == "BJ,XK" or parsed_fmt_15bit == "XJ,BK"
        )
        is_address_like_K = implies_K_field_for_15bit and parsed_fmt_15bit != "JK"

        if is_address_like_K and not is_typical_15bit_reg_format:
            for instr_def_30 in defs_30bit:
                fmt_attempt_30 = instr_def_30.get('format', "").upper()
                try:
                    temp_state_30 = create_temp_state_for_parse()
                    parse_operands(operand_str, fmt_attempt_30, symbol_table, temp_state_30, line_num, assembler, suppress_undefined_error=True)
                    if debug_mode: print(f"DEBUG P1 WidthEst L{line_num}: Matched 30-bit fmt '{fmt_attempt_30}' for {mnemonic_upper} {operand_str} (preferred over K-like 15-bit)")
                    return 30
                except (OperandParseError, ExpressionError, AsmException):
                    continue
            if debug_mode: print(f"DEBUG P1 WidthEst L{line_num}: K-like 15-bit matched ({parsed_fmt_15bit}), but no 30-bit match. Using 15-bit for {mnemonic_upper} {operand_str}")
            return 15
        elif match_15bit_success:
             if debug_mode: print(f"DEBUG P1 WidthEst L{line_num}: Using matched 15-bit (fmt: {parsed_fmt_15bit}) for {mnemonic_upper} {operand_str}")
             return 15

    if defs_30bit:
        for instr_def_30 in defs_30bit:
            fmt_attempt_30 = instr_def_30.get('format', "").upper()
            try:
                temp_state_30 = create_temp_state_for_parse()
                parse_operands(operand_str, fmt_attempt_30, symbol_table, temp_state_30, line_num, assembler, suppress_undefined_error=True)
                if debug_mode: print(f"DEBUG P1 WidthEst L{line_num}: Matched 30-bit fmt '{fmt_attempt_30}' for {mnemonic_upper} {operand_str} (direct 30-bit attempt)")
                return 30
            except (OperandParseError, ExpressionError, AsmException):
                continue

    if defs_60bit:
        for instr_def_60 in defs_60bit:
            fmt_attempt_60 = instr_def_60.get('format', "").upper()
            try:
                temp_state_60 = create_temp_state_for_parse()
                parse_operands(operand_str, fmt_attempt_60, symbol_table, temp_state_60, line_num, assembler, suppress_undefined_error=True)
                if debug_mode: print(f"DEBUG P1 WidthEst L{line_num}: Matched 60-bit fmt '{fmt_attempt_60}' for {mnemonic_upper} {operand_str}")
                return 60
            except (OperandParseError, ExpressionError, AsmException):
                continue

    if match_15bit_success:
        if debug_mode: print(f"DEBUG P1 WidthEst L{line_num}: Reverting to 15-bit match (fmt: {parsed_fmt_15bit}) for {mnemonic_upper} {operand_str} as only viable option.")
        return 15

    if instr_details_list:
        default_w = instr_details_list[0].get('width', 15)
        if default_w not in (15,30,60): default_w = 15
        if debug_mode: print(f"DEBUG P1 WidthEst L{line_num}: Fallback to width {default_w} (no format match, using first def) for {mnemonic_upper} {operand_str}. Last 15-bit error: {last_15bit_parse_error}")
        return default_w

    if debug_mode: print(f"DEBUG P1 WidthEst L{line_num}: Ultimate fallback to 15-bit (no defs found at all) for {mnemonic_upper} {operand_str}")
    return 15


def process_line_pass_1(
    state: 'AssemblerState',
    symbol_table: 'SymbolTable',
    instruction_table: 'InstructionTable',
    error_reporter: 'ErrorReporter',
    macro_definitions: Dict[str, Any],
    micro_definitions: Dict[str, str],
    assembler: 'Assembler',
    line_num: int,
    parsed: Dict[str, Any]
) -> bool:
    debug_mode = state.debug_mode

    label = parsed['label']
    mnemonic = parsed['opcode'].upper() if parsed['opcode'] else None
    operand_str = parsed['operand_str'] if parsed['operand_str'] is not None else ""

    lc_at_line_start_processing = state.location_counter
    pc_at_line_start_processing = state.position_counter
    deferred_force_was_pending_at_line_start = state.deferred_force_upper_pending

    current_mnemonic_for_tracking = mnemonic

    # --- RMT Block Collection ---
    if state.current_remote_block_name: # ... (same as v2.59)
        mnemonic_in_remote = parsed['opcode'].upper() if parsed['opcode'] else None
        operand_field_for_rmt_check = parsed.get('operand_str')
        if mnemonic_in_remote == "RMT" and not (operand_field_for_rmt_check or "").strip():
            if debug_mode: print(f"DEBUG P1 RMT: L{line_num} Ending remote block '{state.current_remote_block_name}'")
            state.current_remote_block_name = None
        elif mnemonic_in_remote == "END":
            if debug_mode: print(f"DEBUG P1 RMT: L{line_num} END encountered, ending remote block '{state.current_remote_block_name}'")
            assembler.remote_blocks[state.current_remote_block_name].append(parsed.copy())
            state.current_remote_block_name = None
        else:
            if debug_mode: print(f"DEBUG P1 RMT: L{line_num} Collecting line for remote block '{state.current_remote_block_name}': {parsed['original']}")
            assembler.remote_blocks[state.current_remote_block_name].append(parsed.copy())
        return True

    # --- Macro/Opdef Definition Collection ---
    if state.is_defining: # ... (same as v2.59)
        mnemonic_in_def = parsed['opcode'].upper() if parsed['opcode'] else None
        if mnemonic_in_def == "ENDM":
            if state.current_definition_name:
                 if state.is_defining in ("MACRO", "OPDEF"):
                      macro_definitions[state.current_definition_name] = {
                           'type': state.is_defining, 'params': state.current_definition_params,
                           'body': state.current_definition_lines, 'defined_line': line_num
                      }
                      if debug_mode: print(f"DEBUG P1 MACRO/OPDEF: Defined '{state.current_definition_name}' with {len(state.current_definition_lines)} lines.")
            else: error_reporter.add_error(f"{mnemonic_in_def} encountered outside of a named definition block", line_num, code='S')
            state.is_defining = None; state.current_definition_name = None
            state.current_definition_params = []; state.current_definition_lines = []
        else:
            if state.is_defining in ("MACRO", "OPDEF"): state.current_definition_lines.append(parsed['original'])
        return True

    # --- Handle Deferred Force from PREVIOUS line ---
    lc_for_symbol_def = lc_at_line_start_processing
    pc_for_symbol_def = pc_at_line_start_processing # This is the PC *within* the word of lc_for_symbol_def

    is_equ_star_current = (mnemonic == "EQU" and operand_str.strip() == '*')
    is_negating_label_current = (label == '-')

    if deferred_force_was_pending_at_line_start:
        if is_negating_label_current:
            if debug_mode: print(f">>> DEBUG LC P1: L{line_num} PREVIOUS deferred force negated by '-' label.")
            state.deferred_force_upper_pending = False
            # lc_for_symbol_def remains lc_at_line_start_processing
        elif not is_equ_star_current: # Not EQU* and not negating: execute deferred force now
            if debug_mode: print(f">>> DEBUG LC P1: L{line_num} Executing PREVIOUS deferred force (before current line processing).")
            state.location_counter = lc_at_line_start_processing
            state.position_counter = pc_at_line_start_processing
            handle_force_upper(state, None, error_reporter, line_num) # Consumes the flag
            lc_for_symbol_def = state.location_counter
            pc_for_symbol_def = state.position_counter # Should be 0 now
        elif is_equ_star_current and debug_mode:
            print(f">>> DEBUG LC P1: L{line_num} PREVIOUS deferred force pending, current is EQU*. Symbol will use pre-force LC={lc_for_symbol_def:o}, PC={pc_for_symbol_def}.")

    # --- Handle Comment-Only Lines (with potential label) ---
    if parsed['is_comment_line']:
        if parsed['label'] and not is_negating_label_current:
             block_for_comment_label = state.pre_loc_block_name if state.lc_is_absolute_due_to_loc and state.pre_loc_block_name else state.current_block
             sym_type = 'absolute' if state.lc_is_absolute_due_to_loc or block_for_comment_label == '*ABS*' else 'relocatable'
             value_for_comment_label = lc_for_symbol_def # Uses LC after any prev line's deferred force
             if debug_mode:
                 print(f">>> DEBUG LC: L{line_num} Define Label (Comment): '{parsed['label']}'")
                 print(f"    Value = {value_for_comment_label:o} (Block: {block_for_comment_label})")
                 print(f"    From SymbolDef LC={lc_for_symbol_def:o}, PC={pc_for_symbol_def}, LOC_Abs={state.lc_is_absolute_due_to_loc}")
             try:
                  # For a label on a comment line, it refers to the start of the word.
                  if pc_for_symbol_def != 0: error_reporter.add_warning(f"Label '{parsed['label']}' on comment line is not word-aligned (PC={pc_for_symbol_def})", line_num, code='A')
                  attrs_comment_label = {'type': sym_type, 'redefinable': False, 'block': block_for_comment_label}
                  symbol_table.define(parsed['label'], value_for_comment_label, line_num, attrs_comment_label, state.current_qualifier)
             except AsmException as e: error_reporter.add_error(str(e), line_num, code=e.code); return False
        return True

    # --- EQU * Definition ---
    if is_equ_star_current:
        if not label: error_reporter.add_error("EQU * requires a label", line_num, code='S'); return False
        
        # If a deferred force was pending from the previous line (and not negated by '-'),
        # EQU * takes the LC of the word containing the special op.
        value_for_equ_star = lc_at_line_start_processing if deferred_force_was_pending_at_line_start and not is_negating_label_current else lc_for_symbol_def

        if debug_mode and deferred_force_was_pending_at_line_start and not is_negating_label_current:
            print(f"    EQU* L{line_num}: Using LC of prev special op's word: {value_for_equ_star:o} (was {lc_at_line_start_processing:o} PC {pc_at_line_start_processing})")
        
        block_for_equ_star = state.pre_loc_block_name if state.lc_is_absolute_due_to_loc and state.pre_loc_block_name else state.current_block
        type_for_equ_star = 'absolute' if state.lc_is_absolute_due_to_loc or block_for_equ_star == '*ABS*' else 'relocatable'
        
        if debug_mode:
            print(f"    EQU* L{line_num}: Defining '{label}' with Value={value_for_equ_star:o} (Effective PC for def is 0 relative to this word)")
        attrs_equ_star = {'type': type_for_equ_star, 'redefinable': False, 'block': block_for_equ_star, 'equ_star': True}
        if not symbol_table.define(label, value_for_equ_star, line_num, attrs_equ_star, state.current_qualifier): return False

        if deferred_force_was_pending_at_line_start and not is_negating_label_current:
            if debug_mode: print(f">>> DEBUG LC P1: L{line_num} Executing PREVIOUS deferred force AFTER EQU *.")
            state.location_counter = lc_at_line_start_processing # Word of the special op
            state.position_counter = pc_at_line_start_processing # PC *within* that word
            handle_force_upper(state, None, error_reporter, line_num) # Consumes the flag
            
        state.last_significant_mnemonic = "EQU"; state.last_significant_mnemonic_lc = value_for_equ_star
        parsed['pass1_width_estimate'] = 0
        return True

    # --- LC/PC for the content of the current line ---
    # Starts from lc_for_symbol_def/pc_for_symbol_def, which is already post-any-previous-deferred-force
    # (unless current was EQU* or negating label, in which case deferred force is handled above or still pending for EQU*)
    lc_for_line_content = lc_for_symbol_def
    pc_for_line_content = pc_for_symbol_def

    # --- Conditional Assembly Directives ---
    # ... (same as v2.59, using lc_for_line_content, pc_for_line_content)
    label_defined_by_pseudo = (mnemonic in ('EQU', '=', 'SET', 'END', 'ENDL', 'IDENT', 'MACRO', 'OPDEF', 'MICRO', 'LOC', 'RMT', 'HERE'))
    is_conditional_directive = False
    if mnemonic and (mnemonic.startswith("IF") or mnemonic in ("ELSE", "ENDIF")):
        is_conditional_directive = True
        if not handle_pseudo_op_pass_1(assembler, line_num, mnemonic, operand_str, label, label_defined_by_pseudo, lc_for_line_content, pc_for_line_content):
            return False

    if not state.conditional_stack[-1]: # Skip if conditional is false
        # ... (same as v2.59, using lc_for_line_content, pc_for_line_content for skipped label)
        if label and not is_conditional_directive and not label_defined_by_pseudo and not is_negating_label_current:
             block_for_skipped_label = state.pre_loc_block_name if state.lc_is_absolute_due_to_loc and state.pre_loc_block_name else state.current_block
             sym_type = 'absolute' if state.lc_is_absolute_due_to_loc or block_for_skipped_label == '*ABS*' else 'relocatable'
             value_for_skipped_label = lc_for_line_content
             if debug_mode:
                 print(f">>> DEBUG LC: L{line_num} Define Label (Skipped): '{label}' Value = {value_for_skipped_label:o}")
             try:
                  if pc_for_line_content != 0: error_reporter.add_warning(f"Label '{label}' on skipped line is not word-aligned (PC={pc_for_line_content})", line_num, code='A')
                  attrs_skipped_label = {'type': sym_type, 'redefinable': False, 'block': block_for_skipped_label}
                  if not symbol_table.define(label, value_for_skipped_label, line_num, attrs_skipped_label, state.current_qualifier): return False
             except AsmException as e: error_reporter.add_error(str(e), line_num, code=e.code); return False
        return True

    # --- Process Block Control Pseudo-ops ---
    # ... (same as v2.59, using and updating lc_for_line_content, pc_for_line_content)
    state_changed_by_block_pseudo = False
    if mnemonic in ('USE', 'ABS', 'REL', 'LOC'):
        state.location_counter = lc_for_line_content
        state.position_counter = pc_for_line_content
        if not handle_pseudo_op_pass_1(assembler, line_num, mnemonic, operand_str, label, label_defined_by_pseudo, state.location_counter, state.position_counter):
             return False
        state_changed_by_block_pseudo = True
        lc_for_line_content = state.location_counter
        pc_for_line_content = state.position_counter

    # --- Label Definition (if not already handled) ---
    lc_for_this_label_def = lc_for_line_content
    pc_for_this_label_def = pc_for_line_content
    # ... (label alignment logic same as v2.59, using and updating lc_for_this_label_def, pc_for_this_label_def)
    label_needs_force_upper = False
    if label and not label_defined_by_pseudo and not (mnemonic == "LOC" and label) and not is_negating_label_current:
        if pc_for_this_label_def != 0: label_needs_force_upper = True
    elif label == '+':
        if pc_for_this_label_def != 0: label_needs_force_upper = True

    if label_needs_force_upper:
        state.location_counter = lc_for_this_label_def
        state.position_counter = pc_for_this_label_def
        handle_force_upper(state, None, error_reporter, line_num)
        lc_for_this_label_def = state.location_counter
        pc_for_this_label_def = state.position_counter
        lc_for_line_content = lc_for_this_label_def # Update content start if label forced alignment
        pc_for_line_content = pc_for_this_label_def

    if label and not label_defined_by_pseudo and not (mnemonic == "LOC" and label) and not is_negating_label_current:
        # ... (define label using lc_for_this_label_def, pc_for_this_label_def - same as v2.59)
        symbol_value_for_label = lc_for_this_label_def
        if pc_for_this_label_def != 0:
             error_reporter.add_warning(f"Label '{label}' defined at non-zero PC ({pc_for_this_label_def}) after alignment attempt.", line_num, code='A')
        label_block_context = state.pre_loc_block_name if state.lc_is_absolute_due_to_loc and state.pre_loc_block_name else state.current_block
        sym_type = 'absolute' if state.lc_is_absolute_due_to_loc or label_block_context == '*ABS*' else 'relocatable'
        try:
            attrs_for_label = {'type': sym_type, 'redefinable': False, 'block': label_block_context}
            if debug_mode:
                print(f">>> DEBUG LC: L{line_num} Define Label (Regular): '{label}' Value = {symbol_value_for_label:o} (Block: {label_block_context}, Type: {sym_type}, PC={pc_for_this_label_def})")
            if not symbol_table.define(label, symbol_value_for_label, line_num, attrs_for_label, state.current_qualifier): return False
        except AsmException as e: error_reporter.add_error(str(e), line_num, code=e.code); return False


    # --- Process Opcode ---
    if is_conditional_directive: return True # Already handled

    if mnemonic:
        state.location_counter = lc_for_line_content # Set state to where content processing begins
        state.position_counter = pc_for_line_content
        # ... (RMT, HERE, MACRO, OPDEF, MICRO handlers same as v2.59) ...
        if mnemonic == "RMT":
            rmt_operand = operand_str.split('.')[0].split('*')[0].strip().upper()
            if rmt_operand:
                if state.current_remote_block_name: error_reporter.add_error(f"Nested RMT '{rmt_operand}' not allowed.", line_num, 'S'); return False
                state.current_remote_block_name = rmt_operand
                assembler.remote_blocks[state.current_remote_block_name] = []
            return True
        if mnemonic == "HERE":
            here_operand = operand_str.split('.')[0].split('*')[0].strip().upper()
            if not here_operand: error_reporter.add_error("HERE requires a label operand.", line_num, 'S'); return False
            if here_operand not in assembler.remote_blocks: error_reporter.add_error(f"HERE label '{here_operand}' references an undefined RMT block.", line_num, 'U'); return False
            lines_to_expand = assembler.remote_blocks.pop(here_operand)
            for stored_parsed_line_dict in lines_to_expand:
                state.line_start_address = state.location_counter; state.line_start_position_bits = state.position_counter
                expanded_line_parsed = stored_parsed_line_dict.copy(); expanded_line_parsed['is_remote_expansion'] = True
                expanded_line_parsed['original_line_num_for_error'] = line_num
                if not process_line_pass_1(state, symbol_table, instruction_table, error_reporter, macro_definitions, micro_definitions, assembler, line_num, expanded_line_parsed): return False
            return True
        if mnemonic in ("MACRO", "OPDEF", "MICRO"):
             state.is_defining = mnemonic; def_name = None; params_str = ""; micro_body_raw = ""
             if mnemonic == "MICRO":
                  def_name = label
                  if not def_name: error_reporter.add_error(f"MICRO requires a label", line_num, code='S'); state.is_defining = None; return False
                  micro_body_raw = operand_str
             elif label: def_name = label; params_str = operand_str if operand_str is not None else ""
             else:
                  parts = re.split(r'[,\s]+', operand_str, 1) if operand_str else []
                  if parts: def_name = parts[0]; params_str = parts[1] if len(parts) > 1 else ""
                  elif operand_str: def_name = operand_str
             if not def_name: error_reporter.add_error(f"{mnemonic} requires a name", line_num, code='S'); state.is_defining = None; return False
             state.current_definition_name = def_name.upper()
             state.current_definition_params = [p.strip().upper() for p in params_str.split(',') if p.strip()] if mnemonic != "MICRO" else []
             state.current_definition_lines = []
             if mnemonic == "MICRO":
                  micro_body_raw_no_comment = micro_body_raw.split('.')[0].split('*')[0].strip()
                  assembler.micro_definitions[state.current_definition_name] = micro_body_raw_no_comment
                  state.is_defining = None; state.current_definition_name = None; state.current_definition_params = []
             return True

        if not state_changed_by_block_pseudo:
            pass1_width_estimate = 0
            if instruction_table.is_instruction(mnemonic):
                instr_details = instruction_table.get_instruction_details(mnemonic)
                if instr_details:
                    pass1_width_estimate = _estimate_instruction_width_pass1(assembler, line_num, mnemonic, instr_details, operand_str)
            # Align for instruction/data pseudo-op if needed
            # This uses state.location_counter which is now lc_for_line_content (potentially after label alignment)
            if pass1_width_estimate > 0: # Instruction
                force_before_instr = False
                # ... (instruction pre-alignment logic from v2.59, using current state.lc/pc) ...
                if mnemonic in ("PS", "XJ"):
                    if state.position_counter not in (0,30) : force_before_instr = True
                elif pass1_width_estimate == 30 and state.position_counter not in (0, 30): force_before_instr = True
                elif pass1_width_estimate == 60 and state.position_counter != 0: force_before_instr = True
                elif state.position_counter != 0 and (state.position_counter + pass1_width_estimate > 60):
                    force_before_instr = True
                if force_before_instr:
                    if debug_mode: print(f">>> DEBUG LC P1: L{line_num} Pre-aligning for instruction '{mnemonic}' from PC={state.position_counter}")
                    handle_force_upper(state, None, error_reporter, line_num)
            elif mnemonic in ("DATA", "CON", "DIS", "BSS", "BSSZ", "LIT"): # Data pseudo-ops
                if state.position_counter != 0 and mnemonic not in ("VFD", "DIS"):
                    if debug_mode: print(f">>> DEBUG LC P1: L{line_num} Pre-aligning for data pseudo-op '{mnemonic}' from PC={state.position_counter}")
                    handle_force_upper(state, None, error_reporter, line_num)
                if mnemonic == "VFD" and label == '-':
                    if state.position_counter % 15 != 0:
                        bits_to_pad = 15 - (state.position_counter % 15)
                        if debug_mode: print(f">>> DEBUG LC P1: L{line_num} VFD with '-' label: padding {bits_to_pad} bits from PC={state.position_counter}")
                        state.advance_lc(bits_to_pad)

            lc_before_opcode_processing = state.location_counter
            pc_before_opcode_processing = state.position_counter

            if instruction_table.is_pseudo_op(mnemonic):
                # ... (same as v2.59)
                result = handle_pseudo_op_pass_1(assembler, line_num, mnemonic, operand_str, label, label_defined_by_pseudo, lc_before_opcode_processing, pc_before_opcode_processing)
                if mnemonic in ("DATA", "CON", "DIS", "VFD", "BSS", "BSSZ"):
                    state.last_significant_mnemonic = mnemonic; state.last_significant_mnemonic_lc = lc_before_opcode_processing
                return result
            if mnemonic in macro_definitions: # ... (same as v2.59)
                 error_reporter.add_warning(f"MACRO call '{mnemonic}' expansion not implemented in Pass 1 for sizing.", line_num, code='W')
                 parsed['pass1_width_estimate'] = 0
                 state.last_significant_mnemonic = mnemonic; state.last_significant_mnemonic_lc = lc_before_opcode_processing
                 return True
            if (mnemonic + 'Q') in macro_definitions and macro_definitions[mnemonic + 'Q'].get('type') == 'OPDEF': # ... (same as v2.59)
                 opdef_info = macro_definitions[mnemonic + 'Q']
                 opdef_body_first_line = opdef_info['body'][0] if opdef_info['body'] else ""
                 temp_opdef_operand_str = parsed.get('operand_str', ''); opdef_params = opdef_info.get('params', [])
                 parsed_opdef_line = parse_line(opdef_body_first_line, 0)
                 expanded_mnemonic = parsed_opdef_line.get('opcode'); expanded_operand = parsed_opdef_line.get('operand_str', "")
                 if opdef_params and temp_opdef_operand_str and expanded_operand:
                     for i, param_name in enumerate(opdef_params):
                         if expanded_operand.strip().upper() == param_name:
                             actual_args = [a.strip() for a in temp_opdef_operand_str.split(',')];
                             if i < len(actual_args): expanded_operand = actual_args[i]
                             break
                 width = 15
                 if expanded_mnemonic:
                     expanded_mnemonic_upper = expanded_mnemonic.upper()
                     opdef_instr_details = instruction_table.get_instruction_details(expanded_mnemonic_upper)
                     if opdef_instr_details: width = _estimate_instruction_width_pass1(assembler, line_num, expanded_mnemonic_upper, opdef_instr_details, expanded_operand)
                     else: error_reporter.add_warning(f"OPDEF '{mnemonic}' expands to unknown mnemonic '{expanded_mnemonic}'. Assuming 15-bit.", line_num, code='W')
                 else: error_reporter.add_warning(f"OPDEF '{mnemonic}' body is empty or invalid. Assuming 15-bit.", line_num, code='W')
                 parsed['pass1_width_estimate'] = width
                 state.advance_lc(width)
                 state.last_significant_mnemonic = mnemonic; state.last_significant_mnemonic_lc = lc_before_opcode_processing
                 return True

            instr_details = instruction_table.get_instruction_details(mnemonic)
            if instr_details:
                if pass1_width_estimate == 0:
                    pass1_width_estimate = _estimate_instruction_width_pass1(assembler, line_num, mnemonic, instr_details, operand_str)
                state.advance_lc(pass1_width_estimate)
                parsed['pass1_width_estimate'] = pass1_width_estimate
                state.last_significant_mnemonic = mnemonic; state.last_significant_mnemonic_lc = lc_before_opcode_processing
                if state.position_counter != 0:
                    base_mnemonic_for_force = instruction_table.get_base_mnemonic(mnemonic)
                    if base_mnemonic_for_force in DEFERRED_FORCE_MNEMONICS:
                        state.deferred_force_upper_pending = True
                        if debug_mode: print(f">>> DEBUG LC P1: L{line_num} Mnemonic {mnemonic} (Base: {base_mnemonic_for_force}) SET deferred_force_upper_pending because PC={state.position_counter}.")
                else:
                    state.deferred_force_upper_pending = False
                return True
            else: # Unknown mnemonic
                if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(f"Unknown mnemonic '{mnemonic}'", line_num, code='U')
                state.last_significant_mnemonic = mnemonic; state.last_significant_mnemonic_lc = lc_before_opcode_processing
                return False
        elif state_changed_by_block_pseudo:
             state.last_significant_mnemonic = mnemonic
             state.last_significant_mnemonic_lc = state.location_counter
             return True

    elif label and not mnemonic and not is_negating_label_current: # Label only line
        state.last_significant_mnemonic = None
        # If deferred_force_was_pending_at_line_start was true, it means it was for *this* label line.
        # Since there's no opcode, the deferred force effectively completes the previous word.
        # The label itself used the pre-force LC.
        if deferred_force_was_pending_at_line_start:
            if debug_mode: print(f">>> DEBUG LC P1: L{line_num} Label-only line. Executing pending PREVIOUS deferred force.")
            state.location_counter = lc_at_line_start_processing
            state.position_counter = pc_at_line_start_processing
            handle_force_upper(state, None, error_reporter, line_num)
        return True
    elif not label and not mnemonic and not parsed['operand_str']: # Blank line
        if deferred_force_was_pending_at_line_start: # From a previous line
            if debug_mode: print(f">>> DEBUG LC P1: L{line_num} Blank line encountered. Executing pending PREVIOUS deferred force.")
            state.location_counter = lc_at_line_start_processing
            state.position_counter = pc_at_line_start_processing
            handle_force_upper(state, None, error_reporter, line_num)
        return True
    elif not label and not mnemonic and parsed['operand_str']: # Comment starting in operand field
        if not parsed['operand_str'].startswith(('.', '*')):
             error_reporter.add_error("Missing mnemonic", line_num, code='S'); return False
        if deferred_force_was_pending_at_line_start: # From a previous line
            if debug_mode: print(f">>> DEBUG LC P1: L{line_num} Comment-only line. Executing pending PREVIOUS deferred force.")
            state.location_counter = lc_at_line_start_processing
            state.position_counter = pc_at_line_start_processing
            handle_force_upper(state, None, error_reporter, line_num)
        return True

    if current_mnemonic_for_tracking and \
       current_mnemonic_for_tracking not in ("EQU", "SET", "RMT", "HERE") and \
       not is_conditional_directive and \
       not state_changed_by_block_pseudo:
        state.last_significant_mnemonic = current_mnemonic_for_tracking
        state.last_significant_mnemonic_lc = state.location_counter

    if not error_reporter.has_error_on_line(line_num) and not parsed.get('is_remote_expansion'):
        if not (label == '-' and not mnemonic and not operand_str):
            error_reporter.add_error(f"Internal error: Unhandled line structure L{line_num}: '{parsed['original']}'", line_num, code='F')
            return False
    return True

# pass1_processing.py v2.60
