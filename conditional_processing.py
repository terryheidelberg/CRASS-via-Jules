# conditional_processing.py v1.0
"""
Handles conditional assembly pseudo-operations (IF, ELSE, ENDIF)
and condition evaluation for CRASS.
"""
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crass import Assembler
    from symbol_table import SymbolTable
    from assembler_state import AssemblerState
    from errors import ErrorReporter

from errors import AsmException
from expression import ExpressionError, evaluate_expression

def evaluate_condition(assembler: 'Assembler', line_num: int, mnemonic: str, operand_str: str) -> bool:
    state = assembler.state
    symbol_table = assembler.symbol_table
    error_reporter = assembler.error_reporter
    # debug_mode = assembler.debug_mode # Not used directly in this function

    operand_str_for_eval = operand_str
    if mnemonic != "IFC": # IFC handles its own operand parsing including comments
        operand_str_for_eval = operand_str.split('.')[0].split('*')[0].strip()

    parts = [p.strip() for p in operand_str_for_eval.split(',')]
    try:
        if mnemonic == "IF":
            if not parts: raise ExpressionError("IF requires operands")
            cond_type_raw = parts[0].upper()
            arg = ""
            if len(parts) > 1:
                if cond_type_raw in ("SET", "-SET"): # SET,SYMBOL or -SET,SYMBOL
                    arg = parts[1]
                else: # EXPR, possibly with commas if EXPR itself has them
                    arg = ','.join(parts[1:]) # Re-join if EXPR had commas
                    # If cond_type_raw was not a keyword, it's part of the expression
                    if cond_type_raw not in ("ABS", "-ABS", "REL", "-REL", "COM", "-COM", "EXT", "-EXT", "LCM", "-LCM", "LOC", "-LOC", "DEF", "-DEF", "REG", "-REG", "MIC", "-MIC", "CP", "PP", "TPA", "TPB", "TPC", "TPD", "TPE", "TPF"):
                        arg = cond_type_raw + (f",{arg}" if arg else "")
                        cond_type_raw = "EXPR"

            elif cond_type_raw not in ("SET", "-SET", "CP", "PP", "TPA", "TPB", "TPC", "TPD", "TPE", "TPF"):
                 # Single operand which is not a keyword, assume it's an expression
                 arg = cond_type_raw
                 cond_type_raw = "EXPR"


            if cond_type_raw == "SET":
                if not arg: raise ExpressionError("IF SET requires a symbol name")
                return symbol_table.is_defined(arg.upper(), state.current_qualifier)
            elif cond_type_raw == "-SET":
                if not arg: raise ExpressionError("IF -SET requires a symbol name")
                return not symbol_table.is_defined(arg.upper(), state.current_qualifier)
            elif cond_type_raw in ("ABS", "-ABS", "REL", "-REL", "COM", "-COM", "EXT", "-EXT", "LCM", "-LCM", "LOC", "-LOC", "DEF", "-DEF"):
                 if not arg: raise ExpressionError(f"IF {cond_type_raw} requires an argument")
                 is_def_check = False
                 sym_type_check = None
                 try:
                     sym_entry = symbol_table.lookup(arg, line_num, state.current_qualifier)
                     if sym_entry:
                         is_def_check = True
                         sym_type_check = sym_entry['attrs'].get('type', 'absolute')
                 except ExpressionError as ee: # Catch "Undefined symbol" from lookup
                     if "Undefined symbol" in str(ee): is_def_check = False
                     else: raise ee # Re-raise other expression errors

                 if cond_type_raw == "DEF": return is_def_check
                 if cond_type_raw == "-DEF": return not is_def_check
                 if not is_def_check: return False # Must be defined for other type checks

                 if cond_type_raw == "ABS": return sym_type_check == 'absolute'
                 if cond_type_raw == "-ABS": return sym_type_check != 'absolute'
                 if cond_type_raw == "REL": return sym_type_check == 'relocatable'
                 if cond_type_raw == "-REL": return sym_type_check != 'relocatable'
                 # Other types like COM, EXT, LCM, LOC are not fully modeled yet
                 error_reporter.add_warning(f"IF condition type '{cond_type_raw}' not fully implemented for symbol attributes", line_num, code='W'); return False
            elif cond_type_raw in ("REG", "-REG"):
                 if not arg: raise ExpressionError(f"IF {cond_type_raw} requires an argument")
                 is_reg = re.fullmatch(r'[ABX][0-7]', arg.upper()) is not None
                 return is_reg if cond_type_raw == "REG" else not is_reg
            elif cond_type_raw in ("MIC", "-MIC"):
                 if not arg: raise ExpressionError(f"IF {cond_type_raw} requires an argument")
                 is_micro = arg.upper() in assembler.micro_definitions
                 return is_micro if cond_type_raw == "MIC" else not is_micro
            elif cond_type_raw == "EXPR":
                  val, type, _ = evaluate_expression(arg, symbol_table, state, line_num, assembler)
                  if val is None: return False # Error during evaluation
                  return val != 0
            elif cond_type_raw in ("CP", "PP", "TPA", "TPB", "TPC", "TPD", "TPE", "TPF"): # CPU type checks
                 if arg: error_reporter.add_warning(f"IF condition type '{cond_type_raw}' takes no arguments, '{arg}' ignored.", line_num, code='W')
                 if cond_type_raw == "CP": return True # Assume always CPU for now
                 if cond_type_raw == "PP": return False # Assume never PPU for now
                 # TPA-TPF are specific CPU features, not implemented
                 error_reporter.add_warning(f"IF condition type '{cond_type_raw}' not fully implemented", line_num, code='W'); return False
            else: # Fallback: if cond_type_raw was not a keyword, it's an expression
                  val, type, _ = evaluate_expression(cond_type_raw, symbol_table, state, line_num, assembler)
                  if val is None: return False
                  return val != 0
        elif mnemonic == "IFEQ":
             if len(parts) != 2: raise ExpressionError("IFEQ requires two operands")
             val1, type1, _ = evaluate_expression(parts[0], symbol_table, state, line_num, assembler)
             val2, type2, _ = evaluate_expression(parts[1], symbol_table, state, line_num, assembler)
             if val1 is None or val2 is None: return False
             return val1 == val2 and type1 == type2 # COMPASS manual implies types must also match for EQ
        elif mnemonic == "IFNE":
             if len(parts) != 2: raise ExpressionError("IFNE requires two operands")
             val1, type1, _ = evaluate_expression(parts[0], symbol_table, state, line_num, assembler)
             val2, type2, _ = evaluate_expression(parts[1], symbol_table, state, line_num, assembler)
             if val1 is None or val2 is None: return False
             return val1 != val2 or type1 != type2
        elif mnemonic in ("IFGT", "IFGE", "IFLT", "IFLE"):
             if len(parts) != 2: raise ExpressionError(f"{mnemonic} requires two operands")
             val1, type1, _ = evaluate_expression(parts[0], symbol_table, state, line_num, assembler)
             val2, type2, _ = evaluate_expression(parts[1], symbol_table, state, line_num, assembler)
             if val1 is None or val2 is None: return False
             if type1 != 'absolute' or type2 != 'absolute': # COMPASS usually compares absolute values
                  error_reporter.add_warning(f"{mnemonic} typically compares absolute values. Result may be unexpected for relocatable types.", line_num, code='W')
             if mnemonic == "IFGT": return val1 > val2
             if mnemonic == "IFGE": return val1 >= val2
             if mnemonic == "IFLT": return val1 < val2
             if mnemonic == "IFLE": return val1 <= val2
        elif mnemonic == "IFPL":
             if len(parts) != 1: raise ExpressionError("IFPL requires one expression operand")
             val, type, _ = evaluate_expression(parts[0], symbol_table, state, line_num, assembler)
             if val is None: return False
             if type != 'absolute': error_reporter.add_warning(f"IFPL typically checks absolute values. Result may be unexpected for relocatable types.", line_num, code='W')
             return val >= 0
        elif mnemonic == "IFMI":
             if len(parts) != 1: raise ExpressionError("IFMI requires one expression operand")
             val, type, _ = evaluate_expression(parts[0], symbol_table, state, line_num, assembler)
             if val is None: return False
             if type != 'absolute': error_reporter.add_warning(f"IFMI typically checks absolute values. Result may be unexpected for relocatable types.", line_num, code='W')
             return val < 0
        elif mnemonic == "IFC": # IF Character compare
             # Format: IFC op,dSTRING1dSTRING2d
             m = re.match(r"(\w+)\s*,(.*)", operand_str_for_eval.strip(), re.IGNORECASE)
             if not m: raise ExpressionError("Invalid IFC format. Expected 'OP,dSTRING1dSTRING2d'")
             op = m.group(1).upper(); rest = m.group(2).strip()
             if not rest: raise ExpressionError("Missing strings for IFC")
             delim = rest[0]
             # Pattern to extract two strings separated by the delimiter
             # Allows for empty strings between delimiters
             str_match_pattern = f"^{re.escape(delim)}(.*?){re.escape(delim)}(.*?){re.escape(delim)}$"
             str_match = re.match(str_match_pattern, rest)

             if not str_match: # Try allowing spaces between second string and its delimiter
                  str_match_pattern_space = f"^{re.escape(delim)}(.*?){re.escape(delim)}\\s*(.*?){re.escape(delim)}$"
                  str_match = re.match(str_match_pattern_space, rest)
                  if not str_match:
                       raise ExpressionError(f"Invalid IFC string format or mismatched delimiters: '{rest}' using delimiter '{delim}'")
             
             s1, s2 = str_match.group(1), str_match.group(2)
             # COMPASS IFC compares strings by padding shorter with binary zeros
             len1, len2 = len(s1), len(s2); maxlen = max(len1, len2)
             s1_padded = s1.ljust(maxlen, chr(0)); s2_padded = s2.ljust(maxlen, chr(0))

             if op == "EQ": return s1_padded == s2_padded
             if op == "NE": return s1_padded != s2_padded
             if op == "GT": return s1_padded > s2_padded
             if op == "GE": return s1_padded >= s2_padded
             if op == "LT": return s1_padded < s2_padded
             if op == "LE": return s1_padded <= s2_padded
             # COMPASS also supports negated conditions like -EQ, -NE, etc.
             if op == "-NE": return s1_padded == s2_padded # -NE is EQ
             if op == "-EQ": return s1_padded != s2_padded # -EQ is NE
             if op == "-GT": return s1_padded <= s2_padded # -GT is LE
             if op == "-GE": return s1_padded < s2_padded  # -GE is LT
             if op == "-LT": return s1_padded >= s2_padded # -LT is GE
             if op == "-LE": return s1_padded > s2_padded  # -LE is GT
             raise ExpressionError(f"Unknown IFC operator: '{op}'")
        elif mnemonic == "IFCP": return True # Assume CPU for now
        elif mnemonic == "IFPP": return False # Assume not PPU for now
        # IFTPA, IFTPB, etc. are for specific CPU types, not implemented
        else:
             error_reporter.add_warning(f"Conditional '{mnemonic}' not fully implemented, assuming FALSE", line_num, code='W'); return False
    except (ExpressionError, AsmException) as e:
        if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(f"Conditional evaluation error {mnemonic} {operand_str}: {e}", line_num, code='E');
        return False # Error in condition means it's false
    except Exception as e: # Catch any other unexpected errors
        if not error_reporter.has_error_on_line(line_num): error_reporter.add_error(f"Unexpected error in conditional {mnemonic} {operand_str}: {e}", line_num, code='F');
        # traceback.print_exc(); # For debugging, if needed
        return False

# conditional_processing.py v1.0
