# pass_logic.py v2.13
"""
Contains the pass processing logic for CRASS assembler.
Moved from crass.py v1.79.
[...]
v2.12 - Correct arguments passed to process_line_pass_2 to match its
        updated 5-argument signature.
v2.13 - Ensure handle_pseudo_op_pass_2 is imported for Pass 2 IDENT pre-processing.
"""
import traceback
import sys 
from typing import List, Optional, Tuple, Dict, Any, TYPE_CHECKING
import re 

if TYPE_CHECKING: 
    from crass import Assembler
    # from symbol_table import SymbolTable # No longer directly passed
    # from instruction_table import InstructionTable # No longer directly passed
    # from errors import ErrorReporter # No longer directly passed
    # from output_generator import OutputGenerator # No longer directly passed
    # from assembler_state import AssemblerState # No longer directly passed

from lexer import parse_line
from errors import AsmException, ErrorReporter 
from expression import ExpressionError, evaluate_expression, evaluate_data_item
# operand_parser not directly used here
from pseudo_op_handlers import handle_pseudo_op_pass_1, handle_pseudo_op_pass_2, calculate_pseudo_op_size # Ensure all are imported
# instruction_assembler not directly used here
from output_generator import OutputGenerator 
from assembler_state import AssemblerState 
# from symbol_table import SymbolTable # Needed for temp state in P1 LIT scan
from assembler_state import handle_force_upper
from pass1_processing import process_line_pass_1
from pass2_processing import process_line_pass_2


def perform_pass(assembler: 'Assembler', pass_num: int) -> bool:
    assembler.state.set_pass(pass_num)
    assembler.symbol_table.set_current_pass_for_debug(pass_num) 
    assembler.end_statement_label = None
    ident_line_listed_in_pass2_setup = False 

    if pass_num == 1:
        ident_offset = 0 
        temp_literal_pool = {} 
        temp_symbol_table_for_lit_scan = assembler.symbol_table 
        temp_state_for_lit_scan = AssemblerState()
        temp_state_for_lit_scan.current_base = 'D' 
        temp_state_for_lit_scan.current_code = 'D' 
        temp_state_for_lit_scan.debug_mode = assembler.debug_mode
        temp_state_for_lit_scan.error_reporter = assembler.error_reporter 
        temp_state_for_lit_scan.symbol_table = temp_symbol_table_for_lit_scan
        temp_state_for_lit_scan.assembler = assembler 

        for i, line_content in enumerate(assembler.lines):
            ln = i + 1
            line_content_upper = line_content.upper()
            if not line_content.strip().startswith('*') and 'LIT' in line_content_upper:
                 parsed_temp = parse_line(line_content, ln)
                 if parsed_temp.get('opcode','').upper() == 'LIT':
                      operand_str = parsed_temp.get('operand_str', '')
                      if operand_str:
                           operand_str_parsed = operand_str.split('.')[0].split('*')[0].strip()
                           literal_strings = operand_str_parsed.split(',')
                           for lit_str in literal_strings:
                                lit_str = lit_str.strip()
                                if not lit_str: continue
                                try:
                                     lit_value, lit_type, _ = evaluate_data_item(lit_str, temp_symbol_table_for_lit_scan, temp_state_for_lit_scan, ln, assembler, suppress_undefined_error=True)
                                     if lit_type == 'absolute':
                                          if lit_value not in temp_literal_pool:
                                               temp_literal_pool[lit_value] = ln 
                                except ExpressionError:
                                     pass 
                                except Exception:
                                     pass 
        literal_size = len(temp_literal_pool) 
        
        initial_pass1_lc = 0 
        assembler.state.location_counter = initial_pass1_lc
        assembler.state.position_counter = 0
        assembler.state.current_block = "*ABS*" 
        assembler.state.block_lcs = {'*ABS*': initial_pass1_lc} 
        assembler.state.block_order = [] 
        assembler.state.conditional_stack = [(True)]
        assembler.state.end_statement_processed = False
        assembler.state.current_line_number = 0
        assembler.state.line_start_address = initial_pass1_lc
        assembler.state.line_start_position_bits = 0
        assembler.state.current_base = 'D' 
        assembler.state.current_code = 'D' 
        assembler.state.current_qualifier = None
        assembler.state.program_start_symbol = None
        assembler.state.program_start_address = None 
        assembler.state.current_title = ""
        assembler.state.current_ttl_title = ""
        assembler.state.skip_count = 0
        assembler.parsed_lines = {} 
        assembler.symbol_table.symbols = {}
        assembler.symbol_table.literals = {}
        assembler.symbol_table.literal_list = []
        assembler.symbol_table.literal_addr_map = {}
        assembler.symbol_table.program_name_attributes = None
        assembler.symbol_table.equ_star_symbols = set()
        assembler.macro_definitions = {}
        assembler.micro_definitions = {}
        assembler.remote_blocks = {} 
        assembler.state.is_defining = None
        assembler.state.current_definition_name = None
        assembler.state.current_definition_params = []
        assembler.state.current_definition_lines = []
        assembler.block_base_addresses = {} 
        assembler.state.assembler = assembler 
        assembler.total_program_length_for_listing = 0 
        assembler.endl_listing_value = 0 

    elif pass_num == 2:
        assembler.state.reset_for_pass2() 
        assembler.ident_line_num = -1 # type: ignore 
        assembler.state.assembler = assembler 

    if pass_num == 2:
        assembler.output_generator = None 
        try:
            assembler.output_generator = OutputGenerator(assembler._listing_handle, assembler._binary_handle)
            if assembler.output_generator: 
                assembler.output_generator.assembler_ref = assembler 

            ident_word_value = None
            for ln_idx, p_line_content in enumerate(assembler.lines):
                ln = ln_idx + 1
                p_dict = assembler.parsed_lines.get(ln)
                if not p_dict: 
                    p_dict = parse_line(p_line_content, ln)
                    p_dict['original'] = p_line_content 

                if p_dict.get('opcode', '').upper() == 'IDENT':
                    assembler.ident_line_num = ln # type: ignore
                    # Ensure handle_pseudo_op_pass_2 is available
                    ident_binary_data = handle_pseudo_op_pass_2(assembler, ln, 'IDENT', p_dict.get('operand_str', ''), p_dict.get('label'))
                    if ident_binary_data and len(ident_binary_data) == 1 and ident_binary_data[0][1] == 60:
                        ident_word_value = ident_binary_data[0][0]
                        if assembler.output_generator: assembler.output_generator.add_full_word_to_binary(0, ident_word_value)
                    else:
                        assembler.error_reporter.add_error("Failed to generate IDENT word value for binary.", ln, 'F')

                    error_code_ident = assembler.error_reporter.get_error_code_for_line(ln)
                    if assembler.output_generator:
                        assembler.output_generator.write_listing_line(
                            ln, None, 0, None, p_dict['original'], error_code_ident, state=assembler.state, pseudo_op_mnemonic='IDENT'
                        ) 
                    ident_line_listed_in_pass2_setup = True
                    break 

            literal_pool = assembler.symbol_table.get_literal_pool()
            literal_block_start_lc = 0 
            if literal_pool:
                code_start_lc = assembler.state.location_counter
                code_start_pc = assembler.state.position_counter
                
                assembler.state.set_location_counter(literal_block_start_lc, 0) 
                
                for lit_val in literal_pool:
                     lit_addr_abs = assembler.symbol_table.lookup_literal_address(lit_val, 0) 
                     if lit_addr_abs is None:
                          assembler.error_reporter.add_error(f"Internal: Failed to lookup address for literal {lit_val:o}", 0, 'F'); continue
                     
                     if assembler.state.location_counter != lit_addr_abs or assembler.state.position_counter != 0:
                          if assembler.debug_mode:
                               print(f"Warning: Literal block LC state mismatch. Expected Addr={lit_addr_abs:o}, State Addr={assembler.state.location_counter:o}, State PC={assembler.state.position_counter}. Forcing alignment.")
                          assembler.state.set_location_counter(lit_addr_abs, 0) 

                     if assembler.output_generator: assembler.output_generator.add_full_word_to_binary(lit_addr_abs, lit_val)
                     assembler.state.advance_lc(60) 
                
                assembler.state.set_location_counter(code_start_lc, code_start_pc)
        except Exception as e:
            assembler.error_reporter.add_error(f"Failed to initialize OutputGenerator or pre-process IDENT/literals: {e}", 0, code='F')
            traceback.print_exc()
            if assembler._listing_handle and assembler._listing_handle != sys.stdout: assembler._listing_handle.close()
            if assembler._binary_handle: assembler._binary_handle.close()
            return False

    line_num = 0
    last_processed_line_num = 0 
    try:
        if assembler.debug_mode: print(f"!!! DEBUG STATE ID: Before loop: id(assembler.state) = {id(assembler.state)}")

        for i, line_content in enumerate(assembler.lines):
            line_num = i + 1
            current_state = assembler.state 
            current_state.current_line_number = line_num

            if assembler.debug_mode: print(f"!!! DEBUG STATE ID: L{line_num} Top of loop: id(current_state) = {id(current_state)}")

            current_state.line_start_address = current_state.location_counter
            current_state.line_start_position_bits = current_state.position_counter

            if pass_num == 1:
                parsed_dict = parse_line(line_content, line_num)
                parsed_dict['original'] = line_content 
                parsed_dict['pass1_lc'] = current_state.line_start_address
                parsed_dict['pass1_pos'] = current_state.line_start_position_bits
                assembler.parsed_lines[line_num] = parsed_dict
                if not process_line_pass_1(
                    current_state, 
                    assembler.symbol_table,
                    assembler.instruction_table,
                    assembler.error_reporter,
                    assembler.macro_definitions,
                    assembler.micro_definitions,
                    assembler, 
                    line_num,
                    parsed_dict
                ):
                    pass 
            else: 
                if not assembler.output_generator: return False 

                if line_num == getattr(assembler, 'ident_line_num', -1) and ident_line_listed_in_pass2_setup:
                    continue

                parsed_dict = assembler.parsed_lines.get(line_num)
                if parsed_dict:
                    if current_state.skip_count > 0:
                        is_comment = parsed_dict.get('is_comment_line', False)
                        mnemonic_skip = parsed_dict.get('opcode','').upper() if parsed_dict.get('opcode') else None
                        if not is_comment and mnemonic_skip != 'SKIP': 
                            current_state.skip_count -= 1
                        error_code = assembler.error_reporter.get_error_code_for_line(line_num)
                        assembler.output_generator.write_listing_line(
                            line_num, current_state.line_start_address, current_state.line_start_position_bits,
                            None, parsed_dict['original'], error_code, is_skipped=True, state=current_state
                        )
                        if current_state.end_statement_processed: break 
                        continue 

                    if not process_line_pass_2(
                        current_state, 
                        assembler.output_generator,
                        assembler, 
                        line_num,
                        parsed_dict
                    ):
                        pass 
                else:
                    assembler.error_reporter.add_error(f"Internal: Missing parsed data for line {line_num}", line_num, code='F')

            if current_state.end_statement_processed:
                last_processed_line_num = line_num
                break
            else:
                last_processed_line_num = line_num

    except AsmException as e:
        err_line = e.line_num if hasattr(e, 'line_num') and e.line_num else line_num
        assembler.error_reporter.add_error(str(e), err_line, code=e.code)
    except Exception as e:
        assembler.error_reporter.add_error(f"Unexpected error processing line {line_num}: {e}", line_num, code='F')
        traceback.print_exc()

    if pass_num == 1:
        if assembler.end_statement_label:
            end_label = assembler.end_statement_label
            if assembler.state.position_counter != 0:
                 handle_force_upper(assembler.state, None, assembler.error_reporter, last_processed_line_num)
            
            end_value_block = assembler.state.current_block
            end_value_type = 'absolute'
            
            if assembler.state.lc_is_absolute_due_to_loc: 
                end_value = assembler.state.location_counter
            elif end_value_block == '*ABS*':
                end_value = assembler.state.location_counter
            else: 
                end_value = assembler.state.block_lcs.get(end_value_block, assembler.state.location_counter)
                end_value_type = 'relocatable' 

            end_attrs = {'type': end_value_type, 'redefinable': False, 'block': end_value_block}
            if not assembler.symbol_table.define(end_label, end_value, last_processed_line_num, end_attrs, assembler.state.current_qualifier):
                 pass 

        literal_start_address = 0 
        literal_end_address = assembler.symbol_table.assign_literal_addresses(literal_start_address)
        literal_size = assembler.symbol_table.get_literal_block_size()

        assembler.block_base_addresses = {}
        current_base_for_blocks = literal_size 
        assembler.block_base_addresses['*ABS*'] = 0 

        if assembler.debug_mode:
            print("\n>>> DEBUG LC: End Pass 1 - Calculating Block Bases")
            print(f"    Literal Block Size = {literal_size:o} words ({literal_size}d)")
            print(f"    Initial Base Address (after literals) = {current_base_for_blocks:o}")
            print(f"    Block Order = {assembler.state.block_order}")
            print(f"    Block Relative Sizes (state.block_lcs) = { {name: f'{size:o}' for name, size in assembler.state.block_lcs.items()} }")

        processed_blocks = set(['*ABS*']) 
        for block_name in assembler.state.block_order: 
            if block_name not in processed_blocks:
                assembler.block_base_addresses[block_name] = current_base_for_blocks
                block_size = assembler.state.block_lcs.get(block_name, 0)
                if assembler.debug_mode:
                    print(f"    Assigning Block '{block_name}': Base={current_base_for_blocks:o}, Size={block_size:o}")
                current_base_for_blocks += block_size
                processed_blocks.add(block_name)

        last_active_block = assembler.state.current_block
        if last_active_block not in processed_blocks and last_active_block != '*ABS*': 
             if last_active_block not in assembler.state.block_lcs: 
                  assembler.state.block_lcs[last_active_block] = 0
        
        for block_name in sorted(assembler.state.block_lcs.keys()): 
            if block_name not in processed_blocks and block_name != '*ABS*':
                 block_size = assembler.state.block_lcs.get(block_name, 0)
                 if block_size > 0 or block_name == last_active_block : 
                      assembler.error_reporter.add_warning(f"Block '{block_name}' was defined but not explicitly placed via USE in order; appending.", 0, 'W')
                      assembler.block_base_addresses[block_name] = current_base_for_blocks
                      if assembler.debug_mode:
                          print(f"    Assigning Block '{block_name}' (Out of order/Last): Base={current_base_for_blocks:o}, Size={block_size:o}")
                      current_base_for_blocks += block_size
        
        assembler.total_program_length_for_listing = current_base_for_blocks 

        first_code_block_name = None
        if assembler.state.block_order: 
            first_code_block_name = assembler.state.block_order[0]
        
        if first_code_block_name:
            first_code_block_len = assembler.state.block_lcs.get(first_code_block_name, 0)
            assembler.endl_listing_value = literal_size + first_code_block_len
            if assembler.debug_mode:
                print(f"    Calculated ENDL listing value: {assembler.endl_listing_value:o} (Literals: {literal_size:o} + {first_code_block_name} len: {first_code_block_len:o})")
        else: 
            codeblk_size = assembler.state.block_lcs.get('CODEBLK', 0) 
            if codeblk_size > 0 : 
                 assembler.endl_listing_value = literal_size + codeblk_size
            else: 
                 assembler.endl_listing_value = assembler.total_program_length_for_listing

            if assembler.debug_mode:
                print(f"    Calculated ENDL listing value (fallback/abs): {assembler.endl_listing_value:o}")


        if assembler.debug_mode:
            print(f">>> DEBUG LC: End Pass 1 - Final Calculated Bases:")
            for name in sorted(assembler.block_base_addresses.keys()):
                addr = assembler.block_base_addresses[name]
                print(f"    Block: {name:<10} Base: {addr:o}")
            print(f"    Total Program Length (footprint): {assembler.total_program_length_for_listing:o} ({assembler.total_program_length_for_listing}d)")
            print(f"    ENDL Listing Value (main segment size): {assembler.endl_listing_value:o} ({assembler.endl_listing_value}d)")
            print("--- End Block Base Calculation ---\n")

            print("\n--- Symbol Table Dump (End of Pass 1, Relative Values) ---")
            assembler.symbol_table.dump_table(file_handle=sys.stdout) 
            print("--- End Symbol Table Dump (End of Pass 1, Relative Values) ---\n")
            print("\n--- Macro Definitions (End of Pass 1) ---")
            if not assembler.macro_definitions: print("  (No macros defined)")
            else:
                 for name, definition in assembler.macro_definitions.items():
                      print(f"  Definition: {name}")
                      print(f"    Type: {definition.get('type', '??')}")
                      print(f"    Params: {definition.get('params', [])}")
                      print(f"    Body Lines: {len(definition.get('body', []))}")
            print("--- End Macro Definitions ---\n")
            print("\n--- Micro Definitions (End of Pass 1) ---")
            if not assembler.micro_definitions: print("  (No micros defined)")
            else:
                 for name, value in assembler.micro_definitions.items(): print(f"  Micro: {name} = '{value}'")
            print("--- End Micro Definitions ---\n")
            print("\n--- Remote Blocks (End of Pass 1, stored parsed lines) ---")
            if not assembler.remote_blocks: print("  (No remote blocks defined/remaining)")
            else:
                for name, block_lines_dicts in assembler.remote_blocks.items():
                    print(f"  Remote Block: {name} ({len(block_lines_dicts)} lines)")
                    for line_d in block_lines_dicts: print(f"    L{line_d['line_num']}: {line_d['original']}")
            print("--- End Remote Blocks ---\n")

    if pass_num == 2 and assembler.output_generator:
        assembler.output_generator.flush_binary_word(pad_with_noops=True) 

        literal_pool = assembler.symbol_table.get_literal_pool()
        if literal_pool and assembler._listing_handle: 
            try:
                assembler._listing_handle.write("\n\n        CONTENT OF LITERALS BLOCK.\n\n")
                for lit_val in literal_pool:
                    lit_addr_abs = assembler.symbol_table.lookup_literal_address(lit_val, 0) 
                    if lit_addr_abs is not None:
                        octal_str = f"{lit_val:020o}"
                        listing_line = f"{lit_addr_abs:>5o}  {octal_str:<28} ;\n" 
                        assembler._listing_handle.write(listing_line)
                assembler._listing_handle.write("\n")
            except IOError:
                print("Error writing literal block to listing.", file=sys.stderr)

        if assembler._listing_handle:
            try:
                assembler._listing_handle.write("\n\n        SYMBOLIC REFERENCE TABLE.\n\n")
                assembler.symbol_table.dump_table(file_handle=assembler._listing_handle, block_base_addresses=assembler.block_base_addresses)
            except IOError:
                print("Error writing symbol table to listing.", file=sys.stderr)

    if pass_num == 2: 
        if assembler.output_generator: 
            assembler.output_generator.close() 
            assembler.output_generator = None 
        else: 
            if assembler._listing_handle and assembler._listing_handle != sys.stdout: 
                if not assembler._listing_handle.closed: assembler._listing_handle.close()
            if assembler._binary_handle: 
                if not assembler._binary_handle.closed: assembler._binary_handle.close()

    return not assembler.error_reporter.has_errors()

# pass_logic.py v2.13
