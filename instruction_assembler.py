# instruction_assembler.py v1.54
"""
Handles the assembly of individual machine instructions for CRASS.
Contains assemble_instruction and parcel building helpers.
Moved from crass.py v1.79.

v1.53: - Further refined m_final and j_final logic for 30-bit jumps (EQ, NZ, JP types)
         to correctly use i_reg for the m-field and the operand register for the j-field,
         with specific handling for two-register formats like EQ Bi,Bj,K.
         Ensures inst-map.txt is source of truth for F (or GHI) fields.
         (Still had issues with specific field assignments).
v1.54: - Correctly split 9-bit 'ghi' opcodes (from inst-map.txt) into 6-bit 'f' (gh)
         and 3-bit 'm' (i) for instructions like NZ, ZR, RJ, XJ, RE, WE.
       - Refined m_final and j_final assignments for EQ, JP based on i_reg and
         operand registers to match COMPASS manual formats more closely.
       - SA/SB/SX f_final logic retained from v1.50/v1.52.
"""

import re
import traceback
from typing import List, Optional, Tuple, Dict, Any, TYPE_CHECKING
if TYPE_CHECKING:
    from crass import Assembler

from symbol_table import SymbolTable
from assembler_state import AssemblerState
from errors import ErrorReporter, AsmException
from expression import ExpressionError
from operand_parser import parse_operands, OperandParseError 
from instruction_table import InstructionTable

NOOP_15_BIT = 0o46000
MASK_18_BIT = (1 << 18) - 1

# Mnemonics whose base_opcode_val_from_def in inst-map.txt is a 9-bit GHI field
# These require splitting into GH (f_final) and I (m_final)
GHI_OPCODE_MNEMONICS = {
    "RJ", "RE", "WE", "XJ", 
    "ZR", "NZ", "PL", "NG", "IR", "OR", "DF", "ID"
}


def _build_parcel_15bit(f3: int, m3: int, i: int, j: int, k: int, error_reporter: ErrorReporter, line_num: int) -> int:
    if not (0 <= f3 <= 7 and 0 <= m3 <= 7 and 0 <= i <= 7 and 0 <= j <= 7 and 0 <= k <= 7):
        error_reporter.add_error(f"Internal Error: Invalid field values for 15-bit parcel: f={f3}, m={m3}, i={i}, j={j}, k={k}", line_num, code='F')
        return NOOP_15_BIT
    return (f3 << 12) | (m3 << 9) | (i << 6) | (j << 3) | k

def _build_parcel_30bit_k18(f: int, m: int, j: int, k: int, error_reporter: ErrorReporter, line_num: int, debug_mode: bool) -> int:
    if not (0 <= f <= 0o77 and 0 <= m <= 7 and 0 <= j <= 7):
        error_reporter.add_error(f"Internal Error: Invalid field values (F,M,j) for 30-bit parcel: f={f:o}, m={m:o}, j={j:o}", line_num, code='F')
        return (NOOP_15_BIT << 15) | NOOP_15_BIT
    def to_octal(val, bits):
        if val >= 0: return format(val & ((1 << bits) - 1), 'o').zfill((bits + 2) // 3)
        else: return "-" + format((-val) & ((1 << bits) - 1), 'o').zfill((bits + 2) // 3)

    k_masked = k
    if k < 0:
        if not (-(1 << 17) <= k < 0):
             error_reporter.add_warning(f"Negative address/constant '{k}' ({to_octal(k, 18)}o) out of 18-bit range, truncated.", line_num, code='V')
        k_masked = (~abs(k)) & MASK_18_BIT
    else:
        if not (0 <= k < (1 << 18)):
             error_reporter.add_warning(f"Positive address/constant '{k:o}' out of 18-bit range, truncated.", line_num, code='V')
        k_masked = k & MASK_18_BIT

    final_val = (f << 24) | (m << 21) | (j << 18) | k_masked
    return final_val

def assemble_instruction(
    opcode_upper: str,
    details_list: List[Dict[str, Any]],
    parsed_operand_str: str,
    symbol_table: SymbolTable,
    assembler_state: AssemblerState,
    error_reporter: ErrorReporter,
    instruction_table: InstructionTable,
    line_num: int,
    assembler: 'Assembler'
) -> Optional[List[Tuple[int, int]]]:
    base_mnemonic = instruction_table.get_base_mnemonic(opcode_upper)
    debug_mode = getattr(assembler_state, 'debug_mode', False)
    pass_num = assembler_state.pass_number

    sorted_details = sorted(details_list, key=lambda x: x.get('width', 999))

    chosen_instr_def = None
    parsed_operands = None
    last_parse_error = None

    for instr_def_attempt in sorted_details:
        width_attempt = instr_def_attempt.get('width', 0)
        fmt_str_attempt = instr_def_attempt.get('format', "").upper()
        if debug_mode:
            print(f"DEBUG L{line_num} assemble_instruction: Trying def: width={width_attempt}, fmt='{fmt_str_attempt}', opcode_val={instr_def_attempt.get('opcode_val'):o} for operand '{parsed_operand_str}'")
        try:
            operands_attempt = parse_operands(parsed_operand_str, fmt_str_attempt, symbol_table, assembler_state, line_num, assembler)
            parsed_fmt_attempt = operands_attempt.get('parsed_fmt', "")
            if debug_mode:
                print(f"DEBUG L{line_num} assemble_instruction: parse_operands SUCCESS for fmt='{fmt_str_attempt}'. Returned parsed_fmt='{parsed_fmt_attempt}'")

            if len(sorted_details) > 1:
                if width_attempt == 15:
                    implies_K_field = (
                        parsed_fmt_attempt == 'K' or
                        parsed_fmt_attempt.endswith((',K', '+K', '-K')) or
                        (parsed_fmt_attempt.startswith('-') and parsed_fmt_attempt.endswith('J') and not parsed_fmt_attempt.startswith('-X'))
                    )
                    is_typical_15bit_reg_format = (
                        re.fullmatch(r"^[ABX]J,[ABX]K$", parsed_fmt_attempt) or
                        re.fullmatch(r"^[ABX]J[+*/-][ABX]K$", parsed_fmt_attempt) or
                        re.fullmatch(r"^-[ABX]K[+*/-][ABX]J$", parsed_fmt_attempt) or
                        re.fullmatch(r"^[ABX][0-7]$", parsed_fmt_attempt) or
                        parsed_fmt_attempt == "-XK" or
                        parsed_fmt_attempt == "JK" or
                        parsed_fmt_attempt == "BJ,XK" or parsed_fmt_attempt == "XJ,BK"
                    )
                    if implies_K_field and not is_typical_15bit_reg_format:
                        if any(d.get('width') == 30 for d in sorted_details):
                            if debug_mode: print(f"DEBUG L{line_num} assemble_instruction: Skipping 15-bit def for K-like operand '{parsed_operand_str}', preferring 30-bit.")
                            raise OperandParseError("Parsed format implies K, better match for 30-bit likely exists")

            chosen_instr_def = instr_def_attempt
            parsed_operands = operands_attempt
            if debug_mode:
                print(f"DEBUG L{line_num} assemble_instruction: CHOSEN def: width={width_attempt}, fmt='{fmt_str_attempt}'. Breaking loop.")
            break
        except OperandParseError as e:
            last_parse_error = e
            if debug_mode:
                print(f"DEBUG L{line_num} assemble_instruction: parse_operands FAILED for fmt='{fmt_str_attempt}'. Error: {e}")
            continue

    if chosen_instr_def is None or parsed_operands is None:
        err_msg = f"Operands '{parsed_operand_str}' do not match any valid format for {opcode_upper}."
        if last_parse_error:
             err_msg += f" Last attempt error: {last_parse_error}"
        if debug_mode:
            print(f"DEBUG L{line_num} assemble_instruction: Returning None. opcode_upper='{opcode_upper}', parsed_operand_str='{parsed_operand_str}'")
            print(f"    chosen_instr_def is None: {chosen_instr_def is None}")
            print(f"    parsed_operands is None: {parsed_operands is None}")
            print(f"    last_parse_error: {last_parse_error}")
        if not error_reporter.has_error_on_line(line_num):
             error_reporter.add_error(err_msg, line_num, code='O')
        return None

    instr_def = chosen_instr_def
    operands = parsed_operands
    width = instr_def.get('width', 0)
    base_opcode_val_from_def = instr_def.get('opcode_val', 0) 
    parsed_fmt = operands.get('parsed_fmt', "")

    parcels: List[Tuple[int, int]] = []

    i_reg_val = 0 
    match_mmi_reg = re.match(r'\w\w([0-7])', opcode_upper) 
    if match_mmi_reg: i_reg_val = int(match_mmi_reg.group(1))

    try:
        k_val = 0 
        if width == 30: 
             if base_mnemonic != 'PS': 
                 k_val = operands.get('K', 0)
                 k_type = operands.get('K_type', 'absolute')
                 k_block = operands.get('K_block', None)

                 if debug_mode:
                     print(f">>> DEBUG LC: L{line_num} Assemble K Field: '{parsed_operand_str}'")
                     print(f"    Parsed K Value = {k_val:o}")
                     print(f"    Parsed K Type  = {k_type}")
                     print(f"    Parsed K Block = {k_block}")

                 if pass_num == 2 and k_type == 'relocatable' and k_block and k_block != '*ABS*':
                     block_base = assembler.block_base_addresses.get(k_block)
                     if block_base is None:
                         if debug_mode: print(f"    ERROR: Base address for block '{k_block}' not found for K field!")
                         error_reporter.add_error(f"Internal: Base address for block '{k_block}' not found for K field.", line_num, 'F')
                         block_base = 0 
                     if debug_mode:
                         print(f"    Relocating K: Block='{k_block}', Base={block_base:o}, Relative Offset={k_val:o}")
                     k_val += block_base
                     k_type = 'absolute' 
                     if debug_mode: print(f"    Adjusted K (Absolute) = {k_val:o}")
                 elif k_type != 'absolute' and pass_num == 2:
                      error_reporter.add_warning(f"Relocation type '{k_type}' for K field not fully supported, using value directly.", line_num, 'R')

        f_final, m_final, i_final_for_15bit, j_final, k_final_for_parcel = 0, 0, 0, 0, 0

        if width == 15:
            f_final = (base_opcode_val_from_def >> 12) & 7
            m_final = (base_opcode_val_from_def >> 9) & 7
            i_final_for_15bit = i_reg_val 
            
            j_from_operand = operands.get('j', 0)
            k_from_operand = operands.get('k', 0)
            j_final = j_from_operand
            k_final_for_parcel = k_from_operand
            
            if base_mnemonic == 'BX':
                f_final = 1 
                if parsed_fmt == "XJ*XK": m_final = 1
                elif parsed_fmt == "XJ+XK": m_final = 2
                elif parsed_fmt == "XJ-XK": m_final = 3
                elif re.fullmatch(r"X[0-7]", parsed_fmt): 
                     m_final = 0; actual_k_operand_reg = j_from_operand 
                     j_final = actual_k_operand_reg; k_final_for_parcel = actual_k_operand_reg
                elif parsed_fmt == "-XK":
                     m_final = 4; actual_k_operand_reg = k_from_operand 
                     j_final = actual_k_operand_reg; k_final_for_parcel = actual_k_operand_reg
                elif parsed_fmt == "-XK*XJ": m_final = 5
                elif parsed_fmt == "-XK+XJ": m_final = 6
                elif parsed_fmt == "-XK-XJ": m_final = 7
                else: raise ValueError(f"Unexpected parsed format '{parsed_fmt}' for BX {parsed_operand_str}")
            elif base_mnemonic in ['FX', 'RX', 'DX', 'IX']:
                op = operands.get('op'); op_char = ''
                is_single_reg_arith = re.fullmatch(r"X[0-7]", parsed_fmt) is not None
                if is_single_reg_arith:
                    j_final = j_from_operand; k_final_for_parcel = j_from_operand
                    instr_fmt_def = chosen_instr_def.get('format','').upper() 
                    if '+' in instr_fmt_def: op_char = '+'
                    elif '-' in instr_fmt_def: op_char = '-'
                    elif '*' in instr_fmt_def or (base_mnemonic == 'IX' and not op): op_char = '*' 
                    elif '/' in instr_fmt_def: op_char = '/'
                    else: op_char = '*' 
                elif op:
                    op_char = op[0]; j_final = operands.get('j', 0); k_final_for_parcel = operands.get('k', 0)
                else: raise ValueError(f"Missing operator or invalid format for {base_mnemonic} {parsed_operand_str}")
                
                valid_ops_map = { 
                    'FX': {'+': (3, 0), '-': (3, 1), '*': (4, 0), '/': (4, 4)},
                    'RX': {'+': (3, 4), '-': (3, 5), '*': (4, 1), '/': (4, 5)},
                    'DX': {'+': (3, 2), '-': (3, 3), '*': (4, 2)}, 
                    'IX': {'+': (3, 6), '-': (3, 7), '*': (3, 6)}  
                }
                if base_mnemonic not in valid_ops_map or op_char not in valid_ops_map[base_mnemonic]:
                    raise ValueError(f"Invalid op '{op_char}' for 15-bit {base_mnemonic}")
                f_final, m_final = valid_ops_map[base_mnemonic][op_char]
            elif base_mnemonic in ['LX', 'AX']:
                f_final = 2 
                if parsed_fmt == "BJ,XK": m_final = 2 if base_mnemonic == 'LX' else 3; j_final = operands.get('j', 0); k_final_for_parcel = operands.get('k', 0)
                elif parsed_fmt == "JK": m_final = 0 if base_mnemonic == 'LX' else 1; jk_val = operands['jk'] & 0o77; j_final = (jk_val >> 3) & 7; k_final_for_parcel = jk_val & 7
                elif parsed_fmt == "XK": m_final = 2 if base_mnemonic == 'LX' else 3; j_final = 0; k_final_for_parcel = operands.get('k', 0) 
                else: raise ValueError(f"Unexpected parsed format '{parsed_fmt}' for {base_mnemonic} {parsed_operand_str}")
            elif base_mnemonic in ['EQ', 'NE', 'GE', 'LT']: 
                f_final = 0; m3_map = {'EQ': 0, 'NE': 1, 'GE': 2, 'LT': 3}; m_final = m3_map[base_mnemonic]
                if parsed_fmt != 'BI,BJ': raise ValueError(f"Unexpected parsed format '{parsed_fmt}' for 15-bit {base_mnemonic}")
                i_final_for_15bit = operands.get('j', 0); j_final = operands.get('k', 0); k_final_for_parcel = 0 
            elif base_mnemonic in ['ZR', 'NZ', 'PL', 'NG', 'IR', 'OR', 'DF', 'ID']: 
                f_final = 1; m3_map = {'ZR': 0, 'NZ': 1, 'PL': 2, 'NG': 3, 'IR': 4, 'OR': 5, 'DF': 6, 'ID': 7}; m_final = m3_map[base_mnemonic]
                if parsed_fmt != 'BI,XJ': raise ValueError(f"Unexpected parsed format '{parsed_fmt}' for 15-bit {base_mnemonic}")
                i_final_for_15bit = operands.get('j', 0); j_final = operands.get('k', 0); k_final_for_parcel = 0 
            elif base_mnemonic in ['NX', 'ZX', 'UX', 'PX']:
                f_final = 2; m3_map = {'NX': 4, 'ZX': 5, 'UX': 6, 'PX': 7}; m_final = m3_map[base_mnemonic]
                if parsed_fmt == "BJ,XK": j_final = operands.get('j', 0); k_final_for_parcel = operands.get('k', 0)
                elif parsed_fmt == "XJ,BK": j_final = operands.get('k', 0); k_final_for_parcel = operands.get('j', 0) 
                elif parsed_fmt == "XK": j_final = 0; k_final_for_parcel = operands.get('k', 0) 
                else: raise ValueError(f"Unexpected parsed format '{parsed_fmt}' for {base_mnemonic}")
            elif base_mnemonic == 'MX':
                f_final, m_final = 4, 3
                if parsed_fmt != 'JK': raise ValueError(f"Unexpected parsed format '{parsed_fmt}' for MX")
                jk_val = operands.get('jk', 0) & 0o77; j_final = (jk_val >> 3) & 7; k_final_for_parcel = jk_val & 7
            elif base_mnemonic == 'CX':
                f_final, m_final = 4, 7
                if not (parsed_fmt.startswith('X') and len(parsed_fmt) == 2 and parsed_fmt[1].isdigit()):
                    raise ValueError(f"Unexpected parsed format '{parsed_fmt}' for CX, expected single Xk")
                k_reg_index = operands.get('k', 0); j_final = k_reg_index; k_final_for_parcel = k_reg_index
            elif base_mnemonic in ['SA', 'SB', 'SX']: 
                f_map = {'SA': 5, 'SB': 6, 'SX': 7}; f_final = f_map[base_mnemonic]
                if len(parsed_fmt) == 2 and parsed_fmt[0] in 'ABX' and parsed_fmt[1].isdigit(): 
                     reg_type = parsed_fmt[0]; j_final = operands.get('j', 0); k_final_for_parcel = 0 
                     m_map_single = {'A': 4, 'B': 6, 'X': 3} 
                     if reg_type not in m_map_single: raise ValueError(f"Invalid reg type '{reg_type}' for single reg {base_mnemonic}")
                     m_final = m_map_single[reg_type]
                elif parsed_fmt.endswith('J+BK') or parsed_fmt.endswith('J-BK'): 
                     r1t = parsed_fmt[0]; op = operands.get('op')
                     m_map = { ('X', '+'): 3, ('A', '+'): 4, ('A', '-'): 5, ('B', '+'): 6, ('B', '-'): 7 }
                     if (r1t, op) not in m_map: raise ValueError(f"Invalid 15-bit {base_mnemonic} format/op: {parsed_fmt} / {op}")
                     m_final = m_map[(r1t, op)]; j_final = operands.get('j', 0); k_final_for_parcel = operands.get('k', 0)
                else: raise ValueError(f"Unexpected parsed format '{parsed_fmt}' for 15-bit {base_mnemonic}")
            elif base_mnemonic == 'NO':
                 f_final, m_final, i_final_for_15bit, j_final, k_final_for_parcel = 4, 6, 0, 0, 0
            else: error_reporter.add_error(f"Internal: Logic missing for 15-bit base mnemonic '{base_mnemonic}'", line_num, code='F'); return None
            
            parcel_value = _build_parcel_15bit(f_final, m_final, i_final_for_15bit, j_final, k_final_for_parcel, error_reporter, line_num)
            parcels.append((parcel_value, 15))

        elif width == 30:
            f_final, m_final, j_final = 0,0,0 
            k_final_for_parcel = k_val 

            if base_mnemonic == 'PS': 
                if parsed_operand_str and not (parsed_operand_str.startswith('.') or parsed_operand_str.startswith('*')):
                    error_reporter.add_error("PS takes no operands unless it's a comment", line_num, code='O')
                f_final, m_final, j_final, k_final_for_parcel = 0, 0, 0, 0 
            
            elif base_mnemonic in GHI_OPCODE_MNEMONICS: # RJ, XJ, RE, WE, ZR, NZ, etc.
                ghi = base_opcode_val_from_def
                f_final = (ghi >> 3) & 0o77 # gh part
                m_final = ghi & 0o7      # i part
                
                # j_final is the register index from the operand
                if parsed_fmt == "XJ,K": j_final = operands.get('j', 0)
                elif parsed_fmt == "BI,K": j_final = operands.get('i', 0) 
                elif parsed_fmt == "BJ,K": j_final = operands.get('j', 0)
                elif parsed_fmt == "K": j_final = 0
                elif len(parsed_fmt) == 2 and parsed_fmt[0] in 'ABX' and parsed_fmt[1].isdigit(): # e.g. RE B1
                    j_final = operands.get('j',0)
                    k_final_for_parcel = 0
                elif parsed_fmt.endswith('J+K') or parsed_fmt.endswith('J-K'): # e.g. RE B1+K
                    j_final = operands.get('j',0)
                else: 
                    # This case might occur if a GHI instruction has no register operand in its typical form (e.g. RJ K)
                    # but parse_operands still found 'j' or 'i' (e.g. from a mistaken parse as another format)
                    # For safety, default j_final to 0 if not explicitly set by a matched XJ,K or BI,K etc.
                    if 'j' not in operands and 'i' not in operands:
                        j_final = 0
                    else: # Should have been caught by specific format checks above
                        j_final = operands.get('j', operands.get('i', 0))


            elif base_mnemonic in ['SA', 'SB', 'SX']:
                m_final = i_reg_val 
                if parsed_fmt == 'K': 
                    j_final = 0
                    f_map = {'SA': 0o51, 'SB': 0o61, 'SX': 0o71}
                    f_final = f_map[base_mnemonic]
                elif parsed_fmt.startswith(('AJ', 'BJ', 'XJ')) and ('+' in parsed_fmt or '-' in parsed_fmt) and parsed_fmt.endswith('K'): 
                    j_final = operands.get('j', 0)
                    reg_char_for_f = parsed_fmt[0]
                    f_map_rj_k = {'SA': {'A':0o50, 'B':0o51, 'X':0o52}, 
                                  'SB': {'A':0o60, 'B':0o61, 'X':0o62}, 
                                  'SX': {'A':0o70, 'B':0o71, 'X':0o72}}
                    f_final = f_map_rj_k[base_mnemonic][reg_char_for_f]
                elif len(parsed_fmt) == 2 and parsed_fmt[0] in 'ABX' and parsed_fmt[1].isdigit(): 
                     j_final = operands.get('j', 0)
                     k_final_for_parcel = 0 
                     reg_char_for_f = parsed_fmt[0]
                     f_map_rj = {'SA': {'A':0o50, 'B':0o51, 'X':0o52},
                                 'SB': {'A':0o60, 'B':0o61, 'X':0o62},
                                 'SX': {'A':0o70, 'B':0o71, 'X':0o72}}
                     f_final = f_map_rj[base_mnemonic][reg_char_for_f]
                else:
                    raise ValueError(f"Unexpected parsed format '{parsed_fmt}' for 30-bit {base_mnemonic} {parsed_operand_str}")
            
            elif base_mnemonic in ['EQ', 'NE', 'GE', 'LT']: 
                f_final = base_opcode_val_from_def 
                
                if parsed_fmt == 'BI,BJ,K': 
                    m_final = operands.get('i', 0) 
                    j_final = operands.get('j', 0)
                else: # Single register operand or just K
                    m_final = i_reg_val # This is the 'i' from EQi, or 0 if just EQ
                    single_reg_idx = operands.get('j', operands.get('i', 0)) # Get the B/X register index
                    
                    if i_reg_val == 0 and (parsed_fmt == 'BI,K' or parsed_fmt == 'BJ,K'): 
                        # Form EQ Bx,K -> M=Bx, J=0
                        m_final = single_reg_idx
                        j_final = 0
                    else: # Form EQi Bx,K or EQi K or EQ K
                        j_final = single_reg_idx
                        if parsed_fmt == 'K': # EQi K or EQ K
                            j_final = 0
            
            elif base_mnemonic == 'JP': 
                f_final = base_opcode_val_from_def # F=02
                m_final = i_reg_val # i from JPi (or 0 if just JP)
                j_final = 0     # Default j
                
                if parsed_fmt == 'BI+K' or parsed_fmt == 'BI-K': 
                    reg_idx = operands.get('i',0)
                    if i_reg_val == 0: # JP Bi+K
                        m_final = reg_idx
                        j_final = reg_idx # As per good.txt for JP B1+LOOP -> 0211K
                    else: # JPi Bi+K
                        m_final = i_reg_val # M is from JPi
                        j_final = reg_idx   # J is from Bi
                elif parsed_fmt == 'K': 
                    # m_final is i_reg_val, j_final is 0
                    pass
                elif len(parsed_fmt) == 2 and parsed_fmt.startswith('B') and parsed_fmt[1].isdigit(): # JP Bi
                     reg_idx = operands.get('j', 0)
                     if i_reg_val == 0: # JP Bi
                         m_final = reg_idx 
                         j_final = reg_idx 
                     else: # JPi Bi
                         m_final = i_reg_val
                         j_final = reg_idx
                     k_final_for_parcel = 0
                else: raise ValueError(f"Unexpected parsed format '{parsed_fmt}' for JP {parsed_operand_str}")
            else: 
                error_reporter.add_error(f"Internal: Logic missing for 30-bit base mnemonic '{base_mnemonic}' (Opcode: {opcode_upper}, Operand: '{parsed_operand_str}')", line_num, code='F')
                return None

            parcel_value = _build_parcel_30bit_k18(f_final, m_final, j_final, k_final_for_parcel, error_reporter, line_num, debug_mode)
            parcels.append((parcel_value, 30))

        elif width == 60: 
            if opcode_upper in ["IM", "DM", "CC", "CU"]: 
                 error_reporter.add_warning(f"60-bit instruction {opcode_upper} not implemented, generating placeholder.", line_num, code='W')
                 parcels.append((base_opcode_val_from_def, 60)) 
            else: error_reporter.add_error(f"Internal: Unhandled 60-bit instruction {opcode_upper}", line_num, code='F'); return None
        else: error_reporter.add_error(f"Internal: Unexpected width {width} for {opcode_upper}", line_num, code='F'); return None

        return parcels

    except (AsmException, ExpressionError, OperandParseError, SyntaxError, ValueError, TypeError, KeyError) as e:
        if not error_reporter.has_error_on_line(line_num):
             err_code = getattr(e, 'code', 'A');
             if debug_mode:
                 print(f"DEBUG L{line_num} assemble_instruction: CAUGHT ERROR during assembly.")
                 print(f"    Opcode: {opcode_upper}, Operand: '{parsed_operand_str}'")
                 print(f"    Attempted InstrDef: {instr_def}") 
                 print(f"    Parsed Operands: {operands}")     
                 print(f"    Exception: {type(e).__name__}: {e}")
             error_reporter.add_error(f"Assembling {opcode_upper} {parsed_operand_str}: {e}", line_num, code=err_code)
        return None
    except Exception as e:
        if debug_mode:
            print(f"DEBUG L{line_num} assemble_instruction: UNEXPECTED CRITICAL ERROR during assembly.")
            print(f"    Opcode: {opcode_upper}, Operand: '{parsed_operand_str}'")
            print(f"    Attempted InstrDef: {instr_def if chosen_instr_def else 'N/A'}")
            print(f"    Parsed Operands: {operands if parsed_operands else 'N/A'}")
            print(f"    Exception: {type(e).__name__}: {e}")
        error_reporter.add_error(f"Unexpected error assembling {opcode_upper} {parsed_operand_str}: {e}", line_num, code='F');
        traceback.print_exc()
        return None

# instruction_assembler.py v1.54
