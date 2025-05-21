# errors.py v1.2
"""
Error reporting classes for the CRASS assembler.
Includes custom Exception classes.
"""

import sys

# --- Custom Exceptions ---

class AsmException(Exception):
    """Base class for assembler errors that should stop assembly."""
    def __init__(self, message, line_num=None, code='E'):
        super().__init__(message)
        self.message = message
        self.line_num = line_num
        # Ensure code is a single uppercase char, default 'E'
        self.code = code[0].upper() if code and isinstance(code, str) else 'E'

    def __str__(self):
        prefix = f"L{self.line_num}: " if self.line_num else ""
        return f"{prefix}{self.message} [{self.code}]"

class AsmWarning(Warning):
     """Class for assembler warnings that allow assembly to continue."""
     def __init__(self, message, line_num=None, code='W'):
        super().__init__(message)
        self.message = message
        self.line_num = line_num
        # Ensure code is a single uppercase char, default 'W'
        self.code = code[0].upper() if code and isinstance(code, str) else 'W'

     def __str__(self):
        prefix = f"L{self.line_num}: " if self.line_num else ""
        return f"{prefix}{self.message} [{self.code}]"


# --- Error Reporter Class ---

class ErrorReporter:
    """Handles collection and reporting of errors and warnings."""
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.error_lines = set() # Track lines with errors

    def has_errors(self):
        return bool(self.errors)

    def has_warnings(self):
        return bool(self.warnings)

    def has_error_on_line(self, line_num):
        """Checks if an error has already been reported for a specific line."""
        return line_num in self.error_lines

    def add_error(self, message, line_num=None, code='E'):
        """Adds an error message."""
        self.errors.append({'message': message, 'line_num': line_num, 'code': code})
        if line_num:
            self.error_lines.add(line_num)

    def add_warning(self, message, line_num=None, code='W'):
        """Adds a warning message."""
        self.warnings.append({'message': message, 'line_num': line_num, 'code': code})

    def get_error_code_for_line(self, line_num):
         """ Gets the highest severity error code for a given line (F > E > W > None). """
         codes = set()
         for e in self.errors:
              if e['line_num'] == line_num: codes.add(e['code'])
         for w in self.warnings:
              if w['line_num'] == line_num: codes.add(w['code'])

         if 'F' in codes: return 'F'
         if 'E' in codes: return 'E'
         # Add other codes here if needed (A, S, O, etc.) - map to severity?
         # For now, just prioritize F/E over W
         if 'A' in codes: return 'A' # Example: Assembly error
         if 'S' in codes: return 'S' # Example: Syntax error
         if 'O' in codes: return 'O' # Example: Operand error
         if 'U' in codes: return 'U' # Example: Undefined symbol
         if 'V' in codes: return 'V' # Example: Value error
         if 'L' in codes: return 'L' # Example: Label error
         if 'C' in codes: return 'C' # Example: Character error
         if 'R' in codes: return 'R' # Example: Relocation/Type error
         if 'I' in codes: return 'I' # Example: Internal/Instruction Table error

         if 'W' in codes: return 'W'
         return "" # No error/warning code for this line

    def print_summary(self):
        """Prints all collected errors and warnings."""
        if self.errors:
            print("\n--- Errors ---", file=sys.stderr)
            for error in sorted(self.errors, key=lambda x: x['line_num'] or 0):
                line_prefix = f"L{error['line_num']}: " if error['line_num'] else ""
                print(f"{line_prefix}{error['message']} [{error['code']}]", file=sys.stderr)
        if self.warnings:
            print("\n--- Warnings ---", file=sys.stderr)
            for warning in sorted(self.warnings, key=lambda x: x['line_num'] or 0):
                line_prefix = f"L{warning['line_num']}: " if warning['line_num'] else ""
                print(f"{line_prefix}{warning['message']} [{warning['code']}]", file=sys.stderr)

        print(f"\nTotal Errors: {len(self.errors)}, Total Warnings: {len(self.warnings)}")

# errors.py v1.2
