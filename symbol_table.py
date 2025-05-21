# symbol_table.py v1.34
"""
Symbol Table for CRASS Assembler
[...]
v1.32: Modify dump_table to accept block_base_addresses and print absolute values for relocatable symbols.
       Adjust debug print in define to reflect value type based on pass.
v1.33: Correct logic in `is_defined` for qualified/unqualified symbol lookup.
v1.34: Add `suppress_undefined_error` parameter to `lookup` to control error
       reporting for undefined symbols, useful during speculative parsing in Pass 1.
"""
import sys
from typing import Optional, Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from assembler_state import AssemblerState
    # Add Assembler for block_base_addresses type hint if passed directly
    # from crass import Assembler

class SymbolTable:
    def __init__(self, error_reporter=None, debug_mode=False):
        self.symbols: Dict[str, Dict[str, Any]] = {}
        self.literals: Dict[int, Dict[str, Any]] = {}
        self.literal_list: List[int] = []
        self.literal_addr_map: Dict[int, int] = {}
        self.error_reporter = error_reporter
        self.debug_mode = debug_mode
        self.program_name_attributes: Optional[Dict[str, Any]] = None
        self.equ_star_symbols = set()
        self.current_pass_for_debug = 1 # For define method's debug print

    def _get_qualified_name(self, name: str, current_qualifier: Optional[str]) -> str:
        name = name.upper()
        if current_qualifier is None or current_qualifier == '*':
            return name
        if '$' in name: # Already qualified
             return name
        return f"{current_qualifier}${name}"

    def set_current_pass_for_debug(self, pass_num: int):
        self.current_pass_for_debug = pass_num

    def define(self, name: str, value: int, line_num: int, attrs: Optional[Dict[str, Any]] = None, current_qualifier: Optional[str] = None):
        if self.debug_mode:
            print(f"!!! DEBUG SYMTABLE.DEFINE (ENTRY - v1.34): L{line_num} for '{name}':") # Updated version
            print(f"    Value = {value:o}")
            print(f"    attrs param RECEIVED (id={id(attrs)}): {attrs}")
            if attrs is not None:
                print(f"    attrs param RECEIVED ['type'] = {attrs.get('type')}")
                print(f"    attrs param RECEIVED ['block'] = {attrs.get('block')}")

        qualified_name = self._get_qualified_name(name, current_qualifier)
        current_attrs_to_use: Dict[str, Any] = attrs.copy() if attrs is not None else {}

        is_set_definition = current_attrs_to_use.get('redefinable', False)
        is_program_name = current_attrs_to_use.get('program_name', False)
        is_equ_star = current_attrs_to_use.get('equ_star', False)
        is_loc_def = current_attrs_to_use.get('defined_by_loc', False)

        if self.debug_mode:
            symbol_type_for_debug = current_attrs_to_use.get('type', 'unknown')
            val_type_desc = "Value"
            if self.current_pass_for_debug == 1:
                if symbol_type_for_debug == 'relocatable': val_type_desc = "Relative"
                elif symbol_type_for_debug == 'absolute': val_type_desc = "Absolute"
                elif symbol_type_for_debug == 'external': val_type_desc = "External"
            else: val_type_desc = "Absolute (P2)"

            print(f">>> DEBUG LC: L{line_num} Define Symbol: '{qualified_name}' (SymbolTable Internal Process)")
            print(f"    Value = {value:o} ({val_type_desc} based on attrs & pass)")
            print(f"    Attrs (current_attrs_to_use, id={id(current_attrs_to_use)}): {current_attrs_to_use}")


        if qualified_name in self.symbols:
            existing_attrs = self.symbols[qualified_name]['attrs']
            existing_line = self.symbols[qualified_name]['line_num']
            existing_value = self.symbols[qualified_name]['value']
            was_set = existing_attrs.get('redefinable', False)
            was_program_name = existing_attrs.get('program_name', False)
            was_loc_def = existing_attrs.get('defined_by_loc', False)

            if was_program_name:
                 if self.error_reporter and not self.error_reporter.has_error_on_line(line_num):
                      self.error_reporter.add_error(f"Symbol '{name}' (qualified: {qualified_name}) defined by IDENT cannot be redefined.", line_num, code='L')
                 return False
            elif was_loc_def:
                 if is_loc_def and existing_value == value:
                      self.symbols[qualified_name]['attrs'].update(current_attrs_to_use)
                      return True
                 elif self.error_reporter and not self.error_reporter.has_error_on_line(line_num):
                      self.error_reporter.add_error(f"Symbol '{name}' (qualified: {qualified_name}) defined by LOC on line {existing_line} cannot be redefined by this statement.", line_num, code='L')
                 return False
            elif not was_set: # Not redefinable by SET
                 # Allow redefinition if value and critical attributes are identical (benign redefinition)
                 if existing_value == value and \
                    existing_attrs.get('type') == current_attrs_to_use.get('type') and \
                    existing_attrs.get('block') == current_attrs_to_use.get('block'):
                      # Update with potentially new non-critical attrs (like 'equ_star' if it wasn't there)
                      self.symbols[qualified_name]['attrs'].update(current_attrs_to_use)
                      return True
                 else:
                      if self.error_reporter and not self.error_reporter.has_error_on_line(line_num):
                           self.error_reporter.add_error(f"Symbol '{name}' (qualified: {qualified_name}) already defined on line {existing_line} (Val={existing_value:o}, Attrs={existing_attrs}) and is not redefinable by new (Val={value:o}, Attrs={current_attrs_to_use}).", line_num, code='L')
                      return False
            elif was_set and not is_set_definition: # Was SET, now trying to define with non-SET (e.g. EQU)
                 if self.error_reporter and not self.error_reporter.has_error_on_line(line_num):
                      self.error_reporter.add_error(f"Symbol '{name}' (qualified: {qualified_name}) defined by SET on line {existing_line} cannot be redefined by non-SET.", line_num, code='L')
                 return False
            # If was_set and is_set_definition, it's a valid redefinition by SET.

        self.symbols[qualified_name] = {
            'value': value,
            'line_num': line_num,
            'attrs': current_attrs_to_use # Use the (potentially copied and modified) attrs
        }
        if is_program_name:
             if self.program_name_attributes is not None and self.program_name_attributes['name'] != name.upper() :
                  if self.error_reporter and not self.error_reporter.has_error_on_line(line_num):
                       self.error_reporter.add_error(f"Program name '{name}' conflicts with previous IDENT '{self.program_name_attributes['name']}'.", line_num, code='L')
                  return False
             self.program_name_attributes = {'name': name.upper(), 'value': value, 'type': current_attrs_to_use.get('type', 'absolute')}

        if is_equ_star: # Track symbols defined by EQU *
            self.equ_star_symbols.add(qualified_name)

        return True

    def is_defined(self, name: str, current_qualifier: Optional[str] = None) -> bool:
        name_upper = name.upper()
        if current_qualifier and current_qualifier != '*':
            qualified_name = f"{current_qualifier}${name_upper}"
            if qualified_name in self.symbols:
                return True
        # Always check the unqualified (global) name as a fallback or primary if no qualifier
        return name_upper in self.symbols

    def lookup(self, name: str, line_num: int, current_qualifier: Optional[str] = None, suppress_undefined_error: bool = False) -> Optional[Dict[str, Any]]:
        name_upper = name.upper()
        qualified_name_attempt = self._get_qualified_name(name_upper, current_qualifier) # This handles '$' in name

        entry = self.symbols.get(qualified_name_attempt)

        if entry:
            if self.debug_mode: print(f"!!! DEBUG SYMTABLE.LOOKUP: L{line_num} Found '{qualified_name_attempt}': Value={entry['value']:o}, Attrs={entry['attrs']}")
            return entry

        # If qualified lookup failed AND a qualifier was active, try unqualified (global)
        if current_qualifier and current_qualifier != '*' and qualified_name_attempt != name_upper:
            entry = self.symbols.get(name_upper)
            if entry:
                if self.debug_mode: print(f"!!! DEBUG SYMTABLE.LOOKUP: L{line_num} Found unqualified '{name_upper}' (fallback): Value={entry['value']:o}, Attrs={entry['attrs']}")
                return entry

        # If still not found, check program name as a last resort (program name is always global)
        if self.program_name_attributes and name_upper == self.program_name_attributes['name']:
            # Construct a temporary entry for program name to match expected return type
            return {
                'value': self.program_name_attributes['value'],
                'line_num': 0, # Or some indicator it's from IDENT
                'attrs': {'type': self.program_name_attributes.get('type', 'absolute'), 'program_name': True, 'block': '*ABS*'}
            }

        if self.error_reporter and not suppress_undefined_error: # Check the flag here
            if not self.error_reporter.has_error_on_line(line_num): # Report only once per line
                self.error_reporter.add_error(f"Undefined symbol '{name}' (Qualifier: {current_qualifier})", line_num, code='U')
        return None


    def get_attributes(self, name: str, current_qualifier: Optional[str] = None) -> Optional[Dict[str, Any]]:
        # Simplified: lookup should handle finding the correct entry
        entry = self.lookup(name, 0, current_qualifier) # line_num 0 for non-error-reporting lookup
        return entry.get('attrs') if entry else None

    def get_program_name_attributes(self):
         return self.program_name_attributes

    def add_literal(self, value: int, line_num: int):
        if not isinstance(value, int):
            if self.error_reporter:
                self.error_reporter.add_error(f"Invalid literal value type: {type(value)} for {value}", line_num, code='F')
            return
        if value not in self.literals:
            self.literals[value] = {'defined_line': line_num, 'address': -1}
            if value not in self.literal_list: # Ensure unique values in order of appearance
                 self.literal_list.append(value)

    def assign_literal_addresses(self, start_address: int) -> int:
        current_addr = start_address
        # Sort by value for consistent address assignment if multiple LITs define same value (though add_literal prevents duplicates in list)
        # self.literal_list.sort() # Not strictly needed if add_literal maintains order and uniqueness
        for value in self.literal_list: # Process in order of first appearance
            if value in self.literals and self.literals[value]['address'] == -1: # Assign only once
                self.literals[value]['address'] = current_addr
                self.literal_addr_map[value] = current_addr
                current_addr += 1
        return current_addr # Return next available address after literal block

    def lookup_literal_address(self, value: int, line_num: int) -> Optional[int]:
        addr = self.literal_addr_map.get(value)
        if addr is None:
            # This case should ideally not happen if add_literal and assign_literal_addresses are correct
            if value in self.literals: # It was defined
                 addr = self.literals[value].get('address')
                 if addr == -1: addr = None # Address not yet assigned
            if addr is None and self.error_reporter: # Still not found or assigned
                self.error_reporter.add_error(f"Internal: Literal value {value:o} (decimal: {value}) not found or address not assigned in literal pool.", line_num, code='F')
            return None
        return addr

    def get_literal_pool(self) -> List[int]:
        return self.literal_list # Returns literals in order of first definition

    def get_literal_block_size(self) -> int:
         return len(self.literal_list) # Number of unique literals

    def dump_table(self, file_handle=sys.stdout, block_base_addresses: Optional[Dict[str, int]] = None):
        """
        Dumps the symbol table.
        If block_base_addresses is provided (typically for Pass 2 dump),
        it calculates and prints absolute addresses for relocatable symbols.
        """
        def write_line(line):
            try:
                file_handle.write(line + "\n")
            except IOError:
                print(line, file=sys.__stderr__) # Fallback to stderr if file_handle is bad

        write_line("\n--- Symbol Table Dump ---")
        if self.program_name_attributes:
             pna = self.program_name_attributes
             write_line(f"  Program Name: {pna['name']} (Value={pna['value']:o}, Type={pna['type']})")

        # Separate qualified and unqualified symbols for structured printing
        unqualified_symbols = {}
        qualified_symbols_by_qualifier: Dict[str, Dict[str, Any]] = {}

        for q_name, data in self.symbols.items():
            if self.program_name_attributes and q_name == self.program_name_attributes['name'] and data['attrs'].get('program_name'):
                continue # Skip program name entry if already printed

            if '$' in q_name:
                qual, simple_name = q_name.split('$', 1)
                if qual not in qualified_symbols_by_qualifier:
                    qualified_symbols_by_qualifier[qual] = {}
                qualified_symbols_by_qualifier[qual][simple_name] = data
            else:
                unqualified_symbols[q_name] = data

        if not unqualified_symbols and not qualified_symbols_by_qualifier and not self.program_name_attributes:
            write_line("  (No symbols defined)")
        elif not unqualified_symbols and not qualified_symbols_by_qualifier and self.program_name_attributes:
             write_line("  (No other symbols defined beyond program name)")


        # Print unqualified symbols first
        if unqualified_symbols:
            for name in sorted(unqualified_symbols.keys()):
                self._print_symbol_entry(name, unqualified_symbols[name], file_handle, block_base_addresses)

        # Print qualified symbols
        for qualifier in sorted(qualified_symbols_by_qualifier.keys()):
            write_line(f"\n\n                                                  SYMBOL QUALIFIER =  {qualifier}\n")
            for simple_name in sorted(qualified_symbols_by_qualifier[qualifier].keys()):
                data = qualified_symbols_by_qualifier[qualifier][simple_name]
                # For display, show the simple name, not the internally qualified one
                self._print_symbol_entry(simple_name, data, file_handle, block_base_addresses, qualifier)

        write_line("--- End Symbol Table Dump ---")

    def _print_symbol_entry(self, name_to_print: str, data: Dict[str, Any], file_handle,
                            block_base_addresses: Optional[Dict[str, int]],
                            display_qualifier: Optional[str] = None): # Added display_qualifier
        """Helper to print a single symbol entry, calculating absolute value if needed."""
        attrs = data.get('attrs', {})
        raw_value = data.get('value') # This is relative value from Pass 1
        sym_type = attrs.get('type', 'absolute')
        sym_block = attrs.get('block')
        display_value = raw_value # Start with the raw (relative) value

        # If Pass 2 dump and symbol is relocatable in a named block, calculate absolute value
        if block_base_addresses and sym_type == 'relocatable' and sym_block and sym_block != '*ABS*':
            base_addr = block_base_addresses.get(sym_block)
            if base_addr is not None and isinstance(raw_value, int):
                display_value = raw_value + base_addr # Now display_value is absolute
            # else: error or non-integer raw_value, display_value remains raw_value

        attrs_str_parts = []
        for k, v_attr in sorted(attrs.items()):
            if isinstance(v_attr, bool) and not v_attr: continue # Don't print False booleans
            if k == 'block' and v_attr == '*ABS*' and sym_type == 'absolute': continue # Often redundant
            attrs_str_parts.append(f"{k}={v_attr}")
        attrs_str = ", ".join(attrs_str_parts)

        try:
            if isinstance(display_value, int):
                value_str = f"{display_value:o}"
            elif isinstance(display_value, str) and attrs.get('value_is_char', False):
                value_str = f"'{display_value}'" # Display char values in quotes
            else:
                value_str = str(display_value if display_value is not None else 'N/A')
        except (TypeError, ValueError): # Catch potential formatting errors for unusual values
            value_str = str(data.get('value', 'ERROR_FORMATTING'))

        line_num_str = str(data.get('line_num', 'N/A'))
        # Format to match good.txt: SYMBOL (8) VALUE (8) DEFLINE (6) REFLINES...
        # Our previous: NAME (12) VALUE (10) DEFLINE (4) ATTRS
        # New target: NAME (12) VALUE (10) DEFLINE (4) (No attributes for now to match good.txt closer)
        # For now, keep attributes for debugging.
        file_handle.write(f"  {name_to_print:<12}: Value={value_str:<10}, DefLine={line_num_str:<4}, Attrs=({attrs_str})\n")


    def lookup_symbol_value(self, name: str, line_num: int, current_qualifier: Optional[str] = None) -> Optional[int]:
        entry = self.lookup(name, line_num, current_qualifier)
        return entry['value'] if entry and isinstance(entry.get('value'), int) else None

    def get_symbol_type(self, name: str, line_num: int, current_qualifier: Optional[str] = None) -> Optional[str]:
         entry = self.lookup(name, line_num, current_qualifier) # Lookup handles error reporting
         if entry:
              return entry['attrs'].get('type', 'absolute')
         # Program name is implicitly absolute, handled by lookup if it returns an entry
         return None # If lookup returns None, type is unknown

    def mark_entry_point(self, name: str, line_num: int):
         # Placeholder for future XREF/linking features
         pass

    def update_symbol_value(self, name: str, value: int, line_num: int, symbol_type: str, current_qualifier: Optional[str] = None):
         # This method might be used if Pass 2 needs to adjust symbol values (e.g., for complex relocations)
         # For now, symbol values are primarily set in Pass 1 and made absolute in Pass 2 expressions.
         qualified_name = self._get_qualified_name(name, current_qualifier)
         if qualified_name in self.symbols:
              # Only update if truly necessary, to avoid unintended changes
              if self.symbols[qualified_name]['value'] != value or \
                 self.symbols[qualified_name]['attrs'].get('type') != symbol_type:
                   self.symbols[qualified_name]['value'] = value
                   self.symbols[qualified_name]['attrs']['type'] = symbol_type
                   # Potentially mark as 'updated_in_pass2' if needed for debugging
         else:
              if self.error_reporter:
                   self.error_reporter.add_error(f"Internal: Attempt to update undefined symbol '{name}' (Qualified: {qualified_name}) in Pass 2", line_num, code='F')

    def get_all_symbols(self) -> Dict[str, Dict[str, Any]]:
        return self.symbols

# symbol_table.py v1.34
