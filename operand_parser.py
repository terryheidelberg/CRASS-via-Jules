# operand_parser.py v1.38
"""
Parses operand strings based on expected instruction formats.
Handles various register/expression combinations and reduced forms.
Improved format handling and register type detection.
More robust parsing based on expected format structure.
Added specific handling for formats like Bj,Xk and Reg Op Reg.
Fixes parsing for -XK, -XK*XJ, BJ,XK reductions, and XJ*XK.
Fixes regex for matching operators to avoid consuming '*' in operand.
Fixes parsing for Xj,Bk variant when format is BJ,XK (e.g., NX4 X4,B4).
Uses specific regex matching for Reg Op Reg formats.
Refines Reg Op Reg matching logic.
Fixes validation check for Reg Op Reg formats (XJ*XK etc.).

v1.32 Changes:
- Add debug print for Reg Op Reg parsing to show j and k assignments.
v1.33 Changes:
- Add debug print before returning parsed dict in REG_OP_K_REGEX block
  to check final K value being stored.
v1.34 Changes:
- Add 'assembler' argument to functions calling evaluate_expression.
v1.35 Changes:
- Capture and store K_block from expression evaluation.
v1.36 Changes:
- No functional change. Reviewed for alignment with relative value strategy.
v1.37 Changes:
- Add `suppress_undefined_error` parameter to propagate to expression evaluation,
  allowing speculative parsing (e.g., in Pass 1 width estimation) to not
  report "Undefined symbol" as a hard error.
v1.38 Changes:
- In REG_OP_K_REGEX handler, if operator is '-' and K expression is absolute,
  negate the K value before storing.
"""
import re
# Need access to evaluate_expression for K fields
# *** Import Assembler for type hint ***
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from assembler_state import AssemblerState
    from symbol_table import SymbolTable
    from errors import ErrorReporter
    from crass import Assembler # Import Assembler

# *** Import evaluate_expression from expression v1.34 ***
from expression import evaluate_expression, ExpressionError

# Regex for simple registers (A0-A7, B0-B7, X0-X7)
REG_REGEX_STR = r'([ABX])([0-7])'
REG_REGEX = re.compile(REG_REGEX_STR, re.IGNORECASE)
SINGLE_REG_REGEX = re.compile(f'^{REG_REGEX_STR}$', re.IGNORECASE)
# Regex for -Xk format
# Groups: (1: Type, 2: Num)
NEG_XK_REGEX = re.compile(f'^-{REG_REGEX_STR}$', re.IGNORECASE)

# --- Specific Regex for Reg Op Reg formats ---
# Allows optional spaces around operator
# Groups: (1: Full R1, 2: R1Type, 3: R1Num, 4: Operator, 5: Full R2, 6: R2Type, 7: R2Num)
# Ensures operator is captured correctly
REG_OP_REG_REGEX = re.compile(
    f'^({REG_REGEX_STR})\\s*([+*/-])\\s*({REG_REGEX_STR})$', # Match R1 op R2 structure
    re.IGNORECASE
)
# --- End Specific Regex ---

# Regex for Reg, K formats (EQ, NZ jumps)
# Groups: (1: FullReg, 2: RegType, 3: RegNum, 4: K_expression)
REG_COMMA_K_REGEX = re.compile(
    f'^({REG_REGEX_STR})\\s*,\\s*(.+)$',
    re.IGNORECASE
)

# Regex for Reg Op K formats (JP, RE, WE jumps/memory)
# Groups: (1: FullReg, 2: RegType, 3: RegNum, 4: Operator, 5: K_expression)
REG_OP_K_REGEX = re.compile(
    f'^({REG_REGEX_STR})\\s*([+-])\\s*(.+)$', # Only + or - expected here
    re.IGNORECASE
)

# Regex for -Reg Op Reg format (BX bool)
# Groups: (1: FullNegReg, 2: NegRegType, 3: NegRegNum, 4: Operator, 5: FullReg2, 6: Reg2Type, 7: Reg2Num)
NEG_REG_OP_REG_REGEX = re.compile(
    f'^-({REG_REGEX_STR})\\s*([+*/-])\\s*({REG_REGEX_STR})$',
    re.IGNORECASE
)

# Regex for simple integer constant
INT_CONST_REGEX = re.compile(r'^[0-9]+[BDO]?$', re.IGNORECASE)


class OperandParseError(ValueError):
    """Custom exception for operand parsing errors."""
    pass

def parse_register(reg_str):
    """Parses a simple register string like 'B5', 'X0'. Returns ('B', 5) or raises."""
    # (No changes needed)
    if reg_str is None:
        raise OperandParseError("Invalid register format: None")
    match = SINGLE_REG_REGEX.fullmatch(reg_str.strip())
    if match:
        reg_type = match.group(1).upper()
        reg_num = int(match.group(2))
        return (reg_type, reg_num)
    else:
        if re.match(r'^[ABX]\d+$', reg_str.strip(), re.IGNORECASE):
             raise OperandParseError(f"Invalid register number: '{reg_str}' (must be 0-7)")
        if reg_str.strip() == '*':
             raise OperandParseError(f"Register expected, found location counter '*'")
        raise OperandParseError(f"Invalid register format: '{reg_str}'")

# *** Add assembler argument ***
def _parse_expression_operand(operand_str, symbol_table: 'SymbolTable', assembler_state: 'AssemblerState', line_num: int, assembler: 'Assembler', suppress_undefined_error: bool = False):
    """
    Evaluates an operand string purely as an expression (for K, jk).
    Returns (value, type, block).
    """
    debug_mode = getattr(assembler_state, 'debug_mode', False)
    if debug_mode: print(f"Debug L{line_num} Parser: Evaluating expression operand: '{operand_str}' (Suppress Undef: {suppress_undefined_error})")
    try:
        # *** Pass assembler, capture block ***
        val, type, block = evaluate_expression(operand_str, symbol_table, assembler_state, line_num, assembler, suppress_undefined_error=suppress_undefined_error)
        if val is None:
            raise OperandParseError(f"Expression '{operand_str}' evaluated to None")
        if debug_mode: print(f"Debug L{line_num} Parser: Evaluated '{operand_str}' to Value={val} (Type: {type}, Block: {block})")
        return val, type, block # Return block
    except ExpressionError as e:
        raise OperandParseError(f"Cannot evaluate expression '{operand_str}': {e}")


# --- Main Parsing Function ---
# *** Add assembler argument ***
def parse_operands(operand_str, expected_format, symbol_table: 'SymbolTable', assembler_state: 'AssemblerState', line_num: int, assembler: 'Assembler', suppress_undefined_error: bool = False):
    """
    Parses the operand string based on common structures and the expected format hints.
    Returns a dictionary containing parsed components like 'i', 'j', 'k', 'K', 'op', 'reg_type', etc.
    Includes 'parsed_fmt' key indicating the structure that matched.
    Includes 'K_block' if a K expression involving a symbol was parsed.
    """
    if operand_str is None: operand_str = ""
    operand_str = operand_str.strip()
    parsed = {}
    fmt = expected_format.upper()
    operand_str_orig = operand_str # Keep original for error messages
    debug_mode = getattr(assembler_state, 'debug_mode', False)

    if debug_mode: print(f"Debug L{line_num} Parser: Input='{operand_str_orig}', ExpectedFmt='{fmt}', SuppressUndef={suppress_undefined_error}")

    if fmt == "":
        if operand_str and not operand_str.startswith('.') and not operand_str.startswith('*'):
             if not re.match(r'^\s*(\*.*|\..*)?$', operand_str):
                  raise OperandParseError(f"Expected no operands, got '{operand_str}'")
        parsed['parsed_fmt'] = ""
        return parsed
    if not operand_str:
        # Handle cases where operand is expected but missing (e.g., reduced forms)
        if 'K' in fmt:
            parsed['K'] = 0
            parsed['K_type'] = 'absolute'
            parsed['K_block'] = None # K=0 is absolute, no block
            parsed['parsed_fmt'] = 'K' # Assume reduced K form if K expected
            if debug_mode: print(f"Debug L{line_num} Parser: Empty operand, assuming reduced K=0 -> {parsed}")
            return parsed
        elif 'jk' in fmt:
            parsed['jk'] = 0
            parsed['jk_type'] = 'absolute'
            # No block needed for jk
            parsed['parsed_fmt'] = 'JK' # Assume reduced JK form if JK expected
            if debug_mode: print(f"Debug L{line_num} Parser: Empty operand, assuming reduced JK=0 -> {parsed}")
            return parsed
        pass


    # --- Structural Parsing Attempts ---

    # 1. Try Reg Op Reg (e.g., Xj*Xk, Aj+Bk)
    match_ror = REG_OP_REG_REGEX.match(operand_str)
    if match_ror:
        # (No changes needed here)
        r1t, r1n, op, r2t, r2n = match_ror.group(2), match_ror.group(3), match_ror.group(4), match_ror.group(6), match_ror.group(7)
        parsed['j'] = int(r1n)
        parsed['k'] = int(r2n)
        parsed['op'] = op
        parsed['parsed_fmt'] = f"{r1t.upper()}J{op}{r2t.upper()}K"
        if debug_mode: print(f"Debug L{line_num} Parser: Matched Reg Op Reg -> j={parsed['j']}, k={parsed['k']}, op='{parsed['op']}' -> {parsed}")
        return parsed

    # 2. Try -Reg Op Reg (e.g., -Xk*Xj)
    match_nror = NEG_REG_OP_REG_REGEX.match(operand_str)
    if match_nror:
        # (No changes needed here)
        negrt, negrn, op, r2t, r2n = match_nror.group(2), match_nror.group(3), match_nror.group(4), match_nror.group(6), match_nror.group(7)
        parsed['k'] = int(negrn)
        parsed['j'] = int(r2n)
        parsed['op'] = op
        parsed['parsed_fmt'] = f"-{negrt.upper()}K{op}{r2t.upper()}J"
        if debug_mode: print(f"Debug L{line_num} Parser: Matched -Reg Op Reg -> j={parsed['j']}, k={parsed['k']}, op='{parsed['op']}' -> {parsed}")
        return parsed

    # 3. Try Reg, K (e.g., Xj,K, Bi,K, Bj,Xk but check if K is register)
    match_rck = REG_COMMA_K_REGEX.match(operand_str)
    if match_rck:
        r1t, r1n, k_expr = match_rck.group(2), match_rck.group(3), match_rck.group(4)
        try:
            # Check if K part is actually another register
            r2t, r2n = parse_register(k_expr)
            # It is Reg, Reg format
            parsed['j'] = int(r1n)
            parsed['k'] = r2n
            parsed['parsed_fmt'] = f"{r1t.upper()}J,{r2t.upper()}K"
            if debug_mode: print(f"Debug L{line_num} Parser: Matched Reg, Reg -> {parsed}")
            return parsed
        except OperandParseError:
            # K part is not a simple register, assume expression
            try:
                # *** Pass assembler, capture block ***
                kval, ktype, kblock = _parse_expression_operand(k_expr, symbol_table, assembler_state, line_num, assembler, suppress_undefined_error=suppress_undefined_error)
                if fmt == "BI,K": parsed['i'] = int(r1n)
                else: parsed['j'] = int(r1n)
                parsed['reg_type'] = r1t.upper()
                parsed['K'] = kval
                parsed['K_type'] = ktype
                parsed['K_block'] = kblock # Store block
                parsed['parsed_fmt'] = f"{r1t.upper()}{'I' if 'i' in parsed else 'J'},K"
                if debug_mode: print(f"Debug L{line_num} Parser: Matched Reg, K -> Stored K={kval}, Block={kblock} -> {parsed}")
                return parsed
            except (OperandParseError, ExpressionError) as e:
                raise OperandParseError(f"Cannot parse '{operand_str_orig}' as {r1t.upper()}{r1n},K: {e}")

    # 4. Try Reg Op K (e.g., Bj+K)
    match_rok = REG_OP_K_REGEX.match(operand_str)
    if match_rok:
        r1t, r1n, op, k_expr = match_rok.group(2), match_rok.group(3), match_rok.group(4), match_rok.group(5)
        try:
            # *** Pass assembler, capture block ***
            kval, ktype, kblock = _parse_expression_operand(k_expr, symbol_table, assembler_state, line_num, assembler, suppress_undefined_error=suppress_undefined_error)
            if fmt == "BI+K" or fmt == "BI-K": # Check for BI+K or BI-K
                parsed['i'] = int(r1n)
            else:
                parsed['j'] = int(r1n)
            parsed['op'] = op # Store the operator
            parsed['reg_type'] = r1t.upper()

            # Apply negation here if op is '-' and K is absolute
            if op == '-' and ktype == 'absolute':
                if isinstance(kval, (int, float)): # Ensure kval is numeric before negation
                    kval = -kval
                else:
                    # This case should ideally be caught by evaluate_expression if ktype is absolute but kval isn't numeric
                    raise OperandParseError(f"Cannot negate non-numeric K value '{k_expr}' for operator '-'")

            parsed['K'] = kval
            parsed['K_type'] = ktype # Type might change if negation applied to non-abs (though error usually)
            parsed['K_block'] = kblock # Store block
            parsed['parsed_fmt'] = f"{r1t.upper()}{'I' if 'i' in parsed else 'J'}{op}K"
            if debug_mode: print(f"Debug L{line_num} Parser: Matched Reg Op K. Storing K={kval} ({ktype}), Block={kblock}. Returning: {parsed}")
            return parsed
        except (OperandParseError, ExpressionError) as e:
            raise OperandParseError(f"Cannot parse '{operand_str_orig}' as {r1t.upper()}{r1n}{op}K: {e}")

    # 5. Try -XK (Boolean)
    match_nxk = NEG_XK_REGEX.match(operand_str)
    if match_nxk:
        # (No changes needed here)
        r1t, r1n = match_nxk.group(1), match_nxk.group(2)
        if r1t.upper() == 'X':
            parsed['k'] = int(r1n)
            parsed['j'] = 0
            parsed['parsed_fmt'] = f"-XK"
            if debug_mode: print(f"Debug L{line_num} Parser: Matched -XK -> {parsed}")
            return parsed
        else:
            raise OperandParseError(f"Format -XK expects an X register, got '{operand_str_orig}'")

    # 6. Try Simple Register (e.g., Xj, Aj, Bk)
    try:
        # (No changes needed here)
        reg_type, reg_num = parse_register(operand_str)
        parsed['parsed_fmt'] = f"{reg_type}{reg_num}"
        if len(fmt) == 5 and fmt[0] == 'X' and fmt[1] == 'J' and fmt[3] == 'X' and fmt[4] == 'K': # e.g. XJ*XK format
            parsed['j'] = reg_num
            parsed['k'] = reg_num
            parsed['op'] = fmt[2]
            parsed['parsed_fmt'] = f"XJ"
            if debug_mode: print(f"Debug L{line_num} Parser: Matched Single Reg Reduction (Arith) -> {parsed}")
            return parsed
        if fmt == "BJ,XK" and reg_type == 'X': # e.g. LX/AX format
            parsed['j'] = 0
            parsed['k'] = reg_num
            parsed['reg_type'] = 'B'
            parsed['parsed_fmt'] = 'XK'
            if debug_mode: print(f"Debug L{line_num} Parser: Matched Single Reg Reduction (BJ,XK -> XK) -> {parsed}")
            return parsed
        if fmt == "XK": parsed['k'] = reg_num
        else: parsed['j'] = reg_num
        parsed['reg_type'] = reg_type
        if debug_mode: print(f"Debug L{line_num} Parser: Matched Single Reg -> {parsed}")
        return parsed

    except OperandParseError:
        # 7. Try Expression (K or jk)
        try:
            # Check for JK hint (LX/AX/MX) and simple integer
            is_lx_ax_jk_hint = fmt in ("JK", "BJ,XK", "XK") and expected_format in ("JK", "BJ,XK", "XK")
            is_simple_int = INT_CONST_REGEX.match(operand_str) is not None
            if is_lx_ax_jk_hint and is_simple_int:
                if debug_mode: print(f"Debug L{line_num} Parser: Treating '{operand_str}' as JK for LX/AX/MX.")
                # *** Pass assembler, ignore block for jk ***
                kval, ktype, _ = _parse_expression_operand(operand_str, symbol_table, assembler_state, line_num, assembler, suppress_undefined_error=suppress_undefined_error)
                if ktype != 'absolute': raise OperandParseError(f"jk value '{operand_str_orig}' must be absolute for LX/AX/MX")
                parsed['jk'] = kval
                parsed['jk_type'] = ktype
                parsed['parsed_fmt'] = 'JK'
                if debug_mode: print(f"Debug L{line_num} Parser: Matched JK Expression (LX/AX/MX) -> {parsed}")
                return parsed

            # Assume it's a K expression
            # *** Pass assembler, capture block ***
            kval, ktype, kblock = _parse_expression_operand(operand_str, symbol_table, assembler_state, line_num, assembler, suppress_undefined_error=suppress_undefined_error)
            if fmt == "JK": # Check if format specifically expects JK
                if ktype != 'absolute': raise OperandParseError(f"jk value '{operand_str_orig}' must be absolute")
                parsed['jk'] = kval
                parsed['jk_type'] = ktype
                parsed['parsed_fmt'] = 'JK'
            else: # Assume K
                parsed['K'] = kval
                parsed['K_type'] = ktype
                parsed['K_block'] = kblock # Store block
                # Handle reduced forms where K implies other fields are zero
                if fmt in ("AJ+K", "BJ+K", "XJ+K", "BI+K", "AJ-K", "BJ-K", "XJ-K", "BI-K"): # Added BI-K
                    if fmt.startswith('BI'): parsed['i'] = 0
                    else: parsed['j'] = 0
                    parsed['op'] = fmt[2] if len(fmt) > 2 else '+' # op is from format string
                elif fmt in ("BI,BJ,K", "XJ,K", "BI,K"):
                    if fmt.startswith('BI'): parsed['i'] = 0
                    if fmt != "XJ,K": parsed['j'] = 0
                parsed['parsed_fmt'] = 'K' # Indicate it was parsed as a K expression
            if debug_mode: print(f"Debug L{line_num} Parser: Matched Expression (K or JK) -> {parsed}")
            return parsed
        except (OperandParseError, ExpressionError) as e:
            # If all parsing attempts fail
            raise OperandParseError(f"Operand '{operand_str_orig}' does not match any known structure for expected format '{fmt}': Last error: {e}")

# operand_parser.py v1.38
