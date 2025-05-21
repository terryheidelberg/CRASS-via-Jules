# lexer.py v1.4
"""
Provides the line parsing functionality for the CRASS assembler,
adhering to the COMPASS fixed-field rules. Includes special handling
for pseudo-ops like DIS, TITLE.
"""
import re

# Known pseudo-ops that allow embedded blanks in their operand field
# or have unique operand/comment parsing rules.
PSEUDO_OPS_WITH_SPECIAL_OPERAND_HANDLING = {'DIS', 'TITLE', 'TTL', 'COMMENT'}
# CTEXT/XTEXT might need special handling too, but deferring for now.

def parse_line(line, line_num):
    """
    Parses a single line of COMPASS source code according to strict rules.
    Returns a dictionary containing the fields:
        'line_num': Original line number.
        'original': The original line string (stripped).
        'label': The label found, or None.
        'opcode': The opcode found, or None.
        'operand_str': The raw operand string, or None.
        'comment': The comment string, or None.
        'is_comment_line': True if the line is only a comment.
        'error': An error message if parsing failed badly (currently unused).
    """
    line = line.rstrip()
    original_line = line

    fields = {
        'line_num': line_num,
        'original': original_line,
        'label': None,
        'opcode': None,
        'operand_str': None,
        'comment': None,
        'is_comment_line': False,
        'error': None
    }

    if not line: return fields
    if line.startswith('*'):
        fields['is_comment_line'] = True
        fields['comment'] = line[1:]
        return fields

    col1_char = line[0] if len(line) > 0 else ' '
    col2_char = line[1] if len(line) > 1 else ' '
    current_pos = 0

    # --- Label Field ---
    if col1_char == ' ' and col2_char == ' ':
        fields['label'] = None
        current_pos = 2
    else:
        start_pos = 0 if col1_char != ' ' else 1
        end_pos = start_pos
        while end_pos < len(line) and line[end_pos] != ' ': end_pos += 1
        label_candidate = line[start_pos:end_pos]
        if label_candidate not in ('+', '-') and \
           not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,7}', label_candidate):
            pass # Ignore invalid format for now
        fields['label'] = label_candidate
        current_pos = end_pos

    # --- Skip blanks before Opcode ---
    opcode_start_col = -1
    while current_pos < len(line):
        if line[current_pos] != ' ':
            opcode_start_col = current_pos
            break
        current_pos += 1

    # --- Opcode Field ---
    if opcode_start_col == -1: # No opcode found
         default_comment_col = 30
         first_non_blank = -1
         for i, char in enumerate(line):
             if char != ' ':
                 first_non_blank = i
                 break
         if fields['label'] is None and (first_non_blank == -1 or first_non_blank >= (default_comment_col - 1)):
              fields['is_comment_line'] = True
              if first_non_blank != -1: fields['comment'] = line[first_non_blank:]
              else: fields['comment'] = "" # Blank line case
              return fields
         return fields # Line with just label, or blank line

    end_pos_opcode = opcode_start_col
    while end_pos_opcode < len(line) and line[end_pos_opcode] != ' ':
        end_pos_opcode += 1
    fields['opcode'] = line[opcode_start_col:end_pos_opcode]
    current_pos = end_pos_opcode

    # --- Skip blanks before Operand/Comment ---
    operand_comment_start_col = -1
    while current_pos < len(line):
         if line[current_pos] != ' ':
              operand_comment_start_col = current_pos
              break
         current_pos += 1

    if operand_comment_start_col == -1: # No operand or comment
        return fields

    # --- Operand/Comment Field ---
    remainder_of_line = line[operand_comment_start_col:]
    opcode_upper = fields['opcode'].upper() if fields['opcode'] else ""

    # --- Logic revision based on opcode type ---
    if opcode_upper in PSEUDO_OPS_WITH_SPECIAL_OPERAND_HANDLING:
        # For DIS, TITLE, etc., pass the entire remainder.
        # The specific parser must handle comments according to its rules.
        fields['operand_str'] = remainder_of_line
        fields['comment'] = None # Comment extraction deferred

        # --- Add Lexer Debugging ---
        if opcode_upper == 'DIS':
             print(f"Debug Lexer DIS (Line {line_num}): Passing Remainder='{fields['operand_str']}' to parser.")
        # --- End Lexer Debugging ---

    else:
        # --- Standard Operand/Comment Parsing ---
        # Operand ends at the first blank in the remainder.
        end_pos_operand_in_remainder = 0
        while end_pos_operand_in_remainder < len(remainder_of_line) and \
              remainder_of_line[end_pos_operand_in_remainder] != ' ':
            end_pos_operand_in_remainder += 1

        fields['operand_str'] = remainder_of_line[:end_pos_operand_in_remainder]

        # Comment starts after skipping blanks following the operand.
        start_pos_comment_in_remainder = end_pos_operand_in_remainder
        while start_pos_comment_in_remainder < len(remainder_of_line) and \
              remainder_of_line[start_pos_comment_in_remainder] == ' ':
            start_pos_comment_in_remainder += 1

        if start_pos_comment_in_remainder < len(remainder_of_line):
            # For standard instructions, assume anything after the first blank after the operand is comment.
            # No strict comment column check here, simplifies logic.
            fields['comment'] = remainder_of_line[start_pos_comment_in_remainder:]
        else:
            fields['comment'] = None

    return fields

# lexer.py v1.4
