# crass.py v1.87
"""
CRASS - COMPASS Cross-Assembler for CDC 6000 series.
Main application entry point.

v1.82: Add more id(symbol_table) checks for debugging EQU *.
v1.83: Add macro_definitions dictionary to Assembler class.
v1.84: Add end_statement_label attribute for deferred END label definition.
v1.85: Add block_base_addresses attribute for Pass 2 block LC calculation.
v1.86: Add endl_listing_value attribute for specific ENDL line LC.
v1.87: Add remote_blocks dictionary for RMT/HERE pseudo-op.
"""

import argparse
import sys
import os
import traceback 
from typing import List, Optional, Tuple, Dict, Any

from lexer import parse_line 
from symbol_table import SymbolTable 
from instruction_table import InstructionTable 
from assembler_state import AssemblerState 
from output_generator import OutputGenerator 
from errors import ErrorReporter, AsmException, AsmWarning
from pass_logic import perform_pass

DEFAULT_BINARY_FILENAME = "binfile"
VERSION = "0.0.87" 

class Assembler:
    """ Encapsulates the assembler state and processes. """
    def __init__(self, input_filename: str, listing_filename: Optional[str] = None, binary_filename: Optional[str] = None, debug_mode: bool = False):
        self.input_filename = input_filename
        self.listing_filename = listing_filename
        self.binary_filename = binary_filename if binary_filename else DEFAULT_BINARY_FILENAME
        self.debug_mode = debug_mode
        self.error_reporter = ErrorReporter()
        self.symbol_table = SymbolTable(self.error_reporter, debug_mode=self.debug_mode) 
        self.instruction_table = InstructionTable() 
        self.state = AssemblerState() 
        self.state.error_reporter = self.error_reporter
        self.state.symbol_table = self.symbol_table
        self.state.debug_mode = self.debug_mode
        self.output_generator: Optional[OutputGenerator] = None
        self.lines: List[str] = []
        self.parsed_lines: Dict[int, Dict[str, Any]] = {}
        self._listing_handle = None
        self._binary_handle = None
        self.macro_definitions: Dict[str, Dict[str, Any]] = {}
        self.micro_definitions: Dict[str, str] = {} 
        self.remote_blocks: Dict[str, List[Dict[str, Any]]] = {} # Stores parsed lines for RMT blocks
        self.block_base_addresses: Dict[str, int] = {}
        if self.debug_mode: print(f"Debug CRASS Init: Symbol table ID = {id(self.symbol_table)}")
        self.end_statement_label: Optional[str] = None
        self.total_program_length_for_listing: Optional[int] = None 
        self.endl_listing_value: Optional[int] = None 


    def assemble(self):
        """ Performs the two-pass assembly process. """
        print(f"Starting assembly for: {self.input_filename}")
        if not self._read_input_file(): return False

        self._listing_handle = sys.stdout
        opened_listing = False
        if self.listing_filename:
            try:
                self._listing_handle = open(self.listing_filename, 'w')
                opened_listing = True
            except IOError as e:
                self.error_reporter.add_error(f"Cannot open listing file '{self.listing_filename}': {e}", 0, code='F')
                return False

        self._binary_handle = None
        opened_binary = False
        if self.binary_filename:
             try:
                  self._binary_handle = open(self.binary_filename, 'w')
                  opened_binary = True
             except IOError as e:
                  self.error_reporter.add_error(f"Cannot open binary file '{self.binary_filename}': {e}", 0, code='F')
                  if opened_listing and self._listing_handle != sys.stdout: self._listing_handle.close()
                  return False

        print("Initializing SymbolTable...")
        print(f"Initializing InstructionTable...")
        print("Initializing AssemblerState...")

        print("\n--- Starting Pass 1 ---")
        if self.debug_mode: print(f"Debug CRASS: Symbol table ID before Pass 1: {id(self.symbol_table)}")
        pass1_success = perform_pass(self, 1)
        if not pass1_success:
            print("Assembly failed in Pass 1.")
            self._print_summary()
            if opened_listing and self._listing_handle != sys.stdout: self._listing_handle.close()
            if opened_binary: self._binary_handle.close()
            return False
        print("--- Pass 1 Complete ---")

        if self.debug_mode:
            print(f"Debug CRASS: Symbol table ID after Pass 1: {id(self.symbol_table)}")
            self.symbol_table.dump_table(file_handle=sys.stdout)
            print("\n--- Macro Definitions (End of Pass 1) ---")
            if not self.macro_definitions:
                 print("  (No macros defined)")
            else:
                 for name, definition in self.macro_definitions.items():
                      print(f"  Macro: {name}")
                      print(f"    Type: {definition.get('type', '??')}")
                      print(f"    Params: {definition.get('params', [])}")
                      print(f"    Body Lines: {len(definition.get('body', []))}")
            print("--- End Macro Definitions ---\n")
            print("\n--- Remote Blocks (End of Pass 1) ---")
            if not self.remote_blocks:
                print("  (No remote blocks defined)")
            else:
                for name, block_lines in self.remote_blocks.items():
                    print(f"  Remote Block: {name} ({len(block_lines)} lines)")
                    # for line_dict in block_lines:
                    #     print(f"    L{line_dict['line_num']}: {line_dict['original']}")
            print("--- End Remote Blocks ---\n")

            print("\n--- Block Base Addresses (End of Pass 1) ---")
            if not self.block_base_addresses:
                 print("  (No blocks defined or sized)")
            else:
                 for name, addr in sorted(self.block_base_addresses.items()):
                      print(f"  Block: {name:<10} Base Address: {addr:o}")
            print("--- End Block Base Addresses ---\n")


        if self.error_reporter.has_errors():
            print("Assembly failed in Pass 1 due to errors."); self._print_summary()
            if opened_listing and self._listing_handle != sys.stdout: self._listing_handle.close()
            if opened_binary: self._binary_handle.close()
            return False

        print("\n--- Starting Pass 2 ---")
        if self.debug_mode: print(f"Debug CRASS: Symbol table ID before Pass 2: {id(self.symbol_table)}")
        pass2_success = perform_pass(self, 2)
        if not pass2_success:
            print("Assembly failed in Pass 2.")
            if not self.output_generator:
                 if opened_listing and self._listing_handle != sys.stdout: self._listing_handle.close()
                 if opened_binary: self._binary_handle.close()
            else:
                 pass # Output generator will be closed in finally block of main
            self._print_summary()
            return False
        print("--- Pass 2 Complete ---")

        if self.output_generator:
             if self.debug_mode: print(f"Debug: OutputGenerator closed by perform_pass.")
        else: # Should not happen if Pass 2 succeeded and initialized it
             if opened_listing and self._listing_handle != sys.stdout: self._listing_handle.close()
             if opened_binary: self._binary_handle.close()

        self._print_summary()

        if self.error_reporter.has_errors(): print("Assembly finished with errors."); return False
        elif self.error_reporter.has_warnings(): print("Assembly finished with warnings."); return True
        else: print("Assembly finished successfully."); return True

    def _read_input_file(self) -> bool:
        try:
            with open(self.input_filename, 'r') as f: self.lines = f.readlines()
            # Strip only trailing whitespace, preserve leading for fixed format
            self.lines = [line.rstrip('\n\r') for line in self.lines]; return True
        except FileNotFoundError: self.error_reporter.add_error(f"Input file not found: {self.input_filename}", 0); return False
        except Exception as e: self.error_reporter.add_error(f"Error reading input file: {e}", 0); return False

    def _print_summary(self):
        print("\n--- Assembly Summary ---")
        self.error_reporter.print_summary()
        print("--- End Summary ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"CRASS COMPASS Assembler v{VERSION}")
    parser.add_argument("input_file", help="COMPASS source file to assemble.")
    parser.add_argument("-l", "--listing", help="Output listing file name (defaults to stdout).")
    parser.add_argument("-o", "--output", help=f"Output binary file name (defaults to '{DEFAULT_BINARY_FILENAME}').")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug mode.")

    args = parser.parse_args()

    # Handles are managed by Assembler instance now
    assembler = Assembler(
        input_filename=args.input_file,
        listing_filename=args.listing,
        binary_filename=args.output, # Pass None if not specified, Assembler will use default
        debug_mode=args.debug
    )
    
    exit_code = 0
    try:
        if not assembler.assemble(): exit_code = 1
    except SystemExit as e:
         if isinstance(e.code, int): exit_code = e.code
         else: exit_code = 1
         print(f"Assembly aborted with exit code {exit_code}.")
    except Exception as e: print(f"CRITICAL UNHANDLED ERROR: {e}"); traceback.print_exc(); exit_code = 1
    # finally:
    #     # OutputGenerator.close() is called at the end of perform_pass(2)
    #     # or if perform_pass(1) fails and files were opened.
    #     # Redundant closing here might cause issues if already closed.
    #     pass

    print("Done."); sys.exit(exit_code)

# crass.py v1.87
