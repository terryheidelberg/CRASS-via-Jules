# expression.py v1.43
"""
Expression Evaluation for CRASS Assembler.
Handles constants, symbols, operators (+, -, *, /, ^), parentheses, relocatability,
and data item parsing for LIT/DATA.
evaluate_data_item now handles simple numbers/symbols directly.
parse_dis_operands moved here and updated to strip comments.

v1.41: - Correctly implement DIS %"micro_name"% parsing within parse_dis_operands
         without relying on a globally defined DIS_MICRO_NAME_LITERAL_REGEX.
       - Ensure generate_dis_words correctly uses DISPLAY_CODE_MAP for this case.
v1.42: - Updated character set maps based on improved-charset-table.txt.
       - DISPLAY_CODE_MAP now defaults to CDC 64-character ASCII subset.
       - INTERNAL_CODE_MAP now maps to Internal BCD.
       - Added EXTERNAL_BCD_MAP and ASCII_6BIT_SUBSET_MAP.
       - _parse_char_constant and generate_dis_words updated to use new maps
         based on assembler_state.current_code.
v1.43: - Add `suppress_undefined_error` parameter to evaluation functions to
         control error reporting from symbol_table.lookup during speculative parsing.
"""

import re
import math 
from typing import TYPE_CHECKING, List, Optional, Tuple, Dict, Any
if TYPE_CHECKING:
    from assembler_state import AssemblerState
    from symbol_table import SymbolTable
    from errors import ErrorReporter, AsmException 
    from crass import Assembler


# --- Character Set Maps from improved-charset-table.txt ---

# Default Display Code: CDC 64-character ASCII subset (D 64-as-cs)
# This is used when assembler_state.current_code == 'D' or for DIS %"name"%
DISPLAY_CODE_MAP_ASCII_SUBSET = {
    ':': 0o00, 'A': 0o01, 'B': 0o02, 'C': 0o03, 'D': 0o04, 'E': 0o05, 'F': 0o06, 'G': 0o07,
    'H': 0o10, 'I': 0o11, 'J': 0o12, 'K': 0o13, 'L': 0o14, 'M': 0o15, 'N': 0o16, 'O': 0o17,
    'P': 0o20, 'Q': 0o21, 'R': 0o22, 'S': 0o23, 'T': 0o24, 'U': 0o25, 'V': 0o26, 'W': 0o27,
    'X': 0o30, 'Y': 0o31, 'Z': 0o32, '0': 0o33, '1': 0o34, '2': 0o35, '3': 0o36, '4': 0o37,
    '5': 0o40, '6': 0o41, '7': 0o42, '8': 0o43, '9': 0o44, '+': 0o45, '-': 0o46, '*': 0o47,
    '/': 0o50, '(': 0o51, ')': 0o52, '$': 0o53, '=': 0o54, ' ': 0o55, ',': 0o56, '.': 0o57,
    '#': 0o60, '[': 0o61, ']': 0o62, '%': 0o63, '"': 0o64, '_': 0o65, '!': 0o66, '&': 0o67,
    "'": 0o70, '?': 0o71, '<': 0o72, '>': 0o73, '@': 0o74, '\\': 0o75, '^': 0o76, ';': 0o77,
}
# DISPLAY_CODE_MAP is the general default, pointing to the ASCII subset.
DISPLAY_CODE_MAP = DISPLAY_CODE_MAP_ASCII_SUBSET

# CDC 64-character set (D 64-cs) - For reference or specific future use.
# Graphics for ~n are illustrative placeholders.
DISPLAY_CODE_MAP_64_CHAR_SET = {
    ':': 0o00, 'A': 0o01, 'B': 0o02, 'C': 0o03, 'D': 0o04, 'E': 0o05, 'F': 0o06, 'G': 0o07,
    'H': 0o10, 'I': 0o11, 'J': 0o12, 'K': 0o13, 'L': 0o14, 'M': 0o15, 'N': 0o16, 'O': 0o17,
    'P': 0o20, 'Q': 0o21, 'R': 0o22, 'S': 0o23, 'T': 0o24, 'U': 0o25, 'V': 0o26, 'W': 0o27,
    'X': 0o30, 'Y': 0o31, 'Z': 0o32, '0': 0o33, '1': 0o34, '2': 0o35, '3': 0o36, '4': 0o37,
    '5': 0o40, '6': 0o41, '7': 0o42, '8': 0o43, '9': 0o44, '+': 0o45, '-': 0o46, '*': 0o47,
    '/': 0o50, '(': 0o51, ')': 0o52, '$': 0o53, '=': 0o54, ' ': 0o55, ',': 0o56, '.': 0o57,
    '~1': 0o60, '[': 0o61, ']': 0o62, '%': 0o63, '~2': 0o64, '~3': 0o65, '~4': 0o66, '~5': 0o67, 
    '~6': 0o70, '~7': 0o71, '<': 0o72, '>': 0o73, '~8': 0o74, '~9': 0o75, '~10': 0o76, ';': 0o77,
}

# Internal BCD (CODE I, and for char constants when CODE A)
INTERNAL_BCD_MAP = {
    '0': 0o00, '1': 0o01, '2': 0o02, '3': 0o03, '4': 0o04, '5': 0o05, '6': 0o06, '7': 0o07,
    '8': 0o10, '9': 0o11, '^': 0o12, '=': 0o13, '#': 0o14, ':': 0o15, '"': 0o16, '_': 0o17, 
    '/': 0o20, 'S': 0o21, 'T': 0o22, 'U': 0o23, 'V': 0o24, 'W': 0o25, 'X': 0o26, 'Y': 0o27,
    'Z': 0o30, '?': 0o31, '\\': 0o32, ',': 0o33, '(': 0o34, '>': 0o35, '>': 0o37, # Note: 035 is GE (≥), using '>' as per table for now.
    '-': 0o40, 'A': 0o41, 'B': 0o42, 'C': 0o43, 'D': 0o44, 'E': 0o45, 'F': 0o46, 'G': 0o47,
    'H': 0o50, 'I': 0o51, ';': 0o52, '$': 0o53, '*': 0o54, ' ': 0o55, '!': 0o56, '&': 0o57,
    '+': 0o60, 'J': 0o61, 'K': 0o62, 'L': 0o63, 'M': 0o64, 'N': 0o65, 'O': 0o66, 'P': 0o67,
    'Q': 0o70, 'R': 0o71, '@': 0o72, '.': 0o73, ')': 0o74, '[': 0o75, ']': 0o76, "'": 0o77,
}
INTERNAL_CODE_MAP = INTERNAL_BCD_MAP # Default for "internal" operations if not specified

# External BCD (CODE E)
EXTERNAL_BCD_MAP = {
    ':': 0o00, '1': 0o01, '2': 0o02, '3': 0o03, '4': 0o04, '5': 0o05, '6': 0o06, '7': 0o07,
    '8': 0o10, '9': 0o11, '0': 0o12, '=': 0o13, '"': 0o14, '@': 0o15, '%': 0o16, '[': 0o17,
    ' ': 0o20, '/': 0o21, 'S': 0o22, 'T': 0o23, 'U': 0o24, 'V': 0o25, 'W': 0o26, 'X': 0o27,
    'Y': 0o30, 'Z': 0o31, ']': 0o32, ',': 0o33, '(': 0o34, '_': 0o35, '#': 0o36, '&': 0o37,
    '-': 0o40, 'J': 0o41, 'K': 0o42, 'L': 0o43, 'M': 0o44, 'N': 0o45, 'O': 0o46, 'P': 0o47,
    'Q': 0o50, 'R': 0o51, '!': 0o52, '$': 0o53, '*': 0o54, "'": 0o55, '?': 0o56, '>': 0o57,
    '+': 0o60, 'A': 0o61, 'B': 0o62, 'C': 0o63, 'D': 0o64, 'E': 0o65, 'F': 0o66, 'G': 0o67,
    'H': 0o70, 'I': 0o71, '<': 0o72, '.': 0o73, ')': 0o74, '\\': 0o75, '^': 0o76, ';': 0o77,
}

# ASCII 6-bit Subset (Used for DIS when CODE A is active)
ASCII_6BIT_SUBSET_MAP = { 
    ' ': 0o00, '!': 0o01, '"': 0o02, '#': 0o03, '$': 0o04, '%': 0o05, '&': 0o06, "'": 0o07,
    '(': 0o10, ')': 0o11, '*': 0o12, '+': 0o13, ',': 0o14, '-': 0o15, '.': 0o16, '/': 0o17,
    '0': 0o20, '1': 0o21, '2': 0o22, '3': 0o23, '4': 0o24, '5': 0o25, '6': 0o26, '7': 0o27,
    '8': 0o30, '9': 0o31, ':': 0o32, ';': 0o33, '<': 0o34, '=': 0o35, '>': 0o36, '?': 0o37,
    '@': 0o40, 'A': 0o41, 'B': 0o42, 'C': 0o43, 'D': 0o44, 'E': 0o45, 'F': 0o46, 'G': 0o47,
    'H': 0o50, 'I': 0o51, 'J': 0o52, 'K': 0o53, 'L': 0o54, 'M': 0o55, 'N': 0o56, 'O': 0o57,
    'P': 0o60, 'Q': 0o61, 'R': 0o62, 'S': 0o63, 'T': 0o64, 'U': 0o65, 'V': 0o66, 'W': 0o67,
    'X': 0o70, 'Y': 0o71, 'Z': 0o72, '[': 0o73, '\\': 0o74, ']': 0o75, '^': 0o76, '_': 0o77,
}

# Blanks and Zeros for different modes
DISPLAY_CODE_BLANK = DISPLAY_CODE_MAP_ASCII_SUBSET[' '] 
DISPLAY_CODE_ZERO_CHAR = DISPLAY_CODE_MAP_ASCII_SUBSET['0']  # Character '0' for Display Code

INTERNAL_BCD_BLANK = INTERNAL_BCD_MAP[' '] 
INTERNAL_BCD_ZERO_CHAR = INTERNAL_BCD_MAP['0']  # Character '0' for Internal BCD
INTERNAL_BCD_BINARY_ZERO = 0o00 # Actual binary zero (0o00) for padding/fill in Internal BCD mode

EXTERNAL_BCD_BLANK = EXTERNAL_BCD_MAP[' '] 
EXTERNAL_BCD_ZERO_CHAR = EXTERNAL_BCD_MAP['0'] # Character '0' for External BCD

ASCII_6BIT_BLANK = ASCII_6BIT_SUBSET_MAP[' '] 
ASCII_6BIT_ZERO_CHAR = ASCII_6BIT_SUBSET_MAP['0'] # Character '0' for ASCII 6-bit subset

ZERO_6BIT = 0 # Universal 6-bit binary zero for null terminators in DIS

CHAR_DATA_FMT1_REGEX = re.compile(r'([+-]?)(\d+)([CHARLZHA])(.*)')
CHAR_DATA_FMT2_REGEX = re.compile(r'([+-]?)([CHARLZHA])(.)(.*)') 

CHAR_CONST_REGEX = re.compile(r'(\d+)([CHARLZHA])(.*)')
NUM_CONST_REGEX = re.compile(r'([0-9]+)([BbDdOo]?)')
REG_REGEX = re.compile(r'^[ABX][0-7]$', re.IGNORECASE)
SYM_REGEX = re.compile(r'^[A-Za-z][A-Za-z0-9]{0,7}$')
LITERAL_REGEX = re.compile(r'^=([+-]?)(.*)')
INNER_PAREN_REGEX = re.compile(r'\(([^()]*)\)')
COMMENT_START_REGEX = re.compile(r'(\.|\*).*')
MICRO_REF_FIND_REGEX = re.compile(r'%([A-Za-z][A-Za-z0-9]{0,7})%')

MAX_EVAL_DEPTH = 50
MAX_MICRO_DEPTH = 20 

EQU_STAR_SYMBOLS_FOR_DEBUG = {'START', 'LOOP', 'NEXT', 'NEXT2', 'EXIT', 'BUFF'}

class ExpressionError(ValueError):
    pass

def substitute_micros(text: str, assembler: 'Assembler', line_num: int, depth=0) -> str:
    if depth > MAX_MICRO_DEPTH:
        if not assembler.error_reporter.has_error_on_line(line_num):
             assembler.error_reporter.add_error(f"Maximum micro substitution depth ({MAX_MICRO_DEPTH}) exceeded", line_num, code='M')
        return text
    substituted_text = text
    match = MICRO_REF_FIND_REGEX.search(substituted_text)
    while match:
        micro_name = match.group(1).upper()
        start, end = match.span()
        micro_value = assembler.micro_definitions.get(micro_name)
        if micro_value is not None:
            substituted_text = substituted_text[:start] + micro_value + substituted_text[end:]
            return substitute_micros(substituted_text, assembler, line_num, depth + 1)
        else:
            # For micro substitution, we don't want symbol lookup to report errors if the micro isn't found
            # as a symbol. It's either a defined micro or an error.
            sym_entry = assembler.symbol_table.lookup(micro_name, line_num, assembler.state.current_qualifier, suppress_undefined_error=True)
            if sym_entry and sym_entry['attrs'].get('value_is_char'):
                 char_value = sym_entry['value']
                 substituted_text = substituted_text[:start] + char_value + substituted_text[end:]
                 return substitute_micros(substituted_text, assembler, line_num, depth + 1)
            else:
                 if not assembler.error_reporter.has_error_on_line(line_num):
                      assembler.error_reporter.add_error(f"Undefined micro '%{micro_name}%'", line_num, code='U')
                 match = MICRO_REF_FIND_REGEX.search(substituted_text, end) 
    return substituted_text


def _apply_reloc_rules(val1, type1, op, val2, type2):
    new_val = 0; new_type = 'error'; MASK_60 = (1 << 60) - 1
    if op in ('+', '-'):
        if type1 == 'absolute' and type2 == 'absolute': new_type = 'absolute'
        elif type1 == 'absolute' and type2 == 'relocatable': new_type = 'relocatable'
        elif type1 == 'relocatable' and type2 == 'absolute': new_type = 'relocatable'
        elif type1 == 'relocatable' and type2 == 'relocatable':
            if op == '-': new_type = 'absolute'
            else: raise ExpressionError(f"Illegal op: relocatable + relocatable")
        elif type1 == 'external' or type2 == 'external':
             if type1 == 'absolute' and type2 == 'external': new_type = 'external'
             elif type1 == 'external' and type2 == 'absolute': new_type = 'external'
             elif type1 == 'external' and type2 == 'external': raise ExpressionError(f"Illegal op: external {op} external")
             else: raise ExpressionError(f"Illegal op: external {op} relocatable")
        elif type1 == 'literal_addr' or type2 == 'literal_addr':
            if type1 == 'literal_addr' and type2 == 'literal_addr':
                 if op == '-': new_type = 'absolute'
                 else: raise ExpressionError(f"Illegal op: literal_addr + literal_addr")
            elif type1 == 'literal_addr' and type2 == 'absolute': new_type = 'literal_addr'
            elif type1 == 'absolute' and type2 == 'literal_addr': new_type = 'literal_addr'
            else: raise ExpressionError(f"Unsupported op with literal address: {type1} {op} {type2}")
        else: raise ExpressionError(f"Unsupported operation: {type1} {op} {type2}")
    elif op in ('*', '/'):
        type1_eff = 'absolute' if type1 == 'literal_addr' else type1
        type2_eff = 'absolute' if type2 == 'literal_addr' else type2
        if type1_eff == 'absolute' and type2_eff == 'absolute': new_type = 'absolute'
        else: raise ExpressionError(f"Illegal op: {type1} {op} {type2} (requires absolute)")
    elif op == '^':
        type1_eff = 'absolute' if type1 == 'literal_addr' else type1
        type2_eff = 'absolute' if type2 == 'literal_addr' else type2
        if type1_eff == 'absolute' and type2_eff == 'absolute': new_type = 'absolute'
        else: raise ExpressionError(f"Illegal op: {type1} ^ {type2} (requires absolute)")
    else: raise ExpressionError(f"Internal error: Unknown operator '{op}'")
    if op == '+': new_val = val1 + val2
    elif op == '-': new_val = val1 - val2
    elif op == '*': new_val = val1 * val2
    elif op == '/': new_val = 0 if val2 == 0 else int(val1 / val2) 
    elif op == '^': new_val = val1 ^ val2 
    return new_val, new_type


def _parse_char_constant(n_str, type_char, char_string, assembler_state: 'AssemblerState'):
    try: n = int(n_str)
    except ValueError: raise ExpressionError(f"Invalid character count '{n_str}'")
    if n <= 0: return 0, 'absolute', None
    
    current_code_mode = assembler_state.current_code
    char_map_to_use: Dict[str, int]
    blank_for_invalid: int 
    zero_char_for_fill: int # Code for '0' character of the mode (for C,L,Z type fill)
    binary_zero_for_fill: int = INTERNAL_BCD_BINARY_ZERO # Actual binary zero (0o00)

    if current_code_mode == 'A': # ASCII 6-bit subset (uses Internal BCD map for char consts)
        char_map_to_use = INTERNAL_BCD_MAP
        blank_for_invalid = INTERNAL_BCD_BLANK
        zero_char_for_fill = INTERNAL_BCD_ZERO_CHAR # '0' char in Internal BCD
    elif current_code_mode == 'E': # External BCD
        char_map_to_use = EXTERNAL_BCD_MAP
        blank_for_invalid = EXTERNAL_BCD_BLANK
        zero_char_for_fill = EXTERNAL_BCD_ZERO_CHAR
    elif current_code_mode == 'I': # Internal BCD
        char_map_to_use = INTERNAL_BCD_MAP
        blank_for_invalid = INTERNAL_BCD_BLANK
        zero_char_for_fill = INTERNAL_BCD_ZERO_CHAR
    else: # Default 'D' - Display Code (ASCII Subset)
        char_map_to_use = DISPLAY_CODE_MAP_ASCII_SUBSET
        blank_for_invalid = DISPLAY_CODE_BLANK
        zero_char_for_fill = DISPLAY_CODE_ZERO_CHAR

    max_chars = 10; actual_chars_in_string = len(char_string); chars_to_process = min(n, actual_chars_in_string, max_chars)
    processed_string = char_string[:chars_to_process]; bits_list = []
    for char_in_const in processed_string:
        char_upper = char_in_const.upper()
        code = char_map_to_use.get(char_upper, blank_for_invalid)
        bits_list.append(code)
        
    result_value = 0; num_target_chars = min(n, max_chars)
    
    fill_code_for_justification: int
    # Type C, L, Z: fill with "zero" of the mode.
    # Type H, A, R: fill with "blank" of the mode.
    if type_char in ('H', 'A', 'R'): 
        fill_code_for_justification = blank_for_invalid
    elif type_char in ('C', 'L', 'Z'): 
        # COMPASS manual implies "zero" fill for C,L,Z.
        # For Internal BCD (and thus CODE A char consts), '0' is 0o00.
        # For Display Code, '0' is 0o33. For External BCD, '0' is 0o12.
        if current_code_mode in ('A', 'I'):
            fill_code_for_justification = binary_zero_for_fill # True binary zero
        else: # D, E
            fill_code_for_justification = zero_char_for_fill # The character '0'
    else: raise ExpressionError(f"Internal error: Unknown char const type '{type_char}'")

    if type_char in ('L', 'C', 'Z', 'H'): 
         justified_codes = bits_list[:num_target_chars]
         while len(justified_codes) < num_target_chars: justified_codes.append(fill_code_for_justification)
         temp_val = 0
         for code in justified_codes: temp_val = (temp_val << 6) | code
         result_value = temp_val << (60 - num_target_chars * 6) 
    elif type_char in ('R', 'A'): 
         justified_codes = bits_list[:num_target_chars]
         while len(justified_codes) < num_target_chars: justified_codes.insert(0, fill_code_for_justification) 
         temp_val = 0
         for code in justified_codes: temp_val = (temp_val << 6) | code
         result_value = temp_val 
    else: raise ExpressionError(f"Internal error: Unknown char const type '{type_char}'")
    return result_value, 'absolute', None


def _parse_char_data_item_delimited(type_char, delimiter, rest_of_string, assembler_state: 'AssemblerState'):
    end_delim_index = rest_of_string.find(delimiter)
    if end_delim_index == -1: raise ExpressionError(f"Missing closing delimiter '{delimiter}'")
    char_string = rest_of_string[:end_delim_index]
    n = len(char_string)
    val, type, _ = _parse_char_constant(str(n), type_char, char_string, assembler_state)
    return val, type, None


def evaluate_data_item(item_str, symbol_table: 'SymbolTable', assembler_state: 'AssemblerState', line_num, assembler: 'Assembler', suppress_undefined_error: bool = False):
    item_str_orig = item_str = item_str.strip()
    if not item_str: raise ExpressionError("Empty data item string.")

    try:
        item_str = substitute_micros(item_str, assembler, line_num)
    except AsmException as e:
        raise ExpressionError(f"Error during micro substitution in '{item_str_orig}': {e}")

    sign = 1
    if item_str.startswith('+'): item_str = item_str[1:]
    elif item_str.startswith('-'): sign = -1; item_str = item_str[1:]
    item_str = item_str.strip()
    if not item_str: raise ExpressionError("Data item contains only a sign after substitution.")

    match_char1 = CHAR_DATA_FMT1_REGEX.fullmatch(item_str)
    if match_char1:
        _, n_str, type_char, char_string = match_char1.groups()
        val, _, _ = _parse_char_constant(n_str, type_char.upper(), char_string, assembler_state)
        if sign == -1: val = val ^ ((1<<60)-1) 
        return val, 'absolute', None

    match_char2 = CHAR_DATA_FMT2_REGEX.match(item_str)
    if match_char2:
         _, type_char, delimiter, rest = match_char2.groups()
         try:
              val, _, _ = _parse_char_data_item_delimited(type_char.upper(), delimiter, rest, assembler_state)
              if sign == -1: val = val ^ ((1<<60)-1) 
              return val, 'absolute', None
         except ExpressionError: pass 

    try:
        value, type, block = evaluate_expression(item_str, symbol_table, assembler_state, line_num, assembler, suppress_undefined_error=suppress_undefined_error)
        if sign == -1:
             if type != 'absolute': raise ExpressionError("Cannot apply unary minus to non-absolute data item")
             value = -value 
             type = 'absolute'
             block = None
        return value, type, block
    except ExpressionError as e_expr:
        raise ExpressionError(f"Cannot parse data item '{item_str_orig}' (after micro sub: '{item_str}') as character, numeric, or expression: {e_expr}")


def _parse_single_element(element_str, symbol_table: 'SymbolTable', assembler_state: 'AssemblerState', line_num, assembler: 'Assembler', suppress_undefined_error: bool = False):
    element_str = element_str.strip()
    if not element_str: raise ExpressionError("Empty element in expression")
    debug_mode = getattr(assembler_state, 'debug_mode', False)
    pass_num = assembler_state.pass_number

    if element_str == '*':
        if pass_num == 1:
            if assembler_state.lc_is_absolute_due_to_loc: 
                lc_val = assembler_state.location_counter
                lc_type = 'absolute'
                lc_block = '*ABS*' 
                if debug_mode: print(f">>> DEBUG LC: L{line_num} Eval '*': Pass 1 (LOC Active), AbsLC={lc_val:o}, Block={lc_block}, Type={lc_type}")
                return lc_val, lc_type, lc_block
            else: 
                lc_val = assembler_state.location_counter 
                lc_block = assembler_state.current_block
                lc_type = 'absolute' if lc_block == '*ABS*' else 'relocatable'
                if debug_mode: print(f">>> DEBUG LC: L{line_num} Eval '*': Pass 1, RelLC={lc_val:o}, Block={lc_block}, Type={lc_type}")
                return lc_val, lc_type, lc_block
        else: 
            lc_val = assembler_state.location_counter
            lc_type = 'absolute'
            if debug_mode: print(f">>> DEBUG LC: L{line_num} Eval '*': Pass 2, AbsLC={lc_val:o}, Type={lc_type}")
            return lc_val, lc_type, None 

    elif element_str == '$': return max(0, assembler_state.position_counter - 1), 'absolute', None
    elif element_str == '*P': return assembler_state.position_counter, 'absolute', None

    if REG_REGEX.match(element_str): raise ExpressionError(f"Register '{element_str}' invalid in expression")

    match_literal = LITERAL_REGEX.match(element_str)
    if match_literal:
        sign_part, content_part = match_literal.groups()
        try:
            lit_value, lit_type, _ = evaluate_data_item(sign_part + content_part, symbol_table, assembler_state, line_num, assembler, suppress_undefined_error=suppress_undefined_error)
            if lit_type != 'absolute': raise ExpressionError(f"Literal content '{content_part}' must be absolute")
            symbol_table.add_literal(lit_value, line_num)
            lit_addr = symbol_table.lookup_literal_address(lit_value, line_num)
            if lit_addr is None:
                 if pass_num == 1: return 0, 'literal_addr', None
                 raise ExpressionError(f"Failed to find address for literal '{element_str}'")
            return lit_addr, 'literal_addr', None
        except ExpressionError as e: raise ExpressionError(f"Invalid literal '{element_str}': {e}")

    match_char = CHAR_CONST_REGEX.fullmatch(element_str)
    if match_char:
        n_str, type_char, char_string = match_char.groups()
        if not n_str.isdigit(): raise ExpressionError(f"Invalid char constant in expression: '{element_str}'")
        return _parse_char_constant(n_str, type_char.upper(), char_string, assembler_state)

    match_num = NUM_CONST_REGEX.fullmatch(element_str)
    if match_num:
        num_part, base_suffix = match_num.groups()
        base = 10; base_suffix = base_suffix.upper() if base_suffix else ''; using_default_base = False
        if base_suffix in ('B', 'O'): base = 8
        elif base_suffix == 'D': base = 10
        elif not base_suffix:
            using_default_base = True; current_base_state = assembler_state.current_base
            if current_base_state == 'O': base = 8
            elif current_base_state == 'M': base = 8 if all(c in '01234567' for c in num_part) else 10
            else: base = 10
        try:
             if base == 8 and not all(c in '01234567' for c in num_part): raise ValueError(f"contains invalid octal digits")
             value = int(num_part, base)
             return value, 'absolute', None
        except ValueError as ve: raise ExpressionError(f"Invalid numeric constant '{element_str}' for base {base}: {ve}")

    if SYM_REGEX.match(element_str):
        sym_entry = symbol_table.lookup(element_str, line_num, assembler_state.current_qualifier, suppress_undefined_error=suppress_undefined_error)
        if sym_entry:
            rel_value = sym_entry['value']
            sym_attrs = sym_entry['attrs']
            sym_type = sym_attrs.get('type', 'absolute')
            sym_block = sym_attrs.get('block', None)
            is_equ_star = sym_attrs.get('equ_star', False)

            if debug_mode:
                print(f">>> DEBUG LC: L{line_num} Eval Symbol: '{element_str}' (Pass {pass_num})")
                print(f"    Lookup Result: RelValue={rel_value:o}, Type={sym_type}, Block={sym_block}, EQU*={is_equ_star}")

            if pass_num == 2:
                if sym_type == 'relocatable' and sym_block and sym_block != '*ABS*':
                    block_base = assembler.block_base_addresses.get(sym_block)
                    if block_base is None:
                        if debug_mode: print(f"    ERROR: Base address for block '{sym_block}' not found!")
                        raise ExpressionError(f"Internal: Base address for block '{sym_block}' not found for symbol '{element_str}'.")
                    abs_value = rel_value + block_base
                    if debug_mode:
                        print(f"    Block Base ('{sym_block}') = {block_base:o}")
                        print(f"    Calculated AbsValue = {rel_value:o} + {block_base:o} = {abs_value:o}")
                    return abs_value, 'absolute', None
                else:
                    if debug_mode: print(f"    Using Value As Is (Absolute or *ABS* Block): {rel_value:o}")
                    return rel_value, 'absolute', None
            else: 
                return rel_value, sym_type, sym_block
        else:
            # If lookup returned None and suppress_undefined_error was True, it means the symbol is undefined
            # but we shouldn't report it here. The ExpressionError will be raised by the caller.
            if suppress_undefined_error:
                raise ExpressionError(f"Undefined symbol '{element_str}'") # Raise to be caught by caller
            # If not suppressing, the error was already added by symbol_table.lookup
            # We still need to raise to signal failure to the expression evaluator.
            raise ExpressionError(f"Undefined symbol '{element_str}'")


    raise ExpressionError(f"Cannot parse element '{element_str}'")


def _parse_term(term_str, symbol_table: 'SymbolTable', assembler_state: 'AssemblerState', line_num, assembler: 'Assembler', suppress_undefined_error: bool = False):
    term_str = term_str.strip()
    if not term_str: raise ExpressionError("Empty term string")
    parts = re.split(r'([*/])', term_str); parts = [p.strip() for p in parts if p.strip()]
    if not parts: raise ExpressionError(f"Cannot parse term '{term_str}'")
    try:
        current_value, current_type, current_block = _parse_single_element(parts[0], symbol_table, assembler_state, line_num, assembler, suppress_undefined_error=suppress_undefined_error)
    except ExpressionError as e: raise ExpressionError(f"Term '{parts[0]}': {e}")
    i = 1
    while i < len(parts):
        op = parts[i]
        if op not in ('*', '/'): raise ExpressionError(f"Expected */ found '{op}'")
        if i + 1 >= len(parts): raise ExpressionError(f"Missing element after {op}")
        next_element_str = parts[i+1]
        try:
            next_value, next_type, next_block = _parse_single_element(next_element_str, symbol_table, assembler_state, line_num, assembler, suppress_undefined_error=suppress_undefined_error)
        except ExpressionError as e: raise ExpressionError(f"Term '{next_element_str}': {e}")
        try:
            current_value, current_type = _apply_reloc_rules(current_value, current_type, op, next_value, next_type)
            current_block = None 
        except ExpressionError as e: raise ExpressionError(f"Term '{term_str}': {e}")
        i += 2
    return (current_value, current_type, current_block)


def _evaluate_simple_expression(expr_str, symbol_table: 'SymbolTable', assembler_state: 'AssemblerState', line_num, assembler: 'Assembler', suppress_undefined_error: bool = False):
    expr_str_orig = expr_str; expr_str = expr_str.strip()
    if not expr_str: return (0, 'absolute', None)
    initial_sign = 1
    if expr_str.startswith('+'): expr_str = expr_str[1:].lstrip()
    elif expr_str.startswith('-'): initial_sign = -1; expr_str = expr_str[1:].lstrip()
    if not expr_str:
         if initial_sign == -1: raise ExpressionError("Expression is only '-'")
         else: return(0, 'absolute', None)
    parts_low = re.split(r'([+\-^])', expr_str); parts_low = [p.strip() for p in parts_low if p.strip()]
    if not parts_low: raise ExpressionError(f"Cannot parse '{expr_str_orig}'")
    if parts_low[0] == '-': 
         if len(parts_low) < 2: raise ExpressionError(f"Invalid unary: '{expr_str_orig}'")
         initial_sign *= -1; parts_low = parts_low[1:]
         if not parts_low: raise ExpressionError(f"Invalid unary: '{expr_str_orig}'")
    elif parts_low[0] == '+': 
        if len(parts_low) < 2: return (0, 'absolute', None) 
        parts_low = parts_low[1:]
        if not parts_low: return (0, 'absolute', None) 
    try:
        current_value, current_type, current_block = _parse_term(parts_low[0], symbol_table, assembler_state, line_num, assembler, suppress_undefined_error=suppress_undefined_error)
    except ExpressionError as e: raise ExpressionError(f"First term '{parts_low[0]}': {e}")
    if initial_sign == -1:
        if current_type != 'absolute' and current_type != 'literal_addr': raise ExpressionError(f"Unary minus on non-absolute: '{parts_low[0]}'")
        current_value = -current_value
        if current_type == 'literal_addr': current_type = 'absolute' 
        current_block = None 
    i = 1
    while i < len(parts_low):
        op = parts_low[i]
        if op not in ('+', '-', '^'): raise ExpressionError(f"Expected + - ^ found '{op}'")
        next_term_str = "0" if i + 1 >= len(parts_low) else parts_low[i+1] 
        try:
            next_value, next_type, next_block = _parse_term(next_term_str, symbol_table, assembler_state, line_num, assembler, suppress_undefined_error=suppress_undefined_error)
        except ExpressionError as e:
             if next_term_str == "0": raise 
             raise ExpressionError(f"Term '{next_term_str}' after '{op}': {e}")
        try:
            current_value, current_type = _apply_reloc_rules(current_value, current_type, op, next_value, next_type)
            if current_type == 'relocatable':
                if current_block is None and next_block is not None and next_type == 'relocatable':
                    current_block = next_block
            else:
                current_block = None
        except ExpressionError as e: raise ExpressionError(f"Expr '{expr_str_orig}': {e}")
        i += 2
    return (current_value, current_type, current_block)


def evaluate_expression(expr_str, symbol_table: 'SymbolTable', assembler_state: 'AssemblerState', line_num, assembler: 'Assembler', depth=0, suppress_undefined_error: bool = False):
    if expr_str is None: return (0, 'absolute', None)
    expr_str_orig = expr_str; expr_str = expr_str.strip()
    if not expr_str: return (0, 'absolute', None)
    if depth > MAX_EVAL_DEPTH: raise ExpressionError(f"Max recursion depth exceeded.")

    try:
        expr_str = substitute_micros(expr_str, assembler, line_num)
    except AsmException as e:
        raise ExpressionError(f"Error during micro substitution in '{expr_str_orig}': {e}")

    while True:
        match = INNER_PAREN_REGEX.search(expr_str)
        if not match: break
        sub_expr = match.group(1); start_idx, end_idx = match.span()
        try:
            sub_value, sub_type, sub_block = evaluate_expression(sub_expr, symbol_table, assembler_state, line_num, assembler, depth + 1, suppress_undefined_error=suppress_undefined_error)
        except ExpressionError as e: raise ExpressionError(f"Sub-expr '({sub_expr})': {e}")

        if sub_type == 'literal_addr': sub_type = 'absolute'
        
        sub_value_str = str(sub_value); prefix = expr_str[:start_idx]; suffix = expr_str[end_idx:]
        space_before = " " if (prefix and prefix[-1] not in "+-*/^( ") else ""; space_after = " " if (suffix and suffix[0] not in "+-*/^), ") else ""
        expr_str = prefix + space_before + sub_value_str + space_after + suffix; expr_str = expr_str.strip()

    try:
        result_value, result_type, result_block = _evaluate_simple_expression(expr_str, symbol_table, assembler_state, line_num, assembler, suppress_undefined_error=suppress_undefined_error)
        return result_value, result_type, result_block
    except ExpressionError as e:
        if expr_str != expr_str_orig: raise ExpressionError(f"Simplified '{expr_str}' from '{expr_str_orig}': {e}")
        else: raise e

def parse_dis_operands(operand_str, symbol_table: 'SymbolTable', assembler_state: 'AssemblerState', line_num, assembler: 'Assembler', suppress_undefined_error_for_n: bool = False):
    operand_str_orig = operand_str; operand_str = operand_str.strip() if operand_str else ""
    if not operand_str: raise ExpressionError("DIS requires operands")
    debug_mode = getattr(assembler_state, 'debug_mode', False)

    dis_micro_name_literal_regex = re.compile(r'^(?:(\S+)\s*,\s*)?%"([A-Za-z][A-Za-z0-9]{0,7})"%')
    
    match_dis_micro_literal = dis_micro_name_literal_regex.match(operand_str_orig) 
    if match_dis_micro_literal:
        n_str = match_dis_micro_literal.group(1)
        micro_name_as_string = match_dis_micro_literal.group(2)
        n_val = 0 
        if n_str:
            try:
                n_val_eval, n_type, _ = evaluate_expression(n_str, symbol_table, assembler_state, line_num, assembler, suppress_undefined_error=suppress_undefined_error_for_n)
                if n_type != 'absolute' or not isinstance(n_val_eval, int) or n_val_eval < 0:
                    raise ExpressionError("N value for DIS %\"name\"% must be non-negative absolute integer")
                n_val = n_val_eval
            except ExpressionError as e:
                raise ExpressionError(f"Invalid N expression '{n_str}' in DIS %\"name\"%: {e}")
        
        if debug_mode:
            print(f"Debug L{line_num} Parser: Matched DIS %\"{micro_name_as_string}\"%. N={n_val}")
        return {'format': 1, 'n': n_val, 'string': micro_name_as_string, 'is_micro_name_literal': True}

    try:
        subst_operand_str = substitute_micros(operand_str, assembler, line_num)
    except AsmException as e:
        raise ExpressionError(f"Error during micro substitution in DIS operand '{operand_str_orig}': {e}")

    if subst_operand_str.startswith(','):
        if len(subst_operand_str) < 3: raise ExpressionError(f"Invalid DIS format 2 (too short): '{operand_str_orig}' (after sub: '{subst_operand_str}')")
        delimiter = subst_operand_str[1]
        content_after_first_delim = subst_operand_str[2:]
        try:
            end_delim_pos = content_after_first_delim.index(delimiter)
            string_part = content_after_first_delim[:end_delim_pos]
            return {'format': 2, 'delimiter': delimiter, 'string': string_part, 'is_micro_name_literal': False}
        except ValueError:
            raise ExpressionError(f"Missing closing '{delimiter}' in '{operand_str_orig}' (after sub: '{subst_operand_str}')")
    else:
        comma_pos = subst_operand_str.find(',')
        is_format_2 = False
        if comma_pos > 0 and len(subst_operand_str) > comma_pos + 2:
            potential_delim = subst_operand_str[comma_pos + 1]
            if not potential_delim.isalnum() and not potential_delim.isspace():
                rest_after_potential_delim = subst_operand_str[comma_pos + 2:]
                if potential_delim in rest_after_potential_delim:
                    delimiter = potential_delim
                    content_part = rest_after_potential_delim
                    try:
                        end_delim_pos = content_part.index(delimiter)
                        string_part = content_part[:end_delim_pos]
                        is_format_2 = True
                        return {'format': 2, 'delimiter': delimiter, 'string': string_part, 'is_micro_name_literal': False}
                    except ValueError:
                        pass
        if not is_format_2:
            if comma_pos <= 0:
                raise ExpressionError(f"Invalid DIS format (expected N,String or ,<delim>...<delim>): '{operand_str_orig}' (after sub: '{subst_operand_str}')")
            n_str = subst_operand_str[:comma_pos].strip()
            string_and_comment = subst_operand_str[comma_pos+1:]
            string_part = string_and_comment 
            try:
                n_val, n_type, _ = evaluate_expression(n_str, symbol_table, assembler_state, line_num, assembler, suppress_undefined_error=suppress_undefined_error_for_n)
                if n_type != 'absolute' or not isinstance(n_val, int) or n_val < 0: raise ExpressionError("n must be non-negative absolute integer")
                return {'format': 1, 'n': n_val, 'string': string_part, 'is_micro_name_literal': False}
            except ExpressionError as e:
                raise ExpressionError(f"Invalid n expression '{n_str}' in Format 1 DIS: {e}")


def generate_dis_words(dis_operands, error_reporter: 'ErrorReporter', line_num, assembler_state: 'AssemblerState'):
    generated_words = []; chars_per_word = 10; fmt = dis_operands['format']
    input_string_from_parser = dis_operands['string']
    is_micro_name_literal = dis_operands.get('is_micro_name_literal', False)

    char_map_to_use: Dict[str, int]
    blank_for_invalid: int
    string_to_encode = input_string_from_parser

    if is_micro_name_literal:
        micro_name = input_string_from_parser 
        actual_micro_value = assembler_state.assembler.micro_definitions.get(micro_name.upper())
        if actual_micro_value is None:
            if not error_reporter.has_error_on_line(line_num):
                error_reporter.add_error(f"Internal: Micro '%{micro_name}%' for DIS not found in definitions during word generation.", line_num, code='F')
            return [] 
        string_to_encode = actual_micro_value
        char_map_to_use = DISPLAY_CODE_MAP_ASCII_SUBSET # DIS %"name"% uses Display Code (ASCII Subset)
        blank_for_invalid = DISPLAY_CODE_BLANK
    else: # Regular DIS N,String or DIS ,/String/
        current_code_mode = assembler_state.current_code
        if current_code_mode == 'A': 
            char_map_to_use = ASCII_6BIT_SUBSET_MAP # For DIS string content
            blank_for_invalid = ASCII_6BIT_BLANK
        elif current_code_mode == 'E': 
            char_map_to_use = EXTERNAL_BCD_MAP
            blank_for_invalid = EXTERNAL_BCD_BLANK
        elif current_code_mode == 'I': 
            char_map_to_use = INTERNAL_BCD_MAP
            blank_for_invalid = INTERNAL_BCD_BLANK
        else: # Default 'D' 
            char_map_to_use = DISPLAY_CODE_MAP_ASCII_SUBSET
            blank_for_invalid = DISPLAY_CODE_BLANK

    if fmt == 1: 
        n_words_from_operand = dis_operands['n']
        
        if n_words_from_operand == 0: 
            total_chars_to_pack = len(string_to_encode) + 2 
            num_words_needed = math.ceil(total_chars_to_pack / chars_per_word)
            total_chars_to_encode_in_loop = num_words_needed * chars_per_word
        else: 
            num_words_needed = n_words_from_operand
            total_chars_to_encode_in_loop = num_words_needed * chars_per_word

        current_word = 0; bits_in_current_word = 0
        for i in range(total_chars_to_encode_in_loop):
            char_to_encode_val: Optional[str] = None
            is_null_terminator_or_padding = False

            if i < len(string_to_encode):
                char_to_encode_val = string_to_encode[i]
            elif n_words_from_operand == 0 and i < len(string_to_encode) + 2: 
                is_null_terminator_or_padding = True
            else: 
                is_null_terminator_or_padding = True 

            code_val: int
            if is_null_terminator_or_padding:
                code_val = ZERO_6BIT 
            else:
                char_upper = char_to_encode_val.upper() # type: ignore
                code_val = char_map_to_use.get(char_upper, blank_for_invalid) 
                if code_val == blank_for_invalid and char_to_encode_val != ' ': 
                    effective_code_mode_name = assembler_state.current_code
                    if is_micro_name_literal: effective_code_mode_name = "DISPLAY (for %micro%)"
                    elif current_code_mode == 'D': effective_code_mode_name = "DISPLAY (ASCII Subset)"
                    
                    if not error_reporter.has_error_on_line(line_num):
                         error_reporter.add_warning(f"Invalid char '{char_to_encode_val}' in DIS (CODE={effective_code_mode_name}), using blank of mode.", line_num, "C")
            
            current_word = (current_word << 6) | code_val
            bits_in_current_word += 6
            if bits_in_current_word == 60:
                generated_words.append(current_word)
                current_word = 0
                bits_in_current_word = 0
        
        if bits_in_current_word != 0: 
            current_word <<= (60 - bits_in_current_word) 
            generated_words.append(current_word)
    
    elif fmt == 2: # ,/String/
        current_word = 0; bits_in_current_word = 0
        # Format 2 always uses Display Code (ASCII subset) for encoding the string itself
        effective_char_map = DISPLAY_CODE_MAP_ASCII_SUBSET
        effective_blank_for_invalid = DISPLAY_CODE_BLANK

        for char_actual in string_to_encode: 
            char_upper = char_actual.upper()
            code_val = effective_char_map.get(char_upper, effective_blank_for_invalid) 
            if code_val == effective_blank_for_invalid and char_actual != ' ': 
                 if not error_reporter.has_error_on_line(line_num):
                      error_reporter.add_warning(f"Invalid char '{char_actual}' in DIS Format 2, using blank.", line_num, "C")
            current_word = (current_word << 6) | code_val
            bits_in_current_word += 6
            if bits_in_current_word == 60: generated_words.append(current_word); current_word = 0; bits_in_current_word = 0
        
        for _ in range(2): 
            code_val = ZERO_6BIT
            current_word = (current_word << 6) | code_val
            bits_in_current_word += 6
            if bits_in_current_word == 60: generated_words.append(current_word); current_word = 0; bits_in_current_word = 0
        
        if bits_in_current_word > 0: 
            generated_words.append(current_word << (60 - bits_in_current_word))

    return generated_words

# expression.py v1.43
