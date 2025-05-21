# pass2_processing.py v2.58
"""
Contains the line processing logic for Pass 2 of the CRASS assembler.
Moved from pass_logic.py v2.0 as part of refactoring in v2.0.
[...]
v2.57:  - Fixed NameError for is_solely_equ_star by defining it.
        - Mirrored Pass 1 v2.59 logic for EQU* value when deferred force
          was pending from previous line.
v2.58:  - Fully mirrored Pass 1 v2.60 logic for handling deferred force
          from previous line, especially its interaction with EQU* and '-' labels,
          ensuring correct LC context for symbol definitions and content generation.
"""
import re
import traceback
from typing import TYPE_CHECKING, Dict, Any, Optional, List, Tuple

if TYPE_CHECKING:
    from crass import Assembler
    from output_generator import OutputGenerator

from errors import AsmException
from expression import ExpressionError, evaluate_expression
from operand_parser import OperandParseError
from pseudo_op_handlers import handle_pseudo_op_pass_2
from instruction_assembler import assemble_instruction
from output_generator import (
    PSEUDO_VALUE_WIDTH_INDICATOR, EQU_STAR_LC_INDICATOR, SPACE_COUNT_INDICATOR,
    PSEUDO_STRING_VALUE_INDICATOR
)
from assembler_state import handle_force_upper
from lexer import parse_line

DEFERRED_FORCE_MNEMONICS = {'JP', 'RJ', 'PS', 'XJ'}


def process_line_pass_2(
    state: 'AssemblerState',
    output_generator: 'OutputGenerator',
    assembler: 'Assembler',
    line_num: int,
    parsed: Dict[str, Any]
) -> bool:
    debug_mode = state.debug_mode
    symbol_table = assembler.symbol_table
    instruction_table = assembler.instruction_table
    error_reporter = assembler.error_reporter
    macro_definitions = assembler.macro_definitions

    if not output_generator:
        if error_reporter: error_reporter.add_error("Output generator not initialized", line_num, code='F')
        return False

    original_source_line_text = parsed['original']
    error_code_for_line = error_reporter.get_error_code_for_line(line_num)

    mnemonic = parsed['opcode'].upper() if parsed['opcode'] else None
    operand_str_from_parser = parsed['operand_str'] if parsed['operand_str'] is not None else ""
    label = parsed['label']

    lc_at_line_start_processing = state.location_counter
    pc_at_line_start_processing = state.position_counter
    deferred_force_was_pending_at_line_start = state.deferred_force_upper_pending

    is_solely_equ_star = (mnemonic == "EQU" and operand_str_from_parser.strip() == '*')
    is_negating_label_current = (label == '-')

    # --- RMT Block Skipping, Macro/Opdef Definition Skipping ---
    # ... (same as v2.57) ...
    if state.current_remote_block_name:
        parsed_operand_str_for_rmt_check = parsed.get('operand_str')
        if mnemonic == "RMT" and not (parsed_operand_str_for_rmt_check or "").strip():
            if debug_mode: print(f"DEBUG P2 RMT: L{line_num} Ending remote block collection state for '{state.current_remote_block_name}'")
            state.current_remote_block_name = None
        elif mnemonic == "END":
            if debug_mode: print(f"DEBUG P2 RMT: L{line_num} END encountered, ending remote block collection state for '{state.current_remote_block_name}'")
            state.current_remote_block_name = None
        else:
            if debug_mode: print(f"DEBUG P2 RMT: L{line_num} Skipping line within RMT block '{state.current_remote_block_name}': {original_source_line_text}")
            output_generator.write_listing_line(
                line_num, None, 0, None, original_source_line_text, error_code_for_line, is_skipped=True, state=state,
                pseudo_op_mnemonic=mnemonic
            )
            return True
        output_generator.write_listing_line(
            line_num, None, 0, None, original_source_line_text, error_code_for_line, state=state,
            pseudo_op_mnemonic=mnemonic
        )
        return True

    if state.is_defining:
        mnemonic_in_def = parsed['opcode'].upper() if parsed['opcode'] else None
        if mnemonic_in_def == "ENDM":
            if state.is_defining: state.is_defining = None; state.current_definition_name = None
        output_generator.write_listing_line(
            line_num, None, 0, None, original_source_line_text, error_code_for_line, state=state,
            pseudo_op_mnemonic=mnemonic_in_def or mnemonic
        )
        return True

    if mnemonic in ("MACRO", "OPDEF"):
        state.is_defining = mnemonic
        def_name = parsed.get('label')
        if not def_name:
            op_str = parsed.get('operand_str', ''); parts = re.split(r'[,\s]+', op_str, 1) if op_str else []
            if parts: def_name = parts[0]
            elif op_str: def_name = op_str
        state.current_definition_name = def_name.upper() if def_name else "???"
        output_generator.write_listing_line(
            line_num, None, 0, None, original_source_line_text, error_code_for_line, state=state, pseudo_op_mnemonic=mnemonic
        )
        return True
    if mnemonic == "MICRO":
         output_generator.write_listing_line(
             line_num, None, 0, None, original_source_line_text, error_code_for_line, state=state, pseudo_op_mnemonic=mnemonic
        )
         return True

    if state.skip_count > 0 and mnemonic != 'SKIP':
        state.skip_count -= 1
        output_generator.write_listing_line(
            line_num, lc_at_line_start_processing, pc_at_line_start_processing,
            None, original_source_line_text, error_code_for_line, is_skipped=True, state=state,
            pseudo_op_mnemonic=mnemonic
        )
        if state.end_statement_processed: return True
        return True

    if mnemonic == "RMT":
        rmt_operand = operand_str_from_parser.strip().upper()
        if rmt_operand:
            if state.current_remote_block_name: error_reporter.add_error(f"Nested RMT '{rmt_operand}' not allowed.", line_num, 'S')
            else:
                state.current_remote_block_name = rmt_operand
                if debug_mode: print(f"DEBUG P2 RMT: L{line_num} Entering skip mode for remote block definition '{state.current_remote_block_name}'")
        output_generator.write_listing_line(
            line_num, None, 0, None, original_source_line_text, error_code_for_line, state=state, pseudo_op_mnemonic=mnemonic
        )
        return True

    # --- Handle Deferred Force from PREVIOUS line ---
    lc_for_current_line_processing = lc_at_line_start_processing
    pc_for_current_line_processing = pc_at_line_start_processing

    if deferred_force_was_pending_at_line_start:
        if is_negating_label_current:
            if debug_mode: print(f">>> DEBUG LC P2: L{line_num} PREVIOUS deferred force negated by '-' label.")
            state.deferred_force_upper_pending = False
        elif not is_solely_equ_star:
            if debug_mode: print(f">>> DEBUG LC P2: L{line_num} Executing PREVIOUS deferred force (before current line processing).")
            state.location_counter = lc_at_line_start_processing
            state.position_counter = pc_at_line_start_processing
            handle_force_upper(state, output_generator, error_reporter, line_num)
            lc_for_current_line_processing = state.location_counter
            pc_for_current_line_processing = state.position_counter
        elif is_solely_equ_star and debug_mode:
             print(f">>> DEBUG LC P2: L{line_num} PREVIOUS deferred force pending, current is EQU*. Symbol will use pre-force LC={lc_for_current_line_processing:o}, PC={pc_for_current_line_processing}.")


    # --- Handle Comment-Only Lines (after potential deferred force from prev line) ---
    if parsed['is_comment_line']:
        output_generator.write_listing_line( line_num, None, 0, None, original_source_line_text, error_code_for_line, state=state )
        return True

    # --- EQU * Definition ---
    if is_solely_equ_star:
        # The LC for EQU* listing is its defined value from Pass 1.
        # The actual state.location_counter for subsequent lines is handled by the deferred force logic.
        listing_data_for_equ_star = handle_pseudo_op_pass_2(assembler, line_num, mnemonic, operand_str_from_parser, label)
        output_generator.write_listing_line(
            line_num, None, 0, listing_data_for_equ_star, original_source_line_text,
            error_code_for_line, state=state, pseudo_op_mnemonic=mnemonic
        )
        if deferred_force_was_pending_at_line_start and not is_negating_label_current:
            if debug_mode: print(f">>> DEBUG LC P2: L{line_num} Executing PREVIOUS deferred force AFTER EQU *.")
            state.location_counter = lc_for_current_line_processing # LC of the special op's word
            state.position_counter = pc_for_current_line_processing # PC within that word
            handle_force_upper(state, output_generator, error_reporter, line_num)
        return True

    # --- Conditional Assembly Directives ---
    listing_data_for_conditional = None
    if mnemonic and (mnemonic.startswith("IF") or mnemonic in ("ELSE", "ENDIF")):
        # ... (same as v2.57, using lc_for_current_line_processing)
        handle_pseudo_op_pass_2(assembler, line_num, mnemonic, operand_str_from_parser, label)
        if mnemonic.startswith("IF") and not mnemonic in ("IFC", "IFCP", "IFPP"):
            try:
                cond_parts = operand_str_from_parser.split(','); expr_to_list = cond_parts[0]
                simple_expr_keywords = ("SET", "-SET", "ABS", "-ABS", "REL", "-REL", "DEF", "-DEF", "REG", "-REG", "MIC", "-MIC", "CP", "PP", "TPA", "TPB", "TPC", "TPD", "TPE", "TPF")
                is_simple_keyword_if = False
                if len(cond_parts) > 0 and cond_parts[0].upper() in simple_expr_keywords:
                    is_simple_keyword_if = True
                if not is_simple_keyword_if:
                    val, vtype, _ = evaluate_expression(expr_to_list, symbol_table, state, line_num, assembler)
                    if isinstance(val, int): listing_data_for_conditional = [(val, PSEUDO_VALUE_WIDTH_INDICATOR)]
            except: pass
        output_generator.write_listing_line( line_num, lc_for_current_line_processing, pc_for_current_line_processing, listing_data_for_conditional, original_source_line_text, error_code_for_line, is_skipped=False, state=state, pseudo_op_mnemonic=mnemonic)
        return True

    if not state.conditional_stack[-1]: # Skip if conditional is false
        # ... (same as v2.57, using lc_for_current_line_processing)
        output_generator.write_listing_line( line_num, lc_for_current_line_processing, pc_for_current_line_processing, None, original_source_line_text, error_code_for_line, is_skipped=True, state=state, pseudo_op_mnemonic=mnemonic)
        if state.end_statement_processed: return True
        return True

    # --- HERE expansion ---
    if mnemonic == "HERE": # ... (same as v2.57, using lc_for_current_line_processing)
        here_operand = operand_str_from_parser.strip().upper()
        if not here_operand:
            output_generator.write_listing_line(line_num, None, 0, None, original_source_line_text, error_code_for_line or "S", state=state, pseudo_op_mnemonic=mnemonic); return True
        if here_operand not in assembler.remote_blocks:
            output_generator.write_listing_line(line_num, None, 0, None, original_source_line_text, error_code_for_line or "U", state=state, pseudo_op_mnemonic=mnemonic); return True
        output_generator.write_listing_line(line_num, lc_for_current_line_processing, pc_for_current_line_processing, None, original_source_line_text, error_code_for_line, state=state, pseudo_op_mnemonic=mnemonic)
        if debug_mode: print(f"DEBUG P2 HERE: L{line_num} Expanding remote block '{here_operand}' at LC={state.location_counter:o} PC={state.position_counter} Block={state.current_block}")
        lines_to_expand = assembler.remote_blocks.get(here_operand, [])
        for idx, stored_parsed_line_dict in enumerate(lines_to_expand):
            expanded_line_num_for_listing = stored_parsed_line_dict.get('line_num', line_num)
            expanded_line_parsed = stored_parsed_line_dict.copy()
            expanded_line_parsed['is_remote_expansion'] = True
            expanded_line_parsed['original'] = f"{expanded_line_parsed['original']:<70} *RMT*   .{idx+1}"
            if debug_mode:
                print(f"DEBUG P2 HERE: Expanding L{expanded_line_num_for_listing} (from {here_operand}): '{expanded_line_parsed['original']}'")
            if not process_line_pass_2(state, output_generator, assembler, expanded_line_num_for_listing, expanded_line_parsed):
                return False
        if debug_mode: print(f"DEBUG P2 HERE: L{line_num} Finished expanding '{here_operand}'")
        return True

    # --- Determine LC/PC for actual content generation of *this* line ---
    lc_for_generation_start = lc_for_current_line_processing
    pc_for_generation_start = pc_for_current_line_processing

    if label and not is_solely_equ_star and not is_negating_label_current:
         if label == '+':
             if pc_for_generation_start != 0:
                   state.location_counter = lc_for_generation_start
                   state.position_counter = pc_for_generation_start
                   handle_force_upper(state, output_generator, error_reporter, line_num)
                   lc_for_generation_start = state.location_counter
                   pc_for_generation_start = state.position_counter
         elif pc_for_generation_start != 0 :
                   state.location_counter = lc_for_generation_start
                   state.position_counter = pc_for_generation_start
                   handle_force_upper(state, output_generator, error_reporter, line_num)
                   lc_for_generation_start = state.location_counter
                   pc_for_generation_start = state.position_counter

    if mnemonic and instruction_table.is_instruction(mnemonic):
        estimated_width = parsed.get('pass1_width_estimate', 0)
        force_before_instr = False
        current_pc_for_instr = pc_for_generation_start
        if mnemonic in ("PS", "XJ"):
            if current_pc_for_instr not in (0, 30): force_before_instr = True
        elif estimated_width == 30 and current_pc_for_instr not in (0, 30): force_before_instr = True
        elif estimated_width == 60 and current_pc_for_instr != 0: force_before_instr = True
        elif estimated_width > 0 and current_pc_for_instr != 0 and (current_pc_for_instr + estimated_width > 60):
            force_before_instr = True
        if force_before_instr:
            if debug_mode: print(f"DEBUG P2 L{line_num}: Pre-aligning for instruction '{mnemonic}' from PC_gen_start={current_pc_for_instr}")
            state.location_counter = lc_for_generation_start
            state.position_counter = pc_for_generation_start
            handle_force_upper(state, output_generator, error_reporter, line_num)
            lc_for_generation_start = state.location_counter
            pc_for_generation_start = state.position_counter

    lc_to_print_on_listing = lc_for_generation_start
    pc_to_print_on_listing = pc_for_generation_start

    generated_parcels_for_binary_output: List[Tuple[int, int]] = []
    listing_data_for_output_generator: Optional[List[Tuple[Any, int]]] = None

    try:
        state.location_counter = lc_for_generation_start
        state.position_counter = pc_for_generation_start

        if mnemonic and mnemonic != "EQU":
            if instruction_table.is_pseudo_op(mnemonic):
                # ... (same as v2.56)
                listing_data_for_output_generator = handle_pseudo_op_pass_2(assembler, line_num, mnemonic, operand_str_from_parser, label)
                if mnemonic == 'LOC':
                    lc_to_print_on_listing = state.location_counter
                    pc_to_print_on_listing = 0
                elif mnemonic in ('BSS', 'BSSZ', 'END', 'ENDL'):
                    pc_to_print_on_listing = 0
                    if mnemonic == 'ENDL': lc_to_print_on_listing = getattr(assembler, 'endl_listing_value', lc_for_generation_start)
                elif mnemonic in ('BASE', 'CODE'):
                    lc_to_print_on_listing = None; pc_to_print_on_listing = 0
                if listing_data_for_output_generator is None:
                    error_code_for_line = error_code_for_line or "A"; listing_data_for_output_generator = []
                if mnemonic in ("DATA", "CON", "DIS", "VFD") and listing_data_for_output_generator:
                    generated_parcels_for_binary_output.extend(
                        (item_val, item_wid) for item_val, item_wid in listing_data_for_output_generator if isinstance(item_wid, int) and item_wid > 0 and item_wid != PSEUDO_VALUE_WIDTH_INDICATOR)
            elif mnemonic in macro_definitions or \
                 (mnemonic + 'Q') in macro_definitions and macro_definitions[mnemonic + 'Q'].get('type') == 'OPDEF':
                 # ... (same as v2.56)
                 listing_data_for_output_generator = []
                 if not error_reporter.has_error_on_line(line_num): error_reporter.add_warning(f"Macro/Opdef call '{mnemonic}' expansion not implemented for binary generation.", line_num, code='W')
                 error_code_for_line = error_code_for_line or "W"
            else: # Instruction
                # ... (same as v2.56)
                details_list = instruction_table.get_instruction_details(mnemonic)
                if details_list:
                    parcels = assemble_instruction(mnemonic, details_list, operand_str_from_parser, symbol_table, state, error_reporter, instruction_table, line_num, assembler)
                    if parcels is not None:
                         generated_parcels_for_binary_output.extend(parcels)
                         listing_data_for_output_generator = parcels
                    else:
                         error_code_for_line = error_code_for_line or "A"; listing_data_for_output_generator = []
                         expected_width = parsed.get('pass1_width_estimate', 0)
                         if expected_width > 0: state.advance_lc(expected_width)
                else:
                    error_code_for_line = error_code_for_line or "U"; listing_data_for_output_generator = []
                    if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(f"Unknown mnemonic '{mnemonic}'", line_num, code='U')
                    expected_width = parsed.get('pass1_width_estimate', 15)
                    state.advance_lc(expected_width)
        elif mnemonic == "EQU": # Already handled for listing
            lc_to_print_on_listing = None
            listing_data_for_output_generator = handle_pseudo_op_pass_2(assembler, line_num, mnemonic, operand_str_from_parser, label)
        elif label and not mnemonic and not is_negating_label_current:
            listing_data_for_output_generator = []
        elif not label and not mnemonic and parsed['operand_str'] is not None:
             if not parsed['operand_str'].startswith(('.', '*')):
                  error_code_for_line = error_code_for_line or "S"
                  if not error_reporter.has_error_on_line(line_num): error_reporter.add_error("Missing mnemonic", line_num, code='S')
             listing_data_for_output_generator = []

        if mnemonic in ("DATA", "CON", "DIS", "VFD"):
            for value, width in generated_parcels_for_binary_output:
                if width <= 0: continue
                if state.position_counter + width > 60 and state.position_counter != 0:
                    handle_force_upper(state, output_generator, error_reporter, line_num)
                output_generator.add_parcel_to_binary_word(state.location_counter, value, width)
                state.advance_lc(width)
        elif generated_parcels_for_binary_output: # For instructions
            temp_bin_lc = lc_for_generation_start
            temp_bin_pc = pc_for_generation_start
            for value, width in generated_parcels_for_binary_output:
                if width <= 0: continue
                if temp_bin_pc + width > 60 and temp_bin_pc != 0:
                    output_generator.flush_binary_word(pad_with_noops=True)
                    temp_bin_lc += 1
                    temp_bin_pc = 0
                output_generator.add_parcel_to_binary_word(temp_bin_lc, value, width)
                temp_bin_pc += width
                if temp_bin_pc == 60:
                    temp_bin_lc +=1
                    temp_bin_pc = 0

        if output_generator and not is_solely_equ_star:
            # ... (listing logic from v2.56, using lc_to_print_on_listing, pc_to_print_on_listing)
            source_text_for_listing = parsed.get('original', original_source_line_text)
            if mnemonic in ("DATA", "CON", "DIS") and listing_data_for_output_generator:
                current_listing_lc_for_pseudo = lc_to_print_on_listing
                for i, (item_val, item_width_bits) in enumerate(listing_data_for_output_generator):
                    if not (isinstance(item_width_bits, int) and item_width_bits > 0 and item_width_bits != PSEUDO_VALUE_WIDTH_INDICATOR):
                        continue
                    src_text_to_list = source_text_for_listing if i == 0 else ""
                    err_code_to_list = error_code_for_line if i == 0 else error_reporter.get_error_code_for_line(line_num)
                    output_generator.write_listing_line(
                        line_num, current_listing_lc_for_pseudo, 0,
                        [(item_val, item_width_bits)], src_text_to_list, err_code_to_list,
                        state=state, pseudo_op_mnemonic=mnemonic
                    )
                    if current_listing_lc_for_pseudo is not None: current_listing_lc_for_pseudo += 1
            else:
                output_generator.write_listing_line(
                    line_num, lc_to_print_on_listing, pc_to_print_on_listing,
                    listing_data_for_output_generator, source_text_for_listing,
                    error_code_for_line, state=state, pseudo_op_mnemonic=mnemonic
                )

            if mnemonic == "SPACE" and listing_data_for_output_generator:
                for count_val_any, indicator_val in listing_data_for_output_generator:
                    if indicator_val == SPACE_COUNT_INDICATOR and isinstance(count_val_any, int):
                        output_generator.add_blank_lines(count_val_any, state)

        if mnemonic and instruction_table.is_instruction(mnemonic):
            if state.position_counter != 0:
                base_mnem = instruction_table.get_base_mnemonic(mnemonic)
                if base_mnem in DEFERRED_FORCE_MNEMONICS:
                    state.deferred_force_upper_pending = True
                    if debug_mode: print(f">>> DEBUG LC P2: L{line_num} Mnemonic {mnemonic} (Base: {base_mnem}) SET deferred_force_upper_pending because PC={state.position_counter}.")
            else:
                state.deferred_force_upper_pending = False
        elif mnemonic not in DEFERRED_FORCE_MNEMONICS:
             if state.position_counter == 0 :
                 state.deferred_force_upper_pending = False
        return True
    # ... (exception handling from v2.56) ...
    except (ExpressionError, OperandParseError, SyntaxError, ValueError, TypeError, KeyError, AsmException) as e:
         err_code_val = getattr(e, 'code', 'A');
         if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(f"Pass 2 error: {e}", line_num, code=err_code_val)
         error_code_for_line = error_reporter.get_error_code_for_line(line_num) or err_code_val
         if output_generator:
             output_generator.write_listing_line(
                 line_num, lc_to_print_on_listing, pc_to_print_on_listing,
                 [], original_source_line_text, error_code_for_line, state=state, pseudo_op_mnemonic=mnemonic
             )
         expected_width = parsed.get('pass1_width_estimate', 0)
         if expected_width > 0:
             state.location_counter = lc_for_generation_start
             state.position_counter = pc_for_generation_start
             if state.position_counter + expected_width > 60 and state.position_counter != 0:
                 handle_force_upper(state, output_generator, error_reporter, line_num)
             state.advance_lc(expected_width)
         return False
    except Exception as e:
         err_code_val = 'F'
         error_reporter.add_error(f"Unexpected Pass 2 error: {e}", line_num, code=err_code_val); traceback.print_exc()
         error_code_for_line = error_reporter.get_error_code_for_line(line_num) or err_code_val
         if output_generator:
             output_generator.write_listing_line(
                 line_num, lc_to_print_on_listing, pc_to_print_on_listing,
                 [], original_source_line_text, error_code_for_line, state=state, pseudo_op_mnemonic=mnemonic
             )
         return False

# pass2_processing.py v2.58
