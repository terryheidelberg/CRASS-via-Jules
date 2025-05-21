# instruction_table.py v1.9
"""
Manages the instruction set definitions for the CRASS assembler.
Loads instruction details from inst-map.txt and provides lookup methods.
Handles common MMi mnemonic patterns explicitly.
Added get_base_mnemonic method.
v1.8: Make _load_instruction_map more robust to comments in the operand_format
      field of inst-map.txt.
v1.9: - Fix AttributeError: 'InstructionTable' object has no attribute 'debug_mode'.
      - Fix NameError: name 'traceback' is not defined by adding import.
"""

import re
import sys
import traceback # Added import

class InstructionTable:
    """Loads and provides access to CDC 6000 instruction definitions."""
    def __init__(self, map_file='inst-map.txt'):
        self._instructions = {}
        self._pseudo_ops = set() 
        self._pattern_key_map = {}
        self._pattern_prefixes = {'SA', 'SB', 'SX', 'LX', 'AX', 'FX', 'RX', 'DX', 'IX', 'NX', 'ZX', 'UX', 'PX', 'MX', 'CX', 'BX'}
        self._map_file = map_file
        print(f"Initializing InstructionTable from '{map_file}'...") 
        self._load_pseudo_ops()
        self._load_instruction_map()
        self._build_pattern_key_map()

    def _load_pseudo_ops(self):
        """Initializes the set of known pseudo-operations."""
        self._pseudo_ops = set([
            'IDENT', 'END', 'ABS', 'REL', 'USE', 'LOC', 'ORG', 'ORGC', 'FIN',
            'BASE', 'CODE', 'QUAL', 'SEQ', 'COL', 'LIST', 'NOLIST',
            'DATA', 'CON', 'LIT', 'DIS', 'VFD', 'BSS', 'BSSZ', 'COMMON', 'ENDC',
            'EQU', '=', 'SET', 'MAX', 'MIN', 'MICCNT', 'SST', 
            'IF', 'IFTPA', 'IFCP', 'IFPP', 'IFPPA', 'IFPP7', 
            'IFEQ', 'IFNE', 'IFGT', 'IFGE', 'IFLT', 'IFLE', 
            'IFPL', 'IFMI',
            'IFC', 
            'ENDIF', 'ELSE', 'SKIP',
            'ENTRY', 'EXT', 
            'TITLE', 'TTL', 'SPACE', 'EJECT', 'NOREF', 'XREF',
            'CTEXT', 'XTEXT', 'ENDX',
            'MACRO', 'MACROE', 'ENDM', 'LOCAL', 'IRP', 'ENDD', 'OPDEF', 'PURGE',
            'DUP', 'ECHO', 'RMT', 'HERE', 
            'PPU', 'PERIPH', 'PPOP', 'OPSYN', 
            'MICRO', 'DECMIC', 'OCTMIC', 'ENDMIC',
            'MACHINE', 
            'B1=1', 'B7=1', 'CHAR', 'COMMENT', 'CPOP', 'CPSYN', 'ENTRYC',
            'ERR', 'ERRMI', 'ERRNG', 'ERRNZ', 'ERRPL', 'ERRZR', 'LCC',
            'NIL', 'NOLABEL', 'PURGDEF', 'PURGMAC',
            'REP', 'REPC', 'REPI', 'R=', 'SEG', 'SEGMENT',
            'STEXT', 'STOPDUP',
            'USELCM', 'POS', 
        ])
        self._pseudo_ops = set(op.upper() for op in self._pseudo_ops)
        print(f"Debug: Loaded {len(self._pseudo_ops)} pseudo-op names.")


    def _load_instruction_map(self):
        """Loads instruction definitions from the inst-map.txt file."""
        try:
            with open(self._map_file, 'r') as f:
                line_num = 0
                for line in f:
                    line_num += 1
                    line = line.strip()
                    if not line or line.startswith('#'): 
                        continue

                    parts = re.split(r'\s+', line, maxsplit=3)
                    if len(parts) < 3:
                        print(f"Warning: Skipping malformed line {line_num} in {self._map_file}: '{line}'")
                        continue

                    width_str, opcode_str, mnemonic_raw = parts[0], parts[1], parts[2]
                    
                    operand_format = ""
                    if len(parts) > 3:
                        temp_format = parts[3]
                        comment_match_compass_1 = re.search(r'\s+\*(.*)', temp_format)
                        comment_match_compass_2 = re.search(r'\s+\.(.*)', temp_format)
                        comment_match_hash = temp_format.find('#') 

                        end_pos = len(temp_format)
                        if comment_match_compass_1:
                            end_pos = min(end_pos, comment_match_compass_1.start())
                        if comment_match_compass_2:
                            end_pos = min(end_pos, comment_match_compass_2.start())
                        if comment_match_hash != -1:
                            end_pos = min(end_pos, comment_match_hash)
                        
                        operand_format = temp_format[:end_pos].strip()

                    try:
                        width = int(width_str)
                        opcode_val = int(opcode_str, 8) 

                        mnemonic_upper = mnemonic_raw.upper()
                        instr_def = {
                            'width': width,
                            'opcode_oct': opcode_str, 
                            'opcode_val': opcode_val, 
                            'format': operand_format,
                            'mnemonic': mnemonic_raw, 
                            'source_line': line_num
                        }

                        if mnemonic_upper not in self._instructions:
                            self._instructions[mnemonic_upper] = []
                        self._instructions[mnemonic_upper].append(instr_def)

                    except ValueError:
                        print(f"Warning: Skipping invalid numeric value on line {line_num} in {self._map_file}: '{line}'")
                        continue
            
            # Removed the self.debug_mode check here as it was causing the AttributeError
            # and InstructionTable doesn't need its own debug_mode for this.
            # If specific debug prints are needed here, debug_mode should be passed to __init__.
            # For now, the general print below is sufficient.

            print(f"Debug: Loaded definitions for {len(self._instructions)} unique base mnemonics from {self._map_file}.")

        except FileNotFoundError:
            print(f"Error: Instruction map file not found: {self._map_file}", file=sys.stderr)
            raise SystemExit(f"Error: Missing required file '{self._map_file}'")
        except Exception as e:
            print(f"Error reading instruction map file {self._map_file}: {e}", file=sys.stderr)
            traceback.print_exc() 
            raise SystemExit(f"Error reading '{self._map_file}'")

    def _build_pattern_key_map(self):
        """Builds the map from base (e.g., SA) to map key (e.g., SAI)."""
        self._pattern_key_map = {}
        for key in self._instructions.keys():
             if len(key) >= 3 and key[-1] == 'I' and key[:-1] in self._pattern_prefixes:
                 base = key[:-1]
                 self._pattern_key_map[base] = key
        print(f"Debug: Built pattern key map for {len(self._pattern_key_map)} mnemonics (e.g., SA -> SAI).")


    def is_instruction(self, mnemonic):
        if mnemonic is None:
            return False
        mnemonic_upper = mnemonic.upper()
        if mnemonic_upper in self._instructions:
            return True
        if len(mnemonic_upper) >= 3:
            base = mnemonic_upper[:-1]
            digit = mnemonic_upper[-1]
            if base in self._pattern_key_map and digit in '01234567':
                 return self._pattern_key_map.get(base) in self._instructions
        if mnemonic_upper == 'NO' and 'NO' in self._instructions:
             return True
        return False

    def is_pseudo_op(self, mnemonic):
        if mnemonic is None:
            return False
        return mnemonic.upper() in self._pseudo_ops

    def get_instruction_details(self, mnemonic):
        if mnemonic is None:
            return None
        mnemonic_upper = mnemonic.upper()
        if mnemonic_upper in self._instructions:
            return self._instructions[mnemonic_upper]
        if len(mnemonic_upper) >= 3:
            base = mnemonic_upper[:-1]
            digit = mnemonic_upper[-1]
            if base in self._pattern_key_map and digit in '01234567':
                map_key = self._pattern_key_map.get(base)
                if map_key:
                    return self._instructions.get(map_key)
        if mnemonic_upper == 'NO':
             return self._instructions.get('NO')
        return None

    def get_base_mnemonic(self, mnemonic):
        if mnemonic is None:
            return None
        mnemonic_upper = mnemonic.upper()
        if len(mnemonic_upper) >= 3:
            base = mnemonic_upper[:-1]
            digit = mnemonic_upper[-1]
            if base in self._pattern_prefixes and digit in '01234567':
                return base 
        return mnemonic_upper 

# instruction_table.py v1.9
