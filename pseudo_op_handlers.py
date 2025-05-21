# pseudo_op_handlers.py v2.18
"""
Handles pseudo-operation processing for CRASS during Pass 1 and Pass 2.
This file now acts as a dispatcher to more specific handler modules.
[...]
v2.17:  - Refined TITLE/TTL/IDENT logic in Pass 1 to correctly manage
          state.current_title, state.current_ttl_title, and state.first_title_processed.
v2.18:  - Pass 1 LOC handler now calls state.set_location_counter with
          is_loc_directive=True.
        - Pass 2 LOC handler also calls state.set_location_counter with
          is_loc_directive=True (though pre_loc_block_name is not used in P2).
"""

import re
import math
import traceback
import sys
from typing import List, Optional, Tuple, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from crass import Assembler
    from symbol_table import SymbolTable
    from assembler_state import AssemblerState
    from errors import ErrorReporter
    from output_generator import OutputGenerator

from symbol_table import SymbolTable
from assembler_state import AssemblerState
from errors import ErrorReporter, AsmException
from expression import (
    evaluate_expression, evaluate_data_item, parse_dis_operands,
    generate_dis_words, ExpressionError, substitute_micros,
    DISPLAY_CODE_MAP_ASCII_SUBSET, DISPLAY_CODE_BLANK, DISPLAY_CODE_ZERO_CHAR,
    INTERNAL_BCD_MAP, INTERNAL_BCD_BLANK, INTERNAL_BCD_BINARY_ZERO, INTERNAL_BCD_ZERO_CHAR,
    EXTERNAL_BCD_MAP, EXTERNAL_BCD_BLANK, EXTERNAL_BCD_ZERO_CHAR,
    ASCII_6BIT_SUBSET_MAP, ASCII_6BIT_BLANK, ASCII_6BIT_ZERO_CHAR,
    ZERO_6BIT,
    DISPLAY_CODE_MAP
)
from output_generator import (
    OutputGenerator, LINES_PER_PAGE, PSEUDO_VALUE_WIDTH_INDICATOR,
    EQU_STAR_LC_INDICATOR, SPACE_COUNT_INDICATOR,
    PSEUDO_STRING_VALUE_INDICATOR
)
from assembler_state import handle_force_upper
from conditional_processing import evaluate_condition

MASK_60_BIT = (1 << 60) - 1

def generate_vfd_parcels(assembler: 'Assembler', line_num: int, operand_str: str) -> Optional[List[Tuple[int, int]]]:
    state = assembler.state
    symbol_table = assembler.symbol_table
    error_reporter = assembler.error_reporter

    generated_parcels: List[Tuple[int, int]] = []
    operand_str_no_comment = operand_str.split('.')[0].split('*')[0].rstrip()
    fields = operand_str_no_comment.split(',')

    for field_idx, field in enumerate(fields):
        field = field.strip()
        if not field: continue

        parts = field.split('/', 1)
        if len(parts) != 2: error_reporter.add_error(f"Invalid VFD field format: '{field}'", line_num, code='S'); return None
        width_str, value_str = parts[0].strip(), parts[1].strip()

        try:
            width_val, width_type, _ = evaluate_expression(width_str, symbol_table, state, line_num, assembler)
            if width_type != 'absolute' or not isinstance(width_val, int) or not (0 < width_val <= 60):
                error_reporter.add_error(f"VFD width '{width_str}' must be absolute integer (1-60)", line_num, code='V'); return None
            field_width = width_val

            field_value, value_type, value_block = evaluate_expression(value_str, symbol_table, state, line_num, assembler)
            if field_value is None: return None

            if state.pass_number == 2 and value_type == 'relocatable' and value_block and value_block != '*ABS*':
                block_base = assembler.block_base_addresses.get(value_block, 0)
                field_value += block_base
                value_type = 'absolute'

            if value_type != 'absolute':
                 pass # Allow non-absolute for now, will be resolved or error in Pass 2 if still not absolute

            if not isinstance(field_value, int):
                error_reporter.add_error(f"VFD value '{value_str}' for width {field_width} is not an integer: {field_value}", line_num, code='V')
                return None

            mask = (1 << field_width) - 1
            if field_value < 0:
                if abs(field_value) > (mask >> 1) and field_width < 60 : # Check for potential overflow for negative numbers
                     error_reporter.add_warning(f"Negative VFD value '{field_value}' may overflow width {field_width}.", line_num, code='V')
                field_value = (~abs(field_value)) & mask # One's complement negative
            else:
                if field_value > mask:
                     error_reporter.add_warning(f"Positive VFD value '{field_value:o}' exceeds width {field_width}, truncated.", line_num, code='V')
                field_value &= mask
            generated_parcels.append((field_value, field_width))

        except (ExpressionError, AsmException) as e:
             if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(f"Error evaluating VFD field '{field}': {e}", line_num, code='E');
             return None
        except Exception as e:
             if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(f"Unexpected error processing VFD field '{field}': {e}", line_num, code='F');
             traceback.print_exc();
             return None
    return generated_parcels


def handle_pseudo_op_pass_1(assembler: 'Assembler', line_num: int, mnemonic: str, operand_str: str, label: Optional[str], label_defined_by_pseudo: bool, current_lc: int, current_pc: int) -> bool:
    state = assembler.state
    symbol_table = assembler.symbol_table
    error_reporter = assembler.error_reporter
    debug_mode = assembler.debug_mode

    if mnemonic.startswith("IF") or mnemonic == "ELSE" or mnemonic == "ENDIF":
        currently_active = state.conditional_stack[-1]
        condition_result = False
        
        if mnemonic.startswith("IF"):
            if currently_active:
                try:
                    condition_result = evaluate_condition(assembler, line_num, mnemonic, operand_str)
                except (ExpressionError, AsmException) as e:
                    # Error during condition evaluation means it's false
                    condition_result = False
            new_stack_state = currently_active and condition_result
            state.conditional_stack.append(new_stack_state)
        elif mnemonic == "ELSE":
            if len(state.conditional_stack) <= 1:
                error_reporter.add_error("ELSE without matching IF", line_num, code='S')
                return False
            outer_active = state.conditional_stack[-2] # The state of the IF block this ELSE belongs to
            last_if_true_branch_state = state.conditional_stack.pop() # Remove the IF's true branch state
            new_stack_state = outer_active and (not last_if_true_branch_state) # ELSE is active if outer is active AND IF was false
            state.conditional_stack.append(new_stack_state)
        elif mnemonic == "ENDIF":
            if len(state.conditional_stack) <= 1:
                error_reporter.add_error("ENDIF without matching IF", line_num, code='S')
                return False
            state.conditional_stack.pop()
        return True

    # If not currently assembling due to conditional, skip further processing for this line
    if not state.conditional_stack[-1]:
        return True

    if mnemonic == "QUAL":
         qual_name = operand_str.strip().upper()
         # Remove comments from qualifier name
         qual_name = re.split(r'\s+(\.|\*)', qual_name, maxsplit=1)[0].strip()
         if not qual_name: error_reporter.add_error("QUAL requires an operand (name or *)", line_num, code='S'); return False
         if qual_name == '*': state.current_qualifier = None
         elif not re.fullmatch(r'[A-Z][A-Z0-9]{0,7}', qual_name): error_reporter.add_error(f"Invalid qualifier name '{qual_name}'", line_num, code='S'); return False
         else: state.current_qualifier = qual_name
         return True

    # Parse operand string, removing comments, unless it's a special pseudo-op
    operand_str_parsed = operand_str
    if mnemonic not in ('DIS', 'TITLE', 'TTL', 'COMMENT', 'CTEXT', 'XTEXT', 'MICRO', 'LIST', 'NOLIST', 'IFC'):
         if mnemonic in ("EQU", "=") and operand_str.strip() == '*':
             operand_str_parsed = "*" # Keep '*' for EQU *
         else:
             operand_str_parsed = operand_str.split('.')[0].split('*')[0].strip()
    elif mnemonic in ('LIST', 'NOLIST'): # LIST/NOLIST operands can have commas but no embedded blanks
         operand_str_parsed = operand_str.strip()


    if mnemonic == "IDENT":
        program_name = operand_str_parsed.strip().upper()
        if not program_name: error_reporter.add_error("IDENT requires a program name.", line_num, code='S'); return False
        if label: error_reporter.add_warning(f"Label '{label}' ignored on IDENT statement.", line_num, code='W')
        attrs = {'type': 'absolute', 'redefinable': False, 'block': '*ABS*', 'program_name': True}
        if not symbol_table.define(program_name, 0, line_num, attrs, current_qualifier=None): return False
        
        if not state.first_title_processed: # IDENT can set the initial title
            state.current_title = program_name 
            state.first_title_processed = True
        return True
    elif mnemonic == "EQU" or mnemonic == '=':
        if operand_str_parsed == "*":
            # Handled by main pass1_processing logic for EQU *
            return True
        if not label: error_reporter.add_error("EQU requires a label", line_num, code='S'); return False
        try:
            value, val_type, val_block = evaluate_expression(operand_str_parsed, symbol_table, state, line_num, assembler)
            
            block_for_symbol = val_block
            if state.lc_is_absolute_due_to_loc: # If under LOC, symbol is absolute
                val_type = 'absolute'
                block_for_symbol = '*ABS*'
            elif val_type == 'relocatable' and val_block is None: # Relocatable but no explicit block from expr
                block_for_symbol = state.current_block
            elif val_type == 'absolute': # Absolute expression
                 block_for_symbol = '*ABS*'


            attrs = {'type': val_type, 'redefinable': False, 'block': block_for_symbol}
            if not symbol_table.define(label, value, line_num, attrs, state.current_qualifier): return False
        except ExpressionError as e: error_reporter.add_error(f"EQU error: {e}", line_num, code='E'); return False
        except AsmException as e: error_reporter.add_error(str(e), line_num, code=e.code); return False
        return True
    elif mnemonic == "SET":
        if not label: error_reporter.add_error("SET requires a label", line_num, code='S'); return False
        try:
            value, val_type, val_block = evaluate_expression(operand_str_parsed, symbol_table, state, line_num, assembler)

            block_for_symbol = val_block
            if state.lc_is_absolute_due_to_loc:
                val_type = 'absolute'
                block_for_symbol = '*ABS*'
            elif val_type == 'relocatable' and val_block is None:
                block_for_symbol = state.current_block
            elif val_type == 'absolute':
                 block_for_symbol = '*ABS*'

            attrs = {'type': val_type, 'redefinable': True, 'block': block_for_symbol}
            if not symbol_table.define(label, value, line_num, attrs, state.current_qualifier): return False
        except ExpressionError as e: error_reporter.add_error(f"SET error: {e}", line_num, code='E'); return False
        except AsmException as e: error_reporter.add_error(str(e), line_num, code=e.code); return False
        return True
    elif mnemonic == "LOC":
         try:
            loc_val, val_type, _ = evaluate_expression(operand_str_parsed, symbol_table, state, line_num, assembler)
            if val_type != 'absolute': raise ExpressionError("LOC operand must be absolute")
            if loc_val < 0: raise ExpressionError("LOC address cannot be negative")
            
            if state.position_counter != 0:
                 handle_force_upper(state, None, error_reporter, line_num)
            
            state.set_location_counter(loc_val, 0, is_loc_directive=True) # Pass flag
            
            if label: 
                 attrs_loc_label = {'type': 'absolute', 'redefinable': False, 'block': '*ABS*', 'defined_by_loc': True}
                 if not symbol_table.define(label, loc_val, line_num, attrs_loc_label, state.current_qualifier): return False
         except ExpressionError as e: error_reporter.add_error(f"LOC error: {e}", line_num, code='E'); return False
         return True
    elif mnemonic in ["DATA", "CON", "DIS", "BSS", "BSSZ"]:
         if state.position_counter != 0: 
              handle_force_upper(state, None, error_reporter, line_num)
         try:
            op_str_for_size = operand_str 
            estimated_bits = calculate_pseudo_op_size(assembler, line_num, mnemonic, op_str_for_size)
            if estimated_bits is None: return False 
            if estimated_bits > 0:
                 state.advance_lc(estimated_bits)
         except (ExpressionError, AsmException) as e: error_reporter.add_error(f"Size calc error {mnemonic}: {e}", line_num, code='E'); return False
         return True
    elif mnemonic == "VFD":
         if label == '-': 
              if state.position_counter % 15 != 0:
                   bits_to_pad = 15 - (state.position_counter % 15)
                   state.advance_lc(bits_to_pad)
         try:
            estimated_bits = calculate_pseudo_op_size(assembler, line_num, mnemonic, operand_str)
            if estimated_bits is None: return False
            if estimated_bits > 0:
                 state.advance_lc(estimated_bits)
         except (ExpressionError, AsmException) as e: error_reporter.add_error(f"Size calc error {mnemonic}: {e}", line_num, code='E'); return False
         return True
    elif mnemonic == "LIT":
         if state.position_counter != 0: 
              handle_force_upper(state, None, error_reporter, line_num)
         try:
             literal_values_str = operand_str_parsed.split(',')
             for lit_str in literal_values_str:
                  lit_str = lit_str.strip();
                  if not lit_str: continue
                  lit_value, lit_type, _ = evaluate_data_item(lit_str, symbol_table, state, line_num, assembler)
                  if lit_type != 'absolute': raise ExpressionError(f"Literal must be absolute: '{lit_str}'")
                  symbol_table.add_literal(lit_value, line_num)
         except ExpressionError as e: error_reporter.add_error(f"LIT error: {e}", line_num, code='E'); return False
         return True
    elif mnemonic == "BASE":
        parts = operand_str.split(maxsplit=1)
        base_mode_str = ""
        micro_name = None
        if len(parts) >= 1 and re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,7}', parts[0]):
             if parts[0].upper() not in ['O', 'D', 'M', 'H', '*']: 
                 if len(parts) == 2: 
                      micro_name = parts[0].upper()
                      base_mode_str = parts[1].strip().upper()
                 else: 
                      micro_name = parts[0].upper()
             else: 
                  base_mode_str = parts[0].strip().upper()
        elif len(parts) >= 1: 
             base_mode_str = operand_str.strip().upper()

        if micro_name:
             attrs = {'type': 'absolute', 'value_is_char': True, 'redefinable': True, 'block': '*ABS*'}
             if not symbol_table.define(micro_name, state.current_base, line_num, attrs, state.current_qualifier): return False
        if base_mode_str:
            base_char = base_mode_str[0] if base_mode_str else ''
            if base_char in ['O', 'D', 'H', 'M']: state.set_base(base_char)
            elif base_char == '*': state.set_base('D') 
            else: error_reporter.add_error(f"Invalid base: '{base_mode_str}'", line_num, code='V'); return False
        elif not micro_name: 
             error_reporter.add_error("BASE requires an operand (O, D, M, H, *, or micro name)", line_num, code='S'); return False
        return True
    elif mnemonic == "CODE":
         parts = operand_str.split(maxsplit=1)
         code_mode_str = ""
         micro_name = None
         if len(parts) >= 1 and re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,7}', parts[0]):
             if parts[0].upper() not in ['A', 'D', 'E', 'I', '*']: 
                 if len(parts) == 2: 
                      micro_name = parts[0].upper()
                      code_mode_str = parts[1].strip().upper()
                 else: 
                      micro_name = parts[0].upper()
             else: 
                  code_mode_str = parts[0].strip().upper()
         elif len(parts) >= 1: 
              code_mode_str = operand_str.strip().upper()

         if micro_name:
             attrs = {'type': 'absolute', 'value_is_char': True, 'redefinable': True, 'block': '*ABS*'}
             if not symbol_table.define(micro_name, state.current_code, line_num, attrs, state.current_qualifier): return False
         
         if code_mode_str:
             code_char = code_mode_str[0] if code_mode_str else ''
             state.set_code(code_char) 
         elif not micro_name: 
             error_reporter.add_error("CODE requires an operand (A, D, E, I, *, or micro name)", line_num, code='S'); return False
         return True
    elif mnemonic == "USE":
         block_name = operand_str_parsed.strip().upper()
         if not block_name: error_reporter.add_error("USE requires a block name", line_num, code='S'); return False
         if state.position_counter != 0: 
              handle_force_upper(state, None, error_reporter, line_num)
         state.switch_block(block_name)
         return True
    elif mnemonic == "ABS":
         if state.position_counter != 0: handle_force_upper(state, None, error_reporter, line_num)
         state.switch_block("*ABS*")
         return True
    elif mnemonic == "REL":
         if state.position_counter != 0: handle_force_upper(state, None, error_reporter, line_num)
         rel_block_name = operand_str_parsed.strip().upper() if operand_str_parsed else "*REL*" 
         if not rel_block_name: rel_block_name = "*REL*" 
         state.switch_block(rel_block_name)
         return True
    elif mnemonic == "LIST":
        state.update_listing_flags(operand_str_parsed, turn_on=True)
        return True
    elif mnemonic == "NOLIST":
        state.update_listing_flags(operand_str_parsed, turn_on=False)
        return True
    elif mnemonic == "TITLE":
         title_text = operand_str.strip()
         title_text = re.split(r'\s+(\.|\*)', title_text, maxsplit=1)[0].strip() 
         state.current_title = title_text
         state.current_ttl_title = "" 
         state.first_title_processed = True
         return True
    elif mnemonic == "TTL":
         ttl_text = operand_str.strip()
         ttl_text = re.split(r'\s+(\.|\*)', ttl_text, maxsplit=1)[0].strip()
         state.current_ttl_title = ttl_text
         if not state.first_title_processed: 
             state.current_title = ttl_text
         state.first_title_processed = True
         return True
    elif mnemonic in ["SPACE", "EJECT", "COMMENT", "ERROR", "FIN",
                      "REF", "NOREF", "XREF", "SEQ", "SKIP", "MACHINE", "CPU", "PPU", "CMU",
                      "UNL", "CTEXT", "ENDX", "RMT", "HERE", "EXT", "ENTRY",
                      "LOCAL", "IRP", "ENDD", "PURGE", "OPSYN",
                      "DECMIC", "OCTMIC", "ENDMIC",
                      "B1=1", "B7=1", "CHAR", "CPOP", "CPSYN", "ENTRYC",
                      "ERRMI", "ERRNG", "ERRNZ", "ERRPL", "ERRZR",
                      "LCC", "NIL", "NOLABEL", "PURGDEF", "PURGMAC",
                      "REP", "REPC", "REPI", "R=", "SEG", "SEGMENT",
                      "SST", "STEXT", "STOPDUP", "USELCM", "POS", "MAX", "MIN", "MICCNT"
                      ]:
        if mnemonic == "ENTRY":
             entry_names = operand_str_parsed.split(',')
             for name_str in entry_names:
                  name_str = name_str.strip().upper();
                  if name_str: symbol_table.mark_entry_point(name_str, line_num)
        elif mnemonic == "MACHINE":
            pass 
        elif mnemonic == "SPACE":
            pass
        elif mnemonic == "SKIP":
             try:
                 value, val_type, _ = evaluate_expression(operand_str_parsed, symbol_table, state, line_num, assembler)
                 if val_type != 'absolute': raise ExpressionError("SKIP requires absolute value")
                 if not isinstance(value, int) or value < 0: raise ExpressionError("SKIP requires non-negative integer")
                 state.skip_count = value
             except ExpressionError as e:
                 error_reporter.add_error(f"SKIP error: {e}", line_num, code='E')
                 return False
        return True
    elif mnemonic == "END" or mnemonic == "ENDL":
         state.end_statement_processed = True
         if label:
             assembler.end_statement_label = label
         start_symbol = operand_str_parsed.strip().upper()
         if start_symbol:
              state.program_start_symbol = start_symbol
         return True

    error_reporter.add_warning(f"Pseudo-op '{mnemonic}' not fully handled in Pass 1", line_num, code='W')
    return True


def handle_pseudo_op_pass_2(assembler: 'Assembler', line_num: int, mnemonic: str, operand_str: str, label: Optional[str]) -> Optional[List[Tuple[Any, int]]]:
    state = assembler.state
    symbol_table = assembler.symbol_table
    error_reporter = assembler.error_reporter
    output_generator = assembler.output_generator
    debug_mode = assembler.debug_mode

    source_line_initial_lc = state.line_start_address
    source_line_initial_pc = state.line_start_position_bits

    if mnemonic.startswith("IF") or mnemonic == "ELSE" or mnemonic == "ENDIF":
        currently_active = state.conditional_stack[-1]
        condition_result = False
        if mnemonic.startswith("IF"):
            if currently_active:
                try:
                    condition_result = evaluate_condition(assembler, line_num, mnemonic, operand_str)
                except (ExpressionError, AsmException) as e:
                    if not error_reporter.has_error_on_line(line_num):
                        error_reporter.add_error(f"Error in {mnemonic} condition (Pass 2): {e}", line_num, code='E')
                    condition_result = False
            new_stack_state = currently_active and condition_result
            state.conditional_stack.append(new_stack_state)
        elif mnemonic == "ELSE":
            if len(state.conditional_stack) <= 1:
                if not error_reporter.has_error_on_line(line_num): error_reporter.add_error("ELSE without matching IF", line_num, code='S')
                return None
            outer_active = state.conditional_stack[-2]
            last_if_true_branch_state = state.conditional_stack.pop()
            new_stack_state = outer_active and (not last_if_true_branch_state)
            state.conditional_stack.append(new_stack_state)
        elif mnemonic == "ENDIF":
            if len(state.conditional_stack) <= 1:
                if not error_reporter.has_error_on_line(line_num): error_reporter.add_error("ENDIF without matching IF", line_num, code='S')
                return None
            state.conditional_stack.pop()
        return [] 

    if not state.conditional_stack[-1]:
        return [] 

    operand_str_for_eval = operand_str
    if mnemonic not in ('DIS', 'TITLE', 'TTL', 'COMMENT', 'CTEXT', 'XTEXT', 'MICRO', 'LIST', 'NOLIST', 'SPACE', 'IFC'):
        if mnemonic in ("EQU", "=") and operand_str.strip() == '*':
            operand_str_for_eval = "*" 
        else:
            operand_str_for_eval = operand_str.split('.')[0].split('*')[0].strip()
    elif mnemonic in ('LIST', 'NOLIST', 'SPACE'): 
         operand_str_for_eval = operand_str.strip()


    if mnemonic == "QUAL":
         qual_name = operand_str.strip().upper()
         qual_name = re.split(r'\s+(\.|\*)', qual_name, maxsplit=1)[0].strip() 
         if not qual_name: return None 
         if qual_name == '*': state.current_qualifier = None
         elif not re.fullmatch(r'[A-Z][A-Z0-9]{0,7}', qual_name): return None 
         else: state.current_qualifier = qual_name
         return []

    if mnemonic == "IDENT":
        prog_attrs = symbol_table.get_program_name_attributes()
        if prog_attrs:
            prog_name = prog_attrs['name']
            encoded_word = 0
            chars_to_encode = prog_name[:10].ljust(10) 
            for char_val in chars_to_encode:
                code = DISPLAY_CODE_MAP.get(char_val.upper(), DISPLAY_CODE_BLANK) 
                encoded_word = (encoded_word << 6) | code
            
            if not state.first_title_processed: 
                state.current_title = prog_name
                state.first_title_processed = True
            return [(encoded_word, 60)]
        else:
            error_reporter.add_error("IDENT processed in Pass 2 but no program name found.", line_num, code='F')
            return [(0, 60)] 
    elif mnemonic == "EQU" or mnemonic == '=':
        try:
            operand_expr = operand_str_for_eval.strip()
            value_to_return: Any = 0
            is_equ_star = False

            if operand_expr == '*':
                is_equ_star = True
                if not label:
                    return None
                sym_entry = symbol_table.lookup(label, line_num, state.current_qualifier)
                if sym_entry:
                    raw_value = sym_entry['value']
                    sym_block = sym_entry['attrs'].get('block')
                    sym_type  = sym_entry['attrs'].get('type')

                    if not isinstance(raw_value, int):
                        error_reporter.add_error(f"Internal: Symbol '{label}' value is not an integer in Pass 2 for EQU * (is {type(raw_value)})", line_num, 'F')
                        value_to_return = 0
                    elif sym_type == 'relocatable' and sym_block and sym_block != '*ABS*':
                        block_base = assembler.block_base_addresses.get(sym_block)
                        if block_base is None:
                            error_reporter.add_error(f"Internal: Base for block '{sym_block}' not found for EQU * '{label}'.", line_num, 'F')
                            value_to_return = raw_value 
                        else:
                            value_to_return = raw_value + block_base
                    else: 
                        value_to_return = raw_value
                else:
                    value_to_return = 0 
            else:
                eval_val, _, _ = evaluate_expression(operand_expr, symbol_table, state, line_num, assembler)
                if not isinstance(eval_val, int):
                    error_reporter.add_error(f"EQU expression for '{label if label else operand_expr}' did not resolve to an integer: {eval_val} (type: {type(eval_val)})", line_num, code='V')
                    return None
                value_to_return = eval_val

            if not isinstance(value_to_return, int):
                try: value_to_return = int(value_to_return)
                except (ValueError, TypeError):
                    error_reporter.add_error(f"Internal: Value for {mnemonic} '{label if label else operand_expr}' could not be converted to an integer: {value_to_return} (type: {type(value_to_return)})", line_num, code='F')
                    return None
            indicator = EQU_STAR_LC_INDICATOR if is_equ_star else PSEUDO_VALUE_WIDTH_INDICATOR
            return [(value_to_return, indicator)]
        except ExpressionError as e:
            if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(f"{mnemonic} error: {e}", line_num, code='E');
            return None
        except Exception as e: 
            if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(f"Unexpected error in {mnemonic} handler for '{label if label else operand_expr}': {e}", line_num, code='F');
            traceback.print_exc()
            return None
    elif mnemonic == "SET":
         if label:
              try:
                   value, val_type, val_block = evaluate_expression(operand_str_for_eval, symbol_table, state, line_num, assembler)
                   if not isinstance(value, int):
                        error_reporter.add_error(f"SET value for '{label}' did not resolve to an integer: {value}", line_num, code='V')
                        return None
                   return [(value, PSEUDO_VALUE_WIDTH_INDICATOR)]
              except (ExpressionError, AsmException) as e:
                   if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(str(e), line_num, code='E')
                   return None
              except Exception as e:
                   if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(f"Unexpected error in SET handler for '{label}': {e}", line_num, code='F');
                   traceback.print_exc()
                   return None
         return None 
    elif mnemonic == "DATA" or mnemonic == "CON":
        if state.position_counter != 0:
            handle_force_upper(state, output_generator, error_reporter, line_num)

        expressions = operand_str_for_eval.split(',')
        generated_words_for_this_statement: List[Tuple[Any, int]] = []
        
        for idx, expr_str in enumerate(expressions):
            expr_str = expr_str.strip()
            if not expr_str:
                if idx == 0 and not expressions[0].strip() and len(expressions) == 1:
                     pass
                continue 

            value = 0 
            try:
                value, val_type, _ = evaluate_data_item(expr_str, symbol_table, state, line_num, assembler)
                if value is None: raise ExpressionError("Evaluated to None") 
                if val_type != 'absolute': error_reporter.add_warning(f"{mnemonic} value '{expr_str}' non-absolute type '{val_type}'", line_num, "R")
                if isinstance(value, int) and value < 0: value = (~abs(value)) & MASK_60_BIT
                else: value &= MASK_60_BIT 
            except ExpressionError as e:
                if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(f"Error {mnemonic} expr '{expr_str}': {e}", line_num, 'E')
                value = 0 
            
            generated_words_for_this_statement.append((value, 60))
        return generated_words_for_this_statement

    elif mnemonic == "DIS":
        if state.position_counter != 0: handle_force_upper(state, output_generator, error_reporter, line_num)
        
        gen_words_values: List[int] = []
        generated_tuples_for_listing: List[Tuple[Any, int]] = []
        try:
            dis_ops = parse_dis_operands(operand_str, symbol_table, state, line_num, assembler)
            gen_words_values = generate_dis_words(dis_ops, error_reporter, line_num, state)
            for word_val in gen_words_values:
                generated_tuples_for_listing.append((word_val, 60))
        except (ExpressionError, AsmException) as e:
            if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(f"DIS error: {e}", line_num, code='E');
            return [] 
        
        return generated_tuples_for_listing

    elif mnemonic in ["BSS", "BSSZ"]:
         if state.position_counter != 0: 
             handle_force_upper(state, output_generator, error_reporter, line_num)
         try:
             value, val_type, _ = evaluate_expression(operand_str_for_eval, symbol_table, state, line_num, assembler)
             if val_type != 'absolute': raise ExpressionError(f"{mnemonic} expr must be absolute")
             if not isinstance(value, int) or value < 0: raise ExpressionError(f"{mnemonic} requires non-negative integer value")
             
             if value > 0:
                 pass 
             return [(value, PSEUDO_VALUE_WIDTH_INDICATOR)]
         except ExpressionError as e:
             if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(str(e), line_num, code='E')
             return None 
    elif mnemonic == "VFD":
         if label == '-': 
              if state.position_counter % 15 != 0: pass 
         vfd_fields = generate_vfd_parcels(assembler, line_num, operand_str)
         if vfd_fields is None: return None 
         return vfd_fields
    elif mnemonic == "LOC":
         try:
             value, val_type, _ = evaluate_expression(operand_str_for_eval, symbol_table, state, line_num, assembler)
             if val_type != 'absolute': raise ExpressionError("LOC requires absolute value")
             if value < 0: raise ExpressionError("LOC address cannot be negative")
             if output_generator: output_generator.flush_binary_word(pad_with_noops=True) 
             
             state.set_location_counter(value, 0, is_loc_directive=True) # Pass flag
             return [] 
         except ExpressionError as e:
             if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(str(e), line_num, code='E')
             return None
    elif mnemonic == "BASE":
        old_base = state.current_base
        parts = operand_str.split(maxsplit=1)
        base_mode_str = (parts[1] if len(parts) == 2 and re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,7}', parts[0]) and parts[0].upper() not in ['O','D','M','H','*'] else operand_str).strip().upper()
        if not base_mode_str and len(parts) == 1 and parts[0].upper() in ['O','D','M','H','*']: 
            base_mode_str = parts[0].upper()
        
        new_base = old_base
        if base_mode_str:
            base_char = base_mode_str[0] if base_mode_str else ''
            if base_char in ['O', 'D', 'H', 'M']: new_base = base_char; state.set_base(base_char)
            elif base_char == '*': new_base = 'D'; state.set_base('D')
            else: return None 
        
        mode_change_str = f"{old_base}_{new_base}"
        return [(mode_change_str, PSEUDO_STRING_VALUE_INDICATOR)]

    elif mnemonic == "CODE":
         old_code = state.current_code
         parts = operand_str.split(maxsplit=1)
         code_mode_str = (parts[1] if len(parts) == 2 and re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,7}', parts[0]) and parts[0].upper() not in ['A','D','E','I','*'] else operand_str).strip().upper()
         if not code_mode_str and len(parts) == 1 and parts[0].upper() in ['A','D','E','I','*']:
             code_mode_str = parts[0].upper()

         new_code = old_code
         if code_mode_str:
             code_char = code_mode_str[0] if code_mode_str else ''
             if code_char in ['A', 'D', 'E', 'I']: new_code = code_char; state.set_code(code_char)
             elif code_char == '*': pass 
             else: return None 
         
         mode_change_str = f"{old_code}_{new_code}"
         return [(mode_change_str, PSEUDO_STRING_VALUE_INDICATOR)]

    elif mnemonic == "USE":
         block_name = operand_str_for_eval.strip().upper()
         if not block_name: return None 
         if output_generator: output_generator.flush_binary_word(pad_with_noops=True)
         state.switch_block(block_name)
         return []
    elif mnemonic == "ABS":
         if output_generator: output_generator.flush_binary_word(pad_with_noops=True)
         if state.position_counter != 0: handle_force_upper(state, output_generator, error_reporter, line_num)
         state.switch_block("*ABS*")
         return []
    elif mnemonic == "REL":
         if output_generator: output_generator.flush_binary_word(pad_with_noops=True)
         if state.position_counter != 0: handle_force_upper(state, output_generator, error_reporter, line_num)
         rel_block_name = operand_str_for_eval.strip().upper() if operand_str_for_eval else "*REL*"
         if not rel_block_name: rel_block_name = "*REL*"
         state.switch_block(rel_block_name)
         return []
    elif mnemonic == "SKIP":
         try:
             value, val_type, _ = evaluate_expression(operand_str_for_eval, symbol_table, state, line_num, assembler)
             if val_type != 'absolute': raise ExpressionError("SKIP requires absolute value")
             if not isinstance(value, int) or value < 0: raise ExpressionError("SKIP requires non-negative integer")
             state.skip_count = value
         except ExpressionError as e:
             if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(f"SKIP error: {e}", line_num, code='E')
             return None
         return [] 
    elif mnemonic == "SPACE":
        try:
            counts_str = operand_str_for_eval.split(',')
            counts = []
            if not operand_str_for_eval.strip(): 
                counts.append(1)
            else:
                for c_str in counts_str:
                    c_str = c_str.strip()
                    if not c_str: 
                        counts.append(1)
                    else:
                        val, vtype, _ = evaluate_expression(c_str, symbol_table, state, line_num, assembler)
                        if vtype != 'absolute' or not isinstance(val, int) or val < 0:
                            raise ExpressionError(f"SPACE count '{c_str}' must be non-negative absolute integer")
                        counts.append(val if val > 0 else 1) 
            return [(c, SPACE_COUNT_INDICATOR) for c in counts]
        except ExpressionError as e:
            error_reporter.add_error(f"SPACE error: {e}", line_num, code='E')
            return None
    elif mnemonic == "TITLE":
         title_text = operand_str.strip()
         title_text = re.split(r'\s+(\.|\*)', title_text, maxsplit=1)[0].strip() 
         if not state.first_title_processed:
             state.first_title_processed = True
         else: 
             if output_generator and output_generator.lines_on_current_page > 3 : 
                 output_generator.lines_on_current_page = LINES_PER_PAGE 
         state.current_title = title_text
         state.current_ttl_title = "" 
         return []
    elif mnemonic == "TTL":
         ttl_text = operand_str.strip()
         ttl_text = re.split(r'\s+(\.|\*)', ttl_text, maxsplit=1)[0].strip()
         if not state.first_title_processed: 
             state.current_title = ttl_text
         state.first_title_processed = True 
         state.current_ttl_title = ttl_text
         if output_generator and output_generator.lines_on_current_page > 3: 
             output_generator.lines_on_current_page = LINES_PER_PAGE 
         return []
    elif mnemonic in ["EJECT", "LIST", "NOLIST", "COMMENT", "ERROR", "FIN",
                      "REF", "NOREF", "XREF", "SEQ", "MACHINE", "CPU", "PPU", "CMU",
                      "LIT", "UNL", "CTEXT", "ENDX", "RMT", "HERE", "EXT", "ENTRY",
                      "MACRO", "ENDM", "OPDEF", "MICRO"]: 
         if mnemonic == "EJECT":
              if output_generator:
                  if output_generator.lines_on_current_page > 0 : 
                      output_generator.lines_on_current_page = LINES_PER_PAGE 
         elif mnemonic == "LIST":
             state.update_listing_flags(operand_str_for_eval, turn_on=True)
         elif mnemonic == "NOLIST":
             state.update_listing_flags(operand_str_for_eval, turn_on=False)
         return []
    elif mnemonic == "END" or mnemonic == "ENDL":
        state.end_statement_processed = True
        start_symbol = operand_str_for_eval.strip().upper()
        if start_symbol:
            state.program_start_symbol = start_symbol
            start_addr_info = symbol_table.lookup(start_symbol, line_num, state.current_qualifier)
            if start_addr_info is not None:
                start_addr = start_addr_info['value']
                start_addr_type = start_addr_info['attrs'].get('type')
                start_addr_block = start_addr_info['attrs'].get('block')
                if start_addr_type == 'relocatable' and start_addr_block and start_addr_block != '*ABS*':
                    block_base = assembler.block_base_addresses.get(start_addr_block, 0)
                    state.program_start_address = start_addr + block_base
                else: 
                    state.program_start_address = start_addr
            else:
                state.program_start_address = 0 
        else: 
            prog_attrs = symbol_table.get_program_name_attributes()
            if prog_attrs:
                state.program_start_symbol = prog_attrs['name']
                state.program_start_address = prog_attrs['value'] 
            else: 
                state.program_start_symbol = None
                state.program_start_address = 0
        return [] 

    error_reporter.add_warning(f"Pseudo-op '{mnemonic}' not fully handled in Pass 2 for binary generation.", line_num, code='W')
    return [] 


def calculate_pseudo_op_size(assembler: 'Assembler', line_num: int, mnemonic: str, operand_str: str) -> Optional[int]:
    symbol_table = assembler.symbol_table
    state = assembler.state
    error_reporter = assembler.error_reporter
    debug_mode = assembler.debug_mode
    
    if mnemonic == "DIS":
        try:
            dis_ops = parse_dis_operands(operand_str, symbol_table, state, line_num, assembler, suppress_undefined_error_for_n=True)
            
            string_to_size_for_dis = dis_ops['string']
            if dis_ops.get('is_micro_name_literal', False):
                micro_name_for_dis = dis_ops['string']
                actual_string_from_micro = assembler.micro_definitions.get(micro_name_for_dis.upper())
                if actual_string_from_micro is None:
                    if not error_reporter.has_error_on_line(line_num):
                        error_reporter.add_warning(f"Micro '%{micro_name_for_dis}%' for DIS sizing not found (yet?). Assuming 0 length for Pass 1.", line_num, code='W')
                    return 0
                string_to_size_for_dis = actual_string_from_micro
                if debug_mode:
                    print(f"DEBUG L{line_num} calculate_pseudo_op_size DIS %\"{micro_name_for_dis}\"%: Substituted string is '{string_to_size_for_dis}' (len {len(string_to_size_for_dis)})")

            
            if dis_ops['format'] == 1:
                n_words_from_operand = dis_ops['n']
                if n_words_from_operand == 0: 
                    num_chars = len(string_to_size_for_dis) + 2 
                    chars_per_word = 10
                    num_words_for_string = math.ceil(num_chars / chars_per_word)
                    if debug_mode: print(f"DEBUG L{line_num} calculate_pseudo_op_size DIS N=0: string='{string_to_size_for_dis}', len={len(string_to_size_for_dis)}, num_chars_packed={num_chars}, words={num_words_for_string}")
                    return num_words_for_string * 60
                if debug_mode: print(f"DEBUG L{line_num} calculate_pseudo_op_size DIS N={n_words_from_operand}: words={n_words_from_operand}")
                return n_words_from_operand * 60
            elif dis_ops['format'] == 2: 
                num_chars = len(string_to_size_for_dis) + 2 
                chars_per_word = 10
                num_words = math.ceil(num_chars / chars_per_word)
                if debug_mode: print(f"DEBUG L{line_num} calculate_pseudo_op_size DIS Fmt2: string='{string_to_size_for_dis}', len={len(string_to_size_for_dis)}, num_chars_packed={num_chars}, words={num_words}")
                return num_words * 60
        except (ExpressionError, AsmException) as e:
            if "Undefined symbol" in str(e) and "dis_ops" in locals() and dis_ops.get('format') == 1:
                 if not error_reporter.has_error_on_line(line_num): 
                      error_reporter.add_error(f"Cannot determine size for DIS: N value '{dis_ops.get('n_str', operand_str.split(',')[0])}' is undefined.", line_num, code='U')
                 return None 
            elif not error_reporter.has_error_on_line(line_num): 
                 error_reporter.add_error(f"Error parsing DIS operand '{operand_str}' for size: {e}", line_num, code='E')
            return None
    
    operand_str_for_calc = operand_str
    if mnemonic not in ('VFD', 'IFC', 'TITLE', 'TTL', 'COMMENT', 'CTEXT', 'XTEXT', 'MICRO', 'DIS'): 
         if mnemonic in ("EQU", "=") and operand_str.strip() == '*':
             operand_str_for_calc = "*" 
         else:
             operand_str_for_calc = operand_str.split('.')[0].split('*')[0].strip()


    if mnemonic == "DATA" or mnemonic == "CON":
        operands = operand_str_for_calc.split(',')
        num_operands = len([op for op in operands if op.strip()]) 
        return num_operands * 60
    elif mnemonic == "VFD":
        total_bits = 0
        fields = operand_str.split('.')[0].split('*')[0].rstrip().split(',') 
        for field in fields:
            field = field.strip();
            if not field: continue
            parts = field.split('/', 1)
            if len(parts) != 2: raise ExpressionError(f"Invalid VFD field: '{field}' for sizing")
            try:
                width_val, width_type, _ = evaluate_expression(parts[0].strip(), symbol_table, state, line_num, assembler, suppress_undefined_error=True)
                if width_type != 'absolute' or not isinstance(width_val, int) or not (0 < width_val <= 60):
                    if not error_reporter.has_error_on_line(line_num): 
                        error_reporter.add_error(f"VFD field width '{parts[0].strip()}' is not a valid absolute integer (1-60) in Pass 1. Value: {width_val}, Type: {width_type}", line_num, code='V')
                    return None 
                total_bits += width_val
            except ExpressionError as e:
                if "Undefined symbol" in str(e): 
                    if not error_reporter.has_error_on_line(line_num):
                        error_reporter.add_error(f"Cannot determine VFD size: width expression '{parts[0].strip()}' contains undefined symbol.", line_num, code='U')
                elif not error_reporter.has_error_on_line(line_num): 
                    error_reporter.add_error(f"Error parsing VFD field width '{parts[0].strip()}' for size: {e}", line_num, code='E')
                return None 
            except Exception as e_unexp: 
                if not error_reporter.has_error_on_line(line_num):
                    error_reporter.add_error(f"Unexpected error parsing VFD field width '{parts[0].strip()}' for size: {e_unexp}", line_num, code='F')
                return None

        return total_bits
    elif mnemonic in ["BSS", "BSSZ"]:
        try:
            value, val_type, _ = evaluate_expression(operand_str_for_calc, symbol_table, state, line_num, assembler, suppress_undefined_error=True)
            if val_type != 'absolute': raise ExpressionError(f"{mnemonic} requires absolute value for size")
            if not isinstance(value, int) or value < 0: raise ExpressionError(f"{mnemonic} requires non-negative integer value for size")
            if debug_mode:
                print(f">>> DEBUG LC: L{line_num} SizeCalc {mnemonic}: Size={value} words")
            return value * 60
        except ExpressionError as e:
            if "Undefined symbol" in str(e): 
                 if not error_reporter.has_error_on_line(line_num):
                      error_reporter.add_error(f"Cannot determine size for {mnemonic}: operand '{operand_str_for_calc}' contains undefined symbol.", line_num, code='U')
            elif not error_reporter.has_error_on_line(line_num): 
                 error_reporter.add_error(f"Error parsing {mnemonic} operand '{operand_str_for_calc}' for size: {e}", line_num, code='E')
            return None 
    return 0 

# pseudo_op_handlers.py v2.18
