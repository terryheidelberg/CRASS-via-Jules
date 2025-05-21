# output_generator.py v1.106
"""
Handles the generation of the listing file and the binary output file
for the CRASS assembler. Implements parcel packing.
[...]
v1.105: - In _write_listing_header, after using state.current_ttl_title for
          the header, clear state.current_ttl_title. This ensures the next
          page reverts to state.current_title unless another TTL is encountered.
v1.106: - Refined write_listing_line to print LC only if pc_for_line_output is 0,
          matching COMPASS behavior for multi-parcel words spanning source lines.
"""
import sys
import math
from typing import TYPE_CHECKING, Optional, List, Tuple, Union, Any
if TYPE_CHECKING:
    from assembler_state import AssemblerState
    from instruction_table import InstructionTable
    from crass import Assembler


NOOP_15_BIT = 0o46000
LINES_PER_PAGE = 55
LC_WIDTH = 6
ERR_WIDTH = 1
OCTAL_FIELD_WIDTH = 28
PSEUDO_VALUE_WIDTH_INDICATOR = -1
EQU_STAR_LC_INDICATOR = -2
SPACE_COUNT_INDICATOR = -3
PSEUDO_STRING_VALUE_INDICATOR = -4


BLANK_LC_PC_OCTAL_PSEUDO_OPS_ALWAYS = {
    "TITLE", "TTL", "LIST", "NOLIST",
    "QUAL", "MACHINE", "CPU", "PPU", "CMU", "EJECT", "COMMENT", "ERROR",
    "FIN", "REF", "NOREF", "XREF", "SEQ", "SKIP", "UNL", "CTEXT", "ENDX",
    "RMT", "HERE", "EXT", "ENTRY",
    "MACRO", "ENDM", "OPDEF", "MICRO",
    "LOCAL", "IRP", "ENDD", "DUP", "ECHO", "PURGE", "OPSYN",
    "DECMIC", "OCTMIC", "ENDMIC",
    "B1=1", "B7=1", "CHAR", "CPOP", "CPSYN", "ENTRYC",
    "ERRMI", "ERRNG", "ERRNZ", "ERRPL", "ERRZR",
    "LCC", "NIL", "NOLABEL", "PURGDEF", "PURGMAC",
    "REP", "REPC", "REPI", "R=", "SEG", "SEGMENT",
    "SST", "STEXT", "STOPDUP", "USELCM", "POS", "MAX", "MIN", "MICCNT",
    "IF", "IFC", "IFCP", "IFCP6", "IPCP7", "IFGE",
    "IFGT", "IFLE", "IFLT", "IFMI", "IFNE", "IFPL", "IFPP", "IFPP6", "IFPP7",
    "IFEQ", "ELSE", "ENDIF",
    "LIT",
    "ABS", "USE",
    "SPACE"
}


class OutputGenerator:
    def __init__(self, listing_file_handle, binary_file_handle):
        self.listing_file = listing_file_handle
        self.binary_file = binary_file_handle
        self.buffered_word = 0
        self.bits_in_buffer = 0
        self.buffer_address = -1
        self.current_page_number = 0
        self.lines_on_current_page = LINES_PER_PAGE + 1
        self.debug_mode = False
        self.assembler_ref: Optional['Assembler'] = None

    def _format_octal_parcel(self, value, width_bits) -> str:
        if width_bits <= 0: return ""
        if width_bits == 15: num_octal_digits = 5
        elif width_bits == 30: num_octal_digits = 10
        elif width_bits == 60: num_octal_digits = 20
        else: num_octal_digits = math.ceil(width_bits / 3.0)

        fmt_str = "{:0" + str(int(num_octal_digits)) + "o}"
        mask = (1 << width_bits) - 1
        masked_value = value & mask
        return fmt_str.format(masked_value)

    def _format_pseudo_op_value_unjustified(self, value):
        if self.debug_mode:
            print(f"DEBUG _format_pseudo_op_value_unjustified: Received value='{value}', type={type(value)}")
        try:
            if not isinstance(value, int):
                try: int_value = int(value)
                except (ValueError, TypeError): return "CONV_ERR"
            else: int_value = value

            if int_value < 0:
                mask = (1 << 60) - 1
                return f"{int_value & mask:o}"
            else: return f"{int_value:o}"
        except (TypeError, ValueError): return "FMT_ERR"


    def _write_buffered_word(self):
        if self.binary_file and self.buffer_address != -1:
            if self.bits_in_buffer != 60 and self.bits_in_buffer > 0 :
                 self.buffered_word <<= (60 - self.bits_in_buffer)
            if self.bits_in_buffer > 0:
                self.binary_file.write(f"{self.buffered_word:020o}\n")
        self.buffered_word = 0
        self.bits_in_buffer = 0
        self.buffer_address = -1

    def _write_listing_header(self, state: 'AssemblerState'):
        self.current_page_number += 1
        header_title_line = state.current_ttl_title 
        if not header_title_line: header_title_line = state.current_title 
        if not header_title_line and state.program_start_symbol: 
            header_title_line = state.program_start_symbol
        
        header_title_line = (header_title_line or "")[:62] 

        compass_info_placeholder = "CRASS Assembler"
        page_str = f"PAGE {self.current_page_number:>5}"
        header_line1_content = f"{header_title_line:<70}{compass_info_placeholder:<25}"
        header_line1 = f"{header_line1_content}{page_str:>{130-len(header_line1_content)}}\n"

        block_name_for_header = state.current_block if state.current_block and state.current_block != '*ABS*' else ""
        header_line2_content = f"{block_name_for_header:>120}"
        header_line2 = f"{header_line2_content}\n"

        self.listing_file.write(header_line1)
        self.listing_file.write(header_line2)
        self.listing_file.write("\n")
        self.lines_on_current_page = 3

        if state.current_ttl_title:
            state.current_ttl_title = ""


    def _print_single_listing_segment(self, lc_str: str, err_str: str, octal_data_str: str, source_line_str: str, state: 'AssemblerState'):
        if not self.listing_file: return
        if self.lines_on_current_page >= LINES_PER_PAGE:
            if self.current_page_number > 0 and self.listing_file != sys.stdout:
                self.listing_file.write("\f") 
            self._write_listing_header(state)
        
        listing_line = f"{lc_str:<{LC_WIDTH}} {err_str:<{ERR_WIDTH}} {octal_data_str:<{OCTAL_FIELD_WIDTH}} {source_line_str}\n"
        self.listing_file.write(listing_line)
        self.lines_on_current_page += 1

    def add_blank_lines(self, num_lines: int, state: 'AssemblerState'):
        if not self.listing_file: return
        for _ in range(num_lines):
            if self.lines_on_current_page >= LINES_PER_PAGE:
                if self.current_page_number > 0 and self.listing_file != sys.stdout:
                    self.listing_file.write("\f")
                self._write_listing_header(state)
            self.listing_file.write("\n")
            self.lines_on_current_page += 1

    def write_listing_line(
        self,
        line_num: int,
        lc_for_line_output: Optional[int],
        pc_for_line_output: Optional[int], # PC where the *first parcel of this line* starts
        generated_data: Optional[List[Tuple[Any, int]]],
        source_line_text: str,
        error_code: str = "",
        is_skipped: bool = False,
        state: Optional['AssemblerState'] = None,
        pseudo_op_mnemonic: Optional[str] = None
    ):
        if state is None: return

        self.debug_mode = getattr(state, 'debug_mode', False)
        mnemonic_upper = pseudo_op_mnemonic.upper() if pseudo_op_mnemonic else ""

        # --- Handle lines that don't generate normal octal data or have special formatting ---
        if is_skipped:
            self._print_single_listing_segment(" " * LC_WIDTH, " ", " " * OCTAL_FIELD_WIDTH, source_line_text, state)
            return
        if parsed_line_is_comment_only(source_line_text, mnemonic_upper) and mnemonic_upper != "SPACE":
            err_to_print = error_code if source_line_text.strip() else " "
            self._print_single_listing_segment(" " * LC_WIDTH, err_to_print, " " * OCTAL_FIELD_WIDTH, source_line_text, state)
            return
        if mnemonic_upper in BLANK_LC_PC_OCTAL_PSEUDO_OPS_ALWAYS:
            lc_str_display = " " * LC_WIDTH
            octal_str_display = " " * OCTAL_FIELD_WIDTH
            if mnemonic_upper == "ENDL":
                 endl_val = getattr(self.assembler_ref, 'endl_listing_value', lc_for_line_output) if self.assembler_ref else lc_for_line_output
                 if endl_val is not None: lc_str_display = f"{endl_val:o}".rjust(LC_WIDTH)
            elif mnemonic_upper in ("BASE", "CODE") and isinstance(generated_data, list) and generated_data and \
               generated_data[0][1] == PSEUDO_STRING_VALUE_INDICATOR and isinstance(generated_data[0][0], str):
                octal_str_display = generated_data[0][0].rjust(OCTAL_FIELD_WIDTH)
            self._print_single_listing_segment(lc_str_display, error_code, octal_str_display, source_line_text, state)
            return
        if mnemonic_upper in ("EQU", "=", "SET", "BSS", "BSSZ"):
            lc_str_display = " " * LC_WIDTH
            if mnemonic_upper in ("BSS", "BSSZ") and lc_for_line_output is not None:
                 # For BSS/BSSZ, the LC printed is where the block *starts*.
                 # The "octal" field shows the size.
                 lc_str_display = (f"L {lc_for_line_output:o}").ljust(LC_WIDTH)
            
            octal_str_display = " " * OCTAL_FIELD_WIDTH
            if isinstance(generated_data, list) and generated_data and \
               generated_data[0][1] in (PSEUDO_VALUE_WIDTH_INDICATOR, EQU_STAR_LC_INDICATOR):
                val_str = self._format_pseudo_op_value_unjustified(generated_data[0][0])
                octal_str_display = val_str.rjust(OCTAL_FIELD_WIDTH)
            self._print_single_listing_segment(lc_str_display, error_code, octal_str_display, source_line_text, state)
            return

        # --- Handle lines that DO generate octal data (instructions, VFD, DATA, CON, DIS) ---
        
        current_lc_for_segment_display = lc_for_line_output
        # pc_for_line_output is the starting bit position for the *first* parcel of this source line.
        # current_pc_for_segment_start tracks the bit position for the current *visual segment* being built.
        current_pc_for_segment_start = pc_for_line_output if pc_for_line_output is not None else 0
        
        source_text_for_segment = source_line_text
        error_code_for_segment = error_code

        if not generated_data: 
            # Print LC only if this line starts a new word (pc_for_line_output == 0)
            lc_s = (f"L {lc_for_line_output:o}" if lc_for_line_output is not None and (pc_for_line_output == 0 or pc_for_line_output is None) else " ").ljust(LC_WIDTH)
            self._print_single_listing_segment(lc_s, error_code, " " * OCTAL_FIELD_WIDTH, source_line_text, state)
            return

        octal_accumulator_current_segment: List[str] = []
        bits_in_current_segment = 0 # Bits accumulated for the current visual line segment
        
        for i, (item_value, item_width_bits) in enumerate(generated_data):
            if item_width_bits <= 0: continue # Skip pseudo-indicators

            formatted_parcel = self._format_octal_parcel(item_value, item_width_bits)

            # If adding this parcel would overflow the current visual 60-bit word segment
            # current_pc_for_segment_start is the indent for the current visual line.
            # bits_in_current_segment is what's already in octal_accumulator_current_segment.
            if current_pc_for_segment_start + bits_in_current_segment + item_width_bits > 60:
                # Print what we have accumulated for the current segment
                # LC is printed only if this segment starts a new word (current_pc_for_segment_start == 0)
                lc_s = (f"L {current_lc_for_segment_display:o}" if current_lc_for_segment_display is not None and current_pc_for_segment_start == 0 else " ").ljust(LC_WIDTH)
                # Indent based on where this visual segment starts
                octal_display = (" " * int(math.floor(current_pc_for_segment_start / 3.0))) + "".join(octal_accumulator_current_segment)
                
                self._print_single_listing_segment(
                    lc_s,
                    error_code_for_segment,
                    octal_display.ljust(OCTAL_FIELD_WIDTH),
                    source_text_for_segment, # Source text only for the first segment of a source line
                    state
                )
                octal_accumulator_current_segment = []
                bits_in_current_segment = 0
                current_pc_for_segment_start = 0 # New visual word (segment) starts at PC 0
                source_text_for_segment = "" 
                error_code_for_segment = " " 
                if current_lc_for_segment_display is not None: current_lc_for_segment_display +=1


            octal_accumulator_current_segment.append(formatted_parcel)
            bits_in_current_segment += item_width_bits

            # If this parcel completes a 60-bit boundary for the current visual segment
            if current_pc_for_segment_start + bits_in_current_segment == 60:
                lc_s = (f"L {current_lc_for_segment_display:o}" if current_lc_for_segment_display is not None and current_pc_for_segment_start == 0 else " ").ljust(LC_WIDTH)
                octal_display = (" " * int(math.floor(current_pc_for_segment_start / 3.0))) + "".join(octal_accumulator_current_segment)
                
                self._print_single_listing_segment(
                    lc_s,
                    error_code_for_segment,
                    octal_display.ljust(OCTAL_FIELD_WIDTH),
                    source_text_for_segment,
                    state
                )
                octal_accumulator_current_segment = []
                bits_in_current_segment = 0
                current_pc_for_segment_start = 0 # Next visual segment starts at PC 0
                source_text_for_segment = ""
                error_code_for_segment = " "
                if current_lc_for_segment_display is not None: current_lc_for_segment_display +=1

        # After iterating all generated_data for this call, if there's anything left in the accumulator, print it
        if bits_in_current_segment > 0:
            lc_s = (f"L {current_lc_for_segment_display:o}" if current_lc_for_segment_display is not None and current_pc_for_segment_start == 0 else " ").ljust(LC_WIDTH)
            octal_display = (" " * int(math.floor(current_pc_for_segment_start / 3.0))) + "".join(octal_accumulator_current_segment)
            self._print_single_listing_segment(
                lc_s,
                error_code_for_segment,
                octal_display.ljust(OCTAL_FIELD_WIDTH),
                source_text_for_segment,
                state
            )


    def add_parcel_to_binary_word(self, address, value, width):
        if not self.binary_file: return
        if self.bits_in_buffer > 0 and address != self.buffer_address:
            if self.debug_mode: print(f"DEBUG OG Binary: Address change. Flushing word for {self.buffer_address:o}. New address {address:o}.")
            self.flush_binary_word(pad_with_noops=True)

        if self.bits_in_buffer == 0:
            self.buffer_address = address
            self.buffered_word = 0
        
        if self.bits_in_buffer + width > 60:
            err_msg = (f"Parcel (width {width}) at {address:o} (PC={self.bits_in_buffer}) "
                       f"would overflow current binary word. This indicates an alignment issue.")
            if self.assembler_ref and self.assembler_ref.error_reporter:
                self.assembler_ref.error_reporter.add_error(err_msg, 0, 'A')
            else: print(f"ERROR: {err_msg}")
            self.flush_binary_word(pad_with_noops=True)
            self.buffer_address = address
            self.buffered_word = 0
            self.bits_in_buffer = 0

        shift = 60 - self.bits_in_buffer - width
        mask = (1 << width) - 1
        parcel_to_add = (value & mask) << shift
        self.buffered_word |= parcel_to_add
        self.bits_in_buffer += width
        
        if self.bits_in_buffer == 60:
            self._write_buffered_word()

    def add_full_word_to_binary(self, address, word_value):
        if not self.binary_file: return
        if self.bits_in_buffer > 0:
            if self.debug_mode: print(f"DEBUG OG Binary: Adding full word, but buffer has {self.bits_in_buffer} bits for {self.buffer_address:o}. Flushing.")
            self.flush_binary_word(pad_with_noops=True)
        
        self.buffer_address = address
        self.buffered_word = word_value
        self.bits_in_buffer = 60
        self._write_buffered_word()

    def flush_binary_word(self, pad_with_noops=False):
        if self.bits_in_buffer > 0:
            if pad_with_noops and self.bits_in_buffer < 60:
                remaining_bits = 60 - self.bits_in_buffer
                if self.debug_mode: print(f"DEBUG OG Binary: Flushing word for {self.buffer_address:o}. PC={self.bits_in_buffer}. Padding with {remaining_bits} bits of NOOPs.")
                while remaining_bits >= 15:
                    shift = remaining_bits - 15
                    self.buffered_word |= (NOOP_15_BIT << shift)
                    remaining_bits -= 15
                if remaining_bits > 0:
                     self.buffered_word <<= remaining_bits
                self.bits_in_buffer = 60
            elif self.bits_in_buffer < 60:
                 if self.debug_mode: print(f"DEBUG OG Binary: Flushing word for {self.buffer_address:o}. PC={self.bits_in_buffer}. Left-shifting by {60-self.bits_in_buffer} bits.")
                 self.buffered_word <<= (60 - self.bits_in_buffer)
                 self.bits_in_buffer = 60
            self._write_buffered_word()

    def close(self):
        self.flush_binary_word(pad_with_noops=True)

        if self.listing_file and self.listing_file != sys.stdout:
            self.listing_file.close()
        if self.binary_file:
             self.binary_file.close()

def parsed_line_is_comment_only(source_line: str, mnemonic_upper: Optional[str]) -> bool:
    stripped_line = source_line.strip()
    if stripped_line.startswith('*'):
        return True
    if not mnemonic_upper or mnemonic_upper == '*':
        if not stripped_line or stripped_line.startswith('.'): 
            return True
    return False

# output_generator.py v1.106
